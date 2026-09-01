"""Sales CRM blueprint.

Pages live under /crm, JSON endpoints under /crm/api. Everything requires a
login; the Team screen requires the team's CRM admin (the sponsor account or
a global admin). Reps — sponsored members — get the same screens scoped to
their team.
"""

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from functools import wraps

from flask import (
    Blueprint, g, jsonify, redirect, render_template, request, url_for, Response,
)

import crm_service
from auth_service import login_required

crm_bp = Blueprint('crm', __name__, url_prefix='/crm')


# ---------- Template filters (registered app-wide, crm-prefixed names) ----------

@crm_bp.app_template_filter('crm_timeago')
def crm_timeago(value):
    """Compact relative time for naive-UTC timestamps: 'just now' … '3mo ago'."""
    if not value:
        return 'never'
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day)
    delta = datetime.utcnow() - value
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return 'just now'
    if seconds < 3600:
        return f'{seconds // 60}m ago'
    if seconds < 86400:
        return f'{seconds // 3600}h ago'
    days = seconds // 86400
    if days < 30:
        return f'{days}d ago'
    if days < 365:
        return f'{days // 30}mo ago'
    return f'{days // 365}y ago'


@crm_bp.app_template_filter('crm_recency')
def crm_recency(value):
    """CSS class for a last-touch timestamp: green ≤7d, amber ≤30d, red older."""
    if not value:
        return 'rec-never'
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day)
    days = (datetime.utcnow() - value).days
    if days <= 7:
        return 'rec-fresh'
    if days <= 30:
        return 'rec-warm'
    return 'rec-cold'


@crm_bp.app_template_filter('crm_nydt')
def crm_nydt(value):
    """Naive-UTC timestamp rendered in NY local time."""
    if not value:
        return ''
    aware = value.replace(tzinfo=timezone.utc).astimezone(crm_service.NY_TZ)
    return aware.strftime('%b %-d, %Y · %-I:%M %p')


@crm_bp.app_template_filter('crm_date')
def crm_date(value):
    if not value:
        return ''
    return value.strftime('%a, %b %-d')


@crm_bp.app_template_filter('crm_due')
def crm_due(value):
    """Due-date phrasing relative to the NY calendar."""
    if not value:
        return ''
    today = crm_service.ny_today()
    diff = (value - today).days
    if diff == 0:
        return 'Today'
    if diff == 1:
        return 'Tomorrow'
    if diff == -1:
        return 'Yesterday'
    if diff < 0:
        return f'{-diff}d overdue'
    if diff <= 7:
        return value.strftime('%a')
    return value.strftime('%b %-d')


@crm_bp.app_template_filter('crm_phone_digits')
def crm_phone_digits(value):
    return crm_service.normalize_phone_digits(value)


def _ctx():
    return crm_service.crm_context(g.user)


def crm_admin_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        ctx = _ctx()
        if not ctx['is_admin']:
            if request.path.startswith('/crm/api/'):
                return jsonify({'success': False, 'error': 'Admin access required'}), 403
            return redirect(url_for('crm.today'))
        return f(*args, **kwargs)
    return wrapped


def _json():
    return request.get_json(silent=True) or {}


def _int_or_none(value):
    try:
        return int(value) if value not in (None, '', 'null') else None
    except (TypeError, ValueError):
        return None


def _parse_date(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _collision_warning(last_contact):
    """'Sam logged a touch 2h ago' — shown in the Contacted dialog when the
    lead was already touched inside the last 24 hours."""
    if not last_contact or not last_contact.get('created_at'):
        return ''
    if datetime.utcnow() - last_contact['created_at'] >= timedelta(hours=24):
        return ''
    return f"{last_contact['user_name']} logged a touch {crm_timeago(last_contact['created_at'])}"


def _base_template_args(ctx, active_tab):
    return {
        'active_page': 'crm',
        'crm_tab': active_tab,
        'crm_ctx': ctx,
        'stage_labels': crm_service.STAGE_LABELS,
        'stages': crm_service.STAGES,
        'method_labels': crm_service.METHOD_LABELS,
        'outcome_labels': crm_service.OUTCOME_LABELS,
        'today_ny': crm_service.ny_today(),
    }


# ============================================================
# Pages
# ============================================================

@crm_bp.route('/')
@login_required
def today():
    ctx = _ctx()
    queues = crm_service.follow_up_queues(ctx)
    upcoming_week = [f for f in queues['upcoming']
                     if f['due_date'] <= crm_service.ny_today() + timedelta(days=7)]
    return render_template(
        'crm/today.html',
        counters=crm_service.today_counters(ctx),
        overdue=queues['overdue'],
        due_today=queues['due_today'],
        upcoming=upcoming_week[:10],
        attention=crm_service.needs_attention(ctx),
        my_lists=[l for l in crm_service.list_lists(ctx)
                  if l['assigned_to_id'] in (None, ctx['user_id'])
                  or l['owner_id'] == ctx['user_id']][:6],
        feed=crm_service.team_feed(ctx, limit=15),
        **_base_template_args(ctx, 'today'),
    )


@crm_bp.route('/buildings')
@login_required
def buildings():
    ctx = _ctx()
    stage = request.args.get('stage') or None
    filters = {
        'stage': stage if stage in crm_service.STAGES else None,
        'q': (request.args.get('q') or '').strip() or None,
        'borough': request.args.get('borough') or None,
        'starred': request.args.get('starred') == '1',
        'cold': request.args.get('cold') == '1',
        'mine': request.args.get('mine') == '1',
        'sort': request.args.get('sort') or 'recent',
    }
    rows = crm_service.list_buildings(ctx, **filters)
    return render_template(
        'crm/buildings.html',
        buildings=rows,
        counts=crm_service.building_stage_counts(ctx),
        filters=filters,
        roster=crm_service.get_team_roster(ctx['team_id']),
        **_base_template_args(ctx, 'buildings'),
    )


@crm_bp.route('/buildings/<int:building_id>')
@login_required
def building_detail(building_id):
    ctx = _ctx()
    building = crm_service.get_building(ctx, building_id)
    if not building:
        return redirect(url_for('crm.buildings'))
    crm_service.log_view(ctx, 'building', building_id, building['address'])
    return render_template(
        'crm/building_detail.html',
        b=building,
        last_touch=_collision_warning(building.get('last_contact')),
        timeline=crm_service.get_timeline(ctx, building_id=building_id),
        snapshot=crm_service.permit_snapshot(building.get('bbl')),
        my_lists=crm_service.list_lists(ctx),
        roster=crm_service.get_team_roster(ctx['team_id']),
        building_roles=crm_service.BUILDING_CONTACT_ROLES,
        **_base_template_args(ctx, 'buildings'),
    )


@crm_bp.route('/buildings/add', methods=['GET'])
@login_required
def building_add():
    ctx = _ctx()
    bbl = (request.args.get('bbl') or '').strip() or None
    prefill, permit_contacts = None, []
    if bbl:
        existing = crm_service.find_building_by_bbl(ctx, bbl)
        if existing:
            return redirect(url_for('crm.building_detail', building_id=existing, existing=1))
        prefill = crm_service.permit_building_prefill(bbl)
        try:
            # The dashboard's own deduped contact directory is the source of
            # truth for permit contacts; imported lazily to avoid a cycle.
            from app import DatabaseConnection, _fetch_contact_directory
            with DatabaseConnection() as cur:
                permit_contacts = _fetch_contact_directory(cur, bbl=bbl)
        except Exception as e:
            print(f'crm add: permit contact lookup failed for {bbl}: {e}', flush=True)
        if prefill and prefill.get('owner_name'):
            known_names = {str(c.get('name') or '').strip().upper() for c in permit_contacts}
            if prefill['owner_name'].strip().upper() not in known_names:
                permit_contacts.append({
                    'name': prefill['owner_name'], 'phone': None,
                    'role': 'Owner (tax roll)', 'source': 'PLUTO/RPAD owner record',
                })
    return render_template(
        'crm/building_add.html',
        bbl=bbl,
        prefill=prefill,
        permit_contacts=permit_contacts,
        my_lists=crm_service.list_lists(ctx),
        **_base_template_args(ctx, 'buildings'),
    )


@crm_bp.route('/buildings/add', methods=['POST'])
@login_required
def building_add_post():
    ctx = _ctx()
    form = request.form
    address = (form.get('address') or '').strip()
    bbl = (form.get('bbl') or '').strip() or None
    if not address:
        return redirect(url_for('crm.building_add', bbl=bbl or ''))
    building_id, created = crm_service.create_building(
        ctx,
        address=address,
        bbl=bbl,
        borough=(form.get('borough') or '').strip() or None,
        zip_code=(form.get('zip_code') or '').strip() or None,
        neighborhood=(form.get('neighborhood') or '').strip() or None,
        unit_count=_int_or_none(form.get('unit_count')),
        year_built=_int_or_none(form.get('year_built')),
        num_floors=_int_or_none(form.get('num_floors')),
        building_class=(form.get('building_class') or '').strip() or None,
        owner_name=(form.get('owner_name') or '').strip() or None,
        source='permit' if bbl else 'manual',
    )
    if created:
        # Selected permit contacts arrive as indexed hidden fields next to
        # each checked include_contact checkbox.
        for idx in form.getlist('include_contact'):
            name = (form.get(f'c_name_{idx}') or '').strip()
            if not name:
                continue
            role_text = (form.get(f'c_role_{idx}') or '').strip()
            role = 'owner' if 'owner' in role_text.lower() else 'other'
            crm_service.create_contact(
                ctx,
                name=name,
                title=role_text or None,
                source='permit',
                source_detail=(form.get(f'c_src_{idx}') or 'DOB permit contact').strip()[:255],
                building_id=building_id,
                building_role=role,
                phone=(form.get(f'c_phone_{idx}') or '').strip() or None,
            )
        note = (form.get('first_note') or '').strip()
        if note:
            crm_service.add_note(ctx, building_id=building_id, note=note)
        list_id = _int_or_none(form.get('list_id'))
        new_list_name = (form.get('new_list_name') or '').strip()
        if new_list_name:
            list_id = crm_service.create_list(ctx, name=new_list_name)
        if list_id:
            crm_service.add_list_item(ctx, list_id, building_id=building_id)
    return redirect(url_for('crm.building_detail', building_id=building_id,
                            **({} if created else {'existing': 1})))


@crm_bp.route('/contacts')
@login_required
def contacts():
    ctx = _ctx()
    filters = {
        'q': (request.args.get('q') or '').strip() or None,
        'starred': request.args.get('starred') == '1',
        'cold': request.args.get('cold') == '1',
    }
    return render_template(
        'crm/contacts.html',
        contacts=crm_service.list_contacts(ctx, **filters),
        filters=filters,
        **_base_template_args(ctx, 'contacts'),
    )


@crm_bp.route('/contacts/<int:contact_id>')
@login_required
def contact_detail(contact_id):
    ctx = _ctx()
    contact = crm_service.get_contact(ctx, contact_id)
    if not contact:
        return redirect(url_for('crm.contacts'))
    crm_service.log_view(ctx, 'contact', contact_id, contact['name'])
    return render_template(
        'crm/contact_detail.html',
        c=contact,
        last_touch=_collision_warning(contact.get('last_contact')),
        timeline=crm_service.get_timeline(ctx, contact_id=contact_id),
        my_lists=crm_service.list_lists(ctx),
        **_base_template_args(ctx, 'contacts'),
    )


@crm_bp.route('/lists')
@login_required
def lists():
    ctx = _ctx()
    return render_template(
        'crm/lists.html',
        lists=crm_service.list_lists(ctx),
        saved_filters=crm_service.list_saved_filters(ctx),
        roster=crm_service.get_team_roster(ctx['team_id']),
        **_base_template_args(ctx, 'lists'),
    )


@crm_bp.route('/lists/<int:list_id>')
@login_required
def list_detail(list_id):
    ctx = _ctx()
    lst = crm_service.get_list(ctx, list_id)
    if not lst:
        return redirect(url_for('crm.lists'))
    crm_service.log_view(ctx, 'list', list_id, lst['name'])
    return render_template(
        'crm/list_detail.html',
        l=lst,
        roster=crm_service.get_team_roster(ctx['team_id']),
        **_base_template_args(ctx, 'lists'),
    )


@crm_bp.route('/followups')
@login_required
def followups():
    ctx = _ctx()
    scope_team = request.args.get('scope') == 'team' and ctx['is_admin']
    queues = crm_service.follow_up_queues(ctx, whole_team=scope_team, include_done=True)
    return render_template(
        'crm/followups.html',
        queues=queues,
        scope_team=scope_team,
        **_base_template_args(ctx, 'followups'),
    )


@crm_bp.route('/starred')
@login_required
def starred():
    ctx = _ctx()
    roster = crm_service.get_team_roster(ctx['team_id']) if ctx['is_admin'] else []
    view_user = _int_or_none(request.args.get('user')) if ctx['is_admin'] else None
    everyone = request.args.get('user') == 'all' and ctx['is_admin']
    rows = crm_service.starred_overview(
        ctx, everyone=everyone, for_user_id=view_user)
    return render_template(
        'crm/starred.html',
        stars=rows,
        roster=roster,
        view_user=view_user,
        everyone=everyone,
        **_base_template_args(ctx, 'starred'),
    )


@crm_bp.route('/team')
@login_required
@crm_admin_required
def team():
    ctx = _ctx()
    feed_user = _int_or_none(request.args.get('rep'))
    return render_template(
        'crm/team.html',
        performance=crm_service.rep_performance(ctx),
        feed=crm_service.team_feed(ctx, user_id=feed_user, limit=60),
        feed_user=feed_user,
        views=crm_service.view_log(ctx, user_id=feed_user, limit=120),
        roster=crm_service.get_team_roster(ctx['team_id']),
        **_base_template_args(ctx, 'team'),
    )


# ============================================================
# JSON APIs — the Contacted button and friends
# ============================================================

@crm_bp.route('/api/contacted', methods=['POST'])
@login_required
def api_contacted():
    ctx = _ctx()
    data = _json()
    building_id = _int_or_none(data.get('building_id'))
    contact_id = _int_or_none(data.get('contact_id'))
    if not crm_service.entity_in_team(ctx, building_id=building_id, contact_id=contact_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    activity_id = crm_service.log_contacted(
        ctx,
        building_id=building_id,
        contact_id=contact_id,
        method=data.get('method') or 'call',
        outcome=data.get('outcome') or None,
        note=data.get('note') or None,
        phone_digits=crm_service.normalize_phone_digits(data.get('phone')) or None,
    )
    return jsonify({'success': True, 'activity_id': activity_id})


@crm_bp.route('/api/visit', methods=['POST'])
@login_required
def api_visit():
    ctx = _ctx()
    data = _json()
    building_id = _int_or_none(data.get('building_id'))
    if not crm_service.entity_in_team(ctx, building_id=building_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    crm_service.log_visit(
        ctx, building_id=building_id, note=data.get('note') or None,
        visited_on=_parse_date(data.get('visited_on')),
    )
    return jsonify({'success': True})


@crm_bp.route('/api/note', methods=['POST'])
@login_required
def api_note():
    ctx = _ctx()
    data = _json()
    building_id = _int_or_none(data.get('building_id'))
    contact_id = _int_or_none(data.get('contact_id'))
    if not crm_service.entity_in_team(ctx, building_id=building_id, contact_id=contact_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    if not (data.get('note') or '').strip():
        return jsonify({'success': False, 'error': 'Write the note first'}), 400
    note_id = crm_service.add_note(
        ctx, building_id=building_id, contact_id=contact_id, note=data['note'])
    return jsonify({'success': True, 'activity_id': note_id})


@crm_bp.route('/api/activity/<int:activity_id>/pin', methods=['POST'])
@login_required
def api_pin(activity_id):
    pinned = crm_service.toggle_pin(_ctx(), activity_id)
    return jsonify({'success': True, 'pinned': pinned})


@crm_bp.route('/api/activity/<int:activity_id>/delete', methods=['POST'])
@login_required
def api_activity_delete(activity_id):
    ok, error = crm_service.delete_activity(_ctx(), activity_id)
    status = 200 if ok else 403
    return jsonify({'success': ok, 'error': error}), status


@crm_bp.route('/api/star', methods=['POST'])
@login_required
def api_star():
    ctx = _ctx()
    data = _json()
    building_id = _int_or_none(data.get('building_id'))
    contact_id = _int_or_none(data.get('contact_id'))
    if not crm_service.entity_in_team(ctx, building_id=building_id, contact_id=contact_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    starred = crm_service.toggle_star(ctx, building_id=building_id, contact_id=contact_id)
    return jsonify({'success': True, 'starred': starred})


@crm_bp.route('/api/stage', methods=['POST'])
@login_required
def api_stage():
    ctx = _ctx()
    data = _json()
    building_id = _int_or_none(data.get('building_id'))
    stage = data.get('stage')
    if stage not in crm_service.STAGES:
        return jsonify({'success': False, 'error': 'Invalid stage'}), 400
    if not crm_service.entity_in_team(ctx, building_id=building_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    crm_service.update_building_stage(ctx, building_id, stage)
    return jsonify({'success': True})


@crm_bp.route('/api/assign', methods=['POST'])
@login_required
def api_assign():
    ctx = _ctx()
    data = _json()
    building_id = _int_or_none(data.get('building_id'))
    if not crm_service.entity_in_team(ctx, building_id=building_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    crm_service.assign_building(ctx, building_id, _int_or_none(data.get('user_id')))
    return jsonify({'success': True})


@crm_bp.route('/api/followup', methods=['POST'])
@login_required
def api_followup():
    ctx = _ctx()
    data = _json()
    building_id = _int_or_none(data.get('building_id'))
    contact_id = _int_or_none(data.get('contact_id'))
    if (building_id or contact_id) and not crm_service.entity_in_team(
            ctx, building_id=building_id, contact_id=contact_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    due = _parse_date(data.get('due_date'))
    if not due and data.get('days') is not None:
        days = _int_or_none(data.get('days'))
        if days is not None:
            due = crm_service.ny_today() + timedelta(days=max(0, min(days, 365)))
    if not due:
        return jsonify({'success': False, 'error': 'Pick a date'}), 400
    follow_up_id = crm_service.create_follow_up(
        ctx,
        title=data.get('title') or 'Follow up',
        due_date=due,
        note=data.get('note') or None,
        building_id=building_id,
        contact_id=contact_id,
        assigned_to_id=_int_or_none(data.get('assigned_to_id')),
    )
    return jsonify({'success': True, 'follow_up_id': follow_up_id})


@crm_bp.route('/api/followup/<int:follow_up_id>/<action>', methods=['POST'])
@login_required
def api_followup_action(follow_up_id, action):
    ctx = _ctx()
    if action == 'done':
        crm_service.resolve_follow_up(ctx, follow_up_id, 'done')
    elif action == 'skip':
        crm_service.resolve_follow_up(ctx, follow_up_id, 'skipped')
    elif action == 'reopen':
        crm_service.resolve_follow_up(ctx, follow_up_id, 'open')
    elif action == 'snooze':
        crm_service.snooze_follow_up(ctx, follow_up_id, _int_or_none(_json().get('days')) or 1)
    else:
        return jsonify({'success': False, 'error': 'Unknown action'}), 400
    return jsonify({'success': True})


@crm_bp.route('/api/contact', methods=['POST'])
@login_required
def api_contact_create():
    ctx = _ctx()
    data = _json()
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Name is required'}), 400
    building_id = _int_or_none(data.get('building_id'))
    if building_id and not crm_service.entity_in_team(ctx, building_id=building_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    digits = crm_service.normalize_phone_digits(data.get('phone'))
    duplicates = crm_service.find_contacts_by_digits(ctx, digits) if digits else []
    if duplicates and not data.get('force'):
        return jsonify({
            'success': False, 'duplicate': True,
            'matches': duplicates,
            'error': 'That number is already on ' + ', '.join(d['name'] for d in duplicates[:3]),
        }), 409
    contact_id = crm_service.create_contact(
        ctx,
        name=name,
        title=(data.get('title') or '').strip() or None,
        company=(data.get('company') or '').strip() or None,
        email=(data.get('email') or '').strip() or None,
        source='rep_found' if data.get('source_detail') else 'manual',
        source_detail=(data.get('source_detail') or '').strip() or None,
        building_id=building_id,
        building_role=data.get('building_role') or 'other',
        phone=data.get('phone') or None,
        phone_label=(data.get('phone_label') or '').strip() or None,
    )
    return jsonify({'success': True, 'contact_id': contact_id})


@crm_bp.route('/api/phone', methods=['POST'])
@login_required
def api_phone_add():
    ctx = _ctx()
    data = _json()
    contact_id = _int_or_none(data.get('contact_id'))
    if not crm_service.entity_in_team(ctx, contact_id=contact_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    digits = crm_service.normalize_phone_digits(data.get('number'))
    if not digits:
        return jsonify({'success': False, 'error': 'Enter a phone number'}), 400
    duplicates = [d for d in crm_service.find_contacts_by_digits(ctx, digits)
                  if d['id'] != contact_id]
    if duplicates and not data.get('force'):
        return jsonify({
            'success': False, 'duplicate': True, 'matches': duplicates,
            'error': 'That number is already on ' + ', '.join(d['name'] for d in duplicates[:3]),
        }), 409
    phone_id = crm_service.add_phone(
        ctx, contact_id,
        number=data.get('number'),
        label=(data.get('label') or '').strip() or None,
        source='rep_found',
        source_detail=(data.get('source_detail') or '').strip() or None,
        make_primary=bool(data.get('make_primary')),
    )
    return jsonify({'success': True, 'phone_id': phone_id})


@crm_bp.route('/api/phone/<int:phone_id>/status', methods=['POST'])
@login_required
def api_phone_status(phone_id):
    status = _json().get('status')
    if status not in ('good', 'bad', 'do_not_call'):
        return jsonify({'success': False, 'error': 'Invalid status'}), 400
    crm_service.set_phone_status(_ctx(), phone_id, status)
    return jsonify({'success': True})


@crm_bp.route('/api/dnc', methods=['POST'])
@login_required
def api_dnc():
    ctx = _ctx()
    data = _json()
    contact_id = _int_or_none(data.get('contact_id'))
    if not crm_service.entity_in_team(ctx, contact_id=contact_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    crm_service.set_do_not_contact(ctx, contact_id, bool(data.get('value')))
    return jsonify({'success': True})


@crm_bp.route('/api/list', methods=['POST'])
@login_required
def api_list_create():
    ctx = _ctx()
    data = _json()
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Name the list'}), 400
    list_id = crm_service.create_list(
        ctx, name=name,
        description=(data.get('description') or '').strip() or None,
        assigned_to_id=_int_or_none(data.get('assigned_to_id')),
    )
    return jsonify({'success': True, 'list_id': list_id})


@crm_bp.route('/api/list/<int:list_id>/update', methods=['POST'])
@login_required
def api_list_update(list_id):
    data = _json()
    kwargs = {}
    if 'name' in data:
        kwargs['name'] = data['name']
    if 'description' in data:
        kwargs['description'] = data['description']
    if 'assigned_to_id' in data:
        kwargs['assigned_to_id'] = _int_or_none(data['assigned_to_id'])
    crm_service.update_list(_ctx(), list_id, **kwargs)
    return jsonify({'success': True})


@crm_bp.route('/api/list/<int:list_id>/delete', methods=['POST'])
@login_required
def api_list_delete(list_id):
    crm_service.delete_list(_ctx(), list_id)
    return jsonify({'success': True})


@crm_bp.route('/api/list-item', methods=['POST'])
@login_required
def api_list_item_add():
    ctx = _ctx()
    data = _json()
    building_id = _int_or_none(data.get('building_id'))
    contact_id = _int_or_none(data.get('contact_id'))
    list_id = _int_or_none(data.get('list_id'))
    if not list_id:
        new_name = (data.get('new_list_name') or '').strip()
        if not new_name:
            return jsonify({'success': False, 'error': 'Pick or name a list'}), 400
        list_id = crm_service.create_list(ctx, name=new_name)
    if not crm_service.entity_in_team(ctx, building_id=building_id, contact_id=contact_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    crm_service.add_list_item(ctx, list_id, building_id=building_id,
                              contact_id=contact_id, note=data.get('note'))
    return jsonify({'success': True, 'list_id': list_id})


@crm_bp.route('/api/list-item/<int:item_id>/remove', methods=['POST'])
@login_required
def api_list_item_remove(item_id):
    crm_service.remove_list_item(_ctx(), item_id)
    return jsonify({'success': True})


@crm_bp.route('/api/saved-filter', methods=['POST'])
@login_required
def api_saved_filter():
    ctx = _ctx()
    data = _json()
    name = (data.get('name') or '').strip()
    querystring = (data.get('querystring') or '').strip()
    if not name or not querystring:
        return jsonify({'success': False, 'error': 'Name and filters are required'}), 400
    filter_id = crm_service.save_filter(ctx, name=name, querystring=querystring)
    return jsonify({'success': True, 'filter_id': filter_id})


@crm_bp.route('/api/saved-filter/<int:filter_id>/delete', methods=['POST'])
@login_required
def api_saved_filter_delete(filter_id):
    crm_service.delete_saved_filter(_ctx(), filter_id)
    return jsonify({'success': True})


@crm_bp.route('/api/building/<int:building_id>/delete', methods=['POST'])
@login_required
@crm_admin_required
def api_building_delete(building_id):
    ctx = _ctx()
    if not crm_service.entity_in_team(ctx, building_id=building_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    conn = crm_service.get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM crm_buildings WHERE id = %s", (building_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
    return jsonify({'success': True})


@crm_bp.route('/api/lists')
@login_required
def api_lists():
    """Lightweight list options for pickers on the permit-side pages."""
    lists = crm_service.list_lists(_ctx())
    return jsonify({'success': True,
                    'lists': [{'id': l['id'], 'name': l['name']} for l in lists]})


def _import_permit_contacts(ctx, building_id, bbl, max_contacts=6):
    """Bulk-import the permit contacts that have phones for one building.

    A number already known to the team links the existing contact instead of
    creating a duplicate. Phoneless rows are skipped here — the single-add
    form is where those can be hand-picked.
    """
    try:
        from app import DatabaseConnection, _fetch_contact_directory
        with DatabaseConnection() as cur:
            directory = _fetch_contact_directory(cur, bbl=bbl)
    except Exception as e:
        print(f'crm bulk-add: contact lookup failed for {bbl}: {e}', flush=True)
        return 0
    imported = 0
    for pc in directory:
        if imported >= max_contacts:
            break
        name = (pc.get('name') or '').strip()
        phone = pc.get('phone')
        if not name or not phone:
            continue
        digits = crm_service.normalize_phone_digits(phone)
        if not digits:
            continue
        role_text = (pc.get('role') or '').strip()
        role = 'owner' if 'owner' in role_text.lower() else 'other'
        try:
            matches = crm_service.find_contacts_by_digits(ctx, digits)
            if matches:
                crm_service.link_contact_to_building(ctx, matches[0]['id'], building_id, role)
            else:
                crm_service.create_contact(
                    ctx, name=name, title=role_text or None, source='permit',
                    source_detail=(pc.get('source') or 'DOB permit contact')[:255],
                    building_id=building_id, building_role=role, phone=phone)
            imported += 1
        except Exception as e:
            print(f'crm bulk-add: contact import failed for {bbl}/{name}: {e}', flush=True)
    return imported


@crm_bp.route('/api/bulk-add', methods=['POST'])
@login_required
def api_bulk_add():
    """Add a batch of BBLs from the Properties grid. Already-tracked
    buildings count as existing (and still join the chosen list)."""
    ctx = _ctx()
    data = _json()
    bbls = [str(b).strip() for b in (data.get('bbls') or []) if b][:50]
    if not bbls:
        return jsonify({'success': False, 'error': 'No buildings selected'}), 400
    with_contacts = bool(data.get('with_contacts', True))
    list_id = _int_or_none(data.get('list_id'))
    new_list_name = (data.get('new_list_name') or '').strip()
    if new_list_name:
        list_id = crm_service.create_list(ctx, name=new_list_name)
    added, existing, failed = 0, 0, 0
    in_crm = {}
    for bbl in bbls:
        try:
            building_id = crm_service.find_building_by_bbl(ctx, bbl)
            if building_id:
                existing += 1
            else:
                prefill = crm_service.permit_building_prefill(bbl)
                if not prefill:
                    failed += 1
                    continue
                building_id, created = crm_service.create_building(
                    ctx,
                    address=prefill['address'],
                    bbl=bbl,
                    borough=prefill.get('borough'),
                    zip_code=prefill.get('zip_code'),
                    unit_count=prefill.get('unit_count'),
                    year_built=prefill.get('year_built'),
                    num_floors=prefill.get('num_floors'),
                    building_class=prefill.get('building_class'),
                    owner_name=prefill.get('owner_name'),
                    source='permit',
                )
                if created:
                    added += 1
                    if with_contacts:
                        _import_permit_contacts(ctx, building_id, bbl)
                else:
                    existing += 1
            in_crm[bbl] = building_id
            if list_id:
                crm_service.add_list_item(ctx, list_id, building_id=building_id)
        except Exception as e:
            print(f'crm bulk-add failed for {bbl}: {e}', flush=True)
            failed += 1
    return jsonify({'success': True, 'added': added, 'existing': existing,
                    'failed': failed, 'in_crm': in_crm, 'list_id': list_id})


@crm_bp.route('/api/bbl-status', methods=['POST'])
@login_required
def api_bbl_status():
    """For the permit dashboard: which of these BBLs are already in the CRM."""
    ctx = _ctx()
    bbls = [str(b) for b in (_json().get('bbls') or []) if b][:500]
    if not bbls:
        return jsonify({'success': True, 'in_crm': {}})
    conn = crm_service.get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT bbl, id FROM crm_buildings
               WHERE bbl = ANY(%s) AND (team_id = %s OR team_id IS NULL)""",
            (bbls, ctx['team_id']),
        )
        mapping = {r['bbl']: r['id'] for r in cur.fetchall()}
    finally:
        cur.close()
        conn.close()
    return jsonify({'success': True, 'in_crm': mapping})


# ============================================================
# CSV exports (admin)
# ============================================================

def _csv_response(rows, fieldnames, filename):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ('' if v is None else v) for k, v in row.items()})
    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@crm_bp.route('/api/export/buildings.csv')
@login_required
@crm_admin_required
def export_buildings():
    rows = crm_service.export_buildings_rows(_ctx())
    fields = ['bbl', 'address', 'borough', 'zip_code', 'stage', 'source', 'owner_name',
              'unit_count', 'year_built', 'contact_count', 'last_contacted_at',
              'last_visited_at', 'assigned_to', 'created_at']
    return _csv_response(rows, fields, 'crm-buildings.csv')


@crm_bp.route('/api/export/activity.csv')
@login_required
@crm_admin_required
def export_activity():
    rows = crm_service.export_activity_rows(_ctx())
    fields = ['created_at', 'type', 'method', 'outcome', 'rep', 'building', 'contact', 'note']
    return _csv_response(rows, fields, 'crm-activity.csv')
