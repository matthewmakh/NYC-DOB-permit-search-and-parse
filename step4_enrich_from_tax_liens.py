#!/usr/bin/env python3
"""
Step 4: Tax Delinquency & Liens Enrichment

Enriches buildings with:
- Tax delinquency status from NYC DOF
- ECB violations with financial balances (can become liens)
- DOB building violations

Data Sources:
- NYC DOF Property Tax Delinquencies (9rz4-mjek)
- NYC ECB Violations (6bgk-3dad) - includes penalty_imposed and balance_due
- NYC DOB Violations (3h2n-5cm9)

Updates buildings table fields:
- has_tax_delinquency, tax_delinquency_count, tax_delinquency_water_only
- ecb_violation_count, ecb_total_balance, ecb_open_violations
- dob_violation_count, dob_open_violations
- tax_lien_last_checked
"""

import psycopg2
import psycopg2.extras
import os
import requests
import time
from datetime import datetime
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# Load .env from dashboard_html subdirectory
load_dotenv('dashboard_html/.env')

# Support both DATABASE_URL and individual DB_* variables
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME')
    
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        raise ValueError("Either DATABASE_URL or DB_HOST/DB_USER/DB_PASSWORD/DB_NAME must be set")
    
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# NYC Open Data API endpoints
TAX_DELINQUENCY_API = "https://data.cityofnewyork.us/resource/9rz4-mjek.json"
ECB_VIOLATIONS_API = "https://data.cityofnewyork.us/resource/6bgk-3dad.json"
DOB_VIOLATIONS_API = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"

# Configuration
API_DELAY = float(os.getenv('API_DELAY', '0.1'))  # Reduced since we're parallelizing
BUILDING_BATCH_SIZE = int(os.getenv('BUILDING_BATCH_SIZE', '500'))
MAX_WORKERS = int(os.getenv('MAX_WORKERS', '10'))  # Parallel threads

# Thread-safe counter for progress tracking
progress_lock = Lock()
stats_lock = Lock()


def parse_bbl(bbl):
    """Parse 10-digit BBL into components"""
    boro = bbl[0]
    block_padded = bbl[1:6]  # Keep leading zeros for ECB/DOB APIs
    lot_padded = bbl[6:10]   # Keep leading zeros
    block = str(int(bbl[1:6]))  # Remove leading zeros for tax API
    lot = str(int(bbl[6:10]))
    return boro, block, lot, block_padded, lot_padded


def get_tax_delinquency_data(bbl):
    """
    Check if property is on tax delinquency list
    Returns (data_dict, error_message) tuple
    """
    try:
        boro, block, lot, _, _ = parse_bbl(bbl)
        
        params = {
            "$where": f"borough='{boro}' AND block='{block}' AND lot='{lot}'",
            "$limit": 100  # Get all notices
        }
        
        response = requests.get(TAX_DELINQUENCY_API, params=params, timeout=10)
        response.raise_for_status()
        time.sleep(API_DELAY)
        
        data = response.json()
        
        if not data:
            return {
                'has_tax_delinquency': False,
                'tax_delinquency_count': 0,
                'tax_delinquency_water_only': False
            }, None
        
        # Check if any notice is NOT water-only
        has_non_water = any(
            record.get('water_debt_only', 'NO').upper() == 'NO' 
            for record in data
        )
        
        result = {
            'has_tax_delinquency': True,
            'tax_delinquency_count': len(data),
            'tax_delinquency_water_only': not has_non_water  # TRUE only if ALL are water
        }
        return result, None
        
    except Exception as e:
        return None, f"Tax delinquency API error: {str(e)}"


def get_ecb_violations_data(bbl):
    """
    Get ECB violations with financial data and respondent info
    Returns (data_dict, error_message) tuple
    """
    try:
        boro, _, _, block_padded, lot_padded = parse_bbl(bbl)
        
        params = {
            "$where": f"boro='{boro}' AND block='{block_padded}' AND lot='{lot_padded}'",
            "$order": "issue_date DESC",  # Most recent first
            "$limit": 500  # Get many violations
        }
        
        response = requests.get(ECB_VIOLATIONS_API, params=params, timeout=10)
        response.raise_for_status()
        time.sleep(API_DELAY)
        
        data = response.json()
        
        if not data:
            return {
                'ecb_violation_count': 0,
                'ecb_total_balance': 0,
                'ecb_open_violations': 0,
                'ecb_total_penalty': 0,
                'ecb_amount_paid': 0,
                'ecb_most_recent_hearing_date': None,
                'ecb_most_recent_hearing_status': None,
                'ecb_respondent_name': None,
                'ecb_respondent_address': None,
                'ecb_respondent_city': None,
                'ecb_respondent_zip': None
            }, None
        
        total_balance = 0
        total_penalty = 0
        total_paid = 0
        open_violations = 0
        most_recent_hearing_date = None
        most_recent_hearing_status = None
        respondent_name = None
        respondent_address = None
        respondent_city = None
        respondent_zip = None
        
        for i, record in enumerate(data):
            balance = float(record.get('balance_due', 0) or 0)
            penalty = float(record.get('penality_imposed', 0) or 0)
            paid = float(record.get('amount_paid', 0) or 0)
            
            total_balance += balance
            total_penalty += penalty
            total_paid += paid
            
            # Count as open if has balance or status is ACTIVE
            status = record.get('ecb_violation_status', '').upper()
            if balance > 0 or status == 'ACTIVE':
                open_violations += 1
            
            # Capture most recent hearing info (first record since ordered by date DESC)
            if i == 0:
                hearing_date = record.get('hearing_date')
                if hearing_date and len(hearing_date) >= 8:
                    # Parse YYYYMMDD format
                    try:
                        from datetime import datetime
                        most_recent_hearing_date = datetime.strptime(hearing_date[:8], '%Y%m%d').date()
                    except:
                        pass
                
                most_recent_hearing_status = record.get('hearing_status')
                
                # Capture respondent info (owner/manager)
                respondent_name = record.get('respondent_name')
                house_num = record.get('respondent_house_number', '')
                street = record.get('respondent_street', '')
                respondent_address = f"{house_num} {street}".strip() if house_num or street else None
                respondent_city = record.get('respondent_city')
                respondent_zip = record.get('respondent_zip')
        
        result = {
            'ecb_violation_count': len(data),
            'ecb_total_balance': round(total_balance, 2),
            'ecb_open_violations': open_violations,
            'ecb_total_penalty': round(total_penalty, 2),
            'ecb_amount_paid': round(total_paid, 2),
            'ecb_most_recent_hearing_date': most_recent_hearing_date,
            'ecb_most_recent_hearing_status': most_recent_hearing_status,
            'ecb_respondent_name': respondent_name,
            'ecb_respondent_address': respondent_address,
            'ecb_respondent_city': respondent_city,
            'ecb_respondent_zip': respondent_zip
        }
        return result, None
        
    except Exception as e:
        return None, f"ECB violations API error: {str(e)}"


def get_dob_violations_data(bbl):
    """
    Get DOB violations count
    Returns (data_dict, error_message) tuple
    """
    try:
        boro, block, lot, _, _ = parse_bbl(bbl)
        
        params = {
            "$where": f"boro='{boro}' AND block='{block}' AND lot='{lot}'",
            "$limit": 500
        }
        
        response = requests.get(DOB_VIOLATIONS_API, params=params, timeout=10)
        response.raise_for_status()
        time.sleep(API_DELAY)
        
        data = response.json()
        
        if not data:
            return {
                'dob_violation_count': 0,
                'dob_open_violations': 0
            }, None
        
        # Count open violations (not resolved/certified)
        open_violations = 0
        for record in data:
            disposition = record.get('disposition_comments', '').upper()
            if disposition and ('RESOLVE' in disposition or 'CERTIF' in disposition):
                continue  # Closed
            open_violations += 1
        
        result = {
            'dob_violation_count': len(data),
            'dob_open_violations': open_violations
        }
        return result, None
        
    except Exception as e:
        return None, f"DOB violations API error: {str(e)}"


def enrich_building(building_id, bbl):
    """
    Enrich a single building with tax delinquency and lien data
    Returns dict with all data or None if error
    """
    
    # Get tax delinquency data
    tax_data, tax_error = get_tax_delinquency_data(bbl)
    if tax_error:
        print(f"      ⚠️  {tax_error}")
        tax_data = {
            'has_tax_delinquency': False,
            'tax_delinquency_count': 0,
            'tax_delinquency_water_only': False
        }
    
    # Get ECB violations (these can become liens)
    ecb_data, ecb_error = get_ecb_violations_data(bbl)
    if ecb_error:
        print(f"      ⚠️  {ecb_error}")
        ecb_data = {
            'ecb_violation_count': 0,
            'ecb_total_balance': 0,
            'ecb_open_violations': 0,
            'ecb_total_penalty': 0,
            'ecb_amount_paid': 0,
            'ecb_most_recent_hearing_date': None,
            'ecb_most_recent_hearing_status': None,
            'ecb_respondent_name': None,
            'ecb_respondent_address': None,
            'ecb_respondent_city': None,
            'ecb_respondent_zip': None
        }
    
    # Get DOB violations
    dob_data, dob_error = get_dob_violations_data(bbl)
    if dob_error:
        print(f"      ⚠️  {dob_error}")
        dob_data = {
            'dob_violation_count': 0,
            'dob_open_violations': 0
        }
    
    # Combine all data
    result = {
        **tax_data,
        **ecb_data,
        **dob_data,
        'tax_lien_last_checked': datetime.now()
    }
    
    return result


def update_building_tax_lien_data(cursor, building_id, data):
    """Update building record with tax/lien data"""
    cursor.execute("""
        UPDATE buildings 
        SET 
            has_tax_delinquency = %(has_tax_delinquency)s,
            tax_delinquency_count = %(tax_delinquency_count)s,
            tax_delinquency_water_only = %(tax_delinquency_water_only)s,
            ecb_violation_count = %(ecb_violation_count)s,
            ecb_total_balance = %(ecb_total_balance)s,
            ecb_open_violations = %(ecb_open_violations)s,
            ecb_total_penalty = %(ecb_total_penalty)s,
            ecb_amount_paid = %(ecb_amount_paid)s,
            ecb_most_recent_hearing_date = %(ecb_most_recent_hearing_date)s,
            ecb_most_recent_hearing_status = %(ecb_most_recent_hearing_status)s,
            ecb_respondent_name = %(ecb_respondent_name)s,
            ecb_respondent_address = %(ecb_respondent_address)s,
            ecb_respondent_city = %(ecb_respondent_city)s,
            ecb_respondent_zip = %(ecb_respondent_zip)s,
            dob_violation_count = %(dob_violation_count)s,
            dob_open_violations = %(dob_open_violations)s,
            tax_lien_last_checked = %(tax_lien_last_checked)s
        WHERE id = %(building_id)s
    """, {**data, 'building_id': building_id})


def process_single_building(building, position, total):
    """Process a single building (thread-safe)"""
    building_id = building['id']
    bbl = building['bbl']
    address = building['address']
    
    # Create own database connection for thread safety
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    try:
        with progress_lock:
            print(f"[{position}/{total}] BBL: {bbl}")
            print(f"   📍 {address}")
        
        # Enrich the building
        data = enrich_building(building_id, bbl)
        
        if data:
            # Update database
            update_building_tax_lien_data(cur, building_id, data)
            conn.commit()
            
            # Show summary
            indicators = []
            if data['has_tax_delinquency']:
                water_note = " (water only)" if data['tax_delinquency_water_only'] else ""
                indicators.append(f"Tax Delinquency: {data['tax_delinquency_count']} notices{water_note}")
            if data['ecb_total_balance'] > 0:
                indicators.append(f"ECB Balance: ${data['ecb_total_balance']:,.2f}")
            if data['ecb_open_violations'] > 0:
                indicators.append(f"ECB Open: {data['ecb_open_violations']}")
            if data['ecb_respondent_name']:
                indicators.append(f"ECB Respondent: {data['ecb_respondent_name']}")
            if data['dob_open_violations'] > 0:
                indicators.append(f"DOB Open: {data['dob_open_violations']}")
            
            with progress_lock:
                if indicators:
                    print(f"   ⚠️  {' | '.join(indicators)}")
                else:
                    print(f"   ✓ No issues found")
                print()
            
            return {'success': True, 'data': data}
        else:
            with progress_lock:
                print(f"   ❌ Enrichment failed")
                print()
            return {'success': False, 'data': None}
            
    except Exception as e:
        with progress_lock:
            print(f"   ❌ Error: {str(e)}")
            print()
        return {'success': False, 'data': None}
    finally:
        cur.close()
        conn.close()


def main():
    """Main enrichment process with parallel execution"""
    
    print("=" * 70)
    print("🏢 Step 4: Tax Delinquency & Liens Enrichment (PARALLEL)")
    print("=" * 70)
    print()
    
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    try:
        # Get buildings that need enrichment (never enriched OR >30 days old)
        print("🔍 Checking for buildings needing enrichment...")
        cur.execute("""
            SELECT id, bbl, address
            FROM buildings
            WHERE bbl IS NOT NULL
            AND LENGTH(bbl) = 10
            AND (
                tax_lien_last_checked IS NULL 
                OR tax_lien_last_checked < NOW() - INTERVAL '30 days'
            )
            ORDER BY id
            LIMIT %s
        """, (BUILDING_BATCH_SIZE,))
        
        buildings = cur.fetchall()
        cur.close()
        conn.close()
        
        if not buildings:
            print("   ✅ No buildings need enrichment. All up-to-date!")
            print()
            return
        
        print(f"📊 Found {len(buildings)} buildings to enrich")
        print(f"   Batch size: {BUILDING_BATCH_SIZE}")
        print(f"   Parallel workers: {MAX_WORKERS}")
        print(f"   API delay: {API_DELAY}s between requests")
        print()
        print("🚀 Starting parallel enrichment...")
        print()
        
        successful = 0
        failed = 0
        with_tax_delinquency = 0
        with_ecb_balance = 0
        
        # Process buildings in parallel
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit all tasks
            futures = {
                executor.submit(process_single_building, building, i, len(buildings)): building
                for i, building in enumerate(buildings, 1)
            }
            
            # Collect results as they complete
            for future in as_completed(futures):
                result = future.result()
                if result['success']:
                    successful += 1
                    data = result['data']
                    if data['has_tax_delinquency']:
                        with_tax_delinquency += 1
                    if data['ecb_total_balance'] > 0:
                        with_ecb_balance += 1
                else:
                    failed += 1
        
        # Summary - reconnect to get stats
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        print("=" * 70)
        print("✅ Step 4 Complete!")
        print("=" * 70)
        print(f"\n📊 Statistics:")
        print(f"   • Total processed: {len(buildings)}")
        print(f"   • Successful: {successful}")
        print(f"   • Failed: {failed}")
        print(f"   • With tax delinquency: {with_tax_delinquency}")
        print(f"   • With ECB balances: {with_ecb_balance}")
        
        # Show properties with highest ECB balances
        print(f"\n💰 Top Properties by ECB Balance:")
        cur.execute("""
            SELECT bbl, address, ecb_total_balance, ecb_open_violations
            FROM buildings
            WHERE ecb_total_balance > 0
            ORDER BY ecb_total_balance DESC
            LIMIT 5
        """)
        
        top_properties = cur.fetchall()
        if top_properties:
            for prop in top_properties:
                print(f"   • {prop['bbl']}: ${prop['ecb_total_balance']:,.2f} ({prop['ecb_open_violations']} open)")
        else:
            print("   (None found in this batch)")
        
        print()
        
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
