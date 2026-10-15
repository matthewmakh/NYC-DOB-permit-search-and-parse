#!/usr/bin/env python3
"""Geocode missing permits using NYC services; safely recheck an old run's results.

NYC_GEOCLIENT_SUBSCRIPTION_KEY is the Geoclient v2 subscription key.
NYC_GEOCLIENT_APP_KEY / APP_ID remain supported as legacy variable names.
DATABASE_URL or DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME configure PostgreSQL.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

import _pipeline_path  # noqa: F401
from nyc_geocoding import PermitGeocoder, geoclient_key

load_dotenv()
BATCH_SIZE = int(os.getenv('GEOCODE_BATCH_SIZE', '500'))
RATE_LIMIT_DELAY = max(0.2, float(os.getenv('GEOCODE_DELAY', '0.2')))


def get_db_connection():
    options = {'cursor_factory': psycopg2.extras.RealDictCursor}
    if os.getenv('DATABASE_URL'):
        return psycopg2.connect(os.environ['DATABASE_URL'], **options)
    if not all(os.getenv(name) for name in ('DB_HOST', 'DB_USER', 'DB_NAME')):
        raise ValueError('Set DATABASE_URL or DB_HOST/DB_USER/DB_NAME/DB_PASSWORD for the intended database')
    return psycopg2.connect(
        host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', '5432')),
        user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'), **options)


def ensure_geocode_columns(conn):
    with conn.cursor() as cur:
        cur.execute('ALTER TABLE permits ADD COLUMN IF NOT EXISTS geocode_failed BOOLEAN DEFAULT FALSE')
        cur.execute('ALTER TABLE permits ADD COLUMN IF NOT EXISTS geocode_last_attempted TIMESTAMPTZ')
    conn.commit()


def record_result(conn, permit_id, result):
    with conn.cursor() as cur:
        if result.found:
            cur.execute('''UPDATE permits SET latitude = %s, longitude = %s,
                           geocode_failed = FALSE, geocode_last_attempted = NOW()
                           WHERE id = %s AND (latitude IS NULL OR longitude IS NULL)''',
                        (result.latitude, result.longitude, permit_id))
        else:
            # No match can wait a week. Service failures retry in an hour.
            cur.execute('''UPDATE permits SET geocode_failed = %s, geocode_last_attempted = NOW()
                           WHERE id = %s AND (latitude IS NULL OR longitude IS NULL)''',
                        (not bool(result.error), permit_id))
        changed = cur.rowcount
    conn.commit()
    return bool(changed)


def geocode_permits(limit=BATCH_SIZE):
    print('PERMIT GEOCODING — NYC parcel/address validation')
    print('Geoclient key configured' if geoclient_key() else 'Using NYC GeoSearch (no Geoclient key configured)')
    conn = get_db_connection()
    try:
        ensure_geocode_columns(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT id, address, bbl FROM permits
                WHERE (latitude IS NULL OR longitude IS NULL)
                  AND address IS NOT NULL AND TRIM(address) != ''
                  AND (geocode_last_attempted IS NULL OR geocode_last_attempted < NOW() -
                       CASE WHEN geocode_failed THEN INTERVAL '7 days' ELSE INTERVAL '1 hour' END)
                ORDER BY geocode_last_attempted ASC NULLS FIRST,
                         CASE WHEN bbl IS NOT NULL AND bbl != '' THEN 0 ELSE 1 END, id
                LIMIT %s''', (limit,))
            rows = cur.fetchall()
        geocoder = PermitGeocoder()
        successes = misses = errors = 0
        for index, row in enumerate(rows, 1):
            result = geocoder.lookup(row['address'], row['bbl'])
            saved = record_result(conn, row['id'], result)
            if result.found and saved:
                successes += 1
                status = f'{result.source}: {result.latitude:.6f}, {result.longitude:.6f}'
            elif result.found:
                status = 'Skipped: coordinates were updated by another worker'
            elif result.error:
                errors += 1
                status = f'Service failure (will retry): {result.error}'
            else:
                misses += 1
                status = 'No verified NYC parcel/address match (will recheck in 7 days)'
            print(f"[{index}/{len(rows)}] Permit #{row['id']}: {status}", flush=True)
            if index < len(rows):
                time.sleep(RATE_LIMIT_DELAY)
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) AS remaining FROM permits WHERE latitude IS NULL OR longitude IS NULL')
            remaining = cur.fetchone()['remaining']
        print(f'Verified and saved: {successes}; no match: {misses}; service failures: {errors}')
        print(f'Remaining without coordinates: {remaining:,} (includes retry cooldowns and missing addresses)')
        return 1 if errors else 0
    finally:
        conn.close()


def logged_geocodes(log_text):
    """Only select explicit successes from the old geocoder, never arbitrary IDs."""
    rows = {}
    permit_id = None
    for line in log_text.splitlines():
        match = re.search(r'\[\d+/\d+\] Permit #(\d+):', line)
        if match:
            permit_id = int(match.group(1))
        match = re.search(r'✅ Success: (-?\d+\.\d+), (-?\d+\.\d+)', line)
        if match and permit_id:
            rows[permit_id] = tuple(map(float, match.groups()))
            permit_id = None
    return rows


def repair_logged_geocodes(log_file, report_file, apply=False):
    """Preview by default. Save old/new values before any explicit repair writes."""
    logged = logged_geocodes(Path(log_file).read_text())
    if not logged:
        raise ValueError('No successful permit geocodes found in the supplied log')
    # Refuse to overwrite an earlier recovery record, before making API calls.
    if Path(report_file).exists():
        raise ValueError('Report already exists; choose a new report filename')
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id, address, bbl, latitude, longitude FROM permits WHERE id = ANY(%s)',
                        (list(logged),))
            rows = cur.fetchall()
        geocoder = PermitGeocoder()
        report = []
        for row in rows:
            old = (row['latitude'], row['longitude'])
            snapshot = logged[row['id']]
            entry = dict(row)
            # The log rounds to six decimal places. Don't touch a subsequent fix.
            if any(value is None or abs(float(value) - expected) > 0.00000051
                   for value, expected in zip(old, snapshot)):
                entry['action'] = 'skip_changed_since_log'
            else:
                hit = geocoder.lookup(row['address'], row['bbl'])
                entry.update(new_latitude=hit.latitude, new_longitude=hit.longitude,
                             source=hit.source, error=hit.error)
                entry['action'] = ('replace' if hit.found else
                                   'unresolved_service_error' if hit.error else 'clear_unverified')
                time.sleep(RATE_LIMIT_DELAY)
            report.append(entry)
            print(f"Permit #{row['id']}: {entry['action']}", flush=True)
        # An exclusive, durable audit file must exist before altering any coordinates.
        with open(report_file, 'x') as output:
            json.dump({'apply_requested': apply, 'repairs': report}, output, indent=2, default=str)
            output.flush()
            os.fsync(output.fileno())
        changed = 0
        if apply:
            ensure_geocode_columns(conn)
            with conn.cursor() as cur:
                for entry in report:
                    if entry['action'] not in ('replace', 'clear_unverified'):
                        continue
                    cur.execute('''UPDATE permits SET latitude = %s, longitude = %s,
                                   geocode_failed = %s, geocode_last_attempted = NOW()
                                   WHERE id = %s AND latitude IS NOT DISTINCT FROM %s
                                     AND longitude IS NOT DISTINCT FROM %s''',
                                (entry['new_latitude'], entry['new_longitude'],
                                 entry['action'] == 'clear_unverified', entry['id'],
                                 entry['latitude'], entry['longitude']))
                    changed += cur.rowcount
            conn.commit()
        print(f"{'Applied' if apply else 'Previewed'} {len(report)} records; {changed} updated. Report: {report_file}")
        missing = set(logged) - {row['id'] for row in rows}
        if missing:
            print(f'{len(missing)} logged permit IDs were absent from this database')
        return 1 if missing or any(row['action'] == 'unresolved_service_error' for row in report) else 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=BATCH_SIZE)
    parser.add_argument('--repair-log', help='Recheck geocoding successes in a saved pipeline log (preview by default)')
    parser.add_argument('--report', help='New JSON audit filename, required with --repair-log')
    parser.add_argument('--apply', action='store_true', help='Apply the repairs after writing the audit report')
    args = parser.parse_args()
    if args.limit < 1:
        parser.error('--limit must be positive')
    if args.repair_log:
        if not args.report:
            parser.error('--repair-log requires --report')
        return repair_logged_geocodes(args.repair_log, args.report, args.apply)
    if args.apply or args.report:
        parser.error('--apply and --report require --repair-log')
    return geocode_permits(args.limit)


if __name__ == '__main__':
    sys.stdout.reconfigure(line_buffering=True)
    raise SystemExit(main())
