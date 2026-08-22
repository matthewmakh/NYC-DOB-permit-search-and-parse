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
import sys
from dotenv import load_dotenv

# Force unbuffered output for Railway logging
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

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


def derive_bbl_from_permit(block, lot, permit_no=None, borough=None):
    """
    Create BBL from block and lot
    BBL format: BBBBBLLLL where B is borough code (1-5), block is 5 digits, lot is 4 digits
    Borough is taken from the permit's borough field when available, then
    from the permit number prefix. Ambiguous rows are left unlinked.
    """
    if not block or not lot:
        return None
    
    # Clean up block and lot
    block = str(block).strip()
    lot = str(lot).strip()
    
    # Validate block and lot are numeric
    if not block.isdigit() or not lot.isdigit():
        print(f"⚠️ Invalid block/lot (non-numeric): block={block}, lot={lot}")
        return None
    
    # Map letter codes to numeric codes (DOB NOW uses letters, BIS uses numbers)
    letter_to_number = {
        'M': '1',  # Manhattan
        'X': '2',  # Bronx
        'B': '3',  # Brooklyn
        'Q': '4',  # Queens
        'R': '5',  # Staten Island (Richmond)
        'S': '5',  # Staten Island alternate
    }
    
    borough_names = {
        'MANHATTAN': '1', 'BRONX': '2', 'BROOKLYN': '3',
        'QUEENS': '4', 'STATEN ISLAND': '5', 'RICHMOND': '5',
    }
    borough_text = str(borough or '').strip().upper()
    borough_code = borough_names.get(borough_text)
    if not borough_code and borough_text in {'1', '2', '3', '4', '5'}:
        borough_code = borough_text

    if not borough_code and permit_no and len(permit_no) > 0:
        first_char = permit_no[0].upper()
        # Check if it's already a numeric code (1-5)
        if first_char in ['1', '2', '3', '4', '5']:
            borough_code = first_char
        # Check if it's a letter code (M, X, B, Q, R, S)
        elif first_char in letter_to_number:
            borough_code = letter_to_number[first_char]

    if not borough_code:
        print(f"⚠️ Cannot determine borough for permit {permit_no}; leaving BBL unset")
        return None
    
    # Pad block to 5 digits, lot to 4 digits
    block_padded = block.zfill(5)
    lot_padded = lot.zfill(4)
    
    bbl = f"{borough_code}{block_padded}{lot_padded}"
    
    # Final validation: BBL must be exactly 10 digits
    if len(bbl) != 10 or not bbl.isdigit():
        print(f"⚠️ Generated invalid BBL: {bbl} (from block={block}, lot={lot}, permit={permit_no})")
        return None
    
    return bbl


def link_permits_to_buildings():
    """
    Main process:
    1. Get ALL permits with BBL (existing or derivable)
    2. Create building records for unique BBLs
    3. Link permits to buildings via BBL
    """
    print("Step 1: Linking Permits to Buildings", flush=True)
    print("=" * 60, flush=True)
    
    # Debug: Show connection info (masked)
    db_url_masked = DATABASE_URL[:30] + "..." if DATABASE_URL else "None"
    print(f"🔌 Connecting to database ({db_url_masked})...", flush=True)
    
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor, connect_timeout=30)
        print("✅ Connected to database", flush=True)
    except Exception as e:
        print(f"❌ Database connection failed: {e}", flush=True)
        raise
    
    cur = conn.cursor()
    
    # Phase 1: Derive BBL for permits with block/lot but no BBL
    print("\n📊 Phase 1: Deriving BBLs from block/lot...", flush=True)
    cur.execute("""
        SELECT id, permit_no, address, borough, block, lot, bin
        FROM permits
        WHERE block IS NOT NULL 
        AND lot IS NOT NULL 
        AND bbl IS NULL
    """)
    
    permits_to_derive = cur.fetchall()
    print(f"   Found {len(permits_to_derive)} permits needing BBL derivation")
    
    derived_count = 0
    for permit in permits_to_derive:
        bbl = derive_bbl_from_permit(
            permit['block'], permit['lot'], permit['permit_no'], permit['borough'])
        if bbl:
            cur.execute("UPDATE permits SET bbl = %s WHERE id = %s", (bbl, permit['id']))
            derived_count += 1
    
    conn.commit()
    print(f"   ✅ Derived {derived_count} BBLs from block/lot data")
    
    # Phase 2: Create building records from ALL unique BBLs
    print("\n📊 Phase 2: Creating building records...")
    cur.execute("""
        SELECT DISTINCT ON (p.bbl)
            p.bbl,
            p.address,
            p.block,
            p.lot,
            p.bin
        FROM permits p
        WHERE p.bbl IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM buildings b WHERE b.bbl = p.bbl
        )
        ORDER BY p.bbl, p.issue_date DESC NULLS LAST
    """)
    
    buildings_to_create = cur.fetchall()
    print(f"   Found {len(buildings_to_create)} new buildings to create")
    
    buildings_created = 0
    for building in buildings_to_create:
        try:
            cur.execute("""
                INSERT INTO buildings (bbl, address, borough, block, lot, bin, last_updated)
                VALUES (%s, %s, %s, %s, %s, %s, NULL)
                ON CONFLICT (bbl) DO NOTHING
            """, (building['bbl'], building['address'], building['bbl'][0],
                  building['block'], building['lot'], building['bin']))
            buildings_created += 1
            
            if buildings_created % 100 == 0:
                conn.commit()
                print(f"   Created {buildings_created}/{len(buildings_to_create)} buildings...")
        except Exception as e:
            print(f"   ⚠️ Error creating building {building['bbl']}: {e}")
            continue
    
    conn.commit()

    # Phase 3: actually link the rows. Older versions created buildings but
    # never populated permits.building_id despite this script's name and
    # documentation. Keep compatibility with databases that predate that
    # optional column.
    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'permits' AND column_name = 'building_id'
    """)
    permits_have_building_id = cur.fetchone() is not None
    linked_by_id = 0
    if permits_have_building_id:
        cur.execute("""
            UPDATE permits p
            SET building_id = b.id
            FROM buildings b
            WHERE p.bbl = b.bbl
              AND p.building_id IS DISTINCT FROM b.id
        """)
        linked_by_id = cur.rowcount
        conn.commit()
    
    print(f"\n✅ Complete!")
    print(f"   Buildings created: {buildings_created}")
    print(f"   BBLs derived: {derived_count}")
    if permits_have_building_id:
        print(f"   Permit rows linked by building_id: {linked_by_id}")
    
    # Show summary stats
    cur.execute("SELECT COUNT(DISTINCT bbl) FROM buildings WHERE bbl IS NOT NULL")
    result = cur.fetchone()
    total_buildings = result['count'] if isinstance(result, dict) else result[0]
    
    cur.execute("SELECT COUNT(*) FROM permits WHERE bbl IS NOT NULL")
    result = cur.fetchone()
    linked_permits = result['count'] if isinstance(result, dict) else result[0]
    
    print(f"\n📈 Database Summary:")
    print(f"   Total unique buildings: {total_buildings}")
    print(f"   Permits linked to buildings: {linked_permits}")
    
    cur.close()
    conn.close()


if __name__ == "__main__":
    link_permits_to_buildings()
