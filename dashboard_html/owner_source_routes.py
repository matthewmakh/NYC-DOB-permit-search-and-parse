"""Authenticated source refresh requests backed by a bounded, durable queue."""
import logging
import re
import threading
from datetime import datetime, timedelta, timezone

import psycopg2.extras
from flask import Blueprint, g, jsonify, request

from auth_service import login_required
from owner_source_history import SOURCES, serial

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
COOLDOWN = timedelta(minutes=5)
RETRY_DELAY = timedelta(hours=6)
_worker_started = False
_worker_lock = threading.Lock()
_wake_worker = threading.Event()


def _validate_bbl(bbl):
    if not re.fullmatch(r'[1-5][0-9]{9}', str(bbl or '')):
        raise ValueError('Use a valid 10-digit NYC BBL.')
    return bbl


def _mutation_allowed():
    # Share the review endpoint's proxy-aware CSRF rules.
    from owner_research_routes import same_origin_review_request
    return same_origin_review_request()


def _find_building(cur, bbl, lock=False):
    cur.execute("""SELECT id,to_jsonb(b) || jsonb_build_object(
        'acris_last_enriched',(to_jsonb(b)->>'acris_last_enriched')::timestamptz,
        'ecb_last_checked',(to_jsonb(b)->>'ecb_last_checked')::timestamptz,
        'sos_last_enriched',(to_jsonb(b)->>'sos_last_enriched')::timestamptz) AS building
        FROM buildings b WHERE bbl=%s"""
                + (' FOR UPDATE' if lock else ''), (bbl,))
    row = cur.fetchone()
    if row is None:
        raise LookupError('Property not found.')
    return row


def _utc(value):
    if not value:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _fallback_check(building, source):
    field = {'acris': 'acris_last_enriched', 'ecb': 'ecb_last_checked', 'sos': 'sos_last_enriched'}.get(source)
    return _utc(building.get(field)) if field else None


def enqueue_source_refresh(conn, bbl, source, user_id):
    """Deduplicate before responding; no request thread contacts outside services."""
    _validate_bbl(bbl)
    if source not in SOURCES:
        raise ValueError('Unknown owner source.')
    now = datetime.now(timezone.utc)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Bound pending work from one user even when requests target different lots.
        cur.execute('SELECT pg_advisory_xact_lock(72112,%s)', (int(user_id),))
        row = _find_building(cur, bbl, lock=True)
        building_id = row['id']
        cur.execute('SELECT * FROM owner_source_jobs WHERE building_id=%s AND source=%s',
                    (building_id, source))
        job = cur.fetchone()
        if job and job['status'] in ('queued', 'running'):
            result = {'status': job['status'], 'message': (
                'Refresh is waiting for its scheduled retry; the last successful records are preserved.'
                if job.get('last_error') else 'Refresh is already ' + job['status'] + '.')}
        else:
            cur.execute("""SELECT checked_at AT TIME ZONE current_setting('TimeZone') AS checked_at,
                next_attempt_at AT TIME ZONE current_setting('TimeZone') AS next_attempt_at,error
                FROM building_source_refresh WHERE building_id=%s AND source=%s""", (building_id, source))
            state = cur.fetchone() or {}
            checked = _utc(state.get('checked_at')) or _fallback_check(row['building'], source)
            if state.get('error') and state.get('next_attempt_at') and _utc(state['next_attempt_at']) > now:
                result = {'status': 'failed', 'message': 'This source is temporarily unavailable. Retry after '
                          + serial(_utc(state['next_attempt_at'])) + '; existing records are preserved.'}
            elif (job and job['status'] == 'failed' and _utc(job['available_at']) > now
                  and not (checked and checked > _utc(job['updated_at']))):
                result = {'status': 'failed', 'message': 'This source is temporarily unavailable. Retry after '
                          + serial(_utc(job['available_at'])) + '; existing records are preserved.'}
            elif checked and checked + COOLDOWN > now:
                result = {'status': 'current', 'message': 'This source was checked within the last five minutes.'}
            else:
                cur.execute("SELECT count(*) AS count FROM owner_source_jobs WHERE requested_by=%s "
                            "AND status IN ('queued','running')", (user_id,))
                if cur.fetchone()['count'] >= 12:
                    raise ValueError('You already have 12 source refreshes pending. Wait for those to finish.')
                cur.execute('''INSERT INTO owner_source_jobs (building_id,bbl,source,requested_by)
                    VALUES (%s,%s,%s,%s) ON CONFLICT (building_id,source) DO UPDATE SET
                    status='queued',attempts=0,requested_by=EXCLUDED.requested_by,requested_at=NOW(),
                    available_at=NOW(),locked_at=NULL,last_error=NULL,updated_at=NOW()''',
                    (building_id, bbl, source, user_id))
                result = {'status': 'queued', 'message': 'Refresh queued. This uses the free public source.'}
    conn.commit()
    _wake_worker.set()
    return result


def source_status(conn, bbl):
    _validate_bbl(bbl)
    now = datetime.now(timezone.utc)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        row = _find_building(cur, bbl)
        building_id, building = row['id'], row['building']
        cur.execute("""SELECT source,error,checked_at AT TIME ZONE current_setting('TimeZone') AS checked_at,
            next_attempt_at AT TIME ZONE current_setting('TimeZone') AS next_attempt_at
            FROM building_source_refresh WHERE building_id=%s""", (building_id,))
        states = {item['source']: item for item in cur.fetchall()}
        cur.execute('SELECT * FROM owner_source_jobs WHERE building_id=%s', (building_id,))
        jobs = {item['source']: item for item in cur.fetchall()}
        sources = []
        for source, label in SOURCES.items():
            state = states.get(source, {})
            job = jobs.get(source, {})
            checked = _utc(state.get('checked_at')) or _fallback_check(building, source)
            fallback_error = building.get({'acris': 'acris_last_error', 'sos': 'sos_last_error'}.get(source, ''))
            # A later automated success supersedes a terminal manual failure.
            job_failed = job.get('status') == 'failed' and not (
                checked and checked > _utc(job['updated_at']))
            error = state.get('error') or (job.get('last_error') if job_failed or job.get('status') == 'queued' else None) or fallback_error
            status = job.get('status') if job.get('status') in ('queued', 'running') or job_failed else (
                'failed' if error else 'current' if checked else 'not_checked')
            next_attempt = (job.get('available_at') if status in ('queued', 'failed') and job.get('last_error')
                            else state.get('next_attempt_at') if error else checked + COOLDOWN if checked else None)
            can_refresh = status not in ('queued', 'running') and (
                not next_attempt or _utc(next_attempt) <= now)
            sources.append(dict(key=source, label=label, status=status, checked_at=checked,
                error=('The latest attempt failed. Previously saved records are preserved.' if error else None),
                next_attempt_at=next_attempt, can_refresh=can_refresh))
        cur.execute('''SELECT id,source,kind,reported_date,observed_at,before,after,changes
            FROM owner_source_history WHERE building_id=%s
            ORDER BY observed_at DESC,id DESC LIMIT 100''', (building_id,))
        history = [dict(item) for item in cur.fetchall()]
    return serial({'sources': sources, 'history': history})


def run_source_adapter(conn, building_id, bbl, source):
    """Only the requested free public adapter runs; ECB does not refresh other tax feeds."""
    if source in ('hpd', 'pluto', 'rpad'):
        from property_source_refresh import refresh_property_sources
        report = refresh_property_sources(conn, building_id, bbl, sources=[source], force=True)
        result = report.get(source, report.get('property', 'error: refresh did not return a result'))
        if result == 'already running':
            raise BlockingIOError('Another property source refresh is running.')
    elif source == 'acris':
        from property_lookup import _run_acris
        result = _run_acris(conn, building_id, bbl)
    elif source == 'ecb':
        from step4_enrich_from_tax_liens import get_ecb_violations_data, update_building_tax_lien_data
        from owner_source_history import source_revisions, capture_source_snapshot, source_revision_matches
        revision = source_revisions(conn, [building_id], 'ecb')[building_id]
        data, error = get_ecb_violations_data(bbl)
        if error:
            with conn.cursor() as cur:
                capture_source_snapshot(cur, building_id, 'ecb')
                superseded = not source_revision_matches(cur, building_id, 'ecb', revision)
            conn.commit()
            if superseded:
                return 'newer refresh retained'
            raise RuntimeError(error)
        data['_source_revision'] = revision
        with conn.cursor() as cur:
            update_building_tax_lien_data(cur, building_id, data)
        conn.commit()
        result = 'updated'
    elif source == 'sos':
        from property_lookup import _run_sos
        result = _run_sos(conn, building_id, bbl)
    else:
        raise ValueError('Unknown owner source.')
    if 'error:' in str(result):
        raise RuntimeError(result)
    return result


def _finish_job(conn, job_id, building_id, source, error=None, busy=False, revision=None):
    superseded = False
    with conn.cursor() as cur:
        if error and not busy:
            from owner_source_history import source_revision_matches
            cur.execute('SELECT id FROM buildings WHERE id=%s FOR UPDATE', (building_id,))
            if not source_revision_matches(cur, building_id, source, revision):
                error = None
                superseded = True
        if busy:
            cur.execute("""UPDATE owner_source_jobs SET status='queued',attempts=GREATEST(attempts-1,0),
                available_at=NOW()+INTERVAL '1 minute',locked_at=NULL,updated_at=NOW()
                WHERE id=%s""", (job_id,))
        elif error:
            cur.execute("""UPDATE owner_source_jobs SET status=CASE WHEN attempts >= %s THEN 'failed' ELSE 'queued' END,
                available_at=NOW()+INTERVAL '6 hours',locked_at=NULL,last_error=%s,updated_at=NOW()
                WHERE id=%s""", (MAX_ATTEMPTS, str(error)[:1000], job_id))
            cur.execute("""INSERT INTO building_source_refresh
                (building_id,source,attempted_at,next_attempt_at,error)
                VALUES (%s,%s,NOW(),NOW()+INTERVAL '6 hours',%s)
                ON CONFLICT (building_id,source) DO UPDATE SET attempted_at=NOW(),
                next_attempt_at=EXCLUDED.next_attempt_at,error=EXCLUDED.error""",
                (building_id, source, str(error)[:1000]))
        else:
            cur.execute("""UPDATE owner_source_jobs SET status='completed',locked_at=NULL,
                last_error=NULL,updated_at=NOW() WHERE id=%s""", (job_id,))
    conn.commit()
    return superseded


def process_source_job(connect, job_id):
    conn = connect()
    job = None
    revision = None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,72111)) AS acquired', (str(job_id),))
            if not cur.fetchone()['acquired']:
                return None
            cur.execute("""UPDATE owner_source_jobs SET status='running',attempts=attempts+1,
                locked_at=NOW(),updated_at=NOW() WHERE id=%s AND status='queued' AND available_at<=NOW()
                RETURNING building_id,bbl,source""", (job_id,))
            job = cur.fetchone()
        conn.commit()
        if not job:
            return None
        from owner_source_history import source_revisions
        revision = source_revisions(conn, [job['building_id']], job['source'])[job['building_id']]
        result = run_source_adapter(conn, job['building_id'], job['bbl'], job['source'])
        _finish_job(conn, job_id, job['building_id'], job['source'])
        return result
    except Exception as exc:
        conn.rollback()
        if job:
            if _finish_job(conn, job_id, job['building_id'], job['source'],
                           error=exc, busy=isinstance(exc, BlockingIOError), revision=revision):
                return 'newer refresh retained'
        log.warning('Owner source refresh did not complete: job=%s source=%s', job_id, (job or {}).get('source'))
        return 'failed'
    finally:
        # Closing the dedicated connection also releases its session advisory lock.
        conn.close()


def process_queued_source_jobs(connect, limit=5):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""UPDATE owner_source_jobs SET status=CASE WHEN attempts >= %s THEN 'failed' ELSE 'queued' END,
                locked_at=NULL,available_at=NOW(),last_error='The worker stopped before completing this refresh.',
                updated_at=NOW() WHERE status='running' AND locked_at < NOW()-INTERVAL '20 minutes'
                AND pg_try_advisory_xact_lock(hashtextextended(id::text,72111))""", (MAX_ATTEMPTS,))
            cur.execute("SELECT id FROM owner_source_jobs WHERE status='queued' AND available_at<=NOW() "
                        'ORDER BY requested_at,id LIMIT %s', (min(max(int(limit), 1), 25),))
            job_ids = [row[0] for row in cur.fetchall()]
        conn.commit()
    finally:
        conn.close()
    for job_id in job_ids:
        process_source_job(connect, job_id)
    return len(job_ids)


def start_owner_source_worker(connect):
    """Each web worker drains a small batch; PostgreSQL coordinates claims/recovery."""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
    def run():
        while True:
            try:
                process_queued_source_jobs(connect)
            except Exception:
                log.exception('Owner source recovery pass failed')
            _wake_worker.wait(30)
            _wake_worker.clear()
    threading.Thread(target=run, daemon=True, name='owner-source-refresh').start()


def create_blueprint(connect):
    bp = Blueprint('owner_sources', __name__)

    @bp.after_request
    def private_response(response):
        response.headers['Cache-Control'] = 'private, no-store'
        response.vary.add('Cookie')
        return response

    @bp.get('/api/property/<bbl>/owner-sources')
    @login_required
    def get_sources(bbl):
        conn = None
        try:
            _validate_bbl(bbl)
            conn = connect()
            return jsonify(success=True, **source_status(conn, bbl))
        except (ValueError, LookupError) as exc:
            return jsonify(success=False, error=str(exc)), 404 if isinstance(exc, LookupError) else 400
        except Exception:
            log.exception('Could not load owner source state')
            return jsonify(success=False, error='Could not load source status. Please retry.'), 503
        finally:
            if conn is not None:
                conn.close()

    @bp.post('/api/property/<bbl>/owner-sources/<source>/refresh')
    @login_required
    def request_refresh(bbl, source):
        if not _mutation_allowed():
            return jsonify(success=False, error='Reload this page before requesting a refresh.'), 403
        if request.content_length and request.content_length > 1024:
            return jsonify(success=False, error='Refresh request is too large.'), 413
        if not isinstance(request.get_json(silent=True), dict):
            return jsonify(success=False, error='Expected a JSON object.'), 400
        conn = None
        try:
            _validate_bbl(bbl)
            if source not in SOURCES:
                raise ValueError('Unknown owner source.')
            conn = connect()
            result = enqueue_source_refresh(conn, bbl, source, g.user['id'])
            return jsonify(success=True, **result), 202 if result['status'] in ('queued', 'running') else 200
        except (ValueError, LookupError) as exc:
            if conn is not None:
                conn.rollback()
            return jsonify(success=False, error=str(exc)), 404 if isinstance(exc, LookupError) else 400
        except Exception:
            if conn is not None:
                conn.rollback()
            log.exception('Could not queue owner source refresh')
            return jsonify(success=False, error='Could not queue this refresh. Please retry.'), 503
        finally:
            if conn is not None:
                conn.close()

    return bp
