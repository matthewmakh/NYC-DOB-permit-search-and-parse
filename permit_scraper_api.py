#!/usr/bin/env python3
"""
NYC Open Data API Client for DOB Permit Data - OPTIMIZED VERSION
Replaces Selenium scraper with direct API access

=== WHAT CHANGED (v2 - Performance Refactor) ===
• REMOVED all per-record permit_exists() calls - eliminated N+1 query pattern
• REPLACED row-by-row inserts with chunked bulk upserts using execute_values()
• ADDED BATCH_SIZE constant (default 5000, configurable via PERMIT_BATCH_SIZE env var)
• ADDED one transaction per chunk with per-chunk rollback on failure
• ADDED prepare_rows_*() functions for each source (fast tuple generation)
• ADDED module-scope helper functions (trunc, parse_date_iso, parse_date_mdy)
• ADDED timing metrics for each phase (fetch, prepare, upsert)
• ADDED bad record counting (skips malformed records without crashing)
• SIMPLIFIED upsert logic - rely on ON CONFLICT instead of pre-checking
• KEPT same CLI interface, same endpoints, same output summary format

=== SCHEMA ASSUMPTIONS ===
Columns used in INSERT (permits table):
  permit_no (varchar 100) - UNIQUE, used for ON CONFLICT
  job_type (varchar 500)
  issue_date (date)
  exp_date (date)
  bin (varchar 50)
  address (text)
  applicant (varchar 225)
  block (varchar 20)
  lot (varchar 20)
  status (varchar 50)
  filing_date (date)
  proposed_job_start (date)
  work_description (text)
  job_number (varchar 50)
  bbl (varchar 10)
  latitude (double precision)
  longitude (double precision)
  borough (varchar 20)
  house_number (varchar 50)
  street_name (varchar 255)
  zip_code (varchar 15)
  community_board (varchar 3)
  job_doc_number (varchar 50)
  self_cert (varchar 20)
  bldg_type (varchar 50)
  residential (varchar 20)
  special_district_1 (varchar 50)
  special_district_2 (varchar 50)
  work_type (varchar 50)
  permit_status (varchar 50)
  filing_status (varchar 50)
  permit_type (varchar 50)
  permit_sequence (varchar 50)
  permit_subtype (varchar 50)
  oil_gas (varchar 20)
  permittee_first_name (varchar 100)
  permittee_last_name (varchar 100)
  permittee_business_name (varchar 255)
  permittee_phone (varchar 50)
  permittee_license_type (varchar 50)
  permittee_license_number (varchar 50)
  act_as_superintendent (varchar 20)
  permittee_other_title (varchar 100)
  hic_license (varchar 50)
  site_safety_mgr_first_name (varchar 100)
  site_safety_mgr_last_name (varchar 100)
  site_safety_mgr_business_name (varchar 255)
  superintendent_name (varchar 255)
  superintendent_business_name (varchar 255)
  owner_business_type (varchar 100)
  non_profit (varchar 20)
  owner_business_name (varchar 255)
  owner_first_name (varchar 100)
  owner_last_name (varchar 100)
  owner_house_number (varchar 50)
  owner_street_name (varchar 255)
  owner_city (varchar 100)
  owner_state (varchar 20)
  owner_zip_code (varchar 15)
  owner_phone (varchar 50)
  dob_run_date (date)
  permit_si_no (varchar 50)
  council_district (varchar 20)
  census_tract (varchar 20)
  nta_name (varchar 255)
  stories (varchar 20)
  total_units (varchar 20)
  api_source (varchar 50)
  api_last_updated (timestamp)

Datasets:
1. DOB Permit Issuance (Legacy BIS) - ipu4-2q9a
2. DOB NOW: Build - Job Application Filings - w9ak-ipjd (MOST NEW FILINGS)
3. DOB NOW: Build - Approved Permits - rbx6-tga4

Documentation: https://dev.socrata.com/
"""

import os
from dotenv import load_dotenv

# Load .env from multiple possible locations
if os.path.exists('.env'):
    load_dotenv('.env')
elif os.path.exists('dashboard_html/.env'):
    load_dotenv('dashboard_html/.env')
else:
    load_dotenv()

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timedelta, date
import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values
from typing import List, Dict, Optional, Tuple, Any
import time
import json

# =============================================================================
# CONFIGURATION
# =============================================================================

BATCH_SIZE = int(os.getenv('PERMIT_BATCH_SIZE', '5000'))
API_BATCH_SIZE = int(os.getenv('API_BATCH_SIZE', '50000'))  # Socrata allows up to 50k
DEBUG_MODE = os.getenv('PERMIT_DEBUG', '').lower() in ('1', 'true', 'yes')
SAMPLE_SIZE = int(os.getenv('PERMIT_SAMPLE_SIZE', '50'))  # For sample runs

NYC_OPEN_DATA_ENDPOINTS = {
    'bis_permits': 'https://data.cityofnewyork.us/resource/ipu4-2q9a.json',
    'dob_now_filings': 'https://data.cityofnewyork.us/resource/w9ak-ipjd.json',
    'dob_now_approved': 'https://data.cityofnewyork.us/resource/rbx6-tga4.json',
    'dob_job_applications': 'https://data.cityofnewyork.us/resource/ic3t-wcy2.json',
}
NYC_OPEN_DATA_ENDPOINT = NYC_OPEN_DATA_ENDPOINTS['bis_permits']
NYC_APP_TOKEN = os.getenv('NYC_OPEN_DATA_APP_TOKEN')

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', '5432')),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME', 'railway')
}

# Borough code mapping (used multiple times)
BOROUGH_MAP = {
    'MANHATTAN': '1', 'BRONX': '2', 'BROOKLYN': '3',
    'QUEENS': '4', 'STATEN ISLAND': '5'
}

# =============================================================================
# MODULE-SCOPE HELPER FUNCTIONS (fast, no per-row overhead)
# =============================================================================

def trunc(val: Any, max_len: int) -> Optional[str]:
    """Truncate string to max length. Returns None if val is None."""
    if val is None:
        return None
    s = str(val)
    return s[:max_len] if len(s) > max_len else s


def parse_date_iso(date_str: Optional[str]) -> Optional[date]:
    """
    Parse ISO format date (YYYY-MM-DDTHH:MM:SS.sss or YYYY-MM-DD).
    Returns date object or None.
    """
    if not date_str:
        return None
    try:
        # Handle "YYYY-MM-DDTHH:MM:SS.000" or "YYYY-MM-DD HH:MM:SS"
        clean = date_str.replace('T', ' ').split('.')[0].split()[0]
        if len(clean) == 10:  # YYYY-MM-DD
            return datetime.strptime(clean, '%Y-%m-%d').date()
        return datetime.strptime(clean[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError, IndexError):
        return None


def parse_date_mdy(date_str: Optional[str]) -> Optional[date]:
    """
    Parse MM/DD/YYYY format date.
    Returns date object or None.
    """
    if not date_str:
        return None
    try:
        clean = str(date_str).split()[0]
        return datetime.strptime(clean, '%m/%d/%Y').date()
    except (ValueError, TypeError, IndexError):
        return None


def build_bbl(borough: Optional[str], block: Optional[str], lot: Optional[str]) -> Optional[str]:
    """
    Build BBL from borough/block/lot. Returns 10-char string or None.
    """
    if not borough or not block or not lot:
        return None
    try:
        borough_upper = str(borough).upper().strip()
        borough_code = BOROUGH_MAP.get(borough_upper, borough_upper)
        if not borough_code.isdigit() or len(borough_code) != 1:
            return None
        
        block_num = str(block).strip().lstrip('0') or '0'
        lot_num = str(lot).strip().lstrip('0') or '0'
        
        bbl = f"{borough_code}{block_num.zfill(5)}{lot_num.zfill(4)}"
        
        if len(bbl) == 10 and bbl.isdigit():
            return bbl
        return None
    except (ValueError, TypeError):
        return None


def safe_float(val: Any) -> Optional[float]:
    """Convert to float safely, return None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def clean_phone(phone: Any) -> Optional[str]:
    """Clean phone number - keep only digits, validate length."""
    if not phone:
        return None
    digits = ''.join(c for c in str(phone) if c.isdigit())
    # Accept 10 or 11 digit numbers (11 if starts with 1)
    if len(digits) == 10:
        return digits
    if len(digits) == 11 and digits[0] == '1':
        return digits[1:]
    return trunc(str(phone), 50)  # Keep original if invalid, just truncate


# =============================================================================
# EXPECTED FIELD MAPPINGS (for validation)
# =============================================================================

# Keys we expect from each API and their criticality
BIS_EXPECTED_KEYS = {
    'critical': ['job__'],  # filing_date OR issuance_date - checked separately
    'important': ['borough', 'block', 'lot', 'bin__', 'house__', 'street_name', 
                  'permit_status', 'job_type', 'gis_latitude', 'gis_longitude'],
    'optional': ['filing_date', 'issuance_date', 'job_start_date', 'expiration_date', 'zip_code', 'community_board',
                 'owner_s_first_name', 'owner_s_last_name', 'owner_s_business_name',
                 'permittee_s_first_name', 'permittee_s_last_name', 'permittee_s_business_name']
}

FILINGS_EXPECTED_KEYS = {
    'critical': ['job_filing_number', 'filing_date'],
    'important': ['borough', 'block', 'lot', 'bin', 'house_no', 'street_name',
                  'filing_status', 'job_type', 'latitude', 'longitude'],
    'optional': ['building_type', 'initial_cost', 'postcode', 'zip', 'commmunity_board',
                 'owner_s_business_name', 'applicant_first_name', 'applicant_last_name']
}

APPROVED_EXPECTED_KEYS = {
    'critical': ['job_filing_number', 'issued_date'],  # work_permit as fallback
    'important': ['borough', 'block', 'lot', 'bin', 'house_no', 'street_name',
                  'permit_status', 'work_type', 'latitude', 'longitude'],
    'optional': ['expired_date', 'zip_code', 'community_board', 'c_b_no',
                 'owner_business_name', 'applicant_business_name', 'job_description']
}


def create_retry_session(retries: int = 3, backoff_factor: float = 0.5) -> requests.Session:
    """
    Create a requests session with retry logic for 5xx errors and timeouts.
    Handles 429 rate limiting with Retry-After header.
    """
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,  # Honor Retry-After for 429
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def validate_record(record: Dict, expected_keys: Dict, source_name: str) -> List[str]:
    """
    Validate a single record against expected keys.
    Returns list of warning messages.
    """
    warnings = []
    record_keys = set(record.keys())
    
    # Check critical keys
    for key in expected_keys.get('critical', []):
        if key not in record_keys:
            warnings.append(f"⚠️  CRITICAL key missing: {key}")
        elif not record.get(key):
            warnings.append(f"⚠️  CRITICAL key empty: {key}")
    
    # Check important keys
    for key in expected_keys.get('important', []):
        if key not in record_keys:
            warnings.append(f"⚠️  Important key missing: {key}")
    
    # Validate permit_no derivation
    if source_name == 'BIS':
        permit_no = record.get('job__')
        if not permit_no:
            permit_no = f"{record.get('bin__', '')}_{record.get('issuance_date', '')}"
        if not permit_no or permit_no == '_':
            warnings.append("❌ permit_no would be empty/invalid")
    elif source_name == 'DOB NOW Filings':
        if not record.get('job_filing_number'):
            warnings.append("❌ job_filing_number missing")
    elif source_name == 'DOB NOW Approved':
        permit_no = record.get('job_filing_number') or record.get('work_permit')
        if not permit_no or permit_no in ('Permit is no', 'Permit is not yet issued'):
            warnings.append("❌ No valid permit_no (job_filing_number or work_permit)")
    
    # Validate date fields - BIS uses MDY format, DOB NOW uses ISO
    date_fields = ['filing_date', 'issued_date', 'expired_date', 'job_start_date', 'expiration_date', 'issuance_date']
    for field in date_fields:
        if field in record_keys and record.get(field):
            if source_name == 'BIS':
                parsed = parse_date_mdy(record.get(field))
                fmt = 'MDY'
            else:
                parsed = parse_date_iso(record.get(field))
                fmt = 'ISO'
            if parsed is None:
                warnings.append(f"⚠️  Date parse failed ({fmt}): {field}={record.get(field)}")
    
    # Validate BBL
    borough = record.get('borough')
    block = record.get('block')
    lot = record.get('lot')
    if borough and block and lot:
        bbl = build_bbl(borough, block, lot)
        if bbl is None:
            warnings.append(f"⚠️  BBL invalid: borough={borough}, block={block}, lot={lot}")
    
    # Validate lat/long
    lat_field = 'gis_latitude' if source_name == 'BIS' else 'latitude'
    lon_field = 'gis_longitude' if source_name == 'BIS' else 'longitude'
    if record.get(lat_field):
        if safe_float(record.get(lat_field)) is None:
            warnings.append(f"⚠️  Latitude not a float: {record.get(lat_field)}")
    if record.get(lon_field):
        if safe_float(record.get(lon_field)) is None:
            warnings.append(f"⚠️  Longitude not a float: {record.get(lon_field)}")
    
    return warnings


def debug_record(record: Dict, prepared_tuple: tuple, columns: List[str], 
                 source_name: str, endpoint: str, expected_keys: Dict):
    """
    Print detailed debug info for a single record.
    """
    print(f"\n{'='*80}")
    print(f"🔍 DEBUG: {source_name}")
    print(f"{'='*80}")
    print(f"📡 Endpoint: {endpoint}")
    
    # Print sorted keys from API
    print(f"\n📋 API Record Keys ({len(record)} total):")
    for i, key in enumerate(sorted(record.keys())):
        val = record.get(key)
        val_preview = str(val)[:50] + '...' if len(str(val)) > 50 else str(val)
        print(f"   {i+1:2}. {key}: {val_preview}")
    
    # Check expected keys
    print(f"\n🎯 Expected Key Mapping Check:")
    all_expected = (expected_keys.get('critical', []) + 
                   expected_keys.get('important', []) + 
                   expected_keys.get('optional', []))
    record_keys = set(record.keys())
    for key in all_expected:
        exists = key in record_keys
        has_value = bool(record.get(key)) if exists else False
        status = "✅" if exists and has_value else "⚠️ " if exists else "❌"
        print(f"   {status} {key}: exists={exists}, has_value={has_value}")
    
    # Print warnings
    warnings = validate_record(record, expected_keys, source_name)
    if warnings:
        print(f"\n⚠️  Validation Warnings:")
        for w in warnings:
            print(f"   {w}")
    else:
        print(f"\n✅ No validation warnings")
    
    # Print prepared tuple with column names
    print(f"\n📦 Prepared Tuple ({len(prepared_tuple)} values):")
    for i, (col, val) in enumerate(zip(columns, prepared_tuple)):
        val_str = str(val)[:60] + '...' if val and len(str(val)) > 60 else str(val)
        print(f"   {i+1:2}. {col}: {val_str}")
    
    print(f"\n{'='*80}\n")


def run_debug_mode():
    """
    DEBUG mode: Fetch 1 record from each source and validate mappings.
    """
    print("\n" + "="*80)
    print("🔬 DEBUG MODE - Field Mapping Validation")
    print("="*80)
    
    session = create_retry_session()
    
    # 1. BIS Permits
    print("\n📥 Fetching 1 BIS record...")
    try:
        resp = session.get(
            NYC_OPEN_DATA_ENDPOINTS['bis_permits'],
            params={'$limit': 1, '$order': 'filing_date DESC'},
            timeout=30
        )
        resp.raise_for_status()
        bis_records = resp.json()
        if bis_records:
            rows, _ = prepare_rows_bis(bis_records)
            if rows:
                debug_record(bis_records[0], rows[0], BIS_COLUMNS, 
                           'BIS', NYC_OPEN_DATA_ENDPOINTS['bis_permits'], BIS_EXPECTED_KEYS)
            else:
                print("   ❌ Failed to prepare BIS row")
    except Exception as e:
        print(f"   ❌ BIS fetch error: {e}")
    
    # 2. DOB NOW Filings
    print("\n📥 Fetching 1 DOB NOW Filings record...")
    try:
        resp = session.get(
            NYC_OPEN_DATA_ENDPOINTS['dob_now_filings'],
            params={'$limit': 1, '$order': 'filing_date DESC'},
            timeout=30
        )
        resp.raise_for_status()
        filings_records = resp.json()
        if filings_records:
            rows, _ = prepare_rows_dob_now_filings(filings_records)
            if rows:
                debug_record(filings_records[0], rows[0], FILINGS_COLUMNS,
                           'DOB NOW Filings', NYC_OPEN_DATA_ENDPOINTS['dob_now_filings'], 
                           FILINGS_EXPECTED_KEYS)
            else:
                print("   ❌ Failed to prepare Filings row")
    except Exception as e:
        print(f"   ❌ DOB NOW Filings fetch error: {e}")
    
    # 3. DOB NOW Approved - fetch more records to find one with valid permit_no
    print("\n📥 Fetching DOB NOW Approved records (looking for valid permit_no)...")
    try:
        resp = session.get(
            NYC_OPEN_DATA_ENDPOINTS['dob_now_approved'],
            params={'$limit': 20, '$order': 'issued_date DESC'},
            timeout=30
        )
        resp.raise_for_status()
        approved_records = resp.json()
        if approved_records:
            # Find first record with valid permit_no
            valid_record = None
            for rec in approved_records:
                permit_no = rec.get('job_filing_number')
                if permit_no and permit_no not in ('Permit is no', 'Permit is not yet issued'):
                    valid_record = rec
                    break
                permit_no = rec.get('work_permit')
                if permit_no and permit_no not in ('Permit is no', 'Permit is not yet issued'):
                    valid_record = rec
                    break
            
            if valid_record:
                rows, _ = prepare_rows_dob_now_approved([valid_record])
                if rows:
                    debug_record(valid_record, rows[0], APPROVED_COLUMNS,
                               'DOB NOW Approved', NYC_OPEN_DATA_ENDPOINTS['dob_now_approved'],
                               APPROVED_EXPECTED_KEYS)
                else:
                    print("   ❌ Failed to prepare Approved row")
            else:
                print(f"   ⚠️  No valid permit_no found in {len(approved_records)} records")
                print("   Sample invalid records:")
                for rec in approved_records[:3]:
                    print(f"      job_filing_number={rec.get('job_filing_number')}, work_permit={rec.get('work_permit')}")
    except Exception as e:
        print(f"   ❌ DOB NOW Approved fetch error: {e}")
    
    print("\n✅ Debug mode complete. Review warnings above for mapping issues.")


def run_sample_mode(sample_size: int = None, sources: List[str] = None):
    """
    SAMPLE mode: Fetch N records per source, prepare, upsert, and report counts.
    """
    if sample_size is None:
        sample_size = SAMPLE_SIZE
    if sources is None:
        sources = ['bis', 'dob_now_filings', 'dob_now_approved']
    
    print("\n" + "="*80)
    print(f"🧪 SAMPLE MODE - Testing with {sample_size} records per source")
    print("="*80)
    
    db = PermitDatabase(DB_CONFIG)
    db.connect()
    session = create_retry_session()
    
    results = {}
    
    try:
        for source in sources:
            print(f"\n{'─'*40}")
            print(f"📋 Source: {source}")
            print(f"{'─'*40}")
            
            if source == 'bis':
                endpoint = NYC_OPEN_DATA_ENDPOINTS['bis_permits']
                resp = session.get(endpoint, params={
                    '$limit': sample_size, 
                    '$order': 'filing_date DESC'
                }, timeout=30)
                resp.raise_for_status()
                records = resp.json()
                rows, skipped = prepare_rows_bis(records)
                upserted, failed = db.upsert_bis_permits(rows)
                
            elif source == 'dob_now_filings':
                endpoint = NYC_OPEN_DATA_ENDPOINTS['dob_now_filings']
                resp = session.get(endpoint, params={
                    '$limit': sample_size,
                    '$order': 'filing_date DESC'
                }, timeout=30)
                resp.raise_for_status()
                records = resp.json()
                rows, skipped = prepare_rows_dob_now_filings(records)
                upserted, failed = db.upsert_dob_now_filings(rows)
                
            elif source == 'dob_now_approved':
                endpoint = NYC_OPEN_DATA_ENDPOINTS['dob_now_approved']
                resp = session.get(endpoint, params={
                    '$limit': sample_size,
                    '$order': 'issued_date DESC'
                }, timeout=30)
                resp.raise_for_status()
                records = resp.json()
                rows, skipped = prepare_rows_dob_now_approved(records)
                upserted, failed = db.upsert_dob_now_approved(rows)
            else:
                continue
            
            results[source] = {
                'fetched': len(records),
                'prepared': len(rows),
                'skipped': skipped,
                'upserted': upserted,
                'failed_chunks': failed
            }
            
            print(f"   📊 Fetched: {len(records)}")
            print(f"   📝 Prepared: {len(rows)}")
            print(f"   ⏭️  Skipped: {skipped}")
            print(f"   💾 Upserted: {upserted}")
            if failed > 0:
                print(f"   ❌ Failed chunks: {failed}")
        
        # Summary
        print(f"\n{'='*80}")
        print("📊 SAMPLE RUN SUMMARY")
        print(f"{'='*80}")
        for src, stats in results.items():
            print(f"   {src}: fetched={stats['fetched']}, prepared={stats['prepared']}, "
                  f"skipped={stats['skipped']}, upserted={stats['upserted']}")
        print(f"\n✅ Sample run complete. Run SQL verification queries to validate data.")
        
    except Exception as e:
        print(f"\n❌ Sample mode error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()
    
    return results


# =============================================================================
# API CLIENTS (unchanged logic, just cleaner)
# =============================================================================

class NYCOpenDataClient:
    """Client for NYC Open Data DOB Permit Issuance API (Legacy BIS)"""
    
    # Fields we actually use - for $select optimization
    SELECT_FIELDS = [
        'job__', 'job_type', 'job_start_date', 'expiration_date', 'bin__',
        'house__', 'street_name', 'borough', 'block', 'lot', 'permit_status',
        'filing_date', 'issuance_date', 'zip_code', 'community_board', 'job_doc___', 'self_cert',
        'bldg_type', 'residential', 'special_district_1', 'special_district_2',
        'work_type', 'filing_status', 'permit_type', 'permit_sequence__',
        'permit_subtype', 'oil_gas', 'permittee_s_first_name', 'permittee_s_last_name',
        'permittee_s_business_name', 'permittee_s_phone__', 'permittee_s_license_type',
        'permittee_s_license__', 'act_as_superintendent', 'permittee_s_other_title',
        'hic_license', 'site_safety_mgr_s_first_name', 'site_safety_mgr_s_last_name',
        'site_safety_mgr_business_name', 'superintendent_first___last_name',
        'superintendent_business_name', 'owner_s_business_type', 'non_profit',
        'owner_s_business_name', 'owner_s_first_name', 'owner_s_last_name',
        'owner_s_house__', 'owner_s_house_street_name', 'city', 'state',
        'owner_s_zip_code', 'owner_s_phone__', 'dobrundate', 'permit_si_no',
        'gis_council_district', 'gis_census_tract', 'gis_nta_name',
        'gis_latitude', 'gis_longitude'
    ]
    
    def __init__(self, app_token=None):
        self.base_url = NYC_OPEN_DATA_ENDPOINTS['bis_permits']
        self.app_token = app_token
        self.session = create_retry_session()
        if self.app_token:
            self.session.headers.update({'X-App-Token': self.app_token})
    
    def fetch_permits(
        self, 
        start_date: str,
        end_date: Optional[str] = None,
        permit_type: Optional[str] = None,
        borough: Optional[str] = None,
        limit: int = None,
        offset: int = 0,
        use_select: bool = True
    ) -> List[Dict]:
        if limit is None:
            limit = API_BATCH_SIZE
        if not end_date:
            end_date = start_date
        
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            start_formatted = start_dt.strftime('%Y-%m-%dT00:00:00')
            end_formatted = end_dt.strftime('%Y-%m-%dT23:59:59')
        except ValueError:
            print(f"❌ Invalid date format. Use YYYY-MM-DD")
            return []
        
        where_clauses = [
            f"filing_date >= '{start_formatted}' AND filing_date <= '{end_formatted}'"
        ]
        
        if permit_type:
            where_clauses.append(f"permit_type='{permit_type}'")
        if borough:
            where_clauses.append(f"borough='{borough.upper()}'")
        
        params = {
            '$where': ' AND '.join(where_clauses),
            '$limit': limit,
            '$offset': offset,
            '$order': 'filing_date DESC, job__ ASC'  # Deterministic ordering for pagination
        }
        
        # Optionally select only needed fields to reduce payload
        if use_select:
            params['$select'] = ','.join(self.SELECT_FIELDS)
        
        try:
            response = self.session.get(self.base_url, params=params, timeout=60)
            response.raise_for_status()
            data = response.json()
            print(f"   Fetched {len(data)} permits (offset: {offset})")
            return data
        except requests.exceptions.RequestException as e:
            print(f"❌ API Error: {e}")
            return []
    
    def fetch_all_permits(
        self,
        start_date: str,
        end_date: Optional[str] = None,
        permit_type: Optional[str] = None,
        borough: Optional[str] = None,
        batch_size: int = None,
        stream_callback=None
    ) -> List[Dict]:
        """
        Fetch all permits with pagination.
        If stream_callback is provided, calls it per batch instead of accumulating.
        """
        if batch_size is None:
            batch_size = API_BATCH_SIZE
        
        all_permits = [] if stream_callback is None else None
        offset = 0
        
        print(f"📥 Fetching permits from {start_date} to {end_date or start_date}")
        if permit_type:
            print(f"   Permit Type: {permit_type}")
        if borough:
            print(f"   Borough: {borough}")
        
        while True:
            permits = self.fetch_permits(
                start_date=start_date,
                end_date=end_date,
                permit_type=permit_type,
                borough=borough,
                limit=batch_size,
                offset=offset
            )
            
            if not permits:
                break
            
            if stream_callback:
                stream_callback(permits)
            else:
                all_permits.extend(permits)
            
            if len(permits) < batch_size:
                break
            
            offset += batch_size
            # No fixed sleep - retry logic handles rate limiting
        
        total = offset + len(permits) if permits else offset
        print(f"✅ Total permits fetched: {total if stream_callback else len(all_permits)}")
        return all_permits if all_permits is not None else []


class DOBNowFilingsClient:
    """Client for DOB NOW: Build - Job Application Filings"""
    
    # Fields we actually use
    SELECT_FIELDS = [
        'job_filing_number', 'job_type', 'filing_date', 'filing_status',
        'bin', 'house_no', 'street_name', 'borough', 'block', 'lot',
        'building_type', 'initial_cost', 'postcode', 'zip', 'commmunity_board',
        'existing_stories', 'proposed_no_of_stories', 'existing_dwelling_units',
        'proposed_dwelling_units', 'owner_s_business_name', 'owner_s_street_name',
        'city', 'state', 'applicant_first_name', 'applicant_last_name',
        'applicant_license', 'council_district', 'census_tract', 'nta',
        'latitude', 'longitude', 'bbl'
    ]
    
    def __init__(self, app_token=None):
        self.base_url = NYC_OPEN_DATA_ENDPOINTS['dob_now_filings']
        self.app_token = app_token
        self.session = create_retry_session()
        if self.app_token:
            self.session.headers.update({'X-App-Token': self.app_token})
    
    def fetch_filings(
        self,
        start_date: str,
        end_date: Optional[str] = None,
        job_type: Optional[str] = None,
        borough: Optional[str] = None,
        limit: int = None,
        offset: int = 0,
        use_select: bool = True
    ) -> List[Dict]:
        if limit is None:
            limit = API_BATCH_SIZE
        if not end_date:
            end_date = start_date
        
        where_clauses = [
            f"filing_date >= '{start_date}T00:00:00' AND filing_date <= '{end_date}T23:59:59'"
        ]
        
        if job_type:
            where_clauses.append(f"job_type='{job_type}'")
        if borough:
            where_clauses.append(f"borough='{borough.upper()}'")
        
        params = {
            '$where': ' AND '.join(where_clauses),
            '$limit': limit,
            '$offset': offset,
            '$order': 'filing_date DESC, job_filing_number ASC'
        }
        
        if use_select:
            params['$select'] = ','.join(self.SELECT_FIELDS)
        
        try:
            response = self.session.get(self.base_url, params=params, timeout=60)
            response.raise_for_status()
            data = response.json()
            print(f"   [DOB NOW Filings] Fetched {len(data)} records (offset: {offset})")
            return data
        except requests.exceptions.RequestException as e:
            print(f"❌ DOB NOW Filings API Error: {e}")
            return []


class DOBNowApprovedClient:
    """Client for DOB NOW: Build - Approved Permits"""
    
    # Fields we actually use
    SELECT_FIELDS = [
        'job_filing_number', 'work_permit', 'work_type', 'issued_date', 'expired_date',
        'bin', 'house_no', 'street_name', 'borough', 'block', 'lot',
        'permit_status', 'job_description', 'zip_code', 'community_board', 'c_b_no',
        'owner_business_name', 'applicant_business_name', 'applicant_first_name',
        'applicant_last_name', 'permittee_s_license_type', 'applicant_license',
        'council_district', 'census_tract', 'nta', 'latitude', 'longitude', 'bbl'
    ]
    
    def __init__(self, app_token=None):
        self.base_url = NYC_OPEN_DATA_ENDPOINTS['dob_now_approved']
        self.app_token = app_token
        self.session = create_retry_session()
        if self.app_token:
            self.session.headers.update({'X-App-Token': self.app_token})
    
    def fetch_permits(
        self,
        start_date: str,
        end_date: Optional[str] = None,
        work_type: Optional[str] = None,
        borough: Optional[str] = None,
        limit: int = None,
        offset: int = 0,
        use_select: bool = True
    ) -> List[Dict]:
        if limit is None:
            limit = API_BATCH_SIZE
        if not end_date:
            end_date = start_date
        
        where_clauses = [
            f"issued_date >= '{start_date}T00:00:00' AND issued_date <= '{end_date}T23:59:59'"
        ]
        
        if work_type:
            where_clauses.append(f"work_type='{work_type}'")
        if borough:
            where_clauses.append(f"borough='{borough.upper()}'")
        
        params = {
            '$where': ' AND '.join(where_clauses),
            '$limit': limit,
            '$offset': offset,
            '$order': 'issued_date DESC, job_filing_number ASC'
        }
        
        if use_select:
            params['$select'] = ','.join(self.SELECT_FIELDS)
        
        try:
            response = self.session.get(self.base_url, params=params, timeout=60)
            response.raise_for_status()
            data = response.json()
            print(f"   [DOB NOW Approved] Fetched {len(data)} records (offset: {offset})")
            return data
        except requests.exceptions.RequestException as e:
            print(f"❌ DOB NOW Approved API Error: {e}")
            return []


# =============================================================================
# ROW PREPARATION FUNCTIONS (convert API dicts to tuples for bulk insert)
# =============================================================================

# Column order for BIS permits (67 columns)
BIS_COLUMNS = [
    'permit_no', 'job_type', 'issue_date', 'exp_date', 'bin', 'address',
    'applicant', 'block', 'lot', 'status', 'filing_date', 'proposed_job_start',
    'work_description', 'job_number', 'bbl', 'latitude', 'longitude', 'borough',
    'house_number', 'street_name', 'zip_code', 'community_board', 'job_doc_number',
    'self_cert', 'bldg_type', 'residential', 'special_district_1', 'special_district_2',
    'work_type', 'permit_status', 'filing_status', 'permit_type', 'permit_sequence',
    'permit_subtype', 'oil_gas', 'permittee_first_name', 'permittee_last_name',
    'permittee_business_name', 'permittee_phone', 'permittee_license_type',
    'permittee_license_number', 'act_as_superintendent', 'permittee_other_title',
    'hic_license', 'site_safety_mgr_first_name', 'site_safety_mgr_last_name',
    'site_safety_mgr_business_name', 'superintendent_name', 'superintendent_business_name',
    'owner_business_type', 'non_profit', 'owner_business_name', 'owner_first_name',
    'owner_last_name', 'owner_house_number', 'owner_street_name', 'owner_city',
    'owner_state', 'owner_zip_code', 'owner_phone', 'dob_run_date', 'permit_si_no',
    'council_district', 'census_tract', 'nta_name', 'api_source', 'api_last_updated'
]

# Column order for DOB NOW filings (33 columns - subset)
FILINGS_COLUMNS = [
    'permit_no', 'job_type', 'filing_date', 'bin', 'address', 'applicant',
    'block', 'lot', 'filing_status', 'work_description', 'job_number', 'bbl',
    'latitude', 'longitude', 'borough', 'house_number', 'street_name', 'zip_code',
    'community_board', 'bldg_type', 'stories', 'total_units', 'owner_business_name',
    'owner_street_name', 'owner_city', 'owner_state', 'owner_zip_code',
    'council_district', 'census_tract', 'nta_name', 'permittee_license_number',
    'api_source', 'api_last_updated'
]

# Column order for DOB NOW approved (40 columns - includes contact fields)
APPROVED_COLUMNS = [
    'permit_no', 'work_type', 'issue_date', 'exp_date', 'bin', 'address',
    'applicant', 'block', 'lot', 'permit_status', 'work_description', 'job_number',
    'bbl', 'latitude', 'longitude', 'borough', 'house_number', 'street_name',
    'zip_code', 'community_board', 'owner_business_name', 'owner_first_name',
    'owner_last_name', 'owner_business_type', 'owner_house_number', 'owner_street_name',
    'owner_city', 'owner_state', 'owner_zip_code', 'owner_phone',
    'permittee_first_name', 'permittee_last_name', 'permittee_business_name',
    'permittee_phone', 'permittee_license_type', 'permittee_license_number',
    'council_district', 'census_tract', 'nta_name', 'api_source', 'api_last_updated'
]


def prepare_rows_bis(permits: List[Dict]) -> Tuple[List[tuple], int]:
    """
    Convert BIS API records to tuples for bulk insert.
    Returns (list of tuples, count of skipped bad records).
    """
    rows = []
    skipped = 0
    now = datetime.now()
    
    for p in permits:
        try:
            # Get permit_no (required)
            permit_no = p.get('job__')
            if not permit_no:
                permit_no = f"{p.get('bin__', '')}_{p.get('issuance_date', '')}"
            if not permit_no or permit_no == '_':
                skipped += 1
                continue
            
            # Parse dates - BIS uses MM/DD/YYYY format, not ISO!
            # Note: issuance_date = when permit was issued (use for issue_date)
            #       job_start_date = proposed construction start (use for proposed_job_start)
            filing_date = parse_date_mdy(p.get('filing_date')) or parse_date_mdy(p.get('issuance_date'))
            issue_date = parse_date_mdy(p.get('issuance_date'))  # NOT job_start_date (can be future)
            exp_date = parse_date_mdy(p.get('expiration_date'))
            job_start = parse_date_mdy(p.get('job_start_date'))
            dob_run = parse_date_mdy(p.get('dobrundate'))
            
            # Build address
            address = f"{p.get('house__', '')} {p.get('street_name', '')}".strip() or None
            
            # Applicant
            applicant = (
                p.get('permittee_s_business_name') or 
                p.get('owner_s_business_name') or 
                f"{p.get('owner_s_first_name', '')} {p.get('owner_s_last_name', '')}".strip() or
                None
            )
            
            # Work description
            work_desc_parts = []
            if p.get('job_type'):
                work_desc_parts.append(f"Type: {p.get('job_type')}")
            if p.get('permit_subtype'):
                work_desc_parts.append(f"Subtype: {p.get('permit_subtype')}")
            if p.get('bldg_type'):
                work_desc_parts.append(f"Building Type: {p.get('bldg_type')}")
            work_description = ', '.join(work_desc_parts) if work_desc_parts else None
            
            # BBL
            bbl = build_bbl(p.get('borough'), p.get('block'), p.get('lot'))
            
            row = (
                trunc(permit_no, 100),
                trunc(p.get('job_type'), 500),
                issue_date,  # issue_date (parsed above)
                exp_date,  # exp_date (parsed above)
                trunc(p.get('bin__'), 50),
                address,
                trunc(applicant, 225),
                trunc(p.get('block'), 20),
                trunc(p.get('lot'), 20),
                trunc(p.get('permit_status'), 50),  # status
                filing_date,  # filing_date (parsed above)
                job_start,  # proposed_job_start (parsed above)
                work_description,
                trunc(p.get('job__'), 50),  # job_number
                bbl,
                safe_float(p.get('gis_latitude')),
                safe_float(p.get('gis_longitude')),
                trunc(p.get('borough'), 20),
                trunc(p.get('house__'), 50),
                trunc(p.get('street_name'), 255),
                trunc(p.get('zip_code'), 15),
                trunc(p.get('community_board'), 3),
                trunc(p.get('job_doc___'), 50),
                trunc(p.get('self_cert'), 20),
                trunc(p.get('bldg_type'), 50),
                trunc(p.get('residential'), 20),
                trunc(p.get('special_district_1'), 50),
                trunc(p.get('special_district_2'), 50),
                trunc(p.get('work_type'), 50),
                trunc(p.get('permit_status'), 50),
                trunc(p.get('filing_status'), 50),
                trunc(p.get('permit_type'), 50),
                trunc(p.get('permit_sequence__'), 50),
                trunc(p.get('permit_subtype'), 50),
                trunc(p.get('oil_gas'), 20),
                trunc(p.get('permittee_s_first_name'), 100),
                trunc(p.get('permittee_s_last_name'), 100),
                trunc(p.get('permittee_s_business_name'), 255),
                trunc(p.get('permittee_s_phone__'), 50),
                trunc(p.get('permittee_s_license_type'), 50),
                trunc(p.get('permittee_s_license__'), 50),
                trunc(p.get('act_as_superintendent'), 20),
                trunc(p.get('permittee_s_other_title'), 100),
                trunc(p.get('hic_license'), 50),
                trunc(p.get('site_safety_mgr_s_first_name'), 100),
                trunc(p.get('site_safety_mgr_s_last_name'), 100),
                trunc(p.get('site_safety_mgr_business_name'), 255),
                trunc(p.get('superintendent_first___last_name'), 255),
                trunc(p.get('superintendent_business_name'), 255),
                trunc(p.get('owner_s_business_type'), 100),
                trunc(p.get('non_profit'), 20),
                trunc(p.get('owner_s_business_name'), 255),
                trunc(p.get('owner_s_first_name'), 100),
                trunc(p.get('owner_s_last_name'), 100),
                trunc(p.get('owner_s_house__'), 50),
                trunc(p.get('owner_s_house_street_name'), 255),
                trunc(p.get('city'), 100),
                trunc(p.get('state'), 20),
                trunc(p.get('owner_s_zip_code'), 15),
                trunc(p.get('owner_s_phone__'), 50),
                dob_run,  # dob_run_date (parsed above)
                trunc(p.get('permit_si_no'), 50),
                trunc(p.get('gis_council_district'), 20),
                trunc(p.get('gis_census_tract'), 20),
                trunc(p.get('gis_nta_name'), 255),
                'nyc_open_data',
                now
            )
            rows.append(row)
        except Exception as e:
            skipped += 1
            if DEBUG_MODE:
                print(f"   ⚠️  [BIS] Skipped record: {e}")
            continue
    
    # Deduplicate by permit_no (first column), keeping last occurrence
    seen = {}
    for row in rows:
        seen[row[0]] = row  # permit_no is first element
    deduped = list(seen.values())
    duplicates = len(rows) - len(deduped)
    
    return deduped, skipped + duplicates


def prepare_rows_dob_now_filings(filings: List[Dict]) -> Tuple[List[tuple], int]:
    """
    Convert DOB NOW Filings API records to tuples for bulk insert.
    Returns (list of tuples, count of skipped bad records).
    """
    rows = []
    skipped = 0
    now = datetime.now()
    
    for f in filings:
        try:
            permit_no = f.get('job_filing_number')
            if not permit_no:
                skipped += 1
                continue
            
            # Build address
            address = f"{f.get('house_no', '')} {f.get('street_name', '')}".strip() or None
            
            # Applicant
            applicant = (
                f"{f.get('applicant_first_name', '')} {f.get('applicant_last_name', '')}".strip() or
                f.get('owner_s_business_name') or
                None
            )
            
            # Work description
            work_desc_parts = []
            if f.get('job_type'):
                work_desc_parts.append(f"Type: {f.get('job_type')}")
            if f.get('building_type'):
                work_desc_parts.append(f"Building: {f.get('building_type')}")
            if f.get('initial_cost'):
                work_desc_parts.append(f"Est. Cost: ${f.get('initial_cost')}")
            work_description = ', '.join(work_desc_parts) if work_desc_parts else None
            
            # BBL (provided directly)
            bbl = f.get('bbl')
            if bbl and (len(bbl) != 10 or not bbl.isdigit()):
                bbl = None
            
            row = (
                trunc(permit_no, 100),
                trunc(f.get('job_type'), 500),
                parse_date_iso(f.get('filing_date')),
                trunc(f.get('bin'), 50),
                address,
                trunc(applicant, 225),
                trunc(f.get('block'), 20),
                trunc(f.get('lot'), 20),
                trunc(f.get('filing_status'), 50),
                work_description,
                trunc(permit_no, 50),  # job_number = filing number
                bbl,
                safe_float(f.get('latitude')),
                safe_float(f.get('longitude')),
                trunc(f.get('borough'), 20),
                trunc(f.get('house_no'), 50),
                trunc(f.get('street_name'), 255),
                trunc(f.get('postcode') or f.get('zip'), 15),
                trunc(f.get('commmunity_board'), 3),  # API has typo
                trunc(f.get('building_type'), 50),
                trunc(f.get('existing_stories') or f.get('proposed_no_of_stories'), 20),
                trunc(f.get('existing_dwelling_units') or f.get('proposed_dwelling_units'), 20),
                trunc(f.get('owner_s_business_name'), 255),
                trunc(f.get('owner_s_street_name'), 255),
                trunc(f.get('city'), 100),
                trunc(f.get('state'), 20),
                trunc(f.get('zip'), 15),
                trunc(f.get('council_district'), 20),
                trunc(f.get('census_tract'), 20),
                trunc(f.get('nta'), 255),
                trunc(f.get('applicant_license'), 50),
                'dob_now_filings',
                now
            )
            rows.append(row)
        except Exception as e:
            skipped += 1
            if DEBUG_MODE:
                print(f"   ⚠️  [Filings] Skipped record: {e}")
            continue
    
    # Deduplicate by permit_no (first column), keeping last occurrence
    seen = {}
    for row in rows:
        seen[row[0]] = row
    deduped = list(seen.values())
    duplicates = len(rows) - len(deduped)
    
    return deduped, skipped + duplicates


def prepare_rows_dob_now_approved(permits: List[Dict]) -> Tuple[List[tuple], int]:
    """
    Convert DOB NOW Approved API records to tuples for bulk insert.
    Returns (list of tuples, count of skipped bad records).
    """
    rows = []
    skipped = 0
    now = datetime.now()
    
    for p in permits:
        try:
            # Use job_filing_number to update existing filing records
            permit_no = p.get('job_filing_number')
            if not permit_no or permit_no == 'Permit is no':
                permit_no = p.get('work_permit')
            if not permit_no or permit_no == 'Permit is not yet issued':
                skipped += 1
                continue
            
            # Build address
            address = f"{p.get('house_no', '')} {p.get('street_name', '')}".strip() or None
            
            # Applicant
            applicant = (
                p.get('applicant_business_name') or
                f"{p.get('applicant_first_name', '')} {p.get('applicant_last_name', '')}".strip() or
                None
            )
            
            # BBL
            bbl = p.get('bbl')
            if bbl and (len(bbl) != 10 or not bbl.isdigit()):
                bbl = None
            
            row = (
                trunc(permit_no, 100),
                trunc(p.get('work_type'), 50),
                parse_date_iso(p.get('issued_date')),  # issue_date
                parse_date_iso(p.get('expired_date')),  # exp_date
                trunc(p.get('bin'), 50),
                address,
                trunc(applicant, 225),
                trunc(p.get('block'), 20),
                trunc(p.get('lot'), 20),
                trunc(p.get('permit_status'), 50),
                p.get('job_description'),  # work_description (text, no trunc needed)
                trunc(p.get('job_filing_number'), 50),  # job_number
                bbl,
                safe_float(p.get('latitude')),
                safe_float(p.get('longitude')),
                trunc(p.get('borough'), 20),
                trunc(p.get('house_no'), 50),
                trunc(p.get('street_name'), 255),
                trunc(p.get('zip_code'), 15),
                trunc(p.get('community_board') or p.get('c_b_no'), 3),
                # Owner fields
                trunc(p.get('owner_business_name'), 255),
                trunc(p.get('owner_first_name'), 100),
                trunc(p.get('owner_last_name'), 100),
                trunc(p.get('owner_business_type'), 100),
                trunc(p.get('owner_house_number'), 50),
                trunc(p.get('owner_street_name'), 255),
                trunc(p.get('owner_city'), 100),
                trunc(p.get('owner_state'), 20),
                trunc(p.get('owner_zip_code'), 15),
                trunc(p.get('owner_phone'), 50),
                # Permittee fields
                trunc(p.get('permittee_first_name'), 100),
                trunc(p.get('permittee_last_name'), 100),
                trunc(p.get('permittee_business_name'), 255),
                trunc(p.get('permittee_phone'), 50),
                trunc(p.get('permittee_license_type'), 50),
                trunc(p.get('permittee_license_number') or p.get('applicant_license'), 50),
                # Location fields
                trunc(p.get('council_district'), 20),
                trunc(p.get('census_tract'), 20),
                trunc(p.get('nta'), 255),
                'dob_now_approved',
                now
            )
            rows.append(row)
        except Exception as e:
            skipped += 1
            if DEBUG_MODE:
                print(f"   ⚠️  [Approved] Skipped record: {e}")
            continue
    
    # Deduplicate by permit_no (first column), keeping last occurrence
    seen = {}
    for row in rows:
        seen[row[0]] = row
    deduped = list(seen.values())
    duplicates = len(rows) - len(deduped)
    
    return deduped, skipped + duplicates


# =============================================================================
# DATABASE CLASS (optimized bulk operations)
# =============================================================================

class PermitDatabase:
    """Database operations for permits - optimized for bulk operations"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.conn = None
        self.cursor = None
    
    def connect(self):
        """Connect to database"""
        self.conn = psycopg2.connect(**self.config)
        self.cursor = self.conn.cursor()
        print("🔌 Connected to database")
    
    def close(self):
        """Close database connection"""
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()
        print("🔌 Database connection closed")
    
    def upsert_bis_permits(self, rows: List[tuple]) -> Tuple[int, int]:
        """
        Bulk upsert BIS permits using execute_values.
        Returns (total_affected, failed_chunks).
        """
        if not rows:
            return 0, 0
        
        columns = ', '.join(BIS_COLUMNS)
        
        sql = f"""
            INSERT INTO permits ({columns})
            VALUES %s
            ON CONFLICT (permit_no) DO UPDATE SET
                permit_status = EXCLUDED.permit_status,
                exp_date = EXCLUDED.exp_date,
                filing_date = EXCLUDED.filing_date,
                proposed_job_start = EXCLUDED.proposed_job_start,
                filing_status = EXCLUDED.filing_status,
                api_last_updated = EXCLUDED.api_last_updated
        """
        
        return self._chunked_upsert(sql, rows, "BIS")
    
    def upsert_dob_now_filings(self, rows: List[tuple]) -> Tuple[int, int]:
        """
        Bulk upsert DOB NOW filings using execute_values.
        Returns (total_affected, failed_chunks).
        """
        if not rows:
            return 0, 0
        
        columns = ', '.join(FILINGS_COLUMNS)
        
        sql = f"""
            INSERT INTO permits ({columns})
            VALUES %s
            ON CONFLICT (permit_no) DO UPDATE SET
                filing_status = EXCLUDED.filing_status,
                filing_date = EXCLUDED.filing_date,
                api_last_updated = EXCLUDED.api_last_updated
        """
        
        return self._chunked_upsert(sql, rows, "DOB NOW Filings")
    
    def upsert_dob_now_approved(self, rows: List[tuple]) -> Tuple[int, int]:
        """
        Bulk upsert DOB NOW approved permits using execute_values.
        Returns (total_affected, failed_chunks).
        """
        if not rows:
            return 0, 0
        
        columns = ', '.join(APPROVED_COLUMNS)
        
        sql = f"""
            INSERT INTO permits ({columns})
            VALUES %s
            ON CONFLICT (permit_no) DO UPDATE SET
                permit_status = COALESCE(EXCLUDED.permit_status, permits.permit_status),
                issue_date = COALESCE(EXCLUDED.issue_date, permits.issue_date),
                exp_date = COALESCE(EXCLUDED.exp_date, permits.exp_date),
                work_type = COALESCE(EXCLUDED.work_type, permits.work_type),
                work_description = COALESCE(EXCLUDED.work_description, permits.work_description),
                api_source = CASE 
                    WHEN EXCLUDED.issue_date IS NOT NULL THEN 'dob_now_approved'
                    ELSE permits.api_source
                END,
                api_last_updated = EXCLUDED.api_last_updated
        """
        
        return self._chunked_upsert(sql, rows, "DOB NOW Approved")
    
    def _chunked_upsert(self, sql: str, rows: List[tuple], source_name: str) -> Tuple[int, int]:
        """
        Execute upsert in chunks with per-chunk transactions.
        Returns (total_rows_affected, failed_chunk_count).
        """
        total_affected = 0
        failed_chunks = 0
        total_chunks = (len(rows) + BATCH_SIZE - 1) // BATCH_SIZE
        
        for i in range(0, len(rows), BATCH_SIZE):
            chunk = rows[i:i + BATCH_SIZE]
            chunk_num = (i // BATCH_SIZE) + 1
            
            try:
                execute_values(self.cursor, sql, chunk, page_size=BATCH_SIZE)
                affected = self.cursor.rowcount if self.cursor.rowcount >= 0 else len(chunk)
                self.conn.commit()
                total_affected += affected
                print(f"   [{source_name}] Chunk {chunk_num}/{total_chunks}: {affected} rows")
            except Exception as e:
                self.conn.rollback()
                failed_chunks += 1
                print(f"   ❌ [{source_name}] Chunk {chunk_num}/{total_chunks} FAILED: {e}")
        
        return total_affected, failed_chunks


# =============================================================================
# MAIN SCRAPER FUNCTION
# =============================================================================

def run_api_scraper(
    start_date: str,
    end_date: Optional[str] = None,
    permit_type: Optional[str] = None,
    borough: Optional[str] = None,
    sources: List[str] = None
):
    """
    Main function to run the API scraper.
    """
    if sources is None:
        sources = ['bis', 'dob_now_filings', 'dob_now_approved']
    
    print("=" * 80)
    print("NYC DOB Permit Scraper - NYC Open Data API (Multi-Source) [OPTIMIZED v2]")
    print("=" * 80)
    print(f"📅 Date Range: {start_date} to {end_date or start_date}")
    print(f"📦 Sources: {', '.join(sources)}")
    print(f"⚡ Batch Size: {BATCH_SIZE}")
    print("=" * 80)
    
    total_start = time.time()
    
    # Initialize database
    db = PermitDatabase(DB_CONFIG)
    db.connect()
    
    total_fetched = 0
    total_upserted = 0
    total_skipped = 0
    total_failed_chunks = 0
    
    try:
        # 1. Fetch from Legacy BIS Permit Issuance
        if 'bis' in sources:
            # BIS is legacy system - no new data after November 2020
            bis_cutoff = datetime(2020, 11, 30)
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            if start_dt > bis_cutoff:
                print("\n" + "─" * 40)
                print("📋 SOURCE 1: Legacy BIS Permit Issuance")
                print("   ⚠️  Skipping - BIS has no data after Nov 2020")
                print("─" * 40)
            else:
                print("\n" + "─" * 40)
                print("📋 SOURCE 1: Legacy BIS Permit Issuance")
                print("─" * 40)
                
                # Fetch phase
                fetch_start = time.time()
                bis_client = NYCOpenDataClient(app_token=None)
                bis_permits = bis_client.fetch_all_permits(
                    start_date=start_date,
                    end_date=end_date,
                    permit_type=permit_type,
                    borough=borough
                )
                fetch_time = time.time() - fetch_start
                print(f"   ⏱️  Fetch time: {fetch_time:.2f}s")
                
                # Prepare phase
                prep_start = time.time()
                bis_rows, bis_skipped = prepare_rows_bis(bis_permits)
                prep_time = time.time() - prep_start
                print(f"   📝 Prepared {len(bis_rows)} rows ({bis_skipped} skipped) in {prep_time:.2f}s")
                
                # Upsert phase
                upsert_start = time.time()
                bis_upserted, bis_failed = db.upsert_bis_permits(bis_rows)
                upsert_time = time.time() - upsert_start
                print(f"   💾 Upserted {bis_upserted} rows in {upsert_time:.2f}s")
                
                total_fetched += len(bis_permits)
                total_upserted += bis_upserted
                total_skipped += bis_skipped
                total_failed_chunks += bis_failed
        
        # 2. Fetch from DOB NOW Job Filings
        if 'dob_now_filings' in sources:
            print("\n" + "─" * 40)
            print("📋 SOURCE 2: DOB NOW Job Application Filings")
            print("   (This is where MOST new permit filings go!)")
            print("─" * 40)
            
            # Fetch phase
            fetch_start = time.time()
            filings_client = DOBNowFilingsClient(app_token=None)
            dob_now_filings = []
            offset = 0
            
            while True:
                filings = filings_client.fetch_filings(
                    start_date=start_date,
                    end_date=end_date,
                    borough=borough,
                    limit=API_BATCH_SIZE,
                    offset=offset
                )
                
                if not filings:
                    break
                
                dob_now_filings.extend(filings)
                
                if len(filings) < API_BATCH_SIZE:
                    break
                
                offset += API_BATCH_SIZE
                # No fixed sleep - retry session handles rate limits
            
            fetch_time = time.time() - fetch_start
            print(f"✅ [DOB NOW Filings] Total fetched: {len(dob_now_filings)}")
            print(f"   ⏱️  Fetch time: {fetch_time:.2f}s")
            
            # Prepare phase
            prep_start = time.time()
            filings_rows, filings_skipped = prepare_rows_dob_now_filings(dob_now_filings)
            prep_time = time.time() - prep_start
            print(f"   📝 Prepared {len(filings_rows)} rows ({filings_skipped} skipped) in {prep_time:.2f}s")
            
            # Upsert phase
            upsert_start = time.time()
            filings_upserted, filings_failed = db.upsert_dob_now_filings(filings_rows)
            upsert_time = time.time() - upsert_start
            print(f"   💾 Upserted {filings_upserted} rows in {upsert_time:.2f}s")
            
            total_fetched += len(dob_now_filings)
            total_upserted += filings_upserted
            total_skipped += filings_skipped
            total_failed_chunks += filings_failed
        
        # 3. Fetch from DOB NOW Approved Permits
        if 'dob_now_approved' in sources:
            print("\n" + "─" * 40)
            print("📋 SOURCE 3: DOB NOW Approved Permits")
            print("   (Permits that have been issued)")
            print("─" * 40)
            
            # Fetch phase
            fetch_start = time.time()
            approved_client = DOBNowApprovedClient(app_token=None)
            dob_now_approved = []
            offset = 0
            
            while True:
                permits = approved_client.fetch_permits(
                    start_date=start_date,
                    end_date=end_date,
                    borough=borough,
                    limit=API_BATCH_SIZE,
                    offset=offset
                )
                
                if not permits:
                    break
                
                dob_now_approved.extend(permits)
                
                if len(permits) < API_BATCH_SIZE:
                    break
                
                offset += API_BATCH_SIZE
                # No fixed sleep - retry session handles rate limits
            
            fetch_time = time.time() - fetch_start
            print(f"✅ [DOB NOW Approved] Total fetched: {len(dob_now_approved)}")
            print(f"   ⏱️  Fetch time: {fetch_time:.2f}s")
            
            # Prepare phase
            prep_start = time.time()
            approved_rows, approved_skipped = prepare_rows_dob_now_approved(dob_now_approved)
            prep_time = time.time() - prep_start
            print(f"   📝 Prepared {len(approved_rows)} rows ({approved_skipped} skipped) in {prep_time:.2f}s")
            
            # Upsert phase
            upsert_start = time.time()
            approved_upserted, approved_failed = db.upsert_dob_now_approved(approved_rows)
            upsert_time = time.time() - upsert_start
            print(f"   💾 Upserted {approved_upserted} rows in {upsert_time:.2f}s")
            
            total_fetched += len(dob_now_approved)
            total_upserted += approved_upserted
            total_skipped += approved_skipped
            total_failed_chunks += approved_failed
        
        # Summary
        total_time = time.time() - total_start
        print(f"\n{'=' * 80}")
        print(f"🎉 SCRAPING COMPLETE!")
        print(f"{'=' * 80}")
        print(f"   📊 Total records from all APIs: {total_fetched}")
        print(f"   ✅ Total rows upserted: {total_upserted}")
        print(f"   ⏭️  Skipped (malformed): {total_skipped}")
        if total_failed_chunks > 0:
            print(f"   ❌ Failed chunks: {total_failed_chunks}")
        print(f"   ⏱️  Total time: {total_time:.2f}s")
        if total_fetched > 0:
            print(f"   ⚡ Speed: {total_fetched / total_time:.1f} records/sec")
        print("=" * 80)
    
    except Exception as e:
        print(f"\n❌ Scraper error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        db.close()


def run_dob_now_only(days: int = 7, borough: Optional[str] = None):
    """
    Convenience function to fetch ONLY from DOB NOW sources (newest filings).
    """
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    run_api_scraper(
        start_date=start_date,
        end_date=end_date,
        borough=borough,
        sources=['dob_now_filings', 'dob_now_approved']
    )


def print_sql_verification_queries():
    """Print SQL queries for verifying data correctness after a run."""
    print("""
================================================================================
📊 SQL VERIFICATION QUERIES
================================================================================

-- 1. Check permit_no uniqueness and duplicates
SELECT permit_no, COUNT(*) as cnt 
FROM permits 
GROUP BY permit_no 
HAVING COUNT(*) > 1 
LIMIT 20;

-- 2. Null-rate checks on key columns by api_source
SELECT 
    api_source,
    COUNT(*) as total,
    COUNT(filing_date) as has_filing_date,
    COUNT(issue_date) as has_issue_date,
    COUNT(borough) as has_borough,
    COUNT(address) as has_address,
    COUNT(bbl) as has_bbl,
    COUNT(permit_status) as has_permit_status,
    COUNT(filing_status) as has_filing_status,
    ROUND(100.0 * COUNT(bbl) / NULLIF(COUNT(*), 0), 1) as bbl_pct,
    ROUND(100.0 * COUNT(borough) / NULLIF(COUNT(*), 0), 1) as borough_pct
FROM permits 
WHERE api_source IS NOT NULL
GROUP BY api_source
ORDER BY api_source;

-- 3. Verify update behavior: Approved updates existing filing rows
-- Find permit_no that appear in both filings and approved (via api_source history)
-- and check if issue_date is populated after approved runs
SELECT 
    p.permit_no,
    p.api_source,
    p.filing_date,
    p.issue_date,
    p.permit_status,
    p.api_last_updated
FROM permits p
WHERE p.api_source = 'dob_now_approved'
  AND p.issue_date IS NOT NULL
  AND p.filing_date IS NOT NULL
ORDER BY p.api_last_updated DESC
LIMIT 20;

-- 4. Sanity spot check: 20 recent records per source with key fields
(SELECT 
    'nyc_open_data' as source,
    permit_no, borough, address, filing_date, issue_date, 
    permit_status, bbl, api_last_updated
FROM permits 
WHERE api_source = 'nyc_open_data' 
ORDER BY api_last_updated DESC 
LIMIT 20)
UNION ALL
(SELECT 
    'dob_now_filings' as source,
    permit_no, borough, address, filing_date, issue_date,
    filing_status as permit_status, bbl, api_last_updated
FROM permits 
WHERE api_source = 'dob_now_filings' 
ORDER BY api_last_updated DESC 
LIMIT 20)
UNION ALL
(SELECT 
    'dob_now_approved' as source,
    permit_no, borough, address, filing_date, issue_date,
    permit_status, bbl, api_last_updated
FROM permits 
WHERE api_source = 'dob_now_approved' 
ORDER BY api_last_updated DESC 
LIMIT 20);

-- 5. Check for records with bad BBLs (not 10 digits)
SELECT permit_no, bbl, borough, block, lot, api_source
FROM permits 
WHERE bbl IS NOT NULL 
  AND (LENGTH(bbl) != 10 OR bbl !~ '^[0-9]+$')
LIMIT 20;

-- 6. Check date field quality
SELECT 
    api_source,
    COUNT(*) as total,
    COUNT(CASE WHEN filing_date > CURRENT_DATE THEN 1 END) as future_filing_dates,
    COUNT(CASE WHEN issue_date > CURRENT_DATE THEN 1 END) as future_issue_dates,
    COUNT(CASE WHEN filing_date < '2000-01-01' THEN 1 END) as old_filing_dates,
    MIN(filing_date) as min_filing_date,
    MAX(filing_date) as max_filing_date
FROM permits
WHERE api_source IS NOT NULL
GROUP BY api_source;

-- 7. Recommended indexes (run once)
-- CREATE UNIQUE INDEX IF NOT EXISTS idx_permits_permit_no ON permits(permit_no);
-- CREATE INDEX IF NOT EXISTS idx_permits_api_source_filing_date ON permits(api_source, filing_date);
-- CREATE INDEX IF NOT EXISTS idx_permits_bbl ON permits(bbl) WHERE bbl IS NOT NULL;
-- CREATE INDEX IF NOT EXISTS idx_permits_borough_filing_date ON permits(borough, filing_date);

================================================================================
""")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='NYC DOB Permit Scraper - Multi-Source [OPTIMIZED v2]')
    parser.add_argument('--start', '-s', type=str, help='Start date (YYYY-MM-DD)', 
                        default=(datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d'))
    parser.add_argument('--end', '-e', type=str, help='End date (YYYY-MM-DD)',
                        default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--days', '-d', type=int, help='Days back to fetch (alternative to start/end)')
    parser.add_argument('--borough', '-b', type=str, help='Filter by borough name')
    parser.add_argument('--permit-type', '-p', type=str, help='Filter by permit type')
    parser.add_argument('--sources', type=str, nargs='+', 
                        choices=['bis', 'dob_now_filings', 'dob_now_approved', 'all'],
                        default=['all'],
                        help='Data sources to fetch from')
    parser.add_argument('--dob-now-only', action='store_true',
                        help='Fetch only from DOB NOW sources (newest filings)')
    parser.add_argument('--batch-size', type=int, help=f'Batch size for DB upserts (default: {BATCH_SIZE})')
    
    # New validation modes
    parser.add_argument('--debug', action='store_true',
                        help='DEBUG mode: Fetch 1 record per source and validate field mappings')
    parser.add_argument('--sample', action='store_true',
                        help='SAMPLE mode: Fetch N records per source, upsert, and report counts')
    parser.add_argument('--sample-size', type=int, default=50,
                        help='Number of records per source in sample mode (default: 50)')
    parser.add_argument('--show-sql', action='store_true',
                        help='Print SQL verification queries to run after scraping')
    
    args = parser.parse_args()
    
    # Handle batch size override from CLI
    if args.batch_size:
        BATCH_SIZE = args.batch_size
    
    # DEBUG mode
    if args.debug:
        run_debug_mode()
        exit(0)
    
    # Show SQL queries
    if args.show_sql:
        print_sql_verification_queries()
        exit(0)
    
    # SAMPLE mode
    if args.sample:
        sources = ['bis', 'dob_now_filings', 'dob_now_approved']
        if args.dob_now_only:
            sources = ['dob_now_filings', 'dob_now_approved']
        elif 'all' not in args.sources:
            sources = args.sources
        run_sample_mode(sample_size=args.sample_size, sources=sources)
        exit(0)
    
    # Handle days option
    if args.days:
        args.end = datetime.now().strftime('%Y-%m-%d')
        args.start = (datetime.now() - timedelta(days=args.days)).strftime('%Y-%m-%d')
    
    # Handle sources
    if 'all' in args.sources:
        sources = ['bis', 'dob_now_filings', 'dob_now_approved']
    else:
        sources = args.sources
    
    # DOB NOW only mode
    if args.dob_now_only:
        sources = ['dob_now_filings', 'dob_now_approved']
    
    print(f"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              NYC DOB PERMIT SCRAPER - MULTI-SOURCE [OPTIMIZED v2]            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Sources:                                                                    ║
║    • BIS Permits (ipu4-2q9a) - Legacy system permits                        ║
║    • DOB NOW Filings (w9ak-ipjd) - NEW FILINGS GO HERE! ⭐                  ║
║    • DOB NOW Approved (rbx6-tga4) - Issued permits                          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Performance: Bulk upserts, {BATCH_SIZE} rows/chunk, API batch {API_BATCH_SIZE}          ║
║  Validation: --debug, --sample, --show-sql                                   ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
    
    run_api_scraper(
        start_date=args.start,
        end_date=args.end,
        permit_type=args.permit_type,
        borough=args.borough,
        sources=sources
    )
