#!/usr/bin/env python3
"""
Sync new phone numbers from permits to contacts table.
Run this AFTER adding new permits to ensure phone numbers get validated.

This script:
1. Finds phone numbers in permits that aren't in contacts yet
2. Adds them to contacts table
3. Links them via permit_contacts junction table
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
print("SYNC NEW PERMIT PHONE NUMBERS TO CONTACTS")
print("=" * 80)
print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print()

try:
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    
    # Step 1: Add new phone numbers from permits to contacts
    print("Step 1: Adding new phone numbers from permits to contacts...")
    
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
    
    new_contacts = cur.rowcount
    conn.commit()
    
    if new_contacts > 0:
        print(f"   ✅ Added {new_contacts:,} new contacts")
    else:
        print(f"   ℹ️  No new phone numbers to add")
    print()
    
    # Step 2: Link permits to contacts (for any missing links)
    print("Step 2: Linking permits to contacts...")
    
    # Link permittee contacts
    cur.execute("""
        INSERT INTO permit_contacts (permit_id, contact_id, contact_role)
        SELECT DISTINCT p.id, c.id, 'Permittee'
        FROM permits p
        JOIN contacts c ON REGEXP_REPLACE(p.permittee_phone, '[^0-9]', '', 'g') = c.phone
        WHERE p.permittee_phone IS NOT NULL
        ON CONFLICT (permit_id, contact_id, contact_role) DO NOTHING
    """)
    permittee_links = cur.rowcount
    
    # Link owner contacts
    cur.execute("""
        INSERT INTO permit_contacts (permit_id, contact_id, contact_role)
        SELECT DISTINCT p.id, c.id, 'Owner'
        FROM permits p
        JOIN contacts c ON REGEXP_REPLACE(p.owner_phone, '[^0-9]', '', 'g') = c.phone
        WHERE p.owner_phone IS NOT NULL
        ON CONFLICT (permit_id, contact_id, contact_role) DO NOTHING
    """)
    owner_links = cur.rowcount
    
    conn.commit()
    
    total_links = permittee_links + owner_links
    if total_links > 0:
        print(f"   ✅ Created {total_links:,} new permit-contact links")
        print(f"      Permittee: {permittee_links:,}")
        print(f"      Owner: {owner_links:,}")
    else:
        print(f"   ℹ️  No new links needed")
    print()
    
    # Step 3: Summary
    print("Step 3: Summary...")
    
    cur.execute("SELECT COUNT(*) FROM contacts")
    total_contacts = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM contacts WHERE phone_validated_at IS NULL")
    unvalidated = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM permit_contacts")
    total_links = cur.fetchone()[0]
    
    print(f"   Total contacts: {total_contacts:,}")
    print(f"   Unvalidated: {unvalidated:,}")
    print(f"   Total permit-contact links: {total_links:,}")
    
    if unvalidated > 0:
        print()
        print(f"   💡 Next step: Run update_phone_types.py to validate {unvalidated:,} contacts")
    
    cur.close()
    conn.close()
    
    print()
    print("=" * 80)
    print("✅ SYNC COMPLETE")
    print("=" * 80)
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

except Exception as e:
    print(f"\n❌ Sync failed: {e}")
    import traceback
    traceback.print_exc()
    if conn:
        conn.rollback()
        conn.close()
    exit(1)
