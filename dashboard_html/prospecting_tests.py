"""Run with PROSPECTING_TEST_DATABASE_URL pointing to a disposable PostgreSQL.
Each database test owns an isolated schema; no production app import or .env use.
"""
import csv
import io
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask

import crm_service as crm
import prospecting_service as s
import prospecting_routes as routes
import prospecting_workflows as w
import prospecting_imports as imports
import prospecting_network as network
from crm_routes import crm_bp

SAMPLE = b'Contact Name,Company,Email,Phone,Extra\nJane Doe,Example,jane@example.test,212-555-0100,=1+1\nJohn Smith,Example,john@example.test,212-555-0100,https://example.test\n'
ADMIN = dict(user_id=1,team_id=1,is_admin=True)
REP = dict(user_id=2,team_id=1,is_admin=False)
OTHER_REP = dict(user_id=3,team_id=1,is_admin=False)
OUTSIDER = dict(user_id=4,team_id=4,is_admin=True)


class CsvTests(unittest.TestCase):
    def test_api_serializes_naive_and_aware_timestamps(self):
        self.assertEqual(routes._serial(datetime.fromisoformat('2026-09-23T10:00:00-04:00')), '2026-09-23T14:00:00Z')
        self.assertEqual(routes._serial(datetime(2026,9,23,14,0)), '2026-09-23T14:00:00Z')

    def test_bom_and_aliases(self):
        parsed = s.parse_csv(b'\xef\xbb\xbfbest_contact,manager_or_operator,normalized_address\r\nJane Doe,Example,123 Main St\r\n')
        self.assertEqual(parsed['mapping']['name'], 'c0')
        self.assertEqual(parsed['mapping']['company'], 'c1')
        self.assertEqual(parsed['mapping']['address'], 'c2')

    def test_encodings_and_delimiters(self):
        for delimiter in [',',';','\t','|']:
            for encoding in ['utf-8-sig','utf-16','cp1252']:
                with self.subTest(delimiter=delimiter,encoding=encoding):
                    raw = f'Name{delimiter}Company\nJos\u00e9 Smith{delimiter}Example\n'.encode(encoding)
                    parsed = s.parse_csv(raw)
                    self.assertEqual(parsed['rows'][0]['c0'], 'Jos\u00e9 Smith')
                    self.assertEqual(parsed['mapping']['company'], 'c1')

    def test_excel_hint_quotes_and_multiline(self):
        p = s.parse_csv(b'sep=;\r\nName;Notes\r\n"Doe, Jane";"First line\nSecond line"\r\n')
        self.assertEqual(p['rows'][0]['c1'], 'First line\nSecond line')
        self.assertEqual(p['row_count'],1)

    def test_duplicate_and_blank_headers_and_ragged_rows(self):
        p = s.parse_csv(b'Name,Name,\nA,B,C,D\nE\n',delimiter=',')
        self.assertEqual([c['label'] for c in p['columns']],['Name','Name','Column 3','Column 4'])
        self.assertEqual(p['rows'][0]['c3'],'D')
        self.assertEqual(p['rows'][1]['c3'],'')

    def test_headerless_preserves_first_row(self):
        p = s.parse_csv(b'Jane,00123\nJohn,00234\n',has_header=False)
        self.assertEqual(p['row_count'],2)
        self.assertEqual(p['rows'][0]['c1'],'00123')

    def test_duplicates_reported_never_silently_removed(self):
        p=s.parse_csv(b'Name\nJane\nJane\n\n')
        self.assertEqual(p['row_count'],2)
        self.assertEqual(p['duplicate_count'],1)

    def test_invalid_files_and_limits(self):
        for content in [b'',b'Name\n',b'Name\n"unclosed',b'\x00binary',b'PK\x03\x04fake',b'x'*(s.MAX_BYTES+1),b'Name\n'+b'x'*(s.MAX_CELL+1)]:
            with self.subTest(length=len(content)):
                with self.assertRaises(ValueError):s.parse_csv(content)
        with self.assertRaises(ValueError):s.parse_csv(b'a,'*101+b'\nx,\n')
        with self.assertRaises(ValueError):s.parse_csv(b'Name\n'+b'A\n'*(s.MAX_ROWS+1))

    def test_mapping_rejects_unknown_and_non_string_columns(self):
        p=s.parse_csv(SAMPLE)
        for mapping in [{'name':'missing'},{'name':[]},{'bogus':'c1'}]:
            with self.assertRaises(ValueError):s.validate_mapping(mapping,p['columns'])


@unittest.skipUnless(os.getenv('PROSPECTING_TEST_DATABASE_URL'),'Disposable PostgreSQL not configured')
class DatabaseTests(unittest.TestCase):
    def connect(self):
        return psycopg2.connect(os.environ['PROSPECTING_TEST_DATABASE_URL'],options=f'-c search_path={self.schema}',cursor_factory=RealDictCursor)

    def setUp(self):
        self.schema='prospect_test_'+uuid.uuid4().hex
        self.admin=psycopg2.connect(os.environ['PROSPECTING_TEST_DATABASE_URL']);self.admin.autocommit=True
        with self.admin.cursor() as cur:cur.execute(f'CREATE SCHEMA {self.schema}')
        self.db_patch=patch.object(crm,'get_db_connection',side_effect=self.connect);self.db_patch.start()
        with s.transaction() as cur:
            cur.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,email TEXT,is_admin BOOLEAN,last_login TIMESTAMP)')
            cur.execute("INSERT INTO users VALUES(1,'admin@example.test',true,NULL),(2,'rep@example.test',false,NULL),(3,'other@example.test',false,NULL),(4,'outsider@example.test',true,NULL)")
            cur.execute('CREATE TABLE account_sponsorships(sponsor_user_id INTEGER,member_user_id INTEGER,status TEXT,display_name TEXT,accepted_at TIMESTAMP)')
            cur.execute("INSERT INTO account_sponsorships VALUES(1,2,'active','Rep One',NOW()),(1,3,'active','Rep Two',NOW())")
        crm.init_crm_tables()
        self.parsed=s.parse_csv(SAMPLE)
        self.list_id=self.create()
        self.rows=s.list_rows(REP,self.list_id)['rows']

    def tearDown(self):
        self.db_patch.stop()
        with self.admin.cursor() as cur:cur.execute(f'DROP SCHEMA {self.schema} CASCADE')
        self.admin.close()

    def create(self, ctx=REP, parsed=None, import_key=None, assigned_to_id=None):
        p=parsed or self.parsed
        return s.create_list(ctx,p,name='Test list',filename='test.csv',mapping=p['mapping'],import_key=import_key or str(uuid.uuid4()),assigned_to_id=assigned_to_id)

    def count(self,table):
        with s.transaction() as cur:
            cur.execute(f'SELECT COUNT(*) AS n FROM {table}');return cur.fetchone()['n']

    def touch(self, row, **overrides):
        return s.add_touch(REP,row['id'],dict(version=row['version'],request_key=str(uuid.uuid4()),method='call',outcome='spoke',note='Interested in access control',occurred_at='2026-01-01T10:30',status='interested',next_follow_up='2026-01-05',**overrides))

    def test_import_is_separate_and_idempotent(self):
        token=str(uuid.uuid4())
        first=self.create(import_key=token)
        self.assertEqual(first,self.create(import_key=token))
        self.assertEqual(self.count('prospect_rows'),4)
        self.assertEqual(self.count('crm_contacts'),0)
        with self.assertRaises(s.Conflict):self.create(import_key=token,parsed=s.parse_csv(b'Name\nDifferent\n'))

    def test_schema_can_run_twice(self):
        crm.init_crm_tables()
        self.assertEqual(self.count('prospect_rows'),2)

    def test_layout_persists_without_changing_leads(self):
        before=s.list_rows(REP,self.list_id)
        layout={'order':list(reversed(before['layout']['order'])),'visible':['c4','c0']}
        s.save_layout(REP,self.list_id,layout)
        after=s.list_rows(REP,self.list_id)
        self.assertEqual(after['layout'],{**layout,'saved':True,'widths':{},'pinned':'lead'})
        self.assertEqual(before['rows'],after['rows'])
        self.assertEqual(before['listing']['version'],after['listing']['version'])
        # All source columns may be hidden while tracking columns stay available.
        layout['visible']=[]
        s.save_layout(REP,self.list_id,layout)
        self.assertEqual(s.list_rows(REP,self.list_id)['layout']['visible'],[])

    def test_layout_is_private_per_user_and_per_list(self):
        layout=s.list_rows(REP,self.list_id)['layout']
        layout['order'].reverse()
        s.save_layout(REP,self.list_id,layout)
        self.assertFalse(s.list_rows(ADMIN,self.list_id)['layout']['saved'])
        other=self.create()
        self.assertFalse(s.list_rows(REP,other)['layout']['saved'])
        for ctx in (OTHER_REP,OUTSIDER):
            with self.assertRaises(LookupError):s.save_layout(ctx,self.list_id,layout)
        s.assign_list(ADMIN,self.list_id,assigned_to_id=3,version=1)
        self.assertFalse(s.list_rows(OTHER_REP,self.list_id)['layout']['saved'])
        with self.assertRaises(LookupError):s.save_layout(REP,self.list_id,layout)
        s.assign_list(ADMIN,self.list_id,assigned_to_id=2,version=2)
        self.assertEqual(s.list_rows(REP,self.list_id)['layout']['order'],layout['order'])

    def test_invalid_layouts_rejected_without_overwriting_preferences(self):
        layout=s.list_rows(REP,self.list_id)['layout']
        invalid=[{}, {'order':layout['order'],'visible':['lead']},
                 {'order':layout['order'][:-1],'visible':[]},
                 {'order':layout['order']+['c0'],'visible':[]},
                 {'order':layout['order']+['missing'],'visible':[]},
                 {'order':[{}],'visible':[]}, {'order':layout['order'],'visible':'c0'},
                 {'order':layout['order'],'visible':['c0','c0']}]
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(ValueError):s.save_layout(REP,self.list_id,value)
        self.assertFalse(s.list_rows(REP,self.list_id)['layout']['saved'])

    def test_default_layout_handles_duplicate_mappings(self):
        listing=s.list_rows(REP,self.list_id)['listing']
        listing['mapping']['email']=listing['mapping']['phone']
        layout=s.default_layout(listing)
        self.assertEqual(len(layout['order']),len(set(layout['order'])))
        self.assertEqual(len(layout['order']),len(listing['columns'])+5)

    def test_layout_api_checks_csrf_and_owner(self):
        app=Flask(__name__);app.secret_key='test';app.register_blueprint(routes.prospecting_bp)
        client=app.test_client()
        layout=s.list_rows(REP,self.list_id)['layout']
        path=f'/crm/prospecting/api/lists/{self.list_id}/layout'
        with client.session_transaction() as session:session['prospecting_csrf']='token'
        with patch('auth_service.validate_session',return_value={'id':2,'is_sponsored':True,'sponsor_user_id':1}):
            self.assertEqual(client.put(path,json=layout).status_code,403)
            self.assertEqual(client.put(path,json=layout,headers={'X-CSRF-Token':'token'}).status_code,200)
        with patch('auth_service.validate_session',return_value={'id':3,'is_sponsored':True,'sponsor_user_id':1}):
            self.assertEqual(client.put(path,json=layout,headers={'X-CSRF-Token':'token'}).status_code,404)

    def test_assignment_migration_preserves_old_uploads_without_regranting_access(self):
        with s.transaction() as cur:cur.execute('ALTER TABLE prospect_lists DROP COLUMN assigned_to_id CASCADE')
        crm.init_crm_tables()
        listing=s.list_rows(REP,self.list_id)['listing']
        self.assertEqual(listing['assigned_to_id'],REP['user_id'])
        with s.transaction() as cur:cur.execute('UPDATE prospect_lists SET assigned_to_id=NULL WHERE id=%s',(self.list_id,))
        crm.init_crm_tables()
        self.assertEqual(s.list_lists(REP),[])
        self.assertIsNone(s.list_rows(ADMIN,self.list_id)['listing']['assigned_to_id'])

    def test_old_worker_upload_defaults_owner_during_rolling_deploy(self):
        with s.transaction() as cur:
            cur.execute("""INSERT INTO prospect_lists(name,filename,columns,mapping,import_key,import_hash,team_id,added_by_id)
                SELECT name,filename,columns,mapping,%s,import_hash,team_id,added_by_id FROM prospect_lists WHERE id=%s RETURNING assigned_to_id""",
                (str(uuid.uuid4()),self.list_id))
            self.assertEqual(cur.fetchone()['assigned_to_id'],2)

    def test_import_defaults_to_self_and_admin_can_choose_member(self):
        self.assertEqual(s.list_rows(REP,self.list_id)['listing']['assigned_to_id'],2)
        mine=self.create(ADMIN)
        theirs=self.create(ADMIN,assigned_to_id=3)
        self.assertEqual(s.list_rows(ADMIN,mine)['listing']['assigned_to_id'],1)
        listing=s.list_rows(OTHER_REP,theirs)['listing']
        self.assertEqual(listing['assigned_to_id'],3)
        self.assertEqual(listing['added_by_id'],1)
        with self.assertRaises(LookupError):s.list_rows(REP,theirs)
        with self.assertRaises(PermissionError):self.create(REP,assigned_to_id=3)
        with self.assertRaises(ValueError):self.create(ADMIN,assigned_to_id=4)
        with s.transaction() as cur:cur.execute("UPDATE account_sponsorships SET status='revoked' WHERE member_user_id=3")
        with self.assertRaises(ValueError):self.create(ADMIN,assigned_to_id=3)

    def test_reassignment_revokes_uploader_access_and_preserves_work(self):
        row=s.update_row(REP,self.rows[0]['id'],{'version':1,'notes':'Keep this research'})
        self.touch(row)
        listing=s.assign_list(ADMIN,self.list_id,assigned_to_id=3,version=1)
        self.assertEqual(listing['added_by_id'],2)
        self.assertEqual(listing['assigned_to_id'],3)
        self.assertEqual(s.list_lists(REP),[])
        for call in [lambda:s.list_rows(REP,self.list_id),lambda:s.row_detail(REP,row['id']),
                     lambda:s.export_rows(REP,self.list_id),lambda:s.update_row(REP,row['id'],{'version':3,'notes':'forbidden'}),
                     lambda:self.touch(row),lambda:s.promote(REP,row['id'],{'version':3})]:
            with self.assertRaises(LookupError):call()
        detail=s.row_detail(OTHER_REP,row['id'])
        self.assertEqual(detail['row']['notes'],'Keep this research')
        self.assertEqual(detail['row']['touch_count'],1)
        self.assertEqual(detail['touches'][0]['user_id'],2)
        self.assertEqual(detail['row']['original_cells'],self.rows[0]['original_cells'])
        self.assertEqual(self.count('crm_change_history'),1)
        self.assertIn('Keep this research',s.export_rows(OTHER_REP,self.list_id))

    def test_only_admin_reassigns_with_team_and_version_checks(self):
        for ctx in [REP,OTHER_REP]:
            with self.assertRaises(PermissionError):s.assign_list(ctx,self.list_id,assigned_to_id=ctx['user_id'],version=1)
        with self.assertRaises(LookupError):s.assign_list(OUTSIDER,self.list_id,assigned_to_id=4,version=1)
        for value in [4,999,True,1.5,'abc']:
            with self.assertRaises(ValueError):s.assign_list(ADMIN,self.list_id,assigned_to_id=value,version=1)
        assigned=s.assign_list(ADMIN,self.list_id,assigned_to_id=1,version=1)
        self.assertEqual(assigned['assigned_to_id'],1)
        with self.assertRaises(s.Conflict):s.assign_list(ADMIN,self.list_id,assigned_to_id=3,version=1)
        back=s.assign_list(ADMIN,self.list_id,assigned_to_id=2,version=2)
        self.assertEqual(back['version'],3)
        with self.assertRaises(s.Conflict):s.update_row(REP,self.rows[0]['id'],{'version':1,'notes':'stale draft'})

    def test_promotion_after_reassignment_uses_current_owner(self):
        self.touch(self.rows[0])
        s.assign_list(ADMIN,self.list_id,assigned_to_id=3,version=1)
        row=s.list_rows(OTHER_REP,self.list_id)['rows'][0]
        result=s.promote(ADMIN,row['id'],{'version':row['version']})
        with s.transaction() as cur:
            cur.execute('SELECT assigned_to_id,added_by_id FROM crm_contacts WHERE id=%s',(result['contact_id'],))
            self.assertEqual(dict(cur.fetchone()),dict(assigned_to_id=3,added_by_id=1))
            cur.execute('SELECT assigned_to_id FROM crm_follow_ups WHERE contact_id=%s',(result['contact_id'],))
            self.assertEqual(cur.fetchone()['assigned_to_id'],3)
        s.assign_list(ADMIN,self.list_id,assigned_to_id=2,version=2)
        detail=s.row_detail(REP,row['id'])
        self.assertTrue(detail['row']['crm_contact_restricted'])
        self.assertIsNone(detail['row']['promoted_contact_id'])

    def test_write_racing_with_reassignment_rechecks_owner(self):
        locked,release=Event(),Event()
        original=s.resolve_assignee
        def held_resolve(ctx,value,cur):
            result=original(ctx,value,cur)
            locked.set()
            if not release.wait(5):raise RuntimeError('Test did not release assignment')
            return result
        with patch.object(s,'resolve_assignee',side_effect=held_resolve), ThreadPoolExecutor(max_workers=2) as pool:
            assignment=pool.submit(s.assign_list,ADMIN,self.list_id,assigned_to_id=3,version=1)
            self.assertTrue(locked.wait(5))
            write=pool.submit(s.update_row,REP,self.rows[0]['id'],{'version':1,'notes':'old owner write'})
            release.set()
            assignment.result(timeout=5)
            with self.assertRaises(LookupError):write.result(timeout=5)
        self.assertEqual(s.row_detail(OTHER_REP,self.rows[0]['id'])['row']['notes'],'')

    def test_assignment_api_forbids_members_and_accepts_admin(self):
        app=Flask(__name__);app.secret_key='test';app.register_blueprint(routes.prospecting_bp)
        client=app.test_client()
        with client.session_transaction() as session:session['prospecting_csrf']='test-token'
        path=f'/crm/prospecting/api/lists/{self.list_id}/assignment'
        with patch('auth_service.validate_session',return_value={'id':2,'is_sponsored':True,'sponsor_user_id':1}):
            self.assertEqual(client.patch(path,json={'version':1,'assigned_to_id':2},headers={'X-CSRF-Token':'test-token'}).status_code,403)
        with patch('auth_service.validate_session',return_value={'id':1,'is_admin':True}):
            self.assertEqual(client.patch(path,json={'version':1,'assigned_to_id':3}).status_code,403)
            response=client.patch(path,json={'version':1,'assigned_to_id':3},headers={'X-CSRF-Token':'test-token'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json['listing']['assigned_to_id'],3)
        with patch('auth_service.validate_session',return_value={'id':2,'is_sponsored':True,'sponsor_user_id':1}):
            self.assertEqual(client.get(f'/crm/prospecting/{self.list_id}').status_code,404)
            self.assertEqual(client.get(f'/crm/prospecting/{self.list_id}/export.csv').status_code,404)

    def test_scope_on_every_read_and_write(self):
        row=self.rows[0]
        self.assertEqual(len(s.list_lists(ADMIN)),1)
        for ctx in [OUTSIDER,OTHER_REP]:
            self.assertEqual(s.list_lists(ctx),[])
            for call in [lambda:s.list_rows(ctx,self.list_id),lambda:s.row_detail(ctx,row['id']),
                         lambda:s.update_row(ctx,row['id'],{'version':1,'notes':'bad'}),
                         lambda:s.promote(ctx,row['id'],{'version':1}),lambda:s.export_rows(ctx,self.list_id)]:
                with self.assertRaises(LookupError):call()
            with self.assertRaises(LookupError):
                s.add_touch(ctx,row['id'],dict(version=1,request_key=str(uuid.uuid4()),method='call',outcome='spoke',occurred_at='2026-01-01T10:30'))

    def test_edits_keep_original_and_reject_stale_writes(self):
        row=self.rows[0]
        updated=s.update_row(REP,row['id'],{'version':1,'cells':{'c0':'Jane Updated'},'notes':'Research'})
        self.assertEqual(updated['cells']['c0'],'Jane Updated')
        self.assertEqual(updated['original_cells']['c0'],'Jane Doe')
        with self.assertRaises(s.Conflict):s.update_row(REP,row['id'],{'version':1,'notes':'lost update'})

    def test_touches_idempotent_ordered_and_dnc(self):
        row=self.rows[0]; token=str(uuid.uuid4())
        data=dict(version=1,request_key=token,method='call',outcome='spoke',occurred_at='2026-01-02T10:00',status='connected')
        touched=s.add_touch(REP,row['id'],data)
        self.assertEqual(s.add_touch(REP,row['id'],data)['touch_count'],1)
        data.update(version=2,request_key=str(uuid.uuid4()),occurred_at='2026-01-01T10:00')
        earlier=s.add_touch(REP,row['id'],data)
        self.assertEqual(earlier['last_touch_at'],touched['last_touch_at'])
        self.assertEqual(earlier['touch_count'],2)
        s.update_row(REP,row['id'],{'version':3,'status':'do_not_contact','next_follow_up':'2026-01-03'})
        data.update(version=4,request_key=str(uuid.uuid4()))
        with self.assertRaises(s.Conflict):s.add_touch(REP,row['id'],data)
        self.assertIsNone(s.row_detail(REP,row['id'])['row']['next_follow_up'])

    def test_promotion_preserves_history_notes_followup_and_retries(self):
        touched=self.touch(self.rows[0])
        result=s.promote(REP,touched['id'],{'version':touched['version']})
        self.assertEqual(s.promote(REP,touched['id'],{'version':touched['version']})['contact_id'],result['contact_id'])
        self.assertEqual(self.count('crm_contacts'),1)
        self.assertEqual(self.count('crm_activity'),2)
        self.assertEqual(self.count('crm_follow_ups'),1)
        with s.transaction() as cur:
            cur.execute('SELECT * FROM crm_contacts WHERE id=%s',(result['contact_id'],));contact=cur.fetchone()
            self.assertEqual(contact['assigned_to_id'],REP['user_id'])
            self.assertEqual(contact['last_contacted_at'],touched['last_touch_at'])
            cur.execute("SELECT note,meta FROM crm_activity WHERE type='note'");note=cur.fetchone()
            self.assertIn('Extra: =1+1',note['note'])
            self.assertEqual(note['meta']['original_cells']['c0'],'Jane Doe')
        with self.assertRaises(s.Conflict):s.update_row(REP,touched['id'],{'version':3,'notes':'frozen'})

    def test_promotion_dedup_requires_name_not_just_switchboard(self):
        first=s.promote(REP,self.rows[0]['id'],{'version':1})
        second=s.promote(REP,self.rows[1]['id'],{'version':1})
        self.assertNotEqual(first['contact_id'],second['contact_id'])
        duplicate=s.list_rows(REP,self.create())['rows'][0]
        linked=s.promote(REP,duplicate['id'],{'version':1})
        self.assertEqual(first['contact_id'],linked['contact_id'])
        self.assertEqual(self.count('crm_contacts'),2)

    def test_claimed_and_do_not_contact_matches_cannot_be_promoted(self):
        first=s.promote(REP,self.rows[0]['id'],{'version':1})
        duplicate=s.list_rows(REP,self.create())['rows'][0]
        with s.transaction() as cur:cur.execute('UPDATE crm_contacts SET assigned_to_id=3 WHERE id=%s',(first['contact_id'],))
        with self.assertRaises(s.Conflict):s.promote(REP,duplicate['id'],{'version':1})
        with s.transaction() as cur:cur.execute('UPDATE crm_contacts SET assigned_to_id=2,do_not_contact=true WHERE id=%s',(first['contact_id'],))
        with self.assertRaises(s.Conflict):s.promote(REP,duplicate['id'],{'version':1})
        self.assertEqual(self.count('crm_contacts'),1)

    def test_concurrent_promotion_creates_one_contact(self):
        row=self.rows[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:s.promote(REP,row['id'],{'version':1}), range(2)))
        self.assertEqual(results[0]['contact_id'],results[1]['contact_id'])
        self.assertEqual(self.count('crm_activity'),1)

    def test_export_formula_safety_and_full_data(self):
        exported=list(csv.reader(io.StringIO(s.export_rows(REP,self.list_id).lstrip('\ufeff'))))
        self.assertEqual(exported[1][4],"'=1+1")
        self.assertEqual(exported[2][4],'https://example.test')
        self.assertIn('Contact status',exported[0])

    def test_filters_and_pagination(self):
        self.touch(self.rows[0])
        self.assertEqual(s.list_rows(REP,self.list_id,due=True)['total'],1)
        self.assertEqual(s.list_rows(REP,self.list_id,q='Smith')['total'],1)
        self.assertEqual(s.list_rows(REP,self.list_id,q='%')['total'],0)
        self.assertEqual(s.list_rows(REP,self.list_id,status='new')['total'],1)
        self.assertEqual(s.list_rows(REP,self.list_id,page=900)['page'],1)

    def test_bad_promotion_rolls_back(self):
        with self.assertRaises(ValueError):s.promote(REP,self.rows[0]['id'],{'version':1,'email':'two addresses'})
        self.assertEqual(self.count('crm_contacts'),0)
        self.assertIsNone(s.row_detail(REP,self.rows[0]['id'])['row']['promoted_at'])

    def test_authenticated_api_and_csrf(self):
        app=Flask(__name__);app.secret_key='test';app.register_blueprint(routes.prospecting_bp)
        app.add_url_rule('/login',endpoint='auth.login',view_func=lambda:'Login')
        client=app.test_client()
        with patch('auth_service.validate_session',return_value={'id':2,'is_sponsored':True,'sponsor_user_id':1}):
            self.assertEqual(client.post('/crm/prospecting/api/preview').status_code,403)
            with client.session_transaction() as session:session['prospecting_csrf']='test-token'
            preview=client.post('/crm/prospecting/api/preview',data={'file':(io.BytesIO(SAMPLE),'test.csv')},headers={'X-CSRF-Token':'test-token'})
            self.assertEqual(preview.status_code,200)
            self.assertEqual(preview.json['row_count'],2)
            response=client.get(f'/crm/prospecting/api/lists/{self.list_id}')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json['rows'][0]['cells']['c0'],'Jane Doe')
        with patch('auth_service.validate_session',return_value=None):
            self.assertEqual(client.get('/crm/prospecting/api/lists').status_code,302)

    def batch_data(self, action='update', **extra):
        data=s.list_rows(REP,self.list_id,archived='all')
        return dict(action=action,request_key=str(uuid.uuid4()),list_version=data['listing']['version'],
                    rows=[{'id':r['id'],'version':r['version']} for r in data['rows']],**extra)

    def test_bulk_update_undo_and_retry(self):
        data=self.batch_data(changes={'status':'interested','next_follow_up':'2026-12-01'})
        result=w.batch_edit(REP,self.list_id,data)
        self.assertEqual(result['count'],2)
        self.assertTrue(w.batch_edit(REP,self.list_id,data)['replayed'])
        after=s.list_rows(REP,self.list_id)
        self.assertTrue(all(r['status']=='interested' for r in after['rows']))
        self.assertEqual(self.count('prospect_changes'),1)
        w.undo(REP,self.list_id,result['change_id'])
        self.assertEqual(w.undo(REP,self.list_id,result['change_id'])['count'],2)
        restored=s.list_rows(REP,self.list_id)
        self.assertTrue(all(r['status']=='new' and not r['next_follow_up'] for r in restored['rows']))
        self.assertIsNone(restored['last_change'])
        self.assertEqual(self.count('crm_contacts'),0)

    def test_bulk_stale_or_foreign_row_is_atomic(self):
        data=self.batch_data(changes={'status':'interested'})
        s.update_row(REP,self.rows[1]['id'],{'version':1,'notes':'newer work'})
        with self.assertRaises(s.Conflict):w.batch_edit(REP,self.list_id,data)
        self.assertEqual(s.row_detail(REP,self.rows[0]['id'])['row']['status'],'new')
        data=self.batch_data(changes={'status':'interested'})
        other=self.create();data['rows'].append({'id':s.list_rows(REP,other)['rows'][0]['id'],'version':1})
        with self.assertRaises(LookupError):w.batch_edit(REP,self.list_id,data)
        self.assertEqual(s.row_detail(REP,self.rows[0]['id'])['row']['status'],'new')

    def test_bulk_respects_reassignment_promotion_and_dnc(self):
        data=self.batch_data(changes={'next_follow_up':'2026-12-01'})
        s.update_row(REP,self.rows[1]['id'],{'version':1,'status':'do_not_contact'})
        data=self.batch_data(changes={'next_follow_up':'2026-12-01'})
        with self.assertRaises(ValueError):w.batch_edit(REP,self.list_id,data)
        self.assertIsNone(s.row_detail(REP,self.rows[0]['id'])['row']['next_follow_up'])
        s.promote(REP,self.rows[0]['id'],{'version':1})
        with self.assertRaises(s.Conflict):w.batch_edit(REP,self.list_id,self.batch_data(changes={'status':'interested'}))
        s.assign_list(ADMIN,self.list_id,assigned_to_id=3,version=1)
        with self.assertRaises(LookupError):w.batch_edit(REP,self.list_id,data)

    def test_archive_restore_preserves_history_and_blocks_editing(self):
        self.touch(self.rows[0])
        w.batch_edit(REP,self.list_id,self.batch_data('archive'))
        self.assertEqual(s.list_rows(REP,self.list_id)['total'],0)
        archived=s.list_rows(REP,self.list_id,archived='archived')
        self.assertEqual(archived['total'],2)
        self.assertEqual(archived['summary']['due'],0)
        row=archived['rows'][0]
        with self.assertRaises(s.Conflict):s.update_row(REP,row['id'],{'version':row['version'],'notes':'no'})
        w.batch_edit(REP,self.list_id,self.batch_data('restore'))
        self.assertEqual(s.list_rows(REP,self.list_id)['rows'][0]['touch_count'],1)

    def test_undo_never_overwrites_new_work_or_other_user(self):
        result=w.batch_edit(REP,self.list_id,self.batch_data(changes={'status':'interested'}))
        with self.assertRaises(LookupError):w.undo(ADMIN,self.list_id,result['change_id'])
        s.update_row(ADMIN,self.rows[0]['id'],{'version':2,'notes':'admin notes'})
        with self.assertRaises(s.Conflict):w.undo(REP,self.list_id,result['change_id'])
        self.assertEqual(s.row_detail(REP,self.rows[0]['id'])['row']['notes'],'admin notes')

    def test_paste_atomic_validation_and_original_cells(self):
        data=self.batch_data('paste');data['rows'][0]['cells']={'c0':'Updated Name','c4':'<script>alert(1)</script>'};data['rows'][1]['cells']={'bad':'x'}
        with self.assertRaises(ValueError):w.batch_edit(REP,self.list_id,data)
        self.assertEqual(s.row_detail(REP,self.rows[0]['id'])['row']['cells']['c0'],'Jane Doe')
        data['rows'][1]['cells']={'c4':'new research'}
        result=w.batch_edit(REP,self.list_id,data)
        row=s.row_detail(REP,self.rows[0]['id'])['row'];self.assertEqual(row['original_cells']['c0'],'Jane Doe')
        self.assertEqual(row['cells']['c0'],'Updated Name')
        w.undo(REP,self.list_id,result['change_id'])
        self.assertEqual(s.row_detail(REP,self.rows[0]['id'])['row']['cells']['c0'],'Jane Doe')

    def test_column_add_rename_and_layout_settings(self):
        result=w.manage_column(REP,self.list_id,{'version':1,'label':'Priority'})
        cid=result['column_id'];self.assertEqual(cid,'c5')
        self.assertEqual(s.list_rows(REP,self.list_id)['rows'][0]['cells'][cid],'')
        w.manage_column(REP,self.list_id,{'version':2,'id':'c0','label':'Full name'})
        listing=s.list_rows(REP,self.list_id)['listing'];self.assertEqual(listing['mapping']['name'],'c0')
        self.assertEqual(listing['columns'][0]['original_label'],'Contact Name')
        with self.assertRaises(s.Conflict):w.manage_column(REP,self.list_id,{'version':1,'label':'stale'})
        layout=s.list_rows(REP,self.list_id)['layout'];layout.update(widths={'lead':400,'c5':220},pinned='c5')
        s.save_layout(REP,self.list_id,layout)
        self.assertEqual(s.list_rows(REP,self.list_id)['layout']['widths'],layout['widths'])
        self.assertEqual(s.list_rows(ADMIN,self.list_id)['layout']['widths'],{})
        for value in ({'c5':True},{'c5':900},{'bogus':200}):
            with self.assertRaises(ValueError):s.save_layout(REP,self.list_id,{**layout,'widths':value})

    def test_duplicate_checks_shared_phone_and_private_crm(self):
        own=crm.create_contact(REP,name='Jane Doe',email='JANE@example.test',phone='(212) 555-0100')
        crm.create_contact(OTHER_REP,name='Private name',email='john@example.test',phone='2125550100')
        found=w.duplicates(REP,self.list_id)['duplicates']
        for row in self.rows:
            match=found[str(row['id'])];self.assertEqual(match['list_count'],1);self.assertEqual(match['crm_count'],1)
            self.assertEqual(match['crm_matches'][0]['name'],'Jane Doe')
        self.assertNotIn('Private name',str(found))
        self.assertEqual(w.duplicates(ADMIN,self.list_id)['duplicates'][str(self.rows[0]['id'])]['crm_count'],2)
        self.assertEqual(self.count('crm_contacts'),2)
        with self.assertRaises(LookupError):w.duplicates(OTHER_REP,self.list_id)

    def test_duplicate_checks_ignore_blank_and_invalid_identifiers(self):
        parsed=s.parse_csv(b'Name,Phone,Email\nA,123,no\nB,123,no\n')
        other=self.create(parsed=parsed)
        self.assertEqual(w.duplicates(REP,other)['duplicates'],{})
        self.assertEqual(w.identity_keys({'phone':'2125550100 ext 123'}),{('phone','2125550100')})
        self.assertEqual(w.identity_keys({'phone':'2125550100 / 2125550101'}),set())

    def test_append_manual_csv_matches_and_idempotency(self):
        original=s.list_rows(REP,self.list_id)['rows']
        token=str(uuid.uuid4());result=imports.append(REP,self.list_id,cells={'c0':'Third person','c1':'Example'},request_key=token,version=1)
        self.assertEqual(result['count'],1)
        self.assertTrue(imports.append(REP,self.list_id,cells={'c0':'Third person','c1':'Example'},request_key=token,version=1)['replayed'])
        parsed=s.parse_csv(b'Email,Name,New field\nfour@example.test,Fourth,Value\n')
        mapping=imports.suggest_matches(s.list_rows(REP,self.list_id)['listing'],parsed)
        self.assertEqual(mapping,{'c0':'c2','c1':'c0','c2':'new'})
        imports.append(REP,self.list_id,parsed=parsed,mapping=mapping,filename='extra.csv',request_key=str(uuid.uuid4()),version=1)
        result=s.list_rows(REP,self.list_id)
        self.assertEqual(result['total'],4);self.assertEqual(len(result['listing']['columns']),6)
        self.assertEqual(result['rows'][3]['cells']['c5'],'Value');self.assertEqual(result['rows'][3]['source_filename'],'extra.csv')
        for before,after in zip(original,result['rows'][:2]):self.assertEqual(before['cells'],after['cells'])
        self.assertEqual(self.count('crm_contacts'),0)
        self.assertIn('extra.csv',s.export_rows(REP,self.list_id))

    def test_append_rejects_collision_stale_and_unauthorized(self):
        parsed=s.parse_csv(b'Name,Alias\nA,B\n')
        with self.assertRaises(ValueError):imports.append(REP,self.list_id,parsed=parsed,mapping={'c0':'c0','c1':'c0'},request_key=str(uuid.uuid4()),version=1)
        with self.assertRaises(ValueError):imports.append(REP,self.list_id,cells={'bogus':'name'},request_key=str(uuid.uuid4()),version=1)
        with self.assertRaises(s.Conflict):imports.append(REP,self.list_id,cells={'c0':'name'},request_key=str(uuid.uuid4()),version=0)
        with self.assertRaises(LookupError):imports.append(OTHER_REP,self.list_id,cells={'c0':'name'},request_key=str(uuid.uuid4()),version=1)
        self.assertEqual(s.list_rows(REP,self.list_id)['total'],2)

    def test_network_build_profiles_relationships_and_privacy(self):
        self.assertEqual(network.sync_people(REP,self.list_id)['added'],2)
        self.assertEqual(network.sync_people(REP,self.list_id)['added'],0)
        graph=network.get_network(REP,self.list_id)
        self.assertEqual(len(graph['nodes']),3);self.assertEqual(len(graph['links']),2)
        self.assertTrue(all(l['relation']=='associated_with' for l in graph['links']))
        company=next(n for n in graph['nodes'] if n['kind']=='company')
        building=network.save_entity(REP,self.list_id,{'kind':'building','name':'123 Main St','address':'123 Main St, Brooklyn'})['entity_id']
        relation={'source_id':company['id'],'target_id':building,'relation':'manages','note':'Confirmed with Jane'}
        link_id=network.link(REP,self.list_id,relation)['link_id']
        self.assertEqual(network.link(REP,self.list_id,relation)['link_id'],link_id)
        graph=network.get_network(REP,self.list_id);self.assertEqual(len(graph['links']),3)
        with self.assertRaises(LookupError):network.get_network(OTHER_REP,self.list_id)
        with self.assertRaises(LookupError):network.link(OTHER_REP,self.list_id,relation)
        network.unlink(REP,self.list_id,link_id)
        self.assertEqual(len(network.get_network(REP,self.list_id)['links']),2)
        self.assertEqual(self.count('crm_buildings'),0);self.assertEqual(self.count('crm_contacts'),0)

    def test_network_rejects_cross_list_and_invalid_relationships(self):
        network.sync_people(REP,self.list_id);other=self.create();network.sync_people(REP,other)
        a=network.get_network(REP,self.list_id)['nodes'][0];b=network.get_network(REP,other)['nodes'][0]
        with self.assertRaises(LookupError):network.link(REP,self.list_id,{'source_id':a['id'],'target_id':b['id'],'relation':'associated_with'})
        with self.assertRaises(ValueError):network.link(REP,self.list_id,{'source_id':a['id'],'target_id':a['id'],'relation':'associated_with'})
        with self.assertRaises(ValueError):network.save_entity(REP,self.list_id,{'kind':'company','name':'Bad','website':'javascript:alert(1)'})
        with self.assertRaises(s.Conflict):network.save_entity(REP,self.list_id,{'kind':'company','name':'example'})
        s.assign_list(ADMIN,self.list_id,assigned_to_id=3,version=1)
        with self.assertRaises(LookupError):network.sync_people(REP,self.list_id)
        self.assertEqual(len(network.get_network(OTHER_REP,self.list_id)['nodes']),3)

    def test_network_live_person_name_and_crm_reference_redaction(self):
        network.sync_people(REP,self.list_id)
        s.update_row(REP,self.rows[0]['id'],{'version':1,'cells':{'c0':'Jane Updated'}})
        node=next(n for n in network.get_network(REP,self.list_id)['nodes'] if n['row_id']==self.rows[0]['id'])
        self.assertEqual(node['display_name'],'Jane Updated')
        promoted=s.promote(REP,self.rows[0]['id'],{'version':2})
        s.assign_list(ADMIN,self.list_id,assigned_to_id=3,version=1)
        node=next(n for n in network.get_network(OTHER_REP,self.list_id)['nodes'] if n['row_id']==self.rows[0]['id'])
        self.assertIsNone(node['promoted_contact_id']);self.assertTrue(node['crm_restricted'])

    def test_explicit_crm_reference_promotes_to_chosen_record(self):
        own=crm.create_contact(REP,name='Jane Old Spelling',email='old@example.test')
        private=crm.create_contact(OTHER_REP,name='Private CRM')
        network.sync_people(REP,self.list_id)
        node=next(n for n in network.get_network(REP,self.list_id)['nodes'] if n['row_id']==self.rows[0]['id'])
        self.assertEqual(network.search_crm(REP,self.list_id,'person','Private')['records'],[])
        with self.assertRaises(LookupError):network.link_crm(REP,self.list_id,node['id'],{'record_id':private,'version':1})
        network.link_crm(REP,self.list_id,node['id'],{'record_id':own,'version':1})
        result=s.promote(REP,self.rows[0]['id'],{'version':1})
        self.assertEqual(result['contact_id'],own);self.assertTrue(result['existing'])
        self.assertEqual(self.count('crm_contacts'),2)
        with self.assertRaises(s.Conflict):network.link_crm(REP,self.list_id,node['id'],{'record_id':None,'version':2})
        with s.transaction() as cur:
            cur.execute("SELECT note FROM crm_activity WHERE contact_id=%s AND type='note'",(own,))
            self.assertIn('Relationship:',cur.fetchone()['note'])

    def test_new_mutation_routes_require_csrf_and_current_scope(self):
        app=Flask(__name__);app.secret_key='test';app.register_blueprint(routes.prospecting_bp);client=app.test_client()
        paths=['batch','undo','columns','people','network/build','profiles','relationships']
        with client.session_transaction() as session:session['prospecting_csrf']='token'
        with patch('auth_service.validate_session',return_value={'id':2,'is_sponsored':True,'sponsor_user_id':1}):
            for path in paths:
                with self.subTest(path=path):self.assertEqual(client.post(f'/crm/prospecting/api/lists/{self.list_id}/{path}',json={}).status_code,403)
        with patch('auth_service.validate_session',return_value={'id':3,'is_sponsored':True,'sponsor_user_id':1}):
            for path in ('network','duplicates','crm-records?kind=person&q=Jane'):
                with self.subTest(path=path):self.assertEqual(client.get(f'/crm/prospecting/api/lists/{self.list_id}/{path}').status_code,404)

    def test_sheet_company_edits_refresh_links_without_restoring_unlinked_relationships(self):
        network.sync_people(REP,self.list_id)
        graph=network.get_network(REP,self.list_id)
        jane=next(n for n in graph['nodes'] if n['row_id']==self.rows[0]['id'])
        company=next(n for n in graph['nodes'] if n['kind']=='company')
        link=next(l for l in graph['links'] if l['source_id']==jane['id'])
        network.unlink(REP,self.list_id,link['id']);network.sync_people(REP,self.list_id)
        self.assertFalse(any(l['source_id']==jane['id'] for l in network.get_network(REP,self.list_id)['links']))
        network.save_entity(REP,self.list_id,{'id':company['id'],'version':1,'kind':'company','name':'Example Renamed'})
        network.sync_people(REP,self.list_id)
        self.assertEqual(len([n for n in network.get_network(REP,self.list_id)['nodes'] if n['kind']=='company']),1)
        s.update_row(REP,self.rows[0]['id'],{'version':1,'cells':{'c1':'New Company'}})
        network.sync_people(REP,self.list_id);graph=network.get_network(REP,self.list_id)
        target=next(l['target_id'] for l in graph['links'] if l['source_id']==jane['id'])
        self.assertEqual(next(n['name'] for n in graph['nodes'] if n['id']==target),'New Company')

    def test_duplicate_scan_bounds_large_shared_switchboard(self):
        parsed=s.parse_csv(('Name,Phone\n'+'\n'.join(f'Person {i},2125550100' for i in range(1000))).encode())
        list_id=self.create(parsed=parsed)
        found=w.duplicates(REP,list_id)['duplicates']
        self.assertEqual(len(found),1000)
        self.assertTrue(all(d['list_count']==999 and len(d['list_matches'])==8 for d in found.values()))

    def test_concurrent_bulk_edit_and_append_positions(self):
        data=self.batch_data(changes={'status':'interested'})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:w.batch_edit(REP,self.list_id,data),range(2)))
        self.assertEqual(self.count('prospect_changes'),1)
        self.assertEqual(sum(bool(r['replayed']) for r in results),1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda i:imports.append(REP,self.list_id,cells={'c0':f'Person {i}'},request_key=str(uuid.uuid4()),version=1),range(2)))
        rows=s.list_rows(REP,self.list_id)['rows']
        self.assertEqual([r['position'] for r in rows],[1,2,3,4])


if __name__=='__main__': unittest.main()
