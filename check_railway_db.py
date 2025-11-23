#!/usr/bin/env python3
"""
Check Railway database schema and verify ACRIS enrichment setup
"""
import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv

load_dotenv()

# Get DATABASE_URL
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME')

DATABASE_URL = f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}'

print('Connecting to Railway database...')
print(f'Host: {DB_HOST}')
print(f'Database: {DB_NAME}\n')

conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
cur = conn.cursor()

print('='*70)
print('DATABASE SCHEMA CHECK')
print('='*70)

# Check buildings table structure
print('\n📋 BUILDINGS TABLE COLUMNS:')
cur.execute('''
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_name = 'buildings'
    ORDER BY ordinal_position
''')
columns = cur.fetchall()
for col in columns:
    nullable = '(NULL)' if col['is_nullable'] == 'YES' else '(NOT NULL)'
    print(f'  {col["column_name"]:<35} {col["data_type"]:<20} {nullable}')

# Check acris_transactions table
print('\n📋 ACRIS_TRANSACTIONS TABLE COLUMNS:')
cur.execute('''
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_name = 'acris_transactions'
    ORDER BY ordinal_position
''')
columns = cur.fetchall()
for col in columns:
    nullable = '(NULL)' if col['is_nullable'] == 'YES' else '(NOT NULL)'
    print(f'  {col["column_name"]:<35} {col["data_type"]:<20} {nullable}')

# Check acris_parties table
print('\n📋 ACRIS_PARTIES TABLE COLUMNS:')
cur.execute('''
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_name = 'acris_parties'
    ORDER BY ordinal_position
''')
columns = cur.fetchall()
for col in columns:
    nullable = '(NULL)' if col['is_nullable'] == 'YES' else '(NOT NULL)'
    print(f'  {col["column_name"]:<35} {col["data_type"]:<20} {nullable}')

print('\n' + '='*70)
print('DATA STATISTICS')
print('='*70)

# Buildings with BBL
cur.execute('SELECT COUNT(*) as count FROM buildings WHERE bbl IS NOT NULL')
bbl_count = cur.fetchone()['count']
print(f'\n📊 Buildings with BBL: {bbl_count:,}')

# Buildings enriched with ACRIS
cur.execute('SELECT COUNT(*) as count FROM buildings WHERE acris_last_enriched IS NOT NULL')
enriched_count = cur.fetchone()['count']
print(f'📊 Buildings enriched with ACRIS: {enriched_count:,}')

# Total transactions
cur.execute('SELECT COUNT(*) as count FROM acris_transactions')
trans_count = cur.fetchone()['count']
print(f'📊 Total ACRIS transactions: {trans_count:,}')

# Total parties
cur.execute('SELECT COUNT(*) as count FROM acris_parties')
party_count = cur.fetchone()['count']
print(f'📊 Total ACRIS parties: {party_count:,}')

# Sample enriched building
print('\n' + '='*70)
print('SAMPLE ENRICHED BUILDING')
print('='*70)
cur.execute('''
    SELECT 
        id, bbl, address, current_owner_name,
        sale_price, sale_date, sale_buyer_primary, sale_seller_primary,
        mortgage_amount, mortgage_date, mortgage_lender_primary,
        is_cash_purchase,
        acris_total_transactions, acris_deed_count, acris_mortgage_count,
        acris_last_enriched
    FROM buildings
    WHERE acris_last_enriched IS NOT NULL
    ORDER BY acris_last_enriched DESC
    LIMIT 1
''')

building = cur.fetchone()
if building:
    print(f'\n🏢 {building["address"]}')
    print(f'   BBL: {building["bbl"]}')
    print(f'   Owner: {building["current_owner_name"]}')
    print(f'\n   💰 Last Sale:')
    if building["sale_price"]:
        print(f'      Price: ${building["sale_price"]:,.2f}')
    else:
        print('      Price: N/A')
    if building["sale_date"]:
        print(f'      Date: {building["sale_date"]}')
    else:
        print('      Date: N/A')
    if building["sale_buyer_primary"]:
        print(f'      Buyer: {building["sale_buyer_primary"]}')
    else:
        print('      Buyer: N/A')
    if building["sale_seller_primary"]:
        print(f'      Seller: {building["sale_seller_primary"]}')
    else:
        print('      Seller: N/A')
    print(f'      Cash Purchase: {"Yes" if building["is_cash_purchase"] else "No"}')
    
    print(f'\n   🏦 Last Mortgage:')
    if building["mortgage_amount"]:
        print(f'      Amount: ${building["mortgage_amount"]:,.2f}')
    else:
        print('      Amount: N/A')
    if building["mortgage_date"]:
        print(f'      Date: {building["mortgage_date"]}')
    else:
        print('      Date: N/A')
    if building["mortgage_lender_primary"]:
        print(f'      Lender: {building["mortgage_lender_primary"]}')
    else:
        print('      Lender: N/A')
    
    print(f'\n   📊 Transactions:')
    print(f'      Total: {building["acris_total_transactions"]}')
    print(f'      Deeds: {building["acris_deed_count"]}')
    print(f'      Mortgages: {building["acris_mortgage_count"]}')
    print(f'\n   ⏰ Last Enriched: {building["acris_last_enriched"]}')
    
    # Get transaction count for this building
    cur.execute('SELECT COUNT(*) as count FROM acris_transactions WHERE building_id = %s', (building['id'],))
    trans_count_building = cur.fetchone()['count']
    
    # Get party count for this building
    cur.execute('SELECT COUNT(*) as count FROM acris_parties WHERE building_id = %s', (building['id'],))
    party_count_building = cur.fetchone()['count']
    
    print(f'\n   📝 Database Records:')
    print(f'      Transactions in DB: {trans_count_building}')
    print(f'      Parties in DB: {party_count_building}')
else:
    print('\n⚠️  No enriched buildings found yet')

cur.close()
conn.close()
print('\n' + '='*70)
print('✅ DATABASE CHECK COMPLETE')
print('='*70)
