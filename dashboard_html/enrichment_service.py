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
    """
    if not full_name:
        return None, None, None
    
    # Clean up the name
    name = full_name.strip().upper()
    
    # Remove common suffixes/prefixes
    for suffix in ['LLC', 'INC', 'CORP', 'LTD', 'CO', 'COMPANY', 'TRUST', 'ESTATE']:
        if suffix in name:
            return None, None, None  # It's a business, not a person
    
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
            # Already has data - check if user has access
            cur.execute("""
                SELECT id FROM user_enrichments 
                WHERE user_id = %s AND building_id = %s
            """, (user_id, building_id))
            
            if cur.fetchone():
                # User already paid for this
                return True, {
                    'phones': building['enriched_phones'],
                    'emails': building['enriched_emails'],
                    'already_had_access': True
                }, "Data already unlocked"
            else:
                # Data exists but user hasn't paid - just record access, charge already happened
                cur.execute("""
                    INSERT INTO user_enrichments (user_id, building_id, owner_name_searched)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, building_id) DO NOTHING
                """, (user_id, building_id, owner_name))
                conn.commit()
                
                return True, {
                    'phones': building['enriched_phones'],
                    'emails': building['enriched_emails'],
                    'from_cache': True
                }, "Data retrieved from cache"
        
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
        
        # Store in database
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
        
        # Record user access
        cur.execute("""
            INSERT INTO user_enrichments (user_id, building_id, owner_name_searched)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, building_id) DO NOTHING
        """, (user_id, building_id, owner_name))
        
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
    Returns: (has_access, enrichment_data, enriched_owner_names)
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Get list of owner names already enriched for this building by this user
        cur.execute("""
            SELECT owner_name_searched FROM user_enrichments
            WHERE user_id = %s AND building_id = %s
        """, (user_id, building_id))
        enriched_owners = [r['owner_name_searched'].upper() for r in cur.fetchall() if r['owner_name_searched']]
        
        # Check if user is admin
        cur.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        is_admin = user and user['is_admin']
        
        # Get building's enrichment data
        cur.execute("""
            SELECT enriched_phones, enriched_emails FROM buildings WHERE id = %s
        """, (building_id,))
        building = cur.fetchone()
        
        # If checking specific owner
        if owner_name:
            already_enriched = owner_name.upper() in enriched_owners
            if already_enriched and building and building['enriched_phones']:
                return True, {
                    'phones': building['enriched_phones'],
                    'emails': building['enriched_emails']
                }, enriched_owners
            return False, None, enriched_owners
        
        # General check - has any enrichment
        if is_admin:
            if building and building['enriched_phones']:
                return True, {
                    'phones': building['enriched_phones'],
                    'emails': building['enriched_emails']
                }, enriched_owners
            return False, None, enriched_owners
        
        # For regular users, check if they have any enrichment on this building
        if len(enriched_owners) > 0 and building and building['enriched_phones']:
            return True, {
                'phones': building['enriched_phones'],
                'emails': building['enriched_emails']
            }, enriched_owners
        
        return False, None, enriched_owners
        
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
