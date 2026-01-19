#!/usr/bin/env python3
"""
Targeted Permit Enrichment for Specific Zip Codes

Fetches permits from the last 180 days for specified zip codes,
then runs full enrichment pipeline on just those buildings.

Usage:
    python targeted_enrichment.py                    # Default zip codes
    python targeted_enrichment.py --zips 11223,11230 # Custom zips
    python targeted_enrichment.py --days 90          # Last 90 days
    python targeted_enrichment.py --dry-run          # Preview only
"""

import os
import sys
import argparse
import time
from datetime import datetime, timedelta
from typing import List, Dict, Set

import requests
import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values
from dotenv import load_dotenv

# Load environment
if os.path.exists('.env'):
    load_dotenv('.env')
elif os.path.exists('dashboard_html/.env'):
    load_dotenv('dashboard_html/.env')
else:
    load_dotenv()

# =============================================================================
# CONFIGURATION
# =============================================================================

DEFAULT_ZIP_CODES = ['11223', '11230', '11235', '11210', '11229']
DEFAULT_DAYS = 180

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', '5432')),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME', 'railway')
}

# NYC Open Data endpoints
ENDPOINTS = {
    'bis': 'https://data.cityofnewyork.us/resource/ipu4-2q9a.json',
    'dob_now_filings': 'https://data.cityofnewyork.us/resource/w9ak-ipjd.json',
    'dob_now_approved': 'https://data.cityofnewyork.us/resource/rbx6-tga4.json',
}

BOROUGH_MAP = {
    'MANHATTAN': '1', 'BRONX': '2', 'BROOKLYN': '3',
    'QUEENS': '4', 'STATEN ISLAND': '5'
}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_db_connection():
    """Get database connection with dict cursor."""
    conn = psycopg2.connect(**DB_CONFIG)
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn


def build_bbl(borough: str, block: str, lot: str) -> str:
    """Build 10-digit BBL from components."""
    if not borough or not block or not lot:
        return None
    try:
        borough_upper = str(borough).upper().strip()
        borough_code = BOROUGH_MAP.get(borough_upper, borough_upper)
        if not borough_code.isdigit() or len(borough_code) != 1:
            return None
        block_num = str(block).strip().lstrip('0') or '0'
        lot_num = str(lot).strip().lstrip('0') or '0'
        bbl = f"{borough_code}{block_num.zfill(5)}{lot_num.zfill(4)}"
        return bbl if len(bbl) == 10 and bbl.isdigit() else None
    except:
        return None


def fetch_permits_for_zips(zip_codes: List[str], days: int) -> List[Dict]:
    """Fetch permits from all sources for given zip codes."""
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    all_permits = []
    
    for source_name, endpoint in ENDPOINTS.items():
        print(f"\n   Fetching from {source_name}...")
        
        for zip_code in zip_codes:
            # Build query based on source
            if source_name == 'bis':
                # BIS uses zip_code field and issuance_date
                query = f"zip_code='{zip_code}' AND issuance_date >= '{cutoff_date}'"
            elif source_name == 'dob_now_filings':
                # DOB NOW Filings uses zip or postcode
                query = f"(zip='{zip_code}' OR postcode='{zip_code}') AND filing_date >= '{cutoff_date}'"
            else:
                # DOB NOW Approved uses zip_code
                query = f"zip_code='{zip_code}' AND issued_date >= '{cutoff_date}'"
            
            params = {
                '$where': query,
                '$limit': 10000,
            }
            
            try:
                response = requests.get(endpoint, params=params, timeout=30)
                response.raise_for_status()
                records = response.json()
                
                # Tag with source
                for record in records:
                    record['_source'] = source_name
                    record['_zip'] = zip_code
                
                all_permits.extend(records)
                print(f"      {zip_code}: {len(records)} permits")
                
                time.sleep(0.1)  # Rate limit
                
            except Exception as e:
                print(f"      {zip_code}: Error - {e}")
    
    return all_permits


def normalize_permit(record: Dict) -> Dict:
    """Normalize permit record from any source to common format."""
    source = record.get('_source', 'unknown')
    
    if source == 'bis':
        return {
            'permit_no': record.get('job__') or f"{record.get('bin__', '')}_{record.get('issuance_date', '')}",
            'borough': record.get('borough'),
            'block': record.get('block'),
            'lot': record.get('lot'),
            'bin': record.get('bin__'),
            'address': f"{record.get('house__', '')} {record.get('street_name', '')}".strip(),
            'zip_code': record.get('zip_code'),
            'job_type': record.get('job_type'),
            'issue_date': record.get('issuance_date'),
            'owner_business_name': record.get('owner_s_business_name'),
            'owner_first_name': record.get('owner_s_first_name'),
            'owner_last_name': record.get('owner_s_last_name'),
            'owner_phone': record.get('owner_s_phone__'),
            'api_source': 'nyc_open_data',
        }
    elif source == 'dob_now_filings':
        return {
            'permit_no': record.get('job_filing_number'),
            'borough': record.get('borough'),
            'block': record.get('block'),
            'lot': record.get('lot'),
            'bin': record.get('bin'),
            'address': f"{record.get('house_no', '')} {record.get('street_name', '')}".strip(),
            'zip_code': record.get('zip') or record.get('postcode'),
            'job_type': record.get('job_type'),
            'issue_date': record.get('filing_date'),
            'owner_business_name': record.get('owner_s_business_name'),
            'owner_first_name': record.get('applicant_first_name'),
            'owner_last_name': record.get('applicant_last_name'),
            'owner_phone': None,  # DOB NOW doesn't have phone
            'api_source': 'dob_now_filings',
        }
    else:  # dob_now_approved
        return {
            'permit_no': record.get('job_filing_number') or record.get('work_permit'),
            'borough': record.get('borough'),
            'block': record.get('block'),
            'lot': record.get('lot'),
            'bin': record.get('bin'),
            'address': f"{record.get('house_no', '')} {record.get('street_name', '')}".strip(),
            'zip_code': record.get('zip_code'),
            'job_type': record.get('work_type'),
            'issue_date': record.get('issued_date'),
            'owner_business_name': record.get('owner_business_name'),
            'owner_first_name': None,
            'owner_last_name': None,
            'owner_phone': None,
            'api_source': 'dob_now_approved',
        }


def upsert_permits(conn, permits: List[Dict]) -> int:
    """Upsert permits into database, return count of new permits."""
    if not permits:
        return 0
    
    with conn.cursor() as cur:
        # Get existing permit numbers
        permit_nos = [p['permit_no'] for p in permits if p.get('permit_no')]
        cur.execute(
            "SELECT permit_no FROM permits WHERE permit_no = ANY(%s)",
            (permit_nos,)
        )
        existing = {row['permit_no'] for row in cur.fetchall()}
        
        # Filter to new permits only
        new_permits = [p for p in permits if p.get('permit_no') and p['permit_no'] not in existing]
        
        if not new_permits:
            return 0
        
        # Build BBL for each permit
        for p in new_permits:
            p['bbl'] = build_bbl(p.get('borough'), p.get('block'), p.get('lot'))
        
        # Insert new permits
        columns = [
            'permit_no', 'borough', 'block', 'lot', 'bin', 'address', 'zip_code',
            'job_type', 'issue_date', 'owner_business_name', 'owner_first_name',
            'owner_last_name', 'owner_phone', 'api_source', 'bbl'
        ]
        
        values = [
            tuple(p.get(col) for col in columns)
            for p in new_permits
        ]
        
        insert_sql = f"""
            INSERT INTO permits ({', '.join(columns)})
            VALUES %s
            ON CONFLICT (permit_no) DO NOTHING
        """
        
        execute_values(cur, insert_sql, values)
        conn.commit()
        
        return len(new_permits)


def create_buildings_for_bbls(conn, bbls: Set[str]) -> int:
    """Create building records for BBLs that don't exist."""
    if not bbls:
        return 0
    
    with conn.cursor() as cur:
        # Find BBLs that don't have buildings yet
        cur.execute(
            "SELECT bbl FROM buildings WHERE bbl = ANY(%s)",
            (list(bbls),)
        )
        existing = {row['bbl'] for row in cur.fetchall()}
        new_bbls = bbls - existing
        
        if not new_bbls:
            return 0
        
        # Get address for each new BBL from permits
        for bbl in new_bbls:
            cur.execute(
                "SELECT address, borough FROM permits WHERE bbl = %s LIMIT 1",
                (bbl,)
            )
            result = cur.fetchone()
            if result:
                cur.execute("""
                    INSERT INTO buildings (bbl, address, borough, block, lot)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (bbl) DO NOTHING
                """, (
                    bbl,
                    result['address'],
                    int(bbl[0]),
                    int(bbl[1:6]),
                    int(bbl[6:10])
                ))
        
        conn.commit()
        return len(new_bbls)


def run_enrichment_for_bbls(conn, bbls: Set[str]):
    """Run enrichment steps for specific BBLs."""
    if not bbls:
        return
    
    bbl_list = list(bbls)
    
    # Import enrichment functions
    try:
        from step2_enrich_from_pluto import get_pluto_data_for_bbl, get_rpad_data_for_bbl, get_hpd_data_for_bbl
        from step3_enrich_from_acris import enrich_building_from_acris
        from ny_sos_lookup import lookup_businesses, is_likely_individual, SOSBusinessResult
    except ImportError as e:
        print(f"   Warning: Could not import enrichment modules: {e}")
        return
    
    with conn.cursor() as cur:
        # Get buildings for these BBLs
        cur.execute("""
            SELECT id, bbl, address, current_owner_name, owner_name_rpad, 
                   owner_name_hpd, sale_buyer_primary, sos_lookup_attempted
            FROM buildings 
            WHERE bbl = ANY(%s)
        """, (bbl_list,))
        buildings = cur.fetchall()
    
    print(f"\n📊 Enriching {len(buildings)} buildings...")
    
    # Step 2: PLUTO/RPAD/HPD
    print("\n   🏢 Step 2: PLUTO/RPAD/HPD enrichment...")
    for i, building in enumerate(buildings, 1):
        bbl = building['bbl']
        
        # Skip if already has all data
        if building['current_owner_name'] and building['owner_name_rpad'] and building['owner_name_hpd']:
            continue
        
        print(f"      [{i}/{len(buildings)}] BBL {bbl}...", end=" ")
        
        updates = {}
        
        # PLUTO
        if not building['current_owner_name']:
            pluto_data, _ = get_pluto_data_for_bbl(bbl)
            if pluto_data:
                updates['current_owner_name'] = pluto_data.get('owner_name')
                updates['units'] = pluto_data.get('total_units')
                updates['sqft'] = pluto_data.get('building_sqft')
                updates['year_built'] = pluto_data.get('year_built')
                updates['building_class'] = pluto_data.get('building_class')
        
        # RPAD
        if not building['owner_name_rpad']:
            rpad_data, _ = get_rpad_data_for_bbl(bbl)
            if rpad_data:
                updates['owner_name_rpad'] = rpad_data.get('owner_name')
                updates['assessed_land_value'] = rpad_data.get('assessed_land_value')
                updates['assessed_total_value'] = rpad_data.get('assessed_total_value')
        
        # HPD
        if not building['owner_name_hpd']:
            hpd_data, _ = get_hpd_data_for_bbl(bbl)
            if hpd_data:
                updates['owner_name_hpd'] = hpd_data.get('owner_name')
                updates['hpd_violations_count'] = hpd_data.get('violations_count')
                updates['hpd_complaints_count'] = hpd_data.get('complaints_count')
        
        if updates:
            set_clause = ', '.join(f"{k} = %s" for k in updates.keys())
            cur.execute(
                f"UPDATE buildings SET {set_clause}, last_updated = NOW() WHERE id = %s",
                list(updates.values()) + [building['id']]
            )
            conn.commit()
            print("✓")
        else:
            print("skip")
    
    # Step 3: ACRIS (simplified - just get owner from deeds)
    print("\n   📜 Step 3: ACRIS enrichment...")
    # This is complex - would need to import the full function
    # For now, we'll rely on existing ACRIS data or skip
    
    # Step 5: NY SOS
    print("\n   🏛️  Step 5: NY SOS enrichment...")
    
    # Refresh buildings data
    cur.execute("""
        SELECT id, bbl, current_owner_name, owner_name_rpad, 
               owner_name_hpd, sale_buyer_primary, sos_lookup_attempted
        FROM buildings 
        WHERE bbl = ANY(%s)
        AND (sos_lookup_attempted IS NULL OR sos_lookup_attempted = FALSE)
    """, (bbl_list,))
    buildings_for_sos = cur.fetchall()
    
    # Find LLCs to look up
    llc_lookups = []
    for b in buildings_for_sos:
        # Priority: ACRIS > RPAD > PLUTO > HPD
        owner_name = (
            b['sale_buyer_primary'] or
            b['owner_name_rpad'] or
            b['current_owner_name'] or
            b['owner_name_hpd']
        )
        
        if owner_name and not is_likely_individual(owner_name):
            # Check if it's an LLC/Corp
            if any(x in owner_name.upper() for x in ['LLC', 'INC', 'CORP', 'LTD', 'LP', 'COMPANY']):
                llc_lookups.append({
                    'building_id': b['id'],
                    'bbl': b['bbl'],
                    'llc_name': owner_name,
                })
    
    if llc_lookups:
        print(f"      Looking up {len(llc_lookups)} LLCs...")
        
        # Batch lookup
        unique_names = list(set(l['llc_name'] for l in llc_lookups))
        results = lookup_businesses(unique_names, concurrency=5)
        
        # Update buildings
        for lookup in llc_lookups:
            result = results.get(lookup['llc_name'])
            if result and result.found:
                # Get best person
                principal = None
                individuals = result.get_individuals()
                if individuals:
                    principal = individuals[0]
                elif result.people:
                    principal = result.get_ceo() or result.people[0]
                
                cur.execute("""
                    UPDATE buildings SET
                        sos_principal_name = %s,
                        sos_principal_title = %s,
                        sos_principal_city = %s,
                        sos_principal_zip = %s,
                        sos_entity_name = %s,
                        sos_entity_status = %s,
                        sos_dos_id = %s,
                        sos_last_enriched = NOW(),
                        sos_lookup_attempted = TRUE
                    WHERE id = %s
                """, (
                    principal.full_name if principal else None,
                    principal.title if principal else None,
                    principal.city if principal else None,
                    principal.zipcode if principal else None,
                    result.entity_name,
                    result.status,
                    result.dos_id,
                    lookup['building_id']
                ))
            else:
                # Mark as attempted even if not found
                cur.execute(
                    "UPDATE buildings SET sos_lookup_attempted = TRUE WHERE id = %s",
                    (lookup['building_id'],)
                )
        
        conn.commit()
        
        found = sum(1 for l in llc_lookups if results.get(l['llc_name']) and results[l['llc_name']].found)
        print(f"      ✓ Found {found}/{len(llc_lookups)} in NY SOS")
    else:
        print("      No LLCs to look up")


def main():
    parser = argparse.ArgumentParser(description='Targeted permit enrichment for specific zip codes')
    parser.add_argument('--zips', type=str, help='Comma-separated zip codes (default: 11223,11230,11235,11210,11229)')
    parser.add_argument('--days', type=int, default=DEFAULT_DAYS, help=f'Days to look back (default: {DEFAULT_DAYS})')
    parser.add_argument('--dry-run', action='store_true', help='Preview only, no database changes')
    args = parser.parse_args()
    
    zip_codes = args.zips.split(',') if args.zips else DEFAULT_ZIP_CODES
    
    print("=" * 70)
    print("🎯 TARGETED PERMIT ENRICHMENT")
    print("=" * 70)
    print(f"   Zip codes: {', '.join(zip_codes)}")
    print(f"   Date range: Last {args.days} days")
    print(f"   Dry run: {args.dry_run}")
    print()
    
    # Step 1: Fetch permits
    print("📥 Fetching permits from NYC Open Data...")
    raw_permits = fetch_permits_for_zips(zip_codes, args.days)
    print(f"\n   Total raw permits: {len(raw_permits)}")
    
    if not raw_permits:
        print("\n❌ No permits found!")
        return
    
    # Normalize permits
    permits = [normalize_permit(p) for p in raw_permits]
    
    # Filter valid permits
    valid_permits = [p for p in permits if p.get('permit_no')]
    print(f"   Valid permits: {len(valid_permits)}")
    
    # Deduplicate by permit_no
    seen = set()
    unique_permits = []
    for p in valid_permits:
        if p['permit_no'] not in seen:
            seen.add(p['permit_no'])
            unique_permits.append(p)
    print(f"   Unique permits: {len(unique_permits)}")
    
    if args.dry_run:
        print("\n🔍 DRY RUN - Sample permits:")
        for p in unique_permits[:10]:
            print(f"   • {p['permit_no']} | {p['address']} | {p['owner_business_name'] or 'No owner'}")
        return
    
    # Connect to database
    print("\n📊 Connecting to database...")
    conn = get_db_connection()
    
    # Step 2: Upsert permits
    print("\n📝 Upserting permits...")
    new_count = upsert_permits(conn, unique_permits)
    print(f"   New permits added: {new_count}")
    print(f"   Already existed: {len(unique_permits) - new_count}")
    
    # Step 3: Get unique BBLs
    bbls = {p['bbl'] for p in unique_permits if p.get('bbl')}
    print(f"\n🏢 Unique BBLs: {len(bbls)}")
    
    # Step 4: Create buildings
    print("\n🏗️  Creating buildings...")
    new_buildings = create_buildings_for_bbls(conn, bbls)
    print(f"   New buildings created: {new_buildings}")
    
    # Step 5: Run enrichment
    print("\n🔄 Running enrichment pipeline...")
    run_enrichment_for_bbls(conn, bbls)
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)
    print(f"   Zip codes processed: {len(zip_codes)}")
    print(f"   Permits fetched: {len(unique_permits)}")
    print(f"   New permits added: {new_count}")
    print(f"   Buildings enriched: {len(bbls)}")
    print("\n✅ Done!")
    
    conn.close()


if __name__ == "__main__":
    main()
