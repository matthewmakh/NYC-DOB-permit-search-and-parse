"""Offline source/validation/HTTP checks plus isolated PostgreSQL integration.

Set OWNER_RESEARCH_TEST_DATABASE_URL to a disposable database to run persistence
checks. Each integration test creates and drops its own isolated schema.
"""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).parent / 'dashboard_html'))
os.environ.setdefault('PYTHON_DOTENV_DISABLED', '1')
os.environ.setdefault('DATABASE_URL', 'postgresql://unused@localhost:1/unused')

from flask import Flask
import psycopg2
from psycopg2.extras import RealDictCursor
import owner_research as research
import enrichment_service as enrichment
from owner_research_routes import owner_research_bp


BBL = '3012980066'


def building(**updates):
    return dict(id=10, bbl=BBL, borough='3', zip_code='11225', hpd_registration_id='600',
                hpd_last_registration_date='2026-09-01', **updates)


def hpd(name='Jordan Davis', **updates):
    row = dict(name=name, role='IndividualOwner', registration_id='600', contact_id='700',
               reported_date='2026-09-01', address='20 SAMPLE AVE', city='Albany', state='NY', zip_code='12207')
    row.update(updates)
    return row


def deed(name='DAVIS, JORDAN', **updates):
    row = dict(party_name=name, party_type='buyer', doc_type='DEED', document_id='deed-1',
               is_primary_deed=True, recorded_date='2026-08-01', address_1='20 SAMPLE AVE',
               city='Albany', state='NY', zip_code='12207')
    row.update(updates)
    return row


def review(**updates):
    row = dict(version=0, status='needs_review', match_status='possible_match', result_url='',
               phones=[], emails=[], notes='Check source match')
    row.update(updates)
    return row


def saved(person, **updates):
    row = dict(person_id=person['id'], identity_ids=person['identity_ids'],
               source_snapshot={k: v for k, v in person.items() if k != 'research'},
               name_key=research.name_key(person['name']), version=1, status='needs_review',
               match_status='possible_match', result_url='', phones=[], emails=[], notes='Saved note',
               reviewed_at='2026-09-30T10:00:00Z', reviewed_by=1, reviewer_name='Researcher')
    row.update(updates)
    return row


class EvidenceTests(unittest.TestCase):
    def test_matching_complete_name_and_full_reported_address_groups(self):
        people, _ = research.build_people(building(hpd_owner_contacts=[hpd()]), [deed()])
        self.assertEqual(len(people), 1)
        self.assertEqual({s['key'] for s in people[0]['sources']}, {'acris', 'hpd'})
        self.assertEqual(len(people[0]['locations']), 2)
        self.assertNotIn('street', people[0]['locations'][0])
        self.assertEqual(people[0]['locations'][0]['source_key'], 'hpd')
        self.assertEqual(people[0]['default_location_id'], people[0]['locations'][0]['id'])

    def test_name_alone_locality_and_initials_do_not_merge(self):
        for contact, party in [(hpd(address=''), deed(address_1='')),
                               (hpd(address='99 OTHER AVE'), deed()),
                               (hpd(name='J Davis'), deed(name='DAVIS, J'))]:
            with self.subTest(contact=contact):
                people, conflicts = research.build_people(building(hpd_owner_contacts=[contact]), [party])
                self.assertEqual(len(people), 2)
                self.assertTrue(any(c['kind'] == 'unconfirmed_identity' for c in conflicts))

    def test_locations_remain_on_their_source_person(self):
        contacts = [hpd(), hpd(name='Alex Smith', contact_id='701', city='Buffalo', zip_code='14201')]
        people, _ = research.build_people(building(hpd_owner_contacts=contacts))
        by_name = {p['name']: p for p in people}
        self.assertEqual(by_name['Jordan Davis']['locations'][0]['city'], 'Albany')
        self.assertEqual(by_name['Alex Smith']['locations'][0]['city'], 'Buffalo')
        self.assertEqual(by_name['Jordan Davis']['sources'][0]['reported_date'], '2026-09-01')

    def test_wrong_registration_does_not_attach_address(self):
        people, _ = research.build_people(building(owner_name_hpd='Jordan Davis',
                                                   hpd_owner_contacts=[hpd(registration_id='wrong')]))
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0]['locations'], [])
        self.assertEqual(people[0]['default_location_id'], 'property')

    def test_organization_has_no_person_search(self):
        people, _ = research.build_people(building(current_owner_name='SAMPLE REALTY LLC'))
        self.assertFalse(people[0]['is_person'])

    def test_official_contact_id_survives_address_refresh(self):
        before, _ = research.build_people(building(hpd_owner_contacts=[hpd()]))
        after, _ = research.build_people(building(hpd_owner_contacts=[hpd(city='Buffalo', zip_code='14201')]),
                                         saved=[saved(before[0])])
        self.assertEqual(after[0]['id'], before[0]['id'])
        self.assertEqual(after[0]['research']['notes'], 'Saved note')
        self.assertEqual(after[0]['locations'][0]['city'], 'Buffalo')

    def test_new_same_named_official_contact_does_not_inherit_review(self):
        before, _ = research.build_people(building(hpd_owner_contacts=[hpd()]))
        after, _ = research.build_people(building(hpd_owner_contacts=[hpd(contact_id='701')]), saved=[saved(before[0])])
        self.assertEqual(len(after), 2)
        self.assertEqual([p for p in after if not p['historical']][0]['research']['version'], 0)
        self.assertEqual([p for p in after if p['historical']][0]['research']['notes'], 'Saved note')

    def test_new_corroborated_source_retains_saved_card_id(self):
        before, _ = research.build_people(building(hpd_owner_contacts=[hpd()]))
        after, _ = research.build_people(building(hpd_owner_contacts=[hpd()]), [deed()], [saved(before[0])])
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]['id'], before[0]['id'])
        self.assertEqual(len(after[0]['sources']), 2)

    def test_two_existing_reviews_never_silently_merge(self):
        people, _ = research.build_people(building(hpd_owner_contacts=[hpd(address='OTHER')]), [deed()])
        after, _ = research.build_people(building(hpd_owner_contacts=[hpd()]), [deed()], [saved(p) for p in people])
        self.assertEqual(len(after), 2)
        self.assertTrue(all(p['research']['version'] == 1 for p in after))

    def test_historical_dnc_conservatively_blocks_same_name(self):
        before, _ = research.build_people(building(hpd_owner_contacts=[hpd()]))
        after, _ = research.build_people(building(hpd_owner_contacts=[hpd(contact_id='new')]),
                                         saved=[saved(before[0], status='do_not_contact')])
        self.assertTrue(all(p['do_not_contact'] for p in after))

    def test_property_location_is_explicit_fallback(self):
        loc = research.property_location(building())
        self.assertTrue(loc['is_property'])
        self.assertIn('fallback', loc['label'])


class ValidationTests(unittest.TestCase):
    def test_normalizes_contacts_and_strips_result_fragment(self):
        value = research.validate_review(review(phones=['(212) 555-0123 x20'], emails=['EXAMPLE@EXAMPLE.COM'],
                                                  result_url='https://www.truepeoplesearch.com/find/person#section'))
        self.assertEqual(value['phones'], ['+12125550123 ext. 20'])
        self.assertEqual(value['emails'], ['example@example.com'])
        self.assertNotIn('#', value['result_url'])

    def test_rejects_unsafe_urls_and_invalid_fields(self):
        for updates in [dict(result_url='javascript:alert(1)'), dict(result_url='https://truepeoplesearch.com.evil.test/person'),
                        dict(result_url='https://evil@truepeoplesearch.com/person'), dict(result_url='https://truepeoplesearch.com:bad/'),
                        dict(team_id=2), dict(version=True), dict(phones=['4412125550123']), dict(emails=['not-email']),
                        dict(notes='a' * 4001), dict(status='contact_found'),
                        dict(match_status='wrong_person', phones=['2125550123'])]:
            with self.subTest(updates=updates), self.assertRaises(research.ResearchError):
                research.validate_review(review(**updates))

    def test_clearing_contacts_and_notes_is_supported(self):
        self.assertEqual(research.validate_review(review(notes='', phones=[], emails=[]))['notes'], '')


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'test-only'
        self.app.register_blueprint(owner_research_bp)
        self.client = self.app.test_client()
        self.user = dict(id=20, sponsor_user_id=10, is_sponsored=True, is_admin=False, has_access=True, email='rep@example.test')
        self.auth = patch('auth_service.validate_session', return_value=self.user)
        self.auth.start()

    def tearDown(self):
        self.auth.stop()

    def test_team_comes_from_authenticated_user_and_response_is_private(self):
        with patch('owner_research.get_owner_research', return_value=dict(success=True)) as get:
            response = self.client.get(f'/api/property/{BBL}/owner-research?team_id=99')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get.call_args.args[1]['team_id'], 10)
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_unauthenticated_denied(self):
        with patch('auth_service.validate_session', return_value=None):
            self.assertEqual(self.client.get(f'/api/property/{BBL}/owner-research').status_code, 401)

    def test_csrf_json_custom_header_and_origin(self):
        url = f'/api/property/{BBL}/owner-research/' + 'a' * 32
        for headers in [{}, {'X-Owner-Research': '1', 'Origin': 'https://evil.test'},
                        {'X-Owner-Research': '1', 'Sec-Fetch-Site': 'same-site'}]:
            self.assertEqual(self.client.post(url, json=review(), headers=headers).status_code, 403)
        with patch('owner_research.save_owner_research', return_value=dict(success=True)) as save:
            response = self.client.post(url, json=review(), headers={'X-Owner-Research': '1', 'Origin': 'http://localhost'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(save.call_args.args[3]['team_id'], 10)

    def test_stale_update_returns_conflict(self):
        with patch('owner_research.save_owner_research', side_effect=research.ResearchError('Reload before saving', 409)):
            response = self.client.post(f'/api/property/{BBL}/owner-research/' + 'a' * 32,
                                        json=review(), headers={'X-Owner-Research': '1'})
        self.assertEqual(response.status_code, 409)

    def test_forwarded_https_requires_configured_proxy_and_never_trusts_forwarded_host(self):
        url = f'/api/property/{BBL}/owner-research/' + 'a' * 32
        headers = {'X-Owner-Research': '1', 'Origin': 'https://localhost', 'X-Forwarded-Proto': 'https'}
        with patch.dict(os.environ, {}, clear=True), patch('owner_research.save_owner_research', return_value=dict(success=True)):
            self.assertEqual(self.client.post(url, json=review(), headers=headers).status_code, 403)
            self.app.config['OWNER_RESEARCH_TRUST_PROXY_PROTO'] = True
            self.assertEqual(self.client.post(url, json=review(), headers=headers).status_code, 200)
            malicious = dict(headers, Origin='https://evil.test', **{'X-Forwarded-Host': 'evil.test'})
            self.assertEqual(self.client.post(url, json=review(), headers=malicious).status_code, 403)
            self.app.config['OWNER_RESEARCH_TRUST_PROXY_PROTO'] = False
            with patch.dict(os.environ, {'RAILWAY_ENVIRONMENT_ID': 'synthetic-railway'}):
                self.assertEqual(self.client.post(url, json=review(), headers=headers).status_code, 200)


class EnrichmentSuppressionTests(unittest.TestCase):
    def test_paid_owner_and_permit_block_before_cache_or_vendor(self):
        conn = Mock()
        with patch.object(enrichment, 'get_db_connection', return_value=conn), \
                patch.object(enrichment, 'is_owner_contact_suppressed', return_value=True), \
                patch.object(enrichment, 'check_permit_contact_enrichment') as cache, \
                patch.object(enrichment, 'call_enformion_api') as provider:
            owner = enrichment.enrich_owner(10, 'Jordan Davis', '', 1)
            permit = enrichment.enrich_permit_contact(BBL, 10, 1, 'Jordan Davis', 'owner', None, None, None, 1)
        self.assertFalse(owner[0])
        self.assertFalse(permit[0])
        self.assertIn('do not contact', owner[2])
        conn.cursor.return_value.execute.assert_not_called()
        cache.assert_not_called()
        provider.assert_not_called()

    def test_scope_failure_never_calls_provider(self):
        with patch.object(enrichment, 'get_db_connection', return_value=Mock()), \
                patch.object(enrichment, 'is_owner_contact_suppressed', side_effect=PermissionError('Account unavailable')), \
                patch.object(enrichment, 'call_enformion_api') as provider:
            result = enrichment.enrich_owner(10, 'Jordan Davis', '', 1)
        self.assertFalse(result[0])
        provider.assert_not_called()

    def test_user_scoped_cached_owner_and_permit_contacts_are_filtered(self):
        conn = Mock()
        cur = conn.cursor.return_value
        cur.fetchone.return_value = dict(is_admin=False)
        cur.fetchall.return_value = [dict(owner_name_searched='DAVIS, JORDAN', enriched_phones=['private'],
                                          enriched_emails=[], enriched_at=None)]
        with patch.object(enrichment, 'get_db_connection', return_value=conn), \
                patch.object(enrichment, '_user_owner_suppression_names', return_value={BBL: {'JORDAN DAVIS'}}):
            self.assertEqual(enrichment.check_user_enrichment_access(1, 10), (False, [], []))
            cur.fetchall.return_value = [dict(contact_name='Jordan Davis')]
            self.assertEqual(enrichment.get_enriched_contacts_for_building(BBL, 1), [])

    def test_alias_and_selection_strategy_cannot_bypass_dnc(self):
        self.assertTrue(enrichment.contact_name_is_suppressed('DAVIS, JORDAN', {'JORDAN DAVIS'}))
        self.assertTrue(enrichment.contact_name_is_suppressed('Jordan Davis', {'JORDAN MICHAEL DAVIS'}))
        self.assertFalse(enrichment.contact_name_is_suppressed('Jordan Davis Jr', {'JORDAN DAVIS SR'}))
        self.assertEqual(enrichment.filter_owners_by_strategy([dict(name='Jordan Davis', do_not_contact=True)], 'all'), [])

    def test_candidate_lists_and_bulk_estimate_omit_suppressed_contact(self):
        conn = Mock()
        cur = conn.cursor.return_value
        source_row = dict(id=10, bbl=BBL, address='Property', current_owner_name='Jordan Davis',
                          owner_name_rpad=None, owner_name_hpd=None, sos_principal_name=None,
                          sos_principal_title=None, sos_entity_name=None, sale_buyer_primary=None)
        with patch.object(enrichment, 'get_db_connection', return_value=conn), \
                patch.object(enrichment, '_user_owner_suppression_names', return_value={BBL: {'JORDAN DAVIS'}}):
            cur.fetchall.return_value = []
            cur.fetchone.return_value = source_row
            self.assertEqual(enrichment.get_available_owners_for_enrichment(10, 1), [])
            cur.fetchall.side_effect = [[], [source_row]]
            self.assertEqual(enrichment.estimate_owners_for_buildings([10], 1, 'all'), (0, 0, [], {}))
            cur.fetchall.side_effect = [[], [], [dict(permit_id=1, permit_no='permit', applicant=None,
                permittee_business_name=None, owner_business_name='Jordan Davis', owner_phone='private')]]
            cur.fetchone.return_value = dict(is_admin=False)
            self.assertEqual(enrichment.get_enrichable_permit_contacts(BBL, 1), [])


class CachedContactApiTests(unittest.TestCase):
    def setUp(self):
        import app
        self.app_module = app
        self.client = app.app.test_client()
        self.user = dict(id=20, sponsor_user_id=10, is_sponsored=True, is_admin=False, has_access=True)
        self.patches = [patch('auth_service.validate_session', return_value=self.user)]
        for name in ('log_api_call', 'log_page_view', 'log_error'):
            self.patches.append(patch.object(app, name))
        for item in self.patches:
            item.start()
        self.blocked = dict(bbl=BBL, owner_name_searched='DAVIS, JORDAN MICHAEL',
                            enriched_phones=['BLOCKED_PHONE'], enriched_emails=['blocked@example.test'], enriched_at=None)
        self.allowed = dict(bbl=BBL, owner_name_searched='Alex Smith', enriched_phones=['ALLOWED_PHONE'],
                            enriched_emails=['allowed@example.test'], enriched_at=None)
        self.dnc = dict(bbl=BBL, name_key='JORDAN DAVIS')

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def test_building_cached_owners_hide_dnc_alias_and_keep_allowed_contact(self):
        cur = Mock()
        cur.fetchone.return_value = dict(id=10)
        cur.fetchall.side_effect = [[self.dnc], [self.blocked, self.allowed]]
        with patch.object(self.app_module, 'DatabaseConnection') as db, \
                patch.object(enrichment, 'get_enriched_contacts_for_building', return_value=[]):
            db.return_value.__enter__.return_value = cur
            response = self.client.get(f'/api/building/{BBL}/enriched-contacts')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([contact['name'] for contact in response.json['owner_contacts']], ['Alex Smith'])
        self.assertNotIn('BLOCKED_PHONE', response.get_data(as_text=True))
        scope_call = next(call for call in cur.execute.call_args_list if 'crm_owner_research' in call.args[0])
        self.assertEqual(scope_call.args[1][0], 10)

    def test_peek_hides_dnc_cached_contacts_and_is_not_cacheable(self):
        cur = Mock()
        cur.fetchone.side_effect = [dict(id=10, bbl=BBL, address='Property', borough='3'), dict(n=0)]
        cur.fetchall.side_effect = [[], [self.dnc], [self.blocked, self.allowed]]
        with patch.object(self.app_module, 'DatabaseConnection') as db, \
                patch.object(self.app_module, '_decorate_owner', side_effect=lambda row: row), \
                patch('streetview.lookup_latlng', return_value=None), \
                patch('streetview.payload', return_value=dict(open_url='', open_kind='', map_url='')):
            db.return_value.__enter__.return_value = cur
            response = self.client.get(f'/api/property/{BBL}/peek')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([contact['owner_name'] for contact in response.json['contacts']['unlocked']], ['Alex Smith'])
        self.assertNotIn('BLOCKED_PHONE', response.get_data(as_text=True))
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_unlocked_status_ignores_dnc_only_property(self):
        for cache_rows, expected in [([self.blocked], {}), ([self.blocked, self.allowed], {BBL: True})]:
            cur = Mock()
            cur.fetchall.side_effect = [[self.dnc], cache_rows]
            with patch.object(self.app_module, 'DatabaseConnection') as db:
                db.return_value.__enter__.return_value = cur
                response = self.client.get(f'/api/enrichment/unlocked-status?bbls={BBL}')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['unlocked'], expected)
            self.assertIn('no-store', response.headers['Cache-Control'])

    def test_peek_contact_section_fails_closed_if_suppression_is_unavailable(self):
        cur = Mock()
        cur.fetchone.side_effect = [dict(id=10, bbl=BBL, address='Property', borough='3'), dict(n=0)]
        cur.fetchall.return_value = []
        with patch.object(self.app_module, 'DatabaseConnection') as db, \
                patch.object(self.app_module, '_decorate_owner', side_effect=lambda row: row), \
                patch.object(research, 'suppressed_owner_names', side_effect=RuntimeError('Suppression unavailable')), \
                patch('streetview.lookup_latlng', return_value=None), \
                patch('streetview.payload', return_value=dict(open_url='', open_kind='', map_url='')):
            db.return_value.__enter__.return_value = cur
            response = self.client.get(f'/api/property/{BBL}/peek')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['contacts']['unlocked'], [])
        self.assertFalse(any('FROM user_enrichments' in call.args[0] for call in cur.execute.call_args_list))


@unittest.skipUnless(os.environ.get('OWNER_RESEARCH_TEST_DATABASE_URL'), 'Set disposable PostgreSQL URL for integration')
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.dsn = os.environ['OWNER_RESEARCH_TEST_DATABASE_URL']
        self.schema = 'owner_research_' + uuid.uuid4().hex
        self.admin = psycopg2.connect(self.dsn)
        self.admin.autocommit = True
        with self.admin.cursor() as cur:
            cur.execute(f'CREATE SCHEMA {self.schema}')
        def connect():
            conn = psycopg2.connect(self.dsn, cursor_factory=RealDictCursor)
            with conn.cursor() as cur:
                cur.execute(f'SET search_path={self.schema}')
            return conn
        self.connect = connect
        self.connection_patch = patch('owner_research.get_db_connection', side_effect=connect)
        self.connection_patch.start()
        conn = connect()
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE users (id INTEGER PRIMARY KEY,email TEXT)')
            cur.execute("INSERT INTO users VALUES (1,'one@example.test'),(2,'two@example.test'),(3,'rep@example.test')")
            cur.execute('''CREATE TABLE buildings (id INTEGER PRIMARY KEY,bbl TEXT,borough TEXT,zip_code TEXT,
                        hpd_registration_id TEXT,hpd_last_registration_date DATE,hpd_owner_contacts JSONB)''')
            cur.execute('INSERT INTO buildings VALUES (10,%s,\'3\',\'11225\',\'600\',\'2026-09-01\',%s)', (BBL, json.dumps([hpd()])))
            cur.execute('''CREATE TABLE acris_transactions (id INTEGER,building_id INTEGER,doc_type TEXT,
                        document_id TEXT,is_primary_deed BOOLEAN,recorded_date DATE)''')
            cur.execute('''CREATE TABLE acris_parties (transaction_id INTEGER,party_name TEXT,party_type TEXT,
                        address_1 TEXT,address_2 TEXT,city TEXT,state TEXT,zip_code TEXT)''')
        conn.commit()
        conn.close()
        research.init_owner_research_tables()
        self.one = dict(user_id=1, team_id=1, email='one@example.test')
        self.two = dict(user_id=2, team_id=2, email='two@example.test')
        self.rep = dict(user_id=3, team_id=1, email='rep@example.test')
        self.person = research.get_owner_research(BBL, self.one)['people'][0]

    def tearDown(self):
        self.connection_patch.stop()
        with self.admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA {self.schema} CASCADE')
        self.admin.close()

    def test_team_isolation_shared_review_and_stale_writes(self):
        first = research.save_owner_research(BBL, self.person['id'], review(notes='Team one only'), self.one)['person']
        self.assertEqual(first['research']['version'], 1)
        self.assertEqual(research.get_owner_research(BBL, self.rep)['people'][0]['research']['notes'], 'Team one only')
        self.assertEqual(research.get_owner_research(BBL, self.two)['people'][0]['research']['version'], 0)
        with self.assertRaises(research.ResearchError) as err:
            research.save_owner_research(BBL, self.person['id'], review(notes='Stale overwrite'), self.rep)
        self.assertEqual(err.exception.status_code, 409)
        second = research.save_owner_research(BBL, self.person['id'], review(version=1, notes=''), self.rep)['person']
        self.assertEqual(second['research']['version'], 2)
        self.assertEqual(second['research']['reviewed_by']['id'], 3)
        self.assertEqual(second['research']['notes'], '')

    def test_deleted_contact_keeps_review_and_dnc_scoped(self):
        research.save_owner_research(BBL, self.person['id'], review(status='do_not_contact'), self.one)
        conn = self.connect()
        with conn.cursor() as cur:
            self.assertTrue(research.do_not_contact_for_owner(cur, 1, BBL, 'DAVIS, JORDAN'))
            self.assertFalse(research.do_not_contact_for_owner(cur, 2, BBL, 'Jordan Davis'))
            self.assertFalse(research.do_not_contact_for_owner(cur, 1, '3012980067', 'Jordan Davis'))
            cur.execute("UPDATE buildings SET hpd_owner_contacts='[]'::jsonb")
        conn.commit()
        conn.close()
        people = research.get_owner_research(BBL, self.one)['people']
        self.assertEqual(len(people), 1)
        self.assertTrue(people[0]['historical'])
        self.assertEqual(people[0]['research']['status'], 'do_not_contact')

    def test_manual_contact_correction_and_removal_never_writes_sources(self):
        first = research.save_owner_research(BBL, self.person['id'], review(status='contact_found',
            match_status='confirmed_match', phones=['2125550123'], emails=['old@example.test']), self.one)['person']
        self.assertEqual(first['research']['phones'], ['+12125550123'])
        research.save_owner_research(BBL, self.person['id'], review(version=1, phones=[], emails=[]), self.one)
        fetched = research.get_owner_research(BBL, self.one)['people'][0]
        self.assertEqual(fetched['research']['phones'], [])
        self.assertEqual(fetched['locations'][0]['city'], 'Albany')

    def test_enrichment_helper_uses_current_sponsor_team_and_fails_closed(self):
        research.save_owner_research(BBL, self.person['id'], review(status='do_not_contact'), self.one)
        def access(user_id=None, **kwargs):
            return dict(id=user_id, has_access=True, is_sponsored=user_id == 3, sponsor_user_id=1, is_admin=False)
        with patch.object(enrichment, 'get_db_connection', side_effect=self.connect), \
                patch('team_service.get_access_context', side_effect=access):
            self.assertTrue(enrichment.is_owner_contact_suppressed(3, 'DAVIS, JORDAN', building_id=10))
            conn = self.connect()
            with conn.cursor() as cur:
                names = research.suppressed_owner_names(cur, 1, [BBL])[BBL]
                self.assertTrue(enrichment.contact_name_is_suppressed('Jordan Michael Davis', names))
                self.assertFalse(enrichment.contact_name_is_suppressed('Alex Davis', names))
            conn.close()
            self.assertFalse(enrichment.is_owner_contact_suppressed(2, 'Jordan Davis', bbl=BBL))
            # Even if the caller supplies an unrelated BBL, building identity remains checked.
            self.assertTrue(enrichment.is_owner_contact_suppressed(3, 'Jordan Davis', building_id=10, bbl='3012980067'))
            conn = self.connect()
            with conn.cursor() as cur:
                cur.execute('DROP TABLE crm_owner_research')
            conn.commit()
            conn.close()
            with self.assertRaises(psycopg2.errors.UndefinedTable):
                enrichment.is_owner_contact_suppressed(3, 'Jordan Davis', bbl=BBL)
        with patch.object(enrichment, 'get_db_connection', side_effect=self.connect), \
                patch('team_service.get_access_context', return_value=dict(id=3, has_access=False)):
            with self.assertRaises(PermissionError):
                enrichment.is_owner_contact_suppressed(3, 'Jordan Davis', bbl=BBL)


if __name__ == '__main__':
    unittest.main()
