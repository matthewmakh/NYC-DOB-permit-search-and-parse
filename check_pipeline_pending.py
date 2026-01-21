#!/usr/bin/env python3
"""Check how many properties need enrichment for each pipeline step"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv('dashboard_html/.env')

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    port=os.getenv('DB_PORT'),
    database=os.getenv('DB_NAME'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD')
)
cur = conn.cursor(cursor_factory=RealDictCursor)

print('=' * 70)
print('ENRICHMENT PIPELINE - PENDING WORK ESTIMATE')
print('=' * 70)

# Step 1: Permits needing BBL linking
cur.execute('''
    SELECT COUNT(*) as count FROM permits 
    WHERE bbl IS NULL AND block IS NOT NULL AND lot IS NOT NULL
''')
step1 = cur.fetchone()['count']
print(f'\nStep 1 - Link Permits to Buildings:')
print(f'   Permits without BBL (but have block/lot): {step1:,}')

# Step 2: Buildings needing PLUTO enrichment (check if has owner data)
cur.execute('''
    SELECT COUNT(*) as count FROM buildings 
    WHERE current_owner_name IS NULL AND bbl IS NOT NULL
''')
step2 = cur.fetchone()['count']
print(f'\nStep 2 - Enrich from PLUTO/RPAD/HPD:')
print(f'   Buildings without owner data: {step2:,}')

# Step 3: Buildings needing ACRIS enrichment
cur.execute('''
    SELECT COUNT(*) as count FROM buildings 
    WHERE acris_last_enriched IS NULL 
    AND bbl IS NOT NULL
''')
step3 = cur.fetchone()['count']
print(f'\nStep 3 - Enrich from ACRIS:')
print(f'   Buildings not yet checked for ACRIS: {step3:,}')

# Step 4: Buildings needing Tax/Lien enrichment
cur.execute('''
    SELECT COUNT(*) as count FROM buildings 
    WHERE tax_lien_last_checked IS NULL AND bbl IS NOT NULL
''')
step4 = cur.fetchone()['count']
print(f'\nStep 4 - Enrich from Tax/Lien Data:')
print(f'   Buildings not yet checked for tax liens: {step4:,}')

# Step 5: Buildings needing SOS enrichment (LLC owners)
cur.execute('''
    SELECT COUNT(*) as count FROM buildings 
    WHERE sos_last_enriched IS NULL 
    AND current_owner_name IS NOT NULL
    AND (current_owner_name ILIKE '%%LLC%%' OR current_owner_name ILIKE '%%CORP%%' OR current_owner_name ILIKE '%%INC%%')
''')
step5 = cur.fetchone()['count']
print(f'\nStep 5 - Enrich from NY SOS (LLC Owners):')
print(f'   LLC-owned buildings not yet enriched: {step5:,}')

# Geocoding: Permits needing coordinates
cur.execute('''
    SELECT COUNT(*) as count FROM permits 
    WHERE latitude IS NULL
''')
geocode = cur.fetchone()['count']
print(f'\nStep 6 - Geocode Permits:')
print(f'   Permits without coordinates: {geocode:,}')

# Total buildings and permits
cur.execute('SELECT COUNT(*) as count FROM buildings')
total_buildings = cur.fetchone()['count']
cur.execute('SELECT COUNT(*) as count FROM permits')
total_permits = cur.fetchone()['count']

print(f'\n' + '=' * 70)
print('TOTALS')
print('=' * 70)
print(f'Total Buildings: {total_buildings:,}')
print(f'Total Permits: {total_permits:,}')

cur.close()
conn.close()
