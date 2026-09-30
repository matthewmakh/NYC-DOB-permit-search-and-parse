"""Local-only UI fixtures; never imports the production app or connects to a DB.
Run: uv run --with flask python dashboard_html/tests/mobile_preview.py
"""
from datetime import date, datetime
from pathlib import Path
from time import monotonic
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

# Synthetic owner research examples. These in-memory edits disappear on restart.
def demo_review(**overrides):
    return dict(version=0, status='not_researched', match_status='unreviewed', result_url='',
                phones=[], emails=[], notes='', reviewed_at=None, reviewed_by=None, **overrides)


def demo_source(key, label, reported='2026-09-23'):
    return dict(key=key, label=label, record_id='synthetic-' + key, reported_date=reported,
                date_label='Reported', period=None, url=None)


RESEARCH_PEOPLE = [
    dict(id='demo-jordan-deed', name='DAVIS, JORDAN', role='Deed grantee · Registered owner', is_person=True,
         historical=False, sources=[demo_source('acris', 'ACRIS deed grantee', '2025-03-01'), demo_source('hpd', 'HPD registered owner')],
         locations=[dict(id='demo-hpd-location', label='Fort Lee, NJ', city='Fort Lee', state='NJ', zip_code='07024', source='HPD registered owner',
                         source_key='hpd', reported_date='2026-09-23', kind='Reported business location', is_property=False),
                    dict(id='demo-acris-location', label='Brooklyn, NY', city='Brooklyn', state='NY', zip_code='11225', source='ACRIS deed grantee',
                         source_key='acris', reported_date='2025-03-01', kind='Reported deed mailing location', is_property=False)],
         default_location_id='demo-hpd-location', research=demo_review()),
    dict(id='demo-jordan-respondent', name='Jordan Davis', role='Violation respondent', is_person=True,
         historical=False, sources=[demo_source('ecb', 'ECB violation respondent', '2024-05-11')],
         locations=[], default_location_id='property', research=demo_review()),
    dict(id='demo-alex', name='Alex Smith', role='Registered owner', is_person=True, historical=False,
         sources=[demo_source('hpd', 'HPD registered owner')], locations=[], default_location_id='property',
         research={**demo_review(), 'version': 1, 'status': 'contact_found', 'match_status': 'confirmed_match',
                   'result_url': 'https://www.truepeoplesearch.com/results?name=Alex%20Smith&citystatezip=10027',
                   'phones': ['+12125550100'], 'emails': ['alex@example.test'], 'notes': 'Synthetic reviewed contact for UI testing.',
                   'reviewed_at': '2026-09-30T15:00:00Z', 'reviewed_by': {'id': 1, 'name': 'Demo reviewer'}}),
    dict(id='demo-morgan', name='Morgan Lee', role='Former deed grantee', is_person=True, historical=True,
         sources=[demo_source('acris', 'ACRIS historical deed', '2011-04-13')], locations=[], default_location_id='property',
         research={**demo_review(), 'version': 1, 'status': 'do_not_contact', 'match_status': 'possible_match',
                   'notes': 'Synthetic do-not-contact example.', 'reviewed_at': '2026-09-29T15:00:00Z',
                   'reviewed_by': {'id': 1, 'name': 'Demo reviewer'}}),
]
RESEARCH_SOURCES = [dict(key=key, label=label, status='current', checked_at='2026-09-30T15:00:00Z',
                         error=None, can_refresh=True, next_attempt_at=None)
                    for key, label in [('hpd', 'HPD registration'), ('acris', 'ACRIS deeds'), ('pluto', 'NYC PLUTO'),
                                       ('rpad', 'Historical RPAD'), ('ecb', 'ECB violations'), ('sos', 'NY Secretary of State')]]
RESEARCH_HISTORY = [dict(id=1, source='hpd', kind='baseline', reported_date='2026-09-23', observed_at='2026-09-30T15:00:00Z',
                        before=None, after={'records': [{'name': 'Jordan Davis', 'role': 'IndividualOwner', 'city': 'Fort Lee', 'state': 'NJ'}]}, changes=[])]
RESEARCH_JOBS = {}


@app.route('/api/property/<bbl>/owner-research', methods=['GET'])
def demo_owner_research(bbl):
    return jsonify(success=True, synthetic=True, people=RESEARCH_PEOPLE,
                   property_location=dict(id='property', label='Property location (fallback)', city='New York', state='NY',
                                          zip_code='10027', kind='Property location', is_property=True),
                   conflicts=[dict(kind='unconfirmed_identity', person_ids=['demo-jordan-deed', 'demo-jordan-respondent'],
                                   message='Jordan Davis appears in separate source records. A shared name does not confirm they are the same person.'),
                              dict(kind='reported_locations', person_ids=['demo-jordan-deed'],
                                   message='Jordan Davis has different reported locations. The more recent HPD location is selected for searching.')])


@app.route('/api/property/<bbl>/owner-research/<person_id>', methods=['POST'])
def demo_save_review(bbl, person_id):
    person = next((person for person in RESEARCH_PEOPLE if person['id'] == person_id), None)
    if not person:
        return jsonify(success=False, error='Synthetic person not found'), 404
    if request.headers.get('X-Owner-Research') != '1' or not request.is_json:
        return jsonify(success=False, error='JSON review header required'), 400
    payload = request.get_json()
    if payload.get('version') != person['research']['version']:
        return jsonify(success=False, error='Another teammate updated this review'), 409
    person['research'] = {**payload, 'version': payload['version'] + 1, 'reviewed_at': datetime.now().isoformat(),
                          'reviewed_by': {'id': 1, 'name': 'Demo reviewer'}}
    return jsonify(success=True, synthetic=True, person=person)


@app.route('/api/property/<bbl>/owner-sources', methods=['GET'])
def demo_source_status(bbl):
    for key, due in list(RESEARCH_JOBS.items()):
        if monotonic() < due:
            continue
        source = next(source for source in RESEARCH_SOURCES if source['key'] == key)
        source.update(status='current', checked_at=datetime.now().isoformat(), can_refresh=True)
        RESEARCH_JOBS.pop(key)
        before = {'records': [{'name': 'Jordan Davis', 'city': 'Brooklyn', 'state': 'NY'}]}
        after = {'records': [{'name': 'Jordan Davis', 'city': 'Fort Lee', 'state': 'NJ'}]}
        RESEARCH_HISTORY.insert(0, dict(id=len(RESEARCH_HISTORY) + 1, source=key, kind='change', reported_date='2026-09-30',
                                       observed_at=datetime.now().isoformat(), before=before, after=after,
                                       changes=[dict(field='Reported contacts', before=before['records'], after=after['records'])]))
    return jsonify(success=True, synthetic=True, sources=RESEARCH_SOURCES, history=RESEARCH_HISTORY)


@app.route('/api/property/<bbl>/owner-sources/<key>/refresh', methods=['POST'])
def demo_source_refresh(bbl, key):
    source = next((source for source in RESEARCH_SOURCES if source['key'] == key), None)
    if not source:
        return jsonify(success=False, error='Unknown synthetic source'), 404
    if request.headers.get('X-Owner-Research') != '1' or not request.is_json:
        return jsonify(success=False, error='JSON review header required'), 400
    source.update(status='queued', can_refresh=False)
    RESEARCH_JOBS[key] = monotonic() + 2
    return jsonify(success=True, synthetic=True, status='queued', message='Synthetic refresh queued; no external source will be contacted.')

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
    if path.startswith('building-profile/'):
        data['building'] = {**BUILDING, 'zip_code': '10027', 'sale_buyer_primary': 'DAVIS, JORDAN', 'owner_name_hpd': 'Jordan Davis; Alex Smith',
                            'ecb_respondent_name': 'Jordan Davis', 'owner_name_rpad': 'Morgan Lee'}
        data['owners'] = {'acris': 'DAVIS, JORDAN', 'pluto': BUILDING['current_owner_name'], 'rpad': 'Morgan Lee', 'ecb': 'Jordan Davis'}
        data['owner_source_dates'] = {key: dict(reported_date='2026-09-23', date_label='Last reported', checked_at='2026-09-30', period=None)
                                      for key in ('acris', 'hpd', 'ecb')}
        data['owner_source_dates'].update(pluto=dict(period='PLUTO 26v2', checked_at='2026-09-30'), rpad=dict(period='FY 2018/19 · Final roll', checked_at='2026-09-30'))
        data['enrichment'] = dict(logged_in=True, cost=0, batch_cost=0, already_enriched=False,
                                  available_owners=[dict(name='DAVIS, JORDAN', source='ACRIS Latest Deed Grantee', recommended=True),
                                                    dict(name='Morgan Lee', source='Historical RPAD Assessment', recommended=False)], enriched_owners=[])
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
    rendered = render_template(template, **ctx)
    if path.startswith('property/'):
        rendered = rendered.replace('<div class="dossier">', '<div class="dossier"><p style="padding:12px 24px;background:#fff2cc;color:#624b00;margin:0">Synthetic local UI demo — sample people and in-memory reviews. No production data or external lookups.</p>', 1)
    return rendered

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5099)
