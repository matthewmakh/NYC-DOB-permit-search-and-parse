"""
Enrichment Service
Handles contact enrichment API calls and storing results
Supports: Enformion (primary) and Apify TruePeopleSearch (fallback)
"""

import os
import re
import requests
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import time
from nameparser import HumanName

# Enformion API Configuration (Primary)
ENFORMION_API_URL = 'https://devapi.enformion.com/Contact/EnrichPlus'
ENFORMION_AP_NAME = os.getenv('ENFORMION_AP_NAME')
ENFORMION_AP_PASSWORD = os.getenv('ENFORMION_AP_PASSWORD')

# Apify TruePeopleSearch Configuration (Fallback)
APIFY_API_TOKEN = os.getenv('APIFY_API_TOKEN')
APIFY_ACTOR_ID = 'vmf6h5lxPAkB1W2gT'  # Skip Trace / TruePeopleSearch actor

# ---------------------------------------------------------------------------
# Enrichment provider selection + pricing
# ---------------------------------------------------------------------------
# Customer rate is what we bill the end user, regardless of which underlying
# provider we use. Provider cost is what we actually pay the data vendor —
# only shown to admins so they can monitor margin.
PROVIDER_ENFORMION = 'enformion'
PROVIDER_APIFY = 'apify'
PROVIDER_ENFORMION_FALLBACK = 'enformion_fallback'  # try Enformion, fall back to Apify
VALID_PROVIDERS = (PROVIDER_ENFORMION, PROVIDER_APIFY, PROVIDER_ENFORMION_FALLBACK)

# Customer-facing batch rate (same regardless of which provider runs the lookup)
CUSTOMER_COST_PER_LOOKUP = 0.35
CUSTOMER_MIN_CHARGE = 0.50  # Stripe minimum

# Provider's actual per-lookup cost — overridable via env vars so we don't
# need a code push if vendor pricing changes.
ENFORMION_PROVIDER_COST = float(os.getenv('ENFORMION_COST_PER_LOOKUP', '0.35'))
APIFY_PROVIDER_COST = float(os.getenv('APIFY_COST_PER_LOOKUP', '0.02'))


def provider_real_cost_per_lookup(provider):
    """Return the actual per-lookup cost we pay the upstream vendor for the given
    provider mode. For the fallback mode we conservatively report the worst case
    (Enformion's higher cost) since we can't know ahead of time which one a given
    lookup will hit."""
    if provider == PROVIDER_APIFY:
        return APIFY_PROVIDER_COST
    # enformion or enformion_fallback both potentially hit Enformion
    return ENFORMION_PROVIDER_COST


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


# Business-entity rejection lists. Tuned for NYC government data quirks
# (e.g. "CHURCH" is allowed as a last name when in "CHURCH, CHARLOTTE" form).
# Kept here because generic name-parser libraries don't know about these.
_STRONG_BUSINESS_INDICATORS = frozenset([
    'LLC', 'INC', 'CORP', 'LTD', 'CO', 'LP', 'LLP', 'PLLC', 'PC',
    'COMPANY', 'PROPERTIES', 'REALTY', 'HOLDINGS', 'ENTERPRISES',
    'INVESTMENTS', 'DEVELOPMENT', 'MANAGEMENT', 'FOUNDATION',
])
_WEAK_BUSINESS_INDICATORS = frozenset([
    'TRUST', 'ESTATE', 'ASSOCIATES', 'PARTNERS', 'PARTNERSHIP',
    'GROUP', 'CAPITAL', 'FUND',
    'HOUSING', 'AUTHORITY', 'BANK', 'GRID', 'EDISON', 'UTILITY',
    'CITY', 'STATE', 'COUNTY', 'FEDERAL', 'MUNICIPAL', 'NATIONAL',
    'CHURCH', 'TEMPLE', 'SYNAGOGUE', 'MOSQUE', 'CONGREGATION',
    'SCHOOL', 'UNIVERSITY', 'COLLEGE', 'HOSPITAL', 'MEDICAL',
    'ASSOCIATION', 'SOCIETY', 'CLUB', 'ORGANIZATION', 'COMMITTEE',
])


def is_business_entity(full_name):
    """True if the name looks like a company/government/etc. rather than a
    person. Runs before HumanName since name-parser libraries don't know
    NYC-specific quirks like 'CHURCH' being a valid last name."""
    if not full_name:
        return True
    name = full_name.strip().upper()
    if '&' in name:
        return True
    for prefix in ('CITY OF', 'STATE OF', 'COUNTY OF',
                   'BANK OF', 'HEIRS OF', 'ESTATE OF'):
        if name.startswith(prefix):
            return True
    words = name.replace(',', ' ').replace('.', ' ').split()
    # "LASTNAME, FIRSTNAME" with only 2 tokens — the first is a last name
    # even if it matches a weak indicator (e.g. "CHURCH, CHARLOTTE").
    is_lastname_firstname = ',' in name and len(words) == 2
    for w in words:
        if w in _STRONG_BUSINESS_INDICATORS:
            return True
    if not is_lastname_firstname:
        for w in words:
            if w in _WEAK_BUSINESS_INDICATORS:
                return True
    return False


def parse_owner_name(full_name):
    """Parse a full name into (first, middle, last). Returns (None, None, None)
    for businesses/entities or unparseable input.

    Uses python-nameparser (HumanName) for the structural parse, which
    correctly handles suffix tokens (JR/SR/III), multi-word last names
    ('VAN DER BERG'), hyphenated last names, periods on initials, etc.
    Our own business-entity filter runs first since HumanName has no concept
    of LLC/Corp/Authority.

    Values are returned uppercased to match the legacy contract (callers
    pass them to address-matching code that assumes upper-case)."""
    if not full_name or is_business_entity(full_name):
        return None, None, None

    h = HumanName(full_name)
    first = (h.first or '').strip().upper().rstrip('.') or None
    middle = (h.middle or '').strip().upper().rstrip('.') or None
    last = (h.last or '').strip().upper() or None

    # Need both ends to be a person. "BOB" alone, or "LLC" remnants, get
    # rejected here.
    if not first or not last:
        return None, None, None

    return first, middle, last


# NYC address parser. Works backwards from the ZIP, which is the most reliable
# anchor — a 5-digit number at the end of the string. The previous parser
# split on whitespace and grabbed parts[0]/parts[1]/parts[2] as city/state/zip,
# which corrupted multi-word boroughs ("NEW YORK" became city="NEW", state="YORK").
_ZIP_TAIL_RE = re.compile(r'\b(\d{5})(?:-\d{4})?\s*$')
_STATE_TAIL_RE = re.compile(r'\b([A-Z]{2})\s*$')


def parse_nyc_address(address):
    """Parse a full street address into (street, city, state, zip).

    Handles multi-word boroughs (NEW YORK, STATEN ISLAND), apartment lines
    embedded as extra comma segments, missing components, and the common
    "STREET CITY ST ZIP" no-comma form. Any missing piece is returned as
    None — callers should default reasonably (state='NY' for NYC data).
    """
    if not address:
        return None, None, None, None
    s = address.strip().upper()

    zipcode = None
    m = _ZIP_TAIL_RE.search(s)
    if m:
        zipcode = m.group(1)
        s = s[:m.start()].rstrip(' ,')

    # Only treat a trailing 2-letter token as a state if we already found a
    # ZIP — otherwise common street suffixes like "ST", "RD", "DR" get
    # misparsed as state codes ('123 main st' -> state='ST').
    state = None
    if zipcode:
        m = _STATE_TAIL_RE.search(s)
        if m:
            state = m.group(1)
            s = s[:m.start()].rstrip(' ,')

    parts = [p.strip() for p in s.split(',') if p.strip()]
    if len(parts) >= 2:
        # Treat the LAST comma segment as city; everything before is street
        # (possibly with apartment/unit info). Handles both
        # "123 MAIN ST, BROOKLYN" and "123 MAIN ST, APT 4, BROOKLYN".
        city = parts[-1]
        street = ', '.join(parts[:-1])
    elif len(parts) == 1:
        # No comma — assume the whole thing is the street, no city info.
        street, city = parts[0], None
    else:
        street, city = None, None

    return street, city, state, zipcode


def _parse_with_suffix(full_name):
    """Variant of parse_owner_name that also returns the suffix (JR/SR/III).
    Used by canonical_name_key so suffix can participate in dedup."""
    if not full_name or is_business_entity(full_name):
        return None
    h = HumanName(full_name)
    first = (h.first or '').strip().upper().rstrip('.')
    last = (h.last or '').strip().upper()
    if not first or not last:
        return None
    middle = (h.middle or '').strip().upper().rstrip('.')
    suffix = (h.suffix or '').strip().upper().rstrip('.').rstrip(',')
    return first, middle, last, suffix


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


def _build_apify_name_query(first_name, last_name, middle_name=None, suffix=None,
                             city=None, state=None, zipcode=None):
    """Build the name-search string the actor expects.

    Format: "FIRST [MIDDLE] LAST [SUFFIX]; [CITY,] STATE [ZIP]"
    The actor recognizes this as the "Name+CityStateZip Search" mode and
    matches on TPS's name index. Including the middle name dramatically
    reduces false-positive matches for common first/last combinations.
    """
    name_parts = [first_name]
    if middle_name:
        name_parts.append(middle_name)
    name_parts.append(last_name)
    if suffix:
        name_parts.append(suffix)
    full_name = ' '.join(p for p in name_parts if p).strip()

    location_parts = []
    if city:
        location_parts.append(city)
    location = ', '.join(location_parts)
    state_zip = ' '.join(p for p in [state or 'NY', zipcode] if p)
    location = f"{location}, {state_zip}" if location else state_zip

    return f"{full_name}; {location}".strip()


def _build_apify_address_query(street_address, city=None, state=None, zipcode=None):
    """Build the street-address search string. Format:
    "STREET; CITY, STATE ZIP" — actor interprets as "Street+CityStateZip Search"
    and returns the person currently associated with that address. Often more
    accurate than a name search for common names like 'JOHN SMITH', since the
    property address is unique to one occupant."""
    if not street_address:
        return None
    location_parts = []
    if city:
        location_parts.append(city)
    location = ', '.join(location_parts)
    state_zip = ' '.join(p for p in [state or 'NY', zipcode] if p)
    location = f"{location}, {state_zip}" if location else state_zip
    return f"{street_address}; {location}".strip()


# Apify's run-sync-get-dataset-items endpoint runs the actor and returns the
# dataset in a single blocking HTTP call. Replaces the old 3-step
# start-run -> poll-status -> fetch-dataset dance, which leaked run state on
# crashes and had a brittle 60s polling loop.
APIFY_SYNC_TIMEOUT_SECONDS = 180


def call_apify_truepeoplesearch(first_name, last_name, middle_name=None, suffix=None,
                                 street_address=None, city=None, state=None, zipcode=None,
                                 max_results=5, include_address_fallback=True):
    """Call the Apify TruePeopleSearch actor via the run-sync endpoint.

    Builds a name-based search ('JOHN R SMITH JR; NEW YORK, NY 10001') as the
    primary query. If `include_address_fallback` is True and we have a street
    address, also includes an address-based query so the actor can return
    whoever lives at that property — useful when the name search returns the
    wrong John Smith.

    Returns (success, response_data, error). `response_data` is the BEST
    single item picked from the dataset (preferring items with phones), with
    the full dataset accessible via `_apify_all_results` for raw storage.
    """
    if not APIFY_API_TOKEN:
        return False, None, "Apify API token not configured"

    queries = []
    primary = _build_apify_name_query(first_name, last_name, middle_name, suffix,
                                       city, state, zipcode)
    if primary:
        queries.append(primary)
    if include_address_fallback:
        address_q = _build_apify_address_query(street_address, city, state, zipcode)
        if address_q:
            queries.append(address_q)

    if not queries:
        return False, None, "No usable search input (no name and no address)"

    run_input = {
        'name': queries,
        'max_results': max_results,
    }

    print(f"Apify TruePeopleSearch call: {len(queries)} queries, max_results={max_results}")
    for q in queries:
        print(f"  query: {q!r}")

    # One blocking call. timeout_secs in the URL is the actor-side ceiling;
    # the requests timeout is a hair higher so the HTTP layer doesn't bail
    # before the actor finishes.
    url = (f"https://api.apify.com/v2/acts/{APIFY_ACTOR_ID}"
           f"/run-sync-get-dataset-items?token={APIFY_API_TOKEN}"
           f"&timeout={APIFY_SYNC_TIMEOUT_SECONDS}")

    try:
        response = requests.post(
            url,
            json=run_input,
            headers={'Content-Type': 'application/json'},
            timeout=APIFY_SYNC_TIMEOUT_SECONDS + 15,
        )
    except requests.Timeout:
        return False, None, "Apify API request timed out"
    except Exception as e:
        print(f"Apify exception: {e}")
        return False, None, str(e)

    if response.status_code not in (200, 201):
        return False, None, (
            f"Apify error {response.status_code}: {response.text[:200]}"
        )

    try:
        items = response.json()
    except ValueError:
        return False, None, "Apify returned non-JSON response"

    if not isinstance(items, list):
        return False, None, f"Unexpected Apify response shape: {type(items).__name__}"
    if not items:
        return False, None, "No results found"

    # Schema-drift sanity check — if the response has none of the keys we
    # expect, the actor probably changed its output shape and we'd silently
    # return zero phones. Surface that loudly so we notice in logs.
    expected_keys = {'Phone-1', 'Email-1', 'Person Link', 'First Name', 'Last Name'}
    first_keys = set(items[0].keys()) if isinstance(items[0], dict) else set()
    if not (expected_keys & first_keys):
        print(f"WARNING: Apify response missing all expected keys. "
              f"Got: {sorted(first_keys)[:20]}")

    best = _pick_best_apify_item(items)
    if best is None:
        return False, None, "No usable result in Apify response"

    # Attach the full result list so the caller can persist it raw without
    # re-fetching. Use a leading underscore so it's clearly internal.
    best['_apify_all_results'] = items
    best['_apify_query_input'] = run_input
    return True, best, None


def _pick_best_apify_item(items):
    """Choose the most useful item from up to N actor results.

    Preference: item with phones > item with emails > first item. When two
    items have phones, prefer the one whose Phone-1 has a more recent
    'Last Reported' date (TPS surfaces dates like 'Last reported Apr 2026').
    """
    if not items:
        return None

    def _has_phone(it):
        return bool(it.get('Phone-1'))

    def _has_email(it):
        return bool(it.get('Email-1'))

    phoned = [i for i in items if _has_phone(i)]
    if phoned:
        # Prefer most-recently reported Phone-1. The TPS date string sorts
        # lexically wrong ('Apr' < 'Dec') so we parse to YYYYMM for ordering.
        def _last_reported_score(it):
            raw = it.get('Phone-1 Last Reported', '') or ''
            return _parse_tps_date_to_yyyymm(raw)
        phoned.sort(key=_last_reported_score, reverse=True)
        return phoned[0]

    emailed = [i for i in items if _has_email(i)]
    if emailed:
        return emailed[0]

    return items[0]


_TPS_MONTH = {
    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
    'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
}


def _parse_tps_date_to_yyyymm(raw):
    """Parse 'Last reported Apr 2026' -> 202604 for sorting. Returns 0 on
    failure so missing-date items sort last."""
    if not raw:
        return 0
    parts = raw.upper().split()
    month = year = None
    for tok in parts:
        if tok[:3] in _TPS_MONTH and month is None:
            month = _TPS_MONTH[tok[:3]]
        elif tok.isdigit() and len(tok) == 4 and year is None:
            year = int(tok)
    if year is None:
        return 0
    return year * 100 + (month or 0)


def extract_apify_contact_info(api_response):
    """Extract phones and emails from one Apify TruePeopleSearch item.

    Captures each phone's full metadata — type (Wireless/Landline/VoIP),
    provider (Verizon/T-Mobile/etc.), and the 'Last reported' / 'First
    reported' dates — so the UI can show 'last seen Apr 2026' annotations
    and rank phones by recency.

    Returns: (phones_list, emails_list, person_id).
    """
    phones = []
    emails = []
    person_id = None

    if not api_response:
        return phones, emails, person_id

    try:
        person_id = api_response.get('Person Link', '') or None

        # TPS exposes 5 phone slots. Capture every populated field per slot.
        for i in range(1, 6):
            number = api_response.get(f'Phone-{i}', '')
            if not number:
                continue
            phone_type = api_response.get(f'Phone-{i} Type', '') or ''
            last_reported = api_response.get(f'Phone-{i} Last Reported', '') or ''
            first_reported = api_response.get(f'Phone-{i} First Reported', '') or ''
            provider = api_response.get(f'Phone-{i} Provider', '') or ''

            phones.append({
                'number': number,
                'type': phone_type.lower() if phone_type else 'unknown',
                'provider': provider or None,
                'last_reported': last_reported or None,
                'last_reported_yyyymm': _parse_tps_date_to_yyyymm(last_reported),
                'first_reported': first_reported or None,
                'is_valid': True,
            })

        # Rank phones with most-recent "Last reported" first. The provider's
        # original ordering is name-search relevance, but for outreach the
        # most recently seen number is what we want to dial first.
        phones.sort(key=lambda p: p.get('last_reported_yyyymm') or 0, reverse=True)

        for i in range(1, 6):
            email_addr = api_response.get(f'Email-{i}', '')
            if email_addr:
                emails.append({
                    'email': email_addr,
                    'is_valid': True,
                })

    except Exception as e:
        print(f"Error extracting Apify contact info: {e}")
        import traceback
        traceback.print_exc()

    print(f"Extracted from Apify: {len(phones)} phones, {len(emails)} emails, "
          f"person_id={person_id!r}")
    return phones, emails, person_id


def summarize_apify_match(api_response):
    """Pull the human-readable identity fields from an Apify item so we can
    show the user *who* we matched (their age, county, current address,
    relatives). Used for match-verification UI and stored alongside the raw
    response. Safe to call on partial responses — every field is optional."""
    if not api_response:
        return None
    return {
        'matched_name': ' '.join(p for p in [
            api_response.get('First Name'),
            api_response.get('Last Name'),
        ] if p) or None,
        'age': api_response.get('Age') or None,
        'born': api_response.get('Born') or None,
        'lives_in': api_response.get('Lives in') or None,
        'current_address': ', '.join(p for p in [
            api_response.get('Street Address'),
            api_response.get('Address Locality'),
            api_response.get('Address Region'),
            api_response.get('Postal Code'),
        ] if p) or None,
        'county': api_response.get('County Name') or None,
        'previous_addresses': api_response.get('Previous Addresses') or [],
        'relatives': api_response.get('Relatives') or [],
        'associates': api_response.get('Associates') or [],
        'search_option': api_response.get('Search Option') or None,
        'input_given': api_response.get('Input Given') or None,
    }


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


def enrich_owner(building_id, owner_name, address, user_id, provider=PROVIDER_ENFORMION_FALLBACK):
    """
    Perform enrichment lookup and store results.

    provider:
      - 'enformion_fallback' (default): try Enformion, fall back to Apify on failure
      - 'enformion': use Enformion only; do not fall back
      - 'apify':     use Apify only; do not try Enformion at all

    Returns: (success, data, message)
    """
    if provider not in VALID_PROVIDERS:
        provider = PROVIDER_ENFORMION_FALLBACK

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

        # Need to call API. Parse the owner name with HumanName so we can pass
        # middle name and suffix to the provider — TPS in particular benefits
        # massively from disambiguation by middle name on common surnames.
        first_name, middle_name, last_name = parse_owner_name(owner_name)
        if not first_name or not last_name:
            return False, None, "Could not parse owner name - may be a business entity"

        with_suffix = _parse_with_suffix(owner_name) or (first_name, middle_name or '', last_name, '')
        suffix = with_suffix[3] or None

        # Parse the property address robustly (handles NEW YORK / STATEN ISLAND
        # and other multi-word boroughs the old parser corrupted).
        street, city, state, zipcode = parse_nyc_address(address)
        if not state:
            state = 'NY'
        # address_line2 = "CITY, STATE ZIP" for Enformion's address line.
        # If we have no city we omit it — better than guessing "Brooklyn".
        addr_line2_parts = []
        if city:
            addr_line2_parts.append(city)
        addr_line2_parts.append(' '.join(p for p in [state, zipcode] if p))
        address_line2 = ', '.join(addr_line2_parts)

        enrichment_source = None
        api_response = None
        error = None

        if provider == PROVIDER_APIFY:
            # Skip Enformion entirely. Pass full identity (middle + suffix) AND
            # the street address so the actor can do both name and address
            # searches in one run.
            success, api_response, error = call_apify_truepeoplesearch(
                first_name=first_name, last_name=last_name,
                middle_name=middle_name, suffix=suffix,
                street_address=street, city=city, state=state, zipcode=zipcode,
            )
            if success:
                enrichment_source = 'apify_truepeoplesearch'
            else:
                return False, None, f"Apify enrichment error: {error}"
        else:
            success, api_response, error = call_enformion_api(
                first_name, last_name, "", address_line2, middle_name
            )
            if success:
                enrichment_source = 'enformion'
            elif provider == PROVIDER_ENFORMION_FALLBACK:
                print(f"Enformion failed ({error}), trying Apify TruePeopleSearch fallback...")
                success, api_response, error = call_apify_truepeoplesearch(
                    first_name=first_name, last_name=last_name,
                    middle_name=middle_name, suffix=suffix,
                    street_address=street, city=city, state=state, zipcode=zipcode,
                )
                if success:
                    enrichment_source = 'apify_truepeoplesearch'
                else:
                    return False, None, f"Enrichment API error: {error}"
            else:
                return False, None, f"Enformion enrichment error: {error}"

        # Extract contact info based on source.
        if enrichment_source == 'apify_truepeoplesearch':
            phones, emails, person_id = extract_apify_contact_info(api_response)
            match_summary = summarize_apify_match(api_response)
        else:
            phones, emails, person_id = extract_contact_info(api_response)
            match_summary = None

        if not phones and not emails:
            api_message = api_response.get('message', '') if isinstance(api_response, dict) else ''
            if 'no' in api_message.lower() and 'match' in api_message.lower():
                return False, None, "No matching records found in our database for this person. They may not be in our data sources."
            return False, None, "No contact information (phone/email) found for this person in our database."

        # Detach the internal extras we attached in call_apify_truepeoplesearch.
        # CRITICAL: api_response IS one of the items in _apify_all_results
        # (same dict reference, not a copy). Leaving the back-pointer in
        # creates a JSON-fatal cycle:
        #   stored_response -> _all_results -> items[k] -> _apify_all_results
        #                   -> items -> items[k] -> ...
        # POPPING from api_response simultaneously removes the back-pointer
        # from items[k] (same object), breaking the cycle in one shot.
        if isinstance(api_response, dict):
            all_results = api_response.pop('_apify_all_results', [])
            query_input = api_response.pop('_apify_query_input', {})
            stored_response = dict(api_response)  # shallow copy, now clean
            stored_response['_all_results'] = all_results
            stored_response['_query_input'] = query_input
            stored_response['_match_summary'] = match_summary
        else:
            stored_response = api_response

        # Store in buildings table (for backward compatibility / quick access).
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
            json.dumps(stored_response),
            building_id,
        ))

        # Record user access WITH the enrichment data (for per-owner display).
        # The raw_api_response column lets us backfill new fields (age,
        # relatives, etc.) later without re-paying for the lookup.
        cur.execute("""
            INSERT INTO user_enrichments (user_id, building_id, owner_name_searched,
                                          enriched_phones, enriched_emails,
                                          enriched_person_id, enriched_at,
                                          raw_api_response)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, building_id, owner_name_searched)
            DO UPDATE SET
                enriched_phones = EXCLUDED.enriched_phones,
                enriched_emails = EXCLUDED.enriched_emails,
                enriched_person_id = EXCLUDED.enriched_person_id,
                enriched_at = EXCLUDED.enriched_at,
                raw_api_response = EXCLUDED.raw_api_response
        """, (user_id, building_id, owner_name,
              json.dumps(phones), json.dumps(emails),
              person_id, datetime.now(),
              json.dumps(stored_response)))
        
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


# ---------------------------------------------------------------------------
# Owner dedup + SOS agent helpers
# ---------------------------------------------------------------------------
# Service-of-Process and Registered Agents are NOT property owners — they're
# the people designated to receive lawsuits or government mail for an LLC.
# Enriching them gives us the agent's phone, which is useless for outreach to
# the real owner. We detect them via the sos_principal_title column populated
# by step5_enrich_from_sos.py.
SOS_AGENT_TITLES = frozenset({'SERVICE OF PROCESS AGENT', 'REGISTERED AGENT'})


def is_sos_agent_title(title):
    """True if an SOS principal title indicates a registered/SoP agent rather
    than an owner (CEO, director, etc.)."""
    if not title:
        return False
    return title.strip().upper() in SOS_AGENT_TITLES


def canonical_name_key(name):
    """Return (FIRST, MIDDLE, LAST, SUFFIX) uppercase tuple for dedup, or None
    if `name` doesn't parse as a person.

    Middle and suffix are included so distinct people who share first+last
    aren't accidentally merged (e.g. JOHN ROBERT SMITH vs JOHN DAVID SMITH).
    The fuzziness — "missing middle = wildcard match" — lives in
    names_compatible(), not in the key itself.
    """
    return _parse_with_suffix(name)


def _middle_compatible(ma, mb):
    """True if two middle-name strings could refer to the same person."""
    if not ma or not mb:
        return True                       # missing on either side = wildcard
    if ma == mb:
        return True
    # Initial vs full: "R" matches "ROBERT" (and "R." was stripped upstream).
    if ma[0] == mb[0] and (len(ma) == 1 or len(mb) == 1):
        return True
    return False


def names_compatible(a, b):
    """Are two canonical keys plausibly the same person?

    Rules:
      - First and last must match exactly.
      - Suffix (JR/SR/III): empty on either side wildcards; otherwise must
        match exactly. JOHN SMITH JR and JOHN SMITH SR are kept distinct
        (father/son), but JOHN SMITH and JOHN SMITH JR collapse since one
        source often omits the suffix.
      - Middle: empty wildcards; full names must match; an initial matches
        any full middle starting with the same letter.
    """
    if a is None or b is None:
        return False
    fa, ma, la, sa = a
    fb, mb, lb, sb = b
    if fa != fb or la != lb:
        return False
    if sa and sb and sa != sb:
        return False
    return _middle_compatible(ma, mb)


def _name_specificity(key):
    """Higher = more informative name. Used to pick which spelling to keep
    when collapsing duplicates."""
    if key is None:
        return -1
    _f, m, _l, s = key
    score = 0
    if m:
        score += 10 + len(m)              # middle name >> none; full > initial
    if s:
        score += 5
    return score


def _dedup_add_owner(owners, keys, new_key, new_owner):
    """Append new_owner unless a compatible owner is already in the list.
    On collision, upgrade the stored entry's NAME (and key) to the more
    specific spelling, while preserving the original entry's source
    attribution, recommended flag, and already_enriched state."""
    for i, existing_key in enumerate(keys):
        if not names_compatible(new_key, existing_key):
            continue
        if _name_specificity(new_key) > _name_specificity(existing_key):
            # The new candidate has more name info (middle or suffix) — use
            # its spelling so the actual Enformion lookup is more accurate.
            # Keep the existing source/recommended fields intact.
            owners[i]['name'] = new_owner['name']
            keys[i] = new_key
        return False
    keys.append(new_key)
    owners.append(new_owner)
    return True


def get_available_owners_for_enrichment(building_id, user_id=None):
    """
    Get list of owner names that can be enriched for a building
    If user_id provided, marks which owners are already enriched
    Returns list of {name, source, recommended, already_enriched} dicts
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Pull every enrichment this user has done on this building so we can
        # flag duplicates as already_enriched. We canonicalize and use
        # names_compatible(), so "JIN PEI XIE" enriched earlier will match a
        # later "XIE, JIN PEI" or "JIN XIE".
        enriched_keys = []
        if user_id:
            cur.execute("""
                SELECT owner_name_searched FROM user_enrichments
                WHERE user_id = %s AND building_id = %s
            """, (user_id, building_id))
            for r in cur.fetchall():
                k = canonical_name_key(r['owner_name_searched'])
                if k:
                    enriched_keys.append(k)

        def _is_already_enriched(key):
            return any(names_compatible(key, ek) for ek in enriched_keys)

        cur.execute("""
            SELECT
                current_owner_name,
                owner_name_rpad,
                owner_name_hpd,
                sos_principal_name,
                sos_principal_title,
                ecb_respondent_name
            FROM buildings WHERE id = %s
        """, (building_id,))

        building = cur.fetchone()
        if not building:
            return []

        owners = []
        keys = []

        # SOS principal — but ONLY if the title says they're an actual
        # principal (CEO, director, etc.), not a Service-of-Process or
        # Registered Agent. Agents are designated mail recipients, not owners.
        sos_name = building['sos_principal_name']
        sos_title = building['sos_principal_title']
        if sos_name and not is_sos_agent_title(sos_title):
            key = canonical_name_key(sos_name)
            if key:
                is_enriched = _is_already_enriched(key)
                _dedup_add_owner(owners, keys, key, {
                    'name': sos_name,
                    'source': 'NY Secretary of State',
                    'title': sos_title,
                    'recommended': not is_enriched,
                    'reason': 'Real person behind LLC',
                    'already_enriched': is_enriched,
                })

        # Other sources, in source-priority order. Compatible duplicates
        # collapse into the SOS row above (or the first one we see).
        source_map = {
            'current_owner_name': 'NYC PLUTO Database',
            'owner_name_rpad': 'Tax Records (RPAD)',
            'owner_name_hpd': 'HPD Registration',
            'ecb_respondent_name': 'ECB Violations',
        }

        for field, source in source_map.items():
            name = building[field]
            if not name:
                continue
            key = canonical_name_key(name)
            if not key:
                continue
            _dedup_add_owner(owners, keys, key, {
                'name': name,
                'source': source,
                'recommended': False,
                'already_enriched': _is_already_enriched(key),
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


def grant_permit_contact_access(user_id, enrichment_id, charge_amount=None, stripe_charge_id=None):
    """
    Grant a user access to a permit contact enrichment.
    Call this ONLY after successful charge or for admin users.
    Returns: (success, error_message)
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            INSERT INTO user_permit_contact_unlocks (user_id, enrichment_id, charge_amount, stripe_charge_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id, enrichment_id) DO NOTHING
        """, (user_id, enrichment_id, charge_amount, stripe_charge_id))
        conn.commit()
        return True, None
    except Exception as e:
        conn.rollback()
        print(f"Error granting permit contact access: {e}")
        return False, str(e)
    finally:
        cur.close()
        conn.close()


def enrich_permit_contact(bbl, building_id, permit_id, contact_name, contact_type, 
                          license_number, license_type, original_phone, user_id, 
                          grant_access=False):
    """
    Enrich a permit contact (applicant/permittee) and store results.
    Returns: (success, data, message)
    
    Logic:
    1. Check if already enriched and user has access - return data
    2. Check if already enriched but user doesn't have access - return data (access granted separately)
    3. If not enriched, call Enformion API and store results
    
    NOTE: This function does NOT grant user access by default.
    Access should be granted by app.py ONLY after successful charge.
    Set grant_access=True for admin users or after charge succeeds.
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
            # Data exists but user hasn't paid yet
            # Return the data but don't grant access (that happens after charge)
            if grant_access:
                # Admin user or charge already succeeded - grant access now
                cur.execute("""
                    INSERT INTO user_permit_contact_unlocks (user_id, enrichment_id)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id, enrichment_id) DO NOTHING
                """, (user_id, existing_data['id']))
                conn.commit()
                return True, existing_data, "Contact unlocked"
            else:
                # Return data but mark that access still needed
                existing_data['needs_access_grant'] = True
                return True, existing_data, "Contact enriched, pending access grant"
        
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
        
        # Only grant access if explicitly requested (admin users or after charge succeeds)
        if grant_access:
            cur.execute("""
                INSERT INTO user_permit_contact_unlocks (user_id, enrichment_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id, enrichment_id) DO NOTHING
            """, (user_id, enrichment_id))
        
        conn.commit()
        
        result_data = {
            'id': enrichment_id,
            'phones': phones,
            'emails': emails,
            'person_id': person_id,
            'from_api': True
        }
        
        if not grant_access:
            result_data['needs_access_grant'] = True
        
        return True, result_data, "Contact information found"
        
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
                p.hic_license as applicant_license_number,
                p.permittee_business_name,
                p.permittee_license_number,
                p.permittee_license_type,
                p.permittee_phone,
                p.owner_business_name,
                p.owner_phone,
                p.issue_date
            FROM permits p
            WHERE p.bbl = %s
            ORDER BY p.issue_date DESC NULLS LAST
        """, (bbl,))
        
        permits = cur.fetchall()
        contacts = []
        seen_names = set()  # Track names we've already added (case-insensitive)
        
        for p in permits:
            # Applicant
            if p['applicant']:
                name = p['applicant'].strip()
                name_upper = name.upper()
                # Skip if we've already seen this name (regardless of type)
                if name_upper not in seen_names:
                    first, _, last = parse_owner_name(name)
                    is_enrichable = first and last  # Must be a person name
                    key = (name_upper, 'applicant')
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
                    seen_names.add(name_upper)
            
            # Permittee - only add if name not already seen
            if p['permittee_business_name']:
                name = p['permittee_business_name'].strip()
                name_upper = name.upper()
                if name_upper not in seen_names:
                    first, _, last = parse_owner_name(name)
                    is_enrichable = first and last
                    key = (name_upper, 'permittee')
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
                    seen_names.add(name_upper)
            
            # Owner from permit - only add if name not already seen
            if p['owner_business_name']:
                name = p['owner_business_name'].strip()
                name_upper = name.upper()
                if name_upper not in seen_names:
                    first, _, last = parse_owner_name(name)
                    is_enrichable = first and last
                    key = (name_upper, 'owner')
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
                    seen_names.add(name_upper)

        return contacts

    finally:
        cur.close()
        conn.close()


# ============================================================================
# OWNER-STRATEGY HELPERS FOR BULK BUILDING-OWNER ENRICHMENT
# ============================================================================

# Recognised owner-selection strategies for bulk enrichment.
OWNER_STRATEGY_RECOMMENDED = 'recommended'
OWNER_STRATEGY_ALL = 'all'
VALID_OWNER_STRATEGIES = (OWNER_STRATEGY_RECOMMENDED, OWNER_STRATEGY_ALL)


def filter_owners_by_strategy(owners, strategy):
    """Given the list returned by get_available_owners_for_enrichment, pick which ones
    to actually enrich based on the user's chosen strategy.

    - 'recommended': the SOS principal (real person behind an LLC) if available,
      otherwise the first non-SOS owner. Returns at most one owner.
    - 'all': returns every distinct owner unchanged.
    """
    if not owners:
        return []
    if strategy == OWNER_STRATEGY_ALL:
        return owners
    # Default to 'recommended'
    sos = next((o for o in owners if o.get('source') == 'NY Secretary of State'), None)
    if sos:
        return [sos]
    return [owners[0]]


def estimate_owners_for_buildings(building_ids, user_id, owner_strategy):
    """Lightweight estimate used to populate the bulk-enrich confirmation modal.

    Two DB queries (one for buildings, one for the user's already-enriched owners)
    instead of 2*N. For 5k buildings this matters.

    Returns: (total_owners, properties_with_owners, breakdown[], per_building_map)
    where per_building_map is {building_id: [owner dicts to enrich]}.
    """
    if not building_ids:
        return 0, 0, [], {}

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT building_id, owner_name_searched
            FROM user_enrichments
            WHERE user_id = %s AND building_id = ANY(%s)
            """,
            (user_id, list(building_ids)),
        )
        # Canonicalize each enriched name once; we keep them as lists rather
        # than sets so we can run names_compatible() against each one (which
        # is needed because middle/suffix wildcards mean two distinct keys
        # may still refer to the same person).
        enriched_by_building = {}
        for r in cur.fetchall():
            k = canonical_name_key(r['owner_name_searched'])
            if not k:
                continue
            enriched_by_building.setdefault(r['building_id'], []).append(k)

        cur.execute(
            """
            SELECT id, address,
                   current_owner_name, owner_name_rpad, owner_name_hpd,
                   sos_principal_name, sos_principal_title, ecb_respondent_name
            FROM buildings
            WHERE id = ANY(%s)
            """,
            (list(building_ids),),
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    # (field, source label, is_sos_principal) — only the SOS row needs the
    # agent-vs-principal title check.
    source_map = [
        ('sos_principal_name', 'NY Secretary of State', True),
        ('current_owner_name', 'NYC PLUTO Database', False),
        ('owner_name_rpad', 'Tax Records (RPAD)', False),
        ('owner_name_hpd', 'HPD Registration', False),
        ('ecb_respondent_name', 'ECB Violations', False),
    ]

    total_owners = 0
    properties_with_owners = 0
    breakdown = []
    per_building_map = {}

    for row in rows:
        bid = row['id']
        enriched_keys = enriched_by_building.get(bid, [])
        sos_title = row['sos_principal_title']
        owners = []
        keys = []
        for field, source, is_sos in source_map:
            name = row[field]
            if not name:
                continue
            # Skip the SOS row entirely if the title says it's an agent
            # (Service of Process / Registered Agent — not an owner).
            if is_sos and is_sos_agent_title(sos_title):
                continue
            key = canonical_name_key(name)
            if not key:
                continue
            already = any(names_compatible(key, ek) for ek in enriched_keys)
            _dedup_add_owner(owners, keys, key, {
                'name': name,
                'source': source,
                'recommended': is_sos,
                'already_enriched': already,
            })
        available = [o for o in owners if not o['already_enriched']]
        chosen = filter_owners_by_strategy(available, owner_strategy)
        if chosen:
            properties_with_owners += 1
            total_owners += len(chosen)
            per_building_map[bid] = chosen
            breakdown.append({
                'building_id': bid,
                'address': row['address'] or f"Building #{bid}",
                'owners': [o['name'] for o in chosen],
                'count': len(chosen),
            })

    return total_owners, properties_with_owners, breakdown, per_building_map