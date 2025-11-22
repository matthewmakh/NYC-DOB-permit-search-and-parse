#!/usr/bin/env python3
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    port=os.getenv('DB_PORT', '5432'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)
cur = conn.cursor()

print("Checking why step2 found 0 buildings...\n")

# Total new buildings
cur.execute('SELECT COUNT(*) FROM buildings WHERE id > 1383')
total_new = cur.fetchone()[0]
print(f'✅ Total new buildings (ID > 1383): {total_new}')

# With BBL
cur.execute('SELECT COUNT(*) FROM buildings WHERE id > 1383 AND bbl IS NOT NULL')
with_bbl = cur.fetchone()[0]
print(f'✅ With BBL: {with_bbl}')

# With NULL owner fields
cur.execute('''
    SELECT COUNT(*)
    FROM buildings
    WHERE id > 1383
    AND bbl IS NOT NULL
    AND (current_owner_name IS NULL OR owner_name_rpad IS NULL OR owner_name_hpd IS NULL)
''')
with_null_owners = cur.fetchone()[0]
print(f'✅ With at least one NULL owner field: {with_null_owners}')

# With NULL or old last_updated
cur.execute('''
    SELECT COUNT(*)
    FROM buildings
    WHERE id > 1383
    AND bbl IS NOT NULL
    AND (last_updated IS NULL OR last_updated < NOW() - INTERVAL '30 days')
''')
with_old_update = cur.fetchone()[0]
print(f'✅ With NULL or old last_updated: {with_old_update}')

# Final eligibility (all conditions)
cur.execute('''
    SELECT COUNT(*)
    FROM buildings
    WHERE id > 1383
    AND bbl IS NOT NULL
    AND (current_owner_name IS NULL OR owner_name_rpad IS NULL OR owner_name_hpd IS NULL)
    AND (last_updated IS NULL OR last_updated < NOW() - INTERVAL '30 days')
''')
eligible = cur.fetchone()[0]
print(f'\n🎯 Buildings eligible for enrichment: {eligible}')

# Sample of 3 buildings
cur.execute('''
    SELECT id, bbl, address, current_owner_name, owner_name_rpad, owner_name_hpd, last_updated
    FROM buildings
    WHERE id > 1383
    LIMIT 3
''')

print(f'\nSample of new buildings:')
for row in cur.fetchall():
    print(f'\n  ID {row[0]}: {row[2]}')
    print(f'    BBL: {row[1]}')
    print(f'    current_owner_name: {row[3]}')
    print(f'    owner_name_rpad: {row[4]}')
    print(f'    owner_name_hpd: {row[5]}')
    print(f'    last_updated: {row[6]}')

conn.close()
