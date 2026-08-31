#!/usr/bin/env python3
"""
Step 4: Tax Delinquency & Liens Enrichment

Enriches buildings with:
- Tax delinquency status from NYC DOF
- ECB violations with financial balances (can become liens)
- DOB building violations
- DOB NOW Safety violations (boilers, elevators, facades, LL33/84/87/97, etc.)

Data Sources:
- NYC DOF Property Tax Delinquencies (9rz4-mjek)
- NYC ECB Violations (6bgk-3dad) - includes penalty_imposed and balance_due
- NYC DOB Violations (3h2n-5cm9)
- NYC DOB NOW: Safety Violations (855j-jady, updated daily)

Updates buildings table fields:
- has_tax_delinquency, tax_delinquency_count, tax_delinquency_water_only
- ecb_violation_count, ecb_total_balance, ecb_open_violations
- dob_violation_count, dob_open_violations
- dob_safety_violation_count, dob_safety_open_violations
- dob_safety_last_checked
- tax_lien_last_checked
"""

import psycopg2
import psycopg2.extras
import os
import sys
import requests
import time
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from socrata_client import SocrataClient, where_block_lot

# Force unbuffered output for Railway logging
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

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

# Configuration
API_DELAY = float(os.getenv('API_DELAY', '0.1'))  # Reduced since we're parallelizing
BUILDING_BATCH_SIZE = int(os.getenv('BUILDING_BATCH_SIZE', '500'))
MAX_WORKERS = int(os.getenv('MAX_WORKERS', '10'))  # Parallel threads

# A lien-sale notice older than this is history, not a live distress signal.
# The lien sale runs roughly annually (and skipped 2022-2024 entirely), so
# 24 months keeps the most recent full cycle in scope.
LIEN_RECENCY_MONTHS = int(os.getenv('LIEN_RECENCY_MONTHS', '24'))

# Thread-safe counter for progress tracking
progress_lock = Lock()
stats_lock = Lock()

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = SocrataClient()
    return _client


def _parse_cycle_date(value):
    """The lien-sale list's cycle column shows up in a few formats depending
    on vintage ('2024-12-17T00:00:00.000', 'December 2017', '12/17/2024')."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%B %Y', '%b %Y', '%Y'):
        try:
            return datetime.strptime(text[:10] if fmt == '%Y-%m-%d' else text, fmt).date()
        except ValueError:
            continue
    return None


def get_tax_delinquency_data(bbl):
    """
    Lien-sale notice status for a property, scoped to the MOST RECENT sale
    cycle. Being on a 2017 notice list says nothing about today — the flag
    is only set when the latest cycle this lot appears on is recent.
    Returns (data_dict, error_message) tuple
    """
    try:
        client = _get_client()
        where = where_block_lot('borough', 'block', 'lot', bbl)
        data = client.get('tax_lien_sale', **{'$where': where, '$limit': 500})
        time.sleep(API_DELAY)

        empty = {
            'has_tax_delinquency': False,
            'tax_delinquency_count': 0,
            'tax_delinquency_water_only': False,
            'tax_delinquency_latest_date': None,
        }
        if not data:
            return empty, None

        # Find the cycle column for this dataset vintage and date each row.
        cycle_col = None
        for candidate in ('month', 'cycle', 'cycle_date', 'sale_date', 'asofdate'):
            if candidate in data[0]:
                cycle_col = candidate
                break

        dated = [(_parse_cycle_date(r.get(cycle_col)) if cycle_col else None, r) for r in data]
        parseable = [(d, r) for d, r in dated if d is not None]

        if parseable:
            latest_date = max(d for d, _ in parseable)
            latest_rows = [r for d, r in parseable if d == latest_date]
            cutoff = date.today() - timedelta(days=LIEN_RECENCY_MONTHS * 30)
            is_current = latest_date >= cutoff
        else:
            # Cycle column missing/unparseable: fall back to flagging (old
            # behavior) rather than silently hiding real delinquencies.
            latest_date = None
            latest_rows = data
            is_current = True

        has_non_water = any(
            (r.get('water_debt_only') or 'NO').upper() == 'NO' for r in latest_rows
        )

        result = {
            'has_tax_delinquency': is_current,
            'tax_delinquency_count': len(latest_rows) if is_current else 0,
            'tax_delinquency_water_only': is_current and not has_non_water,
            'tax_delinquency_latest_date': latest_date,
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
        # Padding-agnostic block/lot filter: matches whichever form (zero-
        # padded or stripped) this dataset stores, without extra calls.
        data = _get_client().get_all('ecb_violations', page_size=1000, max_rows=10000, **{
            "$where": where_block_lot('boro', 'block', 'lot', bbl),
            "$order": "issue_date DESC",
        })
        time.sleep(API_DELAY)
        
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
        data = _get_client().get_all('dob_violations', page_size=1000, max_rows=10000, **{
            "$where": where_block_lot('boro', 'block', 'lot', bbl),
        })
        time.sleep(API_DELAY)

        if not data:
            return {
                'dob_violation_count': 0,
                'dob_open_violations': 0
            }, None

        # violation_category literally says ACTIVE or DISMISSED — use it
        # instead of guessing from free-text disposition comments. Fall back
        # to the old heuristic only when the category is absent.
        open_violations = 0
        for record in data:
            category = (record.get('violation_category') or '').upper()
            if category:
                if 'ACTIVE' in category:
                    open_violations += 1
                continue
            disposition = (record.get('disposition_comments') or '').upper()
            if disposition and ('RESOLVE' in disposition or 'CERTIF' in disposition):
                continue
            open_violations += 1

        result = {
            'dob_violation_count': len(data),
            'dob_open_violations': open_violations
        }
        return result, None
        
    except Exception as e:
        return None, f"DOB violations API error: {str(e)}"


def safety_violation_is_open(status):
    """DOB Safety uses explicit lifecycle labels, not the BIS category.

    ACTIVE and WAIVED/PENDING still require attention. CURED, DISMISSED,
    DISPUTED SUCCESSFULLY and the other terminal states do not.
    """
    normalized = (status or '').strip().upper()
    return normalized == 'ACTIVE' or 'PENDING' in normalized


def get_dob_safety_violations_data(bbl):
    """Daily DOB NOW Safety violations for a numeric 10-digit BBL."""
    try:
        numeric_bbl = str(bbl).strip()
        if len(numeric_bbl) != 10 or not numeric_bbl.isdigit():
            return None, f"DOB Safety violations: invalid BBL {bbl!r}"
        data = _get_client().get_all(
            'dob_safety_violations', page_size=1000, max_rows=10000,
            **{
                '$where': f'bbl={int(numeric_bbl)}',
                '$select': 'violation_status, count(*) AS violation_count',
                '$group': 'violation_status',
                '$order': 'violation_count DESC',
            },
        )
        time.sleep(API_DELAY)

        total = 0
        open_count = 0
        for row in data:
            count = int(row.get('violation_count') or 0)
            total += count
            if safety_violation_is_open(row.get('violation_status')):
                open_count += count
        return {
            'dob_safety_violation_count': total,
            'dob_safety_open_violations': open_count,
            'dob_safety_last_checked': datetime.now(),
        }, None
    except Exception as e:
        return None, f"DOB Safety violations API error: {str(e)}"


def enrich_building(building_id, bbl):
    """
    Enrich a single building with tax delinquency and lien data
    Returns dict with all data or None if error
    """
    
    # Get tax delinquency data
    tax_data, tax_error = get_tax_delinquency_data(bbl)
    errors = []
    if tax_error:
        print(f"      ⚠️  {tax_error}")
        errors.append(tax_error)
    
    # Get ECB violations (these can become liens)
    ecb_data, ecb_error = get_ecb_violations_data(bbl)
    if ecb_error:
        print(f"      ⚠️  {ecb_error}")
        errors.append(ecb_error)
    
    # Get DOB violations
    dob_data, dob_error = get_dob_violations_data(bbl)
    if dob_error:
        print(f"      ⚠️  {dob_error}")
        errors.append(dob_error)

    # The Safety dataset is distinct from the legacy BIS violations above
    # and publishes daily. Keep its success/freshness independent so an ECB
    # outage cannot make a successful Safety refresh look stale.
    safety_data, safety_error = get_dob_safety_violations_data(bbl)
    if safety_error:
        print(f"      ⚠️  {safety_error}")
        errors.append(safety_error)
    
    # Combine all data
    # A failed source contributes no keys. This preserves the last known good
    # figures instead of replacing a real balance/violation count with zero.
    result = {}
    for data in (tax_data, ecb_data, dob_data, safety_data):
        if data:
            result.update(data)
    if not any((tax_error, ecb_error, dob_error)):
        result['tax_lien_last_checked'] = datetime.now()
    result['_errors'] = errors
    
    return result


def update_building_tax_lien_data(cursor, building_id, data):
    """Update only sources that returned successfully."""
    allowed = {
        'has_tax_delinquency', 'tax_delinquency_count',
        'tax_delinquency_water_only',
        'ecb_violation_count', 'ecb_total_balance', 'ecb_open_violations',
        'ecb_total_penalty', 'ecb_amount_paid',
        'ecb_most_recent_hearing_date', 'ecb_most_recent_hearing_status',
        'ecb_respondent_name', 'ecb_respondent_address',
        'ecb_respondent_city', 'ecb_respondent_zip',
        'dob_violation_count', 'dob_open_violations', 'tax_lien_last_checked',
        'dob_safety_violation_count', 'dob_safety_open_violations',
        'dob_safety_last_checked',
    }
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return
    assignments = ', '.join(f'{column} = %s' for column in updates)
    cursor.execute(
        f"UPDATE buildings SET {assignments} WHERE id = %s",
        [*updates.values(), building_id],
    )
    if 'tax_delinquency_latest_date' in data:
        cursor.execute("SAVEPOINT lien_date")
        try:
            cursor.execute("""
                UPDATE buildings SET tax_delinquency_latest_date = %s
                WHERE id = %s
            """, (data['tax_delinquency_latest_date'], building_id))
            cursor.execute("RELEASE SAVEPOINT lien_date")
        except psycopg2.Error:
            cursor.execute("ROLLBACK TO SAVEPOINT lien_date")


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
            if data.get('has_tax_delinquency'):
                water_note = " (water only)" if data['tax_delinquency_water_only'] else ""
                indicators.append(f"Tax Delinquency: {data['tax_delinquency_count']} notices{water_note}")
            if data.get('ecb_total_balance', 0) > 0:
                indicators.append(f"ECB Balance: ${data['ecb_total_balance']:,.2f}")
            if data.get('ecb_open_violations', 0) > 0:
                indicators.append(f"ECB Open: {data['ecb_open_violations']}")
            if data.get('ecb_respondent_name'):
                indicators.append(f"ECB Respondent: {data['ecb_respondent_name']}")
            if data.get('dob_open_violations', 0) > 0:
                indicators.append(f"DOB Open: {data['dob_open_violations']}")
            if data.get('dob_safety_open_violations', 0) > 0:
                indicators.append(f"DOB Safety Open: {data['dob_safety_open_violations']}")
            
            with progress_lock:
                if indicators:
                    print(f"   ⚠️  {' | '.join(indicators)}")
                else:
                    print(f"   ✓ No issues found")
                print()
            
            return {'success': not data.get('_errors'), 'data': data,
                    'partial': bool(data.get('_errors'))}
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
                OR dob_safety_last_checked IS NULL
                OR dob_safety_last_checked < NOW() - INTERVAL '1 day'
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
                    if data.get('has_tax_delinquency'):
                        with_tax_delinquency += 1
                    if data.get('ecb_total_balance', 0) > 0:
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
