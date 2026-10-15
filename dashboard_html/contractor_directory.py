"""Indexed contractor read model, refreshed atomically after source imports.

Source tables remain authoritative. Two generations let readers keep using a
complete snapshot while the next one is built. Transaction-scoped advisory
locks protect publication/cleanup, and a separate lock elects one refresher
across Gunicorn workers. No persistent dependency on permit_participants:
the import pipeline deliberately drops/recreates that view.
"""
import logging
import re
import threading

log = logging.getLogger(__name__)
SOURCE = '''FROM permit_participants pp
            JOIN permits p ON p.id = pp.permit_id
            LEFT JOIN buildings b ON p.bbl = b.bbl'''
FACT_SELECT = '''SELECT pp.permit_id, pp.business_name, pp.person_name,
    pp.license_number, pp.license_type, pp.role, pp.role_confidence,
    pp.contractor_confidence, p.issue_date, p.filing_date, p.bbl,
    p.work_type, p.permit_type, p.job_type, p.permittee_license_type,
    b.assessed_total_value, b.total_units, b.building_class,
    b.hpd_open_violations ''' + SOURCE
VALID_NAME = """COALESCE(pp.business_name, pp.person_name) IS NOT NULL
    AND COALESCE(pp.business_name, pp.person_name) NOT IN ('', 'N/A', 'NA', 'NONE')
    AND COALESCE(pp.business_name, pp.person_name) NOT ILIKE 'unknown%'"""
# This aggregate is shared with the live-query fallback in app.py.
STATS_SELECT = """SELECT
    COALESCE(pp.business_name, pp.person_name) as contractor_name,
    pp.license_number as license,
    COUNT(DISTINCT p.id) as total_jobs,
    COUNT(DISTINCT CASE WHEN p.issue_date BETWEEN CURRENT_DATE - INTERVAL '90 days' AND CURRENT_DATE THEN p.id END) as active_jobs,
    COALESCE(SUM(b.assessed_total_value), 0) as total_value,
    COALESCE(MAX(b.assessed_total_value), 0) as largest_project,
    MAX(p.issue_date) FILTER (WHERE p.issue_date <= CURRENT_DATE) as most_recent_job,
    MIN(p.issue_date) as first_job,
    COUNT(DISTINCT p.bbl) as unique_properties,
    COUNT(DISTINCT NULLIF(btrim(pp.license_type), '')) as license_type_count,
    (array_agg(DISTINCT UPPER(btrim(pp.license_type)))
        FILTER (WHERE btrim(coalesce(pp.license_type, '')) <> ''))[1]
        as license_type,
    string_agg(DISTINCT pp.role, ', ' ORDER BY pp.role) AS participant_roles,
    MAX(pp.role_confidence) AS role_confidence,
    MAX(pp.contractor_confidence) AS contractor_confidence,
    BOOL_OR(pp.role IN ('permittee', 'permit_applicant')
            AND pp.contractor_confidence >= 0.85) AS probable_gc"""


def fact_sql(sql):
    """Rewrite only trusted, server-built filter/aggregate SQL for narrow facts."""
    sql = sql.replace('p.id', 'f.permit_id')
    return re.sub(r'\b(?:pp|p|b)\.', 'f.', sql)


def prepare(cur):
    """Pin a generation until DatabaseConnection commits, or use live fallback."""
    cur.execute('SET LOCAL jit = off')
    cur.execute("SELECT to_regclass('contractor_directory_state') AS relation")
    if cur.fetchone()['relation'] is None:
        return None
    cur.execute('SELECT pg_advisory_xact_lock_shared(72119, 1)')
    cur.execute('SELECT generation FROM contractor_directory_state WHERE singleton')
    row = cur.fetchone()
    return row['generation'] if row and row['generation'] else None


def ensure_schema(cur):
    cur.execute("SET LOCAL lock_timeout = '5s'")
    cur.execute('''CREATE TABLE IF NOT EXISTS contractor_directory_state (
        singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),
        generation BIGINT NOT NULL DEFAULT 0,
        source_version BIGINT NOT NULL DEFAULT 0,
        indexed_version BIGINT NOT NULL DEFAULT -1,
        changed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        indexed_at TIMESTAMPTZ,
        indexed_date DATE);
        INSERT INTO contractor_directory_state(singleton) VALUES(true)
        ON CONFLICT DO NOTHING''')
    cur.execute('CREATE TABLE IF NOT EXISTS contractor_directory_facts AS '
                'SELECT 0::bigint AS generation, facts.* FROM (' + FACT_SELECT +
                ') facts WITH NO DATA')
    cur.execute('CREATE TABLE IF NOT EXISTS contractor_directory_stats AS '
                'SELECT 0::bigint AS generation, stats.* FROM (' +
                fact_sql(STATS_SELECT) + ' FROM contractor_directory_facts f '
                'WHERE false GROUP BY COALESCE(f.business_name, f.person_name), '
                'f.license_number) stats WITH NO DATA')
    cur.execute('''CREATE INDEX IF NOT EXISTS contractor_directory_facts_name
        ON contractor_directory_facts(generation, (COALESCE(business_name,person_name)));
        CREATE INDEX IF NOT EXISTS contractor_directory_facts_role
        ON contractor_directory_facts(generation,role);
        CREATE INDEX IF NOT EXISTS contractor_directory_stats_name
        ON contractor_directory_stats(generation,contractor_name)''')
    for column in ('total_jobs', 'active_jobs', 'total_value', 'largest_project',
                   'unique_properties', 'most_recent_job'):
        cur.execute(f'CREATE INDEX IF NOT EXISTS contractor_directory_stats_{column} '
                    f'ON contractor_directory_stats(generation, {column} DESC NULLS LAST, contractor_name)')
    cur.execute('''CREATE OR REPLACE FUNCTION contractor_directory_changed()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            UPDATE contractor_directory_state SET source_version=source_version+1,
                changed_at=clock_timestamp() WHERE singleton;
            RETURN NULL;
        END $$''')
    # One invalidation per SQL statement, including bulk imports and truncation.
    for table in ('permits', 'buildings'):
        cur.execute(f'DROP TRIGGER IF EXISTS contractor_directory_changed ON {table}')
        cur.execute(f'''CREATE TRIGGER contractor_directory_changed
            AFTER INSERT OR DELETE OR TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION contractor_directory_changed()''')
        cur.execute(f'DROP TRIGGER IF EXISTS contractor_directory_updated ON {table}')
        update = ('UPDATE OF bbl, assessed_total_value, total_units, building_class, hpd_open_violations'
                  if table == 'buildings' else 'UPDATE')
        cur.execute(f'''CREATE TRIGGER contractor_directory_updated
            AFTER {update} ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION contractor_directory_changed()''')


def refresh(connection, force=False):
    """Build/publish one snapshot; errors roll back without harming the last one.

    Call with a dedicated connection. Imports arriving during a build keep the
    model dirty, so the following pass catches up. Activity counts age daily.
    """
    with connection:
        with connection.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_xact_lock(72119, 0) AS acquired')
            if not cur.fetchone()['acquired']:
                return False
            cur.execute('''SELECT *, CURRENT_DATE AS today,
                (clock_timestamp()-changed_at > interval '15 seconds'
                 OR indexed_at IS NULL
                 OR clock_timestamp()-indexed_at > interval '5 minutes') AS settled
                FROM contractor_directory_state WHERE singleton''')
            state = cur.fetchone()
            dirty = state['source_version'] != state['indexed_version']
            if not force and state['generation'] and (
                    (not dirty and state['indexed_date'] == state['today'])
                    or (dirty and not state['settled'])):
                return False
            generation = state['generation'] + 1
            cur.execute('SET LOCAL jit = off')
            cur.execute("SET LOCAL work_mem = '32MB'")
            cur.execute('SET LOCAL statement_timeout = 180000')
            # The previous publication drained readers of older generations.
            # Clean those now, before publication can briefly pause new readers.
            for table in ('contractor_directory_facts', 'contractor_directory_stats'):
                cur.execute(f'DELETE FROM {table} WHERE generation <> %s', [state['generation']])
            cur.execute('INSERT INTO contractor_directory_facts '
                        'SELECT %s, facts.* FROM (' + FACT_SELECT + ') facts', [generation])
            cur.execute('INSERT INTO contractor_directory_stats '
                        'SELECT %s, stats.* FROM (' + fact_sql(STATS_SELECT) +
                        ' FROM contractor_directory_facts f WHERE f.generation=%s AND ' +
                        fact_sql(VALID_NAME).replace('%', '%%') + ' GROUP BY COALESCE(f.business_name,f.person_name), '
                        'f.license_number) stats', [generation, generation])
            # Acquire only when ready to publish; current searches keep running
            # throughout the build. Readers hold the shared lock until commit.
            for table in ('contractor_directory_facts', 'contractor_directory_stats'):
                cur.execute(f'ANALYZE {table}')
            # An import may hold the state row; wait before pausing readers.
            cur.execute('SELECT singleton FROM contractor_directory_state WHERE singleton FOR UPDATE')
            cur.execute('SELECT pg_advisory_xact_lock(72119, 1)')
            cur.execute('''UPDATE contractor_directory_state
                SET generation=%s,indexed_version=%s,indexed_at=clock_timestamp(),
                    indexed_date=%s WHERE singleton''',
                        [generation, state['source_version'], state['today']])
            return True


_started = False
_start_lock = threading.Lock()


def start_worker(connect):
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    def run():
        import time
        initialized = False
        while True:
            connection = None
            try:
                connection = connect()
                if not initialized:
                    with connection:
                        with connection.cursor() as cur:
                            cur.execute('SELECT pg_advisory_xact_lock(72119, 0)')
                            ensure_schema(cur)
                    initialized = True
                refresh(connection)
            except Exception:
                log.exception('Contractor directory refresh failed; retaining previous snapshot')
            finally:
                if connection is not None:
                    connection.close()
            time.sleep(10)
    threading.Thread(target=run, name='contractor-directory', daemon=True).start()


def adapt_query(query, generation):
    """Keep all existing row filters/aggregate semantics on the indexed facts."""
    if generation is None:
        return query
    query = re.sub(r'FROM\s+permit_participants\s+pp\s+JOIN\s+permits\s+p\s+ON\s+p.id\s*=\s*pp.permit_id\s+LEFT JOIN buildings b ON p.bbl = b.bbl',
                   'FROM contractor_directory_facts f', query)
    query = fact_sql(query)
    return re.sub(r'FROM contractor_directory_facts f\s+WHERE ',
                  f'FROM contractor_directory_facts f WHERE f.generation = {int(generation)} AND ',
                  query, count=1)


def summary_queries(args, order_by, generation):
    """Use precomputed aggregates only when no permit/building/role filter applies."""
    if generation is None:
        return None
    row_filters = ('role', 'probable_gc', 'permit_type', 'work_type', 'job_type',
                   'license_type', 'recent_permit_days', 'borough', 'property_type',
                   'building_class', 'has_violations', 'min_units', 'max_units',
                   'min_value', 'max_value')
    if any(any(args.getlist(key)) for key in row_filters):
        return None
    parts, params = ['generation = %s'], [generation]
    search = args.get('search', '').strip()
    if search:
        parts.append('(contractor_name ILIKE %s OR license ILIKE %s)')
        params.extend([f'%{search}%', f'%{search}%'])
    for key, column, operator in (
            ('min_jobs', 'total_jobs', '>='), ('max_jobs', 'total_jobs', '<='),
            ('min_active_jobs', 'active_jobs', '>='),
            ('min_properties', 'unique_properties', '>='),
            ('max_properties', 'unique_properties', '<=')):
        value = args.get(key, type=int)
        if value is not None:
            parts.append(f'{column} {operator} %s')
            params.append(value)
    source = 'FROM contractor_directory_stats WHERE ' + ' AND '.join(parts)
    return (f'SELECT *, COUNT(*) OVER() AS directory_total {source} '
            f'ORDER BY {order_by} LIMIT %s OFFSET %s',
            f'SELECT COUNT(*) AS count {source}', params)
