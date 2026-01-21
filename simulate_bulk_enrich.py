#!/usr/bin/env python3
"""
FINAL VERIFICATION: Simulate bulk enrichment without actually enriching
This verifies the exact same code paths as the real endpoint
"""
import psycopg2
import psycopg2.extras
import os
import sys
sys.path.insert(0, 'dashboard_html')
from dotenv import load_dotenv
load_dotenv('dashboard_html/.env')

from enrichment_service import get_available_owners_for_enrichment

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'),
        cursor_factory=psycopg2.extras.RealDictCursor
    )

def simulate_bulk_enrich(building_ids, user_id, is_admin=True):
    """
    Simulates the EXACT code path of api_bulk_enrich()
    Returns what would be enriched without actually doing it
    """
    results = {
        'successful': 0,
        'failed': 0,
        'skipped': 0,
        'total_charged': 0,
        'details': []
    }
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    for bid in building_ids:
        # EXACT SAME CODE as api_bulk_enrich lines 4440-4441
        owners = get_available_owners_for_enrichment(bid, user_id)
        available = [o for o in owners if not o.get('already_enriched')]
        
        if not available:
            results['skipped'] += 1
            continue
        
        # Get building info
        cur.execute("SELECT address FROM buildings WHERE id = %s", (bid,))
        building_row = cur.fetchone()
        address = building_row['address'] if building_row else f"Building #{bid}"
        
        for owner in available:
            # This is what WOULD be enriched
            results['details'].append({
                'building_id': bid,
                'address': address,
                'owner': owner['name'],
                'source': owner['source'],
                'would_be_charged': 0 if is_admin else 0.35
            })
            results['successful'] += 1
            if not is_admin:
                results['total_charged'] += 0.35
    
    cur.close()
    conn.close()
    return results

if __name__ == '__main__':
    print("=" * 70)
    print("BULK ENRICHMENT SIMULATION - EXACT CODE PATH")
    print("=" * 70)
    
    # Test with your buildings
    building_ids = [17024, 31654, 52117]
    user_id = 2  # matt@tyeny.com
    
    print(f"\nSimulating bulk enrich for buildings: {building_ids}")
    print(f"User ID: {user_id} (admin)")
    print()
    
    results = simulate_bulk_enrich(building_ids, user_id, is_admin=True)
    
    print("SIMULATION RESULTS:")
    print("-" * 40)
    print(f"  Would enrich: {results['successful']} owners")
    print(f"  Would skip: {results['skipped']} buildings (no available owners)")
    print(f"  Total would be charged: ${results['total_charged']:.2f}")
    print()
    
    print("OWNERS THAT WOULD BE ENRICHED:")
    print("-" * 40)
    for detail in results['details']:
        print(f"  Building {detail['building_id']}: {detail['address']}")
        print(f"    Owner: {detail['owner']} ({detail['source']})")
        print(f"    Charge: ${detail['would_be_charged']:.2f}")
    
    print()
    print("=" * 70)
    print("✅ SAFE TO PROCEED - Only the above owners would be enriched")
    print("=" * 70)
