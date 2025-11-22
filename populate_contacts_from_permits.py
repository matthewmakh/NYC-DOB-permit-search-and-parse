#!/usr/bin/env python3
"""
Populate the contacts table from permits data.
This extracts unique phone numbers from permits and inserts them into contacts table.
"""

import os
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras
from datetime import datetime

load_dotenv()

# Database connection
DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': os.getenv('DB_PORT', '5432'),
    'database': os.getenv('DB_NAME'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD')
}

print("=" * 80)
print("POPULATE CONTACTS TABLE FROM PERMITS")
print("=" * 80)
print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print()

try:
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    
    # Check current state
    print("Step 1: Checking current state...")
    cur.execute("SELECT COUNT(*) FROM contacts")
    current_contacts = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM permit_contacts")
    permit_contact_links = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(DISTINCT contact_id) FROM permit_contacts")
    unique_contact_ids = cur.fetchone()[0]
    
    print(f"   Current contacts: {current_contacts:,}")
    print(f"   Permit-contact links: {permit_contact_links:,}")
    print(f"   Unique contact IDs referenced: {unique_contact_ids:,}")
    print()
    
    if current_contacts > 0:
        response = input("⚠️  Contacts table is not empty. Continue anyway? (yes/no): ")
        if response.lower() != 'yes':
            print("Aborted.")
            exit(0)
    
    # Step 2: Insert unique contacts using ONE bulk query
    print("Step 2: Extracting and inserting unique contacts with bulk INSERT...")
    
    # Use a single SQL query to extract and insert
    cur.execute("""
        INSERT INTO contacts (name, phone, role)
        SELECT DISTINCT ON (phone) 
            name, 
            phone, 
            role
        FROM (
            -- Permittee contacts
            SELECT 
                COALESCE(permittee_business_name, applicant, 'Unknown') as name,
                REGEXP_REPLACE(permittee_phone, '[^0-9]', '', 'g') as phone,
                'Permittee' as role
            FROM permits
            WHERE permittee_phone IS NOT NULL 
            AND permittee_phone != ''
            AND LENGTH(REGEXP_REPLACE(permittee_phone, '[^0-9]', '', 'g')) = 10
            
            UNION ALL
            
            -- Owner contacts
            SELECT 
                COALESCE(owner_business_name, 'Unknown') as name,
                REGEXP_REPLACE(owner_phone, '[^0-9]', '', 'g') as phone,
                'Owner' as role
            FROM permits
            WHERE owner_phone IS NOT NULL 
            AND owner_phone != ''
            AND LENGTH(REGEXP_REPLACE(owner_phone, '[^0-9]', '', 'g')) = 10
        ) all_contacts
        ORDER BY phone, role
        ON CONFLICT (phone) DO NOTHING
    """)
    
    inserted = cur.rowcount
    conn.commit()
    print(f"   ✅ Inserted {inserted:,} unique contacts")
    print()
    
    # Step 3: Verification
    print("Step 3: Verifying...")
    cur.execute("SELECT COUNT(*) FROM contacts")
    final_count = cur.fetchone()[0]
    print(f"   Total contacts now: {final_count:,}")
    
    # Check for orphaned permit_contacts
    cur.execute("""
        SELECT COUNT(*) 
        FROM permit_contacts pc
        LEFT JOIN contacts c ON pc.contact_id = c.id
        WHERE c.id IS NULL
    """)
    orphaned = cur.fetchone()[0]
    
    if orphaned > 0:
        print(f"   ⚠️  WARNING: {orphaned:,} permit_contacts still have no matching contact")
        print(f"   This is expected - permit_contacts uses auto-increment IDs")
        print(f"   The migration needs to be re-run to properly link them")
    else:
        print(f"   ✅ All permit_contacts have matching contacts")
    
    # Show sample
    print("\nStep 4: Sample contacts...")
    cur.execute("""
        SELECT name, phone, role, created_at
        FROM contacts
        ORDER BY created_at DESC
        LIMIT 5
    """)
    samples = cur.fetchall()
    for sample in samples:
        print(f"   {sample[0][:40]:<40} | {sample[1]:<12} | {sample[2]:<10}")
    
    cur.close()
    conn.close()
    
    print()
    print("=" * 80)
    print("✅ CONTACTS POPULATION COMPLETE")
    print("=" * 80)
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("⚠️  NOTE: The permit_contacts table still has orphaned references.")
    print("   You need to either:")
    print("   1. Re-run the full migration to rebuild permit_contacts links, OR")
    print("   2. Accept that permit_contacts may not link correctly until rebuilt")
    print()

except Exception as e:
    print(f"\n❌ Failed: {e}")
    import traceback
    traceback.print_exc()
    if conn:
        conn.rollback()
        conn.close()
    exit(1)
