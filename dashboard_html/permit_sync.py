"""Per-building DOB permit fetch, for auto-added properties.

The nightly city-wide sync lives in permit_scraper_api.py at the repo root;
this module serves one building on demand so an auto-added property shows
its permit history immediately instead of waiting for the next sync window.

The field mapping and column order are a copy of prepare_rows_bis /
BIS_COLUMNS from that scraper — the schema knowledge there is proven by
months of nightly runs against the live dataset. The dashboard deploys
self-contained on Railway (the repo root does not exist in its container),
which is why this is a copy and not an import. A parity test in
filter_param_tests.py fails if the two drift.

The fetch filters only by borough name + zero-padded block — the exact
$where shape verify_datasets.py confirmed against the live dataset — and
matches the lot locally, so an unverified lot-padding guess can't silently
return nothing.
"""

from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

import psycopg2.extras

from socrata_client import SocrataClient, bbl_parts, soql_quote

_client = None


def get_client():
    global _client
    if _client is None:
        _client = SocrataClient()
    return _client

BOROUGH_NAMES = {
    '1': 'MANHATTAN', '2': 'BRONX', '3': 'BROOKLYN',
    '4': 'QUEENS', '5': 'STATEN ISLAND',
}
BOROUGH_MAP = {v: k for k, v in BOROUGH_NAMES.items()}

SELECT_FIELDS = [
    'job__', 'job_type', 'job_start_date', 'expiration_date', 'bin__',
    'house__', 'street_name', 'borough', 'block', 'lot', 'permit_status',
    'filing_date', 'issuance_date', 'zip_code', 'community_board', 'job_doc___', 'self_cert',
    'bldg_type', 'residential', 'special_district_1', 'special_district_2',
    'work_type', 'filing_status', 'permit_type', 'permit_sequence__',
    'permit_subtype', 'oil_gas', 'permittee_s_first_name', 'permittee_s_last_name',
    'permittee_s_business_name', 'permittee_s_phone__', 'permittee_s_license_type',
    'permittee_s_license__', 'act_as_superintendent', 'permittee_s_other_title',
    'hic_license', 'site_safety_mgr_s_first_name', 'site_safety_mgr_s_last_name',
    'site_safety_mgr_business_name', 'superintendent_first___last_name',
    'superintendent_business_name', 'owner_s_business_type', 'non_profit',
    'owner_s_business_name', 'owner_s_first_name', 'owner_s_last_name',
    'owner_s_house__', 'owner_s_house_street_name', 'city', 'state',
    'owner_s_zip_code', 'owner_s_phone__', 'dobrundate', 'permit_si_no',
    'gis_council_district', 'gis_census_tract', 'gis_nta_name',
    'gis_latitude', 'gis_longitude'
]

BIS_COLUMNS = [
    'permit_no', 'job_type', 'issue_date', 'exp_date', 'bin', 'address',
    'applicant', 'block', 'lot', 'status', 'filing_date', 'proposed_job_start',
    'work_description', 'job_number', 'bbl', 'latitude', 'longitude', 'borough',
    'house_number', 'street_name', 'zip_code', 'community_board', 'job_doc_number',
    'self_cert', 'bldg_type', 'residential', 'special_district_1', 'special_district_2',
    'work_type', 'permit_status', 'filing_status', 'permit_type', 'permit_sequence',
    'permit_subtype', 'oil_gas', 'permittee_first_name', 'permittee_last_name',
    'permittee_business_name', 'permittee_phone', 'permittee_license_type',
    'permittee_license_number', 'act_as_superintendent', 'permittee_other_title',
    'hic_license', 'site_safety_mgr_first_name', 'site_safety_mgr_last_name',
    'site_safety_mgr_business_name', 'superintendent_name', 'superintendent_business_name',
    'owner_business_type', 'non_profit', 'owner_business_name', 'owner_first_name',
    'owner_last_name', 'owner_house_number', 'owner_street_name', 'owner_city',
    'owner_state', 'owner_zip_code', 'owner_phone', 'dob_run_date', 'permit_si_no',
    'council_district', 'census_tract', 'nta_name', 'api_source', 'api_last_updated'
]


def trunc(val: Any, max_len: int) -> Optional[str]:
    if val is None:
        return None
    s = str(val)
    return s[:max_len] if len(s) > max_len else s


def parse_date_mdy(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    try:
        clean = str(date_str).split()[0]
        return datetime.strptime(clean, '%m/%d/%Y').date()
    except (ValueError, TypeError, IndexError):
        return None


def build_bbl(borough: Optional[str], block: Optional[str], lot: Optional[str]) -> Optional[str]:
    if not borough or not block or not lot:
        return None
    try:
        borough_upper = str(borough).upper().strip()
        borough_code = BOROUGH_MAP.get(borough_upper, borough_upper)
        if not borough_code.isdigit() or len(borough_code) != 1:
            return None
        block_num = str(block).strip().lstrip('0') or '0'
        lot_num = str(lot).strip().lstrip('0') or '0'
        bbl = f"{borough_code}{block_num.zfill(5)}{lot_num.zfill(4)}"
        if len(bbl) == 10 and bbl.isdigit():
            return bbl
        return None
    except (ValueError, TypeError):
        return None


def safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def prepare_rows_bis(permits: List[Dict]) -> Tuple[List[tuple], int]:
    """Copy of permit_scraper_api.prepare_rows_bis — see module docstring."""
    rows = []
    skipped = 0
    now = datetime.now()

    for p in permits:
        try:
            permit_no = p.get('job__')
            if not permit_no:
                permit_no = f"{p.get('bin__', '')}_{p.get('issuance_date', '')}"
            if not permit_no or permit_no == '_':
                skipped += 1
                continue

            filing_date = parse_date_mdy(p.get('filing_date')) or parse_date_mdy(p.get('issuance_date'))
            issue_date = parse_date_mdy(p.get('issuance_date'))
            exp_date = parse_date_mdy(p.get('expiration_date'))
            job_start = parse_date_mdy(p.get('job_start_date'))
            dob_run = parse_date_mdy(p.get('dobrundate'))

            address = f"{p.get('house__', '')} {p.get('street_name', '')}".strip() or None

            applicant = (
                p.get('permittee_s_business_name') or
                p.get('owner_s_business_name') or
                f"{p.get('owner_s_first_name', '')} {p.get('owner_s_last_name', '')}".strip() or
                None
            )

            work_desc_parts = []
            if p.get('job_type'):
                work_desc_parts.append(f"Type: {p.get('job_type')}")
            if p.get('permit_subtype'):
                work_desc_parts.append(f"Subtype: {p.get('permit_subtype')}")
            if p.get('bldg_type'):
                work_desc_parts.append(f"Building Type: {p.get('bldg_type')}")
            work_description = ', '.join(work_desc_parts) if work_desc_parts else None

            bbl = build_bbl(p.get('borough'), p.get('block'), p.get('lot'))

            row = (
                trunc(permit_no, 100),
                trunc(p.get('job_type'), 500),
                issue_date,
                exp_date,
                trunc(p.get('bin__'), 50),
                address,
                trunc(applicant, 225),
                trunc(p.get('block'), 20),
                trunc(p.get('lot'), 20),
                trunc(p.get('permit_status'), 50),
                filing_date,
                job_start,
                work_description,
                trunc(p.get('job__'), 50),
                bbl,
                safe_float(p.get('gis_latitude')),
                safe_float(p.get('gis_longitude')),
                trunc(p.get('borough'), 20),
                trunc(p.get('house__'), 50),
                trunc(p.get('street_name'), 255),
                trunc(p.get('zip_code'), 15),
                trunc(p.get('community_board'), 3),
                trunc(p.get('job_doc___'), 50),
                trunc(p.get('self_cert'), 20),
                trunc(p.get('bldg_type'), 50),
                trunc(p.get('residential'), 20),
                trunc(p.get('special_district_1'), 50),
                trunc(p.get('special_district_2'), 50),
                trunc(p.get('work_type'), 50),
                trunc(p.get('permit_status'), 50),
                trunc(p.get('filing_status'), 50),
                trunc(p.get('permit_type'), 50),
                trunc(p.get('permit_sequence__'), 50),
                trunc(p.get('permit_subtype'), 50),
                trunc(p.get('oil_gas'), 20),
                trunc(p.get('permittee_s_first_name'), 100),
                trunc(p.get('permittee_s_last_name'), 100),
                trunc(p.get('permittee_s_business_name'), 255),
                trunc(p.get('permittee_s_phone__'), 50),
                trunc(p.get('permittee_s_license_type'), 50),
                trunc(p.get('permittee_s_license__'), 50),
                trunc(p.get('act_as_superintendent'), 20),
                trunc(p.get('permittee_s_other_title'), 100),
                trunc(p.get('hic_license'), 50),
                trunc(p.get('site_safety_mgr_s_first_name'), 100),
                trunc(p.get('site_safety_mgr_s_last_name'), 100),
                trunc(p.get('site_safety_mgr_business_name'), 255),
                trunc(p.get('superintendent_first___last_name'), 255),
                trunc(p.get('superintendent_business_name'), 255),
                trunc(p.get('owner_s_business_type'), 100),
                trunc(p.get('non_profit'), 20),
                trunc(p.get('owner_s_business_name'), 255),
                trunc(p.get('owner_s_first_name'), 100),
                trunc(p.get('owner_s_last_name'), 100),
                trunc(p.get('owner_s_house__'), 50),
                trunc(p.get('owner_s_house_street_name'), 255),
                trunc(p.get('city'), 100),
                trunc(p.get('state'), 20),
                trunc(p.get('owner_s_zip_code'), 15),
                trunc(p.get('owner_s_phone__'), 50),
                dob_run,
                trunc(p.get('permit_si_no'), 50),
                trunc(p.get('gis_council_district'), 20),
                trunc(p.get('gis_census_tract'), 20),
                trunc(p.get('gis_nta_name'), 255),
                'nyc_open_data',
                now
            )
            rows.append(row)
        except Exception:
            skipped += 1
            continue

    seen = {}
    for row in rows:
        seen[row[0]] = row
    deduped = list(seen.values())
    return deduped, skipped + (len(rows) - len(deduped))


def _lot_matches(row: Dict, lot: str) -> bool:
    """Compare the row's lot to ours numerically. The dataset's lot padding
    is unverified, so the filter never goes into the $where — a wrong guess
    there would silently return zero rows forever."""
    raw = ''.join(ch for ch in str(row.get('lot') or '') if ch.isdigit())
    return (raw.lstrip('0') or '0') == lot


def fetch_bis_permits_for_bbl(bbl: str) -> List[Dict]:
    """All BIS permit rows for one lot. The $where uses only the shape
    verify_datasets.py confirmed live: borough name + zero-padded block."""
    boro, _, lot, block_padded, _ = bbl_parts(str(bbl))
    borough_name = BOROUGH_NAMES.get(boro)
    if not borough_name:
        return []
    client = get_client()
    rows = client.get_all(
        'dob_permits_bis',
        page_size=1000,
        **{
            '$select': ','.join(SELECT_FIELDS),
            '$where': (f"borough={soql_quote(borough_name)} AND "
                       f"block={soql_quote(block_padded)}"),
        },
    )
    return [r for r in rows if _lot_matches(r, lot)]


def sync_permits_for_bbl(conn, bbl: str) -> int:
    """Fetch this lot's DOB permits and upsert them into the permits table.
    Returns how many rows were written. Same ON CONFLICT contract as the
    nightly scraper, so the two can never fight over a row."""
    raw = fetch_bis_permits_for_bbl(bbl)
    rows, _skipped = prepare_rows_bis(raw)
    if not rows:
        return 0

    columns = ', '.join(BIS_COLUMNS)
    sql = f"""
        INSERT INTO permits ({columns})
        VALUES %s
        ON CONFLICT (permit_no) DO UPDATE SET
            permit_status = EXCLUDED.permit_status,
            exp_date = EXCLUDED.exp_date,
            filing_date = EXCLUDED.filing_date,
            proposed_job_start = EXCLUDED.proposed_job_start,
            filing_status = EXCLUDED.filing_status,
            api_last_updated = EXCLUDED.api_last_updated
    """
    cur = conn.cursor()
    try:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=200)
        conn.commit()
    finally:
        cur.close()
    return len(rows)
