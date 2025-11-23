#!/usr/bin/env python3
"""Quick script to check recently enriched buildings"""
import psycopg2
import os
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

# Railway database connection
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'maglev.proxy.rlwy.net'),
    'port': int(os.getenv('DB_PORT', 26571)),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME', 'railway')
}

conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor(cursor_factory=RealDictCursor)

# Get 3 recently enriched buildings with ALL their data
cur.execute("""
    SELECT 
        bbl,
        address,
        current_owner_name,
        owner_name_rpad,
        owner_name_hpd,
        assessed_land_value,
        assessed_total_value,
        building_class,
        residential_units,
        total_units,
        num_floors,
        building_sqft,
        lot_sqft,
        year_built,
        hpd_total_violations,
        hpd_open_violations,
        hpd_registration_id,
        last_updated
    FROM buildings
    WHERE last_updated > NOW() - INTERVAL '15 minutes'
        AND (current_owner_name IS NOT NULL OR owner_name_rpad IS NOT NULL)
    ORDER BY last_updated DESC
    LIMIT 3
""")

buildings = cur.fetchall()

print("="*80)
print("🏢 RECENTLY ENRICHED BUILDINGS ON RAILWAY (Last 15 min)")
print("="*80)

for i, b in enumerate(buildings, 1):
    print(f"\n{'='*80}")
    print(f"Building {i}: {b['address']}")
    print(f"BBL: {b['bbl']}")
    print(f"{'='*80}")
    
    print(f"\n📋 OWNERSHIP DATA:")
    print(f"   PLUTO Owner:  {b['current_owner_name'] or 'N/A'}")
    print(f"   RPAD Owner:   {b['owner_name_rpad'] or 'N/A'}")
    print(f"   HPD Owner:    {b['owner_name_hpd'] or 'N/A'}")
    
    print(f"\n💰 TAX ASSESSMENT (RPAD):")
    if b['assessed_land_value']:
        print(f"   Land Value:   ${b['assessed_land_value']:,}")
        print(f"   Total Value:  ${b['assessed_total_value']:,}")
    else:
        print(f"   ❌ No RPAD data")
    
    print(f"\n🏗️  BUILDING CHARACTERISTICS (PLUTO):")
    print(f"   Class:        {b['building_class'] or 'N/A'}")
    print(f"   Units:        {b['residential_units'] or 0} residential / {b['total_units'] or 0} total")
    print(f"   Floors:       {b['num_floors'] or 'N/A'}")
    print(f"   Building SF:  {b['building_sqft']:,}" if b['building_sqft'] else "   Building SF:  N/A")
    print(f"   Lot SF:       {b['lot_sqft']:,}" if b['lot_sqft'] else "   Lot SF:       N/A")
    print(f"   Year Built:   {b['year_built'] or 'N/A'}")
    
    print(f"\n⚠️  HPD DATA:")
    if b['hpd_registration_id']:
        print(f"   Registration: {b['hpd_registration_id']}")
        print(f"   Violations:   {b['hpd_total_violations'] or 0} total, {b['hpd_open_violations'] or 0} open")
    else:
        print(f"   ❌ No HPD registration")
    
    print(f"\n⏰ Last Updated: {b['last_updated']}")

print(f"\n{'='*80}")

cur.close()
conn.close()
