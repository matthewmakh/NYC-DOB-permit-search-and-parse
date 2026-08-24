#!/usr/bin/env python3
"""
Shared NYC Open Data (Socrata) client.

One place for the conventions every pipeline script needs:
- app-token auth (SOCRATA_APP_TOKEN) on every request
- retry with backoff on 429/5xx
- pagination past Socrata's per-request limit
- padding-agnostic block/lot filters (ECB pads to 5/4 digits, most
  DOF datasets strip zeros — querying both forms is always safe because
  both forms describe the same physical lot)
- batched `field in (...)` queries so per-document lookups don't become
  one HTTP call per document
- a registry of every dataset ID the project uses
- the ACRIS party-role map: what party_type 1/2/3 MEAN depends on the
  document type (for a DEED, party 1 is the GRANTOR/seller; for a MTGE,
  party 1 is the MORTGAGOR/borrower). Sourced live from the Document
  Control Codes dataset with a static fallback.
"""

import os
import time
import requests

BASE_URL = "https://data.cityofnewyork.us"

# ---------------------------------------------------------------------------
# Dataset registry — single source of truth for IDs.
# ---------------------------------------------------------------------------

DATASETS = {
    # ACRIS
    'acris_master':        'bnx9-e6tj',   # ACRIS - Real Property Master
    'acris_legals':        '8h5j-fqxa',   # ACRIS - Real Property Legals
    'acris_parties':       '636b-3b5g',   # ACRIS - Real Property Parties
    'acris_references':    'pwkr-dpni',   # ACRIS - Real Property References
    'acris_remarks':       '9p4w-7npp',   # ACRIS - Real Property Remarks
    'acris_doc_codes':     '7isb-wh4c',   # ACRIS - Document Control Codes
    'acris_deeds_view':    'vayk-bjrk',   # ACRIS DEEDs (pre-filtered view of master)

    # Property basics
    'pluto':               '64uk-42ks',   # Primary Land Use Tax Lot Output
    'rpad':                'yjxr-fw8i',   # Property Valuation and Assessment Data

    # HPD
    'hpd_registrations':   'tesw-yqqr',
    'hpd_contacts':        'feu5-w2e2',
    'hpd_violations':      'wvxf-dwi5',
    'hpd_complaints':      'ygpa-z7cr',   # Complaints and Problems (row = problem)
    'hpd_litigation':      '59kj-x8nc',   # Housing Litigations
    'speculation_watch':   'adax-9mit',   # Speculation Watch List

    # DOB
    'dob_permits_bis':     'ipu4-2q9a',
    'dob_now_filings':     'w9ak-ipjd',
    'dob_now_permits':     'rbx6-tga4',
    'dob_now_electrical':  'dm9a-ab7w',
    'dob_now_electrical_details': 'xmmq-y7za',
    'dob_now_elevator':    'kfp4-dz4h',
    'city_record':         'dg92-zbpx',
    'dob_violations':      '3h2n-5cm9',
    'ecb_violations':      '6bgk-3dad',
    'dob_complaints':      'eabe-havv',   # DOB Complaints Received
    'dob_co_bis':          'bs8b-p36w',   # DOB Certificate Of Occupancy (legacy)
    'dob_co_now':          'pkdm-hqz6',   # DOB NOW: Certificate of Occupancy
    'fisp_facades':        'xubg-57si',   # DOB NOW: Safety – Facades Compliance Filings

    # DOF
    'tax_lien_sale':       '9rz4-mjek',   # Tax Lien Sale Lists (notice lists per cycle)
    'exemptions':          'muvi-b6kx',   # Property Exemption Detail
    'rolling_sales':       'usep-8jbt',   # NYC Citywide Rolling Calendar Sales

    # Other
    'evictions':           '6z8x-wfk4',   # Marshal evictions 2017+
    'll84_energy':         '5zyy-y8am',   # LL84 energy/water disclosure 2022+
}


# ---------------------------------------------------------------------------
# BBL helpers
# ---------------------------------------------------------------------------

def bbl_parts(bbl):
    """Split a 10-digit BBL into (boro, block, lot, block_padded, lot_padded)."""
    bbl = str(bbl)
    boro = bbl[0]
    block_padded = bbl[1:6]
    lot_padded = bbl[6:10]
    block = block_padded.lstrip('0') or '0'
    lot = lot_padded.lstrip('0') or '0'
    return boro, block, lot, block_padded, lot_padded


def soql_quote(value):
    """Quote a value for interpolation into a $where clause."""
    return "'" + str(value).replace("'", "''") + "'"


def in_clause(field, values):
    """Build `field in ('a','b',...)` for SoQL."""
    quoted = ",".join(soql_quote(v) for v in values)
    return f"{field} in ({quoted})"


def where_block_lot(boro_field, block_field, lot_field, bbl):
    """
    Padding-agnostic block/lot filter. Some BIS-derived datasets store
    zero-padded blocks ('05317'), DOF ones store stripped ('5317');
    matching either form of the same number can never match a different
    lot, so this works on both without needing to know which.
    """
    boro, block, lot, block_p, lot_p = bbl_parts(bbl)
    block_forms = {block, block_p}
    lot_forms = {lot, lot_p}
    return (f"{boro_field}={soql_quote(boro)} AND "
            f"{in_clause(block_field, sorted(block_forms))} AND "
            f"{in_clause(lot_field, sorted(lot_forms))}")


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class SocrataClient:
    """Thin Socrata SODA2 client with auth, retry, and pagination."""

    def __init__(self, timeout=20, max_retries=4):
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        token = os.getenv('SOCRATA_APP_TOKEN')
        if token:
            self.session.headers['X-App-Token'] = token
        self._columns_cache = {}

    def _url(self, dataset):
        dataset_id = DATASETS.get(dataset, dataset)
        return f"{BASE_URL}/resource/{dataset_id}.json"

    def get(self, dataset, **params):
        """Single request. Returns a list of row dicts ([] on no data).
        Raises on persistent HTTP failure so callers can distinguish
        'no rows' from 'API broken'."""
        url = self._url(dataset)
        last_error = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_error = f"HTTP {resp.status_code}"
                    time.sleep(2 ** attempt)
                    continue
                # 4xx other than 429: caller's query is wrong — don't retry
                raise SocrataError(f"HTTP {resp.status_code} for {dataset}: {resp.text[:300]}")
            except requests.RequestException as e:
                last_error = str(e)
                time.sleep(2 ** attempt)
        raise SocrataError(f"Request failed after {self.max_retries} attempts ({last_error})")

    def get_all(self, dataset, page_size=1000, max_rows=100000, **params):
        """Paginate until short page / max_rows. Requires an $order for
        deterministic paging; defaults to :id which every dataset has."""
        params = dict(params)
        params.setdefault('$order', ':id')
        rows = []
        offset = 0
        while True:
            page = self.get(dataset, **params, **{'$limit': page_size, '$offset': offset})
            rows.extend(page)
            if len(page) < page_size or len(rows) >= max_rows:
                return rows
            offset += page_size

    def get_batched(self, dataset, field, values, batch_size=50, select=None, extra_where=None):
        """Fetch rows where `field` is any of `values`, in batches.
        Returns one combined list."""
        values = [v for v in values if v]
        rows = []
        for i in range(0, len(values), batch_size):
            batch = values[i:i + batch_size]
            where = in_clause(field, batch)
            if extra_where:
                where = f"({where}) AND ({extra_where})"
            params = {'$where': where, '$limit': max(1000, batch_size * 40)}
            if select:
                params['$select'] = select
            rows.extend(self.get(dataset, **params))
        return rows

    def get_columns(self, dataset):
        """Column fieldNames for a dataset, from the views metadata API.
        Cached per client. Lets callers adapt to schema differences
        (e.g. whether evictions has a bbl column) without hardcoding."""
        dataset_id = DATASETS.get(dataset, dataset)
        if dataset_id in self._columns_cache:
            return self._columns_cache[dataset_id]
        try:
            resp = self.session.get(f"{BASE_URL}/api/views/{dataset_id}.json", timeout=self.timeout)
            resp.raise_for_status()
            cols = {c['fieldName'] for c in resp.json().get('columns', []) if 'fieldName' in c}
        except Exception:
            cols = set()
        self._columns_cache[dataset_id] = cols
        return cols


class SocrataError(Exception):
    pass


# ---------------------------------------------------------------------------
# ACRIS party roles
# ---------------------------------------------------------------------------
# The parties dataset only carries party_type 1/2/3; the role each number
# plays is defined per doc_type by the Document Control Codes dataset.
# We bucket DOF's labels into stable roles the rest of the pipeline uses:
#   seller / buyer / lender / borrower / other

_ROLE_KEYWORDS = [
    # Assignments transfer a loan/security interest, not the real property.
    # Keeping these roles distinct prevents banks in an ASST from appearing
    # as former owners merely because ASSIGNOR used to bucket as "seller".
    ('assignor',  ('ASSIGNOR',)),
    ('assignee',  ('ASSIGNEE',)),
    ('seller',   ('GRANTOR', 'SELLER')),
    ('buyer',    ('GRANTEE', 'BUYER')),
    ('borrower', ('MORTGAGOR', 'BORROWER', 'DEBTOR')),
    ('lender',   ('MORTGAGEE', 'LENDER', 'SECURED')),
]

# Fallback if the control-codes dataset is unreachable. Covers the doc
# families this pipeline summarizes; anything else maps to 'other'.
_FALLBACK_ROLES = {
    'DEED':  {'1': 'seller', '2': 'buyer'},
    'MTGE':  {'1': 'borrower', '2': 'lender'},
    'M&CON': {'1': 'borrower', '2': 'lender'},
    'AGMT':  {'1': 'borrower', '2': 'lender'},
    'SAT':   {'1': 'borrower', '2': 'lender'},
    'ASST':  {'1': 'assignor', '2': 'assignee'},
    'CNTR':  {'1': 'seller', '2': 'buyer'},
}

_doc_roles_cache = None


def _bucket_label(label):
    label = (label or '').upper()
    for role, keywords in _ROLE_KEYWORDS:
        if any(k in label for k in keywords):
            return role
    return 'other'


def load_party_roles(client=None):
    """{doc_type: {'1': role, '2': role, '3': role}} from the Document
    Control Codes dataset; static fallback offline. Cached per process."""
    global _doc_roles_cache
    if _doc_roles_cache is not None:
        return _doc_roles_cache
    roles = {}
    try:
        client = client or SocrataClient()
        rows = client.get_all('acris_doc_codes', page_size=500)
        for row in rows:
            doc_type = (row.get('doc__type') or row.get('doc_type') or '').strip().upper()
            if not doc_type:
                continue
            roles[doc_type] = {
                '1': _bucket_label(row.get('party1_type')),
                '2': _bucket_label(row.get('party2_type')),
                '3': _bucket_label(row.get('party3_type')),
            }
    except Exception:
        roles = {}
    _doc_roles_cache = roles
    return roles


def party_role(doc_type, party_type, roles=None):
    """Resolve the role a party_type number plays on a given doc type."""
    doc_type = (doc_type or '').strip().upper()
    party_type = str(party_type or '').strip()
    roles = roles if roles is not None else load_party_roles()
    if doc_type in roles and party_type in roles[doc_type]:
        role = roles[doc_type][party_type]
        if role != 'other':
            return role
    for prefix, mapping in _FALLBACK_ROLES.items():
        if doc_type.startswith(prefix):
            return mapping.get(party_type, 'other')
    if 'DEED' in doc_type:
        return _FALLBACK_ROLES['DEED'].get(party_type, 'other')
    return 'other'


def is_deed(doc_type):
    return 'DEED' in (doc_type or '').upper()


def is_ownership_party(doc_type, role):
    """True only for grantee/grantor roles on a deed instrument.

    ACRIS reuses numeric party types across mortgages, assignments, trusts,
    agreements, and deeds. Role text alone is therefore not proof that the
    party ever owned the real property.
    """
    return is_deed(doc_type) and role in ('buyer', 'seller')


def is_mortgage(doc_type):
    dt = (doc_type or '').upper()
    # M&CON is NYC's "Mortgage and Consolidation" instrument. Excluding it
    # made an older, already-satisfied MTGE look like the current loan.
    return dt == 'M&CON' or dt == 'MTGE' or dt.startswith('MTGE')


def is_satisfaction(doc_type):
    dt = (doc_type or '').upper()
    return dt in ('SAT', 'SATS') or dt.startswith('SAT')
