#!/usr/bin/env python3
"""
Step 3: Enhanced ACRIS Enrichment - PARALLEL VERSION
- Queries ACRIS Real Property Legals, Master, and Parties APIs
- Populates acris_transactions with full transaction history
- Populates acris_parties with buyers, sellers, lenders (with addresses!)
- Updates buildings table with primary sale/mortgage data and transaction counts
- Includes 30-day refresh cycle to avoid re-processing recent data
- PARALLEL: 3 workers processing buildings simultaneously
"""

import psycopg2
import psycopg2.extras
import os
import requests
import time
from datetime import datetime
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys

load_dotenv()

# Parallel processing configuration
NUM_WORKERS = 3
BATCH_SIZE = 50  # Smaller batches since each building has many API calls
VERBOSE_LOGGING = os.getenv('VERBOSE_LOGGING', 'false').lower() == 'true'

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

# Socrata API Authentication (prevents rate limiting)
SOCRATA_APP_TOKEN = os.getenv('SOCRATA_APP_TOKEN')
SOCRATA_KEY_ID = os.getenv('SOCRATA_KEY_ID')
SOCRATA_KEY_SECRET = os.getenv('SOCRATA_KEY_SECRET')

# Build auth headers if credentials provided
API_HEADERS = {}
USE_AUTH = False  # Flag to enable/disable auth

if USE_AUTH and SOCRATA_KEY_ID and SOCRATA_KEY_SECRET:
    import base64
    auth_string = f"{SOCRATA_KEY_ID}:{SOCRATA_KEY_SECRET}"
    auth_bytes = auth_string.encode('ascii')
    base64_bytes = base64.b64encode(auth_bytes)
    base64_string = base64_bytes.decode('ascii')
    API_HEADERS['Authorization'] = f'Basic {base64_string}'
    print(f"✅ Using Socrata API authentication (Key ID: {SOCRATA_KEY_ID[:10]}...)")
elif USE_AUTH and SOCRATA_APP_TOKEN:
    # Fallback to app token if no key/secret
    print(f"✅ Using Socrata app token")
else:
    print("ℹ️  Using NYC Open Data API without authentication (free tier)")
    print("   Rate limit: ~1000 requests per 5 minutes")


def parse_bbl(bbl):
    """Parse 10-digit BBL into boro/block/lot components"""
    # BBL format: 1234567890 = boro(1) + block(5) + lot(4)
    boro = bbl[0]
    block = bbl[1:6].lstrip('0') or '0'
    lot = bbl[6:10].lstrip('0') or '0'
    return boro, block, lot


def parse_acris_date(date_str):
    """Convert ACRIS date string to date object"""
    if not date_str:
        return None
    try:
        # ACRIS dates are like "2023-05-15T00:00:00.000"
        return datetime.strptime(date_str[:10], '%Y-%m-%d').date()
    except:
        return None


def get_parties_for_document(document_id):
    """
    Query ACRIS Parties API for buyers, sellers, lenders
    Returns dict with 'buyers', 'sellers', 'lenders' lists
    Each party has: name, address1, address2, city, state, zip, country
    """
    try:
        params = {
            "$where": f"document_id='{document_id}'",
            "$limit": 100
        }
        
        # Add app token if available
        if SOCRATA_APP_TOKEN and not API_HEADERS:
            params['$$app_token'] = SOCRATA_APP_TOKEN
        
        response = requests.get(ACRIS_REAL_PROPERTY_PARTIES, params=params, headers=API_HEADERS, timeout=15)
        if response.status_code != 200:
            return {'buyers': [], 'sellers': [], 'lenders': []}
        
        parties = response.json()
        
        buyers = []
        sellers = []
        lenders = []
        
        for party in parties:
            party_type = party.get('party_type', '').strip()
            
            party_data = {
                'name': party.get('name', '').strip(),
                'address1': party.get('address_1', '').strip(),
                'address2': party.get('address_2', '').strip(),
                'city': party.get('city', '').strip(),
                'state': party.get('state', '').strip(),
                'zip': party.get('zip', '').strip(),
                'country': party.get('country', '').strip()
            }
            
            # Party type: 1=Buyer/Lender, 2=Seller/Borrower, 3=Other
            if party_type == '1':
                buyers.append(party_data)
            elif party_type == '2':
                sellers.append(party_data)
        
        return {
            'buyers': buyers,
            'sellers': sellers,
            'lenders': buyers  # For mortgages, party_type=1 are lenders
        }
        
    except Exception as e:
        print(f"        ⚠️ Error fetching parties: {e}")
        return {'buyers': [], 'sellers': [], 'lenders': []}


def get_acris_full_history(bbl, worker_id=None):
    """
    Query ACRIS for COMPLETE transaction history for a BBL
    Returns list of all transactions with parties
    """
    try:
        boro, block, lot = parse_bbl(bbl)
        worker_prefix = f"[Worker {worker_id}] " if worker_id else ""
        
        # Step 1: Get all legal records for this property
        params = {
            "$where": f"borough='{boro}' AND block='{block}' AND lot='{lot}'",
            "$limit": 500
        }
        
        # Add app token if available
        if SOCRATA_APP_TOKEN and not API_HEADERS:
            params['$$app_token'] = SOCRATA_APP_TOKEN
        
        print(f"      {worker_prefix}Querying ACRIS legals (boro={boro}, block={block}, lot={lot})...")
        response = requests.get(ACRIS_REAL_PROPERTY_LEGALS, params=params, headers=API_HEADERS, timeout=15)
        
        if response.status_code != 200:
            print(f"      {worker_prefix}API returned status {response.status_code}")
            return []
        
        legals = response.json()
        print(f"      {worker_prefix}Found {len(legals)} legal records")
        
        if not legals:
            return []
        
        # Step 2: Get unique document IDs
        doc_ids = list(set([legal['document_id'] for legal in legals if 'document_id' in legal]))
        print(f"      {worker_prefix}Processing {len(doc_ids)} unique documents...")
        
        transactions = []
        
        # Step 3: For each document, get details from master and parties
        for i, doc_id in enumerate(doc_ids, 1):
            try:
                # Get document master record
                params = {
                    "$where": f"document_id='{doc_id}'",
                    "$limit": 1
                }
                
                # Add app token if available
                if SOCRATA_APP_TOKEN and not API_HEADERS:
                    params['$$app_token'] = SOCRATA_APP_TOKEN
                
                response = requests.get(ACRIS_REAL_PROPERTY_MASTER, params=params, headers=API_HEADERS, timeout=10)
                if response.status_code != 200:
                    continue
                
                docs = response.json()
                
                if not docs:
                    continue
                
                doc = docs[0]
                
                # Get document details (using correct API field names)
                doc_type = doc.get('doc_type', '').upper()
                doc_amount = float(doc.get('document_amt', 0) or 0)  # API field is 'document_amt' not 'doc_amount'
                doc_date = parse_acris_date(doc.get('document_date'))  # API field is 'document_date' not 'doc_date'
                recorded_date = parse_acris_date(doc.get('recorded_datetime'))
                crfn = doc.get('crfn', '')
                
                # Parse percent_transferred (can be string like "100.000000")
                percent_trans_str = doc.get('percent_trans', '')
                try:
                    percent_transferred = float(percent_trans_str) if percent_trans_str else None
                except (ValueError, TypeError):
                    percent_transferred = None
                
                # Get parties for this document
                parties = get_parties_for_document(doc_id)
                
                transaction = {
                    'document_id': doc_id,
                    'doc_type': doc_type,
                    'doc_amount': doc_amount,
                    'doc_date': doc_date,
                    'recorded_date': recorded_date,
                    'crfn': crfn,
                    'percent_transferred': percent_transferred,
                    'buyers': parties['buyers'],
                    'sellers': parties['sellers'],
                    'lenders': parties['lenders'] if doc_type == 'MTGE' else []
                }
                
                transactions.append(transaction)
                
                # Show progress every 10 documents
                if i % 10 == 0:
                    print(f"         {worker_prefix}...processed {i}/{len(doc_ids)} documents")
                
                # Small delay to avoid rate limiting
                time.sleep(0.1)
                
            except Exception as e:
                print(f"        {worker_prefix}⚠️ Error processing document {doc_id}: {e}")
                continue
        
        print(f"      {worker_prefix}✅ Retrieved {len(transactions)} complete transactions")
        return transactions
        
    except Exception as e:
        print(f"      {worker_prefix}⚠️ Error fetching ACRIS history: {e}")
        return []


def save_transactions_and_parties(cur, building_id, bbl, transactions):
    """
    Save transactions to acris_transactions and parties to acris_parties
    Returns primary deed and mortgage for buildings table update
    """
    if not transactions:
        return None, None
    
    # First, delete existing records for this building (in case of re-enrichment)
    cur.execute("DELETE FROM acris_parties WHERE building_id = %s", (building_id,))
    cur.execute("DELETE FROM acris_transactions WHERE building_id = %s", (building_id,))
    
    # Find primary deed (most recent) and primary mortgage (most recent)
    # IMPORTANT: Sort by date descending to get the most recent first
    deeds = sorted([t for t in transactions if 'DEED' in t['doc_type']], 
                   key=lambda x: x['doc_date'] or datetime.min.date(), 
                   reverse=True)
    mortgages = sorted([t for t in transactions if t['doc_type'] == 'MTGE'],
                       key=lambda x: x['doc_date'] or datetime.min.date(),
                       reverse=True)
    
    primary_deed = deeds[0] if deeds else None
    primary_mortgage = mortgages[0] if mortgages else None
    
    # Save all transactions
    for transaction in transactions:
        is_primary_deed = (transaction == primary_deed)
        is_primary_mortgage = (transaction == primary_mortgage)
        
        cur.execute("""
            INSERT INTO acris_transactions (
                building_id, bbl, document_id, doc_type, doc_amount,
                doc_date, recorded_date, crfn, percent_transferred,
                is_primary_deed, is_primary_mortgage
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            building_id, bbl, transaction['document_id'], transaction['doc_type'],
            transaction['doc_amount'], transaction['doc_date'], transaction['recorded_date'],
            transaction['crfn'], transaction['percent_transferred'],
            is_primary_deed, is_primary_mortgage
        ))
        
        transaction_id = cur.fetchone()['id']
        
        # Save buyers
        for buyer in transaction['buyers']:
            if buyer['name']:  # Only save if we have a name
                cur.execute("""
                    INSERT INTO acris_parties (
                        building_id, transaction_id, party_type, party_name,
                        address_1, address_2, city, state, zip_code, country
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    building_id, transaction_id, 'buyer', buyer['name'],
                    buyer['address1'], buyer['address2'], buyer['city'],
                    buyer['state'], buyer['zip'], buyer['country']
                ))
        
        # Save sellers
        for seller in transaction['sellers']:
            if seller['name']:
                cur.execute("""
                    INSERT INTO acris_parties (
                        building_id, transaction_id, party_type, party_name,
                        address_1, address_2, city, state, zip_code, country,
                        is_lead
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    building_id, transaction_id, 'seller', seller['name'],
                    seller['address1'], seller['address2'], seller['city'],
                    seller['state'], seller['zip'], seller['country'],
                    True  # Sellers are leads for "previous owners campaign"
                ))
        
        # Save lenders (for mortgages)
        for lender in transaction['lenders']:
            if lender['name']:
                cur.execute("""
                    INSERT INTO acris_parties (
                        building_id, transaction_id, party_type, party_name,
                        address_1, address_2, city, state, zip_code, country
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    building_id, transaction_id, 'lender', lender['name'],
                    lender['address1'], lender['address2'], lender['city'],
                    lender['state'], lender['zip'], lender['country']
                ))
    
    return primary_deed, primary_mortgage


def update_buildings_table(cur, building_id, transactions, primary_deed, primary_mortgage):
    """
    Update buildings table with enhanced ACRIS data
    """
    # Calculate transaction counts
    deed_count = len([t for t in transactions if 'DEED' in t['doc_type']])
    mortgage_count = len([t for t in transactions if t['doc_type'] == 'MTGE'])
    satisfaction_count = len([t for t in transactions if t['doc_type'] in ['SAT', 'SATS']])
    total_count = len(transactions)
    
    # Get primary deed data
    sale_price = None
    sale_date = None
    sale_recorded_date = None
    sale_buyer_primary = None
    sale_seller_primary = None
    sale_percent_transferred = None
    sale_crfn = None
    is_cash_purchase = False
    
    if primary_deed:
        sale_price = primary_deed['doc_amount'] if primary_deed['doc_amount'] > 0 else None
        sale_date = primary_deed['doc_date']
        sale_recorded_date = primary_deed['recorded_date']
        sale_crfn = primary_deed['crfn']
        sale_percent_transferred = primary_deed['percent_transferred']
        
        # Get primary buyer/seller names
        if primary_deed['buyers']:
            sale_buyer_primary = primary_deed['buyers'][0]['name']
        if primary_deed['sellers']:
            sale_seller_primary = primary_deed['sellers'][0]['name']
        
        # Check if cash purchase (no mortgage on same day or within 30 days)
        if sale_date and not primary_mortgage:
            is_cash_purchase = True
        elif sale_date and primary_mortgage and primary_mortgage['doc_date']:
            days_diff = abs((primary_mortgage['doc_date'] - sale_date).days)
            is_cash_purchase = days_diff > 30
    
    # Get primary mortgage data
    mortgage_date = None
    mortgage_lender_primary = None
    mortgage_crfn = None
    mortgage_amount = None
    
    if primary_mortgage:
        mortgage_amount = primary_mortgage['doc_amount'] if primary_mortgage['doc_amount'] > 0 else None
        mortgage_date = primary_mortgage['doc_date']
        mortgage_crfn = primary_mortgage['crfn']
        
        if primary_mortgage['lenders']:
            mortgage_lender_primary = primary_mortgage['lenders'][0]['name']
    
    # Update buildings table
    cur.execute("""
        UPDATE buildings
        SET sale_price = %s,
            sale_date = %s,
            sale_recorded_date = %s,
            sale_buyer_primary = %s,
            sale_seller_primary = %s,
            sale_percent_transferred = %s,
            sale_crfn = %s,
            mortgage_amount = %s,
            mortgage_date = %s,
            mortgage_lender_primary = %s,
            mortgage_crfn = %s,
            is_cash_purchase = %s,
            acris_total_transactions = %s,
            acris_deed_count = %s,
            acris_mortgage_count = %s,
            acris_satisfaction_count = %s,
            acris_last_enriched = CURRENT_TIMESTAMP,
            last_updated = CURRENT_TIMESTAMP
        WHERE id = %s
    """, (
        sale_price, sale_date, sale_recorded_date,
        sale_buyer_primary, sale_seller_primary, sale_percent_transferred, sale_crfn,
        mortgage_amount, mortgage_date, mortgage_lender_primary, mortgage_crfn,
        is_cash_purchase,
        total_count, deed_count, mortgage_count, satisfaction_count,
        building_id
    ))


def enrich_single_building(building, worker_id):
    """
    Worker function to enrich a single building with ACRIS data
    Returns: (status, building_id, result_dict)
    """
    bbl = building['bbl']
    building_id = building['id']
    address = building['address']
    owner = building['current_owner_name'] or 'Unknown'
    
    print(f"   [Worker {worker_id}] 🏢 Starting: {address} (BBL {bbl})")
    sys.stdout.flush()
    
    # Each worker gets its own database connection
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    
    try:
        # Check if ACRIS data has changed (optimization)
        boro, block, lot = parse_bbl(bbl)
        params = {
            "$select": "document_id",
            "$where": f"borough='{boro}' AND block='{block}' AND lot='{lot}'",
            "$limit": 500
        }
        
        # Add app token if available
        if SOCRATA_APP_TOKEN and not API_HEADERS:
            params['$$app_token'] = SOCRATA_APP_TOKEN
        
        response = requests.get(ACRIS_REAL_PROPERTY_LEGALS, params=params, headers=API_HEADERS, timeout=15)
        if response.status_code == 200:
            legals = response.json()
            api_doc_count = len(set([legal['document_id'] for legal in legals if 'document_id' in legal]))
            
            # Check existing transaction count in database
            cur.execute("""
                SELECT COUNT(DISTINCT document_id) as existing_count
                FROM acris_transactions
                WHERE building_id = %s
            """, (building_id,))
            
            result = cur.fetchone()
            existing_count = result['existing_count'] if result else 0
            
            # If counts match, data hasn't changed - skip expensive re-processing
            if existing_count > 0 and api_doc_count == existing_count:
                # Just update the timestamp
                cur.execute("""
                    UPDATE buildings
                    SET acris_last_enriched = CURRENT_TIMESTAMP,
                        last_updated = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (building_id,))
                conn.commit()
                cur.close()
                conn.close()
                
                print(f"   [Worker {worker_id}] ⏭️  Skipped: {address} (unchanged, {existing_count} docs)")
                sys.stdout.flush()
                
                return ('skipped', building_id, {
                    'address': address,
                    'bbl': bbl,
                    'doc_count': existing_count
                })
        
        # Get full ACRIS transaction history
        transactions = get_acris_full_history(bbl, worker_id)
        
        if transactions:
            # Save transactions and parties to database
            primary_deed, primary_mortgage = save_transactions_and_parties(
                cur, building_id, bbl, transactions
            )
            
            # Update buildings table
            update_buildings_table(cur, building_id, transactions, primary_deed, primary_mortgage)
            
            conn.commit()
            
            # Build result summary
            deed_count = len([t for t in transactions if 'DEED' in t['doc_type']])
            mortgage_count = len([t for t in transactions if t['doc_type'] == 'MTGE'])
            party_count = sum(len(t['buyers']) + len(t['sellers']) + len(t['lenders']) for t in transactions)
            
            result_data = {
                'address': address,
                'bbl': bbl,
                'transaction_count': len(transactions),
                'party_count': party_count,
                'deed_count': deed_count,
                'mortgage_count': mortgage_count,
                'primary_deed': primary_deed,
                'primary_mortgage': primary_mortgage
            }
            
            cur.close()
            conn.close()
            
            print(f"   [Worker {worker_id}] ✅ Complete: {address} - {len(transactions)} trans, {party_count} parties")
            sys.stdout.flush()
            
            return ('enriched', building_id, result_data)
        else:
            # No transactions found, but mark as attempted
            cur.execute("""
                UPDATE buildings
                SET acris_last_enriched = CURRENT_TIMESTAMP,
                    last_updated = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (building_id,))
            conn.commit()
            cur.close()
            conn.close()
            
            print(f"   [Worker {worker_id}] ℹ️  No data: {address}")
            sys.stdout.flush()
            
            return ('no_data', building_id, {'address': address, 'bbl': bbl})
    
    except Exception as e:
        # Mark as attempted even on error
        try:
            cur.execute("""
                UPDATE buildings
                SET acris_last_enriched = CURRENT_TIMESTAMP,
                    last_updated = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (building_id,))
            conn.commit()
        except:
            pass
        
        cur.close()
        conn.close()
        
        print(f"   [Worker {worker_id}] ❌ Failed: {address} - {str(e)}")
        sys.stdout.flush()
        
        return ('failed', building_id, {
            'address': address,
            'bbl': bbl,
            'error': str(e)
        })


def enrich_buildings_from_acris():
    """
    Main process with 30-day refresh cycle:
    1. Get buildings without ACRIS data OR last enriched >30 days ago
    2. Query ACRIS for full transaction history
    3. Save to acris_transactions and acris_parties tables
    4. Update buildings table with summary data and counts
    """
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    
    print("Step 3: Enhanced ACRIS Enrichment")
    print("=" * 70)
    
    # Get buildings that need ACRIS enrichment (never enriched OR >30 days old)
    cur.execute("""
        SELECT id, bbl, address, current_owner_name
        FROM buildings
        WHERE bbl IS NOT NULL
        AND (acris_last_enriched IS NULL 
             OR acris_last_enriched < NOW() - INTERVAL '30 days')
        ORDER BY id
    """)
    
    buildings = cur.fetchall()
    total_buildings = len(buildings)
    print(f"\n📊 Found {total_buildings:,} buildings to enrich")
    
    if not buildings:
        print("   ✅ No buildings need enrichment. All up-to-date!")
        cur.close()
        conn.close()
        return
    
    print(f"🚀 Starting ACRIS enrichment with {NUM_WORKERS} parallel workers...")
    print(f"   Processing in batches of {BATCH_SIZE} buildings")
    sys.stdout.flush()
    
    # Split into batches
    batches = [buildings[i:i + BATCH_SIZE] for i in range(0, len(buildings), BATCH_SIZE)]
    total_batches = len(batches)
    
    enriched = 0
    no_data = 0
    failed = 0
    skipped_unchanged = 0
    buildings_processed = 0
    
    # Process each batch with parallel workers
    for batch_num, batch in enumerate(batches, 1):
        print(f"\n{'='*70}")
        print(f"📦 Batch {batch_num}/{total_batches}: Buildings {buildings_processed + 1}-{buildings_processed + len(batch)}")
        print(f"   🔄 {NUM_WORKERS} workers processing in parallel...")
        print(f"{'='*70}")
        sys.stdout.flush()
        
        # Process batch in parallel
        with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
            # Submit all buildings in this batch to workers
            future_to_building = {
                executor.submit(enrich_single_building, building, worker_id): building 
                for worker_id, building in enumerate(batch, 1)
            }
            
            # Collect results as they complete
            batch_results = []
            for future in as_completed(future_to_building):
                try:
                    status, building_id, result_data = future.result()
                    batch_results.append((status, building_id, result_data))
                    
                    # Update counters
                    if status == 'enriched':
                        enriched += 1
                    elif status == 'skipped':
                        skipped_unchanged += 1
                    elif status == 'no_data':
                        no_data += 1
                    elif status == 'failed':
                        failed += 1
                    
                    buildings_processed += 1
                    
                except Exception as e:
                    failed += 1
                    buildings_processed += 1
        
        # Show batch summary
        batch_enriched = sum(1 for s, _, _ in batch_results if s == 'enriched')
        batch_skipped = sum(1 for s, _, _ in batch_results if s == 'skipped')
        batch_no_data = sum(1 for s, _, _ in batch_results if s == 'no_data')
        batch_failed = sum(1 for s, _, _ in batch_results if s == 'failed')
        
        print(f"\n   ✅ Batch complete!")
        print(f"      Enriched: {batch_enriched} | Skipped: {batch_skipped} | No data: {batch_no_data} | Failed: {batch_failed}")
        print(f"      Progress: {buildings_processed}/{total_buildings} ({buildings_processed * 100 // total_buildings}%)")
        sys.stdout.flush()
        
        # Show sample results from this batch if verbose
        if VERBOSE_LOGGING or batch_num <= 3:  # Show first 3 batches always
            for status, building_id, data in batch_results[:3]:  # Show first 3 results
                if status == 'enriched':
                    print(f"      ✅ {data['address']}: {data['transaction_count']} transactions, {data['party_count']} parties")
                    if data['primary_deed'] and data['primary_deed']['doc_amount'] > 0:
                        print(f"         Last sale: ${data['primary_deed']['doc_amount']:,.0f} on {data['primary_deed']['doc_date']}")
                elif status == 'skipped':
                    print(f"      ⏭️  {data['address']}: Unchanged ({data['doc_count']} docs)")
                elif status == 'no_data':
                    print(f"      ℹ️  {data['address']}: No ACRIS data")
                elif status == 'failed':
                    print(f"      ❌ {data['address']}: {data.get('error', 'Unknown error')}")
        
        # Small delay between batches
        if batch_num < total_batches:
            time.sleep(2)
    
    # Close the initial connection
    cur.close()
    conn.close()
    
    # Final summary
    print(f"\n{'='*70}")
    print(f"✅ ACRIS ENRICHMENT COMPLETE!")
    print(f"{'='*70}")
    print(f"   Buildings enriched: {enriched}")
    print(f"   Skipped (unchanged): {skipped_unchanged}")
    print(f"   No data found: {no_data}")
    print(f"   Failed: {failed}")
    print(f"   Total processed: {buildings_processed}/{total_buildings}")
    
    if skipped_unchanged > 0:
        print(f"\n💡 Optimization: Skipped {skipped_unchanged} buildings with unchanged ACRIS data")
        print(f"   (Saved ~{skipped_unchanged * 30} API calls and ~{skipped_unchanged * 0.5:.1f} minutes)")
    
    # Get final statistics from database
    conn_stats = psycopg2.connect(DATABASE_URL)
    cur_stats = conn_stats.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    # Show enrichment statistics
    cur_stats.execute("""
        SELECT 
            COUNT(*) as total_buildings,
            COUNT(sale_date) as with_sales,
            COUNT(CASE WHEN is_cash_purchase THEN 1 END) as cash_purchases,
            COUNT(CASE WHEN acris_deed_count >= 2 THEN 1 END) as multiple_sales,
            SUM(acris_total_transactions) as total_transactions,
            SUM(acris_deed_count) as total_deeds,
            SUM(acris_mortgage_count) as total_mortgages
        FROM buildings
        WHERE acris_last_enriched IS NOT NULL
    """)
    
    stats = cur_stats.fetchone()
    if stats:
        print(f"\n📊 Overall ACRIS Statistics:")
        print(f"   Buildings enriched: {stats['total_buildings']}")
        print(f"   With sale data: {stats['with_sales']}")
        print(f"   Cash purchases: {stats['cash_purchases']}")
        print(f"   Multiple sales: {stats['multiple_sales']}")
        print(f"   Total transactions: {stats['total_transactions']}")
        print(f"   Total deeds: {stats['total_deeds']}")
        print(f"   Total mortgages: {stats['total_mortgages']}")
    
    # Show seller leads count
    cur_stats.execute("""
        SELECT COUNT(*) as seller_leads
        FROM acris_parties
        WHERE party_type = 'seller'
        AND is_lead = TRUE
        AND address_1 IS NOT NULL
    """)
    
    leads = cur_stats.fetchone()
    if leads and leads['seller_leads'] > 0:
        print(f"\n💰 Seller Leads (Previous Owners Campaign):")
        print(f"   {leads['seller_leads']} sellers with addresses available!")
    
    # Show sample enriched buildings
    cur_stats.execute("""
        SELECT b.bbl, b.address, b.sale_date, b.sale_price, 
               b.sale_buyer_primary, b.sale_seller_primary,
               b.is_cash_purchase, b.acris_total_transactions
        FROM buildings b
        WHERE b.acris_last_enriched IS NOT NULL
        AND b.sale_date IS NOT NULL
        ORDER BY b.acris_last_enriched DESC
        LIMIT 5
    """)
    
    samples = cur_stats.fetchall()
    if samples:
        print(f"\n📋 Sample Enriched Buildings:")
        for s in samples:
            print(f"   BBL {s['bbl']}: {s['address']}")
            price = f"${s['sale_price']:,.0f}" if s['sale_price'] else "Unknown"
            cash = " 💵 CASH" if s['is_cash_purchase'] else ""
            print(f"      Sale: {s['sale_date']} for {price}{cash}")
            if s['sale_buyer_primary']:
                print(f"      Buyer: {s['sale_buyer_primary']}")
            if s['sale_seller_primary']:
                print(f"      Seller: {s['sale_seller_primary']}")
            print(f"      Total transactions: {s['acris_total_transactions']}")
    
    cur_stats.close()
    conn_stats.close()
    print(f"\n{'='*70}")




if __name__ == "__main__":
    enrich_buildings_from_acris()
