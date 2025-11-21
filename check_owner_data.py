import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Database configuration matching app.py
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': os.getenv('DB_PORT', '5432'),
    'database': os.getenv('DB_NAME', 'permits_db'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', '')
}

conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
cur = conn.cursor()

# Check what owner data we have in buildings table
print('🏢 OWNER DATA IN BUILDINGS TABLE:')
print('=' * 80)
cur.execute("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name = 'buildings' 
    AND column_name LIKE '%owner%'
    ORDER BY ordinal_position
""")
owner_cols = cur.fetchall()
for col in owner_cols:
    print(f"{col['column_name']:<30} {col['data_type']}")

# Get sample owner data for BBL 1006130048
print('\n\n📋 SAMPLE OWNER DATA (BBL 1006130048):')
print('=' * 80)
cur.execute("""
    SELECT current_owner_name, owner_name_rpad, owner_name_hpd
    FROM buildings 
    WHERE bbl = '1006130048'
""")
owner_data = cur.fetchone()
if owner_data:
    for key, value in owner_data.items():
        print(f"{key:<25} {value}")
else:
    print('No owner data found')

# Determine which owner field to use
owner_name = owner_data['current_owner_name'] if owner_data else None

# Check if we can find other properties by same owner
print('\n\n🏘️  PORTFOLIO SIZE:')
print('=' * 80)
if owner_name:
    cur.execute("""
        SELECT COUNT(*) as count,
               SUM(assessed_total_value) as total_value,
               SUM(residential_units) as total_units
        FROM buildings 
        WHERE current_owner_name = %s
    """, (owner_name,))
    portfolio = cur.fetchone()
    print(f"Properties owned by '{owner_name}':")
    print(f"  Total Properties: {portfolio['count']}")
    if portfolio['total_value']:
        print(f"  Total Assessed Value: ${portfolio['total_value']:,.0f}")
    if portfolio['total_units']:
        print(f"  Total Units: {portfolio['total_units']}")
    
    # Get top properties
    print('\n  Top Properties:')
    cur.execute("""
        SELECT bbl, address, assessed_total_value, total_units
        FROM buildings 
        WHERE current_owner_name = %s
        ORDER BY assessed_total_value DESC NULLS LAST
        LIMIT 5
    """, (owner_name,))
    top_props = cur.fetchall()
    for prop in top_props:
        val = f"${prop['assessed_total_value']:,.0f}" if prop['assessed_total_value'] else 'N/A'
        units = prop['total_units'] or 'N/A'
        print(f"    {prop['bbl']}: {val:>20} | {units:>3} units | {prop['address']}")

# Check ACRIS parties
print('\n\n👥 ACRIS PARTIES (ownership history):')
print('=' * 80)
cur.execute("""
    SELECT DISTINCT 
        ap.party_name,
        ap.party_type,
        at.doc_type,
        at.recorded_date,
        at.doc_amount
    FROM acris_parties ap
    JOIN acris_transactions at ON ap.transaction_id = at.id
    WHERE at.building_id = (SELECT id FROM buildings WHERE bbl = '1006130048')
    AND ap.party_type IN ('BUYER', 'SELLER', 'GRANTOR', 'GRANTEE')
    ORDER BY at.recorded_date DESC
    LIMIT 10
""")
parties = cur.fetchall()
print(f"Found {len(parties)} ownership-related parties:")
for party in parties:
    amt = f"${party['doc_amount']:,.0f}" if party['doc_amount'] else 'N/A'
    print(f"  {party['recorded_date']} | {party['party_type']:<8} | {party['doc_type']:<6} | {amt:>15} | {party['party_name']}")

# Check permit applicants
print('\n\n🔨 PERMIT APPLICANTS (activity intelligence):')
print('=' * 80)
cur.execute("""
    SELECT DISTINCT applicant, COUNT(*) as permit_count
    FROM permits
    WHERE bbl = '1006130048'
    AND applicant IS NOT NULL
    GROUP BY applicant
    ORDER BY permit_count DESC
    LIMIT 10
""")
applicants = cur.fetchall()
print(f"Found {len(applicants)} unique applicants:")
for app in applicants:
    print(f"  {app['permit_count']:>3} permits | {app['applicant']}")

cur.close()
conn.close()
