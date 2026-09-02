"""Google Street View / Maps links for a building.

Two modes, decided by one optional env var:

* ``GOOGLE_MAPS_EMBED_KEY`` set  -> an embeddable Street View iframe URL
  (Maps Embed API, referrer-restricted key; Google bills Embed API requests
  at $0). Street View needs a lat/lng, so the embed falls back to a map of
  the address when the lot has never been geocoded.
* not set -> no iframe; the UI shows an "Open Street View" button using the
  keyless Google Maps URLs API, which works everywhere without a key.

Coordinates come from the permits table (the only geocoded source), so a
building with no permit on file has links but no embed.
"""

import os
from urllib.parse import quote_plus, urlencode


def embed_key():
    return (os.getenv('GOOGLE_MAPS_EMBED_KEY') or '').strip()


def _valid(lat, lng):
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat == 0 and lng == 0):
        return None
    return lat, lng


def lookup_latlng(cur, bbl):
    """Best geocode for a BBL: the newest geocoded permit on the lot."""
    if not bbl:
        return None
    cur.execute(
        """SELECT latitude, longitude FROM permits
           WHERE bbl = %s AND latitude IS NOT NULL AND longitude IS NOT NULL
             AND latitude BETWEEN 40 AND 41.2 AND longitude BETWEEN -74.5 AND -73.4
           ORDER BY COALESCE(filing_date, issue_date) DESC NULLS LAST, id DESC
           LIMIT 1""",
        (str(bbl),),
    )
    row = cur.fetchone()
    if not row:
        return None
    return _valid(row['latitude'], row['longitude'])


def payload(address, lat=None, lng=None, borough=None):
    """Everything a template needs: embed URL (if possible) + open links."""
    coords = _valid(lat, lng)
    place = ', '.join(p for p in (address, borough, 'NY') if p)
    key = embed_key()
    out = {
        'has_coords': coords is not None,
        'embed_url': None,
        'embed_kind': None,
        'open_url': None,
        'map_url': 'https://www.google.com/maps/search/?api=1&query=' + quote_plus(place),
        'apple_url': 'https://maps.apple.com/?q=' + quote_plus(place),
        'key_configured': bool(key),
    }
    if coords:
        lat, lng = coords
        out['open_url'] = (
            'https://www.google.com/maps/@?api=1&map_action=pano'
            f'&viewpoint={lat:.6f},{lng:.6f}'
        )
        if key:
            out['embed_url'] = 'https://www.google.com/maps/embed/v1/streetview?' + urlencode({
                'key': key, 'location': f'{lat:.6f},{lng:.6f}', 'fov': 90, 'pitch': 5,
            })
            out['embed_kind'] = 'streetview'
    else:
        # No geocode: Street View by address is only possible in the full
        # Maps UI, so the open link searches the address with the Street
        # View layer on, and the embed (if keyed) shows a map with a pin.
        out['open_url'] = 'https://www.google.com/maps?' + urlencode({'q': place, 'layer': 'c'})
        if key:
            out['embed_url'] = 'https://www.google.com/maps/embed/v1/place?' + urlencode({
                'key': key, 'q': place, 'zoom': 18,
            })
            out['embed_kind'] = 'map'
    return out
