#!/usr/bin/env python3
"""
Add UNIQUE constraint to permit_no column to enable UPSERT functionality.

This allows the API scraper to properly update permits when their status changes
(e.g., expired → renewed) instead of skipping them as duplicates.
"""

import os
from dotenv import load_dotenv
import psycopg2
from datetime import datetime

load_dotenv()

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': os.getenv('DB_PORT', '5432'),
    'database': os.getenv('DB_NAME'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD')
}

print("=" * 80)
print("ADD UNIQUE CONSTRAINT TO permit_no")
print("=" * 80)
print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print()

try:
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    
    # Step 1: Check for duplicate permit_no values
    print("Step 1: Checking for duplicate permit_no values...")
    cur.execute("""
        SELECT permit_no, COUNT(*) as count
        FROM permits
        WHERE permit_no IS NOT NULL
        GROUP BY permit_no
        HAVING COUNT(*) > 1
        ORDER BY count DESC
        LIMIT 10
    """)
    
    duplicates = cur.fetchall()
    
    if duplicates:
        print(f"   ⚠️  Found {len(duplicates)} permit numbers with duplicates:")
        for dup in duplicates[:10]:
            print(f"      {dup[0]}: {dup[1]} occurrences")
        print()
        
        print("   Checking total duplicate records...")
        cur.execute("""
            SELECT COUNT(*)
            FROM permits p1
            WHERE EXISTS (
                SELECT 1 FROM permits p2
                WHERE p2.permit_no = p1.permit_no
                AND p2.id != p1.id
                AND p1.permit_no IS NOT NULL
            )
        """)
        total_dup_records = cur.fetchone()[0]
        print(f"   Total records involved in duplicates: {total_dup_records:,}")
        print()
        
        response = input("   Do you want to merge duplicates before adding constraint? (yes/no): ")
        if response.lower() == 'yes':
            print("\n   Merging duplicates - keeping most recent record for each permit_no...")
            
            # Strategy: For each duplicate permit_no, keep the one with the latest api_last_updated or issue_date
            cur.execute("""
                WITH ranked_permits AS (
                    SELECT 
                        id,
                        permit_no,
                        ROW_NUMBER() OVER (
                            PARTITION BY permit_no 
                            ORDER BY 
                                api_last_updated DESC NULLS LAST,
                                issue_date DESC NULLS LAST,
                                id DESC
                        ) as rn
                    FROM permits
                    WHERE permit_no IS NOT NULL
                )
                DELETE FROM permits
                WHERE id IN (
                    SELECT id
                    FROM ranked_permits
                    WHERE rn > 1
                )
            """)
            
            deleted = cur.rowcount
            conn.commit()
            print(f"   ✅ Deleted {deleted:,} duplicate records")
            print()
        else:
            print("\n   ⚠️  Cannot add UNIQUE constraint with duplicates present.")
            print("   Either merge duplicates or allow NULL permit_no values.")
            print("   Aborting.")
            cur.close()
            conn.close()
            exit(1)
    else:
        print("   ✅ No duplicates found")
        print()
    
    # Step 2: Add UNIQUE constraint
    print("Step 2: Adding UNIQUE constraint to permit_no...")
    
    # Check if constraint already exists
    cur.execute("""
        SELECT constraint_name
        FROM information_schema.table_constraints
        WHERE table_name = 'permits'
        AND constraint_type = 'UNIQUE'
        AND constraint_name LIKE '%permit_no%'
    """)
    
    existing = cur.fetchone()
    if existing:
        print(f"   ℹ️  UNIQUE constraint already exists: {existing[0]}")
    else:
        # Drop existing non-unique index first
        cur.execute("DROP INDEX IF EXISTS idx_permits_permit_no")
        
        # Create UNIQUE constraint
        cur.execute("""
            ALTER TABLE permits
            ADD CONSTRAINT unique_permit_no UNIQUE (permit_no)
        """)
        conn.commit()
        print("   ✅ UNIQUE constraint added")
    
    print()
    
    # Step 3: Verification
    print("Step 3: Verifying...")
    cur.execute("""
        SELECT 
            conname,
            pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'permits'::regclass
        AND contype = 'u'
        AND pg_get_constraintdef(oid) LIKE '%permit_no%'
    """)
    
    constraint = cur.fetchone()
    if constraint:
        print(f"   ✅ Constraint verified: {constraint[0]}")
        print(f"   Definition: {constraint[1]}")
    else:
        print("   ❌ Constraint not found!")
    
    # Check if there are NULLs
    cur.execute("SELECT COUNT(*) FROM permits WHERE permit_no IS NULL")
    null_count = cur.fetchone()[0]
    if null_count > 0:
        print(f"\n   ℹ️  Note: {null_count:,} permits have NULL permit_no (these are allowed)")
    
    cur.close()
    conn.close()
    
    print()
    print("=" * 80)
    print("✅ MIGRATION COMPLETE")
    print("=" * 80)
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("Next steps:")
    print("1. The API scraper can now properly UPSERT permits")
    print("2. Updates to permit status will work correctly")
    print("3. No more duplicate permit issues")
    print()

except Exception as e:
    print(f"\n❌ Migration failed: {e}")
    import traceback
    traceback.print_exc()
    if conn:
        conn.rollback()
        conn.close()
    exit(1)
