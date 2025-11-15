#!/usr/bin/env python3
"""
Step 2: Enrich buildings from NYC PLUTO dataset
- Downloads latest MapPLUTO data from NYC Open Data
- Matches buildings by BBL
- Extracts owner name, address, building class, units, sq footage, year built
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

# NYC Open Data PLUTO API endpoint
PLUTO_API_BASE = "https://data.cityofnewyork.us/resource/64uk-42ks.json"


def get_pluto_data_for_bbl(bbl):
    """
    Query NYC Open Data API for PLUTO data by BBL
    BBL format: 10-digit string (borough + block + lot)
    """
    try:
        # PLUTO uses BBL as a 10-digit string
        params = {
            "$where": f"bbl='{bbl}'",
            "$limit": 1
        }
        
        response = requests.get(PLUTO_API_BASE, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        if not data:
            return None
            
        record = data[0]
        
        # Extract key fields - map to existing database columns
        return {
            'owner_name': record.get('ownername'),
            'owner_address': record.get('address'),
            'building_class': record.get('bldgclass'),
            'land_use': record.get('landuse'),
            'residential_units': int(float(record.get('unitsres', 0) or 0)),
            'total_units': int(float(record.get('unitstotal', 0) or 0)),
            'num_floors': int(float(record.get('numfloors', 0) or 0)),
            'building_sqft': int(float(record.get('bldgarea', 0) or 0)),
            'lot_sqft': int(float(record.get('lotarea', 0) or 0)),
            'year_built': int(float(record.get('yearbuilt', 0) or 0)) if record.get('yearbuilt') else None,
            'zip_code': record.get('zipcode')
        }
        
    except Exception as e:
        print(f"⚠️ Error fetching PLUTO data for BBL {bbl}: {e}")
        return None


def enrich_buildings_from_pluto():
    """
    Main process:
    1. Get all buildings without owner data
    2. Query PLUTO API for each BBL
    3. Update building record with PLUTO data
    """
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    
    print("Step 2: Enriching Buildings from PLUTO")
    print("=" * 60)
    
    # Get buildings that need PLUTO data
    cur.execute("""
        SELECT id, bbl, address
        FROM buildings
        WHERE bbl IS NOT NULL
        AND current_owner_name IS NULL
        ORDER BY id
    """)
    
    buildings = cur.fetchall()
    print(f"\n📊 Found {len(buildings)} buildings to enrich")
    
    if not buildings:
        print("   No buildings need enrichment. All done!")
        cur.close()
        conn.close()
        return
    
    enriched = 0
    failed = 0
    
    for i, building in enumerate(buildings, 1):
        bbl = building['bbl']
        building_id = building['id']
        address = building['address']
        
        print(f"\n🔍 [{i}/{len(buildings)}] BBL {bbl} ({address})...")
        
        # Get PLUTO data
        pluto_data = get_pluto_data_for_bbl(bbl)
        
        if pluto_data:
            # Update building record with correct column names
            cur.execute("""
                UPDATE buildings
                SET current_owner_name = %s,
                    owner_mailing_address = %s,
                    building_class = %s,
                    land_use = %s,
                    residential_units = %s,
                    total_units = %s,
                    num_floors = %s,
                    building_sqft = %s,
                    lot_sqft = %s,
                    year_built = %s,
                    last_updated = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (
                pluto_data['owner_name'],
                pluto_data['owner_address'],
                pluto_data['building_class'],
                pluto_data['land_use'],
                pluto_data['residential_units'],
                pluto_data['total_units'],
                pluto_data['num_floors'],
                pluto_data['building_sqft'],
                pluto_data['lot_sqft'],
                pluto_data['year_built'],
                building_id
            ))
            conn.commit()
            
            print(f"   ✅ Owner: {pluto_data['owner_name']}")
            print(f"      Units: {pluto_data['residential_units']}, Built: {pluto_data['year_built']}")
            enriched += 1
        else:
            print(f"   ❌ No PLUTO data found")
            failed += 1
        
        # Rate limit: 0.5 second delay between API calls
        if i < len(buildings):
            time.sleep(0.5)
    
    print(f"\n✅ Complete!")
    print(f"   Buildings enriched: {enriched}")
    print(f"   Failed/No data: {failed}")
    
    # Show sample results
    cur.execute("""
        SELECT bbl, address, current_owner_name, residential_units, year_built
        FROM buildings
        WHERE current_owner_name IS NOT NULL
        LIMIT 5
    """)
    
    results = cur.fetchall()
    if results:
        print(f"\n📋 Sample enriched buildings:")
        for r in results:
            print(f"   {r['bbl']}: {r['current_owner_name']}")
            print(f"      {r['address']} ({r['residential_units']} units, built {r['year_built']})")
    
    cur.close()
    conn.close()


if __name__ == "__main__":
    enrich_buildings_from_pluto()
