#!/usr/bin/env python3
"""
FAST Targeted BBL Enrichment - Uses parallel processing like the main pipeline.
"""

import os
import sys
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple, Optional

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

# Load environment
if os.path.exists('.env'):
    load_dotenv('.env')
elif os.path.exists('dashboard_html/.env'):
    load_dotenv('dashboard_html/.env')
else:
    load_dotenv()

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', '5432')),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME', 'railway')
}

TARGET_ZIPS = ['11223', '11230', '11235', '11210', '11229']

# API Configuration - match the parallel pipeline
API_DELAY = 0.05
NUM_WORKERS = 8
BATCH_SIZE = 100

# API Endpoints
PLUTO_API = "https://data.cityofnewyork.us/resource/64uk-42ks.json"
RPAD_API = "https://data.cityofnewyork.us/resource/yjxr-fw8i.json"
HPD_REGISTRATION_API = "https://data.cityofnewyork.us/resource/tesw-yqqr.json"
HPD_CONTACTS_API = "https://data.cityofnewyork.us/resource/feu5-w2e2.json"
HPD_VIOLATIONS_API = "https://data.cityofnewyork.us/resource/wvxf-dwi5.json"


def get_db_connection():
    conn = psycopg2.connect(**DB_CONFIG)
    return conn


def get_pluto_data(bbl: str) -> Optional[Dict]:
    """Fetch PLUTO data for a BBL."""
    try:
        r = requests.get(PLUTO_API, params={"$where": f"bbl='{bbl}'", "$limit": 1}, timeout=10)
        r.raise_for_status()
        time.sleep(API_DELAY)
        data = r.json()
        if data:
            rec = data[0]
            return {
                'current_owner_name': rec.get('ownername'),
                'building_class': rec.get('bldgclass'),
                'land_use': rec.get('landuse'),
                'residential_units': int(float(rec.get('unitsres', 0) or 0)),
                'total_units': int(float(rec.get('unitstotal', 0) or 0)),
                'num_floors': int(float(rec.get('numfloors', 0) or 0)),
                'building_sqft': int(float(rec.get('bldgarea', 0) or 0)),
                'lot_sqft': int(float(rec.get('lotarea', 0) or 0)),
                'year_built': int(float(rec.get('yearbuilt', 0) or 0)) if rec.get('yearbuilt') else None,
            }
    except:
        pass
    return None


def get_rpad_data(bbl: str) -> Optional[Dict]:
    """Fetch RPAD tax data for a BBL."""
    try:
        r = requests.get(RPAD_API, params={"$where": f"bble='{bbl}'", "$limit": 1}, timeout=10)
        r.raise_for_status()
        time.sleep(API_DELAY)
        data = r.json()
        if data:
            rec = data[0]
            return {
                'owner_name_rpad': rec.get('owner'),
                'assessed_land_value': int(float(rec.get('avtot2', 0) or 0)),
                'assessed_total_value': int(float(rec.get('avtot', 0) or 0)),
            }
    except:
        pass
    return None


def get_hpd_data(bbl: str) -> Optional[Dict]:
    """Fetch HPD data for a BBL."""
    try:
        boro = bbl[0]
        block = bbl[1:6].lstrip('0') or '0'
        lot = bbl[6:10].lstrip('0') or '0'
        
        result = {}
        
        # Get registration
        r = requests.get(HPD_REGISTRATION_API, 
                        params={'boroid': boro, 'block': block, 'lot': lot, '$limit': 1},
                        timeout=10)
        r.raise_for_status()
        time.sleep(API_DELAY)
        
        regs = r.json()
        if regs:
            reg_id = regs[0].get('registrationid')
            result['hpd_registration_id'] = reg_id
            
            # Get owner from contacts
            if reg_id:
                r = requests.get(HPD_CONTACTS_API,
                                params={'registrationid': reg_id, 'type': 'CorporateOwner', '$limit': 1},
                                timeout=10)
                r.raise_for_status()
                time.sleep(API_DELAY)
                
                contacts = r.json()
                if contacts:
                    c = contacts[0]
                    corp = c.get('corporationname', '')
                    first = c.get('firstname', '')
                    last = c.get('lastname', '')
                    result['owner_name_hpd'] = corp if corp else f"{first} {last}".strip()
        
        # Get violations count
        r = requests.get(HPD_VIOLATIONS_API,
                        params={'boroid': boro, 'block': block, 'lot': lot, '$select': 'currentstatus', '$limit': 1000},
                        timeout=10)
        r.raise_for_status()
        time.sleep(API_DELAY)
        
        violations = r.json()
        result['hpd_total_violations'] = len(violations)
        result['hpd_open_violations'] = sum(1 for v in violations if v.get('currentstatus') not in ['VIOLATION CLOSED', 'VIOLATION DISMISSED'])
        
        return result if result else None
    except:
        pass
    return None


def enrich_building(building: Dict) -> Dict:
    """Enrich a single building - called by parallel workers."""
    bbl = building['bbl']
    
    pluto = get_pluto_data(bbl) if not building.get('current_owner_name') else None
    rpad = get_rpad_data(bbl) if not building.get('owner_name_rpad') else None
    hpd = get_hpd_data(bbl) if not building.get('owner_name_hpd') else None
    
    return {
        'id': building['id'],
        'bbl': bbl,
        'pluto': pluto,
        'rpad': rpad,
        'hpd': hpd,
    }


def update_building(conn, result: Dict) -> bool:
    """Update building with enriched data."""
    updates = {}
    
    if result['pluto']:
        updates.update({k: v for k, v in result['pluto'].items() if v})
    if result['rpad']:
        updates.update({k: v for k, v in result['rpad'].items() if v})
    if result['hpd']:
        updates.update({k: v for k, v in result['hpd'].items() if v})
    
    if not updates:
        return False
    
    updates['last_updated'] = datetime.now()
    
    with conn.cursor() as cur:
        set_clause = ', '.join(f"{k} = %s" for k in updates.keys())
        cur.execute(f"UPDATE buildings SET {set_clause} WHERE id = %s", list(updates.values()) + [result['id']])
    
    return True


def main():
    print("=" * 70)
    print("🚀 FAST TARGETED BBL ENRICHMENT (Parallel)")
    print("=" * 70)
    print(f"   Target zip codes: {', '.join(TARGET_ZIPS)}")
    print(f"   Workers: {NUM_WORKERS}, Batch size: {BATCH_SIZE}")
    print()
    
    conn = get_db_connection()
    
    # Get target BBLs
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT DISTINCT b.bbl
            FROM buildings b
            JOIN permits p ON b.bbl = p.bbl
            WHERE p.zip_code = ANY(%s)
            AND b.bbl IS NOT NULL
        """, (TARGET_ZIPS,))
        target_bbls = [row['bbl'] for row in cur.fetchall()]
    
    print(f"📊 Found {len(target_bbls)} BBLs in target zip codes")
    
    # Get buildings needing enrichment
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, bbl, current_owner_name, owner_name_rpad, owner_name_hpd
            FROM buildings
            WHERE bbl = ANY(%s)
            AND (current_owner_name IS NULL OR owner_name_rpad IS NULL OR owner_name_hpd IS NULL)
        """, (target_bbls,))
        buildings = cur.fetchall()
    
    print(f"   {len(buildings)} buildings need enrichment")
    
    if not buildings:
        print("\n✅ All buildings already enriched!")
        return
    
    # Process in batches with parallel workers
    print(f"\n🏢 Step 2: PLUTO/RPAD/HPD enrichment...")
    start = time.time()
    total_updated = 0
    
    for batch_start in range(0, len(buildings), BATCH_SIZE):
        batch = buildings[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(buildings) + BATCH_SIZE - 1) // BATCH_SIZE
        
        # Process batch in parallel
        results = []
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
            futures = {executor.submit(enrich_building, b): b for b in batch}
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    pass
        
        # Update database
        batch_updated = 0
        for result in results:
            if update_building(conn, result):
                batch_updated += 1
        conn.commit()
        
        total_updated += batch_updated
        elapsed = time.time() - start
        rate = (batch_start + len(batch)) / elapsed if elapsed > 0 else 0
        eta = (len(buildings) - batch_start - len(batch)) / rate if rate > 0 else 0
        
        print(f"   Batch {batch_num}/{total_batches}: {batch_updated} updated | "
              f"Total: {total_updated} | ETA: {eta/60:.1f} min")
    
    print(f"\n   ✅ Step 2 complete: {total_updated} buildings updated in {(time.time()-start)/60:.1f} min")
    
    # Step 5: NY SOS
    print("\n🏛️  Step 5: NY SOS enrichment...")
    try:
        from ny_sos_lookup import lookup_businesses, is_likely_individual
        
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, bbl, current_owner_name, owner_name_rpad, 
                       owner_name_hpd, sale_buyer_primary
                FROM buildings
                WHERE bbl = ANY(%s)
                AND sos_lookup_attempted IS NOT TRUE
            """, (target_bbls,))
            sos_buildings = cur.fetchall()
        
        # Find LLCs
        llc_lookups = []
        for b in sos_buildings:
            owner_name = (
                b['sale_buyer_primary'] or
                b['owner_name_rpad'] or
                b['current_owner_name'] or
                b['owner_name_hpd']
            )
            
            if owner_name and not is_likely_individual(owner_name):
                if any(x in owner_name.upper() for x in ['LLC', 'INC', 'CORP', 'LTD', 'LP', 'COMPANY']):
                    llc_lookups.append({
                        'building_id': b['id'],
                        'bbl': b['bbl'],
                        'llc_name': owner_name,
                    })
        
        print(f"   {len(llc_lookups)} LLCs to look up")
        
        if llc_lookups:
            unique_names = list(set(l['llc_name'] for l in llc_lookups))
            print(f"   Looking up {len(unique_names)} unique LLC names...")
            
            results = lookup_businesses(unique_names, concurrency=10)
            
            found = 0
            with conn.cursor() as cur:
                for lookup in llc_lookups:
                    result = results.get(lookup['llc_name'])
                    if result and result.found:
                        found += 1
                        individuals = result.get_individuals()
                        principal = individuals[0] if individuals else (result.get_ceo() or (result.people[0] if result.people else None))
                        
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
                        cur.execute(
                            "UPDATE buildings SET sos_lookup_attempted = TRUE WHERE id = %s",
                            (lookup['building_id'],)
                        )
                conn.commit()
            
            print(f"   ✅ Found {found}/{len(llc_lookups)} in NY SOS")
    except ImportError as e:
        print(f"   ⚠️  Could not import SOS module: {e}")
    
    print("\n" + "=" * 70)
    print("✅ ENRICHMENT COMPLETE")
    print("=" * 70)
    
    conn.close()


if __name__ == "__main__":
    main()
