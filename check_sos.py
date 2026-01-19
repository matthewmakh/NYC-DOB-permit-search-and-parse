#!/usr/bin/env python3
"""Check SOS lookups that returned real person names."""

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

# Find SOS lookups where principal looks like a real person (not LLC/agent)
cur.execute("""
    SELECT DISTINCT ON (b.bbl)
        b.address,
        COALESCE(b.current_owner_name, b.owner_name_rpad) as owner_name,
        b.sos_principal_name,
        b.sos_principal_title,
        b.sos_entity_name,
        b.sos_entity_status
    FROM buildings b
    JOIN permits p ON b.bbl = p.bbl
    WHERE p.zip_code IN ('11223', '11230', '11235', '11210', '11229')
      AND b.sos_principal_name IS NOT NULL
      AND b.sos_principal_name NOT LIKE '%LLC%'
      AND b.sos_principal_name NOT LIKE '%LLP%'
      AND b.sos_principal_name NOT LIKE '%CORP%'
      AND b.sos_principal_name NOT LIKE '%INC%'
      AND b.sos_principal_name NOT LIKE '%C/O%'
      AND b.sos_principal_name NOT LIKE '%ATTN%'
      AND LENGTH(b.sos_principal_name) > 5
    ORDER BY b.bbl
    LIMIT 15
""")

print("=" * 90)
print("SOS LOOKUPS WITH REAL PERSON NAMES (not agents/LLCs)")
print("=" * 90)
for row in cur.fetchall():
    addr, owner, principal, title, entity, status = row
    print(f"\n{addr}")
    print(f"   LLC Owner:    {owner}")
    print(f"   Real Person:  {principal}")
    print(f"   Title:        {title or '-'}")
    print(f"   Entity:       {entity}")
    print("-" * 60)

# Count stats
cur.execute("""
    SELECT 
        COUNT(DISTINCT CASE WHEN b.sos_principal_name IS NOT NULL THEN b.bbl END) as total_sos,
        COUNT(DISTINCT CASE WHEN b.sos_principal_name IS NOT NULL 
            AND b.sos_principal_name NOT LIKE '%LLC%'
            AND b.sos_principal_name NOT LIKE '%LLP%'
            AND b.sos_principal_name NOT LIKE '%CORP%'
            AND b.sos_principal_name NOT LIKE '%C/O%'
            AND b.sos_principal_name NOT LIKE '%ATTN%'
            THEN b.bbl END) as real_people
    FROM buildings b
    JOIN permits p ON b.bbl = p.bbl
    WHERE p.zip_code IN ('11223', '11230', '11235', '11210', '11229')
""")
total_sos, real_people = cur.fetchone()
pct = 100*real_people//total_sos if total_sos else 0
print(f"\n\nSTATS: {real_people}/{total_sos} SOS lookups returned real person names ({pct}%)")

conn.close()
