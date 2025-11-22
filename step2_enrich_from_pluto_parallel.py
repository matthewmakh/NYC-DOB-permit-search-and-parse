#!/usr/bin/env python3
"""
Step 2 (Parallel): Tri-Source Building Enrichment (PLUTO + RPAD + HPD)

OPTIMIZED FOR RAILWAY:
- Batches of 2000 buildings (progress saved incrementally)
- Parallel API calls (8 workers, each processes different BBLs - NO lock contention)
- Skip already-enriched buildings (only process NULL owner fields)
- Expected: 49K buildings in ~10-15 minutes (first run), <30 seconds daily

Data Sources:
1. NYC PLUTO (MapPLUTO) - Corporate ownership, building characteristics
2. NYC RPAD (Property Tax) - Current taxpayer, assessed values
3. NYC HPD (Housing Preservation) - Registered owner, violations, complaints
"""

import psycopg2
import psycopg2.extras
import os
import sys
import requests
import time
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Optional

load_dotenv()

# Database configuration
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
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
HPD_COMPLAINTS_API = "https://data.cityofnewyork.us/resource/uwyv-629c.json"

# Configuration
API_DELAY = 0.05  # Faster with parallel workers
BATCH_SIZE = 100   # Process 100 buildings at a time (more visibility)
NUM_WORKERS = 3    # Parallel API workers (optimal balance: speed + readability + API limits)


def get_pluto_data_for_bbl(bbl: str) -> Tuple[Optional[Dict], Optional[str]]:
    """Query NYC PLUTO API for building data by BBL"""
    try:
        params = {"$where": f"bbl='{bbl}'", "$limit": 1}
        response = requests.get(PLUTO_API_BASE, params=params, timeout=10)
        response.raise_for_status()
        time.sleep(API_DELAY)
        
        data = response.json()
        if not data:
            return None, None
            
        record = data[0]
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
        return None, f"PLUTO error: {str(e)}"


def get_rpad_data_for_bbl(bbl: str) -> Tuple[Optional[Dict], Optional[str]]:
    """Query NYC RPAD (Property Tax) API for owner and assessed values"""
    try:
        params = {"$where": f"parid='{bbl}'", "$limit": 1}
        response = requests.get(RPAD_API_BASE, params=params, timeout=10)
        response.raise_for_status()
        time.sleep(API_DELAY)
        
        data = response.json()
        if not data:
            return None, None
            
        record = data[0]
        result = {
            'owner_name_rpad': record.get('owner'),
            'assessed_land_value': int(float(record.get('av_land', 0) or 0)),
            'assessed_total_value': int(float(record.get('av_tot', 0) or 0))
        }
        return result, None
    except Exception as e:
        return None, f"RPAD error: {str(e)}"


def get_hpd_data_for_bbl(bbl: str) -> Tuple[Optional[Dict], Optional[str]]:
    """Query NYC HPD APIs for registered owner and quality indicators"""
    try:
        # Parse BBL
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
        
        # 1. Get HPD registration
        r = requests.get(HPD_REGISTRATION_API,
                        params={'boroid': boro, 'block': block, 'lot': lot, '$limit': 1},
                        timeout=10)
        r.raise_for_status()
        time.sleep(API_DELAY)
        
        registrations = r.json()
        if not registrations:
            return None, None
        
        reg = registrations[0]
        result['hpd_registration_id'] = reg.get('registrationid')
        
        # 2. Get owner from contacts
        if result['hpd_registration_id']:
            r = requests.get(HPD_CONTACTS_API,
                            params={'registrationid': result['hpd_registration_id'],
                                   'type': 'HeadOfficer', '$limit': 1},
                            timeout=10)
            r.raise_for_status()
            time.sleep(API_DELAY)
            
            contacts = r.json()
            if contacts:
                contact = contacts[0]
                first = contact.get('firstname', '')
                last = contact.get('lastname', '')
                corp = contact.get('corporationname', '')
                result['owner_name_hpd'] = corp if corp else f"{first} {last}".strip()
        
        # 3. Get violations count
        r = requests.get(HPD_VIOLATIONS_API,
                        params={'boroughid': boro, 'block': block, 'lot': lot,
                               '$select': 'currentstatus', '$limit': 1000},
                        timeout=10)
        r.raise_for_status()
        time.sleep(API_DELAY)
        
        violations = r.json()
        result['hpd_total_violations'] = len(violations)
        result['hpd_open_violations'] = sum(1 for v in violations 
                                           if v.get('currentstatus') not in ['VIOLATION CLOSED', 'VIOLATION DISMISSED'])
        
        # 4. Get complaints count (optional)
        try:
            r = requests.get(HPD_COMPLAINTS_API,
                            params={'boroughid': boro, 'block': block, 'lot': lot,
                                   '$select': 'status', '$limit': 1000},
                            timeout=10)
            r.raise_for_status()
            time.sleep(API_DELAY)
            
            complaints = r.json()
            result['hpd_total_complaints'] = len(complaints)
            result['hpd_open_complaints'] = sum(1 for c in complaints 
                                               if c.get('status') not in ['CLOSE', 'CLOSED'])
        except:
            pass
        
        return result, None
    except Exception as e:
        return None, f"HPD error: {str(e)}"


def enrich_single_building(building: Dict, worker_id: int) -> Dict:
    """
    Enrich a single building - called by parallel workers
    Each worker processes DIFFERENT buildings (no lock contention)
    """
    bbl = building['bbl']
    building_id = building['id']
    address = building['address']
    
    print(f"   [Worker {worker_id}] Fetching {address[:50]}...", flush=True)
    
    # Fetch data from all three sources in parallel (per building)
    pluto_data, pluto_error = get_pluto_data_for_bbl(bbl)
    rpad_data, rpad_error = get_rpad_data_for_bbl(bbl)
    hpd_data, hpd_error = get_hpd_data_for_bbl(bbl)
    
    # Show what we found
    sources_found = []
    if pluto_data:
        sources_found.append(f"PLUTO")
    if rpad_data:
        sources_found.append(f"RPAD")
    if hpd_data and hpd_data.get('owner_name_hpd'):
        sources_found.append(f"HPD")
    
    if sources_found:
        print(f"   [Worker {worker_id}] ✅ Found: {' + '.join(sources_found)}", flush=True)
    else:
        print(f"   [Worker {worker_id}] ℹ️  No data", flush=True)
    
    return {
        'building_id': building_id,
        'bbl': bbl,
        'address': address,
        'pluto_data': pluto_data,
        'rpad_data': rpad_data,
        'hpd_data': hpd_data,
        'pluto_error': pluto_error,
        'rpad_error': rpad_error,
        'hpd_error': hpd_error,
        'worker_id': worker_id
    }


def update_building_in_db(conn, result: Dict) -> bool:
    """Update a single building with enriched data"""
    cur = conn.cursor()
    
    try:
        pluto_data = result['pluto_data']
        rpad_data = result['rpad_data']
        hpd_data = result['hpd_data']
        
        # Build update query dynamically
        update_parts = []
        update_values = []
        
        # PLUTO data
        if pluto_data:
            update_parts.extend([
                "current_owner_name = %s", "building_class = %s", "land_use = %s",
                "residential_units = %s", "total_units = %s", "num_floors = %s",
                "building_sqft = %s", "lot_sqft = %s", "year_built = %s", "year_altered = %s"
            ])
            update_values.extend([
                pluto_data['owner_name'], pluto_data['building_class'], pluto_data['land_use'],
                pluto_data['residential_units'], pluto_data['total_units'], pluto_data['num_floors'],
                pluto_data['building_sqft'], pluto_data['lot_sqft'],
                pluto_data['year_built'], pluto_data['year_altered']
            ])
        
        # RPAD data
        if rpad_data:
            update_parts.extend([
                "owner_name_rpad = %s", "assessed_land_value = %s", "assessed_total_value = %s"
            ])
            update_values.extend([
                rpad_data['owner_name_rpad'], rpad_data['assessed_land_value'], rpad_data['assessed_total_value']
            ])
        
        # HPD data
        if hpd_data and hpd_data.get('owner_name_hpd'):
            update_parts.extend([
                "owner_name_hpd = %s", "hpd_registration_id = %s",
                "hpd_open_violations = %s", "hpd_total_violations = %s",
                "hpd_open_complaints = %s", "hpd_total_complaints = %s"
            ])
            update_values.extend([
                hpd_data['owner_name_hpd'], hpd_data['hpd_registration_id'],
                hpd_data['hpd_open_violations'], hpd_data['hpd_total_violations'],
                hpd_data['hpd_open_complaints'], hpd_data['hpd_total_complaints']
            ])
        
        # Always update timestamp
        update_parts.append("last_updated = CURRENT_TIMESTAMP")
        update_values.append(result['building_id'])
        
        if len(update_parts) > 1:  # More than just timestamp
            query = f"UPDATE buildings SET {', '.join(update_parts)} WHERE id = %s"
            cur.execute(query, update_values)
            return True
        else:
            # Mark as attempted even if no data found
            cur.execute("UPDATE buildings SET last_updated = CURRENT_TIMESTAMP WHERE id = %s",
                       (result['building_id'],))
            return False
            
    except Exception as e:
        print(f"   ❌ DB error for {result['bbl']}: {e}")
        return False
    finally:
        cur.close()


def enrich_buildings_parallel():
    """
    Main parallel enrichment process:
    1. Get buildings needing enrichment (NULL owner fields only)
    2. Process in batches of 2000
    3. Within each batch, use 8 parallel workers for API calls
    4. Each worker processes DIFFERENT buildings (no lock contention)
    5. Commit after each batch (progress saved incrementally)
    """
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    
    print("=" * 70)
    print("🏢 Step 2 (Parallel): Tri-Source Building Enrichment")
    print(f"   Workers: {NUM_WORKERS} | Batch Size: {BATCH_SIZE}")
    print("=" * 70)
    sys.stdout.flush()
    
    # Get buildings that need enrichment (only NULL owner fields)
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
    
    print(f"\n📊 Found {total:,} buildings to enrich")
    
    if not buildings:
        print("   ✅ No buildings need enrichment. All done!")
        cur.close()
        conn.close()
        return
    
    # Process in batches
    total_enriched = 0
    total_pluto = 0
    total_rpad = 0
    total_hpd = 0
    
    for batch_start in range(0, total, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total)
        batch = buildings[batch_start:batch_end]
        batch_num = (batch_start // BATCH_SIZE) + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        
        print(f"\n{'='*70}")
        print(f"📦 Batch {batch_num}/{total_batches}: Buildings {batch_start+1}-{batch_end} ({len(batch)} buildings)")
        print(f"{'='*70}")
        sys.stdout.flush()
        
        # Split batch among workers (each worker gets DIFFERENT buildings)
        chunk_size = len(batch) // NUM_WORKERS + 1
        chunks = [batch[i:i+chunk_size] for i in range(0, len(batch), chunk_size)]
        
        # Parallel fetch data for this batch
        results = []
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
            futures = []
            for worker_id, chunk in enumerate(chunks, 1):
                for building in chunk:
                    future = executor.submit(enrich_single_building, building, worker_id)
                    futures.append(future)
            
            # Collect results as they complete
            print(f"\n   🚀 Starting {NUM_WORKERS} workers to fetch data...\n", flush=True)
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
        
        print(f"\n   💾 Updating database for {len(results)} buildings...\n", flush=True)
        
        # Update database for this batch (sequential to avoid locks)
        batch_enriched = 0
        batch_pluto = 0
        batch_rpad = 0
        batch_hpd = 0
        
        for idx, result in enumerate(results, 1):
            success = update_building_in_db(conn, result)
            
            # Show what we found for each building
            sources = []
            if result['pluto_data']:
                owner = result['pluto_data']['owner_name']
                sources.append(f"PLUTO: {owner[:30]}")
                batch_pluto += 1
            if result['rpad_data']:
                value = result['rpad_data']['assessed_total_value']
                sources.append(f"RPAD: ${value:,}")
                batch_rpad += 1
            if result['hpd_data'] and result['hpd_data'].get('owner_name_hpd'):
                owner = result['hpd_data']['owner_name_hpd']
                sources.append(f"HPD: {owner[:30]}")
                batch_hpd += 1
            
            if sources:
                print(f"   [{idx}/{len(batch)}] ✅ {result['address'][:35]:35} | {' | '.join(sources)}", flush=True)
                batch_enriched += 1
            else:
                print(f"   [{idx}/{len(batch)}] ℹ️  {result['address'][:35]:35} | No data found", flush=True)
        
        # Commit this batch
        conn.commit()
        
        total_enriched += batch_enriched
        total_pluto += batch_pluto
        total_rpad += batch_rpad
        total_hpd += batch_hpd
        
        print(f"   ✅ Batch complete!")
        print(f"      Enriched: {batch_enriched}/{len(batch)}")
        print(f"      PLUTO: {batch_pluto} | RPAD: {batch_rpad} | HPD: {batch_hpd}")
        print(f"      Progress: {batch_end}/{total} ({100*batch_end//total}%)")
    
    print(f"\n{'='*70}")
    print(f"✅ All batches complete!")
    print(f"   Total enriched: {total_enriched:,}/{total:,}")
    print(f"   PLUTO data: {total_pluto:,}")
    print(f"   RPAD data: {total_rpad:,}")
    print(f"   HPD data: {total_hpd:,}")
    print(f"{'='*70}")
    
    # Show sample results
    cur.execute("""
        SELECT bbl, address, current_owner_name, owner_name_rpad, owner_name_hpd,
               assessed_total_value, hpd_total_violations, residential_units, year_built
        FROM buildings
        WHERE current_owner_name IS NOT NULL OR owner_name_rpad IS NOT NULL OR owner_name_hpd IS NOT NULL
        ORDER BY last_updated DESC
        LIMIT 3
    """)
    
    results = cur.fetchall()
    if results:
        print(f"\n📋 Sample recently enriched buildings:")
        for r in results:
            print(f"\n   🏢 {r['address']} (BBL: {r['bbl']})")
            if r['current_owner_name']:
                print(f"      Owner (PLUTO): {r['current_owner_name']}")
            if r['owner_name_rpad']:
                print(f"      Owner (RPAD): {r['owner_name_rpad']}")
            if r['owner_name_hpd']:
                print(f"      Owner (HPD): {r['owner_name_hpd']}")
            if r['assessed_total_value']:
                print(f"      Assessed: ${r['assessed_total_value']:,}")
            if r['hpd_total_violations']:
                print(f"      HPD Violations: {r['hpd_total_violations']}")
            print(f"      {r['residential_units']} units, built {r['year_built']}")
    
    cur.close()
    conn.close()


if __name__ == "__main__":
    enrich_buildings_parallel()
