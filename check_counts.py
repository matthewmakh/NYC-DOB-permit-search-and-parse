import psycopg2
import os
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError('DATABASE_URL environment variable is required')
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

print("=== PHONE DATA BY SOURCE ===\n")

for source in ['nyc_open_data', 'dob_now_approved', 'dob_now_filings']:
    print(f"--- {source} ---")
    
    cur.execute("SELECT COUNT(*) FROM permits WHERE api_source = %s", (source,))
    total = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM permits WHERE api_source = %s AND owner_phone IS NOT NULL AND owner_phone != ''", (source,))
    owner_phone = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM permits WHERE api_source = %s AND permittee_phone IS NOT NULL AND permittee_phone != ''", (source,))
    permittee_phone = cur.fetchone()[0]
    
    print(f"  Total permits: {total:,}")
    print(f"  With owner_phone: {owner_phone:,}")
    print(f"  With permittee_phone: {permittee_phone:,}")
    print()

conn.close()
