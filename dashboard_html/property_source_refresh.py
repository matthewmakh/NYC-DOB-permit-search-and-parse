"""Independent property-source checkpoints shared by cron and on-demand work."""
import os

import psycopg2.extras

SOURCE_DAYS = {'pluto': 30, 'rpad': 365, 'hpd': 14}
PLUTO_FIELDS = ('building_class land_use residential_units total_units num_floors '
                'building_sqft lot_sqft year_built year_altered zoning_district built_far max_resid_far max_comm_far '
                'unused_far pluto_owner_type').split()
HPD_FIELDS = ('owner_name_hpd hpd_registration_id hpd_open_violations hpd_total_violations '
              'hpd_open_complaints hpd_total_complaints hpd_owner_business_address '
              'hpd_owner_business_city hpd_owner_business_state hpd_owner_business_zip '
              'hpd_agent_name hpd_site_manager_name').split()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS building_source_refresh (
    building_id INTEGER NOT NULL REFERENCES buildings(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    attempted_at TIMESTAMP,
    checked_at TIMESTAMP,
    next_attempt_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    error TEXT,
    PRIMARY KEY (building_id, source)
);
CREATE INDEX IF NOT EXISTS idx_building_source_refresh_due
    ON building_source_refresh (source, next_attempt_at);
"""


BUILDING_COLUMNS_SQL = """
ALTER TABLE buildings
    ADD COLUMN IF NOT EXISTS signals_last_attempted TIMESTAMP,
    ADD COLUMN IF NOT EXISTS tax_last_attempted TIMESTAMP,
    ADD COLUMN IF NOT EXISTS tax_last_error TEXT;
ALTER TABLE buildings ALTER COLUMN is_cash_purchase DROP NOT NULL;
ALTER TABLE buildings ALTER COLUMN dob_complaint_count DROP NOT NULL;
ALTER TABLE buildings ALTER COLUMN dob_active_complaint_count DROP NOT NULL;
"""


def source_fields(source, data):
    """An authoritative empty response clears source-owned facts, not parcel identity."""
    data = data or {}
    if source == 'pluto':
        fields = {key: data.get(key) for key in PLUTO_FIELDS}
        fields['current_owner_name'] = data.get('owner_name')
        for key in ('address', 'bin', 'latitude', 'longitude', 'zip_code'):
            if data.get(key):
                fields[key] = data[key]
        return fields
    if source == 'rpad':
        return {key: data.get(key) for key in (
            'owner_name_rpad', 'assessed_land_value', 'assessed_total_value')}
    return {key: data.get(key) for key in HPD_FIELDS}


def refresh_property_sources(conn, building_id, bbl, sources=None, force=False):
    from step2_enrich_from_pluto import (
        get_pluto_data_for_bbl, get_rpad_data_for_bbl, get_hpd_data_for_bbl)
    fetchers = dict(zip(SOURCE_DAYS, (
        get_pluto_data_for_bbl, get_rpad_data_for_bbl, get_hpd_data_for_bbl)))
    report = {}
    with conn.cursor(cursor_factory=psycopg2.extensions.cursor) as cur:
        cur.execute('SELECT pg_try_advisory_lock(72102, %s)', (building_id,))
        if not cur.fetchone()[0]:
            conn.commit()
            return {'property': 'already running'}
    try:
        for source in sources or SOURCE_DAYS:
            with conn.cursor(cursor_factory=psycopg2.extensions.cursor) as cur:
                cur.execute('SELECT next_attempt_at > NOW(), error FROM building_source_refresh '
                            'WHERE building_id=%s AND source=%s', (building_id, source))
                state = cur.fetchone()
                if not force and state and state[0]:
                    report[source] = f'error: awaiting retry: {state[1]}' if state[1] else 'current'
                    continue
            # End read transactions before waiting on outside services.
            conn.commit()
            try:
                data, error = fetchers[source](bbl)
                if error:
                    raise RuntimeError(error)
                fields = source_fields(source, data)
                with conn.cursor(cursor_factory=psycopg2.extensions.cursor) as cur:
                    cur.execute('UPDATE buildings SET ' + ', '.join(f'{k}=%s' for k in fields)
                                + ' WHERE id=%s', [*fields.values(), building_id])
                    cur.execute("""INSERT INTO building_source_refresh
                        (building_id,source,attempted_at,checked_at,next_attempt_at,error)
                        VALUES (%s,%s,NOW(),NOW(),NOW() + %s * INTERVAL '1 day',NULL)
                        ON CONFLICT (building_id,source) DO UPDATE SET
                        attempted_at=NOW(),checked_at=NOW(),
                        next_attempt_at=EXCLUDED.next_attempt_at,error=NULL""",
                        (building_id, source, SOURCE_DAYS[source]))
                conn.commit()
                report[source] = 'updated' if data else 'no source record'
            except Exception as exc:
                conn.rollback()
                with conn.cursor(cursor_factory=psycopg2.extensions.cursor) as cur:
                    cur.execute("""INSERT INTO building_source_refresh
                        (building_id,source,attempted_at,next_attempt_at,error)
                        VALUES (%s,%s,NOW(),NOW() + INTERVAL '6 hours',%s)
                        ON CONFLICT (building_id,source) DO UPDATE SET
                        attempted_at=NOW(),next_attempt_at=EXCLUDED.next_attempt_at,
                        error=EXCLUDED.error""", (building_id, source, str(exc)[:1000]))
                conn.commit()
                report[source] = f'error: {exc}'
        with conn.cursor(cursor_factory=psycopg2.extensions.cursor) as cur:
            cur.execute("""UPDATE buildings SET property_last_attempted=NOW(),
                property_last_error=(SELECT string_agg(source || ': ' || error, '; ')
                    FROM building_source_refresh WHERE building_id=%s AND error IS NOT NULL),
                property_last_enriched=(SELECT CASE WHEN count(checked_at)=3 THEN min(checked_at) END
                    FROM building_source_refresh WHERE building_id=%s
                    AND source IN ('pluto','rpad','hpd'))
                WHERE id=%s""", (building_id, building_id, building_id))
        conn.commit()
        return report
    finally:
        conn.rollback()
        with conn.cursor(cursor_factory=psycopg2.extensions.cursor) as cur:
            cur.execute('SELECT pg_advisory_unlock(72102, %s)', (building_id,))
        conn.commit()


def run_property_refresh(connect):
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
    limit = max(1, int(os.getenv('STEP2_BATCH_LIMIT', '16000')))
    workers = max(1, int(os.getenv('PROPERTY_REFRESH_WORKERS', '6')))
    conn = connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extensions.cursor) as cur:
            cur.execute("""SELECT b.id,b.bbl,min(s.attempted_at) AS attempted
                FROM buildings b
                CROSS JOIN (VALUES ('pluto'),('rpad'),('hpd')) AS sources(source)
                LEFT JOIN building_source_refresh s
                    ON s.building_id=b.id AND s.source=sources.source
                WHERE b.bbl IS NOT NULL AND (s.next_attempt_at IS NULL OR s.next_attempt_at<=NOW())
                GROUP BY b.id,b.bbl ORDER BY min(s.attempted_at) ASC NULLS FIRST,b.id LIMIT %s""", (limit,))
            rows = cur.fetchall()
    finally:
        conn.close()
    def process(row):
        connection = connect()
        try:
            report = refresh_property_sources(connection, row[0], row[1])
            errors = [value for value in report.values() if value.startswith('error:')]
            if errors:
                print(f'Property {row[0]}: {"; ".join(errors)}', flush=True)
            return not errors
        finally:
            connection.close()
    failed = done = 0
    error_limit = max(1, int(os.getenv('PROPERTY_REFRESH_ERROR_LIMIT', '100')))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        remaining = iter(rows)
        pending = set()
        while True:
            while failed < error_limit and len(pending) < workers * 2:
                row = next(remaining, None)
                if row is None:
                    break
                pending.add(pool.submit(process, row))
            if not pending:
                break
            completed, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                try:
                    failed += not future.result()
                except Exception as exc:
                    print(f'Property refresh failed: {exc}', flush=True)
                    failed += 1
                done += 1
            if done % 100 == 0 or done == len(rows):
                print(f'Property refresh: {done}/{len(rows)}; failures={failed}', flush=True)
    if failed:
        raise SystemExit(1)
