"""Enrichment integration tests: isolated PostgreSQL, no production/API calls."""
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from psycopg2.extras import Json

import prospecting_tests as fixtures
import prospecting_service as s
import prospecting_workflows as w
import prospecting_enrichment as e
import prospecting_research as r


@unittest.skipUnless(fixtures.os.getenv('PROSPECTING_TEST_DATABASE_URL'), 'Disposable PostgreSQL not configured')
class EnrichmentTests(unittest.TestCase):
    setUp = fixtures.DatabaseTests.setUp
    tearDown = fixtures.DatabaseTests.tearDown
    connect = fixtures.DatabaseTests.connect
    create = fixtures.DatabaseTests.create
    count = fixtures.DatabaseTests.count

    def job(self, mode='internal', **kwargs):
        return e.start(fixtures.REP, self.list_id, dict(mode=mode,request_key=str(uuid.uuid4()),**kwargs))['job_id']

    def drain(self):
        for _ in range(30):
            if not e.process_one():return
        self.fail('Queue did not drain')

    def result(self, job):
        return e.results(fixtures.REP, self.list_id, job)

    def contact(self, user=2, name='Jane Doe', company='Example', email='new@example.test', dnc=False):
        with s.transaction() as cur:
            cur.execute("""INSERT INTO crm_contacts(name,company,email,title,team_id,added_by_id,assigned_to_id,do_not_contact)
                VALUES (%s,%s,%s,'Manager',1,%s,%s,%s) RETURNING id""", (name,company,email,user,user,dnc))
            return cur.fetchone()['id']

    def public_tables(self):
        with s.transaction() as cur:
            cur.execute('CREATE TABLE buildings(id INTEGER PRIMARY KEY,bbl TEXT,address TEXT,current_owner_name TEXT,owner_name_hpd TEXT,owner_name_rpad TEXT,sale_buyer_primary TEXT,hpd_agent_name TEXT)')
            cur.execute("INSERT INTO buildings VALUES(101,'3012980066','123 Main St','Example LLC',NULL,NULL,NULL,'Agent Person')")
            cur.execute('CREATE TABLE user_enrichments(user_id INTEGER,building_id INTEGER,owner_name_searched TEXT,enriched_phones JSONB,enriched_emails JSONB,enriched_at TIMESTAMP)')
            cur.execute("INSERT INTO user_enrichments VALUES(3,101,'Jane Doe','[{\"number\":\"2125550198\"}]','[]',NOW()),(2,101,'Jane Doe','[{\"number\":\"2125550199\"}]','[]',NOW())")

    def test_schema_idempotent(self):
        fixtures.crm.init_crm_tables()
        self.public_tables(); fixtures.crm.init_crm_tables(); fixtures.crm.init_crm_tables()

    def test_no_writes_before_approval_and_safe_defaults(self):
        self.contact();job=self.job();self.drain()
        result=self.result(job)
        self.assertEqual(result['counts'], {'found':1,'needs_review':1})
        self.assertEqual(s.list_rows(fixtures.REP,self.list_id)['rows'],self.rows)
        item=result['items'][0];email=next(f for f in item['result']['findings'] if f.get('field')=='email')
        self.assertTrue(email['conflict']);self.assertFalse(email['default_selected'])
        title=next(f for f in item['result']['findings'] if f.get('field')=='title')
        self.assertEqual(title['kind'],'research') # Unmapped fields become notes.
        self.assertTrue(title['default_selected'])
        self.assertEqual(self.count('crm_contacts'),1)

    def test_approve_selected_preserves_source_and_undo(self):
        self.contact();job=self.job();self.drain();item=self.result(job)['items'][0]
        email=next(f for f in item['result']['findings'] if f.get('field')=='email')
        data={'items':[{'id':item['id'],'selected':[email['index']]}]}
        saved=e.approve(fixtures.REP,self.list_id,job,data)
        row=s.list_rows(fixtures.REP,self.list_id)['rows'][0]
        self.assertEqual(row['cells']['c2'],'new@example.test');self.assertEqual(row['original_cells'],self.rows[0]['original_cells'])
        self.assertIn('/crm/contacts/',str(row['research']));self.assertNotIn('Manager',str(row['research']))
        self.assertEqual(row['notes'],'')
        self.assertIn('new@example.test',s.export_rows(fixtures.REP,self.list_id))
        self.assertEqual(e.approve(fixtures.REP,self.list_id,job,data)['approved'],0)
        self.assertEqual(row,s.list_rows(fixtures.REP,self.list_id)['rows'][0])
        w.undo(fixtures.REP,self.list_id,saved['change_id'])
        restored=s.list_rows(fixtures.REP,self.list_id)['rows'][0]
        self.assertEqual(restored['cells'],self.rows[0]['cells']);self.assertEqual(restored['notes'],'')
        self.assertEqual(restored['research'],[])

    def test_no_automatic_identity_from_shared_company(self):
        self.contact(name='Different Person');job=self.job();self.drain()
        items=self.result(job)['items']
        self.assertEqual(self.result(job)['counts'], {'needs_review':2})
        self.assertTrue(all(not f['default_selected'] and f['kind']=='research' for i in items for f in i['result']['findings']))

    def test_ambiguous_identity_has_no_field_changes(self):
        self.contact();self.contact();job=self.job();self.drain()
        first=self.result(job)['items'][0]
        self.assertEqual(first['status'],'needs_review')
        self.assertTrue(all(f['kind']=='research' and not f['default_selected'] for f in first['result']['findings']))

    def test_private_crm_and_pending_runs_are_not_shared(self):
        self.contact(user=3);job=self.job();self.drain()
        self.assertEqual(self.result(job)['counts'], {'not_found':2})
        for ctx in (fixtures.OTHER_REP, fixtures.OUTSIDER, fixtures.ADMIN):
            with self.assertRaises(LookupError):e.results(ctx,self.list_id,job)

    def test_private_unlocked_values_only(self):
        self.public_tables()
        result=r.research(fixtures.REP,dict(name='Jane Doe',company='',address='3012980066',phone=''), 'internal', lambda k,f:f())
        phones=[f['value'] for f in result['findings'] if f.get('field')=='phone']
        self.assertEqual(phones,['2125550199'])
        self.assertNotIn('2125550198',str(result))
        self.assertTrue(any('managing agent' in f['label'] for f in result['findings']))

    def test_reassigned_crm_redacted_and_cannot_be_approved(self):
        contact=self.contact();job=self.job();self.drain();item=self.result(job)['items'][0]
        with s.transaction() as cur:cur.execute('UPDATE crm_contacts SET assigned_to_id=3 WHERE id=%s',(contact,))
        item_after=self.result(job)['items'][0]
        self.assertEqual(item_after['result']['findings'],[])
        with self.assertRaises(s.Conflict):e.approve(fixtures.REP,self.list_id,job,{'items':[{'id':item['id'],'selected':[0]}]})

    def test_stale_rows_block_atomic_approval(self):
        self.contact();job=self.job();self.drain();item=self.result(job)['items'][0]
        s.update_row(fixtures.REP,self.rows[0]['id'],{'version':1,'notes':'My newer research'})
        with self.assertRaises(s.Conflict):e.approve(fixtures.REP,self.list_id,job,{'items':[{'id':item['id'],'selected':[0]}]})
        self.assertEqual(s.list_rows(fixtures.REP,self.list_id)['rows'][0]['notes'],'My newer research')
        self.assertIsNone(self.result(job)['items'][0]['reviewed_at'])

    def test_reassignment_stops_worker_and_revokes_results(self):
        job=self.job();s.assign_list(fixtures.ADMIN,self.list_id,assigned_to_id=3,version=1);self.drain()
        with self.assertRaises(LookupError):self.result(job)
        with s.transaction() as cur:
            cur.execute('SELECT DISTINCT status FROM prospect_enrichment_items WHERE job_id=%s',(job,))
            self.assertEqual([row['status'] for row in cur.fetchall()],['skipped'])

    def test_queue_retry_idempotence_and_cancel(self):
        data=dict(mode='internal',request_key=str(uuid.uuid4()))
        job=e.start(fixtures.REP,self.list_id,data)['job_id']
        self.assertEqual(e.start(fixtures.REP,self.list_id,data)['job_id'],job)
        with self.assertRaises(s.Conflict):self.job()
        e.cancel(fixtures.REP,self.list_id,job)
        self.assertFalse(e.process_one());self.assertEqual(self.result(job)['counts'],{'cancelled':2})

    def test_expired_claim_recovered_and_exhaustion_visible(self):
        job=self.job()
        with s.transaction() as cur:
            cur.execute("UPDATE prospect_enrichment_items SET status='running',claim=%s,lease_until=NOW()-INTERVAL '1 minute',attempts=1 WHERE job_id=%s",(str(uuid.uuid4()),job))
        self.drain();self.assertEqual(self.result(job)['counts'],{'not_found':2})
        job=self.job()
        with s.transaction() as cur:
            cur.execute("UPDATE prospect_enrichment_items SET status='running',lease_until=NOW()-INTERVAL '1 minute',attempts=3 WHERE job_id=%s",(job,))
        self.drain();self.assertEqual(self.result(job)['counts'],{'failed':2})

    def test_concurrent_workers_claim_once(self):
        self.contact();job=self.job()
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(list(pool.map(lambda _:e.process_one(), range(2))),[True,True])
        self.assertFalse(e.process_one())
        with s.transaction() as cur:
            cur.execute('SELECT attempts FROM prospect_enrichment_items WHERE job_id=%s',(job,));self.assertEqual([r['attempts'] for r in cur.fetchall()],[1,1])

    def test_unresolved_selection_and_forged_ids(self):
        self.contact();job=self.job();self.drain()
        advanced=self.job('advanced',unresolved_job_id=job)
        self.assertEqual(self.result(advanced)['total'],1)
        e.cancel(fixtures.REP,self.list_id,advanced)
        other=self.create();other_row=s.list_rows(fixtures.REP,other)['rows'][0]
        with self.assertRaises(LookupError):self.job(row_ids=[other_row['id']])

    def test_archived_promoted_dnc_excluded(self):
        s.update_row(fixtures.REP,self.rows[0]['id'],{'version':1,'status':'do_not_contact'})
        job=self.job();self.assertEqual(self.result(job)['total'],1)

    def test_partial_advanced_failure_preserves_success(self):
        self.public_tables()
        with patch.object(r,'_fetch_property',side_effect=lambda source,bbl: {'current_owner_name':'Example LLC'} if source=='pluto' else (_ for _ in ()).throw(RuntimeError('secret'))), \
             patch.object(r,'_fetch_sos',return_value={}),patch.object(r,'_fetch_permits',return_value=[]):
            result=r.research(fixtures.REP,dict(name='',company='',address='3012980066'), 'advanced', lambda k,f:f())
        self.assertTrue(result['matched']);self.assertTrue(result['errors']);self.assertNotIn('secret',str(result))
        self.assertTrue(any(f['value']=='Example LLC' for f in result['findings']))

    def test_geocoder_candidates_are_unchecked_and_sos_agent_not_owner(self):
        with patch.object(r,'_resolve',return_value={'property':{'bbl':'3012980066','address':'123 Main St'},'error':None}), \
             patch.object(r,'_fetch_property',return_value={'current_owner_name':'Example LLC'}),patch.object(r,'_fetch_permits',return_value=[]), \
             patch.object(r,'_fetch_sos',return_value={'entity_name':'Example LLC','dos_id':'123','status':'Active','quality':'exact','people':[{'name':'Jane Agent','role':'Registered Agent'}]}):
            result=r.research(fixtures.REP,dict(name='',company='',address='123 Main'), 'advanced', lambda k,f:f())
        self.assertTrue(all(not f['default_selected'] for f in result['findings']))
        agent=next(f for f in result['findings'] if f['value']=='Jane Agent')
        self.assertIn('not an owner',agent['basis']);self.assertEqual(agent['kind'],'research')

    def test_repeat_properties_cached_within_run(self):
        mock_result={'findings': [],'checks': [],'errors': [],'matched': False}
        calls=[]
        def fake_research(ctx,fields,mode,cache):
            cache('shared-public-record',lambda:calls.append(1) or {'public':'record'})
            return mock_result
        job=self.job('advanced')
        with patch.object(r,'research',side_effect=fake_research):self.drain()
        self.assertEqual(calls,[1]);self.assertEqual(self.result(job)['counts'],{'not_found':2})

    def test_dismiss_is_durable_without_row_mutation(self):
        self.contact();job=self.job();self.drain();item=self.result(job)['items'][0]
        result=e.approve(fixtures.REP,self.list_id,job,{'items':[{'id':item['id'],'selected':[]}]})
        self.assertEqual(result['approved'],0);self.assertIsNone(result['change_id'])
        self.assertEqual(s.list_rows(fixtures.REP,self.list_id)['rows'],self.rows)
        self.assertIsNotNone(self.result(job)['items'][0]['reviewed_at'])

    def test_cancel_fences_a_worker_already_fetching(self):
        job=self.job()
        def fetch(ctx,fields,mode,cache):
            e.cancel(fixtures.REP,self.list_id,job)
            return {'findings':[r.finding('Unexpected','must not save',r.source('Example','https://example.test'),'Synthetic')], 'matched':True}
        with patch.object(r,'research',side_effect=fetch):self.assertTrue(e.process_one())
        self.assertEqual(self.result(job)['counts'],{'cancelled':2})
        self.assertTrue(all(not i['result'].get('findings') for i in self.result(job)['items']))

    def test_api_csrf_scope_and_queue_start(self):
        app=fixtures.Flask(__name__);app.secret_key='test';app.register_blueprint(fixtures.routes.prospecting_bp)
        client=app.test_client();path=f'/crm/prospecting/api/lists/{self.list_id}/enrichment'
        with client.session_transaction() as session:session['prospecting_csrf']='test-token'
        with patch('auth_service.validate_session',return_value={'id':2,'is_sponsored':True,'sponsor_user_id':1}),patch.object(e,'start_worker') as worker:
            data=dict(mode='internal',request_key=str(uuid.uuid4()))
            self.assertEqual(client.post(path,json=data).status_code,403)
            response=client.post(path,json=data,headers={'X-CSRF-Token':'test-token'})
            self.assertEqual(response.status_code,200);worker.assert_called_once()
            job=response.json['job_id'];self.assertEqual(client.get(path).json['job']['id'],job)
        with patch('auth_service.validate_session',return_value={'id':3,'is_sponsored':True,'sponsor_user_id':1}):
            self.assertEqual(client.get(path).status_code,404)
            for suffix in (f'/{job}/cancel',f'/{job}/approve'):
                self.assertEqual(client.post(path+suffix,json={'items':[{'id':1,'selected':[]}]},headers={'X-CSRF-Token':'test-token'}).status_code,404)

    def test_successes_and_failures_cached_independently(self):
        cached={}
        def cache(key,fetch):
            if key not in cached:cached[key]=fetch()
            return cached[key]
        fields=dict(name='',company='Example LLC',address='3012980066')
        with patch.object(r,'_fetch_property',side_effect=RuntimeError('unavailable')) as source, \
             patch.object(r,'_fetch_sos',return_value={}),patch.object(r,'_fetch_permits',return_value=[]):
            first=r.research(fixtures.REP,fields,'advanced',cache)
            second=r.research(fixtures.REP,fields,'advanced',cache)
        self.assertEqual(source.call_count,8);self.assertEqual(first['errors'],second['errors'])

    def test_approved_research_transfers_on_promotion(self):
        self.contact();job=self.job();self.drain();item=self.result(job)['items'][0]
        e.approve(fixtures.REP,self.list_id,job,{'items':[{'id':item['id'],'selected':[0]}]})
        promoted=s.promote(fixtures.REP,self.rows[0]['id'],{'version':2})
        with s.transaction() as cur:
            cur.execute("SELECT note FROM crm_activity WHERE contact_id=%s AND type='note'",(promoted['contact_id'],))
            self.assertTrue(any('Approved research:' in a['note'] and '/crm/contacts/' in a['note'] for a in cur.fetchall()))

    def test_duplicate_addresses_remain_unconfirmed(self):
        self.public_tables()
        with s.transaction() as cur:cur.execute("INSERT INTO buildings(id,bbl,address) VALUES(102,'1012980066','123 Main St')")
        result=r.research(fixtures.REP,dict(name='',company='',address='123 Main St'),'internal',lambda k,f:f())
        self.assertFalse(result['matched']);self.assertTrue(all(not f['default_selected'] for f in result['findings']))

    def test_dnc_crm_candidate_never_supplies_fields(self):
        self.contact(dnc=True);job=self.job();self.drain()
        findings=self.result(job)['items'][0]['result']['findings']
        self.assertTrue(all(f['kind']=='research' and not f['default_selected'] for f in findings))
        self.assertIn('DO NOT CONTACT',findings[0]['value'])

    def test_conflicting_field_sources_need_single_explicit_choice(self):
        self.public_tables();contact=self.contact(email='same@example.test')
        with s.transaction() as cur:
            cur.execute("INSERT INTO crm_phones(contact_id,number,digits,is_primary,status) VALUES(%s,'2125550111','2125550111',true,'good')",(contact,))
        fields=dict(name='Jane Doe',company='Example',address='3012980066',phone='',email='')
        result=r.research(fixtures.REP,fields,'internal',lambda k,f:f())
        phones=[f for f in result['findings'] if f.get('field')=='phone']
        self.assertEqual(len(phones),2);self.assertTrue(all(not f['default_selected'] for f in phones))


class AdapterTests(unittest.TestCase):
    def test_permit_query_is_parcel_scoped_bounded_and_checks_returned_bbl(self):
        from unittest.mock import MagicMock
        import socrata_client
        client=MagicMock()
        client.get_columns.return_value={'borough','block','lot','issued_date'}
        client.get.return_value=[{'borough':'Brooklyn','block':'1298','lot':'66','job_filing_number':'B12345678-I1','issued_date':'2026-09-01','owner_business_name':'Example'},
                                 {'borough':'Manhattan','block':'1298','lot':'66','job_filing_number':'M12345678'}]
        with patch.object(socrata_client,'SocrataClient',return_value=client):
            records=r._fetch_permits('dob_now_permits','3012980066')
        self.assertEqual(len(records),1);self.assertEqual(records[0]['job'],'B12345678-I1')
        query=client.get.call_args.kwargs
        self.assertEqual(query['$limit'],20);self.assertIn("block in ('01298','1298')",query['$where'])
        self.assertIn("lot in ('0066','66')",query['$where'])
        self.assertEqual(r.permit_source_link({'job_number':records[0]['job'],'api_source':records[0]['api_source']})['label'],'Open in DOB NOW')


if __name__ == '__main__':unittest.main()
