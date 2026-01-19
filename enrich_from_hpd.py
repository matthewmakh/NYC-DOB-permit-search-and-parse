#!/usr/bin/env python3
"""
Enrich permits with HPD Registration data.

HPD (Housing Preservation & Development) requires building owners to register.
This script pulls owner/agent contact info from HPD and links it to permits.

Data sources:
- HPD Registrations: https://data.cityofnewyork.us/resource/tesw-yqqr.json
- HPD Contacts: https://data.cityofnewyork.us/resource/feu5-w2e2.json

Note: HPD public API does NOT include phone numbers, but provides:
- Owner/corporation names
- Business addresses
- Managing agent info
- Head officer names
"""

import os
import sys
import argparse
import requests
from datetime import datetime
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor

load_dotenv()

# Database config - requires DATABASE_URL environment variable
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError('DATABASE_URL environment variable is required')

# HPD API endpoints
HPD_REGISTRATIONS_URL = "https://data.cityofnewyork.us/resource/tesw-yqqr.json"
HPD_CONTACTS_URL = "https://data.cityofnewyork.us/resource/feu5-w2e2.json"

# Batch sizes
API_BATCH_SIZE = 50000
DB_BATCH_SIZE = 1000


def get_session():
    """Create session with retry logic"""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


def fetch_hpd_registrations_for_bbls(bbls: set, session) -> dict:
    """Fetch HPD registrations for a list of BBLs. Returns {bbl: registration_info}"""
    if not bbls:
        return {}
    
    # HPD uses separate boro/block/lot, so we need to parse BBLs
    # BBL format: BBBBBLLLL (1 digit boro, 5 digit block, 4 digit lot)
    
    # Group BBLs by boro for efficient querying
    bbls_by_boro = {}  # {boro: set of bbls}
    for bbl in bbls:
        if len(bbl) == 10:
            boro = bbl[0]
            if boro not in bbls_by_boro:
                bbls_by_boro[boro] = set()
            bbls_by_boro[boro].add(bbl)
    
    registrations = {}
    
    # Query each borough (5 total) and filter locally
    for boro, boro_bbls in bbls_by_boro.items():
        print(f"  Fetching HPD registrations for borough {boro} ({len(boro_bbls)} BBLs to match)...")
        
        offset = 0
        while True:
            params = {
                '$limit': API_BATCH_SIZE,
                '$offset': offset,
                '$where': f"boroid = '{boro}'",
                '$select': 'registrationid, boroid, block, lot, bin, streetname, housenumber, lastregistrationdate',
            }
            
            try:
                r = session.get(HPD_REGISTRATIONS_URL, params=params, timeout=120)
                r.raise_for_status()
                data = r.json()
                
                if not data:
                    break
                
                for reg in data:
                    # Build BBL from HPD fields
                    boro_id = str(reg.get('boroid', ''))
                    block_str = str(reg.get('block', '')).zfill(5)
                    lot_str = str(reg.get('lot', '')).zfill(4)
                    bbl = f"{boro_id}{block_str}{lot_str}"
                    
                    if bbl in boro_bbls:
                        registrations[bbl] = {
                            'registration_id': reg.get('registrationid'),
                            'bin': reg.get('bin'),
                            'address': f"{reg.get('housenumber', '')} {reg.get('streetname', '')}".strip(),
                            'last_registration_date': reg.get('lastregistrationdate'),
                        }
                
                print(f"    Fetched {offset + len(data):,} records, matched {len([b for b in registrations if b in boro_bbls])} BBLs...")
                
                if len(data) < API_BATCH_SIZE:
                    break
                offset += API_BATCH_SIZE
                
            except Exception as e:
                print(f"  ⚠️  Error fetching registrations for boro={boro}: {e}")
                break
    
    return registrations


def fetch_hpd_contacts_for_registrations(registration_ids: list, session) -> dict:
    """Fetch HPD contacts for registration IDs. Returns {registration_id: [contacts]}"""
    if not registration_ids:
        return {}
    
    contacts_by_reg = {}
    
    # Batch registration IDs for API queries (Socrata has query limits)
    batch_size = 100
    for i in range(0, len(registration_ids), batch_size):
        batch = registration_ids[i:i + batch_size]
        reg_id_list = ','.join(str(r) for r in batch)
        
        params = {
            '$limit': API_BATCH_SIZE,
            '$where': f"registrationid IN ({reg_id_list})",
        }
        
        try:
            r = session.get(HPD_CONTACTS_URL, params=params, timeout=60)
            r.raise_for_status()
            data = r.json()
            
            for contact in data:
                reg_id = contact.get('registrationid')
                if reg_id not in contacts_by_reg:
                    contacts_by_reg[reg_id] = []
                contacts_by_reg[reg_id].append({
                    'type': contact.get('type'),
                    'corporation_name': contact.get('corporationname'),
                    'first_name': contact.get('firstname'),
                    'last_name': contact.get('lastname'),
                    'title': contact.get('title'),
                    'business_address': f"{contact.get('businesshousenumber', '')} {contact.get('businessstreetname', '')}".strip(),
                    'business_city': contact.get('businesscity'),
                    'business_state': contact.get('businessstate'),
                    'business_zip': contact.get('businesszip'),
                })
            
            print(f"  Fetched contacts for batch {i//batch_size + 1}/{(len(registration_ids) + batch_size - 1)//batch_size}, found {len(data)} contacts...")
            
        except Exception as e:
            print(f"  ⚠️  Error fetching contacts for batch {i//batch_size + 1}: {e}")
    
    return contacts_by_reg


def get_owner_from_contacts(contacts: list) -> dict:
    """Extract primary owner info from HPD contacts list"""
    if not contacts:
        return None
    
    # Priority: CorporateOwner > IndividualOwner > HeadOfficer > Agent
    priority = ['CorporateOwner', 'IndividualOwner', 'HeadOfficer', 'Agent']
    
    for contact_type in priority:
        for contact in contacts:
            if contact.get('type') == contact_type:
                name = contact.get('corporation_name') or f"{contact.get('first_name', '')} {contact.get('last_name', '')}".strip()
                return {
                    'name': name or None,
                    'first_name': contact.get('first_name'),
                    'last_name': contact.get('last_name'),
                    'business_name': contact.get('corporation_name'),
                    'address': contact.get('business_address'),
                    'city': contact.get('business_city'),
                    'state': contact.get('business_state'),
                    'zip': contact.get('business_zip'),
                    'type': contact_type,
                }
    
    return None


def enrich_permits_from_hpd(dry_run=False, limit=None):
    """Main function to enrich permits with HPD data"""
    
    print("=" * 80)
    print("ENRICH PERMITS FROM HPD REGISTRATIONS")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Connect to database
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Find DOB NOW permits missing owner info
    print("Step 1: Finding permits missing owner contact info...")
    
    query = """
        SELECT DISTINCT p.id, p.permit_no, p.bbl, p.address, p.owner_business_name
        FROM permits p
        LEFT JOIN permit_contacts pc ON p.id = pc.permit_id AND pc.contact_role = 'Owner'
        WHERE p.api_source IN ('dob_now_filings', 'dob_now_approved')
        AND p.bbl IS NOT NULL
        AND p.bbl ~ '^[0-9]{10}$'
        AND pc.id IS NULL
    """
    if limit:
        query += f" LIMIT {limit}"
    
    cur.execute(query)
    permits = cur.fetchall()
    
    print(f"  Found {len(permits):,} DOB NOW permits without owner contacts")
    if not permits:
        print("  ✅ Nothing to enrich!")
        return
    
    # Get unique BBLs
    bbl_to_permits = {}
    for p in permits:
        bbl = p['bbl']
        if bbl not in bbl_to_permits:
            bbl_to_permits[bbl] = []
        bbl_to_permits[bbl].append(p)
    
    print(f"  Unique BBLs to look up: {len(bbl_to_permits):,}")
    print()
    
    # Fetch HPD registrations
    print("Step 2: Fetching HPD registrations...")
    session = get_session()
    registrations = fetch_hpd_registrations_for_bbls(set(bbl_to_permits.keys()), session)
    print(f"  Matched {len(registrations):,} BBLs to HPD registrations")
    print()
    
    if not registrations:
        print("  ⚠️  No HPD registration matches found")
        return
    
    # Fetch HPD contacts
    print("Step 3: Fetching HPD contacts...")
    reg_ids = [r['registration_id'] for r in registrations.values()]
    contacts_by_reg = fetch_hpd_contacts_for_registrations(reg_ids, session)
    print(f"  Found contacts for {len(contacts_by_reg):,} registrations")
    print()
    
    # Link contacts to permits
    print("Step 4: Creating owner contacts from HPD data...")
    
    permits_updated = 0
    
    for bbl, reg_info in registrations.items():
        reg_id = reg_info['registration_id']
        contacts = contacts_by_reg.get(reg_id, [])
        owner_info = get_owner_from_contacts(contacts)
        
        if not owner_info or not owner_info.get('name'):
            continue
        
        # Update permits with HPD owner info (stored in permits table, not contacts)
        # Since HPD doesn't have phone numbers, we can't create proper contacts
        if not dry_run:
            for permit in bbl_to_permits.get(bbl, []):
                try:
                    cur.execute("""
                        UPDATE permits SET
                            owner_business_name = COALESCE(owner_business_name, %s),
                            owner_first_name = COALESCE(owner_first_name, %s),
                            owner_last_name = COALESCE(owner_last_name, %s),
                            owner_street_name = COALESCE(owner_street_name, %s),
                            owner_city = COALESCE(owner_city, %s),
                            owner_state = COALESCE(owner_state, %s),
                            owner_zip_code = COALESCE(owner_zip_code, %s)
                        WHERE id = %s
                        AND (owner_business_name IS NULL OR owner_business_name = '')
                    """, (
                        owner_info.get('business_name'),
                        owner_info.get('first_name'),
                        owner_info.get('last_name'),
                        owner_info.get('address'),
                        owner_info.get('city'),
                        owner_info.get('state'),
                        owner_info.get('zip'),
                        permit['id']
                    ))
                    if cur.rowcount > 0:
                        permits_updated += 1
                except Exception as e:
                    print(f"  ⚠️  Error updating permit {permit['permit_no']}: {e}")
        else:
            # Dry run - just count
            for permit in bbl_to_permits.get(bbl, []):
                permits_updated += 1
    
    if not dry_run:
        conn.commit()
    
    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"  Permits updated with HPD owner info: {permits_updated:,}")
    if dry_run:
        print("  (DRY RUN - no changes made)")
    print()
    
    cur.close()
    conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Enrich permits with HPD registration data')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without making changes')
    parser.add_argument('--limit', type=int, help='Limit number of permits to process')
    
    args = parser.parse_args()
    
    enrich_permits_from_hpd(dry_run=args.dry_run, limit=args.limit)
