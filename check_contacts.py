#!/usr/bin/env python3
"""Check contact-related tables"""
import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv
load_dotenv('dashboard_html/.env')

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    port=os.getenv('DB_PORT'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# Check for contact-related tables
cur.execute("""SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name LIKE '%contact%'""")
print('Contact-related tables:')
for row in cur.fetchall():
    print(f"  {row['table_name']}")

# Check permit_contacts
cur.execute("""SELECT column_name FROM information_schema.columns WHERE table_name = 'permit_contacts' ORDER BY ordinal_position""")
cols = cur.fetchall()
if cols:
    print('\npermit_contacts columns:')
    for row in cols:
        print(f"  {row['column_name']}")

# Check owner_contacts
cur.execute("""SELECT column_name FROM information_schema.columns WHERE table_name = 'owner_contacts' ORDER BY ordinal_position""")
cols = cur.fetchall()
if cols:
    print('\nowner_contacts columns:')
    for row in cols:
        print(f"  {row['column_name']}")

# Sample permit_contacts with BBL
cur.execute("""
    SELECT pc.*, p.bbl, p.address 
    FROM permit_contacts pc
    JOIN permits p ON pc.permit_id = p.id
    LIMIT 5
""")
print('\nSample permit_contacts:')
for row in cur.fetchall():
    print(f"  BBL {row['bbl']}: {row.get('name', 'N/A')} - {row.get('phone', 'N/A')}")

cur.close()
conn.close()
