#!/usr/bin/env python3
"""
Add UNIQUE constraints to prevent duplicate entries
- permits.permit_no must be unique
- phone_validations.phone must be unique (already in table creation)
"""
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', '5432')),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME', 'railway')
}

try:
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    
    print("🔧 Adding UNIQUE constraints...")
    
    # Add UNIQUE constraint to permits.permit_no (if not exists)
    cur.execute("""
        DO $$ 
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint 
                WHERE conname = 'permits_permit_no_unique'
            ) THEN
                ALTER TABLE permits ADD CONSTRAINT permits_permit_no_unique UNIQUE (permit_no);
                RAISE NOTICE 'Added UNIQUE constraint to permits.permit_no';
            ELSE
                RAISE NOTICE 'UNIQUE constraint already exists on permits.permit_no';
            END IF;
        END $$;
    """)
    
    # Create index for faster lookups
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_permits_permit_no ON permits(permit_no);
    """)
    
    conn.commit()
    print("✅ Constraints and indexes added successfully!")
    
    # Check for duplicates
    cur.execute("""
        SELECT permit_no, COUNT(*) as count 
        FROM permits 
        GROUP BY permit_no 
        HAVING COUNT(*) > 1
        LIMIT 10;
    """)
    
    duplicates = cur.fetchall()
    if duplicates:
        print(f"\n⚠️  Found {len(duplicates)} duplicate permit numbers:")
        for permit_no, count in duplicates:
            print(f"   {permit_no}: {count} copies")
        print("\n   Run cleanup script to remove duplicates before adding constraint!")
    else:
        print("✅ No duplicate permit_no values found!")
    
    cur.close()
    conn.close()
    
except Exception as e:
    print(f"❌ Error: {e}")
