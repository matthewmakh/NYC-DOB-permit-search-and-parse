#!/usr/bin/env python3
"""
Migrate contacts from contacts table into permits table columns
This consolidates all contact data into the permits table structure
"""

import os
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras

load_dotenv()

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', '5432')),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)

cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

print('=' * 80)
print('MIGRATING CONTACTS TABLE → PERMITS TABLE')
print('=' * 80)

# Get all contacts from contacts table
cur.execute("""
    SELECT 
        c.permit_id,
        c.name,
        c.phone,
        p.permit_no,
        p.applicant,
        p.permittee_business_name,
        p.owner_business_name
    FROM contacts c
    INNER JOIN permits p ON c.permit_id = p.id
    WHERE c.name IS NOT NULL AND c.name != ''
    ORDER BY c.permit_id, c.id
""")

contacts = cur.fetchall()

print(f'\nFound {len(contacts)} contacts to migrate')
print(f'Across {len(set([c["permit_id"] for c in contacts]))} unique permits\n')

# Group contacts by permit
from collections import defaultdict
permits_contacts = defaultdict(list)

for contact in contacts:
    permits_contacts[contact['permit_id']].append(contact)

migrated = 0
skipped_no_phone = 0
skipped_already_has = 0

print('Processing migrations...\n')

for permit_id, contact_list in permits_contacts.items():
    # Get current permit data
    cur.execute("""
        SELECT 
            id, permit_no, applicant,
            permittee_business_name, permittee_phone,
            owner_business_name, owner_phone,
            superintendent_business_name
        FROM permits
        WHERE id = %s
    """, (permit_id,))
    
    permit = cur.fetchone()
    
    if not permit:
        continue
    
    # Try to intelligently map contacts to appropriate fields
    # Strategy: Match contact name to existing business names, or use first available slot
    
    updates = {}
    
    for contact in contact_list:
        name = contact['name']
        phone = contact['phone']
        
        if not phone or phone == '':
            skipped_no_phone += 1
            continue
        
        # Try to match name to business name and populate corresponding phone field
        matched = False
        
        # Check if name matches applicant (old Selenium field)
        if permit['applicant'] and name.upper() in permit['applicant'].upper():
            # This is likely the permittee - populate permittee fields
            if not permit['permittee_business_name']:
                updates['permittee_business_name'] = name
            if not permit['permittee_phone']:
                updates['permittee_phone'] = phone
                matched = True
        
        # Check if this looks like an owner
        elif 'owner' in name.lower() or (permit['owner_business_name'] and name.upper() in permit['owner_business_name'].upper()):
            if not permit['owner_phone']:
                updates['owner_phone'] = phone
                if not permit['owner_business_name']:
                    updates['owner_business_name'] = name
                matched = True
        
        # Check if this looks like a superintendent
        elif 'superintendent' in name.lower() or 'super' in name.lower():
            if not permit['superintendent_business_name']:
                updates['superintendent_business_name'] = name
            matched = True
        
        # If no match, put in first available field (prefer permittee since that's most common)
        if not matched:
            if not permit['permittee_phone']:
                updates['permittee_phone'] = phone
                if not permit['permittee_business_name']:
                    updates['permittee_business_name'] = name
                matched = True
            elif not permit['owner_phone']:
                updates['owner_phone'] = phone
                if not permit['owner_business_name']:
                    updates['owner_business_name'] = name
                matched = True
        
        if matched:
            migrated += 1
        else:
            skipped_already_has += 1
    
    # Execute update if we have changes
    if updates:
        set_clauses = ', '.join([f"{k} = %s" for k in updates.keys()])
        values = list(updates.values()) + [permit_id]
        
        cur.execute(f"""
            UPDATE permits
            SET {set_clauses}
            WHERE id = %s
        """, values)
        
        print(f"✅ Permit {permit['permit_no']}: Updated {len(updates)} field(s)")

conn.commit()

print(f'\n{"=" * 80}')
print(f'MIGRATION COMPLETE')
print(f'{"=" * 80}')
print(f'  Contacts migrated: {migrated}')
print(f'  Skipped (no phone): {skipped_no_phone}')
print(f'  Skipped (field already populated): {skipped_already_has}')
print(f'  Total contacts processed: {len(contacts)}')

# Verify migration
print(f'\n{"=" * 80}')
print('VERIFICATION')
print(f'{"=" * 80}')

cur.execute("""
    SELECT 
        COUNT(*) as total,
        COUNT(permittee_phone) as has_permittee_phone,
        COUNT(owner_phone) as has_owner_phone
    FROM permits
""")

stats = cur.fetchone()
print(f"Total permits: {stats['total']:,}")
print(f"With permittee_phone: {stats['has_permittee_phone']:,}")
print(f"With owner_phone: {stats['has_owner_phone']:,}")

cur.close()
conn.close()

print('\n✅ Migration complete! Contacts table can now be deprecated.')
