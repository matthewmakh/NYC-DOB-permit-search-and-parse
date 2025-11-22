#!/usr/bin/env python3
"""Verify contacts table migration was successful"""

import os
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras

load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME')
    DATABASE_URL = f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}'

conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
cur = conn.cursor()

print('=' * 80)
print('VERIFICATION: CONTACTS TABLE MIGRATION')
print('=' * 80)
print()

# 1. Check contacts table structure
print('1. Contacts Table Schema:')
cur.execute("""
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_name = 'contacts'
    ORDER BY ordinal_position
""")
cols = cur.fetchall()
for col in cols:
    nullable = '✓' if col['is_nullable'] == 'YES' else '✗'
    print(f"   {col['column_name']:<25} {col['data_type']:<20} Nullable: {nullable}")

print()

# 2. Check permit_contacts junction table
print('2. Permit_Contacts Junction Table Schema:')
cur.execute("""
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_name = 'permit_contacts'
    ORDER BY ordinal_position
""")
cols = cur.fetchall()
for col in cols:
    nullable = '✓' if col['is_nullable'] == 'YES' else '✗'
    print(f"   {col['column_name']:<25} {col['data_type']:<20} Nullable: {nullable}")

print()

# 3. Check data counts
print('3. Data Counts:')
cur.execute('SELECT COUNT(*) as count FROM contacts')
contact_count = cur.fetchone()['count']
print(f"   Total contacts: {contact_count:,}")

cur.execute('SELECT COUNT(*) as count FROM permit_contacts')
link_count = cur.fetchone()['count']
print(f"   Total permit-contact links: {link_count:,}")

cur.execute('SELECT COUNT(*) as count FROM contacts WHERE phone_validated_at IS NOT NULL')
validated = cur.fetchone()['count']
print(f"   Validated phones: {validated:,}")

cur.execute('SELECT COUNT(*) as count FROM contacts WHERE is_mobile = TRUE')
mobiles = cur.fetchone()['count']
print(f"   Mobile numbers: {mobiles:,}")

cur.execute('SELECT COUNT(*) as count FROM contacts WHERE is_mobile = FALSE')
landlines = cur.fetchone()['count']
print(f"   Landline numbers: {landlines:,}")

print()

# 4. Check for duplicates (should be 0)
print('4. Duplicate Check:')
cur.execute("""
    SELECT phone, COUNT(*) as count
    FROM contacts
    GROUP BY phone
    HAVING COUNT(*) > 1
""")
dupes = cur.fetchall()
if dupes:
    print(f"   ⚠️  Found {len(dupes)} duplicate phones!")
    for d in dupes[:5]:
        print(f"      {d['phone']}: {d['count']} times")
else:
    print('   ✅ No duplicates (perfect!)')

print()

# 5. Sample validated contacts
print('5. Sample Validated Contacts:')
cur.execute("""
    SELECT name, phone, line_type, carrier_name, is_mobile
    FROM contacts
    WHERE phone_validated_at IS NOT NULL
    ORDER BY phone_validated_at DESC
    LIMIT 10
""")
samples = cur.fetchall()
for s in samples:
    mobile = '📱' if s['is_mobile'] else '☎️ '
    carrier = s['carrier_name'][:20] if s['carrier_name'] else 'N/A'
    print(f"   {mobile} {s['name'][:30]:<30} | {s['phone']:<12} | {s['line_type']:<15} | {carrier}")

print()

# 6. Check many-to-many relationships
print('6. Many-to-Many Relationships (Contacts with Multiple Permits):')
cur.execute("""
    SELECT c.name, c.phone, COUNT(pc.permit_id) as permit_count
    FROM contacts c
    JOIN permit_contacts pc ON c.id = pc.contact_id
    GROUP BY c.id, c.name, c.phone
    HAVING COUNT(pc.permit_id) > 10
    ORDER BY permit_count DESC
    LIMIT 5
""")
multi = cur.fetchall()
for m in multi:
    print(f"   {m['name'][:40]:<40} | {m['phone']:<12} | {m['permit_count']:>4} permits")

print()

# 7. Check permits table still has phone columns
print('7. Permits Table Phone Columns (Should Still Exist):')
cur.execute("""
    SELECT column_name
    FROM information_schema.columns
    WHERE table_name = 'permits'
    AND column_name LIKE '%phone%'
""")
permit_phone_cols = cur.fetchall()
for col in permit_phone_cols:
    print(f"   ✓ {col['column_name']}")

print()
print('=' * 80)
print('✅ VERIFICATION COMPLETE')
print('=' * 80)

cur.close()
conn.close()
