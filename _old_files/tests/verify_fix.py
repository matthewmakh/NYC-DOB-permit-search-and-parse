#!/usr/bin/env python3
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    port=os.getenv('DB_PORT', '5432'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)
cur = conn.cursor()

print('✅ Verifying the fix:\n')

# Count buildings with NULL last_updated (ready for immediate enrichment)
cur.execute('''
    SELECT COUNT(*) 
    FROM buildings 
    WHERE last_updated IS NULL
    AND (current_owner_name IS NULL OR owner_name_rpad IS NULL OR owner_name_hpd IS NULL)
''')
ready = cur.fetchone()[0]

# Count buildings enriched today (not eligible for 30 days)
cur.execute('''
    SELECT COUNT(*) 
    FROM buildings 
    WHERE last_updated::date = CURRENT_DATE
    AND (current_owner_name IS NULL OR owner_name_rpad IS NULL OR owner_name_hpd IS NULL)
''')
enriched_today = cur.fetchone()[0]

# Count buildings eligible for re-enrichment (>30 days old)
cur.execute('''
    SELECT COUNT(*) 
    FROM buildings 
    WHERE last_updated < NOW() - INTERVAL '30 days'
    AND (current_owner_name IS NULL OR owner_name_rpad IS NULL OR owner_name_hpd IS NULL)
''')
old_enrichment = cur.fetchone()[0]

print(f'Buildings with last_updated=NULL (ready now): {ready}')
print(f'Buildings enriched today (blocked for 30 days): {enriched_today}')
print(f'Buildings with old enrichment (>30 days, eligible): {old_enrichment}')

print(f'\n🎯 Total eligible for step2 right now: {ready + old_enrichment}')

print('\n✅ The fix ensures:')
print('   1. step1 creates buildings with last_updated=NULL')
print('   2. step2 immediately finds them (NULL check passes)')
print('   3. step2 sets last_updated=NOW() after enriching')
print('   4. Buildings are protected from re-enrichment for 30 days')

print('\n📊 Current state after today\'s enrichment:')
print(f'   • {enriched_today} buildings enriched today (won\'t be touched for 30 days)')
print(f'   • {ready} buildings still waiting (have NULL last_updated)')
print(f'   • {old_enrichment} buildings with stale data (>30 days old)')

conn.close()
