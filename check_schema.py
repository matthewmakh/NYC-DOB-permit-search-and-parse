#!/usr/bin/env python3
"""Check enrichment schema"""
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

# Check buildings table enrichment columns
cur.execute("""SELECT column_name FROM information_schema.columns 
    WHERE table_name = 'buildings' AND column_name LIKE 'enriched%'""")
print('Buildings enrichment columns:')
for row in cur.fetchall():
    print(f"  {row['column_name']}")

# Check user_enrichments table
cur.execute("""SELECT column_name FROM information_schema.columns 
    WHERE table_name = 'user_enrichments'""")
print('\nuser_enrichments columns:')
for row in cur.fetchall():
    print(f"  {row['column_name']}")

cur.close()
conn.close()
