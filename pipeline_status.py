#!/usr/bin/env python3
"""
Pipeline Status Check - Shows data at each enrichment stage
"""
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

print('=' * 70)
print('DATA PIPELINE STATUS - From Raw Permits to Owner Names')
print('=' * 70)

# Step 0: Raw permits
cur.execute('SELECT COUNT(*) FROM permits')
total_permits = cur.fetchone()[0]
cur.execute("SELECT api_source, COUNT(*) FROM permits GROUP BY api_source ORDER BY COUNT(*) DESC")
sources = cur.fetchall()
print(f'\nSTEP 0: Raw Permits = {total_permits:,}')
for src, cnt in sources:
    print(f'   {src or "Unknown"}: {cnt:,}')

# Permits with owner data from DOB
cur.execute("SELECT COUNT(*) FROM permits WHERE owner_business_name IS NOT NULL AND owner_business_name != ''")
permits_with_owner = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM permits WHERE owner_phone IS NOT NULL AND owner_phone != ''")
permits_with_phone = cur.fetchone()[0]
print(f'\n   Permits with owner_business_name: {permits_with_owner:,}')
print(f'   Permits with owner_phone: {permits_with_phone:,}')

# Step 1: Buildings
cur.execute('SELECT COUNT(*) FROM buildings')
total_buildings = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM permits WHERE bbl IS NOT NULL')
linked_permits = cur.fetchone()[0]
print(f'\nSTEP 1: Buildings Table = {total_buildings:,}')
print(f'   Permits with BBL: {linked_permits:,}')

# Step 2: PLUTO/RPAD/HPD enrichment
cur.execute("SELECT COUNT(*) FROM buildings WHERE current_owner_name IS NOT NULL")
pluto = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM buildings WHERE owner_name_rpad IS NOT NULL")
rpad = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM buildings WHERE owner_name_hpd IS NOT NULL")
hpd = cur.fetchone()[0]
print(f'\nSTEP 2: PLUTO/RPAD/HPD Enrichment')
print(f'   current_owner_name (PLUTO): {pluto:,}')
print(f'   owner_name_rpad (Tax):      {rpad:,}')
print(f'   owner_name_hpd (HPD):       {hpd:,}')

# Step 3: ACRIS
cur.execute("SELECT COUNT(*) FROM buildings WHERE sale_buyer_primary IS NOT NULL")
acris = cur.fetchone()[0]
print(f'\nSTEP 3: ACRIS Enrichment')
print(f'   sale_buyer_primary: {acris:,}')

# Step 5: SOS
cur.execute("SELECT COUNT(*) FROM buildings WHERE sos_lookup_attempted = TRUE")
sos_attempted = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM buildings WHERE sos_principal_name IS NOT NULL")
sos_found = cur.fetchone()[0]
print(f'\nSTEP 5: NY SOS Enrichment')
print(f'   Lookups attempted: {sos_attempted:,}')
print(f'   Principal names found: {sos_found:,}')

# Summary
print('\n' + '=' * 70)
print('OWNER NAME SOURCES SUMMARY')
print('=' * 70)

cur.execute("""
    SELECT 
        COUNT(*) FILTER (WHERE current_owner_name IS NOT NULL) as pluto,
        COUNT(*) FILTER (WHERE owner_name_rpad IS NOT NULL) as rpad,
        COUNT(*) FILTER (WHERE owner_name_hpd IS NOT NULL) as hpd,
        COUNT(*) FILTER (WHERE sale_buyer_primary IS NOT NULL) as acris,
        COUNT(*) FILTER (WHERE sos_principal_name IS NOT NULL) as sos,
        COUNT(*) FILTER (WHERE 
            current_owner_name IS NOT NULL OR 
            owner_name_rpad IS NOT NULL OR 
            owner_name_hpd IS NOT NULL OR
            sale_buyer_primary IS NOT NULL OR
            sos_principal_name IS NOT NULL
        ) as any_owner
    FROM buildings
""")
row = cur.fetchone()
print(f'\nBuildings with owner from:')
print(f'   PLUTO (current_owner_name):  {row[0]:,}')
print(f'   RPAD (owner_name_rpad):      {row[1]:,}')
print(f'   HPD (owner_name_hpd):        {row[2]:,}')
print(f'   ACRIS (sale_buyer_primary):  {row[3]:,}')
print(f'   SOS (sos_principal_name):    {row[4]:,}')
print(f'\n   ANY owner source:            {row[5]:,} / {total_buildings:,}')

conn.close()
