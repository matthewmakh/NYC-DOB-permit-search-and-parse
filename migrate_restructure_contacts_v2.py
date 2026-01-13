#!/usr/bin/env python3
"""
Restructure contacts table to support phone validation
and many-to-many relationships with permits

SAFER APPROACH: Rename old table instead of dropping
"""

import os
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras
from datetime import datetime

load_dotenv()

# Database connection
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME')
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

print("=" * 80)
print("CONTACTS TABLE RESTRUCTURE MIGRATION (SAFE VERSION)")
print("=" * 80)
print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print()

try:
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    
    # Step 1: Rename old contacts table as backup
    print("Step 1: Backing up old contacts table...")
    try:
        cur.execute("ALTER TABLE IF EXISTS contacts RENAME TO contacts_old_backup;")
        conn.commit()
        print("   ✅ Old contacts table renamed to contacts_old_backup")
    except Exception as e:
        print(f"   ℹ️  Table may not exist or already renamed: {e}")
    
    # Also drop old assignment_log if it exists
    try:
        cur.execute("DROP TABLE IF EXISTS assignment_log CASCADE;")
        conn.commit()
        print("   ✅ Dropped assignment_log (will not be recreated)")
    except Exception as e:
        print(f"   ℹ️  assignment_log: {e}")
    
    print()
    
    # Step 2: Create new contacts table with phone validation fields
    print("Step 2: Creating new contacts table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255),
            phone VARCHAR(50) UNIQUE NOT NULL,
            role VARCHAR(100),
            
            -- Phone validation fields (Twilio Lookup API)
            is_mobile BOOLEAN,
            line_type VARCHAR(50),
            carrier_name VARCHAR(255),
            phone_validated_at TIMESTAMP,
            
            -- Metadata
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        -- Indexes for performance
        CREATE INDEX IF NOT EXISTS idx_contacts_phone ON contacts(phone);
        CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(name);
        CREATE INDEX IF NOT EXISTS idx_contacts_phone_validated ON contacts(phone_validated_at);
    """)
    conn.commit()
    print("   ✅ New contacts table created")
    print()
    
    # Step 3: Create permit_contacts junction table
    print("Step 3: Creating permit_contacts junction table...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS permit_contacts (
            id SERIAL PRIMARY KEY,
            permit_id INTEGER REFERENCES permits(id) ON DELETE CASCADE,
            contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
            contact_role VARCHAR(100),
            
            -- Prevent duplicate links
            UNIQUE(permit_id, contact_id, contact_role),
            
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        -- Indexes for fast lookups
        CREATE INDEX IF NOT EXISTS idx_permit_contacts_permit ON permit_contacts(permit_id);
        CREATE INDEX IF NOT EXISTS idx_permit_contacts_contact ON permit_contacts(contact_id);
    """)
    conn.commit()
    print("   ✅ Junction table created")
    print()
    
    # Step 4: Extract and insert unique contacts in ONE bulk operation
    print("Step 4: Extracting and inserting unique contacts...")
    
    cur.execute("""
        INSERT INTO contacts (name, phone, role)
        SELECT DISTINCT ON (phone) 
            name, 
            phone, 
            role
        FROM (
            -- Permittee contacts
            SELECT 
                COALESCE(permittee_business_name, applicant) as name,
                REGEXP_REPLACE(permittee_phone, '[^0-9]', '', 'g') as phone,
                'Permittee' as role
            FROM permits
            WHERE permittee_phone IS NOT NULL 
            AND permittee_phone != ''
            AND LENGTH(REGEXP_REPLACE(permittee_phone, '[^0-9]', '', 'g')) = 10
            
            UNION ALL
            
            -- Owner contacts
            SELECT 
                owner_business_name as name,
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
    print(f"   ✅ Inserted {inserted} unique contacts")
    print()
    
    # Step 5: Link permits to contacts via junction table
    print("Step 5: Linking permits to contacts...")
    
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
    print(f"   ✅ Linked {permittee_links} permittee contacts to permits")
    
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
    print(f"   ✅ Linked {owner_links} owner contacts to permits")
    
    conn.commit()
    print()
    
    # Step 6: Verification
    print("Step 6: Verification...")
    cur.execute("SELECT COUNT(*) FROM contacts")
    contact_count = cur.fetchone()['count']
    
    cur.execute("SELECT COUNT(*) FROM permit_contacts")
    link_count = cur.fetchone()['count']
    
    cur.execute("""
        SELECT COUNT(DISTINCT p.id) 
        FROM permits p
        JOIN permit_contacts pc ON p.id = pc.permit_id
    """)
    permits_with_contacts = cur.fetchone()['count']
    
    print(f"   📊 Total contacts: {contact_count}")
    print(f"   📊 Total links: {link_count}")
    print(f"   📊 Permits with contacts: {permits_with_contacts}")
    print()
    
    # Step 7: Show sample data
    print("Step 7: Sample data...")
    cur.execute("""
        SELECT c.name, c.phone, c.role, COUNT(pc.permit_id) as permit_count
        FROM contacts c
        LEFT JOIN permit_contacts pc ON c.id = pc.contact_id
        GROUP BY c.id, c.name, c.phone, c.role
        ORDER BY permit_count DESC
        LIMIT 5
    """)
    samples = cur.fetchall()
    
    print("   Top 5 contacts by permit count:")
    for sample in samples:
        print(f"   - {sample['name'][:40]:<40} | {sample['phone']:<12} | {sample['role']:<10} | {sample['permit_count']} permits")
    print()
    
    # Step 8: Offer to drop old backup
    print("Step 8: Cleanup...")
    print("   ℹ️  Old contacts table saved as 'contacts_old_backup'")
    print("   ℹ️  You can drop it later with: DROP TABLE contacts_old_backup CASCADE;")
    print()
    
    cur.close()
    conn.close()
    
    print("=" * 80)
    print("✅ MIGRATION COMPLETE!")
    print("=" * 80)
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("Next steps:")
    print("1. Run update_phone_types.py to validate phone numbers")
    print("2. Test dashboard queries")
    print("3. Drop contacts_old_backup when confident")
    print()

except Exception as e:
    print(f"\n❌ Migration failed: {e}")
    import traceback
    traceback.print_exc()
    if conn:
        conn.rollback()
        conn.close()
    exit(1)
