"""
Enrichment Service
Handles contact enrichment API calls and storing results.
Supports Apify Skip Trace (default) and Enformion (fallback/legacy).
"""

import os
import re
import requests
import unicodedata
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
APIFY_ACTOR_ID = os.getenv(
    'APIFY_ACTOR_ID', 'vmf6h5lxPAkB1W2gT')  # one-api/skip-trace

# ---------------------------------------------------------------------------
# Enrichment provider selection + pricing
# ---------------------------------------------------------------------------
# Customer rate is what we bill the end user, regardless of which underlying
# provider we use. Provider cost is what we actually pay the data vendor —
# only shown to admins so they can monitor margin.
PROVIDER_ENFORMION = 'enformion'
PROVIDER_APIFY = 'apify'
PROVIDER_ENFORMION_FALLBACK = 'enformion_fallback'  # try Enformion, fall back to Apify
PROVIDER_APIFY_FALLBACK = 'apify_fallback'          # try Apify, fall back to Enformion (current default)
VALID_PROVIDERS = (PROVIDER_ENFORMION, PROVIDER_APIFY,
                   PROVIDER_ENFORMION_FALLBACK, PROVIDER_APIFY_FALLBACK)

# Default provider for new lookups. Apify (TruePeopleSearch) is now primary
# because it's ~17x cheaper per lookup and returns richer data (age, DOB,
# relatives, current+previous addresses, per-phone provider + last-reported
# dates). Enformion stays as the fallback for cases where Apify misses.
DEFAULT_PROVIDER = PROVIDER_APIFY_FALLBACK

# Customer-facing batch rate (same regardless of which provider runs the lookup)
CUSTOMER_COST_PER_LOOKUP = 0.35
CUSTOMER_MIN_CHARGE = 0.50  # Stripe minimum

# Provider's actual per-lookup cost — overridable via env vars so we don't
# need a code push if vendor pricing changes.
ENFORMION_PROVIDER_COST = float(os.getenv('ENFORMION_COST_PER_LOOKUP', '0.35'))
APIFY_PROVIDER_COST = float(os.getenv('APIFY_COST_PER_LOOKUP', '0.02'))


def provider_real_cost_per_lookup(provider):
    """Return the actual per-lookup cost we pay the upstream vendor for the given
    provider mode. For fallback modes we report the cost of the PRIMARY provider
    (the one we try first) since the fallback only runs on miss — most lookups
    will pay only the primary cost."""
    if provider in (PROVIDER_APIFY, PROVIDER_APIFY_FALLBACK):
        return APIFY_PROVIDER_COST
    # enformion or enformion_fallback both start with Enformion
    return ENFORMION_PROVIDER_COST


def get_db_connection():
    """Get database connection

    Runs inside request handling, so it must fail fast: without a connect
    timeout, a busy or restarting Postgres left every request hanging on
    this call until gunicorn killed the worker — which the platform edge
    reports as a 502. DATABASE_URL wins when set, matching app.py.
    """
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        return psycopg2.connect(
            database_url,
            connect_timeout=5,
            options='-c statement_timeout=30000',
            cursor_factory=RealDictCursor,
        )
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        connect_timeout=5,
        options='-c statement_timeout=30000',
        cursor_factory=RealDictCursor,
    )


# Business-entity rejection lists. Tuned for NYC government data quirks
# (e.g. "CHURCH" is allowed as a last name when in "CHURCH, CHARLOTTE" form).
# Kept here because generic name-parser libraries don't know about these.
_STRONG_BUSINESS_INDICATORS = frozenset([
    'LLC', 'INC', 'INCORPORATED', 'CORP', 'CORPORATION', 'LTD',
    'LIMITED', 'CO', 'COMPANY', 'LP', 'LLP', 'PLLC', 'PC', 'PLC',
    'BANCORP', 'FSB', 'TRUSTEE', 'NOMINEE',
    'COMPANY', 'PROPERTIES', 'REALTY', 'HOLDINGS', 'ENTERPRISES',
    'INVESTMENTS', 'DEVELOPMENT', 'MANAGEMENT', 'FOUNDATION', 'SERVICING',
    'FUNDING', 'MORTGAGE', 'LENDING', 'FINANCIAL', 'FINANCE',
])
_WEAK_BUSINESS_INDICATORS = frozenset([
    'TRUST', 'ESTATE', 'ASSOCIATES', 'PARTNERS', 'PARTNERSHIP',
    'GROUP', 'CAPITAL', 'FUND',
    'HOUSING', 'AUTHORITY', 'BANK', 'BANC', 'GRID', 'EDISON', 'UTILITY',
    'CITY', 'STATE', 'COUNTY', 'FEDERAL', 'MUNICIPAL', 'NATIONAL',
    'CHURCH', 'TEMPLE', 'SYNAGOGUE', 'MOSQUE', 'CONGREGATION',
    'SCHOOL', 'UNIVERSITY', 'COLLEGE', 'HOSPITAL', 'MEDICAL',
    'ASSOCIATION', 'SOCIETY', 'CLUB', 'ORGANIZATION', 'COMMITTEE',
    'DEPARTMENT', 'ADMINISTRATION', 'AGENCY', 'BOARD', 'DISTRICT',
    'CREDIT', 'SAVINGS', 'INSURANCE', 'PARTICIPATION', 'ACQUISITION',
    'SERVICES', 'CONSULTING', 'CONSTRUCTION', 'CONTRACTING', 'BUILDERS',
    'ARCHITECTS', 'ENGINEERS', 'CONTROLS', 'INSTITUTE', 'MINISTRIES',
])

_KNOWN_ORGANIZATION_NAMES = frozenset({
    'FANNIE MAE', 'FREDDIE MAC', 'GINNIE MAE',
    'MERS', 'MORTGAGE ELECTRONIC REGISTRATION SYSTEMS',
    'FEDERAL NATIONAL MORTGAGE ASSOCIATION',
    'FEDERAL HOME LOAN MORTGAGE CORPORATION',
    'SECRETARY OF HOUSING AND URBAN DEVELOPMENT',
    'WELLS FARGO', 'JPMORGAN CHASE', 'CITIBANK', 'CITIGROUP',
    'BANK OF AMERICA', 'US BANK', 'U S BANK',
})

_ORGANIZATION_PHRASES = (
    'NATIONAL ASSOCIATION', 'CREDIT UNION', 'SAVINGS BANK',
    'SAVINGS AND LOAN', 'TRUST COMPANY', 'HOME LOANS', 'LOAN SERVICING',
    'MORTGAGE SERVICING', 'MASTER PARTICIPATION', 'AS TRUSTEE',
    'SUCCESSOR TRUSTEE', 'BOARD OF MANAGERS', 'UNITED STATES OF AMERICA',
    'NEW YORK CITY', 'CITY OF NEW YORK', 'STATE OF NEW YORK',
)


def _clean_identity_name(value):
    """Remove mailing instructions without changing the identity itself."""
    name = re.sub(r'\s+', ' ', str(value or '')).strip()
    return re.sub(r'\s+(?:C/O|C\.O\.|ATTN:?|%)\s+.*$', '', name,
                  flags=re.IGNORECASE).strip(' ,')


def split_candidate_names(value):
    """Split only delimiters the pipeline itself uses for separate parties.

    Commas remain untouched because ACRIS commonly publishes LAST, FIRST.
    Ampersands/AND remain grouped because a joint-name string cannot safely be
    turned into two people without knowing which surname applies to whom.
    """
    parts = [_clean_identity_name(part) for part in
             re.split(r'\s*;\s*|[\r\n]+', str(value or ''))]
    return list(dict.fromkeys(part for part in parts if part))


def is_business_entity(full_name):
    """True if the name looks like a company/government/etc. rather than a
    person. Runs before HumanName since name-parser libraries don't know
    NYC-specific quirks like 'CHURCH' being a valid last name."""
    if not full_name:
        return True
    name = _clean_identity_name(full_name).upper()
    if not name:
        return True
    if len(split_candidate_names(full_name)) > 1:
        return True  # multiple parties are not one enrichable human identity
    if '&' in name:
        return True
    for prefix in ('CITY OF', 'STATE OF', 'COUNTY OF',
                   'BANK OF', 'HEIRS OF', 'ESTATE OF'):
        if name.startswith(prefix):
            return True
    normalized = re.sub(r'[^A-Z0-9]+', ' ', name).strip()
    if normalized in _KNOWN_ORGANIZATION_NAMES:
        return True
    if any(phrase in normalized for phrase in _ORGANIZATION_PHRASES):
        return True
    if re.search(r'\d', name):
        return True
    words = normalized.split()
    # Common financial suffixes appear punctuated as N.A.; tokenization turns
    # that into N A. In this context it is not a person's middle initials.
    if len(words) >= 3 and words[-2:] == ['N', 'A']:
        return True
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


def classify_party_name(full_name):
    """Classify one party name for display and paid-enrichment safeguards.

    Returns a small JSON-safe dict. `unknown` is deliberately not treated as
    a person: false negatives are cheaper than paying to search for a bank,
    trust, joint-owner string, or malformed government record.
    """
    names = split_candidate_names(full_name)
    if not names:
        return {'entity_kind': 'unknown', 'is_person': False,
                'classification_reason': 'empty name'}
    if len(names) > 1:
        return {'entity_kind': 'multiple', 'is_person': False,
                'classification_reason': 'multiple parties in one field'}
    name = names[0]
    if re.search(r'\b(?:AND|ET\s+AL|ET\s+UX|ET\s+VIR)\b', name,
                 flags=re.IGNORECASE):
        return {'entity_kind': 'multiple', 'is_person': False,
                'classification_reason': 'joint or multiple party notation'}
    if is_business_entity(name):
        return {'entity_kind': 'organization', 'is_person': False,
                'classification_reason': 'organization or legal-entity terms'}
    parsed = HumanName(name)
    first = (parsed.first or '').strip()
    last = (parsed.last or '').strip()
    token_count = len(re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)?", name))
    if first and last and 2 <= token_count <= 7:
        return {'entity_kind': 'person', 'is_person': True,
                'classification_reason': 'person-shaped first and last name'}
    return {'entity_kind': 'unknown', 'is_person': False,
            'classification_reason': 'not a confident single-person name'}


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
    cleaned_name = _clean_identity_name(full_name)
    if not cleaned_name or not classify_party_name(cleaned_name)['is_person']:
        return None, None, None

    h = HumanName(cleaned_name)
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

_NYC_BOROUGH_NAMES = {
    '1': 'MANHATTAN',
    '2': 'BRONX',
    '3': 'BROOKLYN',
    '4': 'QUEENS',
    '5': 'STATEN ISLAND',
}


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


def resolve_owner_search_location(building, fallback_address=None):
    """Build provider location fields from the server-side building row.

    The browser used to append ``building.borough`` to the address. That field
    is the NYC code (``4``), while ``borough_name`` is ``Queens``; the result
    was parsed as city="4" and sent to paid providers. Provider inputs now use
    authoritative database fields, with the submitted address only as a
    fallback for older callers or incomplete building rows.
    """
    fallback_street, fallback_city, fallback_state, fallback_zip = (
        parse_nyc_address(fallback_address)
    )
    row = building or {}
    # ``buildings.address`` is not consistent across import paths: older rows
    # contain only the street, while newer property imports store the complete
    # ``STREET, BOROUGH, NY ZIP`` display address. Passing that complete value
    # as the provider's street produced inputs such as
    # ``18423 CAMBRIDGE RD, QUEENS, NY 11432; QUEENS, NY 11432`` and also made
    # a returned ``Street Address`` impossible to compare with our target.
    row_street, row_city, row_state, row_zip = parse_nyc_address(
        row.get('address'))
    street = (row_street or fallback_street or '').strip() or None

    borough_raw = row.get('borough')
    borough_key = str(borough_raw).strip() if borough_raw is not None else ''
    city = _NYC_BOROUGH_NAMES.get(borough_key)
    if not city and borough_key.upper() in _NYC_BOROUGH_NAMES.values():
        city = borough_key.upper()
    if not city:
        candidate_city = (row_city or fallback_city or '').strip().upper()
        city = _NYC_BOROUGH_NAMES.get(candidate_city, candidate_city or None)

    zipcode = str(row.get('zip_code') or row_zip or fallback_zip or '').strip()
    zip_match = re.search(r'\b(\d{5})', zipcode)
    zipcode = zip_match.group(1) if zip_match else None
    state = (row_state or fallback_state or 'NY').strip().upper()
    return street, city, state, zipcode


def _parse_with_suffix(full_name):
    """Variant of parse_owner_name that also returns the suffix (JR/SR/III).
    Used by canonical_name_key so suffix can participate in dedup."""
    cleaned_name = _clean_identity_name(full_name)
    if not cleaned_name or not classify_party_name(cleaned_name)['is_person']:
        return None
    h = HumanName(cleaned_name)
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
    
    # Build proper address - Enformion wants city/state in AddressLine2.
    # If the caller doesn't know the city, send the name WITHOUT an address
    # block: a fabricated default city ("Brooklyn, NY") used to bias paid
    # lookups toward the wrong same-named person.
    payload = {
        "FirstName": first_name or "",
        "MiddleName": middle_name or "",
        "LastName": last_name or "",
    }
    city_state = address_line2.strip() if address_line2 else None
    if address_line1 or city_state:
        # Use "Address" (singular object) NOT "Addresses" (array) - API requires this format
        payload["Address"] = {
            "AddressLine1": address_line1 or "",
            "AddressLine2": city_state or "",
        }
    
    # Do not log the name/address payload; production logs are retained and
    # should not become a second store of enrichment inputs.
    print(f"Enformion API call: {ENFORMION_API_URL}; "
          f"has_address={bool(payload.get('Address'))}")
    
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


def _build_apify_run_input(first_name, last_name, middle_name=None, suffix=None,
                           street_address=None, city=None, state=None,
                           zipcode=None, max_results=5,
                           include_address_fallback=True):
    """Build input using the actor's documented, type-specific fields.

    ``name`` accepts people-name queries. Street queries must go in
    ``street_citystatezip``; putting both strings in ``name`` caused the actor
    to interpret a house number/street as a person's name.
    """
    primary = _build_apify_name_query(
        first_name, last_name, middle_name, suffix, city, state, zipcode)
    run_input = {
        'name': [primary] if primary else [],
        'max_results': max(1, min(int(max_results or 1), 10)),
    }
    if include_address_fallback:
        address_query = _build_apify_address_query(
            street_address, city, state, zipcode)
        if address_query:
            run_input['street_citystatezip'] = [address_query]
    return run_input


# Apify's run-sync-get-dataset-items endpoint runs the actor and returns the
# dataset in a single blocking HTTP call. Replaces the old 3-step
# start-run -> poll-status -> fetch-dataset dance, which leaked run state on
# crashes and had a brittle 60s polling loop.
APIFY_SYNC_TIMEOUT_SECONDS = 180


def call_apify_truepeoplesearch(first_name, last_name, middle_name=None, suffix=None,
                                 street_address=None, city=None, state=None, zipcode=None,
                                 max_results=5, include_address_fallback=True):
    """Call the Apify TruePeopleSearch actor via the run-sync endpoint.

    Runs a name+location search and, when available, a separately typed address
    search. Address-search residents are never accepted merely because they
    have a phone: the returned first/last name and location evidence must match
    the requested owner. Ambiguous top matches are rejected.
    """
    if not APIFY_API_TOKEN:
        return False, None, "Apify API token not configured"
    if not any((street_address, city, zipcode)):
        return False, None, "No usable property location for identity verification"

    run_input = _build_apify_run_input(
        first_name, last_name, middle_name, suffix,
        street_address, city, state, zipcode,
        max_results=max_results,
        include_address_fallback=include_address_fallback,
    )
    if not run_input.get('name') and not run_input.get('street_citystatezip'):
        return False, None, "No usable search input (no name and no address)"

    # Do not print names or addresses. Railway retains application logs and
    # provider queries are personal data.
    print("Apify TruePeopleSearch call: "
          f"name_queries={len(run_input.get('name', []))}, "
          f"address_queries={len(run_input.get('street_citystatezip', []))}, "
          f"max_results={run_input['max_results']}")

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
    except requests.RequestException as e:
        # A requests exception can embed the request URL. The API token is a
        # URL query parameter, so logging/returning the exception can disclose
        # the credential. Keep only the exception class.
        print(f"Apify request failed: {type(e).__name__}")
        return False, None, "Apify API request failed"
    except Exception as e:
        print(f"Apify response handling failed: {type(e).__name__}")
        return False, None, "Apify response handling failed"

    if response.status_code not in (200, 201):
        print(f"Apify returned HTTP {response.status_code}")
        return False, None, f"Apify error {response.status_code}"

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
    observed_keys = set()
    for item in items[:10]:
        if isinstance(item, dict):
            observed_keys.update(item.keys())
    if not (expected_keys & observed_keys):
        print(f"WARNING: Apify response missing all expected keys. "
              f"Got: {sorted(observed_keys)[:20]}")

    best, selection, selection_error = _pick_best_apify_item(
        items,
        first_name=first_name,
        last_name=last_name,
        street_address=street_address,
        city=city,
        state=state,
        zipcode=zipcode,
    )
    if best is None:
        return False, None, f"UNVERIFIED_MATCH: {selection_error}"

    # Persist only the verified selected item. The old code attached every
    # related-person result (including rejected tenants/relatives) to the raw
    # database response.
    selected = dict(best)
    selected['_apify_selection'] = selection
    selected['_apify_result_count'] = len(items)
    return True, selected, None


def _match_key(value):
    """Accent/punctuation-insensitive identity key."""
    folded = unicodedata.normalize('NFKD', str(value or ''))
    folded = ''.join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r'[^A-Z0-9]+', '', folded.upper())


def _one_edit_name_typo(a, b):
    """True for one conservative insertion/deletion/substitution.

    This is intentionally narrower than generic fuzzy matching. It is used
    only for long surnames and only when a corroborated street match strongly
    ties the provider result to the property. Short names and transpositions
    remain exact-only to avoid collapsing distinct people.
    """
    a = _match_key(a)
    b = _match_key(b)
    if a == b or min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(left != right for left, right in zip(a, b)) == 1

    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    short_idx = long_idx = differences = 0
    while short_idx < len(shorter) and long_idx < len(longer):
        if shorter[short_idx] == longer[long_idx]:
            short_idx += 1
            long_idx += 1
            continue
        differences += 1
        if differences > 1:
            return False
        long_idx += 1
    return True


_STREET_SUFFIXES = {
    'STREET': 'ST', 'ROAD': 'RD', 'AVENUE': 'AVE', 'BOULEVARD': 'BLVD',
    'PLACE': 'PL', 'LANE': 'LN', 'DRIVE': 'DR', 'COURT': 'CT',
    'PARKWAY': 'PKWY', 'TERRACE': 'TER', 'HIGHWAY': 'HWY',
}


def _street_key(value):
    text = unicodedata.normalize('NFKD', str(value or '')).upper()
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    # A unit difference does not change the building identity used here.
    text = re.sub(r'\b(?:APT|APARTMENT|UNIT|FL|FLOOR|SUITE)\b.*$', '', text)
    words = re.findall(r'[A-Z0-9]+', text)
    words = [_STREET_SUFFIXES.get(word, word) for word in words]
    return ''.join(words)


def _city_key(value):
    key = re.sub(r'[^A-Z0-9]+', ' ', str(value or '').upper()).strip()
    return {'MANHATTAN': 'NEW YORK', 'NEW YORK CITY': 'NEW YORK'}.get(key, key)


def _zip_key(value):
    match = re.search(r'\b(\d{5})', str(value or ''))
    return match.group(1) if match else ''


def _apify_item_name(item):
    first = item.get('First Name') or item.get('FirstName')
    last = item.get('Last Name') or item.get('LastName')
    if first and last:
        return first, last
    full = item.get('Full Name') or item.get('Name') or ''
    parsed = HumanName(full)
    return parsed.first, parsed.last


def _apify_item_addresses(item):
    """Return current and historical addresses in one normalized shape."""
    addresses = [{
        'kind': 'current',
        'street': item.get('Street Address'),
        'city': item.get('Address Locality'),
        'state': item.get('Address Region'),
        'zip': item.get('Postal Code'),
    }]
    previous = item.get('Previous Addresses') or []
    if isinstance(previous, list):
        for row in previous:
            if not isinstance(row, dict):
                continue
            addresses.append({
                'kind': 'previous',
                'street': row.get('streetAddress') or row.get('Street Address'),
                'city': row.get('addressLocality') or row.get('Address Locality'),
                'state': row.get('addressRegion') or row.get('Address Region'),
                'zip': row.get('postalCode') or row.get('Postal Code'),
            })
    return addresses


def _evaluate_apify_item(item, first_name, last_name, street_address=None,
                          city=None, state=None, zipcode=None):
    """Return identity evidence for a result, or ``None`` when unsafe.

    First name must match exactly after normalization. A long surname may
    differ by one character only when a corroborated street match independently
    ties the result to the property; this handles known government-source typos
    without turning the provider response into a fuzzy people search.
    When we have property location data, at least street, ZIP, or city must
    also match the result's current or previous addresses. State alone is not
    enough.
    """
    if not isinstance(item, dict):
        return None
    result_first, result_last = _apify_item_name(item)
    first_exact = _match_key(result_first) == _match_key(first_name)
    last_exact = _match_key(result_last) == _match_key(last_name)
    last_typo = _one_edit_name_typo(result_last, last_name)
    if not first_exact or not (last_exact or last_typo):
        return None

    wanted_street = _street_key(street_address)
    wanted_city = _city_key(city)
    wanted_state = _match_key(state)
    wanted_zip = _zip_key(zipcode)
    has_location_target = bool(wanted_street or wanted_city or wanted_zip)

    best_location = None
    best_score = -1
    for address in _apify_item_addresses(item):
        street_match = bool(wanted_street and
                            _street_key(address['street']) == wanted_street)
        zip_match = bool(wanted_zip and _zip_key(address['zip']) == wanted_zip)
        city_match = bool(wanted_city and _city_key(address['city']) == wanted_city)
        state_match = bool(wanted_state and
                           _match_key(address['state']) == wanted_state)
        location_score = ((35 if street_match else 0)
                          + (25 if zip_match else 0)
                          + (12 if city_match else 0)
                          + (2 if state_match else 0)
                          - (3 if address['kind'] == 'previous' else 0))
        if location_score > best_score:
            best_score = location_score
            best_location = {
                'address_kind': address['kind'],
                'street_match': street_match,
                'zip_match': zip_match,
                'city_match': city_match,
                'state_match': state_match,
            }

    best_location = best_location or {
        'address_kind': None, 'street_match': False, 'zip_match': False,
        'city_match': False, 'state_match': False,
    }
    # ZIP is unique enough on its own. A street or city match is only useful
    # with corroborating state/city evidence; identical street names occur in
    # many states, and a same-name person in a same-named city is not enough.
    location_evidence = (
        best_location['zip_match']
        or (best_location['street_match']
            and (best_location['state_match'] or best_location['city_match']))
        or (best_location['city_match'] and best_location['state_match'])
    )
    if has_location_target and not location_evidence:
        return None

    # A one-character surname correction needs stronger corroboration than an
    # exact name. City+state alone is deliberately insufficient.
    strong_location_evidence = (
        best_location['street_match']
        and (best_location['zip_match'] or best_location['state_match']
             or best_location['city_match'])
    )
    if last_typo and not strong_location_evidence:
        return None

    search_option = (item.get('Search Option') or item.get('Search Type') or '')
    score = (60 if last_exact else 45) + max(best_score, 0)
    confidence = ('high' if best_location['street_match'] or best_location['zip_match']
                  else 'medium' if best_location['city_match']
                  else 'name-only')
    return {
        'score': score,
        'confidence': confidence,
        'name_match': True,
        'name_match_type': ('exact' if last_exact
                            else 'one-character-surname-typo'),
        'search_option': search_option or None,
        **best_location,
    }


def _apify_identity_key(item):
    link = item.get('Person Link') or item.get('PersonLink')
    if link:
        return ('link', str(link))
    first, last = _apify_item_name(item)
    return ('fallback', _match_key(first), _match_key(last),
            _street_key(item.get('Street Address')), _zip_key(item.get('Postal Code')))


def _apify_contact_utility(item):
    phone_count = sum(bool(item.get(f'Phone-{i}')) for i in range(1, 6))
    email_count = sum(bool(item.get(f'Email-{i}')) for i in range(1, 6))
    recency = max((_parse_tps_date_to_yyyymm(
        item.get(f'Phone-{i} Last Reported')) for i in range(1, 6)), default=0)
    return phone_count * 100 + email_count * 10 + recency


def _pick_best_apify_item(items, first_name, last_name, street_address=None,
                           city=None, state=None, zipcode=None):
    """Select a verified, unambiguous identity before considering contacts."""
    evaluated = []
    for item in items or []:
        evidence = _evaluate_apify_item(
            item, first_name, last_name, street_address, city, state, zipcode)
        if evidence:
            evaluated.append((item, evidence))
    if not evaluated:
        return None, None, "no result matched both the requested name and location"

    # Collapse the same person returned by both name and address searches.
    identities = {}
    for item, evidence in evaluated:
        key = _apify_identity_key(item)
        existing = identities.get(key)
        candidate_rank = (evidence['score'], _apify_contact_utility(item))
        if not existing or candidate_rank > existing[2]:
            identities[key] = (item, evidence, candidate_rank)

    ranked = sorted(identities.values(), key=lambda row: row[2], reverse=True)
    if len(ranked) > 1 and ranked[0][1]['score'] == ranked[1][1]['score']:
        return None, None, "multiple distinct people tied for the strongest match"

    item, evidence, _rank = ranked[0]
    evidence = {
        **evidence,
        'verified_candidate_count': len(ranked),
        'returned_result_count': len(items or []),
    }
    return item, evidence, None


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
        seen_phones = set()
        seen_emails = set()

        # TPS exposes 5 phone slots. Capture every populated field per slot.
        for i in range(1, 6):
            number = api_response.get(f'Phone-{i}', '')
            if not number:
                continue
            digits = re.sub(r'\D', '', str(number))
            if len(digits) == 11 and digits.startswith('1'):
                digits = digits[1:]
            if len(digits) != 10 or digits in seen_phones:
                continue
            seen_phones.add(digits)
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
                # The actor reports this number but does not expose a
                # validation flag. Shape-valid is intentionally distinct from
                # verified/connected.
                'is_valid': None,
            })

        # Rank phones with most-recent "Last reported" first. The provider's
        # original ordering is name-search relevance, but for outreach the
        # most recently seen number is what we want to dial first.
        phones.sort(key=lambda p: p.get('last_reported_yyyymm') or 0, reverse=True)

        for i in range(1, 6):
            email_addr = str(api_response.get(f'Email-{i}', '') or '').strip()
            email_key = email_addr.casefold()
            if (not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email_addr)
                    or email_key in seen_emails):
                continue
            seen_emails.add(email_key)
            emails.append({
                'email': email_addr,
                # Apify does not return an email-validation signal.
                'is_valid': None,
            })

    except Exception as e:
        print(f"Error extracting Apify contact info: {e}")
        import traceback
        traceback.print_exc()

    print(f"Extracted from Apify: {len(phones)} phones, {len(emails)} emails")
    return phones, emails, person_id


def summarize_apify_match(api_response, selection=None):
    """Pull the human-readable identity fields from an Apify item so we can
    show the user *who* we matched (their age, county, current address,
    relatives). Used for match-verification UI and stored alongside the raw
    response. Safe to call on partial responses — every field is optional."""
    if not api_response:
        return None
    summary = {
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
        'search_option': (api_response.get('Search Option')
                          or api_response.get('Search Type') or None),
        'input_given': (api_response.get('Input Given')
                        or api_response.get('Search Input') or None),
    }
    if selection:
        summary['verification'] = selection
    return summary


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
    
    print("Extracting Enformion contact info")
    
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


def _structured_search_location(street=None, city=None, state=None, zipcode=None):
    """Normalize a known person's structured mailing-address fields."""
    street = str(street or '').strip() or None
    city = str(city or '').strip().upper() or None
    state = str(state or '').strip().upper() or None
    zip_match = re.search(r'\b(\d{5})', str(zipcode or ''))
    zipcode = zip_match.group(1) if zip_match else None
    if not any((street, city, zipcode)):
        return None
    return street, city, state or 'NY', zipcode


def _best_owner_search_location(cur, building_id, building, owner_name,
                                fallback_address=None):
    """Prefer a verified owner-specific mailing address over the property.

    SOS supplies the principal's address and ACRIS supplies the latest deed
    grantee's mailing address. Those are substantially better disambiguators
    for non-owner-occupied properties than assuming the person lives in the
    building being researched.

    Returns ``((street, city, state, zip), source)``.
    """
    owner_key = canonical_name_key(owner_name)

    sos_name = building.get('sos_principal_name')
    if names_compatible(owner_key, canonical_name_key(sos_name)):
        location = _structured_search_location(
            building.get('sos_principal_street'),
            building.get('sos_principal_city'),
            building.get('sos_principal_state'),
            building.get('sos_principal_zip'),
        )
        if location:
            return location, 'sos_principal_address'

    sale_buyer = building.get('sale_buyer_primary')
    if names_compatible(owner_key, canonical_name_key(sale_buyer)):
        cur.execute(
            """
            SELECT ap.party_name, ap.address_1, ap.city, ap.state, ap.zip_code
            FROM acris_parties ap
            JOIN acris_transactions at ON at.id = ap.transaction_id
            WHERE at.building_id = %s
              AND ap.party_type = 'buyer'
            ORDER BY at.is_primary_deed DESC NULLS LAST,
                     at.recorded_date DESC NULLS LAST
            LIMIT 20
            """,
            (building_id,),
        )
        for party in cur.fetchall():
            if not names_compatible(owner_key, canonical_name_key(party['party_name'])):
                continue
            location = _structured_search_location(
                party.get('address_1'), party.get('city'), party.get('state'),
                party.get('zip_code'))
            if location:
                return location, 'acris_grantee_address'

    return (resolve_owner_search_location(building, fallback_address),
            'property_address')


def enrich_owner(building_id, owner_name, address, user_id, provider=None):
    """Perform enrichment lookup and store results.

    provider:
      - None / 'apify_fallback' (default): try Apify (cheaper + richer data),
        fall back to Enformion only if Apify misses.
      - 'enformion_fallback':  legacy mode — try Enformion, fall back to Apify.
      - 'apify':               use Apify only; don't fall back.
      - 'enformion':           use Enformion only; don't fall back.

    Returns: (success, data, message)
    """
    classification = classify_party_name(owner_name)
    if not classification['is_person']:
        return (False, None,
                f"Enrichment is limited to human names; classified as "
                f"{classification['entity_kind']}")

    if provider is None or provider not in VALID_PROVIDERS:
        provider = DEFAULT_PROVIDER

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # Load authoritative lookup inputs. Access/caching is owner-specific
        # in user_enrichments and is checked by the API/job caller. The old
        # shortcut read buildings.enriched_* (one global slot per property),
        # which could return Owner B's contacts when asked for Owner A.
        cur.execute("""
            SELECT address, borough, zip_code, sale_buyer_primary,
                   sos_principal_name, sos_principal_street,
                   sos_principal_city, sos_principal_state, sos_principal_zip
            FROM buildings WHERE id = %s
        """, (building_id,))

        building = cur.fetchone()
        if not building:
            return False, None, "Building not found"

        # Need to call API. Parse the owner name with HumanName so we can pass
        # middle name and suffix to the provider — TPS in particular benefits
        # massively from disambiguation by middle name on common surnames.
        first_name, middle_name, last_name = parse_owner_name(owner_name)
        if not first_name or not last_name:
            return False, None, "Could not parse owner name - may be a business entity"

        with_suffix = _parse_with_suffix(owner_name) or (first_name, middle_name or '', last_name, '')
        suffix = with_suffix[3] or None

        # Use the database row, not a browser-composed address. In particular,
        # building.borough is an NYC numeric code (4), not a city name
        # (Queens). The submitted string is only an incomplete-data fallback.
        (street, city, state, zipcode), location_source = (
            _best_owner_search_location(
                cur, building_id, building, owner_name, address)
        )
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

        def _try_apify():
            result = call_apify_truepeoplesearch(
                first_name=first_name, last_name=last_name,
                middle_name=middle_name, suffix=suffix,
                street_address=street, city=city, state=state, zipcode=zipcode,
            )
            success, response_data, response_error = result
            if success and isinstance(response_data, dict):
                response_data.setdefault('_apify_selection', {})[
                    'location_source'] = location_source
            return success, response_data, response_error

        def _try_enformion():
            return call_enformion_api(
                first_name, last_name, street, address_line2, middle_name,
            )

        def _unverified_apify(error_message):
            return str(error_message or '').startswith('UNVERIFIED_MATCH:')

        if provider == PROVIDER_APIFY:
            success, api_response, error = _try_apify()
            if success:
                enrichment_source = 'apify_truepeoplesearch'
            else:
                return False, None, f"Apify enrichment error: {error}"

        elif provider == PROVIDER_ENFORMION:
            success, api_response, error = _try_enformion()
            if success:
                enrichment_source = 'enformion'
            else:
                return False, None, f"Enformion enrichment error: {error}"

        elif provider == PROVIDER_APIFY_FALLBACK:
            # Apify first (cheap, rich data); Enformion only if it misses.
            success, api_response, error = _try_apify()
            if success:
                enrichment_source = 'apify_truepeoplesearch'
            elif _unverified_apify(error):
                # Apify returned people, but none could be tied confidently to
                # this owner/property. Falling through to a less transparent
                # result would undo the identity guard.
                return False, None, error
            else:
                print(f"Apify failed ({error}), trying Enformion fallback...")
                success, api_response, error = _try_enformion()
                if success:
                    enrichment_source = 'enformion'
                else:
                    return False, None, f"Enrichment API error: {error}"

        else:  # PROVIDER_ENFORMION_FALLBACK — legacy: Enformion first, Apify fallback
            success, api_response, error = _try_enformion()
            if success:
                enrichment_source = 'enformion'
            else:
                print(f"Enformion failed ({error}), trying Apify TruePeopleSearch fallback...")
                success, api_response, error = _try_apify()
                if success:
                    enrichment_source = 'apify_truepeoplesearch'
                else:
                    return False, None, f"Enrichment API error: {error}"

        # Extract contact info based on source.
        if enrichment_source == 'apify_truepeoplesearch':
            selection = api_response.pop('_apify_selection', None)
            result_count = api_response.pop('_apify_result_count', None)
            phones, emails, person_id = extract_apify_contact_info(api_response)
            match_summary = summarize_apify_match(api_response, selection)
        else:
            selection = None
            result_count = None
            phones, emails, person_id = extract_contact_info(api_response)
            match_summary = None

        if not phones and not emails:
            api_message = api_response.get('message', '') if isinstance(api_response, dict) else ''
            if 'no' in api_message.lower() and 'match' in api_message.lower():
                return False, None, "No matching records found in our database for this person. They may not be in our data sources."
            return False, None, "No contact information (phone/email) found for this person in our database."

        # Store only the verified selected record. Rejected related-person and
        # address-resident results are intentionally discarded.
        if isinstance(api_response, dict):
            stored_response = dict(api_response)
            if result_count is not None:
                stored_response['_returned_result_count'] = result_count
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
            'match': match_summary,
            'source': enrichment_source,
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
# Business-name suffixes that carry no identity — "65 SPRING REALTY LLC" and
# "65 Spring Realty, L.L.C." are the same company.
_ENTITY_SUFFIXES = re.compile(
    r'\b(L\.?L\.?C|L\.?L\.?P|L\.?P|P\.?L\.?L\.?C|P\.?C|INC|INCORPORATED|'
    r'CORP|CORPORATION|LTD|LIMITED|COMPANY|CO|DBA|D/B/A|USA)\b\.?',
    re.IGNORECASE,
)


def normalize_entity_name(name):
    """Normalize a business name for identity comparison.

    Mirrors ny_sos_lookup.normalize_business_name. The two are deliberately
    separate copies: the scraper runs in the pipeline environment and the web
    app must not depend on it (it pulls httpx, which the dashboard does not
    install). test_entity_match_parity in filter_param_tests.py pins them to
    the same behaviour so they cannot drift silently.
    """
    if not name:
        return ''
    out = str(name).upper().strip()
    # Mirror the scraper: care-of/attention tails are mailing instructions,
    # not identity.
    out = re.sub(r'\s+(?:C/O|C\.O\.|ATTN:?|%)\s+.*$', '', out)
    out = re.sub(r'\s*-\s*[A-Z\s]+,\s*[A-Z]{2}(\s+\d{5})?$', '', out)
    out = _ENTITY_SUFFIXES.sub(' ', out)
    out = re.sub(r'[^\w\s]', ' ', out)
    return re.sub(r'\s+', ' ', out).strip()


def entity_match_quality(registered_name, candidate_names):
    """How well a registered SOS entity matches any of a building's owners.

    Returns (quality, matched_name) where quality is 'exact', 'prefix',
    'mismatch', or 'unknown' when there is nothing to compare against.

    This runs when the profile is served rather than when the data is
    written, so rows stored before the lookup verified its match — which
    could attach an unrelated company's officers to a building — are flagged
    without needing a re-run.
    """
    registered = normalize_entity_name(registered_name)
    if not registered:
        return 'unknown', None

    candidates = [(normalize_entity_name(n), n) for n in candidate_names if n]
    candidates = [(norm, raw) for norm, raw in candidates if norm]
    if not candidates:
        return 'unknown', None

    for norm, raw in candidates:
        if norm == registered:
            return 'exact', raw
    for norm, raw in candidates:
        if norm.startswith(registered) or registered.startswith(norm):
            return 'prefix', raw
    return 'mismatch', None


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
                sos_entity_name,
                sale_buyer_primary
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
        # Also skip when the registered entity is not the company any of our
        # owner fields name. Those people run some other business, so paying
        # to look up their contacts buys nothing for this building.
        sos_match, _matched = entity_match_quality(
            building['sos_entity_name'],
            [building['current_owner_name'], building['owner_name_rpad'],
             building['owner_name_hpd'], building['sale_buyer_primary']],
        )
        sos_classification = classify_party_name(sos_name)
        if (sos_classification['is_person']
                and not is_sos_agent_title(sos_title)
                and sos_match != 'mismatch'):
            key = canonical_name_key(sos_name)
            if key:
                is_enriched = _is_already_enriched(key)
                _dedup_add_owner(owners, keys, key, {
                    'name': sos_name,
                    'source': 'NY Secretary of State',
                    'title': sos_title,
                    **sos_classification,
                    'recommended': not is_enriched,
                    'reason': 'Real person behind LLC',
                    'already_enriched': is_enriched,
                })

        # Other sources, in source-priority order. Compatible duplicates
        # collapse into the SOS row above (or the first one we see).
        source_map = {
            'sale_buyer_primary': 'ACRIS Latest Deed Grantee',
            'current_owner_name': 'NYC PLUTO Database',
            'owner_name_hpd': 'HPD Registration',
            'owner_name_rpad': 'Historical Tax Records (RPAD)',
        }

        for field, source in source_map.items():
            for name in split_candidate_names(building[field]):
                classification = classify_party_name(name)
                if not classification['is_person']:
                    continue
                key = canonical_name_key(name)
                if not key:
                    continue
                _dedup_add_owner(owners, keys, key, {
                    'name': name,
                    'source': source,
                    **classification,
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
    classification = classify_party_name(contact_name)
    if not classification['is_person']:
        return (False, None,
                f"Enrichment is limited to human names; classified as "
                f"{classification['entity_kind']}")

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

    Normal callers pass preclassified rows; this function reclassifies them
    so background jobs remain safe if a legacy or future caller does not.

    - 'recommended': the SOS principal (real person behind an LLC) if available,
      otherwise the first listed human. Returns at most one person.
    - 'all': returns every distinct human candidate unchanged.
    """
    if not owners:
        return []
    # Defense in depth for workers and future callers. Candidate builders
    # already filter names, but no selection strategy may turn an incorrectly
    # tagged legacy row into a paid organization lookup.
    people = []
    for owner in owners:
        classification = classify_party_name(owner.get('name'))
        if classification['is_person']:
            people.append({**owner, **classification})
    if not people:
        return []
    if strategy == OWNER_STRATEGY_ALL:
        return people
    # Default to 'recommended'
    sos = next((o for o in people if o.get('source') == 'NY Secretary of State'), None)
    if sos:
        return [sos]
    return [people[0]]


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
                   sos_principal_name, sos_principal_title, sos_entity_name,
                   sale_buyer_primary
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
        ('sale_buyer_primary', 'ACRIS Latest Deed Grantee', False),
        ('current_owner_name', 'NYC PLUTO Database', False),
        ('owner_name_hpd', 'HPD Registration', False),
        ('owner_name_rpad', 'Historical Tax Records (RPAD)', False),
    ]

    total_owners = 0
    properties_with_owners = 0
    breakdown = []
    per_building_map = {}

    for row in rows:
        bid = row['id']
        enriched_keys = enriched_by_building.get(bid, [])
        sos_title = row['sos_principal_title']
        sos_match, _ = entity_match_quality(
            row['sos_entity_name'],
            [row['sale_buyer_primary'], row['current_owner_name'],
             row['owner_name_hpd'], row['owner_name_rpad']],
        )
        owners = []
        keys = []
        for field, source, is_sos in source_map:
            for name in split_candidate_names(row[field]):
                # Skip the SOS row entirely if the title says it's an agent
                # (Service of Process / Registered Agent — not an owner).
                if is_sos and is_sos_agent_title(sos_title):
                    continue
                if is_sos and sos_match == 'mismatch':
                    continue
                classification = classify_party_name(name)
                if not classification['is_person']:
                    continue
                key = canonical_name_key(name)
                if not key:
                    continue
                already = any(names_compatible(key, ek) for ek in enriched_keys)
                _dedup_add_owner(owners, keys, key, {
                    'name': name,
                    'source': source,
                    **classification,
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
