"""Authenticated prospect work lists and flexible CSV import."""
import json
import secrets
from datetime import date, datetime
from functools import wraps

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request, session, Response
from werkzeug.exceptions import RequestEntityTooLarge

from auth_service import login_required
from crm_routes import _base_template_args
import crm_service
import prospecting_service as service

prospecting_bp = Blueprint('prospecting', __name__, url_prefix='/crm/prospecting')


def _ctx():
    return crm_service.crm_context(g.user)


def _token():
    if 'prospecting_csrf' not in session:
        session['prospecting_csrf'] = secrets.token_urlsafe(32)
    return session['prospecting_csrf']


def _serial(value):
    if isinstance(value, datetime):
        return value.isoformat() + 'Z'
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serial(v) for v in value]
    return value


def api(f):
    @wraps(f)
    @login_required
    def wrapped(*args, **kwargs):
        if request.method != 'GET':
            expected = session.get('prospecting_csrf', '')
            supplied = request.headers.get('X-CSRF-Token', '')
            if not expected or not secrets.compare_digest(expected, supplied):
                return jsonify(error='Your session changed. Reload this page before saving.'), 403
        try:
            return jsonify(success=True, **_serial(f(*args, **kwargs)))
        except LookupError as exc:
            return jsonify(error=str(exc)), 404
        except service.Conflict as exc:
            return jsonify(error=str(exc)), 409
        except PermissionError as exc:
            return jsonify(error=str(exc)), 403
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400
        except RequestEntityTooLarge:
            return jsonify(error='Choose a file up to 10 MB.'), 413
        except Exception:
            current_app.logger.exception('Prospecting request failed')
            return jsonify(error='Could not save or load this list. Please retry.'), 500
    return wrapped


def _data():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('Expected a JSON object.')
    return data


def _upload():
    if request.content_length and request.content_length > service.MAX_BYTES + 128 * 1024:
        raise RequestEntityTooLarge()
    upload = request.files.get('file')
    if not upload:
        raise ValueError('Choose a CSV or TSV file.')
    parsed = service.parse_csv(upload.stream.read(service.MAX_BYTES + 1),
        delimiter=request.form.get('delimiter', 'auto'), has_header=request.form.get('has_header', 'true') == 'true')
    return upload, parsed


@prospecting_bp.route('')
@prospecting_bp.route('/<int:list_id>')
@login_required
def page(list_id=None):
    ctx = _ctx()
    if list_id:
        try:
            with service.transaction() as cur:
                service.get_list(ctx, list_id, cur)
        except LookupError:
            abort(404)
    return render_template('crm/prospecting.html', **_base_template_args(ctx, 'prospecting'),
        prospect_config={'listId': list_id, 'csrf': _token(), 'statuses': service.STATUSES,
                         'fields': service.FIELDS, 'methods': crm_service.METHOD_LABELS,
                         'outcomes': crm_service.OUTCOME_LABELS, 'userId': ctx['user_id'],
                         'isAdmin': ctx['is_admin'],
                         'roster': crm_service.get_team_roster(ctx['team_id']) if ctx['is_admin'] else []})


@prospecting_bp.get('/api/lists')
@api
def lists():
    return {'lists': service.list_lists(_ctx())}


@prospecting_bp.post('/api/preview')
@api
def preview():
    _, parsed = _upload()
    return {**parsed, 'rows': parsed['rows'][:5]}


@prospecting_bp.post('/api/import')
@api
def import_list():
    upload, parsed = _upload()
    list_id = service.create_list(_ctx(), parsed, name=request.form.get('name', ''), filename=upload.filename or 'upload.csv',
        mapping=json.loads(request.form.get('mapping', '{}')), import_key=request.form.get('import_key'),
        assigned_to_id=request.form.get('assigned_to_id'))
    return {'list_id': list_id}


@prospecting_bp.get('/api/lists/<int:list_id>')
@api
def rows(list_id):
    return service.list_rows(_ctx(), list_id, q=request.args.get('q', ''), status=request.args.get('status', ''),
        due=request.args.get('due') == 'true', sort=request.args.get('sort', 'position'), page=int(request.args.get('page', 1)))


@prospecting_bp.patch('/api/lists/<int:list_id>/assignment')
@api
def assignment(list_id):
    data = _data()
    if 'assigned_to_id' not in data:
        raise ValueError('Choose a list owner.')
    return {'listing': service.assign_list(_ctx(), list_id,
        assigned_to_id=data['assigned_to_id'], version=data.get('version'))}


@prospecting_bp.get('/api/rows/<int:row_id>')
@api
def detail(row_id):
    return service.row_detail(_ctx(), row_id)


@prospecting_bp.patch('/api/rows/<int:row_id>')
@api
def update(row_id):
    return {'row': service.update_row(_ctx(), row_id, _data())}


@prospecting_bp.post('/api/rows/<int:row_id>/touches')
@api
def touch(row_id):
    return {'row': service.add_touch(_ctx(), row_id, _data())}


@prospecting_bp.post('/api/rows/<int:row_id>/promote')
@api
def promote(row_id):
    return service.promote(_ctx(), row_id, _data())


@prospecting_bp.get('/<int:list_id>/export.csv')
@login_required
def export(list_id):
    try:
        body = service.export_rows(_ctx(), list_id)
    except LookupError:
        abort(404)
    return Response(body, mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename="prospecting-{list_id}.csv"'})
