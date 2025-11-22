#!/usr/bin/env python3
"""
Restructure contacts table to support phone validation
and many-to-many relationships with permits

This migration:
1. Drops existing contacts table
2. Creates new contacts table with phone validation fields
3. Creates permit_contacts junction table
4. Extracts unique contacts from permits table
5. Links permits to contacts
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
print("CONTACTS TABLE RESTRUCTURE MIGRATION")
print("=" * 80)
print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print()

try:
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    
    # Step 1: Kill any blocking queries on contacts table
    print("Step 1: Checking for blocking queries...")
    cur.execute("""
        SELECT pid, query, state, wait_event_type, wait_event
        FROM pg_stat_activity 
        WHERE datname = current_database()
        AND pid != pg_backend_pid()
        AND query ILIKE '%%contacts%%'
    """)
    blocking = cur.fetchall()
    
    if blocking:
        print(f"   Found {len(blocking)} queries accessing contacts table")
        for proc in blocking:
            print(f"   - PID {proc['pid']}: {proc['state']} - {proc['query'][:50]}...")
            try:
                cur.execute(f"SELECT pg_terminate_backend({proc['pid']})")
                print(f"   ✅ Killed PID {proc['pid']}")
            except Exception as e:
                print(f"   ⚠️  Could not kill PID {proc['pid']}: {e}")
        conn.commit()
    else:
        print("   ✅ No blocking queries found")
    print()
    
    # Step 2: Force close all connections and drop tables
    print("Step 2: Force closing all other database connections...")
    
    # Terminate ALL other connections to this database
    cur.execute("""
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = current_database()
        AND pid != pg_backend_pid()
    """)
    killed_conns = cur.rowcount
    print(f"   ✅ Terminated {killed_conns} other connections")
    conn.commit()
    print()
    
    print("Step 3: Dropping existing tables...")
    
    # Drop assignment_log first (has FK to contacts)
    print("   Dropping assignment_log...")
    cur.execute("DROP TABLE IF EXISTS assignment_log CASCADE;")
    conn.commit()
    print("   ✅ assignment_log dropped")
    
    # Now drop contacts with RESTRICT to see what's blocking
    print("   Dropping contacts...")
    try:
        cur.execute("DROP TABLE IF EXISTS contacts CASCADE;")
        conn.commit()
        print("   ✅ contacts dropped")
    except Exception as e:
        print(f"   ⚠️  Error dropping contacts: {e}")
        # Try to see what's referencing it
        cur.execute("""
            SELECT 
                conname AS constraint_name,
                conrelid::regclass AS table_name,
                confrelid::regclass AS referenced_table
            FROM pg_constraint
            WHERE confrelid = 'contacts'::regclass
            AND contype = 'f'
        """)
        refs = cur.fetchall()
        if refs:
            print(f"   Found {len(refs)} foreign key constraints:")
            for ref in refs:
                print(f"     - {ref['table_name']} → {ref['referenced_table']}")
        raise
    print()
    
    # Step 4: Create new contacts table with phone validation fields
    print("Step 4: Creating new contacts table...")
    cur.execute("""
        CREATE TABLE contacts (
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
        CREATE INDEX idx_contacts_phone ON contacts(phone);
        CREATE INDEX idx_contacts_name ON contacts(name);
        CREATE INDEX idx_contacts_phone_validated ON contacts(phone_validated_at);
    """)
    conn.commit()
    print("   ✅ New contacts table created")
    print()
    
    # Step 5: Create permit_contacts junction table
    print("Step 5: Creating permit_contacts junction table...")
    cur.execute("""
        CREATE TABLE permit_contacts (
            id SERIAL PRIMARY KEY,
            permit_id INTEGER REFERENCES permits(id) ON DELETE CASCADE,
            contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
            contact_role VARCHAR(100),
            
            -- Prevent duplicate links
            UNIQUE(permit_id, contact_id, contact_role),
            
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        -- Indexes for fast lookups
        CREATE INDEX idx_permit_contacts_permit ON permit_contacts(permit_id);
        CREATE INDEX idx_permit_contacts_contact ON permit_contacts(contact_id);
    """)
    conn.commit()
    print("   ✅ Junction table created")
    print()
    
    # Step 6: Extract and insert unique contacts in ONE bulk operation
    print("Step 6: Extracting and inserting unique contacts...")
    
    # Single query to extract ALL unique phone numbers with their metadata
    # This uses UNION to combine permittee and owner contacts, then DISTINCT ON to keep first occurrence
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
    
    # Step 7: Link permits to contacts via junction table
    print("Step 7: Linking permits to contacts...")
    
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
    
    # Step 8: Verification
    print("Step 8: Verification...")
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
    
    # Step 9: Show sample data
    print("Step 9: Sample data...")
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
    
    cur.close()
    conn.close()
    
    print("=" * 80)
    print("✅ MIGRATION COMPLETE!")
    print("=" * 80)
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("Next steps:")
    print("1. Test queries with new structure")
    print("2. Update update_phone_types.py to use contacts table")
    print("3. Update dashboard queries to use permit_contacts junction table")
    print()

except Exception as e:
    print(f"\n❌ Migration failed: {e}")
    import traceback
    traceback.print_exc()
    if conn:
        conn.rollback()
        conn.close()
    exit(1)
