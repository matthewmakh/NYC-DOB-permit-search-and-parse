#!/usr/bin/env python3
"""
NYC Open Data API Client for DOB Permit Data
Replaces Selenium scraper with direct API access

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
    load_dotenv()  # Try default

import requests
from datetime import datetime, timedelta
import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values
from typing import List, Dict, Optional
import time

# NYC Open Data Configuration - Multiple Endpoints
NYC_OPEN_DATA_ENDPOINTS = {
    'bis_permits': 'https://data.cityofnewyork.us/resource/ipu4-2q9a.json',           # Legacy BIS Permit Issuance
    'dob_now_filings': 'https://data.cityofnewyork.us/resource/w9ak-ipjd.json',       # DOB NOW Job Filings (MOST NEW)
    'dob_now_approved': 'https://data.cityofnewyork.us/resource/rbx6-tga4.json',      # DOB NOW Approved Permits
}
NYC_OPEN_DATA_ENDPOINT = NYC_OPEN_DATA_ENDPOINTS['bis_permits']  # Keep for backward compatibility
NYC_APP_TOKEN = os.getenv('NYC_OPEN_DATA_APP_TOKEN')  # Optional but recommended for higher rate limits

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', '5432')),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME', 'railway')
}


class NYCOpenDataClient:
    """
    Client for NYC Open Data DOB Permit Issuance API
    Dataset: https://data.cityofnewyork.us/Housing-Development/DOB-Permit-Issuance/ipu4-2q9a
    
    NOTE: Authentication with app token causes 403 Forbidden for this dataset.
    This appears to be a public dataset that doesn't require/support authentication.
    Rate limit: ~1,000 requests/hour without auth (sufficient for daily scraping)
    """
    
    def __init__(self, app_token=None):
        """Initialize NYC Open Data API client with optional app token"""
        self.base_url = "https://data.cityofnewyork.us/resource/ipu4-2q9a.json"
        self.app_token = app_token
        self.session = requests.Session()
        
        # Add app token to headers if provided
        if self.app_token:
            self.session.headers.update({
                'X-App-Token': self.app_token
            })
    
    def fetch_permits(
        self, 
        start_date: str,
        end_date: Optional[str] = None,
        permit_type: Optional[str] = None,
        borough: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> List[Dict]:
        """
        Fetch permits from NYC Open Data API
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format (defaults to start_date)
            permit_type: Filter by permit type (e.g., 'NB', 'A1', 'A2')
            borough: Filter by borough name (e.g., 'MANHATTAN', 'BROOKLYN')
            limit: Number of records per request (max 50000)
            offset: Offset for pagination
        
        Returns:
            List of permit records
        """
        if not end_date:
            end_date = start_date
        
        # Convert YYYY-MM-DD to datetime for comparison
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            
            # Format for SoQL query (they use MM/DD/YYYY in the data)
            start_formatted = start_dt.strftime('%m/%d/%Y')
            end_formatted = end_dt.strftime('%m/%d/%Y')
        except:
            print(f"❌ Invalid date format. Use YYYY-MM-DD")
            return []
        
        # Build query using SoQL (Socrata Query Language)
        # Note: filing_date might be more reliable than issuance_date
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
            '$order': 'filing_date DESC'
        }
        
        try:
            response = self.session.get(self.base_url, params=params, timeout=30)
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
        batch_size: int = 1000
    ) -> List[Dict]:
        """
        Fetch all permits with pagination
        
        Returns:
            List of all permit records
        """
        all_permits = []
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
            
            all_permits.extend(permits)
            
            # If we got fewer than batch_size, we're done
            if len(permits) < batch_size:
                break
            
            offset += batch_size
            time.sleep(0.5)  # Be nice to the API
        
        print(f"✅ Total permits fetched: {len(all_permits)}")
        return all_permits


class DOBNowFilingsClient:
    """
    Client for DOB NOW: Build - Job Application Filings
    Dataset: https://data.cityofnewyork.us/Housing-Development/DOB-NOW-Build-Job-Application-Filings/w9ak-ipjd
    
    This is where MOST NEW permit filings go since DOB NOW is the current filing system.
    Contains ~850K records, updated daily.
    """
    
    def __init__(self, app_token=None):
        """Initialize DOB NOW Filings API client"""
        self.base_url = NYC_OPEN_DATA_ENDPOINTS['dob_now_filings']
        self.app_token = app_token
        self.session = requests.Session()
        
        if self.app_token:
            self.session.headers.update({'X-App-Token': self.app_token})
    
    def fetch_filings(
        self,
        start_date: str,
        end_date: Optional[str] = None,
        job_type: Optional[str] = None,
        borough: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> List[Dict]:
        """
        Fetch job filings from DOB NOW
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date (defaults to start_date)
            job_type: Filter by job type (e.g., 'New Building', 'Alteration', 'Demolition')
            borough: Filter by borough name
            limit: Records per request
            offset: Pagination offset
        """
        if not end_date:
            end_date = start_date
        
        # DOB NOW uses ISO date format (YYYY-MM-DD)
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
            '$order': 'filing_date DESC'
        }
        
        try:
            response = self.session.get(self.base_url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            print(f"   [DOB NOW Filings] Fetched {len(data)} records (offset: {offset})")
            return data
        except requests.exceptions.RequestException as e:
            print(f"❌ DOB NOW Filings API Error: {e}")
            return []
    
    def fetch_recent_filings(
        self,
        days: int = 7,
        job_type: Optional[str] = None,
        borough: Optional[str] = None,
        batch_size: int = 1000
    ) -> List[Dict]:
        """
        Fetch all recent filings with pagination
        
        Args:
            days: Number of days back to fetch
            job_type: Filter by job type
            borough: Filter by borough
            batch_size: Records per batch
        """
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        all_filings = []
        offset = 0
        
        print(f"📥 [DOB NOW Filings] Fetching from {start_date} to {end_date}")
        
        while True:
            filings = self.fetch_filings(
                start_date=start_date,
                end_date=end_date,
                job_type=job_type,
                borough=borough,
                limit=batch_size,
                offset=offset
            )
            
            if not filings:
                break
            
            all_filings.extend(filings)
            
            if len(filings) < batch_size:
                break
            
            offset += batch_size
            time.sleep(0.5)
        
        print(f"✅ [DOB NOW Filings] Total fetched: {len(all_filings)}")
        return all_filings


class DOBNowApprovedClient:
    """
    Client for DOB NOW: Build - Approved Permits
    Dataset: https://data.cityofnewyork.us/Housing-Development/DOB-NOW-Build-Approved-Permits/rbx6-tga4
    
    Contains all approved construction permits in DOB NOW (~881K records).
    Use this for permits that have been issued (not just filed).
    """
    
    def __init__(self, app_token=None):
        """Initialize DOB NOW Approved Permits API client"""
        self.base_url = NYC_OPEN_DATA_ENDPOINTS['dob_now_approved']
        self.app_token = app_token
        self.session = requests.Session()
        
        if self.app_token:
            self.session.headers.update({'X-App-Token': self.app_token})
    
    def fetch_permits(
        self,
        start_date: str,
        end_date: Optional[str] = None,
        work_type: Optional[str] = None,
        borough: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> List[Dict]:
        """
        Fetch approved permits from DOB NOW
        
        Args:
            start_date: Start date in YYYY-MM-DD format  
            end_date: End date (defaults to start_date)
            work_type: Filter by work type (e.g., 'Plumbing', 'General Construction')
            borough: Filter by borough name
            limit: Records per request
            offset: Pagination offset
        """
        if not end_date:
            end_date = start_date
        
        # Query by issued_date for recently issued permits
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
            '$order': 'issued_date DESC'
        }
        
        try:
            response = self.session.get(self.base_url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            print(f"   [DOB NOW Approved] Fetched {len(data)} records (offset: {offset})")
            return data
        except requests.exceptions.RequestException as e:
            print(f"❌ DOB NOW Approved API Error: {e}")
            return []
    
    def fetch_recent_permits(
        self,
        days: int = 7,
        work_type: Optional[str] = None,
        borough: Optional[str] = None,
        batch_size: int = 1000
    ) -> List[Dict]:
        """
        Fetch all recently issued permits with pagination
        """
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        all_permits = []
        offset = 0
        
        print(f"📥 [DOB NOW Approved] Fetching from {start_date} to {end_date}")
        
        while True:
            permits = self.fetch_permits(
                start_date=start_date,
                end_date=end_date,
                work_type=work_type,
                borough=borough,
                limit=batch_size,
                offset=offset
            )
            
            if not permits:
                break
            
            all_permits.extend(permits)
            
            if len(permits) < batch_size:
                break
            
            offset += batch_size
            time.sleep(0.5)
        
        print(f"✅ [DOB NOW Approved] Total fetched: {len(all_permits)}")
        return all_permits


class PermitDatabase:
    """Database operations for permits"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.conn = None
        self.cursor = None
    
    def connect(self):
        """Connect to database"""
        self.conn = psycopg2.connect(**self.config)
        self.cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        print("🔌 Connected to database")
    
    def close(self):
        """Close database connection"""
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()
        print("🔌 Database connection closed")
    
    def permit_exists(self, permit_no: str) -> bool:
        """Check if permit already exists"""
        self.cursor.execute("SELECT 1 FROM permits WHERE permit_no = %s", (permit_no,))
        return self.cursor.fetchone() is not None
    
    def insert_permit(self, permit_data: Dict) -> bool:
        """
        Insert permit into database
        
        Args:
            permit_data: Dictionary containing permit information from API
        
        Returns:
            True if inserted, False if skipped (duplicate)
        """
        try:
            # Helper to truncate strings to avoid varchar overflow
            def trunc(val, max_len):
                if val is None:
                    return None
                return str(val)[:max_len] if len(str(val)) > max_len else val
            
            # Map API fields to database columns
            # The API returns many fields - we'll store the most useful ones
            
            permit_no = permit_data.get('job__')  # This appears to be the job number
            if not permit_no:
                permit_no = f"{permit_data.get('bin__', '')}_{permit_data.get('issuance_date', '')}"
            
            # NOTE: No longer checking for duplicates here - ON CONFLICT DO UPDATE handles this
            # This allows the UPSERT to update existing permits when status changes

            
            # Parse dates
            def parse_date(date_str):
                if not date_str:
                    return None
                try:
                    # API returns MM/DD/YYYY format
                    return datetime.strptime(date_str.split()[0], '%m/%d/%Y').date()
                except:
                    try:
                        # Fallback: try ISO format
                        return datetime.fromisoformat(date_str.replace('T', ' ').split('.')[0]).date()
                    except:
                        return None
            
            # Build BBL from components  
            bbl = None
            if permit_data.get('borough') and permit_data.get('block') and permit_data.get('lot'):
                try:
                    # Map borough names to codes
                    borough_map = {
                        'MANHATTAN': '1',
                        'BRONX': '2',
                        'BROOKLYN': '3',
                        'QUEENS': '4',
                        'STATEN ISLAND': '5'
                    }
                    borough_code = borough_map.get(permit_data.get('borough'), permit_data.get('borough'))
                    
                    # API sends block/lot already zero-padded, but might be wrong lengths
                    # Ensure: block = 5 digits, lot = 4 digits
                    block_str = str(permit_data.get('block', '')).strip()
                    lot_str = str(permit_data.get('lot', '')).strip()
                    
                    # Remove leading zeros then re-pad to correct length
                    block_num = block_str.lstrip('0') or '0'
                    lot_num = lot_str.lstrip('0') or '0'
                    
                    block_padded = block_num.zfill(5)
                    lot_padded = lot_num.zfill(4)
                    
                    bbl = f"{borough_code}{block_padded}{lot_padded}"
                    
                    # Ensure BBL is exactly 10 characters
                    if len(bbl) != 10 or not bbl.isdigit():
                        bbl = None
                except Exception as e:
                    print(f"⚠️  BBL creation error: {e}")
                    bbl = None
            
            # Build full address
            address = f"{permit_data.get('house__', '')} {permit_data.get('street_name', '')}".strip()
            
            # Get applicant name (prioritize business name, fall back to owner name)
            applicant = (
                permit_data.get('permittee_s_business_name') or 
                permit_data.get('owner_s_business_name') or 
                f"{permit_data.get('owner_s_first_name', '')} {permit_data.get('owner_s_last_name', '')}".strip() or
                None
            )
            
            # Build work description from multiple fields
            work_desc_parts = []
            if permit_data.get('job_type'):
                work_desc_parts.append(f"Type: {permit_data.get('job_type')}")
            if permit_data.get('permit_subtype'):
                work_desc_parts.append(f"Subtype: {permit_data.get('permit_subtype')}")
            if permit_data.get('bldg_type'):
                work_desc_parts.append(f"Building Type: {permit_data.get('bldg_type')}")
            work_description = ', '.join(work_desc_parts) if work_desc_parts else None
            
            # Insert with ALL new fields from NYC Open Data
            self.cursor.execute("""
                INSERT INTO permits (
                    permit_no,
                    job_type,
                    issue_date,
                    exp_date,
                    bin,
                    address,
                    applicant,
                    block,
                    lot,
                    status,
                    filing_date,
                    proposed_job_start,
                    work_description,
                    job_number,
                    bbl,
                    latitude,
                    longitude,
                    borough,
                    house_number,
                    street_name,
                    zip_code,
                    community_board,
                    job_doc_number,
                    self_cert,
                    bldg_type,
                    residential,
                    special_district_1,
                    special_district_2,
                    work_type,
                    permit_status,
                    filing_status,
                    permit_type,
                    permit_sequence,
                    permit_subtype,
                    oil_gas,
                    permittee_first_name,
                    permittee_last_name,
                    permittee_business_name,
                    permittee_phone,
                    permittee_license_type,
                    permittee_license_number,
                    act_as_superintendent,
                    permittee_other_title,
                    hic_license,
                    site_safety_mgr_first_name,
                    site_safety_mgr_last_name,
                    site_safety_mgr_business_name,
                    superintendent_name,
                    superintendent_business_name,
                    owner_business_type,
                    non_profit,
                    owner_business_name,
                    owner_first_name,
                    owner_last_name,
                    owner_house_number,
                    owner_street_name,
                    owner_city,
                    owner_state,
                    owner_zip_code,
                    owner_phone,
                    dob_run_date,
                    permit_si_no,
                    council_district,
                    census_tract,
                    nta_name,
                    api_source,
                    api_last_updated
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (permit_no) DO UPDATE SET
                    permit_status = EXCLUDED.permit_status,
                    exp_date = EXCLUDED.exp_date,
                    filing_date = EXCLUDED.filing_date,
                    proposed_job_start = EXCLUDED.proposed_job_start,
                    filing_status = EXCLUDED.filing_status,
                    api_last_updated = EXCLUDED.api_last_updated
            """, (
                # Original fields
                trunc(permit_no, 100),
                trunc(permit_data.get('job_type'), 500),
                parse_date(permit_data.get('job_start_date')),  # Use job_start_date as issue_date
                parse_date(permit_data.get('expiration_date')),
                trunc(permit_data.get('bin__'), 50),
                address,
                trunc(applicant, 225),
                trunc(permit_data.get('block'), 20),
                trunc(permit_data.get('lot'), 20),
                trunc(permit_data.get('permit_status'), 50),
                parse_date(permit_data.get('filing_date')),
                parse_date(permit_data.get('job_start_date')),
                work_description,
                trunc(permit_data.get('job__'), 50),
                bbl,
                float(permit_data.get('gis_latitude')) if permit_data.get('gis_latitude') else None,
                float(permit_data.get('gis_longitude')) if permit_data.get('gis_longitude') else None,
                # New NYC Open Data fields
                trunc(permit_data.get('borough'), 20),
                trunc(permit_data.get('house__'), 50),
                trunc(permit_data.get('street_name'), 255),
                trunc(permit_data.get('zip_code'), 15),
                trunc(permit_data.get('community_board'), 3),
                trunc(permit_data.get('job_doc___'), 50),
                trunc(permit_data.get('self_cert'), 10),
                trunc(permit_data.get('bldg_type'), 50),
                trunc(permit_data.get('residential'), 10),
                trunc(permit_data.get('special_district_1'), 50),
                trunc(permit_data.get('special_district_2'), 50),
                trunc(permit_data.get('work_type'), 50),
                trunc(permit_data.get('permit_status'), 50),
                trunc(permit_data.get('filing_status'), 50),
                trunc(permit_data.get('permit_type'), 50),
                trunc(permit_data.get('permit_sequence__'), 20),
                trunc(permit_data.get('permit_subtype'), 50),
                trunc(permit_data.get('oil_gas'), 10),
                trunc(permit_data.get('permittee_s_first_name'), 100),
                trunc(permit_data.get('permittee_s_last_name'), 100),
                trunc(permit_data.get('permittee_s_business_name'), 255),
                trunc(permit_data.get('permittee_s_phone__'), 30),
                trunc(permit_data.get('permittee_s_license_type'), 50),
                trunc(permit_data.get('permittee_s_license__'), 50),
                trunc(permit_data.get('act_as_superintendent'), 10),
                trunc(permit_data.get('permittee_s_other_title'), 100),
                trunc(permit_data.get('hic_license'), 50),
                trunc(permit_data.get('site_safety_mgr_s_first_name'), 100),
                trunc(permit_data.get('site_safety_mgr_s_last_name'), 100),
                trunc(permit_data.get('site_safety_mgr_business_name'), 255),
                trunc(permit_data.get('superintendent_first___last_name'), 200),
                trunc(permit_data.get('superintendent_business_name'), 255),
                trunc(permit_data.get('owner_s_business_type'), 50),
                trunc(permit_data.get('non_profit'), 10),
                trunc(permit_data.get('owner_s_business_name'), 255),
                trunc(permit_data.get('owner_s_first_name'), 100),
                trunc(permit_data.get('owner_s_last_name'), 100),
                trunc(permit_data.get('owner_s_house__'), 50),
                trunc(permit_data.get('owner_s_house_street_name'), 255),
                trunc(permit_data.get('city'), 100),
                trunc(permit_data.get('state'), 20),
                trunc(permit_data.get('owner_s_zip_code'), 15),
                trunc(permit_data.get('owner_s_phone__'), 30),
                parse_date(permit_data.get('dobrundate')),
                trunc(permit_data.get('permit_si_no'), 50),
                trunc(permit_data.get('gis_council_district'), 20),
                trunc(permit_data.get('gis_census_tract'), 20),
                trunc(permit_data.get('gis_nta_name'), 255),
                'nyc_open_data',
                datetime.now()
            ))
            
            return True
        
        except Exception as e:
            print(f"❌ Error inserting permit {permit_no}: {e}")
            # Rollback this failed insert so we can continue
            self.conn.rollback()
            return False
    
    def bulk_insert_permits(self, permits: List[Dict]) -> int:
        """
        Insert multiple permits
        
        Returns:
            Number of permits inserted
        """
        inserted = 0
        
        for permit in permits:
            if self.insert_permit(permit):
                inserted += 1
        
        self.conn.commit()
        return inserted
    
    def insert_dob_now_filing(self, filing_data: Dict) -> bool:
        """
        Insert DOB NOW job filing into database
        Maps DOB NOW Filings API fields to database columns
        
        Args:
            filing_data: Dictionary from DOB NOW Filings API
        
        Returns:
            True if inserted/updated, False if failed
        """
        try:
            # DOB NOW uses job_filing_number as the unique identifier
            permit_no = filing_data.get('job_filing_number')
            if not permit_no:
                return False
            
            def parse_date(date_str):
                if not date_str:
                    return None
                try:
                    # DOB NOW uses ISO format: YYYY-MM-DDTHH:MM:SS.000
                    return datetime.fromisoformat(date_str.replace('T', ' ').split('.')[0]).date()
                except:
                    return None
            
            # Helper to truncate strings to avoid varchar overflow
            def trunc(val, max_len):
                if val is None:
                    return None
                return str(val)[:max_len] if len(str(val)) > max_len else val
            
            # BBL is provided directly by DOB NOW
            bbl = filing_data.get('bbl')
            if bbl and (len(bbl) != 10 or not bbl.isdigit()):
                bbl = None
            
            # Build address
            address = f"{filing_data.get('house_no', '')} {filing_data.get('street_name', '')}".strip()
            
            # Get applicant
            applicant = (
                f"{filing_data.get('applicant_first_name', '')} {filing_data.get('applicant_last_name', '')}".strip() or
                filing_data.get('owner_s_business_name') or
                None
            )
            
            # Build work description
            work_desc_parts = []
            if filing_data.get('job_type'):
                work_desc_parts.append(f"Type: {filing_data.get('job_type')}")
            if filing_data.get('building_type'):
                work_desc_parts.append(f"Building: {filing_data.get('building_type')}")
            if filing_data.get('initial_cost'):
                work_desc_parts.append(f"Est. Cost: ${filing_data.get('initial_cost')}")
            work_description = ', '.join(work_desc_parts) if work_desc_parts else None
            
            self.cursor.execute("""
                INSERT INTO permits (
                    permit_no,
                    job_type,
                    filing_date,
                    bin,
                    address,
                    applicant,
                    block,
                    lot,
                    filing_status,
                    work_description,
                    job_number,
                    bbl,
                    latitude,
                    longitude,
                    borough,
                    house_number,
                    street_name,
                    zip_code,
                    community_board,
                    bldg_type,
                    stories,
                    total_units,
                    owner_business_name,
                    owner_street_name,
                    owner_city,
                    owner_state,
                    owner_zip_code,
                    council_district,
                    census_tract,
                    nta_name,
                    permittee_license_number,
                    api_source,
                    api_last_updated
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (permit_no) DO UPDATE SET
                    filing_status = EXCLUDED.filing_status,
                    filing_date = EXCLUDED.filing_date,
                    api_last_updated = EXCLUDED.api_last_updated
            """, (
                trunc(permit_no, 100),
                trunc(filing_data.get('job_type'), 500),
                parse_date(filing_data.get('filing_date')),
                trunc(filing_data.get('bin'), 50),
                address,
                trunc(applicant, 225),
                trunc(filing_data.get('block'), 20),
                trunc(filing_data.get('lot'), 20),
                trunc(filing_data.get('filing_status'), 50),
                work_description,
                trunc(permit_no, 50),  # Use filing number as job number
                bbl,
                float(filing_data.get('latitude')) if filing_data.get('latitude') else None,
                float(filing_data.get('longitude')) if filing_data.get('longitude') else None,
                trunc(filing_data.get('borough'), 20),
                trunc(filing_data.get('house_no'), 50),
                trunc(filing_data.get('street_name'), 255),
                trunc(filing_data.get('postcode') or filing_data.get('zip'), 15),
                trunc(filing_data.get('commmunity_board'), 3),  # Note: API has typo with 3 m's
                trunc(filing_data.get('building_type'), 50),
                trunc(filing_data.get('existing_stories') or filing_data.get('proposed_no_of_stories'), 20),
                trunc(filing_data.get('existing_dwelling_units') or filing_data.get('proposed_dwelling_units'), 20),
                trunc(filing_data.get('owner_s_business_name'), 255),
                trunc(filing_data.get('owner_s_street_name'), 255),
                trunc(filing_data.get('city'), 100),
                trunc(filing_data.get('state'), 20),
                trunc(filing_data.get('zip'), 15),
                trunc(filing_data.get('council_district'), 20),
                trunc(filing_data.get('census_tract'), 20),
                trunc(filing_data.get('nta'), 255),
                trunc(filing_data.get('applicant_license'), 50),
                'dob_now_filings',  # Mark source as DOB NOW Filings
                datetime.now()
            ))
            
            return True
        
        except Exception as e:
            print(f"❌ Error inserting DOB NOW filing {filing_data.get('job_filing_number')}: {e}")
            self.conn.rollback()
            return False
    
    def insert_dob_now_approved(self, permit_data: Dict) -> bool:
        """
        Insert DOB NOW approved permit into database
        Maps DOB NOW Approved Permits API fields to database columns
        
        IMPORTANT: Uses job_filing_number as permit_no to UPDATE existing filing records
        rather than creating duplicates. This links filings to their approved permits.
        
        Args:
            permit_data: Dictionary from DOB NOW Approved Permits API
        
        Returns:
            True if inserted/updated, False if failed
        """
        try:
            # Helper to truncate strings to max length
            def trunc(val, max_len):
                if val is None:
                    return None
                return str(val)[:max_len] if len(str(val)) > max_len else val
            
            # Use job_filing_number as permit_no to UPDATE existing filing records
            # This prevents duplicates when same job appears in both Filings and Approved
            permit_no = permit_data.get('job_filing_number')
            if not permit_no or permit_no == 'Permit is no':
                # Fall back to work_permit if no job_filing_number
                permit_no = permit_data.get('work_permit')
            if not permit_no or permit_no == 'Permit is not yet issued':
                return False
            
            def parse_date(date_str):
                if not date_str:
                    return None
                try:
                    return datetime.fromisoformat(date_str.replace('T', ' ').split('.')[0]).date()
                except:
                    return None
            
            # BBL provided directly
            bbl = permit_data.get('bbl')
            if bbl and (len(bbl) != 10 or not bbl.isdigit()):
                bbl = None
            
            # Build address
            address = f"{permit_data.get('house_no', '')} {permit_data.get('street_name', '')}".strip()
            
            # Get applicant
            applicant = (
                permit_data.get('applicant_business_name') or
                f"{permit_data.get('applicant_first_name', '')} {permit_data.get('applicant_last_name', '')}".strip() or
                None
            )
            
            # Work description from job_description field
            work_description = permit_data.get('job_description')
            
            self.cursor.execute("""
                INSERT INTO permits (
                    permit_no,
                    work_type,
                    issue_date,
                    exp_date,
                    bin,
                    address,
                    applicant,
                    block,
                    lot,
                    permit_status,
                    work_description,
                    job_number,
                    bbl,
                    latitude,
                    longitude,
                    borough,
                    house_number,
                    street_name,
                    zip_code,
                    community_board,
                    owner_business_name,
                    permittee_license_type,
                    permittee_license_number,
                    council_district,
                    census_tract,
                    nta_name,
                    api_source,
                    api_last_updated
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s
                )
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
            """, (
                trunc(permit_no, 100),
                trunc(permit_data.get('work_type'), 50),
                parse_date(permit_data.get('issued_date')),
                parse_date(permit_data.get('expired_date')),
                trunc(permit_data.get('bin'), 50),
                address,
                trunc(applicant, 225),
                trunc(permit_data.get('block'), 20),
                trunc(permit_data.get('lot'), 20),
                trunc(permit_data.get('permit_status'), 50),
                work_description,
                trunc(permit_data.get('job_filing_number'), 50),
                bbl,
                float(permit_data.get('latitude')) if permit_data.get('latitude') else None,
                float(permit_data.get('longitude')) if permit_data.get('longitude') else None,
                trunc(permit_data.get('borough'), 20),
                trunc(permit_data.get('house_no'), 50),
                trunc(permit_data.get('street_name'), 255),
                trunc(permit_data.get('zip_code'), 15),
                trunc(permit_data.get('community_board') or permit_data.get('c_b_no'), 3),
                trunc(permit_data.get('owner_business_name'), 255),
                trunc(permit_data.get('permittee_s_license_type'), 50),
                trunc(permit_data.get('applicant_license'), 50),
                trunc(permit_data.get('council_district'), 20),
                trunc(permit_data.get('census_tract'), 20),
                trunc(permit_data.get('nta'), 255),
                'dob_now_approved',  # Mark source as DOB NOW Approved
                datetime.now()
            ))
            
            return True
        
        except Exception as e:
            print(f"❌ Error inserting DOB NOW permit {permit_data.get('work_permit')}: {e}")
            self.conn.rollback()
            return False
    
    def bulk_insert_dob_now_filings(self, filings: List[Dict]) -> int:
        """Insert multiple DOB NOW filings"""
        inserted = 0
        for filing in filings:
            if self.insert_dob_now_filing(filing):
                inserted += 1
        self.conn.commit()
        return inserted
    
    def bulk_insert_dob_now_approved(self, permits: List[Dict]) -> int:
        """Insert multiple DOB NOW approved permits"""
        inserted = 0
        for permit in permits:
            if self.insert_dob_now_approved(permit):
                inserted += 1
        self.conn.commit()
        return inserted
    
    # ==================== FAST BULK INSERT METHODS ====================
    # These use execute_values for 10-50x faster inserts
    
    def fast_bulk_insert_permits(self, permits: List[Dict], source: str = 'bis') -> int:
        """
        Fast bulk insert using execute_values (10-50x faster than individual inserts)
        """
        if not permits:
            return 0
        
        try:
            # Prepare data tuples
            values = []
            for p in permits:
                values.append((
                    p.get('permit_no') or p.get('job__'),
                    p.get('job_type'),
                    p.get('permit_status'),
                    p.get('filing_status'),
                    p.get('house_no') or p.get('house__'),
                    p.get('street_name'),
                    p.get('borough'),
                    p.get('block'),
                    p.get('lot'),
                    p.get('bin__'),
                    p.get('permit_type'),
                    p.get('permit_subtype'),
                    p.get('work_type'),
                    p.get('job_description'),
                    p.get('owner_name') or p.get('owner_s_first_name', ''),
                    p.get('owner_business_name') or p.get('owner_s_business_name'),
                    p.get('owner_phone') or p.get('owner_s_phone__'),
                    p.get('owner_email'),
                    p.get('contractor_name') or p.get('permittee_s_first_name', ''),
                    p.get('contractor_business_name') or p.get('permittee_s_business_name'),
                    p.get('contractor_phone') or p.get('permittee_s_phone__'),
                    p.get('contractor_email'),
                    p.get('filing_date'),
                    p.get('issuance_date') or p.get('issued_date'),
                    p.get('expiration_date') or p.get('expired_date'),
                    p.get('proposed_job_start') or p.get('job_start_date'),
                    p.get('estimated_job_cost') or p.get('estimated_job_costs'),
                    p.get('bbl'),
                    p.get('latitude'),
                    p.get('longitude'),
                    p.get('city'),
                    p.get('state'),
                    p.get('zip_code') or p.get('zip'),
                    p.get('existing_dwelling_units'),
                    p.get('proposed_dwelling_units'),
                    p.get('existing_stories'),
                    p.get('proposed_stories'),
                    p.get('existing_height'),
                    p.get('proposed_height'),
                    p.get('applicant_license'),
                    p.get('council_district'),
                    p.get('census_tract'),
                    p.get('nta'),
                    source,
                    datetime.now()
                ))
            
            sql = """
                INSERT INTO permits (
                    permit_no, job_type, permit_status, filing_status,
                    house_no, street_name, borough, block, lot, bin,
                    permit_type, permit_subtype, work_type, job_description,
                    owner_name, owner_business_name, owner_phone, owner_email,
                    contractor_name, contractor_business_name, contractor_phone, contractor_email,
                    filing_date, issuance_date, expiration_date, proposed_job_start,
                    estimated_job_cost, bbl, latitude, longitude,
                    city, state, zip_code,
                    existing_dwelling_units, proposed_dwelling_units,
                    existing_stories, proposed_stories,
                    existing_height, proposed_height,
                    applicant_license, council_district, census_tract, nta,
                    source, created_at
                ) VALUES %s
                ON CONFLICT (permit_no) DO UPDATE SET
                    permit_status = EXCLUDED.permit_status,
                    filing_status = EXCLUDED.filing_status,
                    issuance_date = COALESCE(EXCLUDED.issuance_date, permits.issuance_date),
                    expiration_date = COALESCE(EXCLUDED.expiration_date, permits.expiration_date),
                    updated_at = NOW()
            """
            
            execute_values(self.cursor, sql, values, page_size=1000)
            self.conn.commit()
            return len(permits)
            
        except Exception as e:
            print(f"❌ Fast bulk insert error: {e}")
            self.conn.rollback()
            return 0
    
    def fast_bulk_insert_dob_now_filings(self, filings: List[Dict]) -> int:
        """
        Fast bulk insert for DOB NOW filings using execute_values
        """
        if not filings:
            return 0
        
        try:
            values = []
            for f in filings:
                # Parse job_filing_number for permit_no (e.g., "M00501490-I1")
                job_filing = f.get('job_filing_number', '')
                permit_no = job_filing if job_filing else f.get('job__')
                
                values.append((
                    permit_no,
                    f.get('job_type'),
                    f.get('current_status_of_filing'),  # This is the status for filings
                    f.get('current_status_of_filing'),
                    f.get('house_number'),
                    f.get('street_name'),
                    f.get('borough'),
                    f.get('block'),
                    f.get('lot'),
                    f.get('bin'),
                    f.get('work_type'),
                    None,  # permit_subtype
                    f.get('work_type'),
                    f.get('job_description'),
                    f"{f.get('owners_first_name', '')} {f.get('owners_last_name', '')}".strip() or None,
                    f.get('owners_business_name'),
                    f.get('owners_phone_number'),
                    f.get('owners_email'),
                    f"{f.get('applicants_first_name', '')} {f.get('applicants_last_name', '')}".strip() or None,
                    f.get('applicants_business_name'),
                    f.get('applicants_phone_number'),
                    f.get('applicants_email'),
                    f.get('current_status_date'),  # filing_date
                    None,  # issuance_date (not issued yet)
                    None,  # expiration_date
                    f.get('proposed_job_start_date'),
                    f.get('initial_cost'),
                    None,  # bbl (not in this dataset)
                    f.get('latitude'),
                    f.get('longitude'),
                    f.get('city'),
                    f.get('state'),
                    f.get('zip_code'),
                    f.get('existing_dwelling_units'),
                    f.get('proposed_dwelling_units'),
                    f.get('existing_stories'),
                    f.get('proposed_stories'),
                    f.get('existing_building_height'),
                    f.get('proposed_building_height'),
                    f.get('applicants_license_number'),
                    f.get('council_district'),
                    f.get('census_tract'),
                    f.get('nta'),
                    'dob_now_filings',
                    datetime.now()
                ))
            
            sql = """
                INSERT INTO permits (
                    permit_no, job_type, permit_status, filing_status,
                    house_no, street_name, borough, block, lot, bin,
                    permit_type, permit_subtype, work_type, job_description,
                    owner_name, owner_business_name, owner_phone, owner_email,
                    contractor_name, contractor_business_name, contractor_phone, contractor_email,
                    filing_date, issuance_date, expiration_date, proposed_job_start,
                    estimated_job_cost, bbl, latitude, longitude,
                    city, state, zip_code,
                    existing_dwelling_units, proposed_dwelling_units,
                    existing_stories, proposed_stories,
                    existing_height, proposed_height,
                    applicant_license, council_district, census_tract, nta,
                    source, created_at
                ) VALUES %s
                ON CONFLICT (permit_no) DO UPDATE SET
                    permit_status = EXCLUDED.permit_status,
                    filing_status = EXCLUDED.filing_status,
                    issuance_date = COALESCE(EXCLUDED.issuance_date, permits.issuance_date),
                    expiration_date = COALESCE(EXCLUDED.expiration_date, permits.expiration_date),
                    updated_at = NOW()
            """
            
            execute_values(self.cursor, sql, values, page_size=1000)
            self.conn.commit()
            return len(filings)
            
        except Exception as e:
            print(f"❌ Fast bulk insert filings error: {e}")
            self.conn.rollback()
            return 0
    
    def fast_bulk_insert_dob_now_approved(self, permits: List[Dict]) -> int:
        """
        Fast bulk insert for DOB NOW approved permits using execute_values
        Uses job_filing_number as permit_no to UPDATE existing filings
        """
        if not permits:
            return 0
        
        try:
            values = []
            for p in permits:
                # Use job_filing_number as permit_no (matches the filing record)
                permit_no = p.get('job_filing_number') or p.get('work_permit')
                
                values.append((
                    permit_no,
                    p.get('job_type'),
                    'Approved',  # These are approved permits
                    'Approved',
                    p.get('house_number'),
                    p.get('street_name'),
                    p.get('borough'),
                    p.get('block'),
                    p.get('lot'),
                    p.get('bin'),
                    p.get('work_type'),
                    None,
                    p.get('work_type'),
                    p.get('job_description'),
                    f"{p.get('owners_first_name', '')} {p.get('owners_last_name', '')}".strip() or None,
                    p.get('owners_business_name'),
                    p.get('owners_phone_number'),
                    p.get('owners_email'),
                    f"{p.get('permittees_first_name', '')} {p.get('permittees_last_name', '')}".strip() or None,
                    p.get('permittees_business_name'),
                    p.get('permittees_phone_number'),
                    p.get('permittees_email'),
                    p.get('issued_date'),  # filing_date
                    p.get('issued_date'),  # issuance_date
                    p.get('expired_date'),
                    p.get('proposed_job_start'),
                    p.get('estimated_job_costs'),
                    None,
                    p.get('latitude'),
                    p.get('longitude'),
                    p.get('city'),
                    p.get('state'),
                    p.get('zip_code'),
                    p.get('existing_dwelling_units'),
                    p.get('proposed_dwelling_units'),
                    p.get('existing_stories'),
                    p.get('proposed_stories'),
                    p.get('existing_building_height'),
                    p.get('proposed_building_height'),
                    p.get('applicant_license'),
                    p.get('council_district'),
                    p.get('census_tract'),
                    p.get('nta'),
                    'dob_now_approved',
                    datetime.now()
                ))
            
            sql = """
                INSERT INTO permits (
                    permit_no, job_type, permit_status, filing_status,
                    house_no, street_name, borough, block, lot, bin,
                    permit_type, permit_subtype, work_type, job_description,
                    owner_name, owner_business_name, owner_phone, owner_email,
                    contractor_name, contractor_business_name, contractor_phone, contractor_email,
                    filing_date, issuance_date, expiration_date, proposed_job_start,
                    estimated_job_cost, bbl, latitude, longitude,
                    city, state, zip_code,
                    existing_dwelling_units, proposed_dwelling_units,
                    existing_stories, proposed_stories,
                    existing_height, proposed_height,
                    applicant_license, council_district, census_tract, nta,
                    source, created_at
                ) VALUES %s
                ON CONFLICT (permit_no) DO UPDATE SET
                    permit_status = EXCLUDED.permit_status,
                    filing_status = EXCLUDED.filing_status,
                    issuance_date = COALESCE(EXCLUDED.issuance_date, permits.issuance_date),
                    expiration_date = COALESCE(EXCLUDED.expiration_date, permits.expiration_date),
                    proposed_job_start = COALESCE(EXCLUDED.proposed_job_start, permits.proposed_job_start),
                    updated_at = NOW()
            """
            
            execute_values(self.cursor, sql, values, page_size=1000)
            self.conn.commit()
            return len(permits)
            
        except Exception as e:
            print(f"❌ Fast bulk insert approved error: {e}")
            self.conn.rollback()
            return 0


def run_api_scraper(
    start_date: str,
    end_date: Optional[str] = None,
    permit_type: Optional[str] = None,
    borough: Optional[str] = None,
    sources: List[str] = None
):
    """
    Main function to run the API scraper
    
    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        permit_type: Filter by permit type
        borough: Filter by borough (1-5)
        sources: List of sources to fetch from ['bis', 'dob_now_filings', 'dob_now_approved']
                 Defaults to all sources if not specified
    """
    if sources is None:
        sources = ['bis', 'dob_now_filings', 'dob_now_approved']
    
    print("=" * 80)
    print("NYC DOB Permit Scraper - NYC Open Data API (Multi-Source)")
    print("=" * 80)
    print(f"📅 Date Range: {start_date} to {end_date or start_date}")
    print(f"📦 Sources: {', '.join(sources)}")
    print("=" * 80)
    
    # Initialize database
    db = PermitDatabase(DB_CONFIG)
    db.connect()
    
    total_fetched = 0
    total_inserted = 0
    
    try:
        # 1. Fetch from Legacy BIS Permit Issuance
        if 'bis' in sources:
            print("\n" + "─" * 40)
            print("📋 SOURCE 1: Legacy BIS Permit Issuance")
            print("─" * 40)
            
            bis_client = NYCOpenDataClient(app_token=None)
            bis_permits = bis_client.fetch_all_permits(
                start_date=start_date,
                end_date=end_date,
                permit_type=permit_type,
                borough=borough
            )
            
            print(f"\n💾 Inserting BIS permits into database...")
            bis_inserted = db.bulk_insert_permits(bis_permits)
            total_fetched += len(bis_permits)
            total_inserted += bis_inserted
            print(f"✅ BIS: {bis_inserted} inserted, {len(bis_permits) - bis_inserted} duplicates")
        
        # 2. Fetch from DOB NOW Job Filings (MOST NEW FILINGS GO HERE)
        if 'dob_now_filings' in sources:
            print("\n" + "─" * 40)
            print("📋 SOURCE 2: DOB NOW Job Application Filings")
            print("   (This is where MOST new permit filings go!)")
            print("─" * 40)
            
            filings_client = DOBNowFilingsClient(app_token=None)
            
            # Calculate days from start_date to end_date
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_dt = datetime.strptime(end_date or start_date, '%Y-%m-%d')
            days = (end_dt - start_dt).days + 1
            
            # Fetch using date range
            dob_now_filings = []
            offset = 0
            batch_size = 1000
            
            print(f"📥 [DOB NOW Filings] Fetching from {start_date} to {end_date or start_date}")
            
            while True:
                filings = filings_client.fetch_filings(
                    start_date=start_date,
                    end_date=end_date,
                    borough=borough,
                    limit=batch_size,
                    offset=offset
                )
                
                if not filings:
                    break
                
                dob_now_filings.extend(filings)
                
                if len(filings) < batch_size:
                    break
                
                offset += batch_size
                time.sleep(0.5)
            
            print(f"✅ [DOB NOW Filings] Total fetched: {len(dob_now_filings)}")
            
            print(f"\n💾 Inserting DOB NOW filings into database...")
            filings_inserted = db.bulk_insert_dob_now_filings(dob_now_filings)
            total_fetched += len(dob_now_filings)
            total_inserted += filings_inserted
            print(f"✅ DOB NOW Filings: {filings_inserted} inserted, {len(dob_now_filings) - filings_inserted} duplicates")
        
        # 3. Fetch from DOB NOW Approved Permits
        if 'dob_now_approved' in sources:
            print("\n" + "─" * 40)
            print("📋 SOURCE 3: DOB NOW Approved Permits")
            print("   (Permits that have been issued)")
            print("─" * 40)
            
            approved_client = DOBNowApprovedClient(app_token=None)
            
            # Fetch using date range
            dob_now_approved = []
            offset = 0
            batch_size = 1000
            
            print(f"📥 [DOB NOW Approved] Fetching from {start_date} to {end_date or start_date}")
            
            while True:
                permits = approved_client.fetch_permits(
                    start_date=start_date,
                    end_date=end_date,
                    borough=borough,
                    limit=batch_size,
                    offset=offset
                )
                
                if not permits:
                    break
                
                dob_now_approved.extend(permits)
                
                if len(permits) < batch_size:
                    break
                
                offset += batch_size
                time.sleep(0.5)
            
            print(f"✅ [DOB NOW Approved] Total fetched: {len(dob_now_approved)}")
            
            print(f"\n💾 Inserting DOB NOW approved permits into database...")
            approved_inserted = db.bulk_insert_dob_now_approved(dob_now_approved)
            total_fetched += len(dob_now_approved)
            total_inserted += approved_inserted
            print(f"✅ DOB NOW Approved: {approved_inserted} inserted, {len(dob_now_approved) - approved_inserted} duplicates")
        
        # Summary
        print(f"\n{'=' * 80}")
        print(f"🎉 SCRAPING COMPLETE!")
        print(f"{'=' * 80}")
        print(f"   📊 Total permits from all APIs: {total_fetched}")
        print(f"   ✅ New permits inserted: {total_inserted}")
        print(f"   🔄 Duplicates/updates skipped: {total_fetched - total_inserted}")
        print("=" * 80)
    
    except Exception as e:
        print(f"\n❌ Scraper error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        db.close()


def run_dob_now_only(days: int = 7, borough: Optional[str] = None):
    """
    Convenience function to fetch ONLY from DOB NOW sources (newest filings)
    
    Args:
        days: Number of days back to fetch (default 7)
        borough: Filter by borough
    """
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    run_api_scraper(
        start_date=start_date,
        end_date=end_date,
        borough=borough,
        sources=['dob_now_filings', 'dob_now_approved']
    )


if __name__ == '__main__':
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description='NYC DOB Permit Scraper - Multi-Source')
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
    
    args = parser.parse_args()
    
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
║                    NYC DOB PERMIT SCRAPER - MULTI-SOURCE                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Sources:                                                                    ║
║    • BIS Permits (ipu4-2q9a) - Legacy system permits                        ║
║    • DOB NOW Filings (w9ak-ipjd) - NEW FILINGS GO HERE! ⭐                  ║
║    • DOB NOW Approved (rbx6-tga4) - Issued permits                          ║
╚══════════════════════════════════════════════════════════════════════════════╝
""")
    
    run_api_scraper(
        start_date=args.start,
        end_date=args.end,
        permit_type=args.permit_type,
        borough=args.borough,
        sources=sources
    )
