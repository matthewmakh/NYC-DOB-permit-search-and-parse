#!/usr/bin/env python3
"""Check enrichment data before migration"""
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

# Check current enrichments
cur.execute('SELECT COUNT(*) as cnt FROM user_enrichments')
print(f"Total enrichments: {cur.fetchone()['cnt']}")

# Check for any nulls in owner_name_searched
cur.execute('SELECT COUNT(*) as cnt FROM user_enrichments WHERE owner_name_searched IS NULL')
print(f"With NULL owner_name: {cur.fetchone()['cnt']}")

# Check if any user+building has multiple rows (shouldn't with current constraint)
cur.execute('''
    SELECT user_id, building_id, COUNT(*) as cnt
    FROM user_enrichments 
    GROUP BY user_id, building_id
    HAVING COUNT(*) > 1
''')
multi = cur.fetchall()
print(f"User+Building combos with multiple rows: {len(multi)}")

# Show actual data
cur.execute('SELECT id, user_id, building_id, owner_name_searched FROM user_enrichments ORDER BY id')
print("\nAll enrichment records:")
for row in cur.fetchall():
    print(f"  ID {row['id']}: user={row['user_id']}, building={row['building_id']}, owner={row['owner_name_searched']}")

cur.close()
conn.close()
