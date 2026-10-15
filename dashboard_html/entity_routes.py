"""Entity research pages and API: research a person or company by name.

Pages
  GET  /entities                      saved and recent research
  GET  /entity/research?name=..       create-or-reuse a dossier, queue research, redirect
  GET  /entity/<id>                   the profile page

API (login required; writes need a same-origin JSON request)
  POST /api/entity/research           {name, context, force} -> dossier + job
  GET  /api/entity/<id>               dossier, evidence, connections, CRM matches
  GET  /api/entity/jobs/<job_id>      step progress
  POST /api/entity/<id>/refresh       re-run external sources, ignoring the 24h cache
  POST /api/entity/<id>/expand        second hop over connected names
  POST /api/entity/<id>/keep          {permanent: bool}
  POST /api/entity/<id>/add-property  {bbl} -> adds the lot to buildings (explicit, permanent)
"""
import logging
import re
from collections import defaultdict
from urllib.parse import urlencode

from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request
from psycopg2.extras import RealDictCursor

from auth_service import login_required
from crm_service import crm_context
import entity_research as er

log = logging.getLogger(__name__)
MAX_ROWS = 1500


def create_blueprint(connect):
    bp = Blueprint('entity', __name__)

    def ctx():
        try:
            return crm_context(g.user)
        except Exception:
            return None

    def user_id():
        return (g.user or {}).get('id')

    def same_origin_write():
        from owner_research_routes import _origin, request_origin
        if not request.is_json or request.headers.get('X-Entity-Research') != '1':
            return False
        if request.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
            return False
        try:
            for header in ('Origin', 'Referer'):
                value = request.headers.get(header)
                if value and _origin(value) != request_origin():
                    return False
        except ValueError:
            return False
        return True

    def with_conn(fn):
        conn = connect()
        try:
            return fn(conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def start(conn, name, context, force=False):
        dossier, created = er.get_or_create_dossier(conn, name, context, user_id())
        er.touch_dossier(conn, dossier['id'])
        job, queued = er.enqueue_job(conn, dossier['id'], 'research', user_id(), force=force)
        er._wake.set()
        return dossier, job, created, queued

    @bp.after_request
    def private(response):
        if request.path.startswith('/api/entity'):
            response.headers['Cache-Control'] = 'private, no-store'
            response.headers['Vary'] = 'Cookie'
        return response

    # ------------------------------------------------------------------ pages

    @bp.route('/entities')
    @login_required
    def entities_page():
        return render_template('entities.html', active_page='home')

    @bp.route('/entity/research')
    @login_required
    def research_redirect():
        name = request.args.get('name', '')
        context = {k: request.args.get(k) for k in ('bbl', 'role', 'address', 'city', 'state', 'zip', 'source')}
        try:
            dossier, job, _, _ = with_conn(lambda conn: start(conn, name, context))
        except er.ResearchError as exc:
            return render_template('entities.html', active_page='home', error=str(exc), query=name), exc.status_code
        return redirect(f"/entity/{dossier['id']}?" + urlencode({'job': job['id']}))

    @bp.route('/entity/<int:dossier_id>')
    @login_required
    def entity_page(dossier_id):
        dossier = with_conn(lambda conn: er.load_dossier(conn, dossier_id))
        if not dossier:
            return render_template('entities.html', active_page='home',
                                   error='That research has expired or was never saved.'), 404
        return render_template('entity_profile.html', dossier=dossier, active_page='home')

    # -------------------------------------------------------------------- api

    @bp.route('/api/entity/research', methods=['POST'])
    @login_required
    def api_research():
        if not same_origin_write():
            return jsonify(success=False, error='A same-origin JSON request is required'), 403
        data = request.get_json(silent=True) or {}
        try:
            dossier, job, created, queued = with_conn(
                lambda conn: start(conn, data.get('name'), data.get('context'), bool(data.get('force'))))
        except er.ResearchError as exc:
            return jsonify(success=False, error=str(exc)), exc.status_code
        return jsonify(success=True, dossier_id=dossier['id'], job_id=job['id'], created=created, queued=queued,
                       cached=er.external_is_fresh(dossier) and not data.get('force'),
                       url=f"/entity/{dossier['id']}?job={job['id']}")

    @bp.route('/api/entity/jobs/<int:job_id>')
    @login_required
    def api_job(job_id):
        job = with_conn(lambda conn: er.get_job(conn, job_id))
        if not job:
            return jsonify(success=False, error='Job not found'), 404
        return jsonify(success=True, job=serialize_job(job))

    @bp.route('/api/entity/<int:dossier_id>')
    @login_required
    def api_dossier(dossier_id):
        def read(conn):
            dossier = er.load_dossier(conn, dossier_id)
            if not dossier:
                return None
            er.touch_dossier(conn, dossier_id)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT COUNT(*) AS n FROM entity_evidence WHERE dossier_id=%s", (dossier_id,))
                total = cur.fetchone()['n']
                cur.execute("""SELECT id, source, record_id, role, name_as_written, match_tier, bbl, address,
                                      party_address, record_date, details, source_url, hop, via, in_database
                               FROM entity_evidence WHERE dossier_id=%s
                               ORDER BY hop, CASE match_tier WHEN 'strong' THEN 0 WHEN 'exact' THEN 1 ELSE 2 END,
                                        record_date DESC NULLS LAST, id LIMIT %s""", (dossier_id, MAX_ROWS))
                rows = [dict(r) for r in cur.fetchall()]
            conn.rollback()
            return {
                'success': True,
                'dossier': serialize_dossier(dossier),
                'jobs': {k: serialize_job(j) for k, j in er.latest_jobs(conn, dossier_id).items()},
                'steps': {'research': list(er.RESEARCH_STEPS), 'expand': list(er.EXPAND_STEPS)},
                'source_labels': er.SOURCE_LABELS,
                'rows': [serialize_row(r) for r in rows],
                'total_rows': total,
                'properties': group_properties(rows),
                'connections': connections(dossier, rows),
                'expansions': expansions(rows),
                'crm': er.crm_matches(conn, dossier, ctx()),
            }
        payload = with_conn(read)
        if payload is None:
            return jsonify(success=False, error='Research not found'), 404
        return jsonify(payload)

    def mutate(dossier_id, fn):
        if not same_origin_write():
            return jsonify(success=False, error='A same-origin JSON request is required'), 403

        def run(conn):
            dossier = er.load_dossier(conn, dossier_id)
            if not dossier:
                return jsonify(success=False, error='Research not found'), 404
            return fn(conn, dossier)
        try:
            return with_conn(run)
        except er.ResearchError as exc:
            return jsonify(success=False, error=str(exc)), exc.status_code
        except Exception:
            current_app.logger.exception('Entity research mutation failed')
            return jsonify(success=False, error='Entity research is temporarily unavailable'), 503

    @bp.route('/api/entity/<int:dossier_id>/refresh', methods=['POST'])
    @login_required
    def api_refresh(dossier_id):
        def run(conn, dossier):
            job, queued = er.enqueue_job(conn, dossier_id, 'research', user_id(), force=True)
            return jsonify(success=True, job=serialize_job(job), queued=queued)
        return mutate(dossier_id, run)

    @bp.route('/api/entity/<int:dossier_id>/expand', methods=['POST'])
    @login_required
    def api_expand(dossier_id):
        def run(conn, dossier):
            jobs = er.latest_jobs(conn, dossier_id)
            research = jobs.get('research')
            if research and research['status'] in ('queued', 'running'):
                raise er.ResearchError('Wait for the first pass to finish before expanding connections.', 409)
            job, queued = er.enqueue_job(conn, dossier_id, 'expand', user_id(), force=True)
            return jsonify(success=True, job=serialize_job(job), queued=queued)
        return mutate(dossier_id, run)

    @bp.route('/api/entity/<int:dossier_id>/keep', methods=['POST'])
    @login_required
    def api_keep(dossier_id):
        def run(conn, dossier):
            data = request.get_json(silent=True) or {}
            row = er.set_permanent(conn, dossier_id, bool(data.get('permanent', True)), user_id())
            return jsonify(success=True, dossier=serialize_dossier(row))
        return mutate(dossier_id, run)

    @bp.route('/api/entity/<int:dossier_id>/add-property', methods=['POST'])
    @login_required
    def api_add_property(dossier_id):
        def run(conn, dossier):
            data = request.get_json(silent=True) or {}
            bbl = re.sub(r'\D', '', str(data.get('bbl') or ''))
            if not re.fullmatch(r'[1-5]\d{9}', bbl):
                raise er.ResearchError('A 10-digit BBL is required.')
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT 1 FROM entity_evidence WHERE dossier_id=%s AND bbl=%s LIMIT 1", (dossier_id, bbl))
                if not cur.fetchone():
                    raise er.ResearchError('That lot is not part of this research.', 404)
            conn.rollback()
            from property_lookup import auto_add_property
            result = auto_add_property(connect, bbl, background=True)
            if not result.get('success'):
                raise er.ResearchError(result.get('error') or 'Could not add the property.', 422)
            er.mark_in_database(conn, dossier_id)
            er.refresh_summary(conn, dossier_id)
            return jsonify(success=True, bbl=bbl, building_id=result.get('building_id'),
                           already_existed=result.get('already_existed'), url=f'/property/{bbl}')
        return mutate(dossier_id, run)

    @bp.route('/api/entities')
    @login_required
    def api_entities():
        def read(conn):
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""SELECT d.*, (SELECT COUNT(*) FROM entity_evidence e WHERE e.dossier_id=d.id) AS rows
                               FROM entity_dossiers d
                               ORDER BY d.permanent DESC, d.last_viewed_at DESC LIMIT 100""")
                items = [dict(r) for r in cur.fetchall()]
            conn.rollback()
            return items
        items = with_conn(read)
        return jsonify(success=True, dossiers=[dict(serialize_dossier(d), rows=d['rows']) for d in items])

    return bp


# ---------------------------------------------------------------------------
# Serialization and read-model helpers
# ---------------------------------------------------------------------------

def serialize_dossier(d):
    return {
        'id': d['id'], 'display_name': d['display_name'], 'entity_kind': d['entity_kind'],
        'name_key': d['name_key'], 'contexts': d.get('contexts') or [], 'summary': d.get('summary') or {},
        'permanent': bool(d.get('permanent')), 'saved_at': er.iso(d.get('saved_at')),
        'expires_at': er.iso(d.get('expires_at')), 'external_checked_at': er.iso(d.get('external_checked_at')),
        'external_fresh': er.external_is_fresh(d), 'expanded_at': er.iso(d.get('expanded_at')),
        'created_at': er.iso(d.get('created_at')), 'last_viewed_at': er.iso(d.get('last_viewed_at')),
        'retention_days': er.RETENTION_DAYS, 'cache_hours': er.CACHE_HOURS,
    }


def serialize_job(j):
    return {'id': j['id'], 'kind': j['kind'], 'status': j['status'], 'steps': j.get('steps') or {},
            'error': j.get('error'), 'created_at': er.iso(j.get('created_at')),
            'finished_at': er.iso(j.get('finished_at'))}


def serialize_row(r):
    out = dict(r)
    out['record_date'] = er.iso(r.get('record_date'))
    return out


_TIER_RANK = {'strong': 0, 'exact': 1, 'candidate': 2}


def group_properties(rows):
    """One card per lot: best tier, roles, sources, dates, and whether we track it."""
    groups = {}
    for r in rows:
        if not r.get('bbl'):
            continue
        g = groups.setdefault(r['bbl'], {'bbl': r['bbl'], 'address': None, 'tier': 'candidate', 'roles': [],
                                         'sources': [], 'records': 0, 'latest_date': None, 'in_database': False,
                                         'via': [], 'hop': 1, 'names': []})
        if r.get('address') and (not g['address'] or len(r['address']) > len(g['address'])):
            g['address'] = r['address']
        g['records'] += 1
        g['in_database'] = g['in_database'] or bool(r.get('in_database'))
        if r.get('hop'):
            if r.get('via') and r['via'] not in g['via']:
                g['via'].append(r['via'])
        else:
            g['hop'] = 0
            if _TIER_RANK[r['match_tier']] < _TIER_RANK[g['tier']]:
                g['tier'] = r['match_tier']
        label = f"{r['role']} ({er.SOURCE_LABELS.get(r['source'], r['source'])})"
        if label not in g['roles']:
            g['roles'].append(label)
        if r['source'] not in g['sources']:
            g['sources'].append(r['source'])
        if r.get('name_as_written') and r['name_as_written'] not in g['names']:
            g['names'].append(r['name_as_written'])
        d = er.iso(r.get('record_date'))
        if d and (not g['latest_date'] or d > g['latest_date']):
            g['latest_date'] = d
    items = list(groups.values())
    for g in items:
        g['roles'] = g['roles'][:8]
        g['names'] = g['names'][:5]
    items.sort(key=lambda g: (g['hop'], _TIER_RANK[g['tier']], -(g['records']), g['latest_date'] or ''), reverse=False)
    return items


def connections(dossier, rows):
    """Other names on the same records: co-parties, co-registrants, principals, agents."""
    own = rows_for_subject(rows)
    agg = {}
    for r in own:
        details = r.get('details') or {}
        people = details.get('parties') or []
        for p in people:
            name = er.clean_name(p.get('name'))
            if not name or er.entity_key(name) == dossier['name_key']:
                continue
            key = er.entity_key(name)
            item = agg.setdefault(key, {'name': name, 'records': set(), 'relationships': defaultdict(int),
                                        'sources': set(), 'lots': set(), 'is_agent': False})
            item['records'].add(f"{r['source']}:{r['record_id']}")
            item['relationships'][f"{p.get('role') or 'party'} on {er.SOURCE_LABELS.get(r['source'], r['source'])}"] += 1
            item['sources'].add(r['source'])
            if r.get('bbl'):
                item['lots'].add(r['bbl'])
            if p.get('is_agent'):
                item['is_agent'] = True
    out = []
    for key, item in agg.items():
        out.append({'name': item['name'], 'kind': er.classify(item['name']), 'records': len(item['records']),
                    'lots': len(item['lots']), 'sources': sorted(item['sources']), 'is_agent': item['is_agent'],
                    'relationships': [{'label': k, 'count': v} for k, v in
                                      sorted(item['relationships'].items(), key=lambda kv: -kv[1])][:4],
                    'research_url': '/entity/research?' + urlencode({'name': item['name'], 'source': 'connection'})})
    out.sort(key=lambda c: (-c['records'], -c['lots'], c['name']))
    return out[:60]


def rows_for_subject(rows):
    """Direct rows that name the subject; fall back to candidates when nothing is exact."""
    direct = [r for r in rows if not r.get('hop')]
    sure = [r for r in direct if r['match_tier'] != 'candidate']
    return sure or direct


def expansions(rows):
    """Second-hop rows grouped by the connected name they were followed from."""
    groups = {}
    for r in rows:
        if not r.get('hop'):
            continue
        g = groups.setdefault(r.get('via') or 'Connected name', {'via': r.get('via'), 'records': 0, 'lots': set(),
                                                                  'sources': set(), 'kind': er.classify(r.get('via') or '')})
        g['records'] += 1
        g['sources'].add(r['source'])
        if r.get('bbl'):
            g['lots'].add(r['bbl'])
    return [dict(g, lots=len(g['lots']), sources=sorted(g['sources']),
                 research_url='/entity/research?' + urlencode({'name': g['via'] or '', 'source': 'expansion'}))
            for g in groups.values()]
