"""Offline regressions; optional disposable PostgreSQL via ENRICHMENT_TEST_DATABASE_URL.

No production requests, paid providers, or real Stripe calls are made.
"""
import os
os.environ.setdefault('DATABASE_URL', 'postgresql://unused@localhost:1/unused')
import json
import unittest
import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch
import _pipeline_path  # noqa
import psycopg2
from psycopg2.extras import RealDictCursor
import socrata_client as socrata
import step2_enrich_from_pluto as property_api
import step3_enrich_from_acris as acris
import step5_enrich_from_sos as sos
import enrichment_service as contacts
import bulk_enrich_service as bulk
import stripe_service as billing
import property_source_refresh as refresh
import paid_enrichment_store as paid
from sync_dob_safety import aggregate_safety


class SourceRegressionTests(unittest.TestCase):
    def test_cap_rejects_incomplete_pages_but_accepts_exact_complete_limit(self):
        client = socrata.SocrataClient()
        with patch.object(client,'get',side_effect=[[{'id':1},{'id':2}],[{'id':3}]]):
            with self.assertRaises(socrata.SocrataError):
                client.get_all('pluto',page_size=2,max_rows=2)
        with patch.object(client,'get',side_effect=[[{'id':1},{'id':2}],[]]):
            self.assertEqual(len(client.get_all('pluto',page_size=2,max_rows=2)),2)

    def test_batched_lookups_paginate(self):
        client = socrata.SocrataClient()
        with patch.object(client,'get_all',return_value=[{'id':1}]) as fetch:
            self.assertEqual(client.get_batched('acris_parties','document_id',['a','a']),[{'id':1}])
            self.assertEqual(fetch.call_count,1)

    def test_hpd_complaint_outage_is_not_zero(self):
        client = Mock()
        client.get.return_value=[]
        client.get_all.side_effect=[[],socrata.SocrataError('offline')]
        with patch.object(property_api,'_get_client',return_value=client), patch.object(property_api.time,'sleep'):
            result,error=property_api.get_hpd_data_for_bbl('3012980066')
        self.assertIsNone(result)
        self.assertIn('complaints',error)

    def test_hpd_without_registration_still_checks_complaints(self):
        client=Mock();client.get.return_value=[];client.get_all.side_effect=[[],[]]
        with patch.object(property_api,'_get_client',return_value=client),patch.object(property_api.time,'sleep'):
            result,error=property_api.get_hpd_data_for_bbl('3012980066')
        self.assertIsNone(error)
        self.assertEqual(result['hpd_total_complaints'],0)
        self.assertEqual(client.get_all.call_count,2)

    def test_current_individual_does_not_resurrect_previous_company(self):
        self.assertEqual(sos.get_best_llc_name({'sale_buyer_primary':'JOHN SMITH',
                         'current_owner_name':'PREVIOUS OWNER LLC'}),(None,''))

    def test_deed_owner_blocks_former_company_principal(self):
        quality,_=contacts.owner_entity_match_quality({'sale_buyer_primary':'John Smith',
            'current_owner_name':'FORMER LLC','sos_entity_name':'FORMER LLC'})
        self.assertEqual(quality,'mismatch')

    def test_apify_name_and_zip_without_street_is_not_verified(self):
        candidate={'First Name':'John','Last Name':'Smith','Street Address':'999 Other St',
                   'Address Locality':'Brooklyn','Address Region':'NY','Postal Code':'11201'}
        self.assertIsNone(contacts._evaluate_apify_item(candidate,'John','Smith','123 Main St','Brooklyn','NY','11201'))

    def test_safety_snapshot_aggregates_and_rejects_empty(self):
        self.assertEqual(aggregate_safety([
            {'bbl':'3012980066','violation_status':'Active','violation_count':'2'},
            {'bbl':'3012980066','violation_status':'Dismissed','violation_count':'3'}]),
            [('3012980066',5,2)])
        with self.assertRaises(socrata.SocrataError): aggregate_safety([])

    def test_enformion_requires_matching_person_and_street(self):
        response={'person':{'name':{'firstName':'John','lastName':'Smith'},
                            'addresses':[{'street':'123 Main Street','city':'Brooklyn','state':'NY','zip':'11201'}]}}
        args=('John','Smith','123 Main St','Brooklyn, NY 11201')
        self.assertTrue(contacts.verify_enformion_identity(response,*args))
        response['person']['name']['firstName']='James'
        self.assertFalse(contacts.verify_enformion_identity(response,*args))
        response['person']['name']['firstName']='John'
        response['person']['addresses'][0]['street']='999 Other Street'
        self.assertFalse(contacts.verify_enformion_identity(response,*args))
        self.assertFalse(contacts.verify_enformion_identity({},*args))

    def test_acris_reference_failure_stops_refresh(self):
        client=Mock()
        def fetch(dataset,*args,**kwargs):
            if dataset=='acris_master':
                return [{'document_id':'d1','crfn':'123','doc_type':'DEED'}]
            if dataset=='acris_references': raise socrata.SocrataError('references offline')
            return []
        client.get_batched.side_effect=fetch
        client.get_columns.return_value={'document_id','crfn'}
        with patch.object(acris,'get_client',return_value=client),patch.object(acris,'get_document_ids_for_bbl',return_value=['d1']):
            with self.assertRaises(socrata.SocrataError): acris.get_acris_full_history('3012980066')

    def test_nominal_and_partial_transfers_are_not_cash_purchases(self):
        for amount,percent,expected in [(10,100,None),(100000,50,None),(100000,100,True)]:
            deed={'doc_type':'DEED','doc_amount':amount,'doc_date':date(2025,1,1),
                  'recorded_date':date(2025,1,2),'crfn':'123','percent_transferred':percent,
                  'buyers':[],'sellers':[],'document_id':'d1'}
            cursor=Mock()
            acris.update_buildings_table(cursor,1,[deed],deed,None,[])
            params=cursor.execute.call_args_list[0].args[1]
            self.assertIs(params[11],expected)


class PaymentRouteTests(unittest.TestCase):
    def test_single_owner_payment_failure_does_not_grant_or_return_contacts(self):
        import app
        import inspect
        with app.app.test_request_context('/api/enrichment/enrich',method='POST',json={'building_id':1,'owner_name':'John Smith'}), \
                patch.object(contacts,'check_user_enrichment_access',return_value=(False,[],[])), \
                patch.object(contacts,'get_available_owners_for_enrichment',return_value=[{'name':'John Smith'}]), \
                patch.object(contacts,'enrich_owner',return_value=(True,{'phones':['private']},'found')), \
                patch.object(billing,'ensure_usage_billing_ready',return_value=(True,'ready',{})), \
                patch.object(billing,'charge_enrichment_fee',return_value=(False,'declined',None)), \
                patch.object(paid,'grant_owner_access') as grant:
            app.g.user={'id':1,'is_admin':False,'should_charge_usage':True}
            response,status=inspect.unwrap(app.api_enrich_owner)()
            self.assertEqual(status,402)
            self.assertNotIn('private',response.get_data(as_text=True))
            grant.assert_not_called()

    def test_permit_route_rejects_names_not_in_property_source(self):
        import app
        import inspect
        with app.app.test_request_context('/api/enrichment/permit-contact',method='POST',json={'bbl':'3012980066','contact_name':'John Smith'}), \
                patch.object(contacts,'get_enrichable_permit_contacts',return_value=[]), \
                patch.object(contacts,'enrich_permit_contact') as lookup:
            app.g.user={'id':1,'is_admin':False}
            response,status=inspect.unwrap(app.api_enrich_permit_contact)()
            self.assertEqual(status,400)
            lookup.assert_not_called()


@unittest.skipUnless(os.getenv('ENRICHMENT_TEST_DATABASE_URL'),'disposable PostgreSQL DSN not supplied')
class DatabaseRegressionTests(unittest.TestCase):
    def connect(self, real_dict=False):
        return psycopg2.connect(os.environ['ENRICHMENT_TEST_DATABASE_URL'],
                               options=f'-c search_path={self.schema}',
                               cursor_factory=RealDictCursor if real_dict else psycopg2.extensions.cursor)

    def setUp(self):
        self.schema='audit_'+uuid.uuid4().hex
        self.admin=psycopg2.connect(os.environ['ENRICHMENT_TEST_DATABASE_URL']);self.admin.autocommit=True
        with self.admin.cursor() as cur:cur.execute(f'CREATE SCHEMA {self.schema}')
        self.conn=self.connect()
        fields=set(refresh.PLUTO_FIELDS+refresh.HPD_FIELDS+['owner_name_rpad','assessed_land_value',
            'assessed_total_value','current_owner_name','address','bin','latitude','longitude','zip_code',
            'borough','sale_buyer_primary','sos_principal_name','sos_principal_street',
            'sos_principal_city','sos_principal_state','sos_principal_zip'])
        with self.conn.cursor() as cur:
            cur.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,is_admin BOOLEAN DEFAULT FALSE)')
            cur.execute('CREATE TABLE buildings(id INTEGER PRIMARY KEY,bbl TEXT,property_last_attempted TIMESTAMP,property_last_enriched TIMESTAMP,property_last_error TEXT,'+','.join(f'{f} TEXT' for f in sorted(fields))+')')
            cur.execute("INSERT INTO users VALUES(1,FALSE)")
            cur.execute("INSERT INTO buildings(id,bbl,current_owner_name,owner_name_rpad,address,borough,zip_code) VALUES(1,'3012980066','OLD LLC','FORMER OWNER','123 MAIN ST','3','11201')")
            cur.execute('''CREATE TABLE user_enrichments(user_id INTEGER,building_id INTEGER,
                owner_name_searched TEXT,enriched_phones JSONB,enriched_emails JSONB,
                enriched_person_id TEXT,enriched_at TIMESTAMPTZ,raw_api_response JSONB,
                UNIQUE(user_id,building_id,owner_name_searched))''')
            cur.execute(refresh.SCHEMA_SQL);cur.execute(paid.SCHEMA_SQL)
        self.conn.commit()
        self.patches=[patch.object(contacts,'get_db_connection',side_effect=lambda:self.connect(True)),
                      patch.object(bulk,'_get_conn',side_effect=self.connect)]
        for p in self.patches:p.start()
        bulk.init_bulk_enrich_jobs_table()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.conn.close()
        with self.admin.cursor() as cur:cur.execute(f'DROP SCHEMA {self.schema} CASCADE')
        self.admin.close()

    def scalar(self,sql):
        self.conn.rollback()
        with self.conn.cursor() as cur:
            cur.execute(sql);return cur.fetchone()[0]

    def lookup(self,job=None):
        with patch.object(contacts,'_best_owner_search_location',return_value=(('123 Main St','Brooklyn','NY','11201'),'property')),patch.object(contacts,'call_enformion_api',return_value=(True,{'person':{}},None)),patch.object(contacts,'extract_contact_info',return_value=([{'number':'5551234'}],[], 'person1')):
            return contacts.enrich_owner(1,'John Smith','',1,provider='enformion',bulk_job_id=job)

    def test_outage_preserves_data_and_healthy_sources_keep_own_schedule(self):
        with patch.object(property_api,'get_pluto_data_for_bbl',return_value=({'owner_name':'NEW LLC'},None)),patch.object(property_api,'get_rpad_data_for_bbl',return_value=(None,'source unavailable')),patch.object(property_api,'get_hpd_data_for_bbl',return_value=(None,None)):
            report=refresh.refresh_property_sources(self.conn,1,'3012980066')
        self.assertIn('error:',report['rpad'])
        self.assertEqual(self.scalar('SELECT owner_name_rpad FROM buildings'),'FORMER OWNER')
        self.assertEqual(self.scalar('SELECT current_owner_name FROM buildings'),'NEW LLC')
        self.assertIsNone(self.scalar('SELECT property_last_enriched FROM buildings'))
        with patch.object(property_api,'get_pluto_data_for_bbl',side_effect=AssertionError('healthy source re-fetched')):
            report=refresh.refresh_property_sources(self.conn,1,'3012980066',sources=['pluto','rpad'])
        self.assertEqual(report['pluto'],'current')
        self.assertIn('error:',report['rpad'])

    def test_authoritative_empty_clears_old_facts_preserves_address(self):
        with patch.object(property_api,'get_pluto_data_for_bbl',return_value=(None,None)):
            refresh.refresh_property_sources(self.conn,1,'3012980066',sources=['pluto'])
        self.assertIsNone(self.scalar('SELECT current_owner_name FROM buildings'))
        self.assertEqual(self.scalar('SELECT address FROM buildings'),'123 MAIN ST')
        self.assertEqual(self.scalar('SELECT count(checked_at) FROM building_source_refresh'),1)

    def test_lookup_does_not_unlock_until_receipt_is_saved(self):
        self.assertTrue(self.lookup()[0])
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM user_enrichments'),0)
        with patch.object(contacts,'call_enformion_api',side_effect=AssertionError('paid provider called twice')):
            self.assertTrue(contacts.enrich_owner(1,'JOHN SMITH','',1)[0])
        paid.grant_owner_access(1,1,'John Smith','pi_confirmed')
        paid.grant_owner_access(1,1,'John Smith','pi_confirmed')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM user_enrichments'),1)

    def test_pending_result_cannot_be_billed_by_another_job(self):
        self.assertTrue(self.lookup(job=123)[0])
        self.assertFalse(contacts.enrich_owner(1,'John Smith','',1,bulk_job_id=124)[0])

    def test_payment_receipt_reused_after_idempotency_window(self):
        conn=self.connect(True)
        try:
            with conn.cursor() as cur,patch.object(billing.stripe.PaymentIntent,'create',return_value=SimpleNamespace(id='pi_ok',status='succeeded')) as charge:
                args={'idempotency_key':'test-payment','amount':50}
                first=billing._create_enrichment_payment(cur,conn,**args)
                cur.execute("UPDATE enrichment_payment_attempts SET started_at=NOW()-INTERVAL '2 days'");conn.commit()
                second=billing._create_enrichment_payment(cur,conn,**args)
                self.assertEqual(first.id,second.id);self.assertEqual(charge.call_count,1)
        finally:conn.close()

    def test_unknown_old_payment_is_not_charged_again(self):
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO enrichment_payment_attempts VALUES('old','{}',NOW()-INTERVAL '2 days',NULL)")
        self.conn.commit();conn=self.connect(True)
        try:
            with conn.cursor() as cur,patch.object(billing.stripe.PaymentIntent,'create') as charge:
                with self.assertRaises(ValueError):billing._create_enrichment_payment(cur,conn,idempotency_key='old',amount=50)
                charge.assert_not_called()
        finally:conn.close()

    def test_bulk_recovers_saved_lookup_then_payment_without_repeating_either(self):
        job_id=bulk.create_job(1,{},[1],1,0.5,0.35,False,'recommended')
        real_enrich=contacts.enrich_owner
        def lookup(*args,**kwargs):return self.lookup(job=job_id)
        with patch.object(contacts,'get_available_owners_for_enrichment',return_value=[{'name':'John Smith'}]),patch.object(contacts,'enrich_owner',side_effect=lookup),patch.object(billing,'charge_batch_enrichment_total',return_value=(False,'network interrupted',None)):
            # self.lookup calls the real function to avoid recursion.
            with patch.object(self,'lookup',side_effect=lambda job=None:self._raw_lookup(real_enrich,job)):
                bulk._run_job(job_id)
        self.assertEqual(bulk.get_job(job_id)['status'],'charging')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM user_enrichments'),0)
        with patch.object(contacts,'enrich_owner',side_effect=AssertionError('lookup repeated')),patch.object(billing,'charge_batch_enrichment_total',return_value=(True,'paid','pi_bulk')) as charge,patch.object(paid,'grant_result',side_effect=RuntimeError('crash after receipt')):
            with self.assertRaises(RuntimeError):bulk._run_job(job_id)
            self.assertEqual(charge.call_count,1)
        with patch.object(contacts,'enrich_owner',side_effect=AssertionError('lookup repeated')),patch.object(billing,'charge_batch_enrichment_total',side_effect=AssertionError('charge repeated')):
            bulk._run_job(job_id)
        self.assertEqual(bulk.get_job(job_id)['status'],'completed')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM user_enrichments'),1)

    def test_source_lock_prevents_duplicate_refresh(self):
        with self.conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_lock(72102,1)')
        self.conn.commit()
        other=self.connect()
        try:
            with patch.object(property_api,'get_pluto_data_for_bbl') as lookup:
                report=refresh.refresh_property_sources(other,1,'3012980066',sources=['pluto'])
                lookup.assert_not_called()
                self.assertIn('running',report['property'])
        finally:other.close()

    def test_expired_property_lease_is_not_stolen_from_live_worker(self):
        import property_lookup
        with self.conn.cursor() as cur:
            cur.execute("""CREATE TABLE property_enrichment_jobs(id BIGSERIAL PRIMARY KEY,
                building_id INTEGER,bbl TEXT,status TEXT,attempts INTEGER DEFAULT 0,
                available_at TIMESTAMP,locked_at TIMESTAMP,last_error TEXT,
                updated_at TIMESTAMP,created_at TIMESTAMP DEFAULT NOW())""")
            cur.execute("""INSERT INTO property_enrichment_jobs(building_id,bbl,status,locked_at)
                VALUES(1,'3012980066','running',NOW()-INTERVAL '1 hour')""")
            cur.execute("SELECT pg_advisory_lock(hashtextextended('1',72105))")
        self.conn.commit()
        self.assertEqual(property_lookup.process_queued_enrichment_jobs(self.connect),0)
        self.assertEqual(self.scalar('SELECT status FROM property_enrichment_jobs'),'running')

    def test_first_permit_request_does_not_grant_access(self):
        with self.conn.cursor() as cur:
            cur.execute("""CREATE TABLE permit_contact_enrichments(id INTEGER,bbl TEXT,
                contact_name TEXT,contact_type TEXT,enriched_phones JSONB,enriched_emails JSONB,
                first_enriched_by INTEGER,first_enriched_at TIMESTAMP)""")
            cur.execute("""CREATE TABLE user_permit_contact_unlocks(id SERIAL,user_id INTEGER,
                enrichment_id INTEGER,charge_amount NUMERIC,stripe_charge_id TEXT,
                UNIQUE(user_id,enrichment_id))""")
            cur.execute("""INSERT INTO permit_contact_enrichments VALUES
                (1,'3012980066','John Smith','owner','["private"]','[]',1,NOW())""")
        self.conn.commit()
        self.assertFalse(contacts.check_permit_contact_enrichment('3012980066','John Smith','owner',1)[2])
        self.assertTrue(contacts.grant_permit_contact_access(1,1,0.5,'pi_paid')[0])
        self.assertTrue(contacts.check_permit_contact_enrichment('3012980066','John Smith','owner',1)[2])

    def test_schema_migration_is_repeatable(self):
        with self.conn.cursor() as cur:
            cur.execute('ALTER TABLE buildings ADD COLUMN is_cash_purchase BOOLEAN NOT NULL DEFAULT FALSE, ADD COLUMN dob_complaint_count INTEGER NOT NULL DEFAULT 0, ADD COLUMN dob_active_complaint_count INTEGER NOT NULL DEFAULT 0')
            for _ in range(2):
                cur.execute(refresh.SCHEMA_SQL)
                cur.execute(refresh.BUILDING_COLUMNS_SQL)
                cur.execute(paid.SCHEMA_SQL)
            cur.execute('UPDATE buildings SET is_cash_purchase=NULL,dob_complaint_count=NULL')
        self.conn.commit()
        self.assertIsNone(self.scalar('SELECT is_cash_purchase FROM buildings'))

    def test_derived_flag_repair_preserves_source_values_and_is_idempotent(self):
        from repair_enrichment_flags import repair
        with self.conn.cursor() as cur:
            cur.execute("""ALTER TABLE buildings ADD COLUMN is_cash_purchase BOOLEAN,
                ADD COLUMN sale_price NUMERIC,ADD COLUMN sale_percent_transferred NUMERIC,
                ADD COLUMN has_tax_delinquency BOOLEAN,ADD COLUMN tax_delinquency_latest_date DATE""")
            cur.execute("""UPDATE buildings SET is_cash_purchase=TRUE,sale_price=10,
                sale_percent_transferred=100,has_tax_delinquency=TRUE,
                tax_delinquency_latest_date=CURRENT_DATE-INTERVAL '2 years'""")
        self.conn.commit()
        self.assertEqual(repair(self.conn),{'unsupported_cash':1,'stale_lien_notice':1})
        self.assertTrue(self.scalar('SELECT is_cash_purchase FROM buildings'))
        self.assertEqual(repair(self.conn,True),{'unsupported_cash':1,'stale_lien_notice':1})
        self.assertEqual(self.scalar('SELECT sale_price FROM buildings'),10)
        self.assertEqual(repair(self.conn,True),{'unsupported_cash':0,'stale_lien_notice':0})

    def _raw_lookup(self,real_enrich,job):
        with patch.object(contacts,'_best_owner_search_location',return_value=(('123 Main St','Brooklyn','NY','11201'),'property')),patch.object(contacts,'call_enformion_api',return_value=(True,{},None)),patch.object(contacts,'extract_contact_info',return_value=([{'number':'5551234'}],[],'person1')):
            return real_enrich(1,'John Smith','',1,provider='enformion',bulk_job_id=job)


if __name__=='__main__':unittest.main(verbosity=2)
