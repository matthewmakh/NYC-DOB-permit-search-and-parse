#!/usr/bin/env python3
"""
ACRIS Party Search Tool

Search NYC ACRIS records to find all properties associated with a specific
party name (title companies, attorneys, lenders, etc.) over a date range.

Usage Examples:
    # Search for a title company
    python search_acris_parties.py "FIDELITY NATIONAL TITLE"
    
    # Search with custom date range (last 5 years)
    python search_acris_parties.py "CHICAGO TITLE" --years 5
    
    # Search specific date range
    python search_acris_parties.py "STEWART TITLE" --start 2020-01-01 --end 2024-12-31
    
    # Export to CSV
    python search_acris_parties.py "FIRST AMERICAN TITLE" --output results.csv
    
    # Export to JSON
    python search_acris_parties.py "OLD REPUBLIC TITLE" --output results.json
    
    # Search for multiple companies
    python search_acris_parties.py "FIDELITY" "CHICAGO TITLE" "STEWART"
    
    # Filter by transaction type (deeds only)
    python search_acris_parties.py "ACME TITLE" --doc-types DEED,DEEDO
    
    # Filter by borough
    python search_acris_parties.py "ACME TITLE" --borough BROOKLYN
    
    # Fuzzy matching (catches misspellings and variations)
    python search_acris_parties.py "EXCALIBUR" --fuzzy
    # This will also match: EXCELIBUR, EXCALIBUR LLC, EXCALIBURS, etc.
    
    # Show all fuzzy variations being searched
    python search_acris_parties.py "EXCALIBUR" --fuzzy --show-variations

Author: Matthew Makh
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set
from collections import defaultdict

import requests
from dotenv import load_dotenv

# Load environment
load_dotenv()

# =============================================================================
# CONFIGURATION
# =============================================================================

# NYC Open Data ACRIS API endpoints
ACRIS_PARTIES_API = "https://data.cityofnewyork.us/resource/636b-3b5g.json"
ACRIS_LEGALS_API = "https://data.cityofnewyork.us/resource/8h5j-fqxa.json"
ACRIS_MASTER_API = "https://data.cityofnewyork.us/resource/bnx9-e6tj.json"

# Socrata App Token (optional but increases rate limits)
SOCRATA_APP_TOKEN = os.getenv('SOCRATA_APP_TOKEN', os.getenv('NYC_OPEN_DATA_TOKEN', ''))

# API settings
API_BATCH_SIZE = 50000  # Max records per request
REQUEST_DELAY = 0.5     # Seconds between API calls
MAX_RETRIES = 3

# Borough codes
BOROUGH_CODES = {
    'MANHATTAN': '1', 'MN': '1', '1': '1',
    'BRONX': '2', 'BX': '2', '2': '2',
    'BROOKLYN': '3', 'BK': '3', 'KINGS': '3', '3': '3',
    'QUEENS': '4', 'QN': '4', '4': '4',
    'STATEN ISLAND': '5', 'SI': '5', 'RICHMOND': '5', '5': '5',
}

BOROUGH_NAMES = {
    '1': 'Manhattan',
    '2': 'Bronx', 
    '3': 'Brooklyn',
    '4': 'Queens',
    '5': 'Staten Island',
}

# Party type mapping
PARTY_TYPES = {
    '1': 'Buyer/Grantee/Lender',
    '2': 'Seller/Grantor/Borrower',
    '3': 'Other (Title Co/Attorney/etc)',
}

# Common document types
DOC_TYPE_NAMES = {
    'DEED': 'Deed',
    'DEEDO': 'Deed, Other',
    'MTGE': 'Mortgage',
    'ASST': 'Assignment',
    'SAT': 'Satisfaction',
    'AGMT': 'Agreement',
    'RPTT': 'Real Property Transfer Tax',
    'AL&R': 'Assignment of Leases & Rents',
    'UCC1': 'UCC Financing Statement',
    'UCC3': 'UCC Amendment',
}

# Common company suffixes to strip for fuzzy matching
COMPANY_SUFFIXES = [
    ' LLC', ' L.L.C.', ' L.L.C', ' INC', ' INC.', ' INCORPORATED',
    ' CORP', ' CORP.', ' CORPORATION', ' LTD', ' LTD.', ' LIMITED',
    ' LP', ' L.P.', ' LLP', ' L.L.P.', ' COMPANY', ' CO', ' CO.',
    ' TITLE', ' TITLE COMPANY', ' TITLE CO', ' ABSTRACT', ' AGENCY',
    ' GROUP', ' HOLDINGS', ' ENTERPRISES', ' SERVICES', ' PROPERTIES',
]

# Common letter substitutions for fuzzy matching
FUZZY_SUBSTITUTIONS = [
    ('I', 'E'),   # excalibur vs excelibur
    ('E', 'I'),
    ('A', 'E'),
    ('E', 'A'),
    ('O', 'A'),
    ('A', 'O'),
    ('C', 'K'),
    ('K', 'C'),
    ('S', 'Z'),
    ('Z', 'S'),
    ('PH', 'F'),
    ('F', 'PH'),
    ('Y', 'I'),
    ('I', 'Y'),
    ('OU', 'O'),
    ('O', 'OU'),
    ('EI', 'IE'),
    ('IE', 'EI'),
]


# =============================================================================
# FUZZY MATCHING FUNCTIONS
# =============================================================================

def normalize_company_name(name: str) -> str:
    """Strip common suffixes and normalize a company name."""
    name = name.upper().strip()
    
    # Remove common suffixes
    for suffix in sorted(COMPANY_SUFFIXES, key=len, reverse=True):
        if name.endswith(suffix.upper()):
            name = name[:-len(suffix)].strip()
            break
    
    # Remove extra whitespace
    name = ' '.join(name.split())
    
    return name


def generate_fuzzy_variations(term: str, max_variations: int = 20) -> List[str]:
    """
    Generate spelling variations of a search term for fuzzy matching.
    
    Handles:
    - Common misspellings (excalibur vs excelibur)
    - With/without suffixes (EXCALIBUR vs EXCALIBUR LLC)
    - Plurals (EXCALIBUR vs EXCALIBURS)
    """
    term = term.upper().strip()
    variations = {term}
    
    # Normalize the term (strip suffixes)
    base_term = normalize_company_name(term)
    variations.add(base_term)
    
    # Add plural/singular variations
    if base_term.endswith('S'):
        variations.add(base_term[:-1])  # Remove S
    else:
        variations.add(base_term + 'S')  # Add S
    
    # Generate letter substitution variations
    for old, new in FUZZY_SUBSTITUTIONS:
        if old in base_term:
            # Only generate a few substitution variations to avoid explosion
            variation = base_term.replace(old, new, 1)
            variations.add(variation)
            if len(variations) >= max_variations:
                break
    
    # Add double letter variations (excalibur -> excallibur)
    for i, char in enumerate(base_term):
        if char.isalpha():
            doubled = base_term[:i] + char + base_term[i:]
            variations.add(doubled)
            if len(variations) >= max_variations:
                break
    
    # Add missing letter variations (excalibur -> excaibur)
    for i, char in enumerate(base_term):
        if char.isalpha() and i > 0:
            missing = base_term[:i] + base_term[i+1:]
            if len(missing) >= 3:  # Don't make too short
                variations.add(missing)
            if len(variations) >= max_variations:
                break
    
    return list(variations)[:max_variations]


def build_fuzzy_where_clause(term: str, fuzzy: bool = False) -> str:
    """
    Build a WHERE clause for searching party names.
    
    ACRIS data is typically uppercase, so we use upper() for case-insensitive matching.
    """
    term_upper = term.upper().strip()
    
    if not fuzzy:
        # Simple case-insensitive search using upper()
        return f"upper(name) LIKE '%{term_upper}%'"
    
    # Generate spelling variations for fuzzy matching
    variations = generate_fuzzy_variations(term)
    
    # Limit variations to avoid slow queries
    limited_variations = [v.upper() for v in variations[:8]]
    
    # Build OR clause
    like_clauses = [f"upper(name) LIKE '%{v}%'" for v in limited_variations]
    
    return f"({' OR '.join(like_clauses)})"


# =============================================================================
# API FUNCTIONS
# =============================================================================

def get_api_headers():
    """Get headers with optional app token."""
    headers = {'Accept': 'application/json'}
    if SOCRATA_APP_TOKEN:
        headers['X-App-Token'] = SOCRATA_APP_TOKEN
    return headers


def api_request(url: str, params: dict, retries: int = MAX_RETRIES) -> List[dict]:
    """Make API request with retries and rate limiting."""
    headers = get_api_headers()
    
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=60)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                # Rate limited - wait and retry
                wait_time = (attempt + 1) * 5
                print(f"   ⏳ Rate limited, waiting {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"   ⚠️ API error {response.status_code}: {response.text[:200]}")
                
        except requests.exceptions.Timeout:
            print(f"   ⏳ Timeout, retrying ({attempt + 1}/{retries})...")
        except Exception as e:
            print(f"   ⚠️ Request error: {e}")
        
        time.sleep(REQUEST_DELAY * (attempt + 1))
    
    return []


def search_parties(
    search_terms: List[str],
    start_date: str,
    end_date: str,
    doc_types: Optional[List[str]] = None,
    borough: Optional[str] = None,
    party_type: Optional[str] = None,
    fuzzy: bool = False,
) -> List[dict]:
    """
    Search ACRIS Parties for matching names.
    
    If fuzzy=True, also searches for common spelling variations.
    
    Returns list of matching party records with document_id.
    """
    all_results = []
    
    for term in search_terms:
        if fuzzy:
            variations = generate_fuzzy_variations(term)
            print(f"\n🔍 Searching for: '{term}' (fuzzy mode)")
            print(f"   📝 Variations: {', '.join(variations[:5])}{'...' if len(variations) > 5 else ''}")
        else:
            print(f"\n🔍 Searching for: '{term}'")
        
        # Build query - case variations for search
        name_clause = build_fuzzy_where_clause(term, fuzzy=fuzzy)
        where_clauses = [name_clause]
        
        # NOTE: Parties table doesn't have transaction dates!
        # good_through_date is just a data freshness indicator.
        # Date filtering will be done when we get transaction details from Master table.
        
        # Add party type filter
        if party_type:
            where_clauses.append(f"party_type = '{party_type}'")  
        
        where_clause = " AND ".join(where_clauses)
        
        offset = 0
        term_results = []
        
        while True:
            params = {
                "$where": where_clause,
                "$limit": API_BATCH_SIZE,
                "$offset": offset,
            }
            
            print(f"   📥 Fetching records {offset:,} - {offset + API_BATCH_SIZE:,}...")
            
            batch = api_request(ACRIS_PARTIES_API, params)
            
            if not batch:
                break
            
            term_results.extend(batch)
            print(f"   ✅ Got {len(batch):,} records (total: {len(term_results):,})")
            
            if len(batch) < API_BATCH_SIZE:
                break  # No more pages
            
            offset += API_BATCH_SIZE
            time.sleep(REQUEST_DELAY)
        
        all_results.extend(term_results)
        print(f"   📊 Found {len(term_results):,} matches for '{term}'")
    
    return all_results


def get_property_details(document_ids: Set[str]) -> Dict[str, dict]:
    """
    Get property details (BBL, address) for a set of document IDs.
    Returns dict mapping document_id -> property info.
    """
    property_map = {}
    doc_list = list(document_ids)
    
    print(f"\n📍 Fetching property details for {len(doc_list):,} documents...")
    
    # Process in batches since we can't query 50k document_ids at once
    batch_size = 100
    
    for i in range(0, len(doc_list), batch_size):
        batch = doc_list[i:i + batch_size]
        
        # Build IN clause
        doc_ids_str = ",".join([f"'{d}'" for d in batch])
        
        params = {
            "$where": f"document_id IN ({doc_ids_str})",
            "$limit": 5000,
        }
        
        results = api_request(ACRIS_LEGALS_API, params)
        
        for record in results:
            doc_id = record.get('document_id')
            if doc_id:
                borough = record.get('borough', '')
                block = record.get('block', '').zfill(5)
                lot = record.get('lot', '').zfill(4)
                
                property_map[doc_id] = {
                    'borough': borough,
                    'borough_name': BOROUGH_NAMES.get(borough, borough),
                    'block': block,
                    'lot': lot,
                    'bbl': f"{borough}{block}{lot}" if borough else '',
                    'street_number': record.get('street_number', ''),
                    'street_name': record.get('street_name', ''),
                    'unit': record.get('unit', ''),
                }
        
        if i % 500 == 0 and i > 0:
            print(f"   📥 Processed {i:,}/{len(doc_list):,} documents...")
        
        time.sleep(REQUEST_DELAY / 2)
    
    print(f"   ✅ Got property info for {len(property_map):,} documents")
    return property_map


def get_transaction_details(document_ids: Set[str]) -> Dict[str, dict]:
    """
    Get transaction details (doc type, amount, date) for document IDs.
    """
    transaction_map = {}
    doc_list = list(document_ids)
    
    print(f"\n📜 Fetching transaction details for {len(doc_list):,} documents...")
    
    batch_size = 100
    
    for i in range(0, len(doc_list), batch_size):
        batch = doc_list[i:i + batch_size]
        doc_ids_str = ",".join([f"'{d}'" for d in batch])
        
        params = {
            "$where": f"document_id IN ({doc_ids_str})",
            "$limit": 5000,
        }
        
        results = api_request(ACRIS_MASTER_API, params)
        
        for record in results:
            doc_id = record.get('document_id')
            if doc_id:
                transaction_map[doc_id] = {
                    'doc_type': record.get('doc_type', ''),
                    'doc_type_name': DOC_TYPE_NAMES.get(record.get('doc_type', ''), record.get('doc_type', '')),
                    'doc_amount': record.get('doc_amount', ''),
                    'doc_date': record.get('doc_date', '')[:10] if record.get('doc_date') else '',
                    'recorded_date': record.get('recorded_datetime', '')[:10] if record.get('recorded_datetime') else '',
                    'crfn': record.get('crfn', ''),
                }
        
        if i % 500 == 0 and i > 0:
            print(f"   📥 Processed {i:,}/{len(doc_list):,} documents...")
        
        time.sleep(REQUEST_DELAY / 2)
    
    print(f"   ✅ Got transaction info for {len(transaction_map):,} documents")
    return transaction_map


# =============================================================================
# OUTPUT FUNCTIONS
# =============================================================================

def format_address(prop: dict) -> str:
    """Format property address from components."""
    parts = []
    if prop.get('street_number'):
        parts.append(prop['street_number'])
    if prop.get('street_name'):
        parts.append(prop['street_name'])
    if prop.get('unit'):
        parts.append(f"Unit {prop['unit']}")
    
    address = ' '.join(parts)
    if prop.get('borough_name'):
        address += f", {prop['borough_name']}"
    
    return address.strip() or 'Unknown'


def build_results(
    party_records: List[dict],
    property_map: Dict[str, dict],
    transaction_map: Dict[str, dict],
    doc_types: Optional[List[str]] = None,
    borough: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[dict]:
    """
    Combine party, property, and transaction data into final results.
    Apply any additional filters including date range.
    """
    results = []
    
    # Normalize borough filter
    borough_code = None
    if borough:
        borough_code = BOROUGH_CODES.get(borough.upper())
    
    for party in party_records:
        doc_id = party.get('document_id')
        if not doc_id:
            continue
        
        prop = property_map.get(doc_id, {})
        txn = transaction_map.get(doc_id, {})
        
        # Apply borough filter
        if borough_code and prop.get('borough') != borough_code:
            continue
        
        # Apply doc type filter
        if doc_types and txn.get('doc_type') not in doc_types:
            continue
        
        # Apply date filter (using recorded_date - more reliable than doc_date)
        txn_date = txn.get('recorded_date', '') or txn.get('doc_date', '')
        if start_date and txn_date and txn_date < start_date:
            continue
        if end_date and txn_date and txn_date > end_date:
            continue
        
        result = {
            # Property info
            'bbl': prop.get('bbl', ''),
            'borough': prop.get('borough_name', ''),
            'address': format_address(prop),
            'block': prop.get('block', ''),
            'lot': prop.get('lot', ''),
            
            # Transaction info
            'document_id': doc_id,
            'doc_type': txn.get('doc_type', ''),
            'doc_type_name': txn.get('doc_type_name', ''),
            'doc_amount': txn.get('doc_amount', ''),
            'doc_date': txn.get('doc_date', ''),
            'recorded_date': txn.get('recorded_date', ''),
            'crfn': txn.get('crfn', ''),
            
            # Party info
            'party_name': party.get('name', ''),
            'party_type': PARTY_TYPES.get(party.get('party_type', ''), party.get('party_type', '')),
            'party_address': party.get('address_1', ''),
            'party_city': party.get('city', ''),
            'party_state': party.get('state', ''),
            'party_zip': party.get('zip', ''),
        }
        
        results.append(result)
    
    return results


def save_csv(results: List[dict], filepath: str):
    """Save results to CSV file."""
    if not results:
        print("   ⚠️ No results to save")
        return
    
    fieldnames = list(results[0].keys())
    
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    
    print(f"   💾 Saved {len(results):,} records to {filepath}")


def save_json(results: List[dict], filepath: str):
    """Save results to JSON file."""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, default=str)
    
    print(f"   💾 Saved {len(results):,} records to {filepath}")


def find_linked_closings(
    cert_results: List[dict],
    property_map: Dict[str, dict],
    transaction_map: Dict[str, dict],
    window_days: int = 30,
) -> List[dict]:
    """
    For CERT filings, find the actual DEED/MTGE closings on the same property.
    
    Title companies file CERTs around the time of closing. This function
    searches for DEED/MTGE documents on the same BBL within the time window.
    """
    # Filter to only CERT records that have BBL info
    cert_bbls = {}
    for r in cert_results:
        doc_id = r.get('document_id')
        prop = property_map.get(doc_id, {})
        txn = transaction_map.get(doc_id, {})
        
        bbl = prop.get('bbl')
        if not bbl or txn.get('doc_type') != 'CERT':
            continue
        
        recorded_date = txn.get('recorded_date', '') or txn.get('doc_date', '')
        if not recorded_date:
            continue
        
        if bbl not in cert_bbls:
            cert_bbls[bbl] = []
        cert_bbls[bbl].append({
            'cert_doc_id': doc_id,
            'cert_date': recorded_date,
            'party_name': r.get('name', ''),
            'borough': prop.get('borough', ''),
            'block': prop.get('block', ''),
            'lot': prop.get('lot', ''),
        })
    
    if not cert_bbls:
        print("\n⚠️ No CERT filings with valid BBLs found to link")
        return []
    
    print(f"\n🔗 Finding linked closings for {len(cert_bbls):,} properties...")
    
    linked_results = []
    processed = 0
    
    for bbl, cert_info_list in cert_bbls.items():
        borough = cert_info_list[0]['borough']
        block = cert_info_list[0]['block']
        lot = cert_info_list[0]['lot']
        
        # Get the earliest and latest CERT dates for this BBL
        cert_dates = [c['cert_date'] for c in cert_info_list]
        earliest_cert = min(cert_dates)
        latest_cert = max(cert_dates)
        
        # Calculate search window
        try:
            start_dt = datetime.strptime(earliest_cert, '%Y-%m-%d') - timedelta(days=window_days)
            end_dt = datetime.strptime(latest_cert, '%Y-%m-%d') + timedelta(days=window_days)
            search_start = start_dt.strftime('%Y-%m-%d')
            search_end = end_dt.strftime('%Y-%m-%d')
        except:
            continue
        
        # Search ACRIS Master for DEED/MTGE on this BBL in the window
        # First get document IDs from Legals for this BBL
        params = {
            "$where": f"borough = '{borough}' AND block = '{block}' AND lot = '{lot}'",
            "$limit": 1000,
        }
        
        legal_results = api_request(ACRIS_LEGALS_API, params)
        if not legal_results:
            continue
        
        doc_ids = list(set(r.get('document_id') for r in legal_results if r.get('document_id')))
        
        if not doc_ids:
            continue
        
        # Get transaction details for these docs
        batch_size = 100
        for i in range(0, len(doc_ids), batch_size):
            batch = doc_ids[i:i + batch_size]
            doc_ids_str = ",".join([f"'{d}'" for d in batch])
            
            params = {
                "$where": f"document_id IN ({doc_ids_str}) AND doc_type IN ('DEED', 'DEEDO', 'MTGE', 'ASST') AND recorded_datetime >= '{search_start}' AND recorded_datetime <= '{search_end}'",
                "$limit": 500,
            }
            
            master_results = api_request(ACRIS_MASTER_API, params)
            
            for txn in master_results:
                # Get parties for this document to show buyer/seller
                parties_params = {
                    "$where": f"document_id = '{txn.get('document_id')}'",
                    "$limit": 20,
                }
                parties = api_request(ACRIS_PARTIES_API, parties_params)
                
                buyer = next((p.get('name', '') for p in parties if p.get('party_type') == '1'), '')
                seller = next((p.get('name', '') for p in parties if p.get('party_type') == '2'), '')
                
                # Get address from legals
                address_info = next((r for r in legal_results if r.get('document_id') == txn.get('document_id')), {})
                address = format_address({
                    'street_number': address_info.get('street_number', ''),
                    'street_name': address_info.get('street_name', ''),
                    'borough_name': BOROUGH_NAMES.get(borough, borough),
                })
                
                linked_results.append({
                    'bbl': bbl,
                    'borough': BOROUGH_NAMES.get(borough, borough),
                    'address': address,
                    'block': block,
                    'lot': lot,
                    'document_id': txn.get('document_id', ''),
                    'doc_type': txn.get('doc_type', ''),
                    'doc_type_name': DOC_TYPE_NAMES.get(txn.get('doc_type', ''), txn.get('doc_type', '')),
                    'doc_amount': txn.get('doc_amount', ''),
                    'doc_date': txn.get('doc_date', '')[:10] if txn.get('doc_date') else '',
                    'recorded_date': txn.get('recorded_datetime', '')[:10] if txn.get('recorded_datetime') else '',
                    'crfn': txn.get('crfn', ''),
                    'buyer': buyer,
                    'seller': seller,
                    'title_company': cert_info_list[0]['party_name'],
                    'cert_date': cert_info_list[0]['cert_date'],
                    'linked_from': 'CERT',
                })
            
            time.sleep(REQUEST_DELAY / 2)
        
        processed += 1
        if processed % 10 == 0:
            print(f"   📥 Processed {processed:,}/{len(cert_bbls):,} properties...")
    
    print(f"   ✅ Found {len(linked_results):,} linked closings")
    return linked_results


def print_summary(results: List[dict], search_terms: List[str], linked_mode: bool = False):
    """Print a summary of the search results."""
    if not results:
        print("\n❌ No matching records found")
        return
    
    print("\n" + "=" * 70)
    print("📊 SEARCH RESULTS SUMMARY")
    print("=" * 70)
    
    print(f"\n🔍 Search terms: {', '.join(search_terms)}")
    print(f"📝 Total records: {len(results):,}")
    
    # Unique properties
    unique_bbls = set(r['bbl'] for r in results if r['bbl'])
    print(f"🏠 Unique properties: {len(unique_bbls):,}")
    
    # By borough
    by_borough = defaultdict(int)
    for r in results:
        by_borough[r['borough'] or 'Unknown'] += 1
    
    print("\n📍 By Borough:")
    for borough, count in sorted(by_borough.items(), key=lambda x: -x[1]):
        print(f"   {borough}: {count:,}")
    
    # By doc type
    by_doc_type = defaultdict(int)
    for r in results:
        by_doc_type[r['doc_type_name'] or r['doc_type'] or 'Unknown'] += 1
    
    print("\n📜 By Document Type:")
    for doc_type, count in sorted(by_doc_type.items(), key=lambda x: -x[1])[:10]:
        print(f"   {doc_type}: {count:,}")
    
    # By year
    by_year = defaultdict(int)
    for r in results:
        year = r['doc_date'][:4] if r['doc_date'] else 'Unknown'
        by_year[year] += 1
    
    print("\n📅 By Year:")
    for year, count in sorted(by_year.items(), reverse=True)[:8]:
        print(f"   {year}: {count:,}")
    
    # Sample records
    print("\n📋 Sample Records (first 5):")
    for r in results[:5]:
        if linked_mode:
            amount = f"${int(float(r['doc_amount'])):,}" if r.get('doc_amount') else '$0'
            buyer = r.get('buyer', '')[:30] or 'N/A'
            seller = r.get('seller', '')[:30] or 'N/A'
            print(f"   • {r['address']} | {r['doc_type']} | {r['doc_date']} | {amount}")
            print(f"     Buyer: {buyer} | Seller: {seller}")
        else:
            print(f"   • {r['address']} | {r['doc_type']} | {r['doc_date']} | ${r['doc_amount'] or '0'}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Search ACRIS records for parties (title companies, attorneys, lenders, etc.)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python search_acris_parties.py "FIDELITY NATIONAL TITLE"
  python search_acris_parties.py "CHICAGO TITLE" --years 5 --output results.csv
  python search_acris_parties.py "STEWART" "FIRST AMERICAN" --borough BROOKLYN
  python search_acris_parties.py "ACME TITLE" --doc-types DEED,MTGE --start 2020-01-01
  
Fuzzy Matching:
  python search_acris_parties.py "EXCALIBUR" --fuzzy
    # Catches: EXCELIBUR, EXCALIBUR LLC, EXCALIBURS, EXCALABER, etc.  python search_acris_parties.py "EXCALIBUR" --fuzzy --show-variations
    # Shows all spelling variations being searched

Link Closings (find actual DEED/MTGE for CERT filings):
  python search_acris_parties.py "FIDELITY NATIONAL TITLE" --link-closings
    # Finds DEED/MTGE transactions on same property where title company filed a CERT
  python search_acris_parties.py "FIDELITY" --link-closings --link-window 60
    # Searches 60 days before/after CERT for linked closings  python search_acris_parties.py "EXCALIBUR" --fuzzy --show-variations
    # Shows all spelling variations being searched
        """
    )
    
    parser.add_argument(
        'search_terms',
        nargs='+',
        help='Party name(s) to search for (case-insensitive, partial match)'
    )
    
    parser.add_argument(
        '--years', '-y',
        type=int,
        default=8,
        help='Search last N years (default: 8)'
    )
    
    parser.add_argument(
        '--start', '-s',
        type=str,
        help='Start date (YYYY-MM-DD). Overrides --years'
    )
    
    parser.add_argument(
        '--end', '-e',
        type=str,
        help='End date (YYYY-MM-DD). Default: today'
    )
    
    parser.add_argument(
        '--output', '-o',
        type=str,
        help='Output file path (.csv or .json)'
    )
    
    parser.add_argument(
        '--doc-types', '-d',
        type=str,
        help='Filter by document types (comma-separated, e.g., DEED,MTGE)'
    )
    
    parser.add_argument(
        '--borough', '-b',
        type=str,
        choices=['MANHATTAN', 'BRONX', 'BROOKLYN', 'QUEENS', 'STATEN ISLAND', 'MN', 'BX', 'BK', 'QN', 'SI'],
        help='Filter by borough'
    )
    
    parser.add_argument(
        '--party-type', '-p',
        type=str,
        choices=['1', '2', '3'],
        help='Filter by party type: 1=Buyer/Lender, 2=Seller/Borrower, 3=Other'
    )
    
    parser.add_argument(
        '--no-details',
        action='store_true',
        help='Skip fetching property/transaction details (faster but less info)'
    )
    
    parser.add_argument(
        '--fuzzy', '-f',
        action='store_true',
        help='Enable fuzzy matching (catches misspellings like excalibur/excelibur, variations like "EXCALIBUR LLC")'
    )
    
    parser.add_argument(
        '--show-variations',
        action='store_true',
        help='Show all fuzzy variations being searched (for debugging)'
    )
    
    parser.add_argument(
        '--link-closings', '-l',
        action='store_true',
        help='For CERT filings, find linked DEED/MTGE closings on same property (slower but more useful)'
    )
    
    parser.add_argument(
        '--link-window',
        type=int,
        default=30,
        help='Days before/after CERT to search for linked closings (default: 30)'
    )
    
    args = parser.parse_args()
    
    # Calculate date range
    end_date = args.end or datetime.now().strftime('%Y-%m-%d')
    
    if args.start:
        start_date = args.start
    else:
        start_date = (datetime.now() - timedelta(days=365 * args.years)).strftime('%Y-%m-%d')
    
    # Parse doc types
    doc_types = None
    if args.doc_types:
        doc_types = [d.strip().upper() for d in args.doc_types.split(',')]
    
    print("=" * 70)
    print("🔍 ACRIS PARTY SEARCH")
    print("=" * 70)
    print(f"   Search terms: {', '.join(args.search_terms)}")
    print(f"   Date range: {start_date} to {end_date}")
    if args.fuzzy:
        print(f"   Fuzzy matching: ENABLED")
    if args.link_closings:
        print(f"   Link closings: ENABLED (window: ±{args.link_window} days)")
    if doc_types:
        print(f"   Doc types: {', '.join(doc_types)}")
    if args.borough:
        print(f"   Borough: {args.borough}")
    if args.party_type:
        print(f"   Party type: {PARTY_TYPES.get(args.party_type, args.party_type)}")
    
    # Show all variations if requested
    if args.show_variations:
        for term in args.search_terms:
            variations = generate_fuzzy_variations(term)
            print(f"\n   📝 Variations for '{term}':")
            for v in variations:
                print(f"      - {v}")
    
    # Step 1: Search for party records
    party_records = search_parties(
        search_terms=args.search_terms,
        start_date=start_date,
        end_date=end_date,
        party_type=args.party_type,
        fuzzy=args.fuzzy,
    )
    
    if not party_records:
        print("\n❌ No matching party records found")
        return
    
    print(f"\n✅ Found {len(party_records):,} party records")
    
    # Step 2: Get unique document IDs
    document_ids = set(r['document_id'] for r in party_records if r.get('document_id'))
    print(f"   📄 Unique documents: {len(document_ids):,}")
    
    # Step 3: Get property and transaction details (unless skipped)
    if args.no_details:
        property_map = {}
        transaction_map = {}
    else:
        property_map = get_property_details(document_ids)
        transaction_map = get_transaction_details(document_ids)
    
    # Step 4: Build final results
    results = build_results(
        party_records=party_records,
        property_map=property_map,
        transaction_map=transaction_map,
        doc_types=doc_types,
        borough=args.borough,
        start_date=start_date,
        end_date=end_date,
    )
    
    # Step 4b: If link-closings enabled, find DEED/MTGE for CERT filings
    linked_results = []
    if args.link_closings and not args.no_details:
        linked_results = find_linked_closings(
            cert_results=party_records,
            property_map=property_map,
            transaction_map=transaction_map,
            window_days=args.link_window,
        )
    
    # Step 5: Output results
    if args.link_closings and linked_results:
        print_summary(linked_results, args.search_terms, linked_mode=True)
        final_results = linked_results
    else:
        print_summary(results, args.search_terms, linked_mode=False)
        final_results = results
    
    if args.output:
        if args.output.endswith('.json'):
            save_json(final_results, args.output)
        else:
            save_csv(final_results, args.output)
    
    print("\n✅ Done!")


if __name__ == '__main__':
    main()
