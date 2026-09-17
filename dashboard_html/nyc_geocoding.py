"""NYC-only permit geocoding with parcel validation and explicit service errors."""

import os
import re
from dataclasses import dataclass

import requests

from streetview import BOROUGH_NAME, _valid as valid_coordinates


def geoclient_key():
    """V2 uses one subscription key; retain the old environment names."""
    return next((os.environ[name].strip() for name in (
        'NYC_GEOCLIENT_SUBSCRIPTION_KEY', 'NYC_GEOCLIENT_APP_KEY',
        'NYC_GEOCLIENT_APP_ID',
    ) if os.environ.get(name, '').strip()), '')


def geoclient_headers():
    return {'Ocp-Apim-Subscription-Key': geoclient_key()}


def normalize_bbl(value):
    value = str(value or '').strip()
    return value if re.fullmatch(r'[1-5]\d{9}', value) else ''


def parse_address(address):
    """Only strip a trailing borough, never MANHATTAN from MANHATTAN PLAZA."""
    address = ' '.join((address or '').upper().split())
    borough = None
    for name in BOROUGH_NAME.values():
        suffix = re.search(
            r'(?:,\s*|\s+)' + re.escape(name.upper())
            + r'(?:,?\s+NY(?:\s+\d{5})?)?\s*$', address)
        if suffix:
            borough = name
            address = address[:suffix.start()]
            break
    match = re.fullmatch(r'(\d+[A-Z]?(?:-\d+[A-Z]?)?)\s+(.+)', address)
    return (match.group(1), match.group(2), borough) if match else (None, None, borough)


def _street(value):
    value = re.sub(r'(\d+)(?:ST|ND|RD|TH)\b', r'\1', (value or '').upper())
    words = re.sub(r'[^A-Z0-9]+', ' ', value).split()
    aliases = {'ST': 'STREET', 'AVE': 'AVENUE', 'RD': 'ROAD', 'BLVD': 'BOULEVARD',
               'PL': 'PLACE', 'DR': 'DRIVE', 'CT': 'COURT', 'LN': 'LANE',
               'PKWY': 'PARKWAY', 'E': 'EAST', 'W': 'WEST', 'N': 'NORTH', 'S': 'SOUTH'}
    return ' '.join(aliases.get(word, word) for word in words)


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float = None
    longitude: float = None
    source: str = ''
    error: str = ''

    @property
    def found(self):
        return self.latitude is not None and self.longitude is not None


class PermitGeocoder:
    def __init__(self):
        self.cache = {}
        self.geoclient_disabled = ''

    def _geoclient(self, path, params):
        if self.geoclient_disabled:
            return None, self.geoclient_disabled
        try:
            response = requests.get(
                f'https://api.nyc.gov/geoclient/v2/{path}', params=params,
                headers=geoclient_headers(), timeout=10)
            if response.status_code in (401, 403):
                self.geoclient_disabled = (
                    f'Geoclient HTTP {response.status_code}: check the v2 subscription key; '
                    'skipping further Geoclient calls this run')
                return None, self.geoclient_disabled
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get(path), dict):
                return None, 'Geoclient returned an invalid response'
            return data[path], ''
        except requests.HTTPError as exc:
            return None, f'Geoclient HTTP {exc.response.status_code}'
        except (requests.RequestException, ValueError) as exc:
            return None, f'Geoclient {type(exc).__name__}'

    def lookup(self, address, bbl=None):
        bbl = normalize_bbl(bbl)
        house, street, parsed_borough = parse_address(address)
        borough = BOROUGH_NAME.get(bbl[:1]) or parsed_borough
        key = (' '.join((address or '').upper().split()), bbl)
        if key in self.cache:
            return self.cache[key]
        errors = []
        if geoclient_key():
            # A BBL request also handles unusual site addresses and condo lots.
            calls = []
            if house and street and borough:
                calls.append(('address', {'houseNumber': house, 'street': street, 'borough': borough}))
            if bbl:
                calls.append(('bbl', {'borough': bbl[0], 'block': bbl[1:6], 'lot': bbl[6:]}))
            for path, params in calls:
                data, error = self._geoclient(path, params)
                if error:
                    errors.append(error)
                    break
                got_bbl = normalize_bbl(data.get('bbl'))
                location = valid_coordinates(data.get('latitude'), data.get('longitude'), got_bbl or bbl)
                # Do not trust a successful address lookup for another parcel.
                if location and got_bbl and (not bbl or bbl == got_bbl):
                    if not borough or BOROUGH_NAME.get(got_bbl[0]) == borough:
                        hit = GeocodeResult(*location, source='Geoclient')
                        self.cache[key] = hit
                        return hit

        # GeoSearch uses NYC's Property Address Directory, not a global place search.
        query = ', '.join(part for part in (address, borough, 'New York, NY') if part)
        try:
            response = requests.get('https://geosearch.planninglabs.nyc/v2/search',
                                    params={'text': query, 'size': 5}, timeout=10)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get('features'), list):
                raise ValueError('Invalid GeoSearch response')
            for feature in data['features']:
                if not isinstance(feature, dict):
                    errors.append('GeoSearch returned an invalid feature')
                    continue
                props = feature.get('properties') or {}
                geometry = feature.get('geometry') or {}
                if not isinstance(props, dict) or not isinstance(geometry, dict):
                    errors.append('GeoSearch returned an invalid feature')
                    continue
                addendum = props.get('addendum') or {}
                pad = addendum.get('pad') or {} if isinstance(addendum, dict) else None
                if not isinstance(pad, dict):
                    errors.append('GeoSearch returned invalid parcel data')
                    continue
                got_bbl = normalize_bbl(pad.get('bbl') or props.get('pad_bbl'))
                coords = geometry.get('coordinates') or []
                if not isinstance(coords, list):
                    errors.append('GeoSearch returned invalid coordinates')
                    continue
                if len(coords) < 2 or not got_bbl:
                    continue
                location = valid_coordinates(coords[1], coords[0], got_bbl)
                if not location:
                    continue
                if bbl:
                    matches = got_bbl == bbl
                else:
                    matches = (bool(house and street and borough)
                               and props.get('match_type') == 'exact'
                               and str(props.get('housenumber') or '').upper() == house
                               and _street(props.get('street')) == _street(street)
                               and BOROUGH_NAME.get(got_bbl[0]) == borough)
                if matches:
                    hit = GeocodeResult(*location, source='NYC GeoSearch')
                    self.cache[key] = hit
                    return hit
        except requests.HTTPError as exc:
            errors.append(f'GeoSearch HTTP {exc.response.status_code}')
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f'GeoSearch {type(exc).__name__}')
        result = GeocodeResult(error='; '.join(dict.fromkeys(errors)))
        if not result.error:
            self.cache[key] = result
        return result
