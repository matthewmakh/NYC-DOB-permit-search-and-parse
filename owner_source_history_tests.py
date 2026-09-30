"""Offline owner-source tests; optional isolated PostgreSQL uses ENRICHMENT_TEST_DATABASE_URL."""
import os
os.environ.setdefault('DATABASE_URL', 'postgresql://unused@localhost:1/unused')
os.environ.setdefault('PYTHON_DOTENV_DISABLED', '1')
import unittest
import uuid
from unittest.mock import Mock, patch

import _pipeline_path  # noqa
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from flask import Flask
import auth_service
import owner_source_history as history
import owner_source_routes as routes
import property_source_refresh as refresh
import step2_enrich_from_pluto as property_api
import step3_enrich_from_acris as acris
import step4_enrich_from_tax_liens as ecb
import step5_enrich_from_sos as sos


class SnapshotTests(unittest.TestCase):
    def test_reordered_records_and_new_dates_are_not_owner_changes(self):
        contacts = [dict(name='Alex Example', role='JointOwner', address='10 Example St',
                         registration_id='10', reported_date='2025-01-01'),
                    dict(name='Jordan Example', role='JointOwner', address='20 Example St')]
        before = history.make_snapshot({'hpd_owner_contacts': contacts}, 'hpd')
        after_contacts = [dict(contact, registration_id='11', reported_date='2026-01-01')
                          for contact in reversed(contacts)]
        after = history.make_snapshot({'hpd_owner_contacts': after_contacts}, 'hpd')
        self.assertEqual(history.snapshot_changes(before, after), [])
        after_contacts[0]['address'] = '30 Changed St'
        self.assertTrue(history.snapshot_changes(before,
            history.make_snapshot({'hpd_owner_contacts': after_contacts}, 'hpd')))

    def test_sos_formation_date_is_not_contact_reported_date(self):
        snapshot = history.make_snapshot({'sos_principal_name': 'Alex Example',
            'sos_principal_title': 'Registered Agent', 'sos_formation_date': '2020-01-01'}, 'sos')
        self.assertIsNone(snapshot['reported_date'])
        self.assertEqual(snapshot['records'][0]['role'], 'Registered Agent')

    def test_old_aggregated_hpd_name_does_not_inherit_single_address(self):
        snapshot = history.make_snapshot({'owner_name_hpd': 'Alex Example & Jordan Example',
            'hpd_owner_business_address': '10 Someone St'}, 'hpd')
        self.assertNotIn('address', snapshot['records'][0])

    def test_provenance_periods_are_not_fabricated_dates(self):
        self.assertEqual(history.make_snapshot({'rpad_assessment_year': '2018/19',
            'rpad_assessment_period': 'FINAL'}, 'rpad'),
            {'records': [], 'reported_date': None, 'period': '2018/19 FINAL'})
        self.assertIsNone(history.make_snapshot({'pluto_version': '26v2'}, 'pluto')['reported_date'])


class RouteGuardTests(unittest.TestCase):
    def setUp(self):
        self.connect = Mock(side_effect=AssertionError('Guard must not open the database'))
        app = Flask(__name__)
        app.secret_key = 'offline-test'
        app.register_blueprint(routes.create_blueprint(self.connect))
        self.app = app
        self.client = app.test_client()
        self.path = '/api/property/3012980066/owner-sources'

    def test_anonymous_cannot_read_addresses_or_enqueue(self):
        with patch.object(auth_service, 'validate_session', return_value=None):
            self.assertEqual(self.client.get(self.path).status_code, 401)
            self.assertEqual(self.client.post(self.path+'/hpd/refresh', json={},
                headers={'X-Owner-Research': '1'}).status_code, 401)
        self.connect.assert_not_called()

    def test_trusted_forwarded_https_origin_is_accepted_without_trusting_other_hosts(self):
        self.app.config['OWNER_RESEARCH_TRUST_PROXY_PROTO'] = True
        headers = {'X-Owner-Research': '1', 'X-Forwarded-Proto': 'https', 'Origin': 'https://localhost'}
        with self.app.test_request_context(self.path+'/hpd/refresh', method='POST', json={}, headers=headers):
            self.assertTrue(routes._mutation_allowed())
        headers['Origin'] = 'https://evil.example'
        headers['X-Forwarded-Host'] = 'evil.example'
        with self.app.test_request_context(self.path+'/hpd/refresh', method='POST', json={}, headers=headers):
            self.assertFalse(routes._mutation_allowed())
        self.app.config['OWNER_RESEARCH_TRUST_PROXY_PROTO'] = False
        headers['Origin'] = 'https://localhost'
        with patch.dict(os.environ, {}, clear=True), self.app.test_request_context(
                self.path+'/hpd/refresh', method='POST', json={}, headers=headers):
            self.assertFalse(routes._mutation_allowed())

    def test_cross_origin_missing_header_and_invalid_source_are_rejected(self):
        with patch.object(auth_service, 'validate_session', return_value={'id': 1}):
            for headers in ({}, {'X-Owner-Research': '1', 'Origin': 'https://evil.example'},
                            {'X-Owner-Research': '1', 'Sec-Fetch-Site': 'cross-site'},
                            {'X-Owner-Research': '1', 'Referer': 'https://evil.example/page'}):
                self.assertEqual(self.client.post(self.path+'/hpd/refresh', json={},
                    headers=headers).status_code, 403)
            self.assertEqual(self.client.post(self.path+'/paid/refresh', json={},
                headers={'X-Owner-Research': '1'}).status_code, 400)
            self.assertEqual(self.client.get('/api/property/not-a-bbl/owner-sources').status_code, 400)
        self.connect.assert_not_called()


@unittest.skipUnless(os.getenv('ENRICHMENT_TEST_DATABASE_URL'), 'disposable PostgreSQL DSN not supplied')
class HistoryDatabaseTests(unittest.TestCase):
    def connect(self):
        return psycopg2.connect(os.environ['ENRICHMENT_TEST_DATABASE_URL'],
                               options=f'-c search_path={self.schema}')

    def setUp(self):
        self.schema = 'owner_history_' + uuid.uuid4().hex
        self.admin = psycopg2.connect(os.environ['ENRICHMENT_TEST_DATABASE_URL'])
        self.admin.autocommit = True
        with self.admin.cursor() as cur:
            cur.execute(f'CREATE SCHEMA {self.schema}')
        self.conn = self.connect()
        fields = set(refresh.PLUTO_FIELDS + refresh.HPD_FIELDS + refresh.RPAD_FIELDS + [
            'current_owner_name', 'address', 'bin', 'latitude', 'longitude', 'zip_code',
            'sale_buyer_primary', 'sale_recorded_date', 'sale_crfn',
            'acris_last_enriched', 'acris_last_error', 'acris_last_attempted',
            'ecb_respondent_name', 'ecb_respondent_address', 'ecb_respondent_city',
            'ecb_respondent_zip', 'ecb_respondent_issue_date', 'ecb_last_checked',
            'sos_entity_name', 'sos_entity_status', 'sos_dos_id', 'sos_formation_date',
            'sos_principal_name', 'sos_principal_title', 'sos_principal_street',
            'sos_principal_city', 'sos_principal_state', 'sos_principal_zip',
            'sos_last_enriched', 'sos_lookup_source', 'sos_last_error', 'sos_last_error_at'])
        with self.conn.cursor() as cur:
            cur.execute('CREATE TABLE buildings(id INTEGER PRIMARY KEY,bbl TEXT UNIQUE,'
                'property_last_attempted TIMESTAMP,property_last_enriched TIMESTAMP,property_last_error TEXT,'
                'sos_lookup_attempted BOOLEAN,' + ','.join(
                    f'{field} JSONB' if field == 'hpd_owner_contacts' else f'{field} TEXT'
                    for field in sorted(fields)) + ')')
            cur.execute("INSERT INTO buildings(id,bbl) VALUES(1,'3012980066')")
            cur.execute('''CREATE TABLE acris_transactions(id INTEGER PRIMARY KEY,building_id INTEGER,
                document_id TEXT,recorded_date DATE,is_primary_deed BOOLEAN)''')
            cur.execute('''CREATE TABLE acris_parties(id INTEGER PRIMARY KEY,building_id INTEGER,
                transaction_id INTEGER,party_type TEXT,party_name TEXT,address_1 TEXT,address_2 TEXT,
                city TEXT,state TEXT,zip_code TEXT)''')
            cur.execute(refresh.SCHEMA_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        with self.admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA {self.schema} CASCADE')
        self.admin.close()

    def scalar(self, sql):
        self.conn.rollback()
        with self.conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchone()[0]

    def execute(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
        self.conn.commit()

    def enqueue(self, source='hpd'):
        return routes.enqueue_source_refresh(self.conn, '3012980066', source, 1)

    def test_hpd_only_refresh_baseline_and_metadata_updates_not_duplicate_changes(self):
        contact = dict(name='Alex Example', role='IndividualOwner', address='10 Example St',
                       city='Brooklyn', state='NY', zip_code='11201', registration_id='1',
                       reported_date='2025-01-01')
        data = dict(owner_name_hpd='Alex Example', hpd_owner_contacts=[contact],
                    hpd_last_registration_date='2025-01-01')
        with patch.object(property_api, 'get_hpd_data_for_bbl', return_value=(data, None)), \
                patch.object(property_api, 'get_pluto_data_for_bbl') as pluto, \
                patch.object(property_api, 'get_rpad_data_for_bbl') as rpad:
            self.assertEqual(routes.run_source_adapter(self.conn, 1, '3012980066', 'hpd'), 'updated')
            data['hpd_last_registration_date'] = '2026-01-01'
            contact.update(reported_date='2026-01-01', registration_id='2')
            routes.run_source_adapter(self.conn, 1, '3012980066', 'hpd')
        pluto.assert_not_called()
        rpad.assert_not_called()
        self.assertEqual(self.scalar('SELECT count(*) FROM owner_source_history'), 1)
        self.assertEqual(self.scalar('SELECT kind FROM owner_source_history'), 'baseline')
        self.assertEqual(self.scalar("SELECT snapshot->>'reported_date' FROM owner_source_snapshots"), '2026-01-01')

    def test_existing_record_preserved_as_baseline_then_change(self):
        self.execute("UPDATE buildings SET current_owner_name='OLD LLC',pluto_version='25v1'")
        with patch.object(property_api, 'get_pluto_data_for_bbl', return_value=(
                {'owner_name': 'NEW LLC', 'pluto_version': '26v2'}, None)):
            routes.run_source_adapter(self.conn, 1, '3012980066', 'pluto')
        self.assertEqual(self.scalar('SELECT count(*) FROM owner_source_history'), 2)
        self.assertEqual(self.scalar("SELECT before->'records'->0->>'name' FROM owner_source_history WHERE kind='change'"), 'OLD LLC')
        self.assertEqual(self.scalar("SELECT after->'records'->0->>'name' FROM owner_source_history WHERE kind='change'"), 'NEW LLC')
        self.assertIsNone(self.scalar("SELECT reported_date FROM owner_source_history WHERE kind='change'"))

    def test_history_failure_rolls_back_source_facts_and_success_checkpoint(self):
        self.execute("UPDATE buildings SET current_owner_name='OLD LLC'")
        with patch.object(property_api, 'get_pluto_data_for_bbl', return_value=({'owner_name': 'NEW LLC'}, None)), \
                patch.object(refresh, 'record_source_snapshot', side_effect=RuntimeError('history write failed')):
            with self.assertRaisesRegex(RuntimeError, 'history write failed'):
                routes.run_source_adapter(self.conn, 1, '3012980066', 'pluto')
        self.assertEqual(self.scalar('SELECT current_owner_name FROM buildings'), 'OLD LLC')
        self.assertEqual(self.scalar('SELECT count(*) FROM owner_source_history'), 0)
        self.assertIsNone(self.scalar('SELECT checked_at FROM building_source_refresh'))

    def test_deleted_acris_party_is_retained_in_source_history(self):
        self.execute("INSERT INTO acris_transactions VALUES(1,1,'DOC1','2025-01-01',TRUE)")
        self.execute("INSERT INTO acris_parties VALUES(1,1,1,'buyer','Alex Example','10 Example St',NULL,'Brooklyn','NY','11201')")
        self.execute("UPDATE buildings SET sale_buyer_primary='Alex Example',sale_recorded_date='2025-01-01'")
        with self.conn.cursor() as cur:
            before = history.capture_source_snapshot(cur, 1, 'acris')
            cur.execute("DELETE FROM acris_parties")
            cur.execute("UPDATE buildings SET sale_buyer_primary='Jordan Example',sale_recorded_date='2026-01-01'")
            history.record_source_snapshot(cur, 1, 'acris', before)
        self.conn.commit()
        self.assertEqual(self.scalar("SELECT before->'records'->0->>'address' FROM owner_source_history WHERE kind='change'"), '10 Example St')
        self.assertEqual(str(self.scalar("SELECT reported_date FROM owner_source_history WHERE kind='change'")), '2026-01-01')

    def test_sos_actual_writer_records_roles_and_keeps_unknown_report_date(self):
        update = dict.fromkeys(['sos_principal_name', 'sos_principal_title', 'sos_principal_street',
            'sos_principal_city', 'sos_principal_state', 'sos_principal_zip', 'sos_entity_name',
            'sos_entity_status', 'sos_dos_id', 'sos_formation_date'])
        update.update(building_id=1, lookup_source='ACRIS', sos_principal_name='Alex Example',
                      sos_principal_title='Registered Agent', sos_entity_name='EXAMPLE LLC')
        sos.update_buildings_with_sos(self.conn, [update])
        update['sos_principal_street'] = '10 Example St'
        sos.update_buildings_with_sos(self.conn, [update])
        self.assertEqual(self.scalar('SELECT count(*) FROM owner_source_history'), 2)
        self.assertIsNone(self.scalar("SELECT reported_date FROM owner_source_history WHERE kind='change'"))

    def test_source_request_deduplicates_and_worker_runs_only_ecb(self):
        self.assertEqual(self.enqueue('ecb')['status'], 'queued')
        self.assertEqual(self.enqueue('ecb')['status'], 'queued')
        job_id = self.scalar('SELECT id FROM owner_source_jobs')
        self.assertEqual(self.scalar('SELECT count(*) FROM owner_source_jobs'), 1)
        data = {'ecb_respondent_name': 'Alex Example', 'ecb_respondent_address': '10 Example St',
                'ecb_respondent_issue_date': '2026-01-01', 'ecb_last_checked': '2026-09-30'}
        with patch.object(ecb, 'get_ecb_violations_data', return_value=(data, None)) as lookup, \
                patch.object(ecb, 'get_tax_delinquency_data') as tax_lookup:
            routes.process_source_job(self.connect, job_id)
        lookup.assert_called_once_with('3012980066')
        tax_lookup.assert_not_called()
        self.assertEqual(self.scalar('SELECT status FROM owner_source_jobs'), 'completed')
        self.assertEqual(self.scalar('SELECT count(*) FROM owner_source_history'), 1)
        self.assertEqual(self.enqueue('ecb')['status'], 'current')

    def test_failures_preserve_good_data_and_stop_after_three_attempts(self):
        self.execute("UPDATE buildings SET owner_name_hpd='Previous owner'")
        self.enqueue()
        job_id = self.scalar('SELECT id FROM owner_source_jobs')
        with patch.object(property_api, 'get_hpd_data_for_bbl', return_value=(None, 'Source unavailable')) as fetch:
            for _ in range(3):
                self.execute("UPDATE owner_source_jobs SET available_at=NOW()-INTERVAL '1 minute'")
                routes.process_source_job(self.connect, job_id)
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(self.scalar('SELECT status FROM owner_source_jobs'), 'failed')
        self.assertEqual(self.scalar('SELECT owner_name_hpd FROM buildings'), 'Previous owner')
        self.assertEqual(self.scalar('SELECT count(*) FROM owner_source_history'), 0)
        self.assertEqual(self.enqueue()['status'], 'failed')
        self.assertEqual(routes.process_queued_source_jobs(self.connect), 0)

    def test_busy_property_refresh_is_requeued_without_spending_retry(self):
        self.enqueue()
        job_id = self.scalar('SELECT id FROM owner_source_jobs')
        with self.conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_lock(72102,1)')
        self.conn.commit()
        with patch.object(property_api, 'get_hpd_data_for_bbl') as fetch:
            routes.process_source_job(self.connect, job_id)
        fetch.assert_not_called()
        self.assertEqual(self.scalar('SELECT attempts FROM owner_source_jobs'), 0)
        self.assertEqual(self.scalar('SELECT status FROM owner_source_jobs'), 'queued')

    def test_restart_recovers_expired_job_but_does_not_steal_live_lock(self):
        self.enqueue()
        job_id = self.scalar('SELECT id FROM owner_source_jobs')
        self.execute("UPDATE owner_source_jobs SET status='running',attempts=1,locked_at=NOW()-INTERVAL '1 hour'")
        with self.conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_lock(hashtextextended(%s,72111))', (str(job_id),))
        self.conn.commit()
        with patch.object(routes, 'run_source_adapter', return_value='updated') as run:
            self.assertEqual(routes.process_queued_source_jobs(self.connect), 0)
            run.assert_not_called()
            self.execute('SELECT pg_advisory_unlock(hashtextextended(%s,72111))', (str(job_id),))
            self.assertEqual(routes.process_queued_source_jobs(self.connect), 1)
            run.assert_called_once()
        self.assertEqual(self.scalar('SELECT status FROM owner_source_jobs'), 'completed')

    def test_status_and_api_are_authenticated_and_mutation_only_queues(self):
        app = Flask(__name__)
        app.secret_key = 'offline-test'
        app.register_blueprint(routes.create_blueprint(self.connect))
        client = app.test_client()
        path = '/api/property/3012980066/owner-sources'
        with patch.object(auth_service, 'validate_session', return_value={'id': 1}), \
                patch.object(routes, 'run_source_adapter') as run:
            response = client.post(path+'/hpd/refresh', json={}, headers={
                'X-Owner-Research': '1', 'Origin': 'http://localhost'})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json['status'], 'queued')
            result = client.get(path)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(len(result.json['sources']), 6)
            self.assertFalse(result.json['sources'][0]['can_refresh'])
            self.assertEqual(client.get('/api/property/3012980099/owner-sources').status_code, 404)
            run.assert_not_called()

    def test_pending_per_user_limit(self):
        for index in range(2, 14):
            self.execute('INSERT INTO buildings(id,bbl) VALUES(%s,%s)', (index, '3'+str(index).zfill(9)))
            routes.enqueue_source_refresh(self.conn, '3'+str(index).zfill(9), 'hpd', 1)
        with self.assertRaisesRegex(ValueError, '12 source refreshes'):
            self.enqueue()

    def test_schema_is_repeatable(self):
        self.execute(refresh.SCHEMA_SQL)
        self.execute(refresh.SCHEMA_SQL)
        self.assertEqual(self.scalar('SELECT count(*) FROM owner_source_jobs'), 0)

    def test_identical_ecb_success_advances_revision_and_blocks_stale_success_or_failure(self):
        fresh = dict(ecb_respondent_name='Alex Example', ecb_respondent_address='10 Example St',
                     ecb_last_checked='2026-09-30', ecb_respondent_issue_date='2026-01-01')
        with self.conn.cursor() as cur:
            ecb.update_building_tax_lien_data(cur, 1, fresh)
        self.conn.commit()
        revision = history.source_revisions(self.conn, [1], 'ecb')[1]
        with self.conn.cursor() as cur:
            ecb.update_building_tax_lien_data(cur, 1, dict(fresh, _source_revision=revision))
        self.conn.commit()
        self.assertGreater(self.scalar('SELECT revision FROM owner_source_snapshots'), revision)
        with self.conn.cursor() as cur:
            ecb.update_building_tax_lien_data(cur, 1, dict(fresh, _source_revision=revision,
                ecb_respondent_name='Stale Example', ecb_respondent_address='99 Old St'))
            ecb.update_building_tax_lien_data(cur, 1, dict(_source_revision=revision, _ecb_error='Older outage'))
        self.conn.commit()
        self.assertEqual(self.scalar('SELECT ecb_respondent_name FROM buildings'), 'Alex Example')
        self.assertIsNone(self.scalar('SELECT error FROM building_source_refresh'))
        self.assertEqual(self.scalar('SELECT count(*) FROM owner_source_history'), 1)

    def test_sos_stale_empty_result_or_error_cannot_clear_newer_success(self):
        fields = dict.fromkeys(['sos_principal_name', 'sos_principal_title', 'sos_principal_street',
            'sos_principal_city', 'sos_principal_state', 'sos_principal_zip', 'sos_entity_name',
            'sos_entity_status', 'sos_dos_id', 'sos_formation_date'])
        fields.update(building_id=1, lookup_source='ACRIS', _source_revision=None)
        sos.update_buildings_with_sos(self.conn, [dict(fields, sos_principal_name='Latest Example')])
        sos.update_buildings_with_sos(self.conn, [fields])
        self.assertEqual(sos.record_sos_failures(self.conn, [dict(building_id=1,
            _source_revision=None, error='An older failed request')]), 0)
        self.assertEqual(self.scalar('SELECT sos_principal_name FROM buildings'), 'Latest Example')
        self.assertIsNone(self.scalar('SELECT sos_last_error FROM buildings'))

    def test_older_acris_empty_or_failed_fetch_cannot_replace_newer_snapshot(self):
        def newer_refresh_then_return_empty(_):
            other = self.connect()
            try:
                with other.cursor() as cur:
                    before = history.capture_source_snapshot(cur, 1, 'acris')
                    cur.execute("UPDATE buildings SET sale_buyer_primary='Latest Example',sale_recorded_date='2026-01-01'")
                    history.record_source_snapshot(cur, 1, 'acris', before)
                other.commit()
            finally:
                other.close()
            return {'transactions': [], 'references': []}
        with patch.object(acris, 'get_acris_full_history', side_effect=newer_refresh_then_return_empty):
            self.assertEqual(acris.enrich_building_from_acris(self.conn, 1, '3012980066'), 0)
        self.assertEqual(self.scalar('SELECT sale_buyer_primary FROM buildings'), 'Latest Example')
        def newer_refresh_then_fail(bbl):
            newer_refresh_then_return_empty(bbl)
            raise RuntimeError('Old request failed')
        with patch.object(acris, 'get_acris_full_history', side_effect=newer_refresh_then_fail):
            self.assertEqual(acris.enrich_building_from_acris(self.conn, 1, '3012980066'), 0)
        self.assertEqual(self.scalar('SELECT sale_buyer_primary FROM buildings'), 'Latest Example')
        self.assertEqual(self.scalar('SELECT count(*) FROM owner_source_history'), 1)

    def test_successful_no_record_clears_current_owner_and_keeps_history(self):
        self.execute("UPDATE buildings SET owner_name_hpd='Previous Example'")
        with patch.object(property_api, 'get_hpd_data_for_bbl', return_value=(None, None)):
            routes.run_source_adapter(self.conn, 1, '3012980066', 'hpd')
        self.assertIsNone(self.scalar('SELECT owner_name_hpd FROM buildings'))
        self.assertEqual(self.scalar("SELECT before->'records'->0->>'name' FROM owner_source_history WHERE kind='change'"), 'Previous Example')
        self.assertEqual(self.scalar("SELECT after->'records' FROM owner_source_history WHERE kind='change'"), [])

    def test_ecb_history_failure_keeps_previous_facts_and_revision(self):
        fresh = dict(ecb_respondent_name='Alex Example', ecb_last_checked='2026-09-30')
        with self.conn.cursor() as cur:
            ecb.update_building_tax_lien_data(cur, 1, fresh)
        self.conn.commit()
        revision = self.scalar('SELECT revision FROM owner_source_snapshots')
        with patch.object(history, 'record_source_snapshot', side_effect=RuntimeError('disk failure')):
            with self.assertRaises(RuntimeError), self.conn.cursor() as cur:
                ecb.update_building_tax_lien_data(cur, 1, dict(fresh, ecb_respondent_name='Changed Example'))
        self.conn.rollback()
        self.assertEqual(self.scalar('SELECT ecb_respondent_name FROM buildings'), 'Alex Example')
        self.assertEqual(self.scalar('SELECT revision FROM owner_source_snapshots'), revision)

    def test_acris_failure_unwind_cannot_rewrite_a_new_success_diagnostic(self):
        self.enqueue('acris')
        job_id = self.scalar('SELECT id FROM owner_source_jobs')
        record_failure = acris._record_acris_failure
        def fail_then_success(conn, building_id, revision, error):
            # Deterministic interleaving: a fresh request commits after the old
            # adapter records its failure but before outer wrappers handle it.
            result = record_failure(conn, building_id, revision, error)
            other = self.connect()
            try:
                with other.cursor() as cur:
                    before = history.capture_source_snapshot(cur, building_id, 'acris')
                    cur.execute("UPDATE buildings SET sale_buyer_primary='Latest Example',acris_last_error=NULL,acris_last_enriched=NOW() WHERE id=%s", (building_id,))
                    history.record_source_snapshot(cur, building_id, 'acris', before)
                other.commit()
            finally:
                other.close()
            return result
        with patch.object(acris, 'get_acris_full_history', side_effect=RuntimeError('Older fetch failed')), \
                patch.object(acris, '_record_acris_failure', side_effect=fail_then_success):
            routes.process_source_job(self.connect, job_id)
        self.assertEqual(self.scalar('SELECT sale_buyer_primary FROM buildings'), 'Latest Example')
        self.assertIsNone(self.scalar('SELECT acris_last_error FROM buildings'))
        self.assertIsNone(self.scalar('SELECT error FROM building_source_refresh'))
        self.assertEqual(self.scalar('SELECT status FROM owner_source_jobs'), 'completed')

    def test_job_failure_guard_uses_revision_not_ambiguous_observation_clock(self):
        self.enqueue('ecb')
        job_id = self.scalar('SELECT id FROM owner_source_jobs')
        def commit_success_then_error(conn, building_id, bbl, source):
            with conn.cursor() as cur:
                ecb.update_building_tax_lien_data(cur, building_id,
                    {'ecb_respondent_name': 'Latest Example', 'ecb_last_checked': '2026-09-30'})
                # A source observation can have an older transaction timestamp;
                # the monotonic committed revision is the concurrency authority.
                cur.execute("UPDATE owner_source_snapshots SET checked_at='2001-01-01'")
            conn.commit()
            raise RuntimeError('Older wrapper failed')
        with patch.object(routes, 'run_source_adapter', side_effect=commit_success_then_error):
            routes.process_source_job(self.connect, job_id)
        self.assertIsNone(self.scalar('SELECT error FROM building_source_refresh'))
        self.assertEqual(self.scalar('SELECT status FROM owner_source_jobs'), 'completed')

    def test_acris_failure_keeps_revision_and_success_clock_unchanged(self):
        self.execute("UPDATE buildings SET sale_buyer_primary='Previous Example',acris_last_enriched='2025-01-01'")
        with patch.object(acris, 'get_acris_full_history', side_effect=RuntimeError('Unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'Unavailable'):
                acris.enrich_building_from_acris(self.conn, 1, '3012980066')
        self.assertEqual(self.scalar('SELECT acris_last_error FROM buildings'), 'Unavailable')
        self.assertEqual(self.scalar('SELECT acris_last_enriched FROM buildings'), '2025-01-01')
        self.assertEqual(self.scalar('SELECT count(*) FROM owner_source_snapshots'), 0)
        self.assertIsNone(self.scalar('SELECT checked_at FROM building_source_refresh'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
