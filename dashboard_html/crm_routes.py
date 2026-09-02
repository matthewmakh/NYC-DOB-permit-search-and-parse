"""Sales CRM blueprint.

Pages live under /crm, JSON endpoints under /crm/api, and HTML fragments for
in-place refresh under /crm/partials. Everything requires a login; the Team
screen requires the team's CRM admin (the sponsor account or a global admin).
Reps — sponsored members — get the same screens scoped to their team.
"""

import csv
import io
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


def _to_ny(value):
    return value.replace(tzinfo=timezone.utc).astimezone(crm_service.NY_TZ)


@crm_bp.app_template_filter('crm_nydt')
def crm_nydt(value):
    """Naive-UTC timestamp rendered in NY local time."""
    if not value:
        return ''
    return _to_ny(value).strftime('%b %-d, %Y · %-I:%M %p')


@crm_bp.app_template_filter('crm_nytime')
def crm_nytime(value):
    if not value:
        return ''
    return _to_ny(value).strftime('%-I:%M %p')


@crm_bp.app_template_filter('crm_nyday')
def crm_nyday(value):
    """Day bucket label for timeline grouping: Today / Yesterday / Mon, Aug 4."""
    if not value:
        return ''
    day = _to_ny(value).date()
    today = crm_service.ny_today()
    if day == today:
        return 'Today'
    if day == today - timedelta(days=1):
        return 'Yesterday'
    if (today - day).days < 7:
        return day.strftime('%A')
    if day.year == today.year:
        return day.strftime('%a, %b %-d')
    return day.strftime('%b %-d, %Y')


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


@crm_bp.app_template_filter('crm_tel')
def crm_tel(phone):
    """tel: URI for a phone row (dict) or a raw string, extension included."""
    if isinstance(phone, dict):
        return crm_service.tel_href(phone.get('digits'), phone.get('extension'))
    digits = crm_service.normalize_phone_digits(phone)
    _, extension = crm_service.split_phone_extension(phone)
    return crm_service.tel_href(digits, extension)


@crm_bp.app_template_filter('crm_phone_text')
def crm_phone_text(phone):
    """Human-readable number: (212) 555-0100 ext. 204."""
    if isinstance(phone, dict):
        return crm_service.format_phone(phone.get('digits'), phone.get('extension'))
    digits = crm_service.normalize_phone_digits(phone)
    _, extension = crm_service.split_phone_extension(phone)
    return crm_service.format_phone(digits, extension)


@crm_bp.app_template_filter('crm_initials')
def crm_initials(value):
    parts = [p for p in str(value or '').replace(',', ' ').split() if p]
    if not parts:
        return '?'
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


@crm_bp.app_template_filter('crm_hue')
def crm_hue(value):
    """Deterministic hue (0-359) from a name — Contacts.app-style avatars."""
    total = 0
    for ch in str(value or ''):
        total = (total * 31 + ord(ch)) % 100003
    return total % 360


@crm_bp.app_template_filter('crm_compact')
def crm_compact(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return value
    if n >= 1_000_000:
        return f'{n / 1_000_000:.1f}M'
    if n >= 10_000:
        return f'{n / 1000:.0f}K'
    if n >= 1000:
        return f'{n / 1000:.1f}K'
    return f'{int(n)}'


# ---------- Request helpers ----------

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
        'crm_user_name': crm_service.display_name_for(ctx['user_id']) or ctx['email'],
        'due_count': crm_service.due_count(ctx),
        'stage_labels': crm_service.STAGE_LABELS,
        'stages': crm_service.STAGES,
        'method_labels': crm_service.METHOD_LABELS,
        'outcome_labels': crm_service.OUTCOME_LABELS,
        'building_roles': crm_service.BUILDING_CONTACT_ROLES,
        'today_ny': crm_service.ny_today(),
        'ny_hour': crm_service.ny_now().hour,
    }


def _partial_args(ctx):
    """Minimal context for fragment renders (no shell chrome needed)."""
    return {
        'crm_ctx': ctx,
        'stage_labels': crm_service.STAGE_LABELS,
        'stages': crm_service.STAGES,
        'method_labels': crm_service.METHOD_LABELS,
        'outcome_labels': crm_service.OUTCOME_LABELS,
        'building_roles': crm_service.BUILDING_CONTACT_ROLES,
        'today_ny': crm_service.ny_today(),
    }


# ============================================================
# Today
# ============================================================

def _today_context(ctx):
    queues = crm_service.follow_up_queues(ctx)
    upcoming_week = [f for f in queues['upcoming']
                     if f['due_date'] <= crm_service.ny_today() + timedelta(days=7)]
    lists = crm_service.list_lists(ctx)
    return {
        'counters': crm_service.today_counters(ctx),
        'overdue': queues['overdue'],
        'due_today': queues['due_today'],
        'upcoming': upcoming_week[:10],
        'attention': crm_service.needs_attention(ctx),
        'my_lists': [l for l in lists
                     if l['assigned_to_id'] in (None, ctx['user_id'])
                     or l['owner_id'] == ctx['user_id']][:6],
        'feed': crm_service.team_feed(ctx, limit=15),
        'spark': crm_service.touches_per_day(ctx, days=14, user_id=ctx['user_id']),
    }


@crm_bp.route('/')
@login_required
def today():
    ctx = _ctx()
    return render_template('crm/today.html', **_today_context(ctx),
                           **_base_template_args(ctx, 'today'))


@crm_bp.route('/partials/today')
@login_required
def partial_today():
    ctx = _ctx()
    return render_template('crm/partials/today_body.html', **_today_context(ctx),
                           **_partial_args(ctx))


# ============================================================
# Focus mode — one lead at a time
# ============================================================

@crm_bp.route('/focus')
@login_required
def focus():
    ctx = _ctx()
    source = request.args.get('source') or 'today'
    list_id = _int_or_none(request.args.get('list'))
    queue = crm_service.focus_queue(ctx, source=source, list_id=list_id)
    title = {'today': "Today's calls", 'attention': 'Needs attention',
             'cold': 'Cold buildings'}.get(source, 'Work list')
    if source == 'list' and list_id:
        lst = crm_service.get_list(ctx, list_id)
        title = lst['name'] if lst else 'Work list'
    return render_template('crm/focus.html', queue=queue, source=source, list_id=list_id,
                           focus_title=title, **_base_template_args(ctx, 'focus'))


@crm_bp.route('/partials/focus/<kind>/<int:entity_id>')
@login_required
def partial_focus_card(kind, entity_id):
    ctx = _ctx()
    if kind == 'building':
        building = crm_service.get_building(ctx, entity_id)
        if not building:
            return 'Not found', 404
        crm_service.log_view(ctx, 'building', entity_id, building['address'])
        return render_template(
            'crm/partials/focus_card.html', kind='building', b=building, c=None,
            sv=crm_service.building_streetview(building.get('bbl'), building['address'], building.get('borough')),
            timeline=crm_service.get_timeline(ctx, building_id=entity_id, limit=5),
            last_touch=_collision_warning(building.get('last_contact')),
            **_partial_args(ctx))
    contact = crm_service.get_contact(ctx, entity_id)
    if not contact:
        return 'Not found', 404
    crm_service.log_view(ctx, 'contact', entity_id, contact['name'])
    return render_template(
        'crm/partials/focus_card.html', kind='contact', b=None, c=contact, sv=None,
        timeline=crm_service.get_timeline(ctx, contact_id=entity_id, limit=5),
        last_touch=_collision_warning(contact.get('last_contact')),
        **_partial_args(ctx))


# ============================================================
# Buildings
# ============================================================

def _building_filters():
    stage = request.args.get('stage') or None
    return {
        'stage': stage if stage in crm_service.STAGES else None,
        'q': (request.args.get('q') or '').strip() or None,
        'borough': request.args.get('borough') or None,
        'starred': request.args.get('starred') == '1',
        'cold': request.args.get('cold') == '1',
        'mine': request.args.get('mine') == '1',
        'sort': request.args.get('sort') or 'recent',
    }


@crm_bp.route('/buildings')
@login_required
def buildings():
    ctx = _ctx()
    filters = _building_filters()
    view = request.args.get('view') or 'cards'
    if view not in ('cards', 'board', 'table'):
        view = 'cards'
    query = dict(filters)
    if view == 'board':
        query['stage'] = None  # the board shows every column
    rows = crm_service.list_buildings(ctx, limit=400 if view != 'cards' else 200, **query)
    board = None
    if view == 'board':
        board = {s: [] for s in crm_service.STAGES}
        for b in rows:
            board.setdefault(b['stage'], []).append(b)
    return render_template(
        'crm/buildings.html',
        buildings=rows,
        board=board,
        view=view,
        counts=crm_service.building_stage_counts(ctx),
        filters=filters,
        roster=crm_service.get_team_roster(ctx['team_id']),
        my_lists=crm_service.list_lists(ctx),
        **_base_template_args(ctx, 'buildings'),
    )


def _building_detail_context(ctx, building):
    return {
        'b': building,
        'last_touch': _collision_warning(building.get('last_contact')),
        'timeline': crm_service.get_timeline(ctx, building_id=building['id']),
        'snapshot': crm_service.permit_snapshot(building.get('bbl')),
        'sv': crm_service.building_streetview(building.get('bbl'), building['address'], building.get('borough')),
        'my_lists': crm_service.list_lists(ctx),
        'roster': crm_service.get_team_roster(ctx['team_id']),
    }


@crm_bp.route('/buildings/<int:building_id>')
@login_required
def building_detail(building_id):
    ctx = _ctx()
    building = crm_service.get_building(ctx, building_id)
    if not building:
        return redirect(url_for('crm.buildings'))
    crm_service.log_view(ctx, 'building', building_id, building['address'])
    return render_template('crm/building_detail.html',
                           **_building_detail_context(ctx, building),
                           **_base_template_args(ctx, 'buildings'))


@crm_bp.route('/partials/building/<int:building_id>/<section>')
@login_required
def partial_building(building_id, section):
    ctx = _ctx()
    building = crm_service.get_building(ctx, building_id)
    if not building:
        return 'Not found', 404
    names = {'header': 'building_header', 'timeline': 'building_timeline',
             'people': 'building_people', 'rail': 'building_rail'}
    if section not in names:
        return 'Unknown section', 404
    return render_template(f'crm/partials/{names[section]}.html',
                           **_building_detail_context(ctx, building), **_partial_args(ctx))


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
        bbl=bbl, prefill=prefill, permit_contacts=permit_contacts,
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


# ============================================================
# Contacts
# ============================================================

@crm_bp.route('/contacts')
@login_required
def contacts():
    ctx = _ctx()
    filters = {
        'q': (request.args.get('q') or '').strip() or None,
        'starred': request.args.get('starred') == '1',
        'cold': request.args.get('cold') == '1',
    }
    rows = crm_service.list_contacts(ctx, limit=400, **filters)
    return render_template(
        'crm/contacts.html',
        contacts=rows,
        groups=crm_service.contacts_alpha_groups(rows),
        duplicates=crm_service.find_duplicate_contacts(ctx, limit=8),
        filters=filters,
        **_base_template_args(ctx, 'contacts'),
    )


def _contact_detail_context(ctx, contact):
    return {
        'c': contact,
        'last_touch': _collision_warning(contact.get('last_contact')),
        'timeline': crm_service.get_timeline(ctx, contact_id=contact['id']),
        'my_lists': crm_service.list_lists(ctx),
        'roster': crm_service.get_team_roster(ctx['team_id']),
    }


@crm_bp.route('/contacts/<int:contact_id>')
@login_required
def contact_detail(contact_id):
    ctx = _ctx()
    contact = crm_service.get_contact(ctx, contact_id)
    if not contact:
        return redirect(url_for('crm.contacts'))
    crm_service.log_view(ctx, 'contact', contact_id, contact['name'])
    return render_template('crm/contact_detail.html',
                           **_contact_detail_context(ctx, contact),
                           **_base_template_args(ctx, 'contacts'))


@crm_bp.route('/partials/contact/<int:contact_id>/<section>')
@login_required
def partial_contact(contact_id, section):
    ctx = _ctx()
    contact = crm_service.get_contact(ctx, contact_id)
    if not contact:
        return 'Not found', 404
    names = {'header': 'contact_header', 'timeline': 'contact_timeline', 'rail': 'contact_rail'}
    if section not in names:
        return 'Unknown section', 404
    return render_template(f'crm/partials/{names[section]}.html',
                           **_contact_detail_context(ctx, contact), **_partial_args(ctx))


# ============================================================
# Lists, follow-ups, starred, team
# ============================================================

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
        roster=crm_service.get_team_roster(ctx['team_id']),
        **_base_template_args(ctx, 'followups'),
    )


@crm_bp.route('/starred')
@login_required
def starred():
    ctx = _ctx()
    roster = crm_service.get_team_roster(ctx['team_id']) if ctx['is_admin'] else []
    view_user = _int_or_none(request.args.get('user')) if ctx['is_admin'] else None
    everyone = request.args.get('user') == 'all' and ctx['is_admin']
    rows = crm_service.starred_overview(ctx, everyone=everyone, for_user_id=view_user)
    return render_template(
        'crm/starred.html',
        stars=rows, roster=roster, view_user=view_user, everyone=everyone,
        **_base_template_args(ctx, 'starred'),
    )


@crm_bp.route('/team')
@login_required
@crm_admin_required
def team():
    ctx = _ctx()
    feed_user = _int_or_none(request.args.get('rep'))
    performance = crm_service.rep_performance(ctx)
    return render_template(
        'crm/team.html',
        performance=performance,
        leaderboard=sorted(performance, key=lambda r: -r['contacted_7d']),
        touches=crm_service.touches_per_day(ctx, days=14),
        outcomes=crm_service.outcome_mix(ctx, days=30),
        funnel=crm_service.stage_funnel(ctx),
        feed=crm_service.team_feed(ctx, user_id=feed_user, limit=60),
        feed_user=feed_user,
        views=crm_service.view_log(ctx, user_id=feed_user, limit=120),
        roster=crm_service.get_team_roster(ctx['team_id']),
        **_base_template_args(ctx, 'team'),
    )


# ============================================================
# JSON APIs — the Contacted button and friends
# ============================================================

@crm_bp.route('/api/search')
@login_required
def api_search():
    results = crm_service.global_search(_ctx(), request.args.get('q', ''))
    for key in results:
        for row in results[key]:
            for k, v in list(row.items()):
                if isinstance(v, (datetime, date)):
                    row[k] = crm_timeago(v)
    return jsonify({'success': True, **results})


@crm_bp.route('/api/focus-queue')
@login_required
def api_focus_queue():
    ctx = _ctx()
    queue = crm_service.focus_queue(ctx, source=request.args.get('source') or 'today',
                                    list_id=_int_or_none(request.args.get('list')))
    return jsonify({'success': True, 'queue': queue})


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
    completed = None
    follow_up_id = _int_or_none(data.get('complete_followup_id'))
    if follow_up_id:
        crm_service.resolve_follow_up(ctx, follow_up_id, 'done')
        completed = follow_up_id
    return jsonify({'success': True, 'activity_id': activity_id, 'completed_followup': completed})


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
    return jsonify({'success': ok, 'error': error}), (200 if ok else 403)


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
    return jsonify({'success': True, 'stage': stage, 'label': crm_service.STAGE_LABELS[stage]})


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


@crm_bp.route('/api/building/<int:building_id>/update', methods=['POST'])
@login_required
def api_building_update(building_id):
    ctx = _ctx()
    if not crm_service.entity_in_team(ctx, building_id=building_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    try:
        crm_service.update_building(ctx, building_id, _json())
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
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


@crm_bp.route('/api/buildings/bulk', methods=['POST'])
@login_required
def api_buildings_bulk():
    ctx = _ctx()
    data = _json()
    ids = [i for i in (data.get('ids') or []) if _int_or_none(i) is not None]
    action = data.get('action')
    if not ids or not action:
        return jsonify({'success': False, 'error': 'Nothing selected'}), 400
    value = data.get('value')
    if action == 'list' and not _int_or_none(value):
        new_name = (data.get('new_list_name') or '').strip()
        if not new_name:
            return jsonify({'success': False, 'error': 'Pick or name a list'}), 400
        value = crm_service.create_list(ctx, name=new_name)
    try:
        n = crm_service.bulk_update_buildings(ctx, ids, action, value)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    return jsonify({'success': True, 'updated': n})


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
    data = _json()
    if action == 'done':
        crm_service.resolve_follow_up(ctx, follow_up_id, 'done')
    elif action == 'skip':
        crm_service.resolve_follow_up(ctx, follow_up_id, 'skipped')
    elif action == 'reopen':
        crm_service.resolve_follow_up(ctx, follow_up_id, 'open')
    elif action == 'snooze':
        crm_service.snooze_follow_up(ctx, follow_up_id, _int_or_none(data.get('days')) or 1)
    elif action == 'update':
        crm_service.update_follow_up(
            ctx, follow_up_id,
            title=data.get('title') if 'title' in data else None,
            due_date=_parse_date(data.get('due_date')) if data.get('due_date') else None,
            note=data.get('note') if 'note' in data else None,
            assigned_to_id=_int_or_none(data['assigned_to_id']) if 'assigned_to_id' in data else '__keep__',
        )
    elif action == 'delete':
        crm_service.delete_follow_up(ctx, follow_up_id)
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
    extension = (crm_service.normalize_extension(data.get('phone_ext'))
                 or crm_service.split_phone_extension(data.get('phone'))[1])
    duplicates = crm_service.find_contacts_by_digits(ctx, digits, extension) if digits else []
    if duplicates and not data.get('force'):
        return jsonify({
            'success': False, 'duplicate': True, 'matches': duplicates,
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
        phone_extension=extension,
    )
    return jsonify({'success': True, 'contact_id': contact_id})


@crm_bp.route('/api/contact/<int:contact_id>/update', methods=['POST'])
@login_required
def api_contact_update(contact_id):
    ctx = _ctx()
    if not crm_service.entity_in_team(ctx, contact_id=contact_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    try:
        crm_service.update_contact(ctx, contact_id, _json())
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    return jsonify({'success': True})


@crm_bp.route('/api/contact/<int:contact_id>/delete', methods=['POST'])
@login_required
@crm_admin_required
def api_contact_delete(contact_id):
    ok = crm_service.delete_contact(_ctx(), contact_id)
    return jsonify({'success': ok}), (200 if ok else 404)


@crm_bp.route('/api/contact/merge', methods=['POST'])
@login_required
def api_contact_merge():
    ctx = _ctx()
    data = _json()
    source_id = _int_or_none(data.get('source_id'))
    target_id = _int_or_none(data.get('target_id'))
    if not source_id or not target_id:
        return jsonify({'success': False, 'error': 'Pick both people'}), 400
    if not crm_service.entity_in_team(ctx, contact_id=source_id) or \
            not crm_service.entity_in_team(ctx, contact_id=target_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    try:
        ok = crm_service.merge_contacts(ctx, source_id, target_id)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    return jsonify({'success': ok, 'target_id': target_id})


@crm_bp.route('/api/building-contact/unlink', methods=['POST'])
@login_required
def api_unlink_contact():
    ctx = _ctx()
    data = _json()
    building_id = _int_or_none(data.get('building_id'))
    contact_id = _int_or_none(data.get('contact_id'))
    if not crm_service.entity_in_team(ctx, building_id=building_id, contact_id=contact_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    crm_service.unlink_contact(ctx, building_id, contact_id)
    return jsonify({'success': True})


@crm_bp.route('/api/building-contact/role', methods=['POST'])
@login_required
def api_building_contact_role():
    ctx = _ctx()
    data = _json()
    building_id = _int_or_none(data.get('building_id'))
    contact_id = _int_or_none(data.get('contact_id'))
    if not crm_service.entity_in_team(ctx, building_id=building_id, contact_id=contact_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    crm_service.set_building_contact_role(ctx, building_id, contact_id, data.get('role') or 'other')
    return jsonify({'success': True})


@crm_bp.route('/api/phone', methods=['POST'])
@login_required
def api_phone_add():
    ctx = _ctx()
    data = _json()
    contact_id = _int_or_none(data.get('contact_id'))
    if not crm_service.entity_in_team(ctx, contact_id=contact_id):
        return jsonify({'success': False, 'error': 'Not found'}), 404
    digits = crm_service.normalize_phone_digits(data.get('number'))
    extension = (crm_service.normalize_extension(data.get('extension'))
                 or crm_service.split_phone_extension(data.get('number'))[1])
    if not digits:
        return jsonify({'success': False, 'error': 'Enter a phone number'}), 400
    duplicates = [d for d in crm_service.find_contacts_by_digits(ctx, digits, extension)
                  if d['id'] != contact_id]
    if duplicates and not data.get('force'):
        return jsonify({
            'success': False, 'duplicate': True, 'matches': duplicates,
            'error': 'That number is already on ' + ', '.join(d['name'] for d in duplicates[:3]),
        }), 409
    phone_id = crm_service.add_phone(
        ctx, contact_id,
        number=data.get('number'),
        extension=extension,
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


@crm_bp.route('/api/phone/<int:phone_id>/update', methods=['POST'])
@login_required
def api_phone_update(phone_id):
    data = _json()
    ok = crm_service.update_phone(
        _ctx(), phone_id,
        label=data['label'] if 'label' in data else '__keep__',
        extension=data['extension'] if 'extension' in data else '__keep__',
        make_primary=bool(data.get('make_primary')),
    )
    return jsonify({'success': ok}), (200 if ok else 404)


@crm_bp.route('/api/phone/<int:phone_id>/delete', methods=['POST'])
@login_required
def api_phone_delete(phone_id):
    ok = crm_service.delete_phone(_ctx(), phone_id)
    return jsonify({'success': ok}), (200 if ok else 404)


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


@crm_bp.route('/api/roster')
@login_required
def api_roster():
    """Team members for assignee pickers."""
    roster = crm_service.get_team_roster(_ctx()['team_id'])
    return jsonify({'success': True,
                    'roster': [{'id': u['id'], 'name': u['name']} for u in roster]})


@crm_bp.route('/api/lists')
@login_required
def api_lists():
    """Lightweight list options for pickers."""
    lists = crm_service.list_lists(_ctx())
    return jsonify({'success': True,
                    'lists': [{'id': l['id'], 'name': l['name']} for l in lists]})


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


def _iso(value):
    """Timestamps go to the browser as ISO strings; None stays None."""
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _saved_filter_json(row, ctx):
    """One saved search, shaped for the page that renders the menu."""
    return {
        'id': row['id'],
        'name': row['name'],
        'querystring': row['querystring'],
        'page': row.get('page') or 'properties',
        'visibility': row.get('visibility') or 'team',
        'is_pinned': bool(row.get('is_pinned')),
        'is_mine': bool(row.get('is_mine')),
        # The menu only offers pin/rename/delete where the write will land.
        'can_edit': bool(row.get('is_mine')) or bool(ctx['is_admin']),
        'owner_name': row.get('owner_name'),
        'use_count': row.get('use_count') or 0,
        'last_used_at': _iso(row.get('last_used_at')),
        'created_at': _iso(row.get('created_at')),
    }


@crm_bp.route('/api/saved-filters')
@login_required
def api_saved_filters():
    """Saved searches for one page (default the Properties grid)."""
    page = (request.args.get('page') or 'properties').strip()[:32]
    ctx = _ctx()
    rows = crm_service.list_saved_filters(ctx, page=page)
    return jsonify({'success': True, 'searches': [_saved_filter_json(r, ctx) for r in rows]})


@crm_bp.route('/api/saved-filter', methods=['POST'])
@login_required
def api_saved_filter():
    ctx = _ctx()
    data = _json()
    name = (data.get('name') or '').strip()
    querystring = (data.get('querystring') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Give the search a name'}), 400
    filter_id = crm_service.save_filter(
        ctx,
        name=name,
        querystring=querystring,
        page=(data.get('page') or 'properties'),
        visibility=data.get('visibility') or 'team',
    )
    rows = [r for r in crm_service.list_saved_filters(ctx) if r['id'] == filter_id]
    return jsonify({
        'success': True,
        'filter_id': filter_id,
        'search': _saved_filter_json(rows[0], ctx) if rows else None,
    })


@crm_bp.route('/api/saved-filter/<int:filter_id>', methods=['POST'])
@login_required
def api_saved_filter_update(filter_id):
    """Rename, re-point at the current filters, share, or pin a saved search."""
    ctx = _ctx()
    data = _json()
    changed = crm_service.update_saved_filter(
        ctx, filter_id,
        name=data['name'] if 'name' in data else '__keep__',
        querystring=data['querystring'] if 'querystring' in data else '__keep__',
        visibility=data['visibility'] if 'visibility' in data else '__keep__',
        is_pinned=data['is_pinned'] if 'is_pinned' in data else '__keep__',
    )
    if not changed:
        return jsonify({'success': False, 'error': "That search belongs to someone else"}), 403
    rows = [r for r in crm_service.list_saved_filters(ctx) if r['id'] == filter_id]
    return jsonify({'success': True, 'search': _saved_filter_json(rows[0], ctx) if rows else None})


@crm_bp.route('/api/saved-filter/<int:filter_id>/used', methods=['POST'])
@login_required
def api_saved_filter_used(filter_id):
    crm_service.touch_saved_filter(_ctx(), filter_id)
    return jsonify({'success': True})


@crm_bp.route('/api/saved-filter/<int:filter_id>/delete', methods=['POST'])
@login_required
def api_saved_filter_delete(filter_id):
    crm_service.delete_saved_filter(_ctx(), filter_id)
    return jsonify({'success': True})


# ============================================================
# Permit-side integration: bulk add & status
# ============================================================

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
