"""Entity research against a real PostgreSQL: schema, worker, tiers, CRM scoping, routes.

DESTRUCTIVE: this drops and recreates the public schema of the database it is
pointed at. It therefore refuses to run unless ENTITY_RESEARCH_TEST_DATABASE_URL
is set and the database name contains "test". Example:

    createdb entity_test
    ENTITY_RESEARCH_TEST_DATABASE_URL=postgresql://localhost/entity_test \
        python entity_research_integration_check.py
"""
import os
import sys
import types
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'dashboard_html'))
_url = os.environ.get('ENTITY_RESEARCH_TEST_DATABASE_URL')
if not _url:
    print('SKIP: set ENTITY_RESEARCH_TEST_DATABASE_URL to a disposable test database')
    sys.exit(0)
if 'test' not in (urlsplit(_url).path or '').lower():
    print('REFUSED: the database name must contain "test"; this script drops the public schema')
    sys.exit(2)
os.environ['DATABASE_URL'] = _url
import psycopg2
from psycopg2.extras import RealDictCursor

import entity_research as er
import entity_sources as es


def connect():
    return psycopg2.connect(os.environ['DATABASE_URL'])


SCHEMA = """
DROP SCHEMA public CASCADE; CREATE SCHEMA public;
CREATE TABLE users (id SERIAL PRIMARY KEY, email TEXT);
INSERT INTO users (email) VALUES ('a@x.com'), ('b@x.com');
CREATE TABLE buildings (id SERIAL PRIMARY KEY, bbl VARCHAR(10) UNIQUE, address TEXT, borough INT, block INT, lot INT, bin TEXT,
    current_owner_name TEXT, owner_name_rpad TEXT, owner_name_hpd TEXT, hpd_agent_name TEXT, sale_buyer_primary TEXT,
    sale_seller_primary TEXT, mortgage_lender_primary TEXT, ecb_respondent_name TEXT, sos_principal_name TEXT, sos_entity_name TEXT,
    sos_principal_title TEXT, sos_dos_id TEXT, assessed_total_value NUMERIC, total_units INT, building_class TEXT, sale_date DATE,
    sale_price NUMERIC, last_updated TIMESTAMP);
INSERT INTO buildings (bbl, address, borough, current_owner_name, sale_buyer_primary, sos_principal_name, sos_entity_name, assessed_total_value, sale_date)
VALUES ('3012980066', '521 MONTGOMERY STREET', 3, 'ACME REALTY LLC', 'ACME REALTY, L.L.C.', 'JOHN SMITH', 'ACME REALTY LLC', 1500000, '2024-03-01'),
       ('1000010001', '1 MAIN ST', 1, 'OTHER OWNER LLC', NULL, NULL, NULL, 10, NULL);
CREATE TABLE permits (id SERIAL PRIMARY KEY, permit_no TEXT UNIQUE, job_type TEXT, issue_date DATE, filing_date DATE, address TEXT, bbl TEXT,
    applicant TEXT, owner_business_name TEXT, owner_first_name TEXT, owner_last_name TEXT, permittee_business_name TEXT,
    job_number TEXT, work_type TEXT, permit_status TEXT, api_source TEXT, link TEXT, work_description TEXT,
    owner_house_number TEXT, owner_street_name TEXT, owner_city TEXT, owner_state TEXT, owner_zip_code TEXT);
INSERT INTO permits (permit_no, job_type, issue_date, address, bbl, applicant, owner_business_name, owner_first_name, owner_last_name, permittee_business_name, job_number, api_source)
VALUES ('P1', 'A2', '2024-02-01', '521 MONTGOMERY STREET', '3012980066', 'MARIA LOPEZ', 'ACME REALTY LLC', 'JOHN', 'SMITH', 'FAST BUILDERS INC', 'J1', 'dob_now_approved');
CREATE TABLE contacts (id SERIAL PRIMARY KEY, name TEXT, phone TEXT, role TEXT);
INSERT INTO contacts (name, phone, role) VALUES ('JOHN SMITH', '2125551234', 'Owner');
CREATE TABLE permit_contacts (id SERIAL PRIMARY KEY, permit_id INT, contact_id INT, contact_role TEXT);
INSERT INTO permit_contacts (permit_id, contact_id, contact_role) VALUES (1, 1, 'Owner');
CREATE TABLE contact_evidence (id SERIAL PRIMARY KEY, contact_id INT, permit_id INT, source TEXT, raw_name TEXT, observed_role TEXT);
INSERT INTO contact_evidence (contact_id, permit_id, source, raw_name, observed_role) VALUES (NULL, 1, 'legacy', 'SMITH, JOHN', 'Owner');
CREATE TABLE acris_transactions (id SERIAL PRIMARY KEY, building_id INT, document_id TEXT, doc_type TEXT, doc_amount NUMERIC, doc_date DATE, recorded_date DATE, crfn TEXT, is_primary_deed BOOLEAN);
INSERT INTO acris_transactions (building_id, document_id, doc_type, doc_amount, doc_date, recorded_date, crfn) VALUES (1, '2024030100000001', 'DEED', 1250000, '2024-02-20', '2024-03-01', 'C1');
CREATE TABLE acris_parties (id SERIAL PRIMARY KEY, transaction_id INT, party_type TEXT, party_name TEXT, address_1 TEXT, address_2 TEXT, city TEXT, state TEXT, zip TEXT);
INSERT INTO acris_parties (transaction_id, party_type, party_name, address_1, city, state, zip) VALUES
  (1, 'buyer', 'ACME REALTY LLC', '5 ELM ST', 'NEW YORK', 'NY', '10001'), (1, 'seller', 'OLD OWNER LLC', NULL, NULL, NULL, NULL);
CREATE TABLE crm_contacts (id SERIAL PRIMARY KEY, team_id INT, assigned_to_id INT, added_by_id INT, name TEXT, company TEXT, title TEXT, last_contacted_at TIMESTAMPTZ);
INSERT INTO crm_contacts (team_id, assigned_to_id, name, company) VALUES (1, 1, 'John Smith', 'Acme Realty LLC'), (2, 2, 'John Smith', 'Other team');
CREATE TABLE crm_buildings (id SERIAL PRIMARY KEY, team_id INT, assigned_to_id INT, added_by_id INT, bbl TEXT, address TEXT, borough TEXT, stage TEXT, owner_name TEXT, last_contacted_at TIMESTAMPTZ);
INSERT INTO crm_buildings (team_id, assigned_to_id, bbl, address, stage, owner_name) VALUES (1, 1, '3012980066', '521 MONTGOMERY STREET', 'lead', 'ACME REALTY LLC');
CREATE TABLE crm_owner_research (team_id INT, bbl TEXT, person_id TEXT, name_key TEXT, status TEXT, match_status TEXT, notes TEXT, reviewed_at TIMESTAMPTZ DEFAULT NOW());
INSERT INTO crm_owner_research (team_id, bbl, person_id, name_key, status, match_status, notes) VALUES (1, '3012980066', 'p1', 'ACME REALTY LLC', 'do_not_contact', 'confirmed_match', 'no');
"""


def check(label, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + label + (f'  {extra}' if extra else ''))
    if not cond:
        global failures
        failures += 1


failures = 0
conn = connect()
with conn.cursor() as cur:
    cur.execute(SCHEMA)
conn.commit()
er.init_tables(conn)
er.init_tables(conn)  # idempotent
check('schema created', True)

# Dossier lifecycle -----------------------------------------------------------
d, created = er.get_or_create_dossier(conn, 'Acme Realty, LLC', {'bbl': '3012980066', 'role': 'Deed grantee'}, user_id=1)
check('dossier created', created and d['entity_kind'] == 'organization' and d['name_key'] == 'ACME REALTY LLC', d['name_key'])
d2, created2 = er.get_or_create_dossier(conn, 'ACME REALTY LLC', {'bbl': '1000010001'}, user_id=2)
check('same key reused with appended context', not created2 and d2['id'] == d['id'] and len(d2['contexts']) == 2)
check('expiry set ~60 days out', d['expires_at'] and (d['expires_at'] - datetime.now(timezone.utc)).days in (59, 60))
job, queued = er.enqueue_job(conn, d['id'], 'research', user_id=1)
job_dup, queued_dup = er.enqueue_job(conn, d['id'], 'research', user_id=1)
check('duplicate queued job reused', queued and not queued_dup and job_dup['id'] == job['id'])
try:
    for i in range(er.MAX_ACTIVE_JOBS_PER_USER + 1):
        extra, _ = er.get_or_create_dossier(conn, f'Cap Test Person {i}', None, user_id=1)
        er.enqueue_job(conn, extra['id'], 'research', user_id=1)
    check('per-user job cap enforced', False)
except er.ResearchError as exc:
    check('per-user job cap enforced', exc.status_code == 429)
with conn.cursor() as cur:
    cur.execute("DELETE FROM entity_dossiers WHERE display_name LIKE 'Cap Test Person%%'")
conn.commit()

# Internal lookup (real SQL) ------------------------------------------------------
rows = er.internal_lookup(conn, d)
sources = sorted({(r['source'], r['role']) for r in rows})
check('internal lookup finds building, acris, permit rows', len(rows) >= 4, str(sources))
check('acris mirror row carries counterparties', any(r['source'] == 'acris' and r['details']['parties'] == [{'name': 'OLD OWNER LLC', 'role': 'seller'}] for r in rows))
check('permit owner-business row found', any(r['source'] == 'dob_db' and r['role'] == 'Owner (business)' for r in rows))

# Worker run with faked external sources ---------------------------------------------
fake_rows = [
    er.evidence('acris', '2024030100000001', 'ACME REALTY LLC', role='buyer', bbl='3012980066', address='521 MONTGOMERY STREET, Brooklyn',
                party_address={'street': '5 ELM ST', 'city': 'NEW YORK', 'state': 'NY', 'zip': '10001'}, record_date='2024-03-01',
                details={'doc_type': 'DEED', 'ownership': True, 'parties': [{'name': 'OLD OWNER LLC', 'role': 'seller'}]}),
    er.evidence('acris', '2023010100000009', 'ACME REALTY LLC', role='buyer', bbl='4000010001', address='9 OAK ST, Queens',
                party_address={'street': '5 ELM STREET', 'city': 'NEW YORK', 'state': 'NY', 'zip': '10001'}, record_date='2023-01-01',
                details={'doc_type': 'DEED', 'ownership': True, 'parties': [{'name': 'SELLER TWO LLC', 'role': 'seller'}]}),
    er.evidence('hpd', 'C9', 'ACME REALTY HOLDINGS LLC', role='CorporateOwner', bbl='2000010001', address='2 BRONX AVE, Bronx'),
    er.evidence('sos', '6719932', 'ACME REALTY LLC', role='Registered entity', record_date='2015-06-01',
                details={'dos_id': '6719932', 'status': 'Active', 'match_quality': 'exact',
                         'people': [{'name': 'JOHN SMITH', 'role': 'CEO', 'is_agent': False, 'is_person': True},
                                    {'name': 'LEGAL AGENTS INC', 'role': 'Registered Agent', 'is_agent': True, 'is_person': False}],
                         'parties': [{'name': 'JOHN SMITH', 'role': 'CEO'}]}),
]
calls = []


def fake_steps(dossier):
    def make(key, rows):
        def fn():
            calls.append(key)
            if key == 'ecb':
                raise RuntimeError('ECB down')
            return rows, None
        return fn
    return [('acris', make('acris', fake_rows[:2])), ('hpd', make('hpd', fake_rows[2:3])), ('dob_bis', make('dob_bis', [])),
            ('dob_now_filings', make('dob_now_filings', [])), ('dob_now_permits', make('dob_now_permits', [])),
            ('ecb', make('ecb', [])), ('hpd_litigation', make('hpd_litigation', [])), ('sos', make('sos', fake_rows[3:]))]


with patch.object(es, 'research_steps', fake_steps):
    claimed = er._claim_job(connect())
    check('job claimed', claimed and claimed['id'] == job['id'] and claimed['status'] == 'running')
    er.run_job(connect, claimed)

job_after = er.get_job(conn, job['id'])
check('job complete with per-step status', job_after['status'] == 'complete' and job_after['steps']['ecb']['status'] == 'failed'
      and job_after['steps']['acris']['count'] == 2 and job_after['steps']['db']['status'] == 'done', str({k: v['status'] for k, v in job_after['steps'].items()}))
d = er.load_dossier(conn, d['id'])
check('external_checked_at stamped + summary', d['external_checked_at'] is not None and d['summary']['lots'] >= 2, str(d['summary']))
with conn.cursor(cursor_factory=RealDictCursor) as cur:
    cur.execute("SELECT source, record_id, role, match_tier, bbl, in_database FROM entity_evidence WHERE dossier_id=%s ORDER BY source, record_id", (d['id'],))
    ev = [dict(r) for r in cur.fetchall()]
conn.rollback()
tiers = {(e['source'], e['record_id']): e for e in ev}
check('external acris deed on context lot is strong', tiers[('acris', '2024030100000001')]['match_tier'] == 'strong')
check('second deed corroborated by repeated party address', tiers[('acris', '2023010100000009')]['match_tier'] == 'strong')
check('holdings llc is a candidate', tiers[('hpd', 'C9')]['match_tier'] == 'candidate')
check('sos row exact', tiers[('sos', '6719932')]['match_tier'] == 'exact')
check('evidence on tracked lot flagged in_database', tiers[('acris', '2024030100000001')]['in_database'] is True
      and tiers[('acris', '2023010100000009')]['in_database'] is False)
check('db + external acris merged into one row per doc/role', sum(1 for e in ev if e['source'] == 'acris' and e['record_id'] == '2024030100000001') == 1)

# Cached second run skips external steps -------------------------------------------------
job2, _ = er.enqueue_job(conn, d['id'], 'research', user_id=1)
calls.clear()
with patch.object(es, 'research_steps', fake_steps):
    er.run_job(connect, er._claim_job(connect()))
job2 = er.get_job(conn, job2['id'])
check('24h cache skips external sources', not calls and job2['steps']['acris']['status'] == 'cached' and job2['steps']['db']['status'] == 'done')
job3, _ = er.enqueue_job(conn, d['id'], 'research', user_id=1, force=True)
with patch.object(es, 'research_steps', fake_steps):
    er.run_job(connect, er._claim_job(connect()))
check('force bypasses cache', 'acris' in calls)

# Expansion --------------------------------------------------------------------------
with conn.cursor(cursor_factory=RealDictCursor) as cur:
    cur.execute("SELECT source, role, name_as_written, match_tier, details, bbl FROM entity_evidence WHERE dossier_id=%s AND hop=0 AND match_tier<>'candidate'", (d['id'],))
    own = [dict(r) for r in cur.fetchall()]
conn.rollback()
targets = es.expansion_targets(d, own)
check('expansion targets = principal, not agent', [t['name'] for t in targets] == ['JOHN SMITH'], str([t['name'] for t in targets]))


def fake_expand(dossier, targets):
    def fn():
        return [er.evidence('acris', '2022010100000005', 'SMITH, JOHN', role='buyer', bbl='5000010001', address='7 HILL RD, Staten Island',
                            hop=1, via='JOHN SMITH', details={'doc_type': 'DEED'})], 'JOHN SMITH: 1'
    return [('hop_acris', fn)]


ejob, _ = er.enqueue_job(conn, d['id'], 'expand', user_id=1, force=True)
with patch.object(es, 'expand_steps', fake_expand):
    er.run_job(connect, er._claim_job(connect()))
ejob = er.get_job(conn, ejob['id'])
d = er.load_dossier(conn, d['id'])
check('expand job complete and stamped', ejob['status'] == 'complete' and d['expanded_at'] is not None and d['summary']['hop_rows'] == 1, str(ejob['steps']))

# CRM join is team scoped -------------------------------------------------------------------
crm = er.crm_matches(conn, d, {'team_id': 1, 'user_id': 1, 'is_admin': True})
check('crm matches team 1 only', len(crm['contacts']) == 1 and crm['buildings'][0]['bbl'] == '3012980066' and crm['do_not_contact'] == ['3012980066'])
crm2 = er.crm_matches(conn, d, {'team_id': 2, 'user_id': 2, 'is_admin': True})
check('other team sees nothing of team 1', not crm2['contacts'] and not crm2['buildings'] and not crm2['do_not_contact'] and not crm2['research'])

# Keep / expiry / purge -----------------------------------------------------------------------
kept = er.set_permanent(conn, d['id'], True, user_id=1)
check('keep clears expiry', kept['permanent'] and kept['expires_at'] is None)
unkept = er.set_permanent(conn, d['id'], False, user_id=1)
check('unkeep restores expiry', not unkept['permanent'] and unkept['expires_at'] is not None)
with conn.cursor() as cur:
    cur.execute("UPDATE entity_dossiers SET expires_at = NOW() - interval '1 day' WHERE id=%s", (d['id'],))
conn.commit()
other, _ = er.get_or_create_dossier(conn, 'Unrelated Person', None, user_id=1)
er.set_permanent(conn, other['id'], True, user_id=1)
with conn.cursor() as cur:
    cur.execute("UPDATE entity_dossiers SET expires_at = NOW() - interval '1 day' WHERE id=%s", (other['id'],))
conn.commit()
purged = er.purge_expired(conn)
check('purge removes expired, keeps permanent', purged == 1 and er.load_dossier(conn, d['id']) is None and er.load_dossier(conn, other['id']) is not None)
with conn.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM entity_evidence WHERE dossier_id=%s", (d['id'],))
    check('evidence cascaded', cur.fetchone()[0] == 0)
conn.rollback()

# Routes against the real database --------------------------------------------------------------
stub = types.ModuleType('pywebpush'); stub.webpush = lambda *a, **k: None
class WebPushException(Exception): pass
stub.WebPushException = WebPushException; sys.modules['pywebpush'] = stub
os.environ.setdefault('SECRET_KEY', 'test')
import app as flask_app
import auth_service
user = {'id': 1, 'email': 'a@x.com', 'is_admin': True}
auth_service.validate_session = lambda token: user
flask_app.app.config['TESTING'] = True
with flask_app.app.test_client() as c:
    with c.session_transaction() as s:
        s['session_token'] = 'x'
    r = c.post('/api/entity/research', json={'name': 'Acme Realty LLC', 'context': {'bbl': '3012980066'}},
               headers={'X-Entity-Research': '1'})
    data = r.get_json()
    check('POST research creates dossier + job', r.status_code == 200 and data['success'] and data['job_id'], str(data))
    did = data['dossier_id']
    with patch.object(es, 'research_steps', fake_steps):
        er.run_job(connect, er._claim_job(connect()))
    r = c.get(f'/api/entity/{did}')
    payload = r.get_json()
    check('GET dossier payload', r.status_code == 200 and payload['total_rows'] >= 5 and payload['properties'] and payload['connections'],
          f"rows={payload.get('total_rows')} props={len(payload.get('properties') or [])} conns={[c['name'] for c in payload.get('connections') or []]}")
    main = [p for p in payload['properties'] if p['bbl'] == '3012980066'][0]
    check('property card merged: strong, tracked', main['tier'] == 'strong' and main['in_database'] and main['records'] >= 3, str(main['roles']))
    check('crm panel scoped and dnc present', payload['crm']['do_not_contact'] == ['3012980066'])
    r = c.post(f'/api/entity/{did}/add-property', json={'bbl': '5999999999'}, headers={'X-Entity-Research': '1'})
    check('add-property rejects lots outside the dossier', r.status_code == 404, str(r.get_json()))
    r = c.post(f'/api/entity/{did}/keep', json={'permanent': True}, headers={'X-Entity-Research': '1'})
    check('keep via API', r.status_code == 200 and r.get_json()['dossier']['permanent'])
    r = c.post(f'/api/entity/{did}/expand', json={}, headers={'X-Entity-Research': '1'})
    check('expand via API queues job', r.status_code == 200 and r.get_json()['job']['kind'] == 'expand', str(r.get_json()))
    r = c.get(f'/entity/{did}')
    check('profile page renders', r.status_code == 200 and 'Acme Realty LLC' in r.get_data(as_text=True))
    r = c.get('/entity/research?name=Maria%20Lopez&bbl=3012980066&role=Applicant')
    check('GET research redirects to a new dossier', r.status_code == 302 and '/entity/' in r.headers['Location'], r.headers.get('Location'))
    r = c.get('/api/entities')
    check('index lists dossiers', r.status_code == 200 and len(r.get_json()['dossiers']) >= 2)

print('\nFAILURES:', failures)
sys.exit(1 if failures else 0)
