#!/usr/bin/env python3
"""
Step 1: Link permits to buildings table
- Derive BBL from permit data (block + lot)
- Create building records from unique BBLs in permits
- Link permits back to buildings via BBL
"""

import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://postgres:rYOeFwAQciYdTdUVPxuCqNparvRNbUov@maglev.proxy.rlwy.net:26571/railway')


def derive_bbl_from_permit(block, lot, borough_from_address=None):
    """
    Create BBL from block and lot
    BBL format: B-BLOCK-LOT where B is borough code (1-5)
    For now, assume Brooklyn (3) if not specified
    """
    if not block or not lot:
        return None
    
    # Clean up block and lot
    block = str(block).strip()
    lot = str(lot).strip()
    
    # Default to Brooklyn (3) - you can enhance this by parsing address
    borough_code = "3"
    
    # Pad block to 5 digits, lot to 4 digits
    block_padded = block.zfill(5)
    lot_padded = lot.zfill(4)
    
    return f"{borough_code}{block_padded}{lot_padded}"


def link_permits_to_buildings():
    """
    Main process:
    1. Find all permits with block/lot but no BBL
    2. Generate BBL for each
    3. Create building record if doesn't exist
    4. Update permit with BBL
    """
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    
    print("Step 1: Linking Permits to Buildings")
    print("=" * 60)
    
    # Get all permits with block/lot data
    print("\n📊 Analyzing permits...")
    cur.execute("""
        SELECT id, address, block, lot, bin, bbl
        FROM permits
        WHERE block IS NOT NULL AND lot IS NOT NULL
    """)
    
    permits = cur.fetchall()
    print(f"   Found {len(permits)} permits with block/lot data")
    
    # Process each permit
    buildings_created = 0
    permits_updated = 0
    permits_skipped = 0
    
    for permit in permits:
        # Skip if already has BBL
        if permit['bbl']:
            permits_skipped += 1
            continue
        
        # Generate BBL
        bbl = derive_bbl_from_permit(permit['block'], permit['lot'])
        
        if not bbl:
            continue
        
        # Create building record if doesn't exist
        cur.execute("SELECT id FROM buildings WHERE bbl = %s", (bbl,))
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO buildings (bbl, address, block, lot, bin)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (bbl) DO NOTHING
            """, (bbl, permit['address'], permit['block'], permit['lot'], permit['bin']))
            buildings_created += 1
        
        # Update permit with BBL
        cur.execute("UPDATE permits SET bbl = %s WHERE id = %s", (bbl, permit['id']))
        permits_updated += 1
        
        # Commit every 100 permits
        if permits_updated % 100 == 0:
            conn.commit()
            print(f"   Processed {permits_updated} permits...")
    
    conn.commit()
    
    print(f"\n✅ Complete!")
    print(f"   Buildings created: {buildings_created}")
    print(f"   Permits updated: {permits_updated}")
    print(f"   Permits skipped (already had BBL): {permits_skipped}")
    
    # Show summary stats
    cur.execute("SELECT COUNT(DISTINCT bbl) FROM buildings WHERE bbl IS NOT NULL")
    total_buildings = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM permits WHERE bbl IS NOT NULL")
    linked_permits = cur.fetchone()[0]
    
    print(f"\n📈 Database Summary:")
    print(f"   Total unique buildings: {total_buildings}")
    print(f"   Permits linked to buildings: {linked_permits}")
    
    cur.close()
    conn.close()


if __name__ == "__main__":
    link_permits_to_buildings()
