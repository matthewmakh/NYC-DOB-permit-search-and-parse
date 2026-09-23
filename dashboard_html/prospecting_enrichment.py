"""Durable, review-before-write enrichment for private prospect lists.

Workers only fetch evidence. Approval is the sole path that changes sheet data.
Pending evidence belongs to its requester; list reassignment is not a grant to
another user's CRM or purchased contact data. Approved research travels with the
sheet, just like deliberately saved working notes.
"""
import copy
import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from uuid import uuid4

from psycopg2.extras import Json, execute_values

import crm_service as crm
import prospecting_service as s
import prospecting_workflows as w

log = logging.getLogger(__name__)
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS prospect_enrichment_jobs (
        id BIGSERIAL PRIMARY KEY, list_id INTEGER NOT NULL REFERENCES prospect_lists(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        team_id INTEGER NOT NULL, list_version INTEGER NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('internal','advanced')),
        request_key UUID NOT NULL, request_hash TEXT NOT NULL,
        cancelled_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE(user_id,request_key)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_prospect_enrichment_list ON prospect_enrichment_jobs(list_id,user_id,id DESC)""",
    """CREATE TABLE IF NOT EXISTS prospect_enrichment_items (
        id BIGSERIAL PRIMARY KEY, job_id BIGINT NOT NULL REFERENCES prospect_enrichment_jobs(id) ON DELETE CASCADE,
        row_id INTEGER NOT NULL REFERENCES prospect_rows(id) ON DELETE CASCADE, input JSONB NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued', result JSONB NOT NULL DEFAULT '{}',
        attempts INTEGER NOT NULL DEFAULT 0, lease_until TIMESTAMPTZ, claim UUID,
        decision JSONB, reviewed_at TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE(job_id,row_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_prospect_enrichment_queue ON prospect_enrichment_items(status,lease_until,id)
        WHERE status IN ('queued','running')""",
    """CREATE TABLE IF NOT EXISTS prospect_enrichment_cache (
        job_id BIGINT NOT NULL REFERENCES prospect_enrichment_jobs(id) ON DELETE CASCADE,
        cache_key TEXT NOT NULL, result JSONB NOT NULL, PRIMARY KEY(job_id,cache_key)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_crm_contact_match_name ON crm_contacts(LOWER(TRIM(name)))""",
    """CREATE INDEX IF NOT EXISTS idx_crm_contact_match_company ON crm_contacts(LOWER(TRIM(company)))""",
    """DO $$ DECLARE field_name TEXT; BEGIN
        IF to_regclass('buildings') IS NOT NULL THEN
            FOREACH field_name IN ARRAY ARRAY['address','current_owner_name','owner_name_hpd','owner_name_rpad','sale_buyer_primary','hpd_agent_name'] LOOP
                IF EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema()
                          AND table_name='buildings' AND column_name=field_name) THEN
                    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON buildings(LOWER(TRIM(%I)))',
                        'idx_prospect_match_' || field_name, field_name);
                END IF;
            END LOOP;
        END IF;
    END $$""",
]


def start(ctx, list_id, data):
    mode = data.get('mode', 'internal')
    if mode not in ('internal', 'advanced'):
        raise ValueError('Choose an existing-record check or advanced research.')
    token = s.key(data.get('request_key'))
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    ids = data.get('row_ids')
    if ids is not None and (not isinstance(ids, list) or not ids or len(ids) > s.MAX_ROWS or
                           any(type(i) is not int for i in ids) or len(set(ids)) != len(ids)):
        raise ValueError('Choose valid, distinct leads.')
    with s.transaction() as cur:
        listing = s.get_list(ctx, list_id, cur, for_update=True)
        cur.execute('SELECT * FROM prospect_enrichment_jobs WHERE user_id=%s AND request_key=%s', (ctx['user_id'], token))
        prior = cur.fetchone()
        if prior:
            if prior['list_id'] != list_id or prior['request_hash'] != digest:
                raise s.Conflict('This request was already used for different research.')
            return {'job_id': prior['id'], 'replayed': True}
        cur.execute("""SELECT 1 FROM prospect_enrichment_jobs j JOIN prospect_enrichment_items i ON i.job_id=j.id
            WHERE j.list_id=%s AND j.user_id=%s AND j.cancelled_at IS NULL AND i.status IN ('queued','running') LIMIT 1""",
                    (list_id, ctx['user_id']))
        if cur.fetchone():
            raise s.Conflict('Research is already running for you on this list. Review or cancel that run first.')
        where = ''
        params = [list_id]
        if ids is not None:
            where = ' AND id=ANY(%s)'; params.append(ids)
        parent = data.get('unresolved_job_id')
        if parent is not None:
            if type(parent) is not int or mode != 'advanced':
                raise ValueError('Choose a completed research run for advanced research.')
            job = _job(cur, ctx, list_id, parent)
            cur.execute("SELECT 1 FROM prospect_enrichment_items WHERE job_id=%s AND status IN ('queued','running') LIMIT 1", (job['id'],))
            if cur.fetchone():
                raise s.Conflict('Wait for the existing-record check to finish.')
            where += " AND id IN (SELECT row_id FROM prospect_enrichment_items WHERE job_id=%s AND (status IN ('not_found','needs_review','failed') OR jsonb_array_length(COALESCE(result->'errors','[]'))>0))"
            params.append(job['id'])
        cur.execute('SELECT * FROM prospect_rows WHERE list_id=%s' + where + ' ORDER BY id', params)
        all_rows = [dict(r) for r in cur.fetchall()]
        if ids is not None and parent is None and len(all_rows) != len(ids):
            raise LookupError('One or more selected leads are not in this list.')
        rows = [r for r in all_rows if not r['promoted_at'] and not r['archived_at'] and r['status'] != 'do_not_contact']
        if not rows:
            raise ValueError('No eligible leads. Archived, promoted and Do not contact leads are skipped.')
        cur.execute("""INSERT INTO prospect_enrichment_jobs(list_id,user_id,team_id,list_version,mode,request_key,request_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (list_id, ctx['user_id'], ctx['team_id'], listing['version'], mode, token, digest))
        job_id = cur.fetchone()['id']
        execute_values(cur, 'INSERT INTO prospect_enrichment_items(job_id,row_id,input) VALUES %s',
            [(job_id, r['id'], Json({'fields': s.mapped(r, listing), 'version': r['version'], 'position': r['position'],
                                  'mapping': listing['mapping']})) for r in rows], page_size=500)
        return {'job_id': job_id, 'count': len(rows), 'skipped': len(all_rows)-len(rows)}


def _job(cur, ctx, list_id, job_id):
    s.get_list(ctx, list_id, cur)
    cur.execute('SELECT * FROM prospect_enrichment_jobs WHERE id=%s AND list_id=%s AND user_id=%s AND team_id=%s',
                (job_id, list_id, ctx['user_id'], ctx['team_id']))
    job = cur.fetchone()
    if not job:
        raise LookupError('Research run not found. Pending results are private to the person who ran the check.')
    return dict(job)


def _allowed(cur, ctx, finding, lock=False):
    source = finding.get('source', {})
    if source.get('kind') == 'crm_contact':
        cur.execute(f'SELECT id FROM crm_contacts c WHERE c.id=%(id)s AND {crm.record_scope_sql(ctx, "c")}' + (' FOR SHARE' if lock else ''),
                    {**ctx, 'id': source['record_id']})
        return bool(cur.fetchone())
    return True


def results(ctx, list_id, job_id=None, page=1):
    page = max(1, int(page))
    with s.transaction() as cur:
        listing = s.get_list(ctx, list_id, cur)
        cur.execute('SELECT id,mode,created_at FROM prospect_enrichment_jobs WHERE list_id=%s AND user_id=%s AND team_id=%s ORDER BY id DESC LIMIT 30',
                    (list_id, ctx['user_id'], ctx['team_id']))
        jobs = [dict(r) for r in cur.fetchall()]
        if job_id is None:
            job_id = jobs[0]['id'] if jobs else None
        if job_id is None:
            return {'jobs': [], 'job': None, 'items': [], 'counts': {}}
        job = _job(cur, ctx, list_id, job_id)
        cur.execute('SELECT status,COUNT(*) AS count FROM prospect_enrichment_items WHERE job_id=%s GROUP BY status', (job_id,))
        counts = {r['status']: r['count'] for r in cur.fetchall()}
        cur.execute('SELECT COUNT(*) AS count FROM prospect_enrichment_items WHERE job_id=%s AND reviewed_at IS NOT NULL', (job_id,))
        reviewed = cur.fetchone()['count']
        cur.execute("""SELECT COUNT(*) FILTER(WHERE jsonb_array_length(COALESCE(result->'errors','[]'))>0) AS source_issues,
            COUNT(*) FILTER(WHERE status IN ('not_found','needs_review','failed') OR jsonb_array_length(COALESCE(result->'errors','[]'))>0) AS unresolved
            FROM prospect_enrichment_items WHERE job_id=%s""", (job_id,))
        issues = dict(cur.fetchone())
        cur.execute('SELECT * FROM prospect_enrichment_items WHERE job_id=%s ORDER BY id LIMIT 25 OFFSET %s', (job_id, (page-1)*25))
        items = [dict(r) for r in cur.fetchall()]
        for item in items:
            findings = item['result'].get('findings', [])
            allowed = [{**f, 'index': index} for index, f in enumerate(findings) if _allowed(cur, ctx, f)]
            item['result'] = {**item['result'], 'findings': allowed}
            if len(allowed) != len(findings):
                item['result'] = {**item['result'], 'findings': allowed,
                                  'restriction': 'Some CRM findings are no longer accessible.'}
        return {'jobs': jobs, 'job': job, 'items': items, 'counts': counts, 'reviewed': reviewed, **issues,
                'total': sum(counts.values()), 'page': page, 'stale': listing['version'] != job['list_version']}


def cancel(ctx, list_id, job_id):
    with s.transaction() as cur:
        s.get_list(ctx, list_id, cur, for_update=True)
        _job(cur, ctx, list_id, job_id)
        cur.execute('UPDATE prospect_enrichment_jobs SET cancelled_at=NOW() WHERE id=%s', (job_id,))
        cur.execute("UPDATE prospect_enrichment_items SET status='cancelled',claim=NULL,lease_until=NULL WHERE job_id=%s AND status IN ('queued','running')", (job_id,))
    return {}


def approve(ctx, list_id, job_id, data):
    entries = data.get('items')
    if not isinstance(entries, list) or not 1 <= len(entries) <= 25 or any(not isinstance(e, dict) or type(e.get('id')) is not int or
            not isinstance(e.get('selected'), list) or any(type(i) is not int for i in e['selected']) for e in entries):
        raise ValueError('Review up to 25 leads at a time.')
    if len({e['id'] for e in entries}) != len(entries):
        raise ValueError('Review each lead once.')
    with s.transaction() as cur:
        listing = s.get_list(ctx, list_id, cur, for_update=True)
        job = _job(cur, ctx, list_id, job_id)
        if listing['version'] != job['list_version']:
            raise s.Conflict('The columns or list assignment changed. Run a new check before approving findings.')
        before, after, count = [], [], 0
        for entry in entries:
            cur.execute('SELECT * FROM prospect_enrichment_items WHERE id=%s AND job_id=%s FOR UPDATE', (entry['id'], job_id))
            item = cur.fetchone()
            if not item:
                raise LookupError('Research item not found.')
            selected = sorted(set(entry['selected']))
            if item['reviewed_at']:
                if item['decision'] != selected:
                    raise s.Conflict('This research was already reviewed differently. Reload the results.')
                continue  # Network retry: no duplicate notes or edits.
            findings = item['result'].get('findings', [])
            if item['status'] in ('queued', 'running', 'cancelled') or any(i < 0 or i >= len(findings) for i in selected):
                raise ValueError('Choose completed findings from this run.')
            cur.execute('SELECT * FROM prospect_rows WHERE id=%s AND list_id=%s FOR UPDATE', (item['row_id'], list_id))
            original = dict(cur.fetchone())
            s.check_edit(original, item['input']['version'])
            if original['status'] == 'do_not_contact':
                raise s.Conflict('This lead is now marked Do not contact.')
            row = copy.deepcopy(original)
            selected_fields = {}
            for i in selected:
                if findings[i]['kind'] == 'field':
                    selected_fields.setdefault(findings[i]['field'], set()).add(findings[i]['value'])
            if any(len(values) > 1 for values in selected_fields.values()):
                raise ValueError('Choose only one proposed value per mapped field on each lead.')
            for index in selected:
                finding = findings[index]
                if not _allowed(cur, ctx, finding, lock=True):
                    raise s.Conflict('A source CRM record is no longer accessible. Reload the findings.')
                if finding['kind'] == 'field':
                    column = listing['mapping'].get(finding['field'])
                    if not column:
                        raise s.Conflict('Map a column for this field, then run the check again.')
                    row['cells'][column] = s.clean_text(finding['value'], s.MAX_CELL, 'Enriched value')
                saved = {**finding, 'job_id': job_id, 'approved_by': ctx['user_id'],
                         'approved_at': datetime.now(timezone.utc).isoformat()}
                row['research'] = [f for f in row['research'] if not (f['label']==finding['label'] and
                    f['value']==finding['value'] and f['source']['url']==finding['source']['url'])]
                row['research'].append(saved)
            if selected:
                if len(row['research']) > 1000:
                    raise ValueError('This lead already has 1,000 research findings. Review fewer additions.')
                before.append(original); after.append(w.write_row(cur, row)); count += len(selected)
            cur.execute('UPDATE prospect_enrichment_items SET decision=%s,reviewed_at=NOW() WHERE id=%s', (Json(selected), item['id']))
        change_id = w.record_change(cur, ctx, list_id, 'Approve enrichment', before, after) if before else None
        crm.audit_change(cur, ctx, 'prospect_list', list_id, listing['name'], 'updated',
                         new_value={'enrichment_job_id': job_id, 'approved_findings': count, 'change_id': change_id})
        return {'approved': count, 'change_id': change_id}


def _fresh_context(cur, user_id):
    cur.execute("""SELECT u.id,u.is_admin,sp.sponsor_user_id FROM users u LEFT JOIN LATERAL
        (SELECT sponsor_user_id FROM account_sponsorships WHERE member_user_id=u.id AND status='active'
         ORDER BY accepted_at DESC NULLS LAST LIMIT 1) sp ON true WHERE u.id=%s""", (user_id,))
    user = cur.fetchone()
    if not user:
        raise LookupError('Requester no longer exists.')
    return crm.crm_context({**user, 'is_sponsored': bool(user['sponsor_user_id'])})


def process_one():
    """Claim with a lease, release DB resources for HTTP, save with a fencing token."""
    with s.transaction() as cur:
        cur.execute("""UPDATE prospect_enrichment_items SET status='failed',claim=NULL,lease_until=NULL,
            result='{"findings":[],"errors":["Worker interrupted repeatedly. Run a new check to retry."]}'
            WHERE status='running' AND lease_until<NOW() AND attempts>=3""")
        cur.execute("""SELECT i.*,j.list_id,j.user_id,j.team_id,j.list_version,j.mode FROM prospect_enrichment_items i
            JOIN prospect_enrichment_jobs j ON j.id=i.job_id WHERE j.cancelled_at IS NULL AND
            (i.status='queued' OR (i.status='running' AND i.lease_until<NOW() AND i.attempts<3))
            ORDER BY i.id FOR UPDATE OF i SKIP LOCKED LIMIT 1""")
        item = cur.fetchone()
        if not item:
            return False
        item = dict(item); claim = str(uuid4())
        cur.execute("UPDATE prospect_enrichment_items SET status='running',claim=%s,attempts=attempts+1,lease_until=NOW()+INTERVAL '15 minutes' WHERE id=%s", (claim, item['id']))
    stop_heartbeat = threading.Event()
    def heartbeat():
        # A large HPD/ACRIS history can span many HTTP requests. Renew during
        # those requests as well as between sources so a healthy slow fetch
        # cannot be reclaimed by another web worker.
        while not stop_heartbeat.wait(60):
            try:
                with s.transaction() as cur:
                    cur.execute("""UPDATE prospect_enrichment_items SET lease_until=NOW()+INTERVAL '15 minutes'
                        WHERE id=%s AND claim=%s AND status='running'""", (item['id'], claim))
                    if not cur.rowcount:
                        return
            except Exception:
                log.exception('Could not renew prospect research item %s', item['id'])
    threading.Thread(target=heartbeat, daemon=True, name='prospect-research-lease').start()
    try:
        with s.transaction() as cur:
            ctx = _fresh_context(cur, item['user_id'])
            listing = s.get_list(ctx, item['list_id'], cur)
            if ctx['team_id'] != item['team_id'] or listing['version'] != item['list_version']:
                raise s.Conflict('List owner or columns changed. Run a new check.')
            cur.execute('SELECT * FROM prospect_rows WHERE id=%s', (item['row_id'],))
            row = dict(cur.fetchone())
            s.check_edit(row, item['input']['version'])
            if row['status'] == 'do_not_contact':
                raise s.Conflict('Lead is marked Do not contact.')
        from prospecting_research import research
        def cache(key, fetch):
            with s.transaction() as cur:
                cur.execute('SELECT claim,status FROM prospect_enrichment_items WHERE id=%s', (item['id'],))
                current = cur.fetchone()
                if not current or str(current['claim']) != claim or current['status'] != 'running':
                    raise InterruptedError('Research cancelled.')
                cur.execute("UPDATE prospect_enrichment_items SET lease_until=NOW()+INTERVAL '15 minutes' WHERE id=%s AND claim=%s", (item['id'], claim))
                cur.execute('SELECT result FROM prospect_enrichment_cache WHERE job_id=%s AND cache_key=%s', (item['job_id'], key))
                cached = cur.fetchone()
            if cached:
                return cached['result']
            result = fetch()
            with s.transaction() as cur:
                cur.execute('INSERT INTO prospect_enrichment_cache(job_id,cache_key,result) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                            (item['job_id'], key, Json(result)))
            return result
        result = research(ctx, item['input']['fields'], item['mode'], cache)
        for f in result.get('findings', []):
            if f['kind'] == 'field' and not item['input']['mapping'].get(f['field']):
                f['kind'] = 'research'; f['label'] += ' (no mapped column; save as research)'
        status = ('found' if result.get('matched') else 'needs_review') if result.get('findings') else (
            'failed' if result.get('errors') else 'not_found')
    except (s.Conflict, LookupError, PermissionError, InterruptedError) as exc:
        status, result = 'skipped', {'findings': [], 'errors': [str(exc)]}
    except Exception:
        log.exception('Prospecting enrichment item %s failed', item['id'])
        status, result = 'failed', {'findings': [], 'errors': ['Research failed. Run a new check to retry.']}
    finally:
        stop_heartbeat.set()
    with s.transaction() as cur:
        cur.execute("""UPDATE prospect_enrichment_items SET status=%s,result=%s,claim=NULL,lease_until=NULL,updated_at=NOW()
            WHERE id=%s AND claim=%s AND status='running'""", (status, Json(result), item['id'], claim))
    return True


_worker_lock = threading.Lock()
_worker = None
_wake = threading.Event()


def start_worker():
    """One bounded consumer per web process; DB leases coordinate all workers."""
    global _worker
    with _worker_lock:
        if not _worker or not _worker.is_alive():
            def run():
                while True:
                    try:
                        if process_one():
                            continue
                    except Exception:
                        log.exception('Prospecting enrichment worker will retry')
                    _wake.wait(30); _wake.clear()
            _worker = threading.Thread(target=run, daemon=True, name='prospect-enrichment')
            _worker.start()
        _wake.set()
