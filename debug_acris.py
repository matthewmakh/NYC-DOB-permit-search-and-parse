#!/usr/bin/env python3
"""Debug script to check ACRIS Parties API"""
import requests
from datetime import datetime, timedelta

# Get last 10 days of party records to see what's there
end_date = datetime.now().strftime('%Y-%m-%d')
start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')

url = 'https://data.cityofnewyork.us/resource/636b-3b5g.json'

# First, just get some records without date filter
params = {
    '$limit': 20,
}

print('='*70)
print('ACRIS PARTIES API DEBUG')
print('='*70)
print(f'\nQuerying: {url}')
print(f'Params: {params}')

response = requests.get(url, params=params, timeout=30)
print(f'Status: {response.status_code}')

if response.status_code == 200:
    data = response.json()
    print(f'\nGot {len(data)} records')
    
    if data:
        print('\n--- ALL FIELDS IN FIRST RECORD ---')
        for k, v in sorted(data[0].items()):
            print(f'  {k}: {v}')
        
        print('\n--- SAMPLE PARTY NAMES ---')
        for i, record in enumerate(data[:10]):
            name = record.get('name', 'N/A')
            party_type = record.get('party_type', 'N/A')
            doc_id = record.get('document_id', 'N/A')
            print(f'  {i+1}. {name[:50]:50} | type={party_type} | doc={doc_id}')
else:
    print(f'Error: {response.text[:500]}')

# Now search for EXCALIBUR specifically
print('\n' + '='*70)
print('SEARCHING FOR EXCALIBUR')
print('='*70)

# Use simpler query without UPPER() function - much faster
params = {
    '$where': "name LIKE '%EXCALIBUR%' OR name LIKE '%Excalibur%'",
    '$limit': 100,
}
print(f'\nParams: {params}')

response = requests.get(url, params=params, timeout=30)
print(f'Status: {response.status_code}')

if response.status_code == 200:
    data = response.json()
    print(f'\nFound {len(data)} EXCALIBUR records')
    
    for i, record in enumerate(data[:20]):
        name = record.get('name', 'N/A')
        party_type = record.get('party_type', 'N/A')
        doc_id = record.get('document_id', 'N/A')
        print(f'  {i+1}. {name[:60]} | type={party_type}')
else:
    print(f'Error: {response.text[:500]}')

# Search for any title companies
print('\n' + '='*70)
print('SEARCHING FOR TITLE COMPANIES (sample)')
print('='*70)

# Use simpler query without UPPER() function
params = {
    '$where': "name LIKE '%TITLE%' OR name LIKE '%Title%'",
    '$limit': 20,
}
print(f'\nParams: {params}')

response = requests.get(url, params=params, timeout=30)
print(f'Status: {response.status_code}')

if response.status_code == 200:
    data = response.json()
    print(f'\nFound records with TITLE in name (showing first 20)')
    
    for i, record in enumerate(data[:20]):
        name = record.get('name', 'N/A')
        party_type = record.get('party_type', 'N/A')
        print(f'  {i+1}. {name[:60]} | type={party_type}')
else:
    print(f'Error: {response.text[:500]}')
