#!/usr/bin/env python3
"""
Step 2: Tri-Source Building Enrichment (PLUTO + RPAD + HPD)

Data Sources:
1. NYC PLUTO (MapPLUTO) - Corporate ownership, building characteristics
2. NYC RPAD (Property Tax) - Current taxpayer, assessed values
3. NYC HPD (Housing Preservation) - Registered owner, violations, complaints

Populates:
- Owner data: current_owner_name (PLUTO), owner_name_rpad (RPAD), owner_name_hpd (HPD)
- Building data: units, sqft, year built/altered, building class
- Financial data: assessed values
- Quality indicators: HPD violations and complaints counts
"""

import psycopg2
import psycopg2.extras
import os
import sys
import requests
import time
from datetime import datetime
from dotenv import load_dotenv

from socrata_client import (
    SocrataClient, soql_quote, bbl_parts, normalize_pluto_record,
)

# Force unbuffered output for Railway logging
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

load_dotenv()

# Support both DATABASE_URL and individual DB_* variables
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    # Build from individual components
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME')
    
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        raise ValueError("Either DATABASE_URL or DB_HOST/DB_USER/DB_PASSWORD/DB_NAME must be set")
    
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# NYC Open Data API endpoints
PLUTO_API_BASE = "https://data.cityofnewyork.us/resource/64uk-42ks.json"
RPAD_API_BASE = "https://data.cityofnewyork.us/resource/yjxr-fw8i.json"
HPD_REGISTRATION_API = "https://data.cityofnewyork.us/resource/tesw-yqqr.json"
HPD_CONTACTS_API = "https://data.cityofnewyork.us/resource/feu5-w2e2.json"
HPD_VIOLATIONS_API = "https://data.cityofnewyork.us/resource/wvxf-dwi5.json"
# Use public Housing Maintenance Code Complaints dataset (not the restricted one)
HPD_COMPLAINTS_API = "https://data.cityofnewyork.us/resource/ygpa-z7cr.json"

# Configuration
API_DELAY = float(os.getenv('API_DELAY', '0.1'))
BUILDING_BATCH_SIZE = int(os.getenv('BUILDING_BATCH_SIZE', '500'))

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = SocrataClient()
    return _client


def _num(value, cast=float):
    try:
        return cast(float(value))
    except (ValueError, TypeError):
        return None


def get_pluto_data_for_bbl(bbl):
    """
    Query NYC Open Data API for PLUTO data by BBL
    Returns (data_dict, error_message) tuple
    """
    try:
        data = _get_client().get('pluto', **{
            "$where": f"bbl={soql_quote(bbl)}",
            "$limit": 1,
        })
        time.sleep(API_DELAY)
        if not data:
            return None, None  # Not found, but not an error

        result = normalize_pluto_record(data[0])
        # Historical pipeline behavior stores zeros rather than NULL for
        # measured counts/areas when PLUTO has no value.
        for field in ('residential_units', 'total_units', 'num_floors',
                      'building_sqft', 'lot_sqft'):
            if result.get(field) is None:
                result[field] = 0
        return result, None

    except Exception as e:
        return None, f"PLUTO API error: {str(e)}"


def get_rpad_data_for_bbl(bbl):
    """
    Query NYC Open Data API for RPAD (Property Tax) data by BBL
    Returns (data_dict, error_message) tuple
    """
    try:
        client = _get_client()
        boro, block, lot, _, _ = bbl_parts(bbl)

        params = {
            "$where": (f"boro={soql_quote(boro)} AND block={soql_quote(block)} "
                       f"AND lot={soql_quote(lot)}"),
            "$limit": 1,
        }
        # The valuation dataset can carry multiple assessment years per
        # parcel; order by the year column (whatever it's called in this
        # vintage of the dataset) so $limit 1 returns the latest.
        columns = client.get_columns('rpad')
        for year_col in ('year', 'yr4', 'yr', 'fin_yr'):
            if year_col in columns:
                params['$order'] = f'{year_col} DESC'
                break

        data = client.get('rpad', **params)
        time.sleep(API_DELAY)
        if not data:
            return None, None  # Not found, but not an error

        record = data[0]
        result = {
            'owner_name_rpad': record.get('owner'),
            'assessed_land_value': _num(record.get('avland'), int) or 0,
            'assessed_total_value': _num(record.get('avtot'), int) or 0,
        }
        return result, None

    except Exception as e:
        return None, f"RPAD API error: {str(e)}"


def _contact_name(contact):
    corp = (contact.get('corporationname') or '').strip()
    if corp:
        return corp
    person = f"{contact.get('firstname', '') or ''} {contact.get('lastname', '') or ''}".strip()
    return person or None


def _contact_address(contact):
    house = (contact.get('businesshousenumber') or '').strip()
    street = (contact.get('businessstreetname') or '').strip()
    apt = (contact.get('businessapartment') or '').strip()
    line = f"{house} {street}".strip()
    if apt:
        line = f"{line}, {apt}".strip(', ')
    return line or None


def get_hpd_data_for_bbl(bbl):
    """
    Query NYC HPD APIs for owner, contacts, violations, and complaints data
    Returns (data_dict, error_message) tuple
    """
    try:
        client = _get_client()
        boro, block, lot, _, _ = bbl_parts(bbl)

        result = {
            'owner_name_hpd': None,
            'hpd_registration_id': None,
            'hpd_open_violations': 0,
            'hpd_total_violations': 0,
            'hpd_open_complaints': 0,
            'hpd_total_complaints': 0,
            # City-verified mailing address of the registered head officer /
            # owner — free skip-tracing data we previously discarded.
            'hpd_owner_business_address': None,
            'hpd_owner_business_city': None,
            'hpd_owner_business_state': None,
            'hpd_owner_business_zip': None,
            'hpd_agent_name': None,
            'hpd_site_manager_name': None,
        }

        # 1. Most recent HPD registration for the lot
        registration = client.get('hpd_registrations', **{
            'boroid': boro, 'block': block, 'lot': lot,
            '$order': 'registrationenddate DESC', '$limit': 1,
        })
        time.sleep(API_DELAY)
        if not registration:
            return result, None  # Building not in HPD (not an error)

        reg_id = registration[0].get('registrationid')
        result['hpd_registration_id'] = reg_id

        # 2. All contacts for the registration in one call. Explicit owner
        # contact types outrank officers: a HeadOfficer is a corporate role,
        # not proof that the person owns the property. We also keep
        # the managing agent and site manager, and the owner's mailing
        # address.
        if reg_id:
            contacts = client.get('hpd_contacts',
                                  registrationid=reg_id, **{'$limit': 200})
            time.sleep(API_DELAY)
            by_type = {}
            for c in contacts:
                by_type.setdefault((c.get('type') or '').strip(), []).append(c)

            owner_contacts = []
            for contact_type in ('CorporateOwner', 'IndividualOwner', 'JointOwner'):
                owner_contacts.extend(by_type.get(contact_type, []))
            if not owner_contacts:
                owner_contacts = by_type.get('HeadOfficer', [])

            owner_names = []
            for c in owner_contacts:
                name = _contact_name(c)
                if name and name not in owner_names:
                    owner_names.append(name)
                if name and result['hpd_owner_business_address'] is None:
                    result['hpd_owner_business_address'] = _contact_address(c)
                    result['hpd_owner_business_city'] = (c.get('businesscity') or '').strip() or None
                    result['hpd_owner_business_state'] = (c.get('businessstate') or '').strip() or None
                    result['hpd_owner_business_zip'] = (c.get('businesszip') or '').strip() or None
            result['owner_name_hpd'] = ' & '.join(owner_names) or None

            for c in by_type.get('Agent', []):
                result['hpd_agent_name'] = _contact_name(c)
                break
            for c in by_type.get('SiteManager', []):
                result['hpd_site_manager_name'] = _contact_name(c)
                break

        # 3. Violations. violationstatus is the field HPD maintains as the
        # open/closed flag; paginate so big buildings aren't truncated at
        # one page.
        violations = client.get_all('hpd_violations', page_size=1000, max_rows=20000, **{
            '$select': 'violationid,violationstatus',
            '$where': (f"boroid={soql_quote(boro)} AND block={soql_quote(block)} "
                       f"AND lot={soql_quote(lot)}"),
        })
        time.sleep(API_DELAY)
        result['hpd_total_violations'] = len(violations)
        result['hpd_open_violations'] = sum(
            1 for v in violations if (v.get('violationstatus') or '').upper() == 'OPEN')

        # 4. Complaints. Each row in this dataset is a complaint-PROBLEM, so
        # count distinct complaint ids, not rows.
        try:
            problems = client.get_all('hpd_complaints', page_size=1000, max_rows=50000, **{
                '$select': 'complaint_id,complaint_status',
                '$where': f"bbl={soql_quote(bbl)}",
            })
            time.sleep(API_DELAY)
            complaint_status = {}
            for p in problems:
                cid = p.get('complaint_id')
                if not cid:
                    continue
                is_open = (p.get('complaint_status') or '').upper() == 'OPEN'
                complaint_status[cid] = complaint_status.get(cid, False) or is_open
            result['hpd_total_complaints'] = len(complaint_status)
            result['hpd_open_complaints'] = sum(1 for is_open in complaint_status.values() if is_open)
        except Exception as e:
            print(f"      ⚠️  Complaints API error: {str(e)}")

        return result, None

    except Exception as e:
        return None, f"HPD API error: {str(e)}"


_column_cache = None


def _buildings_columns(cur):
    """Columns present on buildings — lets this script write the new signal
    fields when the migration has run, and skip them cleanly when not."""
    global _column_cache
    if _column_cache is None:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'buildings'
        """)
        _column_cache = {r['column_name'] for r in cur.fetchall()}
    return _column_cache


def enrich_buildings_from_pluto():
    """
    Main process - Tri-Source Enrichment:
    1. Get buildings without owner data
    2. Query PLUTO, RPAD, and HPD APIs for each BBL
    3. Update building record with combined data from all sources
    """
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    
    print("=" * 70)
    print("🏢 Step 2: Tri-Source Building Enrichment (PLUTO + RPAD + HPD)")
    print("=" * 70)
    
    available = _buildings_columns(cur)
    freshness_column = ('property_last_enriched'
                        if 'property_last_enriched' in available else 'last_updated')

    # Rotating refresh: EVERY building goes stale after 30 days, not just
    # ones missing owner data — that old gating meant violation/complaint
    # counts froze forever once a building had an owner name. Work is
    # bounded per run (STEP2_BATCH_LIMIT, default 4000) so the nightly job
    # stays short and the whole portfolio rotates in ~portfolio/limit days.
    # Never-enriched buildings jump the queue.
    batch_limit = int(os.getenv('STEP2_BATCH_LIMIT', '4000'))
    cur.execute(f"""
        SELECT id, bbl, address
        FROM buildings
        WHERE bbl IS NOT NULL
        AND ({freshness_column} IS NULL
             OR {freshness_column} < NOW() - INTERVAL '30 days')
        ORDER BY (current_owner_name IS NULL AND owner_name_rpad IS NULL
                  AND owner_name_hpd IS NULL) DESC,
                 {freshness_column} ASC NULLS FIRST
        LIMIT %s
    """, (batch_limit,))

    buildings = cur.fetchall()
    total = len(buildings)
    print(f"\n📊 Found {total} buildings to enrich this run "
          f"(batch limit {batch_limit}; never-enriched first, then oldest)")
    
    if not buildings:
        print("   No buildings need enrichment. All done!")
        cur.close()
        conn.close()
        return
    
    enriched = 0
    pluto_success = 0
    rpad_success = 0
    hpd_success = 0
    failed = 0
    already_enriched = 0
    
    for i, building in enumerate(buildings, 1):
        bbl = building['bbl']
        building_id = building['id']
        address = building['address']
        
        print(f"\n🔍 [{i}/{total}] BBL {bbl} ({address})...")
        
        # Check if building already has data
        cur.execute("""
            SELECT current_owner_name, owner_name_rpad, owner_name_hpd
            FROM buildings WHERE id = %s
        """, (building_id,))
        existing = cur.fetchone()
        
        has_pluto = existing['current_owner_name'] is not None
        has_rpad = existing['owner_name_rpad'] is not None
        has_hpd = existing['owner_name_hpd'] is not None
        
        # Get data from all three sources
        pluto_data, pluto_error = get_pluto_data_for_bbl(bbl)
        rpad_data, rpad_error = get_rpad_data_for_bbl(bbl)
        hpd_data, hpd_error = get_hpd_data_for_bbl(bbl)
        
        # Report errors if any
        if pluto_error:
            print(f"   ⚠️ {pluto_error}")
        if rpad_error:
            print(f"   ⚠️ {rpad_error}")
        if hpd_error:
            print(f"   ⚠️ {hpd_error}")
        
        # Check what's available
        if not pluto_data and not rpad_data and not hpd_data and not pluto_error and not rpad_error and not hpd_error:
            sources = []
            if has_pluto:
                sources.append("PLUTO")
            if has_rpad:
                sources.append("RPAD")
            if has_hpd:
                sources.append("HPD")
            
            print(f"   ✓ Sources checked; no new rows"
                  + (f" (existing: {' + '.join(sources)})" if sources else ""))
            try:
                if 'property_last_enriched' in available:
                    cur.execute("""
                        UPDATE buildings
                        SET property_last_attempted = CURRENT_TIMESTAMP,
                            property_last_enriched = CURRENT_TIMESTAMP,
                            property_last_error = NULL,
                            last_updated = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (building_id,))
                else:
                    cur.execute("""
                        UPDATE buildings SET last_updated = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (building_id,))
                conn.commit()
                already_enriched += 1
            except Exception as e:
                conn.rollback()
                print(f"   ❌ Database error: {e}")
                failed += 1
            continue
        
        # Build update query dynamically. Optional (post-migration) columns
        # are written only when they exist so the script runs either way.
        update_parts = []
        update_values = []

        def add_field(column, value):
            update_parts.append(f"{column} = %s")
            update_values.append(value)

        def add_optional(column, value):
            if column in available:
                add_field(column, value)

        # PLUTO data (corporate ownership + geometry + zoning headroom)
        if pluto_data:
            add_field("current_owner_name", pluto_data['owner_name'])
            if pluto_data.get('address'):
                add_field("address", pluto_data['address'])
            if pluto_data.get('bin'):
                add_field("bin", pluto_data['bin'])
            add_field("building_class", pluto_data['building_class'])
            add_field("land_use", pluto_data['land_use'])
            add_field("residential_units", pluto_data['residential_units'])
            add_field("total_units", pluto_data['total_units'])
            add_field("num_floors", pluto_data['num_floors'])
            add_field("building_sqft", pluto_data['building_sqft'])
            add_field("lot_sqft", pluto_data['lot_sqft'])
            add_field("year_built", pluto_data['year_built'])
            add_field("year_altered", pluto_data['year_altered'])
            add_optional("zip_code", pluto_data['zip_code'])
            add_optional("latitude", pluto_data['latitude'])
            add_optional("longitude", pluto_data['longitude'])
            add_optional("zoning_district", pluto_data['zoning_district'])
            add_optional("built_far", pluto_data['built_far'])
            add_optional("max_resid_far", pluto_data['max_resid_far'])
            add_optional("max_comm_far", pluto_data['max_comm_far'])
            add_optional("unused_far", pluto_data['unused_far'])
            add_optional("pluto_owner_type", pluto_data['pluto_owner_type'])
            pluto_success += 1
            print(f"   ✅ PLUTO: {pluto_data['owner_name']}")
            if pluto_data['unused_far']:
                print(f"      🏗️  Unused FAR: {pluto_data['unused_far']} "
                      f"(built {pluto_data['built_far']}, allowed "
                      f"{max(pluto_data['max_resid_far'] or 0, pluto_data['max_comm_far'] or 0)})")

        # RPAD data (current taxpayer + assessed values)
        if rpad_data:
            add_field("owner_name_rpad", rpad_data['owner_name_rpad'])
            add_field("assessed_land_value", rpad_data['assessed_land_value'])
            add_field("assessed_total_value", rpad_data['assessed_total_value'])
            rpad_success += 1
            print(f"   💰 RPAD: {rpad_data['owner_name_rpad']}")
            print(f"      💵 Assessed: ${rpad_data['assessed_total_value']:,}")

        # HPD data. Violation/complaint counts are written whenever HPD knows
        # the building — an owner-name match is NOT required (that gating
        # used to silently drop violation data).
        if hpd_data and hpd_data.get('hpd_registration_id'):
            add_field("hpd_registration_id", hpd_data['hpd_registration_id'])
            add_field("hpd_open_violations", hpd_data['hpd_open_violations'])
            add_field("hpd_total_violations", hpd_data['hpd_total_violations'])
            add_field("hpd_open_complaints", hpd_data['hpd_open_complaints'])
            add_field("hpd_total_complaints", hpd_data['hpd_total_complaints'])
            if hpd_data.get('owner_name_hpd'):
                add_field("owner_name_hpd", hpd_data['owner_name_hpd'])
                add_optional("hpd_owner_business_address", hpd_data['hpd_owner_business_address'])
                add_optional("hpd_owner_business_city", hpd_data['hpd_owner_business_city'])
                add_optional("hpd_owner_business_state", hpd_data['hpd_owner_business_state'])
                add_optional("hpd_owner_business_zip", hpd_data['hpd_owner_business_zip'])
            add_optional("hpd_agent_name", hpd_data['hpd_agent_name'])
            add_optional("hpd_site_manager_name", hpd_data['hpd_site_manager_name'])
            hpd_success += 1
            print(f"   🏘️  HPD: {hpd_data.get('owner_name_hpd') or '(no owner contact)'}")
            if hpd_data['hpd_total_violations'] > 0:
                print(f"      ⚠️  Violations: {hpd_data['hpd_open_violations']} open / {hpd_data['hpd_total_violations']} total")
            if hpd_data['hpd_total_complaints'] > 0:
                print(f"      📋 Complaints: {hpd_data['hpd_open_complaints']} open / {hpd_data['hpd_total_complaints']} total")
        
        # Source-specific freshness prevents ACRIS, SOS, or a failed request
        # from making property ownership look fresh for another 30 days.
        errors = [e for e in (pluto_error, rpad_error, hpd_error) if e]
        add_optional("property_last_attempted", datetime.now())
        add_optional("property_last_error", '; '.join(errors) if errors else None)
        if not errors:
            add_optional("property_last_enriched", datetime.now())
            update_parts.append("last_updated = CURRENT_TIMESTAMP")
        elif 'property_last_enriched' not in available:
            # On pre-migration databases do not advance the shared timestamp
            # after a partial failure; the next run should retry promptly.
            failed += 1

        if not update_parts:
            failed += 1
            continue

        update_values.append(building_id)
        
        query = f"""
            UPDATE buildings
            SET {', '.join(update_parts)}
            WHERE id = %s
        """
        
        try:
            cur.execute(query, update_values)
            conn.commit()
            enriched += 1
            if errors:
                failed += 1
        except Exception as e:
            print(f"   ❌ Database error: {e}")
            conn.rollback()
            failed += 1
    
    print(f"\n" + "=" * 70)
    print(f"✅ Complete!")
    print(f"   Buildings enriched: {enriched}")
    print(f"   Already enriched: {already_enriched}")
    print(f"   PLUTO data retrieved: {pluto_success}")
    print(f"   RPAD data retrieved: {rpad_success}")
    print(f"   HPD data retrieved: {hpd_success}")
    print(f"   Failed/No data: {failed}")
    print("=" * 70)
    
    # Show sample results
    cur.execute("""
        SELECT bbl, address, current_owner_name, owner_name_rpad, owner_name_hpd,
               assessed_total_value, hpd_open_violations, hpd_total_violations,
               residential_units, year_built
        FROM buildings
        WHERE current_owner_name IS NOT NULL OR owner_name_rpad IS NOT NULL OR owner_name_hpd IS NOT NULL
        LIMIT 3
    """)
    
    results = cur.fetchall()
    if results:
        print(f"\n📋 Sample enriched buildings:")
        for r in results:
            print(f"\n   🏢 {r['address']}")
            print(f"      BBL: {r['bbl']}")
            if r['current_owner_name']:
                print(f"      Owner (PLUTO): {r['current_owner_name']}")
            if r['owner_name_rpad']:
                print(f"      Owner (RPAD): {r['owner_name_rpad']}")
            if r['owner_name_hpd']:
                print(f"      Owner (HPD): {r['owner_name_hpd']}")
            if r['assessed_total_value']:
                print(f"      Assessed Value: ${r['assessed_total_value']:,}")
            if r['hpd_total_violations'] and r['hpd_total_violations'] > 0:
                print(f"      HPD Violations: {r['hpd_open_violations']} open / {r['hpd_total_violations']} total")
            print(f"      {r['residential_units']} units, built {r['year_built']}")
    
    cur.close()
    conn.close()


if __name__ == "__main__":
    enrich_buildings_from_pluto()
