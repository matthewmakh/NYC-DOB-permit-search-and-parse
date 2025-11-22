#!/usr/bin/env python3
"""
Migration: Add dual owner tracking and assessment fields
- Removes owner_mailing_address (not actually mailing address)
- Adds owner_name_rpad (current owner from tax records)
- Adds assessed_land_value and assessed_total_value
- Adds year_altered (most recent renovation)
- Keeps current_owner_name (PLUTO corporate entity)
"""

import psycopg2
import os
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


def migrate():
    """Add new columns and remove old ones"""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    print("=" * 70)
    print("MIGRATION: Add Dual Owner Tracking + Assessment Fields")
    print("=" * 70)
    
    try:
        # 1. Remove owner_mailing_address (it's not actually a mailing address)
        print("\n1️⃣  Dropping owner_mailing_address column...")
        cur.execute("""
            ALTER TABLE buildings 
            DROP COLUMN IF EXISTS owner_mailing_address
        """)
        print("   ✅ Dropped owner_mailing_address")
        
        # 2. Add owner_name_rpad (more current owner from tax records)
        print("\n2️⃣  Adding owner_name_rpad column...")
        cur.execute("""
            ALTER TABLE buildings 
            ADD COLUMN IF NOT EXISTS owner_name_rpad VARCHAR(500)
        """)
        print("   ✅ Added owner_name_rpad (current owner from RPAD)")
        
        # 3. Add assessment value fields
        print("\n3️⃣  Adding assessment value fields...")
        cur.execute("""
            ALTER TABLE buildings 
            ADD COLUMN IF NOT EXISTS assessed_land_value NUMERIC(15, 2),
            ADD COLUMN IF NOT EXISTS assessed_total_value NUMERIC(15, 2)
        """)
        print("   ✅ Added assessed_land_value")
        print("   ✅ Added assessed_total_value")
        
        # 4. Add year_altered (most recent major alteration)
        print("\n4️⃣  Adding year_altered column...")
        cur.execute("""
            ALTER TABLE buildings 
            ADD COLUMN IF NOT EXISTS year_altered INTEGER
        """)
        print("   ✅ Added year_altered (most recent renovation)")
        
        # Commit changes
        conn.commit()
        
        print("\n" + "=" * 70)
        print("✅ MIGRATION COMPLETE!")
        print("=" * 70)
        print("\nNew Schema:")
        print("  - current_owner_name (PLUTO): Corporate entity name")
        print("  - owner_name_rpad (RPAD): Current taxpayer name")
        print("  - assessed_land_value: Land assessment for tax purposes")
        print("  - assessed_total_value: Total property assessment")
        print("  - year_altered: Most recent major alteration/renovation")
        print("\nRemoved:")
        print("  - owner_mailing_address (was property address, not mailing)")
        print("=" * 70)
        
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    migrate()
