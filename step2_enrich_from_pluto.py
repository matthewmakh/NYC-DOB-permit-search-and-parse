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
import requests
import time
from dotenv import load_dotenv

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


def get_pluto_data_for_bbl(bbl):
    """
    Query NYC Open Data API for PLUTO data by BBL
    Returns (data_dict, error_message) tuple
    """
    try:
        # PLUTO uses BBL as a 10-digit string
        params = {
            "$where": f"bbl='{bbl}'",
            "$limit": 1
        }
        
        response = requests.get(PLUTO_API_BASE, params=params, timeout=10)
        response.raise_for_status()
        time.sleep(API_DELAY)
        
        data = response.json()
        if not data:
            return None, None  # Not found, but not an error
            
        record = data[0]
        
        # Extract key fields - map to existing database columns
        result = {
            'owner_name': record.get('ownername'),
            'building_class': record.get('bldgclass'),
            'land_use': record.get('landuse'),
            'residential_units': int(float(record.get('unitsres', 0) or 0)),
            'total_units': int(float(record.get('unitstotal', 0) or 0)),
            'num_floors': int(float(record.get('numfloors', 0) or 0)),
            'building_sqft': int(float(record.get('bldgarea', 0) or 0)),
            'lot_sqft': int(float(record.get('lotarea', 0) or 0)),
            'year_built': int(float(record.get('yearbuilt', 0) or 0)) if record.get('yearbuilt') else None,
            'year_altered': int(float(record.get('yearalter1', 0) or 0)) if record.get('yearalter1') else None,
            'zip_code': record.get('zipcode')
        }
        return result, None
        
    except Exception as e:
        return None, f"PLUTO API error: {str(e)}"


def get_rpad_data_for_bbl(bbl):
    """
    Query NYC Open Data API for RPAD (Property Tax) data by BBL
    Returns (data_dict, error_message) tuple
    """
    try:
        # RPAD BBL format: separate boro, block, lot components
        boro = bbl[0]
        block = str(int(bbl[1:6]))  # Remove leading zeros
        lot = str(int(bbl[6:10]))   # Remove leading zeros
        
        params = {
            "$where": f"boro='{boro}' AND block='{block}' AND lot='{lot}'",
            "$limit": 1
        }
        
        response = requests.get(RPAD_API_BASE, params=params, timeout=10)
        response.raise_for_status()
        time.sleep(API_DELAY)
        
        data = response.json()
        if not data:
            return None, None  # Not found, but not an error
            
        record = data[0]
        
        result = {
            'owner_name_rpad': record.get('owner'),
            'assessed_land_value': int(float(record.get('avland', 0) or 0)),
            'assessed_total_value': int(float(record.get('avtot', 0) or 0))
        }
        return result, None
        
    except Exception as e:
        return None, f"RPAD API error: {str(e)}"


def get_hpd_data_for_bbl(bbl):
    """
    Query NYC HPD APIs for owner, violations, and complaints data
    Returns (data_dict, error_message) tuple
    """
    try:
        boro = bbl[0]
        block = str(int(bbl[1:6]))
        lot = str(int(bbl[6:10]))
        
        result = {
            'owner_name_hpd': None,
            'hpd_registration_id': None,
            'hpd_open_violations': 0,
            'hpd_total_violations': 0,
            'hpd_open_complaints': 0,
            'hpd_total_complaints': 0
        }
        
        # 1. Get HPD registration (most recent)
        params = {
            'boroid': boro,
            'block': block,
            'lot': lot,
            '$order': 'registrationenddate DESC',
            '$limit': 1
        }
        r = requests.get(HPD_REGISTRATION_API, params=params, timeout=10)
        r.raise_for_status()
        time.sleep(API_DELAY)
        
        registration = r.json()
        if not registration:
            return result, None  # Building not in HPD (not an error)
        
        reg_id = registration[0].get('registrationid')
        result['hpd_registration_id'] = reg_id
        
        # 2. Get owner from HPD contacts (try multiple contact types)
        if reg_id:
            # Try HeadOfficer first (best quality), then CorporateOwner, then IndividualOwner
            for contact_type in ['HeadOfficer', 'CorporateOwner', 'IndividualOwner']:
                r = requests.get(HPD_CONTACTS_API, 
                               params={'registrationid': reg_id, 'type': contact_type, '$limit': 1},
                               timeout=10)
                r.raise_for_status()
                time.sleep(API_DELAY)
                
                contacts = r.json()
                if contacts:
                    contact = contacts[0]
                    corp_name = contact.get('corporationname', '')
                    first_name = contact.get('firstname', '')
                    last_name = contact.get('lastname', '')
                    result['owner_name_hpd'] = corp_name if corp_name else f"{first_name} {last_name}".strip()
                    break  # Found owner, stop trying other types
        
        # 3. Get violations count
        r = requests.get(HPD_VIOLATIONS_API,
                        params={'boroid': boro, 'block': block, 'lot': lot, 
                               '$select': 'currentstatus', '$limit': 1000},
                        timeout=10)
        r.raise_for_status()
        time.sleep(API_DELAY)
        
        violations = r.json()
        result['hpd_total_violations'] = len(violations)
        result['hpd_open_violations'] = sum(1 for v in violations 
                                            if v.get('currentstatus') not in ['VIOLATION CLOSED', 'VIOLATION DISMISSED'])
        
        # 4. Get complaints count using public Housing Maintenance Code Complaints API
        try:
            # This dataset uses BBL directly (10-digit format), which is more reliable
            r = requests.get(HPD_COMPLAINTS_API,
                            params={'bbl': bbl, '$select': 'complaint_status,problem_status', '$limit': 5000},
                            timeout=10)
            r.raise_for_status()
            time.sleep(API_DELAY)
            
            complaints = r.json()
            result['hpd_total_complaints'] = len(complaints)
            # Count open complaints (either complaint_status or problem_status is OPEN)
            result['hpd_open_complaints'] = sum(1 for c in complaints 
                                               if c.get('complaint_status') == 'OPEN' or c.get('problem_status') == 'OPEN')
        except Exception as e:
            # Log the error but don't fail the entire enrichment
            print(f"      ⚠️  Complaints API error: {str(e)}")
            pass
        
        return result, None
        
    except Exception as e:
        return None, f"HPD API error: {str(e)}"


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
    
    # Get buildings that need data from ANY source
    # Only select buildings where:
    # 1. At least one owner field is NULL (missing data), AND
    # 2. Never attempted (last_updated IS NULL), OR last updated >30 days ago
    # This ensures new buildings get enriched immediately and old data gets refreshed
    cur.execute("""
        SELECT id, bbl, address
        FROM buildings
        WHERE bbl IS NOT NULL
        AND (current_owner_name IS NULL OR owner_name_rpad IS NULL OR owner_name_hpd IS NULL)
        AND (last_updated IS NULL OR last_updated < NOW() - INTERVAL '30 days')
        ORDER BY id
    """)
    
    buildings = cur.fetchall()
    total = len(buildings)
    print(f"\n📊 Found {total} buildings to enrich (never attempted or >30 days old)")
    
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
            
            if sources:
                print(f"   ✓ Already enriched ({' + '.join(sources)})")
                already_enriched += 1
            else:
                print(f"   ℹ️  No data found in any source - marking as attempted")
                # Mark as attempted to avoid re-querying on future runs
                try:
                    cur.execute("""
                        UPDATE buildings
                        SET last_updated = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (building_id,))
                    conn.commit()
                except Exception as e:
                    print(f"   ❌ Database error: {e}")
                failed += 1
            continue
        
        # Skip if errors occurred
        if pluto_error or rpad_error or hpd_error:
            failed += 1
            continue
        
        # Build update query dynamically
        update_parts = []
        update_values = []
        
        # PLUTO data (corporate ownership)
        if pluto_data:
            update_parts.extend([
                "current_owner_name = %s",
                "building_class = %s",
                "land_use = %s",
                "residential_units = %s",
                "total_units = %s",
                "num_floors = %s",
                "building_sqft = %s",
                "lot_sqft = %s",
                "year_built = %s",
                "year_altered = %s"
            ])
            update_values.extend([
                pluto_data['owner_name'],
                pluto_data['building_class'],
                pluto_data['land_use'],
                pluto_data['residential_units'],
                pluto_data['total_units'],
                pluto_data['num_floors'],
                pluto_data['building_sqft'],
                pluto_data['lot_sqft'],
                pluto_data['year_built'],
                pluto_data['year_altered']
            ])
            pluto_success += 1
            print(f"   ✅ PLUTO: {pluto_data['owner_name']}")
            if pluto_data['year_altered']:
                print(f"      📅 Built: {pluto_data['year_built']}, Altered: {pluto_data['year_altered']}")
        
        # RPAD data (current taxpayer + assessed values)
        if rpad_data:
            update_parts.extend([
                "owner_name_rpad = %s",
                "assessed_land_value = %s",
                "assessed_total_value = %s"
            ])
            update_values.extend([
                rpad_data['owner_name_rpad'],
                rpad_data['assessed_land_value'],
                rpad_data['assessed_total_value']
            ])
            rpad_success += 1
            print(f"   💰 RPAD: {rpad_data['owner_name_rpad']}")
            print(f"      💵 Assessed: ${rpad_data['assessed_total_value']:,}")
        
        # HPD data (registered owner + quality indicators)
        if hpd_data and hpd_data.get('owner_name_hpd'):
            update_parts.extend([
                "owner_name_hpd = %s",
                "hpd_registration_id = %s",
                "hpd_open_violations = %s",
                "hpd_total_violations = %s",
                "hpd_open_complaints = %s",
                "hpd_total_complaints = %s"
            ])
            update_values.extend([
                hpd_data['owner_name_hpd'],
                hpd_data['hpd_registration_id'],
                hpd_data['hpd_open_violations'],
                hpd_data['hpd_total_violations'],
                hpd_data['hpd_open_complaints'],
                hpd_data['hpd_total_complaints']
            ])
            hpd_success += 1
            print(f"   🏘️  HPD: {hpd_data['owner_name_hpd']}")
            if hpd_data['hpd_total_violations'] > 0:
                print(f"      ⚠️  Violations: {hpd_data['hpd_open_violations']} open / {hpd_data['hpd_total_violations']} total")
            if hpd_data['hpd_total_complaints'] > 0:
                print(f"      📋 Complaints: {hpd_data['hpd_open_complaints']} open / {hpd_data['hpd_total_complaints']} total")
        
        # Execute update
        update_parts.append("last_updated = CURRENT_TIMESTAMP")
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
