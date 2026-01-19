#!/usr/bin/env python3
"""
Run enrichment on specific BBLs from targeted zip codes.
"""

import os
import sys
import time
from datetime import datetime

import psycopg2
import psycopg2.extras
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

def get_db_connection():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn

def get_target_bbls(conn):
    """Get BBLs from our target zip codes."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT b.bbl
            FROM buildings b
            JOIN permits p ON b.bbl = p.bbl
            WHERE p.zip_code = ANY(%s)
            AND b.bbl IS NOT NULL
        """, (TARGET_ZIPS,))
        return [row['bbl'] for row in cur.fetchall()]

def main():
    print("=" * 70)
    print("🎯 TARGETED BBL ENRICHMENT")
    print("=" * 70)
    print(f"   Target zip codes: {', '.join(TARGET_ZIPS)}")
    print()
    
    conn = get_db_connection()
    bbls = get_target_bbls(conn)
    print(f"📊 Found {len(bbls)} BBLs to enrich")
    
    if not bbls:
        print("No BBLs found!")
        return
    
    # Import enrichment functions
    from step2_enrich_from_pluto import get_pluto_data_for_bbl, get_rpad_data_for_bbl, get_hpd_data_for_bbl
    
    # Get buildings that need enrichment
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, bbl, current_owner_name, owner_name_rpad, owner_name_hpd
            FROM buildings
            WHERE bbl = ANY(%s)
            AND (current_owner_name IS NULL OR owner_name_rpad IS NULL OR owner_name_hpd IS NULL)
        """, (bbls,))
        buildings = cur.fetchall()
    
    print(f"   {len(buildings)} buildings need enrichment")
    
    if not buildings:
        print("\n✅ All buildings already enriched!")
        return
    
    # Step 2: PLUTO/RPAD/HPD
    print("\n🏢 Step 2: PLUTO/RPAD/HPD enrichment...")
    start = time.time()
    
    for i, building in enumerate(buildings, 1):
        bbl = building['bbl']
        
        if i % 50 == 0 or i == 1:
            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(buildings) - i) / rate if rate > 0 else 0
            print(f"   [{i}/{len(buildings)}] ETA: {eta/60:.1f} min")
        
        updates = {}
        
        # PLUTO
        if not building['current_owner_name']:
            try:
                pluto_data, _ = get_pluto_data_for_bbl(bbl)
                if pluto_data:
                    updates['current_owner_name'] = pluto_data.get('owner_name')
                    updates['total_units'] = pluto_data.get('total_units')
                    updates['building_sqft'] = pluto_data.get('building_sqft')
                    updates['year_built'] = pluto_data.get('year_built')
                    updates['building_class'] = pluto_data.get('building_class')
            except Exception as e:
                pass
        
        # RPAD
        if not building['owner_name_rpad']:
            try:
                rpad_data, _ = get_rpad_data_for_bbl(bbl)
                if rpad_data:
                    updates['owner_name_rpad'] = rpad_data.get('owner_name')
                    updates['assessed_land_value'] = rpad_data.get('assessed_land_value')
                    updates['assessed_total_value'] = rpad_data.get('assessed_total_value')
            except Exception as e:
                pass
        
        # HPD
        if not building['owner_name_hpd']:
            try:
                hpd_data, _ = get_hpd_data_for_bbl(bbl)
                if hpd_data:
                    updates['owner_name_hpd'] = hpd_data.get('owner_name')
                    updates['hpd_open_violations'] = hpd_data.get('open_violations')
                    updates['hpd_total_violations'] = hpd_data.get('total_violations')
                    updates['hpd_open_complaints'] = hpd_data.get('open_complaints')
                    updates['hpd_total_complaints'] = hpd_data.get('total_complaints')
            except Exception as e:
                pass
        
        if updates:
            with conn.cursor() as cur:
                set_clause = ', '.join(f"{k} = %s" for k in updates.keys())
                cur.execute(
                    f"UPDATE buildings SET {set_clause}, last_updated = NOW() WHERE id = %s",
                    list(updates.values()) + [building['id']]
                )
            conn.commit()
        
        time.sleep(0.1)  # Rate limit
    
    print(f"   ✓ Step 2 complete in {(time.time()-start)/60:.1f} min")
    
    # Step 3: ACRIS
    print("\n📜 Step 3: ACRIS enrichment...")
    try:
        from step3_enrich_from_acris import enrich_building_from_acris
        
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, bbl
                FROM buildings
                WHERE bbl = ANY(%s)
                AND sale_buyer_primary IS NULL
            """, (bbls,))
            acris_buildings = cur.fetchall()
        
        print(f"   {len(acris_buildings)} buildings need ACRIS data")
        
        start = time.time()
        for i, building in enumerate(acris_buildings, 1):
            if i % 50 == 0 or i == 1:
                elapsed = time.time() - start
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(acris_buildings) - i) / rate if rate > 0 else 0
                print(f"   [{i}/{len(acris_buildings)}] ETA: {eta/60:.1f} min")
            
            try:
                enrich_building_from_acris(conn, building['id'], building['bbl'])
            except Exception as e:
                pass
            
            time.sleep(0.1)
        
        print(f"   ✓ Step 3 complete in {(time.time()-start)/60:.1f} min")
    except ImportError:
        print("   ⚠️  Could not import step3 - skipping ACRIS")
    
    # Step 5: NY SOS
    print("\n🏛️  Step 5: NY SOS enrichment...")
    try:
        from ny_sos_lookup import lookup_businesses, is_likely_individual
        
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, bbl, current_owner_name, owner_name_rpad, 
                       owner_name_hpd, sale_buyer_primary
                FROM buildings
                WHERE bbl = ANY(%s)
                AND sos_lookup_attempted IS NOT TRUE
            """, (bbls,))
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
            # Batch lookup
            unique_names = list(set(l['llc_name'] for l in llc_lookups))
            print(f"   Looking up {len(unique_names)} unique LLC names...")
            
            results = lookup_businesses(unique_names, concurrency=5)
            
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
            
            print(f"   ✓ Found {found}/{len(llc_lookups)} in NY SOS")
    except ImportError as e:
        print(f"   ⚠️  Could not import SOS module: {e}")
    
    # Summary
    print("\n" + "=" * 70)
    print("✅ ENRICHMENT COMPLETE")
    print("=" * 70)
    
    conn.close()

if __name__ == "__main__":
    main()
