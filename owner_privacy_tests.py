"""Synthetic address attribution, API access and saved-payload regressions."""
import os
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['DATABASE_URL'] = 'postgresql://unused@localhost:1/unused'
import csv
import io
import json
import unittest
import uuid
from contextlib import contextmanager
from datetime import date
from unittest.mock import Mock, patch

import _pipeline_path  # noqa: F401
import psycopg2
from psycopg2.extras import RealDictCursor, Json
import app
import auth_service
import enrichment_service as enrichment
import property_source_refresh as refresh
import step2_enrich_from_pluto as property_api
from enrichment_privacy import minimal_result, migrate_privacy
from owner_address_evidence import resolve_export_address
from paid_enrichment_store import SCHEMA_SQL, grant_result


class AddressEvidenceTests(unittest.TestCase):
    def test_hpd_preserves_each_owner_address_and_clears_ambiguous_legacy_fields(self):
        contacts = [dict(type='IndividualOwner', firstname='Jordan', lastname='Davis',
                         registrationcontactid='1', businesshousenumber='10',
                         businessstreetname='Example St', businesscity='Albany', businessstate='NY'),
                    dict(type='JointOwner', firstname='Morgan', lastname='Lee',
                         registrationcontactid='2', businesshousenumber='20',
                         businessstreetname='Sample St', businesscity='Boston', businessstate='MA')]
        client = Mock()
        client.get_all.side_effect = [[dict(registrationid='55', lastregistrationdate='2026-09-01')], contacts, [], []]
        with patch.object(property_api, '_get_client', return_value=client), patch.object(property_api.time, 'sleep'):
            data, error = property_api.get_hpd_data_for_bbl('3012980066')
        self.assertIsNone(error)
        self.assertEqual(data['owner_name_hpd'], 'Jordan Davis & Morgan Lee')
        self.assertIsNone(data['hpd_owner_business_address'])
        self.assertIsNone(data['hpd_owner_business_city'])
        self.assertEqual([(c['name'], c['address'], c['city']) for c in data['hpd_owner_contacts']],
                         [('Jordan Davis', '10 Example St', 'Albany'), ('Morgan Lee', '20 Sample St', 'Boston')])
        self.assertTrue(all(c['reported_date'] == '2026-09-01' and c['registration_id'] == '55'
                            for c in data['hpd_owner_contacts']))
        prop = dict(owner_name='Morgan Lee', owner_source='hpd', **data)
        self.assertIn('20 Sample St', resolve_export_address(prop)['owner_address'])
        prop['owner_name'] = data['owner_name_hpd']
        self.assertEqual(resolve_export_address(prop), {})

    def test_hpd_single_contact_legacy_fields_still_belong_to_that_owner(self):
        client = Mock()
        client.get_all.side_effect = [[dict(registrationid='55')],
            [dict(type='IndividualOwner', firstname='Jordan', lastname='Davis',
                  businesshousenumber='10', businessstreetname='Example St')], [], []]
        with patch.object(property_api, '_get_client', return_value=client), patch.object(property_api.time, 'sleep'):
            data, error = property_api.get_hpd_data_for_bbl('3012980066')
        self.assertIsNone(error)
        self.assertEqual(data['hpd_owner_business_address'], '10 Example St')
        self.assertEqual(data['hpd_owner_contacts'][0]['name'], data['owner_name_hpd'])

    def test_address_never_borrows_an_agent_or_other_source(self):
        prop = dict(owner_name='Jordan Davis', owner_source='pluto',
                    sos_principal_street='Agent address', ecb_respondent_address='Respondent address')
        self.assertEqual(resolve_export_address(prop), {})

    def test_acris_requires_current_deed_buyer_name_date_and_unambiguous_address(self):
        prop = dict(owner_name='Jordan Davis', owner_source='acris', sale_crfn='new',
                    sale_recorded_date='2026-09-01')
        party = dict(party_name='JORDAN DAVIS', party_type='buyer', doc_type='DEED',
                     is_primary_deed=True, crfn='new', recorded_date='2026-09-01',
                     address_1='10 Example St', city='Albany', state='NY', zip_code='12201')
        self.assertEqual(resolve_export_address(prop, [party])['owner_address_reported_date'], '2026-09-01')
        for change in (dict(party_name='Morgan Lee'), dict(party_type='seller'),
                       dict(doc_type='MTGE'), dict(is_primary_deed=False),
                       dict(crfn='old'), dict(recorded_date='2020-01-01')):
            with self.subTest(change=change):
                self.assertEqual(resolve_export_address(prop, [{**party, **change}]), {})
        self.assertEqual(resolve_export_address(prop, [party, {**party, 'address_1':'20 Sample St'}]), {})

    def test_vendor_match_retains_only_target_name_and_match_evidence(self):
        vendor = {'First Name':'Jordan', 'Last Name':'Davis', 'Age':60, 'Born':'1966',
                  'Relatives':['PRIVATE RELATIVE'], 'Associates':['PRIVATE ASSOCIATE'],
                  'Street Address':'PRIVATE HOME', 'Previous Addresses':['PRIVATE HISTORY']}
        match = enrichment.summarize_apify_match(vendor, {'street_match':True, 'address_kind':'previous',
                                                         'input_given':'PRIVATE SEARCH', 'score':99})
        self.assertEqual(match, {'matched_name':'Jordan Davis',
            'verification':{'score':99, 'address_kind':'previous', 'street_match':True}})
        old = {'phones':[{'number':'2125550100'}], 'emails':[], 'match':{
            **match, 'relatives':['PRIVATE RELATIVE'], 'current_address':'PRIVATE HOME'},
            'raw_response':vendor, 'extra':'PRIVATE EXTRA'}
        clean = minimal_result(old)
        self.assertNotIn('PRIVATE', json.dumps(clean))
        self.assertEqual(clean['phones'], old['phones'])

    def test_provider_errors_do_not_echo_private_payloads_into_logs_or_responses(self):
        with patch.object(enrichment, 'ENFORMION_AP_NAME', 'test'), \
                patch.object(enrichment, 'ENFORMION_AP_PASSWORD', 'test'), \
                patch.object(enrichment.requests, 'post', return_value=Mock(status_code=400, text='PRIVATE QUERY')), \
                patch('builtins.print') as log:
            result = enrichment.call_enformion_api('Jordan','Davis')
        self.assertNotIn('PRIVATE',str(result))
        self.assertNotIn('PRIVATE',str(log.call_args_list))


class ApiPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        for name in ('log_api_call', 'log_page_view', 'log_error'):
            p = patch.object(app, name)
            p.start()
            self.addCleanup(p.stop)

    def test_anonymous_address_apis_reject_before_any_database_or_provider_read(self):
        with patch.object(app, 'DatabaseConnection') as db, \
                patch.object(enrichment, 'get_enriched_contacts_for_building') as lookup:
            for path in ('/api/building-profile/3012980066', '/api/building/3012980066/enriched-contacts',
                         '/api/buildings', '/api/buildings/1', '/api/buildings/1/contacts',
                         '/api/property/3012980066', '/api/seller-leads'):
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 401)
                    self.assertIn('no-store', response.headers['Cache-Control'])
            db.assert_not_called()
            lookup.assert_not_called()

    def test_authenticated_profile_reaches_lookup_and_enriched_contacts_receive_user(self):
        with self.client.session_transaction() as session:
            session['session_token'] = 'synthetic-session'
        cursor = Mock()
        cursor.fetchone.return_value = None
        with patch.object(auth_service, 'validate_session', return_value={'id':42}), \
                patch.object(app, 'DatabaseConnection') as db, \
                patch.object(enrichment, 'get_enriched_contacts_for_building', return_value=[]) as lookup:
            db.return_value.__enter__.return_value = cursor
            self.assertEqual(self.client.get('/api/building-profile/3012980066').status_code, 404)
            response = self.client.get('/api/building/3012980066/enriched-contacts')
            self.assertEqual(response.status_code, 200)
            lookup.assert_called_once_with('3012980066', 42)
            self.assertTrue(response.json['logged_in'])

    def test_legacy_building_api_excludes_global_paid_cache(self):
        cursor = Mock()
        cursor.fetchall.return_value = [dict(id=1, address='Property address',
            enriched_raw_response={'relatives':['PRIVATE']}, enriched_phones=['PRIVATE'], enriched_emails=['PRIVATE'])]
        with patch.object(auth_service, 'validate_session', return_value={'id':42}), \
                patch.object(app, 'DatabaseConnection') as db:
            db.return_value.__enter__.return_value = cursor
            response = self.client.get('/api/buildings')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('PRIVATE', response.get_data(as_text=True))
        self.assertEqual(response.json['buildings'][0]['address'], 'Property address')


@unittest.skipUnless(os.getenv('ENRICHMENT_TEST_DATABASE_URL'), 'disposable PostgreSQL DSN not supplied')
class DatabasePrivacyTests(unittest.TestCase):
    def setUp(self):
        self.schema = 'privacy_' + uuid.uuid4().hex
        self.conn = psycopg2.connect(os.environ['ENRICHMENT_TEST_DATABASE_URL'])
        with self.conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA {self.schema}')
            cur.execute(f'SET search_path={self.schema}')
            cur.execute('CREATE TABLE users(id INTEGER PRIMARY KEY)')
            cur.execute('INSERT INTO users VALUES(1),(2)')
            cur.execute('''CREATE TABLE buildings(id INTEGER PRIMARY KEY,bbl TEXT,address TEXT,
                current_owner_name TEXT,owner_name_hpd TEXT,owner_name_rpad TEXT,sale_buyer_primary TEXT,
                sale_recorded_date DATE,sale_crfn TEXT,hpd_registration_id TEXT,hpd_last_registration_date DATE,
                hpd_owner_contacts JSONB,hpd_owner_business_address TEXT,hpd_owner_business_city TEXT,
                hpd_owner_business_state TEXT,hpd_owner_business_zip TEXT,enriched_raw_response JSONB,
                enriched_phones JSONB,enriched_emails JSONB,sos_principal_street TEXT,ecb_respondent_address TEXT,
                building_class TEXT,year_built INTEGER,total_units INTEGER,residential_units INTEGER,
                assessed_total_value NUMERIC,sale_price NUMERIC,sale_date DATE,is_cash_purchase BOOLEAN,
                hpd_total_violations INTEGER,property_last_attempted TIMESTAMP,
                property_last_enriched TIMESTAMP,property_last_error TEXT,
                hpd_open_violations INTEGER,hpd_open_complaints INTEGER,hpd_total_complaints INTEGER,
                hpd_agent_name TEXT,hpd_site_manager_name TEXT)''')
            cur.execute('''INSERT INTO buildings(id,bbl,address,sale_buyer_primary,sale_recorded_date,sale_crfn,
                current_owner_name,sos_principal_street,ecb_respondent_address)
                VALUES(1,'3012980066','Property address','Jordan Davis','2026-09-01','new',
                       'Former Owner LLC','WRONG AGENT ADDRESS','WRONG RESPONDENT ADDRESS')''')
            cur.execute('''CREATE TABLE user_enrichments(user_id INTEGER,building_id INTEGER,
                owner_name_searched TEXT,enriched_phones JSONB,enriched_emails JSONB,
                enriched_person_id TEXT,enriched_at TIMESTAMPTZ,raw_api_response JSONB,
                UNIQUE(user_id,building_id,owner_name_searched))''')
            cur.execute('''CREATE TABLE permit_contact_enrichments(id INTEGER PRIMARY KEY,
                enriched_phones JSONB,enriched_emails JSONB,enriched_raw_response JSONB)''')
            cur.execute('CREATE TABLE permits(id INTEGER PRIMARY KEY,bbl TEXT)')
            cur.execute('''CREATE TABLE acris_transactions(id INTEGER PRIMARY KEY,building_id INTEGER,
                is_primary_deed BOOLEAN,doc_type TEXT,recorded_date DATE,crfn TEXT)''')
            cur.execute('''CREATE TABLE acris_parties(transaction_id INTEGER,party_type TEXT,party_name TEXT,
                address_1 TEXT,address_2 TEXT,city TEXT,state TEXT,zip_code TEXT)''')
            cur.execute("INSERT INTO acris_transactions VALUES(1,1,TRUE,'DEED','2026-09-01','new')")
            cur.execute("INSERT INTO acris_parties VALUES(1,'buyer','Jordan Davis','10 Example St',NULL,'Albany','NY','12201')")
            cur.execute(SCHEMA_SQL)
            cur.execute(refresh.SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()
        with self.conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA {self.schema} CASCADE')
        self.conn.commit()
        self.conn.close()

    @contextmanager
    def database(self):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur

    def test_export_uses_deed_pair_and_only_matching_user_paid_contacts(self):
        with self.conn.cursor() as cur:
            cur.execute('''INSERT INTO user_enrichments(user_id,building_id,owner_name_searched,enriched_phones)
                VALUES(1,1,'Other Person','[{"number":"WRONG PERSON"}]'),
                      (2,1,'Jordan Davis','[{"number":"WRONG USER"}]'),
                      (1,1,'Jordan Davis','[{"number":"2125550100"}]')''')
        client = app.app.test_client()
        with patch.object(auth_service, 'validate_session', return_value={'id':1}), \
                patch.object(app, 'DatabaseConnection', self.database), patch.object(app, 'log_api_call'):
            response = client.get('/api/properties/export?fields=owner_address,owner_phone')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        row = next(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
        self.assertEqual(row['Owner Name'], 'Jordan Davis')
        self.assertEqual(row['Reported Owner Mailing Address'], '10 Example St, Albany, NY, 12201')
        self.assertEqual(row['Owner Address Reported Date'], '2026-09-01')
        self.assertEqual(row['Owner Address Source'], 'ACRIS deed grantee mailing address')
        self.assertEqual(row['Enriched Owner Phone'], '2125550100')
        self.assertNotIn('WRONG', response.get_data(as_text=True))
        with self.conn.cursor() as cur:
            cur.execute("UPDATE acris_parties SET party_name='Another Buyer'")
        with patch.object(auth_service, 'validate_session', return_value={'id':1}), \
                patch.object(app, 'DatabaseConnection', self.database), patch.object(app, 'log_api_call'):
            response = client.get('/api/properties/export?fields=owner_address')
        row = next(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
        self.assertEqual(row['Reported Owner Mailing Address'], '')

    def test_cleanup_is_idempotent_preserves_paid_contacts_and_receipts(self):
        private = dict(relatives=['PRIVATE'], born='PRIVATE', current_address='PRIVATE',
                       previous_addresses=['PRIVATE'])
        result = dict(phones=[dict(number='2125550100')], emails=[dict(email='jordan@example.test')],
                      person_id='selected-person', source='apify_truepeoplesearch', from_api=True,
                      match={**private, 'matched_name':'Jordan Davis',
                             'verification':{'street_match':True, 'input_given':'PRIVATE'}})
        with self.conn.cursor() as cur:
            cur.execute('''INSERT INTO owner_enrichment_results(user_id,building_id,owner_name,
                billing_scope,result,raw_response,payment_id,granted_at)
                VALUES(1,1,'JORDAN DAVIS','single',%s,%s,'receipt',NOW())''', (Json(result),Json(private)))
            cur.execute('''INSERT INTO user_enrichments(user_id,building_id,owner_name_searched,
                enriched_phones,enriched_emails,raw_api_response)
                VALUES(1,1,'JORDAN DAVIS',%s,%s,%s)''', (Json(result['phones']),Json(result['emails']),Json(private)))
            cur.execute("INSERT INTO permit_contact_enrichments VALUES(1,%s,%s,%s)",
                        (Json(result['phones']),Json(result['emails']),Json(private)))
            cur.execute("UPDATE buildings SET enriched_raw_response=%s,hpd_owner_business_address='UNPAIRED'", (Json(private),))
            for _ in range(2):
                migrate_privacy(cur)
            cur.execute('SELECT result,raw_response,payment_id,granted_at FROM owner_enrichment_results')
            stored, raw, receipt, granted_at = cur.fetchone()
            self.assertNotIn('PRIVATE', json.dumps(stored))
            self.assertEqual(stored['phones'], result['phones'])
            self.assertEqual(stored['emails'], result['emails'])
            self.assertTrue(stored['match']['verification']['street_match'])
            self.assertIsNone(raw)
            self.assertEqual(receipt, 'receipt')
            self.assertIsNotNone(granted_at)
            grant_result(cur,1,1,'Jordan Davis','receipt')
            cur.execute('SELECT raw_api_response,enriched_phones FROM user_enrichments')
            self.assertEqual(cur.fetchone(), (None,result['phones']))
            cur.execute('SELECT enriched_raw_response,enriched_emails FROM permit_contact_enrichments')
            self.assertEqual(cur.fetchone(), (None,result['emails']))
            cur.execute('SELECT enriched_raw_response,hpd_owner_business_address FROM buildings')
            self.assertEqual(cur.fetchone(), (None,None))

    def test_enriched_export_uses_the_same_address_provenance_without_any_lookup(self):
        client = app.app.test_client()
        with patch.object(auth_service,'validate_session',return_value={'id':1,'is_admin':True}), \
                patch.object(app,'DatabaseConnection',self.database), patch.object(app,'log_api_call'), \
                patch.object(enrichment,'get_enrichable_permit_contacts',return_value=[]), \
                patch.object(enrichment,'enrich_permit_contact') as lookup:
            response = client.post('/api/properties/export-with-enrichment?fields=owner_address')
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        row = next(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
        self.assertEqual(row['Owner Name'],'Jordan Davis')
        self.assertEqual(row['Reported Owner Mailing Address'],'10 Example St, Albany, NY, 12201')
        self.assertEqual(row['Owner Address Source'],'ACRIS deed grantee mailing address')
        lookup.assert_not_called()

    def test_hpd_version_refresh_saves_contact_pairs_once_and_honors_failures(self):
        with self.conn.cursor() as cur:
            cur.execute("""INSERT INTO building_source_refresh(building_id,source,next_attempt_at,owner_dates_version)
                VALUES(1,'hpd',NOW()+INTERVAL '14 days',1)""")
        self.conn.commit()
        contacts = [dict(name='Jordan Davis', address='10 Example St', registration_id='55', role='IndividualOwner')]
        with patch.object(property_api, 'get_hpd_data_for_bbl', return_value=(dict(
                owner_name_hpd='Jordan Davis', hpd_owner_contacts=contacts),None)) as fetch:
            refresh.refresh_property_sources(self.conn,1,'3012980066',sources=['hpd'])
            refresh.refresh_property_sources(self.conn,1,'3012980066',sources=['hpd'])
            self.assertEqual(fetch.call_count,1)
        with self.conn.cursor() as cur:
            cur.execute('SELECT hpd_owner_contacts FROM buildings')
            self.assertEqual(cur.fetchone()[0],contacts)
            cur.execute("UPDATE building_source_refresh SET owner_dates_version=1,error='offline'")
        self.conn.commit()
        with patch.object(property_api,'get_hpd_data_for_bbl') as fetch:
            refresh.refresh_property_sources(self.conn,1,'3012980066',sources=['hpd'])
            fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
