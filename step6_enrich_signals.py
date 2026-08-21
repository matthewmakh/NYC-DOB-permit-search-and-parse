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
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

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
# Fetchers — each returns a partial {column: value} dict, or {} on failure.
# ---------------------------------------------------------------------------

def fetch_litigation(bbl, bin_number):
    rows = client.get_all('hpd_litigation', page_size=1000, max_rows=5000, **{
        '$where': where_block_lot('boro', 'block', 'lot', bbl),
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
        return {}
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
        return {}
    rows = client.get_all('exemptions', page_size=1000, max_rows=2000, **{'$where': where})

    code_col = _first_present(columns, ['exmp_code', 'exemption_code', 'excode'])
    desc_col = _first_present(columns, ['description', 'exemption_name', 'long_name'])
    codes = set()
    blob = []
    for r in rows:
        if code_col and r.get(code_col):
            codes.add(str(r[code_col]).strip())
        if desc_col and r.get(desc_col):
            blob.append(str(r[desc_col]).upper())
    text = ' '.join(blob)
    return {
        'exemption_count': len(rows),
        'exemption_codes': ','.join(sorted(codes)) or None,
        'has_senior_exemption': 'SCHE' in text or 'SENIOR' in text,
        'has_disabled_exemption': 'DHE' in text or 'DISABLED' in text,
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
        return {}
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
        return {}
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
            return []
        where = where_block_lot(boro_col, 'block', 'lot', bbl)
    else:
        return []
    rows = client.get_all(dataset, page_size=1000, max_rows=2000, **{'$where': where})
    date_col = _first_present(columns, [
        'c_of_o_issuance_date', 'c_of_o_issue_date', 'issuance_date', 'issue_date'])
    type_col = _first_present(columns, ['filing_type', 'type_of_c_of_o', 'co_type', 'certificate_type'])
    job_col = _first_present(columns, ['job_filing_number', 'job_number', 'job'])
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
        try:
            cos.extend(_fetch_cos_from(dataset, bin_number, bbl))
        except Exception as e:
            with _print_lock:
                print(f"      ⚠️  CO fetch ({dataset}) failed: {e}")
    if not cos:
        return {'co_count': 0}
    dated = [c for c in cos if c['date']]
    latest = max(dated, key=lambda c: c['date']) if dated else None
    return {
        'co_count': len(cos),
        'latest_co_date': latest['date'] if latest else None,
        'latest_co_type': (latest['type'] or None) if latest else None,
        'latest_co_job_number': (latest['job'] or None) if latest else None,
    }


def fetch_fisp(bbl, bin_number):
    if not bin_number:
        return {}
    columns = client.get_columns('fisp_facades')
    bin_col = _first_present(columns, ['bin', 'bin_number'])
    if not bin_col:
        return {}
    rows = client.get_all('fisp_facades', page_size=500, max_rows=1000, **{
        '$where': f"{bin_col}={soql_quote(bin_number)}",
    })
    if not rows:
        return {}
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
    bbl_col = _first_present(columns, ['bbl_10_digits', 'bbl', 'nyc_borough_block_and_lot_bbl'])
    if not bbl_col:
        return {}
    rows = client.get('ll84_energy', **{'$where': f"{bbl_col}={soql_quote(bbl)}", '$limit': 200})
    if not rows:
        return {}
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
        return {}
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
        return {}
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
    """Run every fetcher; a failing dataset degrades to a skipped signal."""
    fields = {}
    for name, fetcher in FETCHERS:
        try:
            fields.update(fetcher(bbl, bin_number))
        except Exception as e:
            with _print_lock:
                print(f"      ⚠️  {name} failed for {bbl}: {e}")
    if building_sqft:
        fields['ll97_covered_estimated'] = building_sqft >= LL97_SQFT_THRESHOLD
    return fields


def _process_building(building, position, total):
    conn = psycopg2.connect(DATABASE_URL)
    try:
        fields = enrich_signals_for_building(
            building['bbl'], building['bin'], building['building_sqft'])
        cur = conn.cursor()
        assignments = ', '.join(f"{col} = %s" for col in fields)
        values = list(fields.values())
        if assignments:
            cur.execute(
                f"UPDATE buildings SET {assignments}, signals_last_enriched = NOW() WHERE id = %s",
                values + [building['id']])
        else:
            cur.execute("UPDATE buildings SET signals_last_enriched = NOW() WHERE id = %s",
                        (building['id'],))
        conn.commit()
        cur.close()

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

    # Hard requirement: the migration must have run.
    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'buildings' AND column_name = 'signals_last_enriched'
    """)
    if not cur.fetchone():
        print("❌ Run migrate_add_intel_signals.py first — signal columns are missing.")
        sys.exit(1)

    cur.execute(f"""
        SELECT id, bbl, bin, building_sqft
        FROM buildings
        WHERE bbl IS NOT NULL
        AND (signals_last_enriched IS NULL
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
        futures = [pool.submit(_process_building, b, i, len(buildings))
                   for i, b in enumerate(buildings, 1)]
        for f in as_completed(futures):
            if f.result():
                ok += 1
            else:
                failed += 1

    print(f"\n✅ Done in {(time.time()-started)/60:.1f} min — {ok} enriched, {failed} failed")


if __name__ == "__main__":
    main()
