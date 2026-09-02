"""Where is this building? Google Street View / Maps links for a lot.

Location resolution, best source first:

1. NYC Planning's GeoSearch (free, keyless, official): the address point for
   the house number, verified against the lot's BBL so a bad parse of a
   hyphenated Queens number ("181-10") can't send anyone across the borough.
   Answers are cached in ``building_geocodes`` so each lot is looked up once.
2. The geocode on the lot's permits (DOB open data): the newest permit that
   agrees with the other permits on the lot and sits inside its borough. A
   lone bad row, a rounded placeholder, or a borough-centroid fallback is
   ignored rather than sending the rep to a random spot.
3. Nothing: the UI offers an address search in Google Maps instead of a
   Street View link that would open on a black screen.

Two link modes, decided by one optional env var:

* ``GOOGLE_MAPS_EMBED_KEY`` set -> an embeddable Street View iframe URL
  (Maps Embed API, referrer-restricted key; Google bills Embed API requests
  at $0). Street View needs a lat/lng, so the embed falls back to a map of
  the address when the lot could not be located.
* not set -> no iframe; the UI shows an "Open Street View" button. The link
  uses the classic ``layer=c&cbll=`` form, which snaps to the nearest
  panorama. The Maps URLs API ``map_action=pano&viewpoint=`` form was tried
  first and rejected: it gives up with "No Street View imagery available
  here" when nothing was photographed within 50 m of the point.
"""

import math
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlencode

import requests

GEOSEARCH_URL = 'https://geosearch.planninglabs.nyc/v2/search'
GEOSEARCH_TIMEOUT = 3               # seconds; the CRM renders this synchronously
NEGATIVE_TTL = timedelta(days=7)    # "no match": ask GeoSearch again in a week
ERROR_TTL = timedelta(minutes=10)   # "GeoSearch was down": ask again soon
AGREE_METERS = 150                  # permits on one lot should sit this close

# Generous bounding boxes. Borough = first digit of the BBL.
NYC_BBOX = (40.45, 40.95, -74.30, -73.65)
BOROUGH_BBOX = {
    '1': (40.68, 40.89, -74.03, -73.90),   # Manhattan (incl. Marble Hill)
    '2': (40.78, 40.93, -73.94, -73.74),   # Bronx (incl. City Island)
    '3': (40.55, 40.75, -74.05, -73.83),   # Brooklyn
    '4': (40.53, 40.82, -73.97, -73.69),   # Queens (incl. the Rockaways)
    '5': (40.48, 40.66, -74.27, -74.04),   # Staten Island
}
BOROUGH_NAME = {'1': 'Manhattan', '2': 'Bronx', '3': 'Brooklyn', '4': 'Queens', '5': 'Staten Island'}

GEOCODE_SCHEMA = """
CREATE TABLE IF NOT EXISTS building_geocodes (
    geo_key     VARCHAR(160) PRIMARY KEY,
    latitude    DOUBLE PRECISION,
    longitude   DOUBLE PRECISION,
    source      VARCHAR(20) NOT NULL,
    label       TEXT,
    matched_bbl VARCHAR(10),
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)"""

_HOUSE_RE = re.compile(r'^\s*(\d+[A-Za-z]?(?:\s*-\s*\d+[A-Za-z]?)?)')


def embed_key():
    return (os.getenv('GOOGLE_MAPS_EMBED_KEY') or '').strip()


def init_geocode_table():
    """Idempotent; runs from app.init_db_pool() next to the other schema inits."""
    from crm_service import get_db_connection  # same env precedence as the app
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(GEOCODE_SCHEMA)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# --------------------------------------------------------------------------
# Coordinate sanity
# --------------------------------------------------------------------------

def _inside(box, lat, lng):
    return box[0] <= lat <= box[1] and box[2] <= lng <= box[3]


def _valid(lat, lng, bbl=None):
    """(lat, lng) as floats when they plausibly locate an NYC lot, else None."""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if math.isnan(lat) or math.isnan(lng) or not _inside(NYC_BBOX, lat, lng):
        return None
    # Two decimals is ~1 km: a rounded placeholder, not a geocode.
    if round(lat, 2) == lat and round(lng, 2) == lng:
        return None
    box = BOROUGH_BBOX.get(str(bbl or '')[:1])
    if box and not _inside(box, lat, lng):
        return None
    return lat, lng


def _meters(a, b):
    dlat = (a[0] - b[0]) * 111_320
    dlng = (a[1] - b[1]) * 111_320 * math.cos(math.radians(a[0]))
    return math.hypot(dlat, dlng)


def _house_number(text):
    m = _HOUSE_RE.match(text or '')
    return re.sub(r'\s+', '', m.group(1)).upper() if m else ''


# --------------------------------------------------------------------------
# Source 2: the lot's permits
# --------------------------------------------------------------------------

def lookup_latlng(cur, bbl):
    """Best permit geocode for a BBL, newest first, outliers ignored."""
    if not bbl:
        return None
    cur.execute(
        """SELECT latitude, longitude FROM permits
           WHERE bbl = %s AND latitude IS NOT NULL AND longitude IS NOT NULL
           ORDER BY COALESCE(filing_date, issue_date) DESC NULLS LAST, id DESC
           LIMIT 25""",
        (str(bbl),),
    )
    points = []
    for row in cur.fetchall():
        loc = _valid(row['latitude'], row['longitude'], bbl)
        if loc:
            points.append(loc)
    if not points:
        return None
    if len(points) == 1:
        return points[0]
    lats = sorted(p[0] for p in points)
    lngs = sorted(p[1] for p in points)
    middle = (lats[len(lats) // 2], lngs[len(lngs) // 2])
    for point in points:  # newest that agrees with the rest of the lot
        if _meters(point, middle) <= AGREE_METERS:
            return point
    return min(points, key=lambda p: _meters(p, middle))


# --------------------------------------------------------------------------
# Source 1: NYC GeoSearch
# --------------------------------------------------------------------------

def _geosearch_request(params):
    resp = requests.get(GEOSEARCH_URL, params=params, timeout=GEOSEARCH_TIMEOUT)
    resp.raise_for_status()
    return resp.json() or {}


def geosearch(address, borough=None, bbl=None):
    """(lat, lng, label, matched_bbl) for an address, or None when GeoSearch
    returns nothing we trust. Raises on transport failure so the caller can
    tell "no match" from "service down"."""
    want_bbl = str(bbl or '').strip()
    borough = borough or BOROUGH_NAME.get(want_bbl[:1]) or ''
    text = ', '.join(p for p in (address, borough, 'NY') if p)
    data = _geosearch_request({'text': text, 'size': 5})
    want_house = _house_number(address)
    fallback = None
    for feature in data.get('features') or []:
        props = feature.get('properties') or {}
        pad = (props.get('addendum') or {}).get('pad') or {}
        coords = (feature.get('geometry') or {}).get('coordinates') or [None, None]
        loc = _valid(coords[1], coords[0], want_bbl or None)
        if not loc:
            continue
        got_bbl = str(pad.get('bbl') or '')
        hit = (loc[0], loc[1], props.get('label'), got_bbl or None)
        if want_bbl and got_bbl == want_bbl:
            return hit
        # Same house number, same borough, exact match: accept even when the
        # PAD BBL differs (condo billing lots, merged/apportioned lots).
        same_house = bool(want_house) and _house_number(props.get('housenumber')) == want_house
        same_boro = not borough or (props.get('borough') or '').lower() == borough.lower()
        if fallback is None and same_house and same_boro and props.get('match_type') == 'exact':
            fallback = hit
    return fallback


# --------------------------------------------------------------------------
# Cache (building_geocodes)
# --------------------------------------------------------------------------

def _cache_get(cur, key):
    cur.execute(
        "SELECT latitude, longitude, source, fetched_at FROM building_geocodes WHERE geo_key = %s",
        (key,),
    )
    return cur.fetchone()


def _cache_put(cur, key, hit, source):
    lat, lng, label, matched = hit or (None, None, None, None)
    cur.execute(
        """INSERT INTO building_geocodes (geo_key, latitude, longitude, source, label, matched_bbl, fetched_at)
           VALUES (%s, %s, %s, %s, %s, %s, NOW())
           ON CONFLICT (geo_key) DO UPDATE SET
               latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude,
               source = EXCLUDED.source, label = EXCLUDED.label,
               matched_bbl = EXCLUDED.matched_bbl, fetched_at = NOW()""",
        (key, lat, lng, source, label, matched),
    )
    return True


def _guarded(cur, fn, *args):
    """Run a cache statement inside a savepoint: a missing table (first boot
    before the schema init ran) must not poison the caller's transaction."""
    try:
        cur.execute('SAVEPOINT sv_geocode')
        out = fn(cur, *args)
        cur.execute('RELEASE SAVEPOINT sv_geocode')
        return out
    except Exception as e:
        try:
            cur.execute('ROLLBACK TO SAVEPOINT sv_geocode')
        except Exception:
            pass
        print(f"[streetview] geocode cache skipped: {e}", flush=True)
        return None


def _cache_key(bbl, address, borough):
    if bbl:
        return bbl
    if not address:
        return None
    return 'addr:' + re.sub(r'\s+', ' ', f'{address} {borough or ""}').strip().upper()[:150]


# --------------------------------------------------------------------------
# Public: resolve + payload
# --------------------------------------------------------------------------

def resolve(cur, bbl=None, address=None, borough=None):
    """Best location for a lot as {'lat', 'lng', 'source'} or None.

    ``source`` is 'geosearch' (NYC address point) or 'permit' (DOB geocode).
    Cheap after the first call per lot: GeoSearch answers, including "no
    match", are cached. The caller owns the transaction (commit to keep the
    cache)."""
    bbl = str(bbl or '').strip() or None
    key = _cache_key(bbl, address, borough)
    ask_geosearch = bool(key and address)
    cached = _guarded(cur, _cache_get, key) if key else None
    if cached:
        if cached['latitude'] is not None:
            loc = _valid(cached['latitude'], cached['longitude'], bbl)
            if loc:
                return {'lat': loc[0], 'lng': loc[1], 'source': cached['source']}
        else:
            ttl = ERROR_TTL if cached['source'] == 'error' else NEGATIVE_TTL
            fetched = cached['fetched_at']
            if fetched and datetime.now(timezone.utc) - fetched < ttl:
                ask_geosearch = False
    if ask_geosearch:
        try:
            hit = geosearch(address, borough, bbl)
        except Exception as e:
            print(f"[streetview] GeoSearch failed for {address!r}: {e}", flush=True)
            _guarded(cur, _cache_put, key, None, 'error')
        else:
            _guarded(cur, _cache_put, key, hit, 'geosearch' if hit else 'none')
            if hit:
                return {'lat': hit[0], 'lng': hit[1], 'source': 'geosearch'}
    if bbl:
        loc = lookup_latlng(cur, bbl)
        if loc:
            return {'lat': loc[0], 'lng': loc[1], 'source': 'permit'}
    return None


def payload(address, lat=None, lng=None, borough=None, source=None):
    """Everything a template needs: embed URL (if possible) + open links."""
    coords = _valid(lat, lng)
    place = ', '.join(p for p in (address, borough, 'NY') if p)
    key = embed_key()
    map_url = 'https://www.google.com/maps/search/?api=1&query=' + quote_plus(place)
    out = {
        'has_coords': coords is not None,
        'lat': coords[0] if coords else None,
        'lng': coords[1] if coords else None,
        'source': source if coords else None,
        'embed_url': None,
        'embed_kind': None,
        'open_url': map_url,      # what the Street View button opens
        'open_kind': 'map',       # 'streetview' when we could pinpoint the lot
        'map_url': map_url,
        'apple_url': 'https://maps.apple.com/?q=' + quote_plus(place),
        'key_configured': bool(key),
    }
    if coords:
        lat, lng = coords
        at = f'{lat:.6f},{lng:.6f}'
        out['open_url'] = f'https://www.google.com/maps?q={at}&layer=c&cbll={at}'
        out['open_kind'] = 'streetview'
        if key:
            out['embed_url'] = 'https://www.google.com/maps/embed/v1/streetview?' + urlencode({
                'key': key, 'location': at, 'fov': 90, 'pitch': 5,
            })
            out['embed_kind'] = 'streetview'
    elif key:
        # Could not pinpoint the lot: a map with a pin beats a black panorama.
        out['embed_url'] = 'https://www.google.com/maps/embed/v1/place?' + urlencode({
            'key': key, 'q': place, 'zoom': 18,
        })
        out['embed_kind'] = 'map'
    return out
