#!/usr/bin/env python3
"""
Verify API data was inserted correctly
"""
import psycopg2
import os
from dotenv import load_dotenv
load_dotenv()

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', '5432')),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)
cursor = conn.cursor()

# Check latest permits from API
cursor.execute("""
    SELECT 
        permit_no,
        borough,
        address,
        permittee_business_name,
        owner_business_name,
        owner_phone,
        work_type,
        permit_type,
        api_source
    FROM permits
    WHERE api_source = 'nyc_open_data'
    ORDER BY api_last_updated DESC
    LIMIT 5
""")

print('Latest permits from NYC Open Data API:\n')
print(f"{'Permit':<12} {'Borough':<15} {'Permittee Business':<30} {'Owner Phone':<15}")
print('-' * 85)
for row in cursor.fetchall():
    permit_no, borough, address, permittee, owner, phone, work_type, permit_type, source = row
    print(f"{permit_no[:11]:<12} {(borough or 'N/A'):<15} {(permittee or owner or 'N/A')[:29]:<30} {(phone or 'N/A'):<15}")

# Count totals
cursor.execute("SELECT COUNT(*) FROM permits WHERE api_source = 'nyc_open_data'")
api_count = cursor.fetchone()[0]

cursor.execute("SELECT COUNT(*) FROM permits")
total_count = cursor.fetchone()[0]

cursor.execute("SELECT COUNT(*) FROM permits WHERE api_source IS NULL OR api_source != 'nyc_open_data'")
selenium_count = cursor.fetchone()[0]

print(f'\n{"="*85}')
print(f'✅ Total permits in database: {total_count:,}')
print(f'📊 Permits from NYC Open Data API: {api_count:,}')
print(f'🤖 Permits from Selenium scraper: {selenium_count:,}')
print(f'{"="*85}')
