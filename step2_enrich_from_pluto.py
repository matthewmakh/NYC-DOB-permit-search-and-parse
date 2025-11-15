#!/usr/bin/env python3
"""
Enhanced Building Enrichment - Dual Source (PLUTO + RPAD)

Combines data from:
1. NYC PLUTO (MapPLUTO) - Corporate ownership, building characteristics
2. NYC RPAD (Property Tax) - Current taxpayer, assessed values

Populates:
- current_owner_name (PLUTO) - Corporate entity
- owner_name_rpad (RPAD) - Current taxpayer
- assessed_land_value, assessed_total_value (RPAD)
- year_altered (PLUTO) - Recent renovations
- Plus all existing building data
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
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME')
    
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        raise ValueError("Either DATABASE_URL or DB_HOST/DB_USER/DB_PASSWORD/DB_NAME must be set")
    
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# API endpoints
PLUTO_API_BASE = "https://data.cityofnewyork.us/resource/64uk-42ks.json"
RPAD_API_BASE = "https://data.cityofnewyork.us/resource/yjxr-fw8i.json"

# Rate limiting
API_DELAY = float(os.getenv('API_DELAY', 0.1))  # 100ms delay between API calls


def get_pluto_data(bbl):
    """Query PLUTO for building characteristics and corporate owner"""
    try:
        time.sleep(API_DELAY)  # Rate limiting
        params = {"$where": f"bbl='{bbl}'", "$limit": 1}
        response = requests.get(PLUTO_API_BASE, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        if not data:
            return None
            
        record = data[0]
        
        return {
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
            'assessed_land': float(record.get('assessland', 0) or 0),
            'assessed_total': float(record.get('assesstot', 0) or 0),
            'zip_code': record.get('zipcode')
        }
        
    except Exception as e:
        print(f"  ⚠️  PLUTO error: {e}")
        return None


def get_rpad_data(bbl):
    """Query RPAD for current taxpayer and assessed values"""
    try:
        time.sleep(API_DELAY)  # Rate limiting
        params = {"bble": bbl, "$limit": 1}
        response = requests.get(RPAD_API_BASE, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        if not data:
            return None
            
        record = data[0]
        
        return {
            'owner_name_rpad': record.get('owner'),
            'assessed_land_value': float(record.get('avland', 0) or 0),
            'assessed_total_value': float(record.get('avtot', 0) or 0)
        }
        
    except Exception as e:
        print(f"  ⚠️  RPAD error: {e}")
        return None


def enrich_buildings():
    """Main enrichment process - fetches data from PLUTO and RPAD for buildings"""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    
    # Get batch size from environment (default 500 for production)
    batch_size = int(os.getenv('BUILDING_BATCH_SIZE', 500))
    
    print("=" * 70)
    print("🏢 DUAL-SOURCE BUILDING ENRICHMENT (PLUTO + RPAD)")
    print("=" * 70)
    print(f"📦 Batch size: {batch_size}")
    
    # Get buildings that need enrichment
    cur.execute(f"""
        SELECT id, bbl, address
        FROM buildings
        WHERE bbl IS NOT NULL
        AND (current_owner_name IS NULL OR owner_name_rpad IS NULL)
        ORDER BY id
        LIMIT {batch_size}
    """)
    
    buildings = cur.fetchall()
    print(f"\n📊 Found {len(buildings)} buildings to enrich\n")
    
    if not buildings:
        print("✅ All buildings already enriched!")
        cur.close()
        conn.close()
        return
    
    pluto_success = 0
    rpad_success = 0
    both_success = 0
    failed = 0
    
    for i, building in enumerate(buildings, 1):
        bbl = building['bbl']
        building_id = building['id']
        address = building['address']
        
        print(f"[{i}/{len(buildings)}] BBL {bbl} ({address})")
        
        # Get data from both sources
        pluto_data = get_pluto_data(bbl)
        rpad_data = get_rpad_data(bbl)
        
        if pluto_data:
            print(f"  ✅ PLUTO: {pluto_data['owner_name']}")
            pluto_success += 1
        
        if rpad_data:
            print(f"  ✅ RPAD:  {rpad_data['owner_name_rpad']}")
            rpad_success += 1
        
        # Update database with all available data
        if pluto_data or rpad_data:
            update_fields = []
            update_values = []
            
            if pluto_data:
                update_fields.extend([
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
            
            if rpad_data:
                update_fields.append("owner_name_rpad = %s")
                update_values.append(rpad_data['owner_name_rpad'])
                
                # RPAD has more current assessed values - use them if available
                if rpad_data['assessed_land_value'] > 0:
                    update_fields.append("assessed_land_value = %s")
                    update_values.append(rpad_data['assessed_land_value'])
                if rpad_data['assessed_total_value'] > 0:
                    update_fields.append("assessed_total_value = %s")
                    update_values.append(rpad_data['assessed_total_value'])
            elif pluto_data:
                # Use PLUTO assessment values if RPAD not available
                update_fields.extend([
                    "assessed_land_value = %s",
                    "assessed_total_value = %s"
                ])
                update_values.extend([
                    pluto_data['assessed_land'],
                    pluto_data['assessed_total']
                ])
            
            update_fields.append("last_updated = CURRENT_TIMESTAMP")
            
            query = f"""
                UPDATE buildings
                SET {', '.join(update_fields)}
                WHERE id = %s
            """
            update_values.append(building_id)
            
            cur.execute(query, update_values)
            conn.commit()
            
            if pluto_data and rpad_data:
                both_success += 1
            
            print()
        else:
            print("  ❌ No data from either source\n")
            failed += 1
        
        # Rate limiting
        time.sleep(0.1)
    
    # Summary
    print("=" * 70)
    print("📊 ENRICHMENT SUMMARY")
    print("=" * 70)
    print(f"✅ PLUTO data retrieved: {pluto_success}")
    print(f"✅ RPAD data retrieved: {rpad_success}")
    print(f"🎯 Both sources retrieved: {both_success}")
    print(f"❌ Failed: {failed}")
    print("=" * 70)
    
    conn.close()


if __name__ == "__main__":
    try:
        enrich_buildings()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
