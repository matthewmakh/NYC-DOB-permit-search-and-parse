"""
Auto-add a property to the buildings table from a search query.

Triggered from the home-page search bar when the user enters an address
or BBL that isn't yet in our `buildings` table. Runs every FREE NYC /
state enrichment step in sequence:

  - NYC Geoclient v2 (address -> BBL, BIN, lat/lon)
  - NYC PLUTO Socrata (building class, units, year built, owner of record)
  - NYC RPAD Socrata (tax-record owner)
  - NYC HPD Registrations (multi-unit owner registration)
  - NYC ACRIS (deed/mortgage history, sale price, recent transactions)
  - NYC DOF Tax Delinquency + ECB violations + DOB violations
  - NY Secretary of State (real person behind an LLC owner)

All of the above are free public APIs. Paid contact enrichment
(Apify / Enformion) is INTENTIONALLY skipped here — those only run when
a user explicitly clicks Enrich on the building profile.

Each step is wrapped in its own try/except so one failure can't kill the
whole lookup. The orchestrator returns a status dict listing which steps
succeeded and which failed; the caller can show a partial-success page.
"""

import os
import re
import sys
import logging
import threading
import psycopg2
import psycopg2.extras
import requests

# The shared data modules live in this same directory (dashboard_html/), so
# the dashboard service deploys self-contained on Railway (whose root
# directory setting is dashboard_html — the repo root doesn't exist there).
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from step2_enrich_from_pluto import (
    get_pluto_data_for_bbl,
    get_rpad_data_for_bbl,
    get_hpd_data_for_bbl,
)
from step3_enrich_from_acris import enrich_building_from_acris
from step4_enrich_from_tax_liens import (
    enrich_building as fetch_tax_lien_data,
    update_building_tax_lien_data,
)
from step5_enrich_from_sos import (
    get_best_llc_name,
    process_sos_result,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input parsing — accept a BBL, a borough+address, or an address
# ---------------------------------------------------------------------------

NYC_APP_ID = os.getenv('NYC_GEOCLIENT_APP_ID')

# Geoclient borough names it'll accept verbatim (case-insensitive).
_BOROUGH_TOKENS = {
    'MANHATTAN', 'BROOKLYN', 'QUEENS', 'BRONX', 'STATEN ISLAND', 'STATEN',
}
# Canonical borough names mapped to Geoclient's expected input.
_BOROUGH_ALIASES = {
    'NEW YORK': 'MANHATTAN',
    'NYC': 'MANHATTAN',
    'BX': 'BRONX',
    'BK': 'BROOKLYN',
    'SI': 'STATEN ISLAND',
}
_BBL_RE = re.compile(r'^\s*(\d{10})\s*$')


def _normalize_bbl(query):
    """If `query` is a 10-digit BBL (with or without dashes), return the
    bare 10-digit form; else None. Accepts both '3053170021' and
    '3-05317-0021' forms (the dashed form is how NYC officially prints BBLs).
    """
    if not query:
        return None
    stripped = re.sub(r'[\s\-]', '', query)
    m = _BBL_RE.match(stripped)
    return m.group(1) if m else None


def _detect_borough(address):
    """Pull a borough name out of an address string, normalizing aliases.
    Returns the Geoclient-friendly borough or None.
    """
    if not address:
        return None
    up = address.upper()
    for alias, canonical in _BOROUGH_ALIASES.items():
        if alias in up:
            return canonical
    # Order matters: 'STATEN ISLAND' must be checked before 'STATEN' alone.
    for tok in ['STATEN ISLAND', 'MANHATTAN', 'BROOKLYN', 'QUEENS', 'BRONX']:
        if tok in up:
            return tok
    return None


def _parse_house_and_street(address):
    """Split '141 WYONA STREET, BROOKLYN, NY 11207' into ('141','WYONA STREET').
    Returns (None, None) on failure. We can't rely on a comma since some
    users type bare addresses like '141 WYONA STREET BROOKLYN NY'.
    """
    if not address:
        return None, None
    # Strip any trailing borough/city/state/zip so we just have street.
    cleaned = address.split(',')[0].strip()
    # House number can include digits, letters, and dashes (Queens-style
    # addresses like "47-22 47TH AVE" are common in Woodside, Astoria, etc.).
    # The character class must include digits AFTER the leading run.
    m = re.match(r'^\s*(\d[\dA-Z\-]*)\s+(.+?)\s*$', cleaned, re.IGNORECASE)
    if not m:
        return None, None
    return m.group(1), m.group(2)


_ZIP_RE = re.compile(r'\b(\d{5})(?:-\d{4})?\s*$')


def _detect_zip(address):
    """Pull a trailing ZIP code out of an address string, or None. A ZIP can
    stand in for the borough on Geoclient's /address endpoint, which is how
    '184-23 91ST AVE 11432' resolves without the word QUEENS in it."""
    if not address:
        return None
    m = _ZIP_RE.search(address.strip())
    return m.group(1) if m else None


def _house_number_candidates(house):
    """The house number as typed, plus the Queens hyphenated reading.

    Queens addresses are officially '184-23' style, but people type '18423'.
    Geoclient usually normalizes this itself, so the plain form goes first
    and the hyphenated guess is only a retry.
    """
    candidates = [house]
    if house and house.isdigit() and 4 <= len(house) <= 6:
        candidates.append(f"{house[:-2]}-{house[-2:]}")
    return candidates


def _lookup_from_geoclient_address(addr, house, street, borough):
    """Shape one Geoclient address response into our lookup bundle, or None
    if it carries no usable BBL. Both /address and /search responses use
    this same field vocabulary."""
    bbl = addr.get('bbl')
    if not bbl or len(str(bbl)) != 10:
        return None

    # Build a canonical "{house} {street}, {borough}, NY {zip}" string for
    # the buildings.address column.
    pretty_street = addr.get('firstStreetNameNormalized') or street
    canonical = f"{addr.get('houseNumber') or house} {pretty_street}".strip().upper()
    parts = [canonical]
    resolved_borough = (addr.get('firstBoroughName') or borough or '').upper() or None
    city = addr.get('uspsPreferredCityName') or resolved_borough or ''
    if city:
        parts.append(city.upper())
    zip5 = addr.get('zipCode')
    parts.append(f"NY {zip5}" if zip5 else 'NY')
    pretty_address = ', '.join(parts)

    return {
        'bbl': str(bbl),
        'bin': addr.get('buildingIdentificationNumber') or None,
        'latitude': addr.get('latitude'),
        'longitude': addr.get('longitude'),
        'address': pretty_address,
        'borough': resolved_borough.title() if resolved_borough else None,
        'block': addr.get('bblTaxBlock'),
        'lot': addr.get('bblTaxLot'),
    }


def _geoclient_get(path, params):
    """One Geoclient call. Returns the parsed JSON dict, or None on any
    transport/HTTP failure (logged, never raised — a retry with the next
    candidate should still run)."""
    try:
        resp = requests.get(
            f'https://api.nyc.gov/geoclient/v2/{path}',
            params=params,
            headers={'subscription-key': NYC_APP_ID},
            timeout=10,
        )
    except requests.RequestException as e:
        log.warning(f"Geoclient {path} request failed: {e}")
        return None
    if resp.status_code != 200:
        log.warning(f"Geoclient {path} {resp.status_code}: {resp.text[:200]}")
        return None
    try:
        return resp.json() or {}
    except ValueError:
        return None


def resolve_address_to_property(query):
    """Resolve a search query into an address/BBL bundle via Geoclient v2.

    Returns (lookup_dict, None) on success or (None, reason) on failure —
    the reason is user-facing, so it says what to try next.

    Free public API. The Geoclient v2 response already includes BBL, BIN,
    canonical street name, latitude, longitude — we use them all so the
    INSERT can populate as many `buildings` columns as possible.

    Resolution order:
      1. /address with borough (when the query names one) or ZIP
      2. /search with the free-form query — Geoclient tries all boroughs
    Each step also retries with the Queens hyphenated house number
    ('18423' -> '184-23') since that's the official form.
    """
    if not NYC_APP_ID:
        log.warning("NYC_GEOCLIENT_APP_ID not set; cannot resolve addresses")
        return None, ('Address lookup is not configured on this server '
                      '(NYC_GEOCLIENT_APP_ID is missing). You can still '
                      'paste the 10-digit BBL directly.')

    house, street = _parse_house_and_street(query)
    if not house or not street:
        return None, ('That doesn\'t look like a street address. Use '
                      '"HOUSE NUMBER STREET, BOROUGH" (e.g. "141 WYONA '
                      'STREET, BROOKLYN") or paste the 10-digit BBL.')

    borough = _detect_borough(query)
    zip5 = _detect_zip(query)

    # Strip a ZIP that got glued onto the street ("cambridge rd 11432").
    if zip5 and street.upper().endswith(zip5):
        street = street[:-len(zip5)].rstrip(' ,')

    if borough or zip5:
        for house_form in _house_number_candidates(house):
            params = {'houseNumber': house_form, 'street': street}
            if borough:
                params['borough'] = borough
            else:
                params['zip'] = zip5
            data = _geoclient_get('address', params)
            addr = (data or {}).get('address') or {}
            lookup = _lookup_from_geoclient_address(addr, house_form, street, borough)
            if lookup:
                return lookup, None

    # No borough/ZIP in the query, or the strict lookup missed: let
    # Geoclient's single-field search try every borough.
    for house_form in _house_number_candidates(house):
        free_form = f"{house_form} {street}"
        data = _geoclient_get('search', {'input': free_form})
        for result in (data or {}).get('results') or []:
            addr = result.get('response') or {}
            lookup = _lookup_from_geoclient_address(addr, house_form, street, borough)
            if lookup:
                return lookup, None

    return None, (f'Could not match "{query}" to a NYC property. '
                  'Try adding the borough (e.g. "QUEENS") or the ZIP code, '
                  'or paste the 10-digit BBL directly.')


# ---------------------------------------------------------------------------
# Building row creation + enrichment orchestration
# ---------------------------------------------------------------------------

def _ensure_building_row(conn, lookup):
    """Find or create the buildings row keyed by `lookup['bbl']`.
    Returns (building_id, created_flag)."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM buildings WHERE bbl = %s", (lookup['bbl'],))
    row = cur.fetchone()
    if row:
        cur.close()
        return row[0], False
    cur.execute("""
        INSERT INTO buildings (bbl, address, borough, block, lot, bin, last_updated)
        VALUES (%(bbl)s, %(address)s, %(borough)s, %(block)s, %(lot)s, %(bin)s, NOW())
        RETURNING id
    """, lookup)
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    return new_id, True


def _apply_dict_update(conn, building_id, columns_to_values):
    """Run `UPDATE buildings SET k=v,... WHERE id=%s` ignoring None values.
    Returns the count of columns actually updated.
    """
    updates = {k: v for k, v in columns_to_values.items() if v is not None}
    if not updates:
        return 0
    cur = conn.cursor()
    set_clause = ', '.join(f"{k} = %s" for k in updates.keys())
    cur.execute(
        f"UPDATE buildings SET {set_clause}, last_updated = NOW() WHERE id = %s",
        list(updates.values()) + [building_id],
    )
    conn.commit()
    cur.close()
    return len(updates)


def _run_pluto(conn, building_id, bbl):
    # step2 helpers return a (data, error) tuple with normalized keys.
    data, error = get_pluto_data_for_bbl(bbl)
    if error:
        return f'error: {error}'
    if not data:
        return 'no data'
    n = _apply_dict_update(conn, building_id, {
        'current_owner_name': data.get('owner_name'),
        'building_class': data.get('building_class'),
        'land_use': data.get('land_use'),
        'residential_units': data.get('residential_units'),
        'total_units': data.get('total_units'),
        'year_built': data.get('year_built'),
        'year_altered': data.get('year_altered'),
        'num_floors': data.get('num_floors'),
        'building_sqft': data.get('building_sqft'),
        'lot_sqft': data.get('lot_sqft'),
        'zip_code': data.get('zip_code'),
        'latitude': data.get('latitude'),
        'longitude': data.get('longitude'),
        'zoning_district': data.get('zoning_district'),
        'built_far': data.get('built_far'),
        'max_resid_far': data.get('max_resid_far'),
        'max_comm_far': data.get('max_comm_far'),
        'unused_far': data.get('unused_far'),
        'pluto_owner_type': data.get('pluto_owner_type'),
    })
    return f'{n} fields updated'


def _run_rpad(conn, building_id, bbl):
    data, error = get_rpad_data_for_bbl(bbl)
    if error:
        return f'error: {error}'
    if not data:
        return 'no data'
    n = _apply_dict_update(conn, building_id, {
        'owner_name_rpad': data.get('owner_name_rpad'),
        'assessed_land_value': data.get('assessed_land_value'),
        'assessed_total_value': data.get('assessed_total_value'),
    })
    return f'{n} fields updated'


def _run_hpd(conn, building_id, bbl):
    data, error = get_hpd_data_for_bbl(bbl)
    if error:
        return f'error: {error}'
    if not data:
        return 'no data'
    n = _apply_dict_update(conn, building_id, {
        'owner_name_hpd': data.get('owner_name_hpd'),
        'hpd_registration_id': data.get('hpd_registration_id'),
        'hpd_open_violations': data.get('hpd_open_violations'),
        'hpd_total_violations': data.get('hpd_total_violations'),
        'hpd_open_complaints': data.get('hpd_open_complaints'),
        'hpd_total_complaints': data.get('hpd_total_complaints'),
        'hpd_owner_business_address': data.get('hpd_owner_business_address'),
        'hpd_owner_business_city': data.get('hpd_owner_business_city'),
        'hpd_owner_business_state': data.get('hpd_owner_business_state'),
        'hpd_owner_business_zip': data.get('hpd_owner_business_zip'),
        'hpd_agent_name': data.get('hpd_agent_name'),
        'hpd_site_manager_name': data.get('hpd_site_manager_name'),
    })
    return f'{n} fields updated'


def _run_acris(conn, building_id, bbl):
    count = enrich_building_from_acris(conn, building_id, bbl)
    return f'{count} transactions' if count else 'no transactions'


def _run_tax_liens(conn, building_id, bbl):
    data = fetch_tax_lien_data(building_id, bbl)
    if not data:
        return 'no data'
    cur = conn.cursor()
    update_building_tax_lien_data(cur, building_id, data)
    conn.commit()
    cur.close()
    return 'updated'


def _run_sos(conn, building_id, bbl):
    """LLC -> real person via NY Secretary of State. Skipped if no LLC name
    is present on the row yet (e.g. PLUTO returned a real-person owner)."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT current_owner_name, owner_name_rpad, owner_name_hpd
          FROM buildings WHERE id = %s
    """, (building_id,))
    row = cur.fetchone()
    cur.close()
    if not row:
        return 'no building row'

    llc_name, source = get_best_llc_name(dict(row))
    if not llc_name:
        return 'no LLC name'

    # Import here so the heavy async lookup module isn't pulled in at
    # module-load time for callers who don't need SOS.
    from ny_sos_lookup import lookup_businesses
    results = lookup_businesses([llc_name], concurrency=1, timeout=20)
    sos_result = results.get(llc_name)
    if not sos_result or not getattr(sos_result, 'found', False):
        return f'no match for {llc_name!r}'

    sos_fields = process_sos_result(sos_result)
    n = _apply_dict_update(conn, building_id, sos_fields)
    return f'{n} fields updated (source={source})'


# Each step is named for the status report. Ordered so that PLUTO/RPAD/HPD
# populate the owner-name fields before SOS reads them to look up the LLC.
_ENRICHMENT_STEPS = [
    ('pluto',     _run_pluto),
    ('rpad',      _run_rpad),
    ('hpd',       _run_hpd),
    ('acris',     _run_acris),
    ('tax_liens', _run_tax_liens),
    ('sos',       _run_sos),
]


def run_free_enrichment(conn, building_id, bbl):
    """Run every free enrichment step against the given building. Returns
    a dict {step_name: 'ok|failed|skipped reason', ...} for the caller to
    render. Never raises — each step is isolated."""
    report = {}
    for name, fn in _ENRICHMENT_STEPS:
        try:
            report[name] = fn(conn, building_id, bbl)
        except Exception as e:
            log.exception(f"{name} enrichment failed for bbl={bbl}")
            report[name] = f'error: {e}'
            # A failed statement aborts the shared transaction; roll back so
            # the remaining steps still run.
            try:
                conn.rollback()
            except Exception:
                pass
    return report


# ---------------------------------------------------------------------------
# Top-level entry point used by the Flask route
# ---------------------------------------------------------------------------

def _enrich_in_background(connect, building_id, bbl):
    """Run the free enrichment on its own thread and its own connection.

    The web request that started it has already returned — running the six
    enrichment steps inline held a sync gunicorn worker for 10-120s, and
    with only two workers that turned into edge 502s for everyone whenever
    the database was busy. A daemon thread dying with the worker is fine:
    the nightly cron re-enriches whatever was left half-done.
    """
    def run():
        conn = None
        try:
            conn = connect()
            report = run_free_enrichment(conn, building_id, bbl)
            log.info(f"background enrichment for {bbl}: {report}")
        except Exception:
            log.exception(f"background enrichment failed for bbl={bbl}")
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass

    threading.Thread(target=run, name=f'auto-add-{bbl}', daemon=True).start()


def auto_add_property(conn, query, background_connect=None):
    """Main entry point. Resolve the query, ensure a buildings row exists,
    run every free enrichment step. Returns:

      {
        'success': bool,
        'error': str | None,
        'bbl': str | None,
        'building_id': int | None,
        'already_existed': bool,
        'enrichment_running': bool,
        'report': {step_name: status, ...},
      }

    With `background_connect` (a zero-arg callable returning a fresh DB
    connection), only the fast part — resolve + insert — happens on the
    caller's clock; the enrichment steps run on a background thread and the
    report says so. Without it (scripts, pipeline), everything runs inline
    as before.
    """
    if not query or not query.strip():
        return {'success': False, 'error': 'empty query'}

    bbl = _normalize_bbl(query)
    if bbl:
        # User pasted a raw BBL. We don't have an address — Geoclient
        # requires house+street, not BBL, so we INSERT a minimal row with
        # just the BBL and let PLUTO fill in the rest.
        lookup = {'bbl': bbl, 'address': None, 'borough': None,
                  'block': None, 'lot': None, 'bin': None}
        resolved_note = 'accepted as a raw BBL'
    else:
        lookup, reason = resolve_address_to_property(query)
        if not lookup:
            return {'success': False, 'error': reason}
        resolved_note = f"resolved to {lookup['address']}"

    building_id, created = _ensure_building_row(conn, lookup)

    if background_connect is not None:
        _enrich_in_background(background_connect, building_id, lookup['bbl'])
        return {
            'success': True,
            'error': None,
            'bbl': lookup['bbl'],
            'building_id': building_id,
            'already_existed': not created,
            'enrichment_running': True,
            'report': {
                'address': resolved_note,
                'enrichment': ('running in the background — the profile '
                               'fills in as each source lands'),
            },
        }

    report = run_free_enrichment(conn, building_id, lookup['bbl'])

    return {
        'success': True,
        'error': None,
        'bbl': lookup['bbl'],
        'building_id': building_id,
        'already_existed': not created,
        'enrichment_running': False,
        'report': report,
    }
