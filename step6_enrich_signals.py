#!/usr/bin/env python3
"""
Step 6: Distress, compliance, and freshness signals.

Per building, pulls from NYC Open Data:
- HPD Housing Litigations (city suing the owner — distress)
- Marshal evictions (landlord distress)
- DOF Property Exemption Detail (senior/disabled owner-occupants)
- HPD Speculation Watch List (flagged speculative purchases)
- DOB Complaints (illegal work / activity before permits)
- Certificates of Occupancy, BIS + DOB NOW (completion + freshness)
- FISP/LL11 facade compliance filings (upcoming facade work)
- LL84 energy disclosure + estimated LL97 coverage (retrofit demand)
- DOF Rolling Sales (clean arm's-length sale cross-check)

Several of these datasets are keyed by BIN rather than BBL, and column
names drift between vintages — fetchers probe the live schema via the
dataset metadata and adapt, so a renamed column degrades to a skipped
signal instead of a crash.

Requires migrate_add_intel_signals.py to have run.
Refreshes every SIGNALS_REFRESH_DAYS (default 30).
"""

import os
import sys
import time
from datetime import datetime, date
from threading import Lock, local

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

import _pipeline_path  # noqa: F401  (puts dashboard_html on sys.path)
from enrichment_batch import run_checkpointed_batch
from socrata_client import SocrataClient, where_block_lot, soql_quote, bbl_parts, in_clause

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

load_dotenv()
load_dotenv('dashboard_html/.env')

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME')
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        raise ValueError("Either DATABASE_URL or DB_HOST/DB_USER/DB_PASSWORD/DB_NAME must be set")
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

MAX_WORKERS = int(os.getenv('MAX_WORKERS', '8'))
SIGNALS_REFRESH_DAYS = int(os.getenv('SIGNALS_REFRESH_DAYS', '30'))
SIGNALS_MAX_BUILDINGS = max(0, int(os.getenv('SIGNALS_MAX_BUILDINGS', '0')))
SIGNALS_PROGRESS_EVERY = max(1, int(os.getenv('SIGNALS_PROGRESS_EVERY', '100')))
# Bump this whenever source mappings or signal semantics change. A building is
# current only after every source succeeds under this version.
SIGNALS_ENRICHMENT_VERSION = 4
# LL97 broadly covers buildings over 25,000 sqft; the official covered-
# buildings list is only published as a DOB spreadsheet, so we estimate.
LL97_SQFT_THRESHOLD = 25000

_print_lock = Lock()
_thread_state = local()
# Tests may install a deterministic shared stub here. Production threads each
# get their own requests.Session through _get_client().
client = None


def _get_client():
    if client is not None:
        return client
    if not hasattr(_thread_state, 'client'):
        _thread_state.client = SocrataClient()
    return _thread_state.client


def parse_any_date(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    # DOB NOW uses strings like "06/18/25  9:43:09 AM", unlike BIS ISO dates.
    date_text = text.split()[0][:10]
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%Y%m%d', '%m/%d/%y'):
        try:
            return datetime.strptime(date_text, fmt).date()
        except ValueError:
            continue
    return None


def parse_money(value):
    try:
        return float(str(value).replace(',', '').replace('$', ''))
    except (ValueError, TypeError, AttributeError):
        return None


def _first_present(columns, candidates):
    for c in candidates:
        if c in columns:
            return c
    return None


# ---------------------------------------------------------------------------
# Fetchers — each returns a partial {column: value} dict. An empty dict means
# a successful lookup with no applicable record; source failures must raise.
# ---------------------------------------------------------------------------

def fetch_litigation(bbl, bin_number):
    columns = _get_client().get_columns('hpd_litigation')
    if 'bbl' in columns:
        where = f"bbl={soql_quote(bbl)}"
    elif 'bin' in columns and bin_number:
        where = f"bin={soql_quote(bin_number)}"
    elif {'boroid', 'block', 'lot'} <= columns:
        where = where_block_lot('boroid', 'block', 'lot', bbl)
    elif {'boro', 'block', 'lot'} <= columns:
        where = where_block_lot('boro', 'block', 'lot', bbl)
    else:
        raise RuntimeError('Housing Litigations has no usable BBL/BIN fields')
    rows = _get_client().get_all('hpd_litigation', page_size=1000, max_rows=5000, **{
        '$where': where,
    })
    open_rows = [r for r in rows if 'CLOS' not in (r.get('casestatus') or '').upper()]
    last = None
    for r in rows:
        d = parse_any_date(r.get('caseopendate'))
        if d and (last is None or d > last[0]):
            last = (d, r)
    return {
        'litigation_count': len(rows),
        'litigation_open_count': len(open_rows),
        'litigation_last_case_type': (last[1].get('casetype') if last else None),
        'litigation_last_open_date': (last[0] if last else None),
    }


def fetch_evictions(bbl, bin_number):
    columns = _get_client().get_columns('evictions')
    if 'bbl' in columns:
        where = f"bbl={soql_quote(bbl)}"
    elif 'bin' in columns and bin_number:
        where = f"bin={soql_quote(bin_number)}"
    else:
        raise RuntimeError('Evictions has no usable BBL/BIN fields')
    rows = _get_client().get_all('evictions', page_size=1000, max_rows=5000, **{'$where': where})
    date_col = _first_present(columns, ['executed_date', 'executeddate'])
    dates = [parse_any_date(r.get(date_col)) for r in rows] if date_col else []
    dates = [d for d in dates if d]
    return {
        'eviction_count': len(rows),
        'eviction_last_date': max(dates) if dates else None,
    }


def fetch_exemptions(bbl, bin_number):
    columns = _get_client().get_columns('exemptions')
    if 'parid' in columns:
        where = f"parid={soql_quote(bbl)}"
    elif {'boro', 'block', 'lot'} <= columns:
        where = where_block_lot('boro', 'block', 'lot', bbl)
    else:
        raise RuntimeError('Property Exemption Detail has no usable parcel fields')
    rows = _get_client().get_all('exemptions', page_size=1000, max_rows=2000, **{'$where': where})

    code_col = _first_present(columns, ['exmp_code', 'exemption_code', 'excode'])
    if not code_col:
        raise RuntimeError('Property Exemption Detail has no exemption-code field')
    if not {'year', 'period', 'pstatus'} <= columns:
        raise RuntimeError(
            'Property Exemption Detail lacks year/period/approval fields')

    # This feed contains many fiscal years and roll periods. Only the newest
    # snapshot describes the current property. The separate official
    # Exemption Classification Codes dataset maps 1015 -> Senior Citizen
    # Homeowner and 1019 -> Disabled Homeowner; `exname` is the beneficiary's
    # name, not the program description, so keyword-matching it is unsafe.
    def numeric(value, default=-1):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return default

    latest_year = max((numeric(r.get('year')) for r in rows), default=-1)
    current_year_rows = [r for r in rows if numeric(r.get('year')) == latest_year]
    latest_period = max(
        (numeric(r.get('period')) for r in current_year_rows), default=-1)
    current_rows = [
        r for r in current_year_rows
        if numeric(r.get('period')) == latest_period
    ] if latest_period >= 0 else current_year_rows

    codes = set()
    approved_codes = set()
    for r in current_rows:
        if r.get(code_col):
            codes.add(str(r[code_col]).strip())
            status = str(r.get('pstatus') or '').strip().upper()
            if status.startswith('A'):
                approved_codes.add(str(r[code_col]).strip())
    return {
        'exemption_count': len(current_rows),
        'exemption_codes': ','.join(sorted(codes)) or None,
        'has_senior_exemption': '1015' in approved_codes,
        'has_disabled_exemption': '1019' in approved_codes,
    }


def fetch_speculation(bbl, bin_number):
    columns = _get_client().get_columns('speculation_watch')
    if 'bbl' in columns:
        where = f"bbl={soql_quote(bbl)}"
    elif {'borough', 'block', 'lot'} <= columns:
        where = where_block_lot('borough', 'block', 'lot', bbl)
    elif {'boro', 'block', 'lot'} <= columns:
        where = where_block_lot('boro', 'block', 'lot', bbl)
    else:
        raise RuntimeError('Speculation Watch List has no usable parcel fields')
    rows = _get_client().get_all('speculation_watch', page_size=1000, max_rows=10000, **{'$where': where})
    if not rows:
        return {'on_speculation_watch_list': False, 'speculation_watch_date': None}
    date_col = _first_present(columns, ['deed_date', 'sale_date', 'as_of_date', 'date'])
    dates = [parse_any_date(r.get(date_col)) for r in rows] if date_col else []
    dates = [d for d in dates if d]
    return {
        'on_speculation_watch_list': True,
        'speculation_watch_date': max(dates) if dates else None,
    }


def _usable_bin(bin_number, bbl):
    """Borough-wide placeholder BINs (e.g. 3000000) identify no building."""
    value = str(bin_number or '').strip()
    if (len(value) == 7 and value.isascii() and value.isdigit()
            and value[0] in '12345' and value[0] == str(bbl)[:1]
            and value[1:] != '000000'):
        return value
    return None


def _dob_parcel_where(columns, bbl):
    """Match DOB's geocoded BBL or its original borough/block/lot columns.

    Legacy CO lots use five digits; DOB NOW commonly strips the padding.
    Boroughs are names in both current CO feeds. Raw parcel fields also cover
    records whose added geocoded BBL is missing.
    """
    bbl = str(bbl)
    if len(bbl) != 10 or not bbl.isascii() or not bbl.isdigit() or bbl[0] not in '12345':
        raise ValueError('Invalid BBL for DOB parcel lookup')
    clauses = []
    if 'bbl' in columns:
        clauses.append(f'bbl={soql_quote(bbl)}')
    boro_col = _first_present(columns, ['borough', 'boro'])
    if boro_col and {'block', 'lot'} <= columns:
        boro, block, lot, block_p, lot_p = bbl_parts(bbl)
        borough_names = {'1': 'MANHATTAN', '2': 'BRONX', '3': 'BROOKLYN',
                         '4': 'QUEENS', '5': 'STATEN ISLAND'}
        borough_forms = [boro, borough_names[boro]]
        if boro == '5':
            borough_forms.append('RICHMOND')
        clauses.append(
            f"({in_clause(f'upper({boro_col})', borough_forms)} AND "
            f"{in_clause('block', sorted({block, block_p}))} AND "
            f"{in_clause('lot', sorted({lot, lot_p, lot.zfill(5)}))})")
    if not clauses:
        raise RuntimeError('DOB source has no usable BBL or borough/block/lot fields')
    return ' OR '.join(clauses)


def fetch_dob_complaints(bbl, bin_number):
    bin_number = _usable_bin(bin_number, bbl)
    if not bin_number:
        return {
            'dob_complaint_count': None,
            'dob_active_complaint_count': None,
            'dob_last_complaint_date': None,
        }
    rows = _get_client().get_all('dob_complaints', page_size=1000, max_rows=10000, **{
        '$where': f"bin={soql_quote(bin_number)}",
    })
    active = [r for r in rows if (r.get('status') or '').upper() == 'ACTIVE']
    dates = [parse_any_date(r.get('date_entered')) for r in rows]
    dates = [d for d in dates if d]
    return {
        'dob_complaint_count': len(rows),
        'dob_active_complaint_count': len(active),
        'dob_last_complaint_date': max(dates) if dates else None,
    }


def _fetch_cos_from(dataset, bin_number, bbl):
    columns = _get_client().get_columns(dataset)
    bin_col = _first_present(columns, ['bin', 'bin_number', 'bin_num'])
    bin_number = _usable_bin(bin_number, bbl)
    if bin_col and bin_number:
        where = f"{bin_col}={soql_quote(bin_number)}"
    else:
        where = _dob_parcel_where(columns, bbl)
    rows = _get_client().get_all(dataset, page_size=1000, max_rows=2000, **{'$where': where})
    date_col = _first_present(columns, [
        'c_of_o_issuance_date', 'c_of_o_issue_date', 'c_o_issue_date',
        'issuance_date', 'issue_date'])
    type_col = _first_present(columns, [
        'c_of_o_filing_type', 'issue_type', 'filing_type', 'type_of_c_of_o',
        'co_type', 'certificate_type'])
    job_col = _first_present(columns, [
        'application_number', 'job_filing_number', 'job_number', 'job'])
    out = []
    for r in rows:
        out.append({
            'date': parse_any_date(r.get(date_col)) if date_col else None,
            'type': (r.get(type_col) or '').strip() if type_col else None,
            'job': (r.get(job_col) or '').strip() if job_col else None,
        })
    return out


def fetch_certificates_of_occupancy(bbl, bin_number):
    cos = []
    for dataset in ('dob_co_bis', 'dob_co_now'):
        # A failed feed is not the same as a feed with zero matching rows.
        # Let the caller record/retry the source error instead of stamping the
        # building current with an incomplete CO history.
        cos.extend(_fetch_cos_from(dataset, bin_number, bbl))
    if not cos:
        return {
            'co_count': 0,
            'latest_co_date': None,
            'latest_co_type': None,
            'latest_co_job_number': None,
        }
    dated = [c for c in cos if c['date']]
    latest = max(dated, key=lambda c: c['date']) if dated else None
    return {
        'co_count': len(cos),
        'latest_co_date': latest['date'] if latest else None,
        'latest_co_type': (latest['type'] or None) if latest else None,
        'latest_co_job_number': (latest['job'] or None) if latest else None,
    }


def fetch_fisp(bbl, bin_number):
    columns = _get_client().get_columns('fisp_facades')
    bin_col = _first_present(columns, ['bin', 'bin_number'])
    bin_number = _usable_bin(bin_number, bbl)
    if bin_col and bin_number:
        where = f"{bin_col}={soql_quote(bin_number)}"
    else:
        where = _dob_parcel_where(columns, bbl)
    rows = _get_client().get_all('fisp_facades', page_size=500, max_rows=1000, **{
        '$where': where,
    })
    if not rows:
        return {
            'fisp_status': None,
            'fisp_cycle': None,
            'fisp_filing_date': None,
        }
    status_col = _first_present(columns, ['current_status', 'filing_status', 'status'])
    cycle_col = _first_present(columns, ['cycle', 'sub_cycle', 'cycle_number'])
    date_col = _first_present(columns, ['submitted_on', 'filing_date', 'submitted_date'])
    dated = [(parse_any_date(r.get(date_col)) if date_col else None, r) for r in rows]
    dated.sort(key=lambda pair: pair[0] or date.min, reverse=True)
    latest_date, latest = dated[0]
    return {
        'fisp_status': (latest.get(status_col) or '').strip() or None if status_col else None,
        'fisp_cycle': (latest.get(cycle_col) or '').strip() or None if cycle_col else None,
        'fisp_filing_date': latest_date,
    }


def fetch_ll84(bbl, bin_number):
    columns = _get_client().get_columns('ll84_energy')
    bbl_col = _first_present(columns, [
        'bbl_10_digits', 'bbl', 'nyc_borough_block_and_lot',
        'nyc_borough_block_and_lot_bbl'])
    if not bbl_col:
        raise RuntimeError('LL84 Energy has no usable BBL field')
    rows = _get_client().get_all('ll84_energy', page_size=1000, max_rows=20000, **{'$where': f"{bbl_col}={soql_quote(bbl)}"})
    if not rows:
        return {
            'energy_star_score': None,
            'site_eui': None,
            'll84_year': None,
        }
    score_col = _first_present(columns, ['energy_star_score', 'energy_star_1_100_score'])
    eui_col = _first_present(columns, [
        'site_eui_kbtu_ft', 'site_eui_kbtu_ft2', 'site_eui'])
    year_col = _first_present(columns, ['report_year', 'data_year', 'year_ending'])

    def year_of(r):
        try:
            return int(str(r.get(year_col))[:4]) if year_col and r.get(year_col) else 0
        except ValueError:
            return 0

    latest = max(rows, key=year_of)
    score = eui = None
    if score_col:
        try:
            score = int(float(latest.get(score_col)))
        except (ValueError, TypeError):
            score = None
    if eui_col:
        eui = parse_money(latest.get(eui_col))
    return {
        'energy_star_score': score,
        'site_eui': eui,
        'll84_year': year_of(latest) or None,
    }


def fetch_rolling_sales(bbl, bin_number):
    columns = _get_client().get_columns('rolling_sales')
    boro_col = _first_present(columns, ['borough', 'boro'])
    if not boro_col or 'block' not in columns or 'lot' not in columns:
        raise RuntimeError('Rolling Sales has no usable borough/block/lot fields')
    rows = _get_client().get_all('rolling_sales', page_size=500, max_rows=1000, **{
        '$where': where_block_lot(boro_col, 'block', 'lot', bbl),
    })
    date_col = _first_present(columns, ['sale_date', 'saledate'])
    price_col = _first_present(columns, ['sale_price', 'saleprice'])
    sqft_col = _first_present(columns, ['gross_square_feet', 'gross_sqft'])
    best = None
    for r in rows:
        d = parse_any_date(r.get(date_col)) if date_col else None
        p = parse_money(r.get(price_col)) if price_col else None
        # $0 / nominal transfers aren't sales
        if not d or not p or p < 1000:
            continue
        if best is None or d > best[0]:
            best = (d, p, parse_money(r.get(sqft_col)) if sqft_col else None)
    if not best:
        return {
            'rolling_sale_date': None,
            'rolling_sale_price': None,
            'rolling_sale_ppsf': None,
        }
    d, p, sqft = best
    return {
        'rolling_sale_date': d,
        'rolling_sale_price': p,
        'rolling_sale_ppsf': round(p / sqft, 2) if sqft and sqft > 0 else None,
    }


FETCHERS = [
    ('litigation', fetch_litigation),
    ('evictions', fetch_evictions),
    ('exemptions', fetch_exemptions),
    ('speculation', fetch_speculation),
    ('dob_complaints', fetch_dob_complaints),
    ('certificates_of_occupancy', fetch_certificates_of_occupancy),
    ('fisp', fetch_fisp),
    ('ll84', fetch_ll84),
    ('rolling_sales', fetch_rolling_sales),
]

SIGNAL_DATASETS = (
    'hpd_litigation', 'evictions', 'exemptions', 'speculation_watch',
    'dob_complaints', 'dob_co_bis', 'dob_co_now', 'fisp_facades',
    'll84_energy', 'rolling_sales',
)


def preflight_signal_sources():
    """Fail once, early, when a source is unavailable or changes schema.

    Without this gate a global metadata outage becomes the same error on every
    one of 100k buildings and the run spends days making no durable progress.
    """
    failures = []
    source_client = _get_client()
    for dataset in SIGNAL_DATASETS:
        try:
            source_client.get_columns(dataset)
        except Exception as exc:
            failures.append(f'{dataset}: {exc}')
    if failures:
        raise RuntimeError('Signal source preflight failed: ' + ' | '.join(failures))


def enrich_signals_for_building(bbl, bin_number, building_sqft):
    """Run every fetcher and return ``(fields, errors)``.

    Successful partial fields are safe to persist, but a building is not
    marked current while any source is unavailable. This is the distinction
    the old pipeline lost: an API failure was stamped as a healthy zero and
    then skipped for the next 30 days.
    """
    fields = {}
    errors = []
    for name, fetcher in FETCHERS:
        try:
            fields.update(fetcher(bbl, bin_number))
        except Exception as e:
            errors.append(f'{name}: {e}')
            with _print_lock:
                print(f"      ⚠️  {name} failed for {bbl}: {e}")
    fields['ll97_covered_estimated'] = bool(
        building_sqft and building_sqft >= LL97_SQFT_THRESHOLD)
    return fields, errors


def _write_signal_fields(building, fields, errors):
    """Retry idempotent writes with fresh connections, retaining fetched data."""
    for attempt in range(3):
        conn = None
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=10,
                                   options='-c statement_timeout=30000')
            with conn.cursor() as cur:
                assignments = ', '.join(f"{col} = %s" for col in fields)
                values = list(fields.values())
                if errors:
                    error_text = ' | '.join(errors)[:4000]
                    if assignments:
                        cur.execute(
                            f"""UPDATE buildings SET {assignments},
                                signals_last_attempted = NOW(), signals_last_error = %s,
                                signals_last_error_at = NOW()
                                WHERE id = %s""",
                            values + [error_text, building['id']])
                    else:
                        cur.execute(
                            """UPDATE buildings
                               SET signals_last_attempted = NOW(), signals_last_error = %s,
                                   signals_last_error_at = NOW()
                               WHERE id = %s""",
                            (error_text, building['id']))
                elif assignments:
                    cur.execute(
                        f"""UPDATE buildings SET {assignments},
                            signals_last_attempted = NOW(), signals_last_enriched = NOW(),
                            signals_enrichment_version = %s,
                            signals_last_error = NULL,
                            signals_last_error_at = NULL
                            WHERE id = %s""",
                        values + [SIGNALS_ENRICHMENT_VERSION, building['id']])
                else:
                    cur.execute(
                        """UPDATE buildings
                           SET signals_last_attempted = NOW(), signals_last_enriched = NOW(),
                               signals_enrichment_version = %s,
                               signals_last_error = NULL,
                               signals_last_error_at = NULL
                           WHERE id = %s""",
                        (SIGNALS_ENRICHMENT_VERSION, building['id']))
            conn.commit()
            return
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            if attempt == 2:
                raise
        finally:
            # Closing also rolls back failed transactions, even if the server
            # has gone away. Do not call rollback on a broken connection.
            if conn is not None:
                conn.close()
        time.sleep(2 ** attempt)


def _process_building(building, position, total):
    try:
        # Slow external requests must not hold a database connection open.
        fields, errors = enrich_signals_for_building(
            building['bbl'], building['bin'], building['building_sqft'])
        _write_signal_fields(building, fields, errors)
        if errors:
            with _print_lock:
                print(f"[{position}/{total}] BBL {building['bbl']}: "
                      f"⚠️ partial ({len(errors)} source errors; will retry)")
            return False
        return True
    except Exception as exc:
        try:
            _write_signal_fields(building, {}, [str(exc)])
        except Exception:
            # The failed record remains due if the database is unavailable.
            pass
        with _print_lock:
            print(f"[{position}/{total}] BBL {building['bbl']}: ❌ {exc}")
        return False


def main():
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()

    # Hard requirement: the version/error columns make failure distinguishable
    # from a healthy zero and force a one-time refresh after mapping changes.
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'buildings'
          AND column_name IN (
              'signals_last_enriched', 'signals_enrichment_version',
              'signals_last_error', 'signals_last_error_at')
    """)
    if len(cur.fetchall()) != 4:
        print("❌ Run migrate_add_intel_signals.py first — signal columns are missing.")
        sys.exit(1)

    try:
        preflight_signal_sources()
    except Exception as exc:
        print(f"❌ {exc}")
        cur.close()
        conn.close()
        raise SystemExit(1)

    limit_sql = f"LIMIT {SIGNALS_MAX_BUILDINGS}" if SIGNALS_MAX_BUILDINGS else ""
    cur.execute(f"""
        SELECT id, bbl, bin, building_sqft
        FROM buildings
        WHERE bbl IS NOT NULL
        AND (signals_enrichment_version < {SIGNALS_ENRICHMENT_VERSION}
             OR NULLIF(signals_last_error, '') IS NOT NULL
             OR signals_last_enriched IS NULL
             OR signals_last_enriched < NOW() - INTERVAL '{SIGNALS_REFRESH_DAYS} days')
        AND (signals_last_error IS NULL OR signals_last_attempted IS NULL
             OR signals_last_attempted < NOW() - INTERVAL '6 hours')
        ORDER BY signals_last_attempted ASC NULLS FIRST, id
        {limit_sql}
    """)
    buildings = cur.fetchall()
    cur.close()
    conn.close()

    print("Step 6: Distress / compliance / freshness signals")
    print(f"📊 {len(buildings)} buildings to enrich ({MAX_WORKERS} workers)")
    if not buildings:
        print("   ✅ All up to date.")
        return

    started = time.time()
    result = run_checkpointed_batch(
        list(enumerate(buildings, 1)),
        lambda item, retry: _process_building(item[1], item[0], len(buildings)),
        workers=MAX_WORKERS, label='Signal refresh',
        progress_every=SIGNALS_PROGRESS_EVERY,
        error_limit=max(1, int(os.getenv('SIGNALS_ERROR_LIMIT', '100'))))
    ok, failed = result.succeeded, result.failed

    duration_minutes = (time.time() - started) / 60
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    cur = conn.cursor()
    cur.execute(f"""
        SELECT COUNT(*)
        FROM buildings
        WHERE bbl IS NOT NULL
        AND (signals_enrichment_version < {SIGNALS_ENRICHMENT_VERSION}
             OR NULLIF(signals_last_error, '') IS NOT NULL
             OR signals_last_enriched IS NULL
             OR signals_last_enriched < NOW() - INTERVAL '{SIGNALS_REFRESH_DAYS} days')
    """)
    remaining_backlog = cur.fetchone()[0]
    cur.close()
    conn.close()
    print(f"   Backlog remaining after this pass: {remaining_backlog:,}")
    if failed:
        print(f"\n❌ Incomplete in {duration_minutes:.1f} min — "
              f"{ok} enriched, {failed} failed and remain eligible for retry")
        raise SystemExit(1)
    print(f"\n✅ Done in {duration_minutes:.1f} min — {ok} enriched, 0 failed")


if __name__ == "__main__":
    main()
