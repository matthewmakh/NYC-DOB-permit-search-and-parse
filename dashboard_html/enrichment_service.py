"""
Enformion Enrichment Service
Handles contact enrichment API calls and storing results
"""

import os
import requests
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
import json

# Enformion API Configuration
ENFORMION_API_URL = 'https://devapi.enformion.com/Contact/EnrichPlus'
ENFORMION_AP_NAME = os.getenv('ENFORMION_AP_NAME')
ENFORMION_AP_PASSWORD = os.getenv('ENFORMION_AP_PASSWORD')


def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        cursor_factory=RealDictCursor
    )


def parse_owner_name(full_name):
    """
    Parse a full name into first, middle, last
    Handles formats like "SMITH, JOHN" or "JOHN SMITH" or "JOHN T SMITH"
    Returns (None, None, None) for businesses/entities
    """
    if not full_name:
        return None, None, None
    
    # Clean up the name
    name = full_name.strip().upper()
    
    # Quick checks for obvious non-person patterns
    if '&' in name:  # Partnership: "SMITH & JONES"
        return None, None, None
    if name.startswith('CITY OF') or name.startswith('STATE OF') or name.startswith('COUNTY OF'):
        return None, None, None
    if name.startswith('BANK OF') or name.startswith('HEIRS OF') or name.startswith('ESTATE OF'):
        return None, None, None
    
    # Check for business suffixes using word boundaries
    # Split into words to avoid matching "CO" inside "FRANCO"
    words = name.replace(',', ' ').replace('.', ' ').split()
    
    # If it looks like "LASTNAME, FIRSTNAME" format with just 2 words, 
    # the first word is likely a last name (even if it matches a keyword)
    # e.g., "CHURCH, CHARLOTTE" is a person, not "ST MARKS CHURCH"
    is_lastname_firstname_format = ',' in name and len(words) == 2
    
    # Strong business indicators - always reject (legal suffixes, very unlikely last names)
    strong_indicators = [
        'LLC', 'INC', 'CORP', 'LTD', 'CO', 'LP', 'LLP', 'PLLC', 'PC',
        'COMPANY', 'PROPERTIES', 'REALTY', 'HOLDINGS', 'ENTERPRISES',
        'INVESTMENTS', 'DEVELOPMENT', 'MANAGEMENT', 'FOUNDATION'
    ]
    
    # Weak indicators - only reject if NOT in lastname,firstname format
    # These could be last names: CHURCH, BANKS, GRANT, TEMPLE, etc.
    weak_indicators = [
        'TRUST', 'ESTATE', 'ASSOCIATES', 'PARTNERS', 'PARTNERSHIP',
        'GROUP', 'CAPITAL', 'FUND',
        'HOUSING', 'AUTHORITY', 'BANK', 'GRID', 'EDISON', 'UTILITY',
        'CITY', 'STATE', 'COUNTY', 'FEDERAL', 'MUNICIPAL', 'NATIONAL',
        'CHURCH', 'TEMPLE', 'SYNAGOGUE', 'MOSQUE', 'CONGREGATION',
        'SCHOOL', 'UNIVERSITY', 'COLLEGE', 'HOSPITAL', 'MEDICAL',
        'ASSOCIATION', 'SOCIETY', 'CLUB', 'ORGANIZATION', 'COMMITTEE'
    ]
    
    # Always check strong indicators
    for word in words:
        if word in strong_indicators:
            return None, None, None
    
    # Only check weak indicators if it's NOT a simple lastname,firstname format
    if not is_lastname_firstname_format:
        for word in words:
            if word in weak_indicators:
                return None, None, None
    
    # Handle "LAST, FIRST MIDDLE" format
    if ',' in name:
        parts = name.split(',', 1)
        last_name = parts[0].strip()
        first_parts = parts[1].strip().split()
        first_name = first_parts[0] if first_parts else None
        middle_name = first_parts[1] if len(first_parts) > 1 else None
        return first_name, middle_name, last_name
    
    # Handle "FIRST MIDDLE LAST" format
    parts = name.split()
    if len(parts) == 1:
        return parts[0], None, None
    elif len(parts) == 2:
        return parts[0], None, parts[1]
    else:
        return parts[0], parts[1], parts[-1]


def call_enformion_api(first_name, last_name, address_line1=None, address_line2=None, middle_name=None):
    """
    Call Enformion Contact Enrich Plus API
    Returns: (success, response_data, error_message)
    """
    if not ENFORMION_AP_NAME or not ENFORMION_AP_PASSWORD:
        return False, None, "Enformion API credentials not configured"
    
    headers = {
        'accept': 'application/json',
        'content-type': 'application/json',
        'galaxy-ap-name': ENFORMION_AP_NAME,
        'galaxy-ap-password': ENFORMION_AP_PASSWORD,
        'galaxy-search-type': 'DevAPIContactEnrichPlus'
    }
    
    # Build proper address - Enformion needs city/state in AddressLine2
    # Default to Brooklyn, NY if no address_line2 provided
    city_state = address_line2.strip() if address_line2 else "Brooklyn, NY"
    
    # Use "Address" (singular object) NOT "Addresses" (array) - API requires this format
    payload = {
        "FirstName": first_name or "",
        "MiddleName": middle_name or "",
        "LastName": last_name or "",
        "Address": {
            "AddressLine1": address_line1 or "",
            "AddressLine2": city_state
        }
    }
    
    print(f"Enformion API call: {ENFORMION_API_URL}")
    print(f"Payload: {payload}")
    
    try:
        response = requests.post(
            ENFORMION_API_URL,
            headers=headers,
            json=payload,
            timeout=30
        )
        
        print(f"Enformion response status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"Enformion response data keys: {data.keys() if isinstance(data, dict) else 'list'}")
            return True, data, None
        else:
            print(f"Enformion error response: {response.text[:500]}")
            return False, None, f"API error: {response.status_code} - {response.text[:200]}"
            
    except requests.Timeout:
        return False, None, "API request timed out"
    except Exception as e:
        print(f"Enformion exception: {e}")
        return False, None, str(e)


def extract_contact_info(api_response):
    """
    Extract phones and emails from Enformion response
    Returns: (phones_list, emails_list, person_id)
    """
    phones = []
    emails = []
    person_id = None
    
    if not api_response:
        return phones, emails, person_id
    
    print(f"Extracting contact info from response: {str(api_response)[:500]}")
    
    # The response structure may vary - handle different formats
    try:
        # Check if there's a person result - Enformion returns lowercase keys
        person = api_response.get('person') or api_response.get('Person') or api_response
        
        if isinstance(api_response, list) and len(api_response) > 0:
            person = api_response[0]
        
        # Get person ID
        person_id = person.get('personId') or person.get('PersonId')
        
        # Get phones - Enformion uses lowercase 'phones' array
        phone_list = person.get('phones') or person.get('Phones') or []
        print(f"Found {len(phone_list)} phones")
        for phone in phone_list[:5]:  # Top 5
            phone_number = phone.get('phone') or phone.get('number') or phone.get('Phone')
            phone_type = phone.get('phoneType') or phone.get('type') or phone.get('Type') or 'Unknown'
            is_connected = phone.get('isConnected', True)
            if phone_number:
                phones.append({
                    'number': phone_number,
                    'type': phone_type,
                    'is_valid': is_connected
                })
        
        # Get emails - Enformion uses lowercase 'emails' array
        email_list = person.get('emails') or person.get('Emails') or []
        print(f"Found {len(email_list)} emails")
        for email in email_list[:5]:  # Top 5
            email_address = email.get('email') or email.get('Email') or email.get('address')
            is_validated = email.get('isValidated', True)
            if email_address:
                emails.append({
                    'email': email_address,
                    'is_valid': is_validated
                })
        
    except Exception as e:
        print(f"Error extracting contact info: {e}")
        import traceback
        traceback.print_exc()
    
    print(f"Extracted: {len(phones)} phones, {len(emails)} emails")
    return phones, emails, person_id


def enrich_owner(building_id, owner_name, address, user_id):
    """
    Perform enrichment lookup and store results
    Returns: (success, data, message)
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Check if already enriched
        cur.execute("""
            SELECT enriched_phones, enriched_emails, enriched_at
            FROM buildings WHERE id = %s
        """, (building_id,))
        
        building = cur.fetchone()
        if building and building['enriched_phones']:
            # Already has data - check if THIS USER has enriched THIS SPECIFIC OWNER
            cur.execute("""
                SELECT id FROM user_enrichments 
                WHERE user_id = %s AND building_id = %s AND UPPER(owner_name_searched) = UPPER(%s)
            """, (user_id, building_id, owner_name))
            
            if cur.fetchone():
                # User already paid for this specific owner
                return True, {
                    'phones': building['enriched_phones'],
                    'emails': building['enriched_emails'],
                    'already_had_access': True
                }, "Data already unlocked"
            # Note: Even if building has cached data from another owner's lookup,
            # we still need to call API for this new owner - fall through to API call
        
        # Need to call API
        # Parse the owner name
        first_name, middle_name, last_name = parse_owner_name(owner_name)
        
        if not first_name or not last_name:
            return False, None, "Could not parse owner name - may be a business entity"
        
        # Parse address - only use city/state/zip, NOT street address (improves match rate)
        address_parts = address.split(',') if address else []
        # Skip street address (first part), use city/state/zip only
        address_line2 = ', '.join(address_parts[1:]).strip() if len(address_parts) > 1 else "Brooklyn, NY"
        
        # Call API with empty street address - just city/state/zip
        success, api_response, error = call_enformion_api(
            first_name, last_name, "", address_line2, middle_name
        )
        
        if not success:
            return False, None, f"Enrichment API error: {error}"
        
        # Extract contact info
        phones, emails, person_id = extract_contact_info(api_response)
        
        if not phones and not emails:
            # Check if the API returned a "no matches" message
            api_message = api_response.get('message', '') if isinstance(api_response, dict) else ''
            if 'no' in api_message.lower() and 'match' in api_message.lower():
                return False, None, "No matching records found in our database for this person. They may not be in our data sources."
            return False, None, "No contact information (phone/email) found for this person in our database."
        
        # Store in buildings table (for backward compatibility / quick access)
        cur.execute("""
            UPDATE buildings SET
                enriched_phones = %s,
                enriched_emails = %s,
                enriched_at = %s,
                enriched_person_id = %s,
                enriched_raw_response = %s
            WHERE id = %s
        """, (
            json.dumps(phones),
            json.dumps(emails),
            datetime.now(),
            person_id,
            json.dumps(api_response),
            building_id
        ))
        
        # Record user access WITH the enrichment data (for per-owner display)
        cur.execute("""
            INSERT INTO user_enrichments (user_id, building_id, owner_name_searched, enriched_phones, enriched_emails, enriched_person_id, enriched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, building_id, owner_name_searched) 
            DO UPDATE SET 
                enriched_phones = EXCLUDED.enriched_phones,
                enriched_emails = EXCLUDED.enriched_emails,
                enriched_person_id = EXCLUDED.enriched_person_id,
                enriched_at = EXCLUDED.enriched_at
        """, (user_id, building_id, owner_name, json.dumps(phones), json.dumps(emails), person_id, datetime.now()))
        
        conn.commit()
        
        return True, {
            'phones': phones,
            'emails': emails,
            'person_id': person_id,
            'from_api': True
        }, "Contact information found"
        
    except Exception as e:
        conn.rollback()
        print(f"Enrichment error: {e}")
        return False, None, str(e)
        
    finally:
        cur.close()
        conn.close()


def check_user_enrichment_access(user_id, building_id, owner_name=None):
    """
    Check if user has already paid for enrichment on this building
    If owner_name is provided, checks for that specific owner
    Returns: (has_access, enrichment_data_list, enriched_owner_names)
    enrichment_data_list is a list of {owner_name, phones, emails} for each enriched owner
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Get all enrichments for this building by this user WITH their data
        cur.execute("""
            SELECT owner_name_searched, enriched_phones, enriched_emails, enriched_at
            FROM user_enrichments
            WHERE user_id = %s AND building_id = %s
        """, (user_id, building_id))
        enrichments = cur.fetchall()
        
        enriched_owners = [r['owner_name_searched'].upper() for r in enrichments if r['owner_name_searched']]
        
        # Build enrichment data list with per-owner data
        enrichment_data_list = []
        for r in enrichments:
            if r['enriched_phones'] or r['enriched_emails']:
                enrichment_data_list.append({
                    'owner_name': r['owner_name_searched'],
                    'phones': r['enriched_phones'] if r['enriched_phones'] else [],
                    'emails': r['enriched_emails'] if r['enriched_emails'] else [],
                    'enriched_at': r['enriched_at']
                })
        
        # Check if user is admin
        cur.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        is_admin = user and user['is_admin']
        
        # If checking specific owner
        if owner_name:
            already_enriched = owner_name.upper() in enriched_owners
            # Find this owner's specific data
            owner_data = next((e for e in enrichment_data_list if e['owner_name'].upper() == owner_name.upper()), None)
            if already_enriched and owner_data:
                return True, enrichment_data_list, enriched_owners
            return False, enrichment_data_list, enriched_owners
        
        # General check - has any enrichment with data
        if len(enrichment_data_list) > 0:
            return True, enrichment_data_list, enriched_owners
        
        return False, enrichment_data_list, enriched_owners
        
    finally:
        cur.close()
        conn.close()


def get_available_owners_for_enrichment(building_id, user_id=None):
    """
    Get list of owner names that can be enriched for a building
    If user_id provided, marks which owners are already enriched
    Returns list of {name, source, recommended, already_enriched} dicts
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Get already enriched owners for this user
        enriched_owners = []
        if user_id:
            cur.execute("""
                SELECT owner_name_searched FROM user_enrichments
                WHERE user_id = %s AND building_id = %s
            """, (user_id, building_id))
            enriched_owners = [r['owner_name_searched'].upper() for r in cur.fetchall() if r['owner_name_searched']]
        
        cur.execute("""
            SELECT 
                current_owner_name,
                owner_name_rpad,
                owner_name_hpd,
                sos_principal_name,
                ecb_respondent_name
            FROM buildings WHERE id = %s
        """, (building_id,))
        
        building = cur.fetchone()
        if not building:
            return []
        
        owners = []
        
        # SOS Principal is recommended (real person behind LLC)
        if building['sos_principal_name']:
            first, middle, last = parse_owner_name(building['sos_principal_name'])
            if first and last:  # Only if it's a person name
                is_enriched = building['sos_principal_name'].upper() in enriched_owners
                owners.append({
                    'name': building['sos_principal_name'],
                    'source': 'NY Secretary of State',
                    'recommended': not is_enriched,  # Only recommend if not already enriched
                    'reason': 'Real person behind LLC',
                    'already_enriched': is_enriched
                })
        
        # Other sources
        source_map = {
            'current_owner_name': 'NYC PLUTO Database',
            'owner_name_rpad': 'Tax Records (RPAD)',
            'owner_name_hpd': 'HPD Registration',
            'ecb_respondent_name': 'ECB Violations'
        }
        
        for field, source in source_map.items():
            name = building[field]
            if name:
                # Check if it's a person (not LLC)
                first, middle, last = parse_owner_name(name)
                if first and last:
                    # Check if already added
                    if not any(o['name'].upper() == name.upper() for o in owners):
                        is_enriched = name.upper() in enriched_owners
                        owners.append({
                            'name': name,
                            'source': source,
                            'recommended': False,
                            'already_enriched': is_enriched
                        })
        
        return owners
        
    finally:
        cur.close()
        conn.close()

# ============================================================================
# PERMIT CONTACT ENRICHMENT FUNCTIONS
# ============================================================================

def check_permit_contact_enrichment(bbl, contact_name, contact_type, user_id=None):
    """
    Check if a permit contact has already been enriched.
    Returns: (already_enriched, enrichment_data, user_has_access)
    
    - If enriched by anyone, return the data
    - If user_id provided, check if THIS user has unlocked access
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Check if this contact has been enriched
        cur.execute("""
            SELECT id, enriched_phones, enriched_emails, first_enriched_by, first_enriched_at
            FROM permit_contact_enrichments
            WHERE bbl = %s AND UPPER(contact_name) = UPPER(%s) AND contact_type = %s
        """, (bbl, contact_name, contact_type))
        
        enrichment = cur.fetchone()
        
        if not enrichment:
            return False, None, False
        
        # Check if this user has unlocked access
        user_has_access = False
        if user_id:
            # First enricher always has access
            if enrichment['first_enriched_by'] == user_id:
                user_has_access = True
            else:
                # Check if they paid to unlock
                cur.execute("""
                    SELECT id FROM user_permit_contact_unlocks
                    WHERE user_id = %s AND enrichment_id = %s
                """, (user_id, enrichment['id']))
                user_has_access = cur.fetchone() is not None
            
            # Check if admin (always has access)
            if not user_has_access:
                cur.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
                user = cur.fetchone()
                user_has_access = user and user['is_admin']
        
        enrichment_data = {
            'id': enrichment['id'],
            'phones': enrichment['enriched_phones'] or [],
            'emails': enrichment['enriched_emails'] or [],
            'enriched_at': str(enrichment['first_enriched_at']) if enrichment['first_enriched_at'] else None
        }
        
        return True, enrichment_data, user_has_access
        
    finally:
        cur.close()
        conn.close()


def enrich_permit_contact(bbl, building_id, permit_id, contact_name, contact_type, 
                          license_number, license_type, original_phone, user_id):
    """
    Enrich a permit contact (applicant/permittee) and store results.
    Returns: (success, data, message)
    
    Logic:
    1. Check if already enriched - if yes, just grant user access
    2. If not enriched, call Enformion API
    3. Store enrichment data
    4. Grant user access
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Check if already enriched
        already_enriched, existing_data, user_has_access = check_permit_contact_enrichment(
            bbl, contact_name, contact_type, user_id
        )
        
        if already_enriched and user_has_access:
            return True, existing_data, "Contact already unlocked"
        
        if already_enriched and existing_data:
            # Data exists but user hasn't paid - grant access
            cur.execute("""
                INSERT INTO user_permit_contact_unlocks (user_id, enrichment_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id, enrichment_id) DO NOTHING
            """, (user_id, existing_data['id']))
            conn.commit()
            return True, existing_data, "Contact unlocked"
        
        # Need to call API - parse the contact name
        first_name, middle_name, last_name = parse_owner_name(contact_name)
        
        if not first_name or not last_name:
            return False, None, "Could not parse name - may be a business entity"
        
        # Get address from building for better match
        cur.execute("SELECT address FROM buildings WHERE bbl = %s", (bbl,))
        building = cur.fetchone()
        address = building['address'] if building else ""
        
        # Call Enformion API
        success, api_response, error = call_enformion_api(
            first_name, last_name, "", "New York, NY", middle_name
        )
        
        if not success:
            return False, None, f"Enrichment API error: {error}"
        
        # Extract contact info
        phones, emails, person_id = extract_contact_info(api_response)
        
        if not phones and not emails:
            api_message = api_response.get('message', '') if isinstance(api_response, dict) else ''
            if 'no' in api_message.lower() and 'match' in api_message.lower():
                return False, None, "No matching records found for this person."
            return False, None, "No contact information found for this person."
        
        # Store enrichment
        cur.execute("""
            INSERT INTO permit_contact_enrichments 
            (bbl, building_id, permit_id, contact_name, contact_type, license_number, 
             license_type, original_phone, enriched_phones, enriched_emails, 
             enriched_person_id, enriched_raw_response, first_enriched_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (bbl, contact_name, contact_type) 
            DO UPDATE SET 
                enriched_phones = EXCLUDED.enriched_phones,
                enriched_emails = EXCLUDED.enriched_emails,
                enriched_person_id = EXCLUDED.enriched_person_id,
                enriched_raw_response = EXCLUDED.enriched_raw_response
            RETURNING id
        """, (
            bbl, building_id, permit_id, contact_name, contact_type, license_number,
            license_type, original_phone, json.dumps(phones), json.dumps(emails),
            person_id, json.dumps(api_response), user_id
        ))
        
        enrichment_id = cur.fetchone()['id']
        
        # Grant user access
        cur.execute("""
            INSERT INTO user_permit_contact_unlocks (user_id, enrichment_id)
            VALUES (%s, %s)
            ON CONFLICT (user_id, enrichment_id) DO NOTHING
        """, (user_id, enrichment_id))
        
        conn.commit()
        
        return True, {
            'id': enrichment_id,
            'phones': phones,
            'emails': emails,
            'person_id': person_id,
            'from_api': True
        }, "Contact information found"
        
    except Exception as e:
        conn.rollback()
        print(f"Permit contact enrichment error: {e}")
        import traceback
        traceback.print_exc()
        return False, None, str(e)
        
    finally:
        cur.close()
        conn.close()


def get_enriched_contacts_for_building(bbl, user_id=None):
    """
    Get all enriched permit contacts for a building.
    Only returns contact data for contacts the user has access to.
    Returns list of contacts with enrichment data (or locked indicator)
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Check if user is admin
        is_admin = False
        if user_id:
            cur.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
            user = cur.fetchone()
            is_admin = user and user['is_admin']
        
        # Get all enrichments for this building
        cur.execute("""
            SELECT 
                pce.id,
                pce.contact_name,
                pce.contact_type,
                pce.license_number,
                pce.license_type,
                pce.original_phone,
                pce.enriched_phones,
                pce.enriched_emails,
                pce.first_enriched_by,
                pce.first_enriched_at,
                upcu.user_id as unlocked_by_user
            FROM permit_contact_enrichments pce
            LEFT JOIN user_permit_contact_unlocks upcu 
                ON pce.id = upcu.enrichment_id AND upcu.user_id = %s
            WHERE pce.bbl = %s
            ORDER BY pce.first_enriched_at DESC
        """, (user_id or 0, bbl))
        
        enrichments = cur.fetchall()
        
        contacts = []
        for e in enrichments:
            # Check if user has access
            has_access = is_admin or e['first_enriched_by'] == user_id or e['unlocked_by_user'] is not None
            
            contact = {
                'id': e['id'],
                'name': e['contact_name'],
                'type': e['contact_type'],
                'license_number': e['license_number'],
                'license_type': e['license_type'],
                'original_phone': e['original_phone'],
                'enriched': True,
                'has_access': has_access,
                'enriched_at': str(e['first_enriched_at']) if e['first_enriched_at'] else None
            }
            
            if has_access:
                contact['phones'] = e['enriched_phones'] or []
                contact['emails'] = e['enriched_emails'] or []
            else:
                # Show locked indicator
                contact['phones'] = None
                contact['emails'] = None
                contact['locked'] = True
            
            contacts.append(contact)
        
        return contacts
        
    finally:
        cur.close()
        conn.close()


def get_enrichable_permit_contacts(bbl, user_id=None):
    """
    Get list of permit contacts that can be enriched for a building.
    Returns contacts from permits that have names but may not have phone numbers.
    Marks which ones are already enriched/unlocked.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Get already enriched contacts for this building
        cur.execute("""
            SELECT UPPER(contact_name) as name, contact_type
            FROM permit_contact_enrichments
            WHERE bbl = %s
        """, (bbl,))
        enriched = {(r['name'], r['contact_type']) for r in cur.fetchall()}
        
        # Get user's unlocked contacts
        unlocked_ids = set()
        if user_id:
            cur.execute("""
                SELECT pce.id
                FROM permit_contact_enrichments pce
                LEFT JOIN user_permit_contact_unlocks upcu 
                    ON pce.id = upcu.enrichment_id AND upcu.user_id = %s
                WHERE pce.bbl = %s AND (pce.first_enriched_by = %s OR upcu.id IS NOT NULL)
            """, (user_id, bbl, user_id))
            unlocked_ids = {r['id'] for r in cur.fetchall()}
        
        # Get all contacts from permits for this building
        cur.execute("""
            SELECT DISTINCT
                p.id as permit_id,
                p.permit_no,
                p.applicant,
                p.applicant_license as applicant_license_number,
                p.permittee_business_name,
                p.permittee_license_number,
                p.permittee_license_type,
                p.permittee_phone,
                p.owner_business_name,
                p.owner_phone
            FROM permits p
            WHERE p.bbl = %s
            ORDER BY p.issue_date DESC NULLS LAST
        """, (bbl,))
        
        permits = cur.fetchall()
        contacts = []
        seen_names = set()
        
        for p in permits:
            # Applicant
            if p['applicant']:
                name = p['applicant'].strip()
                key = (name.upper(), 'applicant')
                if name.upper() not in seen_names:
                    first, _, last = parse_owner_name(name)
                    is_enrichable = first and last  # Must be a person name
                    is_enriched = key in enriched
                    
                    contacts.append({
                        'permit_id': p['permit_id'],
                        'permit_no': p['permit_no'],
                        'name': name,
                        'type': 'applicant',
                        'license_number': p['applicant_license_number'],
                        'license_type': None,
                        'existing_phone': None,
                        'is_enrichable': is_enrichable,
                        'is_enriched': is_enriched,
                        'is_unlocked': is_enriched  # Will be updated below
                    })
                    seen_names.add(name.upper())
            
            # Permittee
            if p['permittee_business_name']:
                name = p['permittee_business_name'].strip()
                key = (name.upper(), 'permittee')
                if name.upper() not in seen_names:
                    first, _, last = parse_owner_name(name)
                    is_enrichable = first and last
                    is_enriched = key in enriched
                    
                    contacts.append({
                        'permit_id': p['permit_id'],
                        'permit_no': p['permit_no'],
                        'name': name,
                        'type': 'permittee',
                        'license_number': p['permittee_license_number'],
                        'license_type': p['permittee_license_type'],
                        'existing_phone': p['permittee_phone'],
                        'is_enrichable': is_enrichable,
                        'is_enriched': is_enriched,
                        'is_unlocked': is_enriched
                    })
                    seen_names.add(name.upper())
            
            # Owner from permit
            if p['owner_business_name']:
                name = p['owner_business_name'].strip()
                key = (name.upper(), 'owner')
                if name.upper() not in seen_names:
                    first, _, last = parse_owner_name(name)
                    is_enrichable = first and last
                    is_enriched = key in enriched
                    
                    contacts.append({
                        'permit_id': p['permit_id'],
                        'permit_no': p['permit_no'],
                        'name': name,
                        'type': 'owner',
                        'license_number': None,
                        'license_type': None,
                        'existing_phone': p['owner_phone'],
                        'is_enrichable': is_enrichable,
                        'is_enriched': is_enriched,
                        'is_unlocked': is_enriched
                    })
                    seen_names.add(name.upper())
        
        return contacts
        
    finally:
        cur.close()
        conn.close()