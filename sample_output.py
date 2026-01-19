#!/usr/bin/env python3
"""Quick script to show sample enriched buildings."""

import psycopg2
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
cur = conn.cursor()

# Check owner name sources
cur.execute('''
    SELECT 
        COUNT(DISTINCT b.bbl) as total,
        COUNT(DISTINCT CASE WHEN b.current_owner_name IS NOT NULL THEN b.bbl END) as current_owner,
        COUNT(DISTINCT CASE WHEN b.owner_name_rpad IS NOT NULL THEN b.bbl END) as rpad_owner,
        COUNT(DISTINCT CASE WHEN b.owner_name_hpd IS NOT NULL THEN b.bbl END) as hpd_owner
    FROM buildings b
    JOIN permits p ON b.bbl = p.bbl
    WHERE p.zip_code IN ('11223', '11230', '11235', '11210', '11229')
''')
total, current, rpad, hpd = cur.fetchone()
print('=' * 80)
print('OWNER NAME SOURCES:')
print('=' * 80)
print(f'   Total buildings: {total}')
print(f'   current_owner_name (ACRIS): {current} ({100*current//total}%)')
print(f'   owner_name_rpad: {rpad} ({100*rpad//total}%)')
print(f'   owner_name_hpd: {hpd} ({100*hpd//total}%)')

# Sample owner names
print()
print('=' * 80)
print('SAMPLE OWNER NAMES:')
print('=' * 80)
cur.execute('''
    SELECT DISTINCT ON (b.bbl)
        b.address,
        b.current_owner_name,
        b.owner_name_rpad,
        b.owner_name_hpd
    FROM buildings b
    JOIN permits p ON b.bbl = p.bbl
    WHERE p.zip_code IN ('11223', '11230', '11235', '11210', '11229')
      AND (b.current_owner_name IS NOT NULL OR b.owner_name_rpad IS NOT NULL OR b.owner_name_hpd IS NOT NULL)
    ORDER BY b.bbl
    LIMIT 10
''')
for row in cur.fetchall():
    addr, curr, rpad_name, hpd_name = row
    print(f'{addr}')
    print(f'   ACRIS: {curr or "-"}')
    print(f'   RPAD:  {rpad_name or "-"}')
    print(f'   HPD:   {hpd_name or "-"}')
    print()

conn.close()
