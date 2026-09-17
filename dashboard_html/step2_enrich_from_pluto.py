#!/usr/bin/env python3
"""
Step 2: Tri-Source Building Enrichment (PLUTO + RPAD + HPD)

Data Sources:
1. NYC PLUTO (MapPLUTO) - Corporate ownership, building characteristics
2. NYC RPAD (Property Tax) - Current taxpayer, assessed values
3. NYC HPD (Housing Preservation) - Registered owner, violations, complaints

Populates:
- Owner data: current_owner_name (PLUTO), owner_name_rpad (RPAD), owner_name_hpd (HPD)
- Building data: units, sqft, year built/altered, building class
- Financial data: assessed values
- Quality indicators: HPD violations and complaints counts
"""

import psycopg2
import psycopg2.extras
import os
import sys
import requests
import time
from threading import local
from datetime import datetime
from dotenv import load_dotenv

from socrata_client import (
    SocrataClient, soql_quote, bbl_parts, normalize_pluto_record,
)

# Force unbuffered output for Railway logging
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

load_dotenv()

# Support both DATABASE_URL and individual DB_* variables
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    # Build from individual components
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME')
    
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        raise ValueError("Either DATABASE_URL or DB_HOST/DB_USER/DB_PASSWORD/DB_NAME must be set")
    
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# NYC Open Data API endpoints
PLUTO_API_BASE = "https://data.cityofnewyork.us/resource/64uk-42ks.json"
RPAD_API_BASE = "https://data.cityofnewyork.us/resource/yjxr-fw8i.json"
HPD_REGISTRATION_API = "https://data.cityofnewyork.us/resource/tesw-yqqr.json"
HPD_CONTACTS_API = "https://data.cityofnewyork.us/resource/feu5-w2e2.json"
HPD_VIOLATIONS_API = "https://data.cityofnewyork.us/resource/wvxf-dwi5.json"
# Use public Housing Maintenance Code Complaints dataset (not the restricted one)
HPD_COMPLAINTS_API = "https://data.cityofnewyork.us/resource/ygpa-z7cr.json"

# Configuration
API_DELAY = float(os.getenv('API_DELAY', '0.1'))
BUILDING_BATCH_SIZE = int(os.getenv('BUILDING_BATCH_SIZE', '500'))

_client = None
_thread_state = local()


def _get_client():
    if _client is not None:
        return _client
    if not hasattr(_thread_state, 'client'):
        _thread_state.client = SocrataClient()
    return _thread_state.client


def _num(value, cast=float):
    try:
        return cast(float(value))
    except (ValueError, TypeError):
        return None


def get_pluto_data_for_bbl(bbl):
    """
    Query NYC Open Data API for PLUTO data by BBL
    Returns (data_dict, error_message) tuple
    """
    try:
        data = _get_client().get('pluto', **{
            "$where": f"bbl={soql_quote(bbl)}",
            "$limit": 1,
        })
        time.sleep(API_DELAY)
        if not data:
            return None, None  # Not found, but not an error

        result = normalize_pluto_record(data[0])
        return result, None

    except Exception as e:
        return None, f"PLUTO API error: {str(e)}"


def get_rpad_data_for_bbl(bbl):
    """
    Query NYC Open Data API for RPAD (Property Tax) data by BBL
    Returns (data_dict, error_message) tuple
    """
    try:
        client = _get_client()
        boro, block, lot, _, _ = bbl_parts(bbl)

        params = {
            "$where": (f"boro={soql_quote(boro)} AND block={soql_quote(block)} "
                       f"AND lot={soql_quote(lot)}"),
            "$limit": 1,
        }
        # The valuation dataset can carry multiple assessment years per
        # parcel; order by the year column (whatever it's called in this
        # vintage of the dataset) so $limit 1 returns the latest.
        columns = client.get_columns('rpad')
        for year_col in ('year', 'yr4', 'yr', 'fin_yr'):
            if year_col in columns:
                params['$order'] = f'{year_col} DESC'
                break

        data = client.get('rpad', **params)
        time.sleep(API_DELAY)
        if not data:
            return None, None  # Not found, but not an error

        record = data[0]
        result = {
            'owner_name_rpad': record.get('owner'),
            'assessed_land_value': _num(record.get('avland'), int),
            'assessed_total_value': _num(record.get('avtot'), int),
        }
        return result, None

    except Exception as e:
        return None, f"RPAD API error: {str(e)}"


def _contact_name(contact):
    corp = (contact.get('corporationname') or '').strip()
    if corp:
        return corp
    person = f"{contact.get('firstname', '') or ''} {contact.get('lastname', '') or ''}".strip()
    return person or None


def _contact_address(contact):
    house = (contact.get('businesshousenumber') or '').strip()
    street = (contact.get('businessstreetname') or '').strip()
    apt = (contact.get('businessapartment') or '').strip()
    line = f"{house} {street}".strip()
    if apt:
        line = f"{line}, {apt}".strip(', ')
    return line or None


def get_hpd_data_for_bbl(bbl):
    """
    Query NYC HPD APIs for owner, contacts, violations, and complaints data
    Returns (data_dict, error_message) tuple
    """
    try:
        client = _get_client()
        boro, block, lot, _, _ = bbl_parts(bbl)

        result = {
            'owner_name_hpd': None,
            'hpd_registration_id': None,
            'hpd_open_violations': 0,
            'hpd_total_violations': 0,
            'hpd_open_complaints': 0,
            'hpd_total_complaints': 0,
            # City-verified mailing address of the registered head officer /
            # owner — free skip-tracing data we previously discarded.
            'hpd_owner_business_address': None,
            'hpd_owner_business_city': None,
            'hpd_owner_business_state': None,
            'hpd_owner_business_zip': None,
            'hpd_agent_name': None,
            'hpd_site_manager_name': None,
        }

        # 1. Most recent HPD registration for the lot
        registration = client.get('hpd_registrations', **{
            'boroid': boro, 'block': block, 'lot': lot,
            '$order': 'registrationenddate DESC', '$limit': 1,
        })
        time.sleep(API_DELAY)
        reg_id = registration[0].get('registrationid') if registration else None
        result['hpd_registration_id'] = reg_id

        # 2. All contacts for the registration in one call. Explicit owner
        # contact types outrank officers: a HeadOfficer is a corporate role,
        # not proof that the person owns the property. We also keep
        # the managing agent and site manager, and the owner's mailing
        # address.
        if reg_id:
            contacts = client.get_all('hpd_contacts', page_size=1000, max_rows=10000,
                                      registrationid=reg_id)
            time.sleep(API_DELAY)
            by_type = {}
            for c in contacts:
                by_type.setdefault((c.get('type') or '').strip(), []).append(c)

            owner_contacts = []
            for contact_type in ('CorporateOwner', 'IndividualOwner', 'JointOwner'):
                owner_contacts.extend(by_type.get(contact_type, []))
            # Officers and managing agents are not independently recorded owners.

            owner_names = []
            for c in owner_contacts:
                name = _contact_name(c)
                if name and name not in owner_names:
                    owner_names.append(name)
                if name and result['hpd_owner_business_address'] is None:
                    result['hpd_owner_business_address'] = _contact_address(c)
                    result['hpd_owner_business_city'] = (c.get('businesscity') or '').strip() or None
                    result['hpd_owner_business_state'] = (c.get('businessstate') or '').strip() or None
                    result['hpd_owner_business_zip'] = (c.get('businesszip') or '').strip() or None
            result['owner_name_hpd'] = ' & '.join(owner_names) or None

            for c in by_type.get('Agent', []):
                result['hpd_agent_name'] = _contact_name(c)
                break
            for c in by_type.get('SiteManager', []):
                result['hpd_site_manager_name'] = _contact_name(c)
                break

        # 3. Violations. violationstatus is the field HPD maintains as the
        # open/closed flag; paginate so big buildings aren't truncated at
        # one page.
        violations = client.get_all('hpd_violations', page_size=1000, max_rows=20000, **{
            '$select': 'violationid,violationstatus',
            '$where': (f"boroid={soql_quote(boro)} AND block={soql_quote(block)} "
                       f"AND lot={soql_quote(lot)}"),
        })
        time.sleep(API_DELAY)
        result['hpd_total_violations'] = len(violations)
        result['hpd_open_violations'] = sum(
            1 for v in violations if (v.get('violationstatus') or '').upper() == 'OPEN')

        # 4. Complaints. Each row in this dataset is a complaint-PROBLEM, so
        # count distinct complaint ids, not rows.
        try:
            problems = client.get_all('hpd_complaints', page_size=1000, max_rows=50000, **{
                '$select': 'complaint_id,complaint_status',
                '$where': f"bbl={soql_quote(bbl)}",
            })
            time.sleep(API_DELAY)
            complaint_status = {}
            for p in problems:
                cid = p.get('complaint_id')
                if not cid:
                    continue
                is_open = (p.get('complaint_status') or '').upper() == 'OPEN'
                complaint_status[cid] = complaint_status.get(cid, False) or is_open
            result['hpd_total_complaints'] = len(complaint_status)
            result['hpd_open_complaints'] = sum(1 for is_open in complaint_status.values() if is_open)
        except Exception as e:
            return None, f'HPD complaints API error: {e}'

        return result, None

    except Exception as e:
        return None, f"HPD API error: {str(e)}"


_column_cache = None


def _buildings_columns(cur):
    """Columns present on buildings — lets this script write the new signal
    fields when the migration has run, and skip them cleanly when not."""
    global _column_cache
    if _column_cache is None:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'buildings'
        """)
        _column_cache = {r['column_name'] for r in cur.fetchall()}
    return _column_cache


def enrich_buildings_from_pluto():
    from property_source_refresh import run_property_refresh
    run_property_refresh(lambda: psycopg2.connect(DATABASE_URL, connect_timeout=10))


if __name__ == '__main__':
    enrich_buildings_from_pluto()
