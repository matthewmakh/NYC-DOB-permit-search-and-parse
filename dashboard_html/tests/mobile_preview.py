"""Local-only UI fixtures; never imports the production app or connects to a DB.
Run: uv run --with flask python dashboard_html/tests/mobile_preview.py
"""
from datetime import date, datetime
from pathlib import Path
from flask import Flask, g, jsonify, render_template, request, send_from_directory
from jinja2 import ChainableUndefined

ROOT = Path(__file__).resolve().parents[1]
app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
app.secret_key = 'local-ui-fixture-only'
for endpoint in ('login', 'signup', 'forgot_password', 'team_setup'):
    app.add_url_rule('/auth/' + endpoint, endpoint='auth.' + endpoint, build_only=True)
app.jinja_env.undefined = ChainableUndefined
for name in ('date', 'due', 'input_time', 'nyday', 'nydt', 'nytime', 'recency', 'timeago', 'tel', 'input_dt', 'compact'):
    app.jinja_env.filters['crm_' + name] = lambda value, *args: str(value or '')
app.jinja_env.filters['crm_hue'] = lambda value: 210
app.jinja_env.filters['crm_initials'] = lambda value: 'JD'

BUILDING = dict(id=1, bbl='1001230045', address='123 West 123rd Street', borough='MANHATTAN',
    current_owner_name='West 123rd Street Property Management Associates LLC', owner_name='West 123rd Street Property Management Associates LLC',
    sale_price=12345678, sale_date='2025-03-01', owner='West 123rd Street Property Management Associates LLC', purchase_price=12345678, assessed_total_value=9876543, total_units=42, residential_units=40,
    num_floors=8, building_sqft=32000, lot_sqft=8000, year_built=1920, permit_count=12, total_permits=12,
    stage='new', contact_count=2, assigned_to_name='Jordan Davis', added_by_name='Alex Smith', unit_count=42)
PERMIT = dict(id=1, permit_id=1, permit_no='M01234567-I1', address=BUILDING['address'], bbl=BUILDING['bbl'],
    issue_date='2026-09-01', job_type='A2', applicant='Metropolitan Construction and Engineering Associates',
    owner=BUILDING['owner_name'], lead_score=85, estimated_cost=1250000, total_units=42,
    work_description='Interior renovation, electrical upgrades and building access improvements.', contacts=[])
DEAL = dict(id=1, name='Building access and security modernization', stage='new', estimated_value=125000,
    service_type='Access control', building_address=BUILDING['address'], next_step='Discuss scope with owner',
    next_step_at='2026-09-24 10:00', assigned_to_name='Jordan Davis', added_by_name='Alex Smith')
CONTRACTOR = dict(name=PERMIT['applicant'], contractor_name=PERMIT['applicant'], total_jobs=124, total_permits=124,
    active_jobs=12, total_buildings=32, total_units=250, total_value=1250000, phone='212-555-0100', work_mix=[])

@app.route('/crm/service-worker.js')
def service_worker():
    return send_from_directory(ROOT / 'static/js', 'crm-service-worker.js')

@app.before_request
def fixture_user():
    g.user = dict(is_admin=True, id=1, display_name='Jordan Davis')

@app.route('/api/<path:path>', methods=['GET', 'POST'])
@app.route('/crm/api/<path:path>', methods=['GET', 'POST'])
def api(path):
    data = dict(success=True, stats=dict(total_permits=24, total_value=12345678, total_properties=24, total_assessed_value=9876543,
        total_buildings=24, top_borough='MANHATTAN', trending_type='A2', by_borough=[], by_job_type=[], trends=[]),
        contractor=CONTRACTOR, properties=[BUILDING]*3, contractors=[CONTRACTOR]*3, permits=[PERMIT]*3, buildings=[BUILDING],
        building=BUILDING, freshness={}, facts={}, source=dict(url='https://data.cityofnewyork.us'), transactions=[], violations=[], contacts=[], owners={}, parties=[], risk_assessment=dict(score=25, label='Low risk', color='green', factors=[]), activity_timeline=[], signals=[], locations=[],
        pagination=dict(total_count=24, page=1, pages=2, total_pages=2, total=24, per_page=20),
        items=[], lists=[], filters=[], jobs=[], plays=[], unlocked={}, results=[BUILDING], total=24, total_count=24, users=[], logs=[], members=[],
        counts=dict(property=3, owner=1, job_type=1, permit=3),
        buyers=[dict(buyer_name=BUILDING['owner_name'], distinct_projects=24, recent_projects_12m=12,
            distinct_properties=5, smart_fit_projects=3, total_initial_cost=123456789, account_score=85,
            sample_addresses=[BUILDING['address']])],
        alerts=[dict(project_key='DOBNOW:M01234567', address=BUILDING['address'], owner_name=BUILDING['owner_name'],
            alert_type='new_filing', job_type='A2', initial_cost=1250000, description=PERMIT['work_description'])])
    return jsonify(data)

@app.route('/')
@app.route('/<path:path>')
def page(path=''):
    routes = {'':'home.html', 'permits':'construction.html', 'properties':'properties.html',
        'contractors':'contractors.html', 'buyers':'repeat_buyers.html', 'alerts':'sales_alerts.html',
        'search':'search_results.html', 'property/1001230045':'building_profile.html',
        'permit/1':'permit_detail.html', 'contractor/example':'contractor_profile.html',
        'admin/team':'admin_team.html', 'admin/activity':'admin_activity.html',
        'auth/login':'login.html', 'auth/signup':'signup.html', 'auth/team-setup':'team_setup.html',
        'crm':'crm/today.html'}
    template = routes.get(path, path + '.html' if path.startswith('crm/') else None)
    if not template: return 'Fixture route not found', 404
    ctx = dict(active_page=path.split('/')[0], bbl=BUILDING['bbl'], contractor_name=CONTRACTOR['name'],
        permit={**PERMIT, 'issue_date': datetime(2026,9,1)}, contacts=[], contractor=CONTRACTOR,
        crm_ctx=dict(is_admin=True, user_id=1), crm_user_name='Jordan Davis', crm_tab=path.split('/')[-1],
        due_count=3, notification_count=2, push_public_key='', default_reminder_minutes=15,
        stages=['new','contacted','qualified','won'], stage_labels=dict(new='New', contacted='Contacted', qualified='Qualified', won='Won'),
        roster=[], view=request.args.get('view','cards'), filters=dict(sort='recent'),
        buildings=[BUILDING]*3, board=dict(new=[BUILDING], contacted=[], qualified=[], won=[]),
        counts={}, deals=[DEAL]*3, today_ny=date(2026,9,23), ny_hour=10, counters={}, spark=[dict(n=n, day='Mon') for n in [1,2,3,2,5,2,3]],
        overdue=[], due_today=[], upcoming=[], feed=[], groups=[], lists=[], notifications=[], history=[],
        csrf_token='fixture', q='', building=BUILDING, contact={}, record={}, stats={},
        b=BUILDING, c=dict(id=1,name='Jordan Davis',company=BUILDING['owner_name']), deal=DEAL,
        queue=[], source='today', focus_title='Today', sv={}, timeline=[], phones=[], emails=[],
        deal_funnel={}, report=dict(no_next_step=0, overdue=0), touches=[dict(n=1,day=date(2026,9,23))],
        form_values={}, listing=dict(id=1,name='Prospects'), summary={})
    if path == 'crm/buildings': ctx['counts'] = {'all':3}
    return render_template(template, **ctx)

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5099)
