#!/usr/bin/env python3
"""
Step 3: Enrich buildings from NYC ACRIS (Automated City Register Information System)
- Gets property transaction history by BBL
- Extracts most recent deed (purchase) and mortgage information
- Updates buildings with purchase_date, purchase_price, mortgage_amount
"""

import psycopg2
import psycopg2.extras
import os
import requests
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Support both DATABASE_URL and individual DB_* variables
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    # Build from individual components
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME')
    
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        raise ValueError("Either DATABASE_URL or DB_HOST/DB_USER/DB_PASSWORD/DB_NAME must be set")
    
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# NYC Open Data ACRIS API endpoints
ACRIS_REAL_PROPERTY_LEGALS = "https://data.cityofnewyork.us/resource/8h5j-fqxa.json"
ACRIS_REAL_PROPERTY_MASTER = "https://data.cityofnewyork.us/resource/bnx9-e6tj.json"
ACRIS_REAL_PROPERTY_PARTIES = "https://data.cityofnewyork.us/resource/636b-3b5g.json"


def get_acris_transactions_for_bbl(bbl):
    """
    Query ACRIS API for property transactions by BBL
    Returns most recent deed and mortgage
    """
    try:
        # ACRIS data - try simple query first
        params = {
            "bbl": bbl,
            "$limit": 50
        }
        
        print(f"      Querying ACRIS legals...")
        response = requests.get(ACRIS_REAL_PROPERTY_LEGALS, params=params, timeout=15)
        
        if response.status_code != 200:
            print(f"      API returned status {response.status_code}")
            return None
            
        legals = response.json()
        print(f"      Found {len(legals)} legal records")
        
        if not legals:
            return None
        
        # Get unique document IDs
        doc_ids = list(set([legal['document_id'] for legal in legals if 'document_id' in legal]))
        
        if not doc_ids:
            return None
        
        # Get document details from master table
        # Look for deeds (DEED, DEED-CO, etc.) and mortgages (MTGE)
        deed_data = None
        mortgage_data = None
        
        for doc_id in doc_ids[:20]:  # Check first 20 documents
            params = {
                "$where": f"document_id='{doc_id}'",
                "$limit": 1
            }
            
            response = requests.get(ACRIS_REAL_PROPERTY_MASTER, params=params, timeout=10)
            response.raise_for_status()
            docs = response.json()
            
            if not docs:
                continue
            
            doc = docs[0]
            doc_type = doc.get('doc_type', '').upper()
            
            # Check for deed (sale transaction)
            if not deed_data and 'DEED' in doc_type:
                deed_data = {
                    'document_id': doc_id,
                    'doc_type': doc_type,
                    'doc_date': doc.get('doc_date'),
                    'recorded_datetime': doc.get('recorded_datetime'),
                    'doc_amount': float(doc.get('doc_amount', 0) or 0)
                }
            
            # Check for mortgage
            if not mortgage_data and doc_type == 'MTGE':
                mortgage_data = {
                    'document_id': doc_id,
                    'doc_type': doc_type,
                    'doc_date': doc.get('doc_date'),
                    'recorded_datetime': doc.get('recorded_datetime'),
                    'doc_amount': float(doc.get('doc_amount', 0) or 0)
                }
            
            # Stop if we have both
            if deed_data and mortgage_data:
                break
        
        return {
            'deed': deed_data,
            'mortgage': mortgage_data
        }
        
    except Exception as e:
        print(f"⚠️ Error fetching ACRIS data for BBL {bbl}: {e}")
        return None


def parse_acris_date(date_str):
    """Convert ACRIS date string to date object"""
    if not date_str:
        return None
    try:
        # ACRIS dates are like "2023-05-15T00:00:00.000"
        return datetime.strptime(date_str[:10], '%Y-%m-%d').date()
    except:
        return None


def enrich_buildings_from_acris():
    """
    Main process:
    1. Get buildings without purchase data
    2. Query ACRIS API for each BBL
    3. Update with purchase_date, purchase_price, mortgage_amount
    """
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    
    print("Step 3: Enriching Buildings from ACRIS")
    print("=" * 60)
    
    # Get buildings that need ACRIS data
    cur.execute("""
        SELECT id, bbl, address, current_owner_name
        FROM buildings
        WHERE bbl IS NOT NULL
        AND purchase_date IS NULL
        ORDER BY id
    """)
    
    buildings = cur.fetchall()
    print(f"\n📊 Found {len(buildings)} buildings to enrich")
    
    if not buildings:
        print("   No buildings need enrichment. All done!")
        cur.close()
        conn.close()
        return
    
    enriched = 0
    failed = 0
    
    for i, building in enumerate(buildings, 1):
        bbl = building['bbl']
        building_id = building['id']
        address = building['address']
        owner = building['current_owner_name'] or 'Unknown'
        
        print(f"\n🔍 [{i}/{len(buildings)}] BBL {bbl} ({address})...")
        print(f"   Owner: {owner}")
        
        # Get ACRIS data
        acris_data = get_acris_transactions_for_bbl(bbl)
        
        if acris_data and acris_data.get('deed'):
            deed = acris_data['deed']
            mortgage = acris_data.get('mortgage')
            
            purchase_date = parse_acris_date(deed.get('doc_date') or deed.get('recorded_datetime'))
            purchase_price = deed.get('doc_amount', 0)
            mortgage_amount = mortgage.get('doc_amount', 0) if mortgage else None
            
            # Update building record
            cur.execute("""
                UPDATE buildings
                SET purchase_date = %s,
                    purchase_price = %s,
                    mortgage_amount = %s,
                    last_updated = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (
                purchase_date,
                purchase_price if purchase_price > 0 else None,
                mortgage_amount if mortgage_amount and mortgage_amount > 0 else None,
                building_id
            ))
            conn.commit()
            
            print(f"   ✅ Purchase: ${purchase_price:,.0f} on {purchase_date}")
            if mortgage_amount:
                print(f"      Mortgage: ${mortgage_amount:,.0f}")
            enriched += 1
        else:
            print(f"   ❌ No ACRIS transaction data found")
            failed += 1
        
        # Rate limit: 0.5 second delay between API calls
        if i < len(buildings):
            time.sleep(0.5)
    
    print(f"\n✅ Complete!")
    print(f"   Buildings enriched: {enriched}")
    print(f"   Failed/No data: {failed}")
    
    # Show sample results
    cur.execute("""
        SELECT bbl, address, current_owner_name, 
               purchase_date, purchase_price, mortgage_amount
        FROM buildings
        WHERE purchase_date IS NOT NULL
        ORDER BY purchase_date DESC
        LIMIT 5
    """)
    
    results = cur.fetchall()
    if results:
        print(f"\n📋 Sample enriched buildings:")
        for r in results:
            print(f"   {r['bbl']}: {r['current_owner_name']}")
            print(f"      {r['address']}")
            price_str = f"${r['purchase_price']:,.0f}" if r['purchase_price'] else "Unknown"
            print(f"      Purchased: {r['purchase_date']} for {price_str}")
            if r['mortgage_amount']:
                print(f"      Mortgage: ${r['mortgage_amount']:,.0f}")
    
    cur.close()
    conn.close()


if __name__ == "__main__":
    enrich_buildings_from_acris()
