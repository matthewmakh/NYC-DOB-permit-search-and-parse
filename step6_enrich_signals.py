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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Lock

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

import _pipeline_path  # noqa: F401  (puts dashboard_html on sys.path)
from socrata_client import SocrataClient, where_block_lot, soql_quote, bbl_parts

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
# Bump this whenever source mappings or signal semantics change. A building is
# current only after every source succeeds under this version.
SIGNALS_ENRICHMENT_VERSION = 2
# LL97 broadly covers buildings over 25,000 sqft; the official covered-
# buildings list is only published as a DOB spreadsheet, so we estimate.
LL97_SQFT_THRESHOLD = 25000

_print_lock = Lock()
client = SocrataClient()


def parse_any_date(value):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%Y%m%d'):
        try:
            return datetime.strptime(text[:10], fmt).date()
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
    columns = client.get_columns('hpd_litigation')
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
    rows = client.get_all('hpd_litigation', page_size=1000, max_rows=5000, **{
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
    columns = client.get_columns('evictions')
    if 'bbl' in columns:
        where = f"bbl={soql_quote(bbl)}"
    elif 'bin' in columns and bin_number:
        where = f"bin={soql_quote(bin_number)}"
    else:
        raise RuntimeError('Evictions has no usable BBL/BIN fields')
    rows = client.get_all('evictions', page_size=1000, max_rows=5000, **{'$where': where})
    date_col = _first_present(columns, ['executed_date', 'executeddate'])
    dates = [parse_any_date(r.get(date_col)) for r in rows] if date_col else []
    dates = [d for d in dates if d]
    return {
        'eviction_count': len(rows),
        'eviction_last_date': max(dates) if dates else None,
    }


def fetch_exemptions(bbl, bin_number):
    columns = client.get_columns('exemptions')
    if 'parid' in columns:
        where = f"parid={soql_quote(bbl)}"
    elif {'boro', 'block', 'lot'} <= columns:
        where = where_block_lot('boro', 'block', 'lot', bbl)
    else:
        raise RuntimeError('Property Exemption Detail has no usable parcel fields')
    rows = client.get_all('exemptions', page_size=1000, max_rows=2000, **{'$where': where})

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
    columns = client.get_columns('speculation_watch')
    if 'bbl' in columns:
        where = f"bbl={soql_quote(bbl)}"
    elif {'borough', 'block', 'lot'} <= columns:
        where = where_block_lot('borough', 'block', 'lot', bbl)
    elif {'boro', 'block', 'lot'} <= columns:
        where = where_block_lot('boro', 'block', 'lot', bbl)
    else:
        raise RuntimeError('Speculation Watch List has no usable parcel fields')
    rows = client.get('speculation_watch', **{'$where': where, '$limit': 50})
    if not rows:
        return {'on_speculation_watch_list': False, 'speculation_watch_date': None}
    date_col = _first_present(columns, ['deed_date', 'sale_date', 'as_of_date', 'date'])
    dates = [parse_any_date(r.get(date_col)) for r in rows] if date_col else []
    dates = [d for d in dates if d]
    return {
        'on_speculation_watch_list': True,
        'speculation_watch_date': max(dates) if dates else None,
    }


def fetch_dob_complaints(bbl, bin_number):
    if not bin_number:
        return {
            'dob_complaint_count': 0,
            'dob_active_complaint_count': 0,
            'dob_last_complaint_date': None,
        }
    rows = client.get_all('dob_complaints', page_size=1000, max_rows=10000, **{
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
    columns = client.get_columns(dataset)
    bin_col = _first_present(columns, ['bin', 'bin_number', 'bin_num'])
    if bin_col and bin_number:
        where = f"{bin_col}={soql_quote(bin_number)}"
    elif {'block', 'lot'} <= columns:
        boro_col = _first_present(columns, ['borough', 'boro'])
        if not boro_col:
            raise RuntimeError(f'{dataset} has block/lot but no borough field')
        where = where_block_lot(boro_col, 'block', 'lot', bbl)
    else:
        raise RuntimeError(f'{dataset} has no usable BIN or block/lot fields')
    rows = client.get_all(dataset, page_size=1000, max_rows=2000, **{'$where': where})
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
    columns = client.get_columns('fisp_facades')
    bin_col = _first_present(columns, ['bin', 'bin_number'])
    if bin_col and bin_number:
        where = f"{bin_col}={soql_quote(bin_number)}"
    elif {'block', 'lot'} <= columns:
        boro_col = _first_present(columns, ['borough', 'boro'])
        if not boro_col:
            raise RuntimeError('FISP Facades has block/lot but no borough field')
        where = where_block_lot(boro_col, 'block', 'lot', bbl)
    else:
        raise RuntimeError('FISP Facades has no usable BIN or block/lot fields')
    rows = client.get_all('fisp_facades', page_size=500, max_rows=1000, **{
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
    columns = client.get_columns('ll84_energy')
    bbl_col = _first_present(columns, [
        'bbl_10_digits', 'bbl', 'nyc_borough_block_and_lot',
        'nyc_borough_block_and_lot_bbl'])
    if not bbl_col:
        raise RuntimeError('LL84 Energy has no usable BBL field')
    rows = client.get('ll84_energy', **{'$where': f"{bbl_col}={soql_quote(bbl)}", '$limit': 200})
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
    columns = client.get_columns('rolling_sales')
    boro_col = _first_present(columns, ['borough', 'boro'])
    if not boro_col or 'block' not in columns or 'lot' not in columns:
        raise RuntimeError('Rolling Sales has no usable borough/block/lot fields')
    rows = client.get_all('rolling_sales', page_size=500, max_rows=1000, **{
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


def _process_building(building, position, total):
    conn = psycopg2.connect(DATABASE_URL)
    try:
        fields, errors = enrich_signals_for_building(
            building['bbl'], building['bin'], building['building_sqft'])
        cur = conn.cursor()
        assignments = ', '.join(f"{col} = %s" for col in fields)
        values = list(fields.values())
        if errors:
            error_text = ' | '.join(errors)[:4000]
            if assignments:
                cur.execute(
                    f"""UPDATE buildings SET {assignments},
                        signals_last_error = %s,
                        signals_last_error_at = NOW()
                        WHERE id = %s""",
                    values + [error_text, building['id']])
            else:
                cur.execute(
                    """UPDATE buildings
                       SET signals_last_error = %s,
                           signals_last_error_at = NOW()
                       WHERE id = %s""",
                    (error_text, building['id']))
        elif assignments:
            cur.execute(
                f"""UPDATE buildings SET {assignments},
                    signals_last_enriched = NOW(),
                    signals_enrichment_version = %s,
                    signals_last_error = NULL,
                    signals_last_error_at = NULL
                    WHERE id = %s""",
                values + [SIGNALS_ENRICHMENT_VERSION, building['id']])
        else:
            cur.execute(
                """UPDATE buildings
                   SET signals_last_enriched = NOW(),
                       signals_enrichment_version = %s,
                       signals_last_error = NULL,
                       signals_last_error_at = NULL
                   WHERE id = %s""",
                (SIGNALS_ENRICHMENT_VERSION, building['id']))
        conn.commit()
        cur.close()

        if errors:
            with _print_lock:
                print(f"[{position}/{total}] BBL {building['bbl']}: "
                      f"⚠️ partial ({len(errors)} source errors; will retry)")
            return False

        highlights = []
        if fields.get('litigation_open_count'):
            highlights.append(f"litigation:{fields['litigation_open_count']}")
        if fields.get('eviction_count'):
            highlights.append(f"evictions:{fields['eviction_count']}")
        if fields.get('has_senior_exemption'):
            highlights.append("senior-exemption")
        if fields.get('on_speculation_watch_list'):
            highlights.append("speculation-list")
        if fields.get('latest_co_date'):
            highlights.append(f"CO:{fields['latest_co_date']}")
        with _print_lock:
            note = ' · '.join(highlights) if highlights else 'no signals'
            print(f"[{position}/{total}] BBL {building['bbl']}: {note}")
        return True
    except Exception as e:
        conn.rollback()
        with _print_lock:
            print(f"[{position}/{total}] BBL {building['bbl']}: ❌ {e}")
        return False
    finally:
        conn.close()


def main():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
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

    cur.execute(f"""
        SELECT id, bbl, bin, building_sqft
        FROM buildings
        WHERE bbl IS NOT NULL
        AND (signals_enrichment_version < {SIGNALS_ENRICHMENT_VERSION}
             OR signals_last_enriched IS NULL
             OR signals_last_enriched < NOW() - INTERVAL '{SIGNALS_REFRESH_DAYS} days')
        ORDER BY id
    """)
    buildings = cur.fetchall()
    cur.close()
    conn.close()

    print("Step 6: Distress / compliance / freshness signals")
    print(f"📊 {len(buildings)} buildings to enrich ({MAX_WORKERS} workers)")
    if not buildings:
        print("   ✅ All up to date.")
        return

    ok = failed = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        # A first backfill can exceed 100k buildings. Submitting every future
        # up front consumes substantial memory during a multi-day cron run, so
        # keep only a small bounded window in flight.
        work = iter(enumerate(buildings, 1))
        pending = set()
        max_in_flight = max(MAX_WORKERS * 4, MAX_WORKERS)

        def fill_window():
            while len(pending) < max_in_flight:
                try:
                    position, building = next(work)
                except StopIteration:
                    break
                pending.add(pool.submit(
                    _process_building, building, position, len(buildings)))

        fill_window()
        while pending:
            completed, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                if future.result():
                    ok += 1
                else:
                    failed += 1
            fill_window()

    duration_minutes = (time.time() - started) / 60
    if failed:
        print(f"\n❌ Incomplete in {duration_minutes:.1f} min — "
              f"{ok} enriched, {failed} failed and remain eligible for retry")
        raise SystemExit(1)
    print(f"\n✅ Done in {duration_minutes:.1f} min — {ok} enriched, 0 failed")


if __name__ == "__main__":
    main()
