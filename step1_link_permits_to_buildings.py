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


def derive_bbl_from_permit(block, lot, permit_no=None):
    """
    Create BBL from block and lot
    BBL format: BBBBBLLLL where B is borough code (1-5), block is 5 digits, lot is 4 digits
    Borough is extracted from the first character of the permit number
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
    
    # Extract borough code from permit number (first character)
    borough_code = "3"  # Default to Brooklyn
    if permit_no and len(permit_no) > 0:
        first_char = permit_no[0].upper()
        # Check if it's already a numeric code (1-5)
        if first_char in ['1', '2', '3', '4', '5']:
            borough_code = first_char
        # Check if it's a letter code (M, X, B, Q, R, S)
        elif first_char in letter_to_number:
            borough_code = letter_to_number[first_char]
        else:
            print(f"⚠️ Invalid borough code in permit {permit_no}: {first_char}")
            borough_code = "3"  # Fallback to Brooklyn
    
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
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    
    print("Step 1: Linking Permits to Buildings")
    print("=" * 60)
    
    # Phase 1: Derive BBL for permits with block/lot but no BBL
    print("\n📊 Phase 1: Deriving BBLs from block/lot...")
    cur.execute("""
        SELECT id, permit_no, address, block, lot, bin
        FROM permits
        WHERE block IS NOT NULL 
        AND lot IS NOT NULL 
        AND bbl IS NULL
    """)
    
    permits_to_derive = cur.fetchall()
    print(f"   Found {len(permits_to_derive)} permits needing BBL derivation")
    
    derived_count = 0
    for permit in permits_to_derive:
        bbl = derive_bbl_from_permit(permit['block'], permit['lot'], permit['permit_no'])
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
                INSERT INTO buildings (bbl, address, block, lot, bin, last_updated)
                VALUES (%s, %s, %s, %s, %s, NULL)
                ON CONFLICT (bbl) DO NOTHING
            """, (building['bbl'], building['address'], building['block'], 
                  building['lot'], building['bin']))
            buildings_created += 1
            
            if buildings_created % 100 == 0:
                conn.commit()
                print(f"   Created {buildings_created}/{len(buildings_to_create)} buildings...")
        except Exception as e:
            print(f"   ⚠️ Error creating building {building['bbl']}: {e}")
            continue
    
    conn.commit()
    
    print(f"\n✅ Complete!")
    print(f"   Buildings created: {buildings_created}")
    print(f"   BBLs derived: {derived_count}")
    
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
