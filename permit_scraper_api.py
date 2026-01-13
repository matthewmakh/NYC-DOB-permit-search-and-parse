#!/usr/bin/env python3
"""
NYC Open Data API Client for DOB Permit Issuance
Replaces Selenium scraper with direct API access

Dataset: DOB Permit Issuance
API Endpoint: https://data.cityofnewyork.us/resource/ipu4-2q9a.json
Documentation: https://dev.socrata.com/foundry/data.cityofnewyork.us/ipu4-2q9a
"""

from dotenv import load_dotenv
load_dotenv()

import os
import requests
from datetime import datetime, timedelta
import psycopg2
import psycopg2.extras
from typing import List, Dict, Optional
import time

# NYC Open Data Configuration
NYC_OPEN_DATA_ENDPOINT = "https://data.cityofnewyork.us/resource/ipu4-2q9a.json"
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
                    job_start_date = EXCLUDED.job_start_date,
                    filing_status = EXCLUDED.filing_status,
                    api_last_updated = EXCLUDED.api_last_updated
            """, (
                # Original fields
                permit_no,
                permit_data.get('job_type'),
                parse_date(permit_data.get('job_start_date')),  # Use job_start_date as issue_date
                parse_date(permit_data.get('expiration_date')),
                permit_data.get('bin__'),
                address,
                applicant,
                permit_data.get('block'),
                permit_data.get('lot'),
                permit_data.get('permit_status'),
                parse_date(permit_data.get('filing_date')),
                parse_date(permit_data.get('job_start_date')),
                work_description,
                permit_data.get('job__'),
                bbl,
                float(permit_data.get('gis_latitude')) if permit_data.get('gis_latitude') else None,
                float(permit_data.get('gis_longitude')) if permit_data.get('gis_longitude') else None,
                # New NYC Open Data fields
                permit_data.get('borough'),
                permit_data.get('house__'),
                permit_data.get('street_name'),
                permit_data.get('zip_code'),
                permit_data.get('community_board'),
                permit_data.get('job_doc___'),
                permit_data.get('self_cert'),
                permit_data.get('bldg_type'),
                permit_data.get('residential'),
                permit_data.get('special_district_1'),
                permit_data.get('special_district_2'),
                permit_data.get('work_type'),
                permit_data.get('permit_status'),
                permit_data.get('filing_status'),
                permit_data.get('permit_type'),
                permit_data.get('permit_sequence__'),
                permit_data.get('permit_subtype'),
                permit_data.get('oil_gas'),
                permit_data.get('permittee_s_first_name'),
                permit_data.get('permittee_s_last_name'),
                permit_data.get('permittee_s_business_name'),
                permit_data.get('permittee_s_phone__'),
                permit_data.get('permittee_s_license_type'),
                permit_data.get('permittee_s_license__'),
                permit_data.get('act_as_superintendent'),
                permit_data.get('permittee_s_other_title'),
                permit_data.get('hic_license'),
                permit_data.get('site_safety_mgr_s_first_name'),
                permit_data.get('site_safety_mgr_s_last_name'),
                permit_data.get('site_safety_mgr_business_name'),
                permit_data.get('superintendent_first___last_name'),
                permit_data.get('superintendent_business_name'),
                permit_data.get('owner_s_business_type'),
                permit_data.get('non_profit'),
                permit_data.get('owner_s_business_name'),
                permit_data.get('owner_s_first_name'),
                permit_data.get('owner_s_last_name'),
                permit_data.get('owner_s_house__'),
                permit_data.get('owner_s_house_street_name'),
                permit_data.get('city'),
                permit_data.get('state'),
                permit_data.get('owner_s_zip_code'),
                permit_data.get('owner_s_phone__'),
                parse_date(permit_data.get('dobrundate')),
                permit_data.get('permit_si_no'),
                permit_data.get('gis_council_district'),
                permit_data.get('gis_census_tract'),
                permit_data.get('gis_nta_name'),
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


def run_api_scraper(
    start_date: str,
    end_date: Optional[str] = None,
    permit_type: Optional[str] = None,
    borough: Optional[str] = None
):
    """
    Main function to run the API scraper
    
    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        permit_type: Filter by permit type
        borough: Filter by borough (1-5)
    """
    print("=" * 80)
    print("NYC DOB Permit Scraper - NYC Open Data API")
    print("=" * 80)
    
    # Initialize API client (without app token - dataset doesn't support it)
    api_client = NYCOpenDataClient(app_token=None)
    
    # Initialize database
    db = PermitDatabase(DB_CONFIG)
    db.connect()
    
    try:
        # Fetch permits from API
        permits = api_client.fetch_all_permits(
            start_date=start_date,
            end_date=end_date,
            permit_type=permit_type,
            borough=borough
        )
        
        # Insert into database
        print(f"\n💾 Inserting permits into database...")
        inserted = db.bulk_insert_permits(permits)
        
        print(f"\n{'=' * 80}")
        print(f"✅ Scraping complete!")
        print(f"   Total permits from API: {len(permits)}")
        print(f"   New permits inserted: {inserted}")
        print(f"   Duplicates skipped: {len(permits) - inserted}")
        print("=" * 80)
    
    except Exception as e:
        print(f"\n❌ Scraper error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        db.close()


if __name__ == '__main__':
    import sys
    
    # Example usage with command line args or defaults
    if len(sys.argv) > 1:
        start_date = sys.argv[1]
    else:
        # Default to last 7 days
        start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    
    end_date = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime('%Y-%m-%d')
    permit_type = sys.argv[3] if len(sys.argv) > 3 else None
    borough = sys.argv[4] if len(sys.argv) > 4 else None
    
    run_api_scraper(start_date, end_date, permit_type, borough)
