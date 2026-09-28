"""Regressions for the September 28 Safety/CO pipeline failures.

Run: python -m unittest enrichment_crash_tests -v
Optional database tests use only ENRICHMENT_TEST_DATABASE_URL.
"""
import os
os.environ.setdefault('DATABASE_URL', 'postgresql://unused@localhost:1/unused')

import unittest
import uuid
from datetime import date
from unittest.mock import Mock, patch

import psycopg2
import requests
import _pipeline_path  # noqa: F401
import socrata_client as socrata
import step6_enrich_signals as signals
import sync_dob_safety as safety


class CertificateRegressionTests(unittest.TestCase):
    def test_placeholder_bins_do_not_fetch_unrelated_certificates(self):
        class COClient(socrata.SocrataClient):
            def get_columns(self, dataset):
                return {'bin', 'bbl', 'borough', 'block', 'lot',
                        'c_o_issue_date', 'issue_type', 'job_number'}

            def get(self, dataset, **params):
                where = params['$where']
                # Reproduce the real 3,959/2,839-row placeholder BIN groups.
                if where in ("bin='3000000'", "bin='4000000'"):
                    return [{'c_o_issue_date': '2020-01-01'}] * params['$limit']
                self.assert_parcel(where)
                return [{'c_o_issue_date': '2026-08-01', 'issue_type': 'Final',
                         'job_number': 'PROPERTY-1'}] if dataset == 'dob_co_bis' else []

        for bbl, bin_number in [('3012420070', '3000000'), ('4105190039', '4000000')]:
            with self.subTest(bin_number=bin_number):
                client = COClient()
                client.assert_parcel = lambda where: self.assertIn(f"bbl='{bbl}'", where)
                with patch.object(signals, 'client', client):
                    result = signals.fetch_certificates_of_occupancy(bbl, bin_number)
                self.assertEqual(result['co_count'], 1)
                self.assertEqual(result['latest_co_job_number'], 'PROPERTY-1')

    def test_legacy_parcel_fallback_matches_borough_name_and_five_digit_lot(self):
        client = Mock()
        client.get_columns.return_value = {'borough', 'block', 'lot', 'c_o_issue_date'}
        client.get_all.return_value = []
        with patch.object(signals, 'client', client):
            signals._fetch_cos_from('dob_co_bis', None, '3012420070')
        where = client.get_all.call_args.kwargs['$where']
        for value in ('BROOKLYN', '01242', '1242', '00070', '0070', '70'):
            self.assertIn(f"'{value}'", where)

    def test_valid_bin_keeps_building_specific_lookup(self):
        client = Mock()
        client.get_columns.return_value = {'bin_number', 'bbl', 'c_o_issue_date'}
        client.get_all.return_value = []
        with patch.object(signals, 'client', client):
            signals._fetch_cos_from('dob_co_bis', '3012345', '3012420070')
        self.assertEqual(client.get_all.call_args.kwargs['$where'], "bin_number='3012345'")

    def test_bad_or_wrong_borough_bins_fall_back_to_parcel(self):
        for bin_number in ('0', '0000000', '3000000', 'None', '301234', '4012345', None):
            with self.subTest(bin_number=bin_number):
                client = Mock()
                client.get_columns.return_value = {'bin', 'bbl', 'c_o_issue_date'}
                client.get_all.return_value = []
                with patch.object(signals, 'client', client):
                    signals._fetch_cos_from('dob_co_bis', bin_number, '3012420070')
                self.assertEqual(client.get_all.call_args.kwargs['$where'], "bbl='3012420070'")

    def test_missing_geocoded_bbl_can_match_raw_parcel(self):
        client = Mock()
        client.get_columns.return_value = {'bin', 'bbl', 'borough', 'block', 'lot'}
        client.get_all.return_value = []
        with patch.object(signals, 'client', client):
            signals._fetch_cos_from('dob_co_bis', '3000000', '3012420070')
        where = client.get_all.call_args.kwargs['$where']
        self.assertIn("bbl='3012420070'", where)
        self.assertIn(' OR ', where)
        self.assertIn("'00070'", where)

    def test_dob_now_dates_participate_in_latest_certificate_selection(self):
        client = Mock()
        client.get_columns.side_effect = [
            {'bin_number', 'c_o_issue_date', 'issue_type', 'job_number'},
            {'bin', 'c_of_o_issuance_date', 'c_of_o_filing_type', 'application_number'},
        ]
        client.get_all.side_effect = [
            [{'c_o_issue_date': '2025-01-01T00:00:00.000', 'issue_type': 'Final'}],
            [{'c_of_o_issuance_date': '06/18/26  9:43:09 AM',
              'c_of_o_filing_type': 'Temporary', 'application_number': 'NOW-1'}],
        ]
        with patch.object(signals, 'client', client):
            result = signals.fetch_certificates_of_occupancy('3012420070', '3012345')
        self.assertEqual(result['co_count'], 2)
        self.assertEqual(result['latest_co_date'], date(2026, 6, 18))
        self.assertEqual(result['latest_co_job_number'], 'NOW-1')

    def test_date_formats_and_bad_dates(self):
        for value in ('2026-06-18T00:00:00.000', '06/18/2026', '20260618',
                      '06/18/26  9:43:09 AM'):
            self.assertEqual(signals.parse_any_date(value), date(2026, 6, 18))
        self.assertIsNone(signals.parse_any_date('02/30/26 11:00:00 AM'))

    def test_placeholder_complaints_remain_unknown(self):
        client = Mock()
        with patch.object(signals, 'client', client):
            result = signals.fetch_dob_complaints('3012420070', '3000000')
        self.assertIsNone(result['dob_complaint_count'])
        client.get_all.assert_not_called()

    def test_fisp_uses_parcel_for_placeholder_bin(self):
        client = Mock()
        client.get_columns.return_value = {'bin', 'bbl', 'borough', 'block', 'lot'}
        client.get_all.return_value = []
        with patch.object(signals, 'client', client):
            signals.fetch_fisp('3012420070', '3000000')
        self.assertIn("bbl='3012420070'", client.get_all.call_args.kwargs['$where'])

    def test_source_failure_does_not_publish_partial_co_count(self):
        client = Mock()
        client.get_columns.return_value = {'bin', 'c_o_issue_date'}
        client.get_all.side_effect = [[{'c_o_issue_date': '2025-01-01'}],
                                      socrata.SocrataError('source unavailable')]
        with patch.object(signals, 'client', client):
            with self.assertRaises(socrata.SocrataError):
                signals.fetch_certificates_of_occupancy('3012420070', '3012345')


class SafetyRegressionTests(unittest.TestCase):
    def test_fetch_uses_disjoint_borough_ranges(self):
        client = Mock()
        client.get_all.side_effect = [
            [{'bbl': str(boro * 1_000_000_000 + 10001),
              'violation_status': 'Active', 'violation_count': '1'}]
            for boro in range(1, 6)
        ]
        rows = safety.fetch_safety_snapshot(client)
        self.assertEqual(len(safety.aggregate_safety(rows)), 5)
        for boro, call in enumerate(client.get_all.call_args_list, 1):
            self.assertEqual(call.kwargs['$where'],
                             f'bbl >= {boro * 1_000_000_000} AND bbl < {(boro + 1) * 1_000_000_000}')
            self.assertEqual(call.kwargs['$order'], 'bbl, violation_status')

    def test_timed_out_range_splits_without_duplicating_partial_rows(self):
        client = Mock()
        row = {'bbl': '1000010001', 'violation_status': 'Active', 'violation_count': '2'}
        client.get_all.side_effect = [socrata.SocrataTransientError('read timeout'),
                                      [row], [], [], [], [], []]
        self.assertEqual(safety.fetch_safety_snapshot(client), [row])
        wheres = [call.kwargs['$where'] for call in client.get_all.call_args_list]
        self.assertEqual(wheres[1:3], ['bbl >= 1000000000 AND bbl < 1500000000',
                                     'bbl >= 1500000000 AND bbl < 2000000000'])

    def test_timeout_on_later_page_discards_the_original_partial_range(self):
        client = socrata.SocrataClient()
        row = {'bbl': '1000010001', 'violation_status': 'Active', 'violation_count': '2'}

        def fetch(_dataset, **params):
            where = params['$where']
            if where == 'bbl >= 1000000000 AND bbl < 2000000000':
                if params['$offset']:
                    raise socrata.SocrataTransientError('second page timed out')
                return [row] * params['$limit']
            if where == 'bbl >= 1000000000 AND bbl < 1500000000':
                return [row]
            return []

        with patch.object(client, 'get', side_effect=fetch):
            rows = safety.fetch_safety_snapshot(client)
        self.assertEqual(safety.aggregate_safety(rows), [('1000010001', 2, 2)])

    def test_permanent_query_failure_does_not_split_or_write(self):
        client = Mock()
        client.get_all.side_effect = socrata.SocrataError('HTTP 400')
        with patch.object(safety, 'SocrataClient', return_value=client), \
                patch.object(safety.psycopg2, 'connect') as connect:
            with self.assertRaises(socrata.SocrataError):
                safety.main()
        self.assertEqual(client.get_all.call_count, 1)
        connect.assert_not_called()

    def test_later_partition_failure_does_not_write(self):
        client = Mock()
        client.get_all.side_effect = [
            [{'bbl': '1000010001', 'violation_status': 'Active', 'violation_count': '2'}],
            socrata.SocrataError('invalid row response'),
        ]
        with patch.object(safety, 'SocrataClient', return_value=client), \
                patch.object(safety.psycopg2, 'connect') as connect:
            with self.assertRaises(socrata.SocrataError):
                safety.main()
        connect.assert_not_called()

    def test_repeated_outage_has_bounded_splitting(self):
        client = Mock()
        client.get_all.side_effect = socrata.SocrataTransientError('offline')
        with self.assertRaises(socrata.SocrataTransientError):
            safety.fetch_safety_snapshot(client)
        self.assertLessEqual(client.get_all.call_count, 5)

    def test_complete_empty_snapshot_is_rejected_without_writes(self):
        client = Mock()
        client.get_all.return_value = []
        with patch.object(safety, 'SocrataClient', return_value=client), \
                patch.object(safety.psycopg2, 'connect') as connect:
            with self.assertRaises(socrata.SocrataError):
                safety.main()
        connect.assert_not_called()

    def test_transient_http_failure_is_distinct_from_query_errors(self):
        client = socrata.SocrataClient(max_retries=1)
        with patch.object(client.session, 'get', return_value=Mock(status_code=503)), \
                patch.object(socrata.time, 'sleep'):
            with self.assertRaises(socrata.SocrataTransientError):
                client.get('dob_safety_violations')
        with patch.object(client.session, 'get', return_value=Mock(status_code=400, text='bad query')):
            with self.assertRaises(socrata.SocrataError) as raised:
                client.get('dob_safety_violations')
        self.assertNotIsInstance(raised.exception, socrata.SocrataTransientError)

    def test_read_timeout_retries_and_remains_a_socrata_error(self):
        client = socrata.SocrataClient(max_retries=2)
        with patch.object(client.session, 'get', side_effect=requests.ReadTimeout('read timeout')) as get, \
                patch.object(socrata.time, 'sleep'):
            with self.assertRaises(socrata.SocrataTransientError) as raised:
                client.get('dob_safety_violations')
        self.assertIsInstance(raised.exception, socrata.SocrataError)
        self.assertEqual(get.call_count, 2)


@unittest.skipUnless(os.getenv('ENRICHMENT_TEST_DATABASE_URL'), 'disposable PostgreSQL DSN not supplied')
class SafetyDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.schema = 'crash_' + uuid.uuid4().hex
        self.dsn = os.environ['ENRICHMENT_TEST_DATABASE_URL']
        self.admin = psycopg2.connect(self.dsn)
        self.admin.autocommit = True
        with self.admin.cursor() as cur:
            cur.execute(f'CREATE SCHEMA {self.schema}')
            cur.execute(f'SET search_path TO {self.schema}')
            cur.execute('''CREATE TABLE buildings (id INTEGER PRIMARY KEY, bbl TEXT,
                dob_safety_violation_count INTEGER, dob_safety_open_violations INTEGER,
                dob_safety_last_checked TIMESTAMP)''')
            cur.execute("INSERT INTO buildings VALUES (1,'1000010001',9,8,NULL),(2,'2000010001',7,6,NULL)")

    def tearDown(self):
        with self.admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA {self.schema} CASCADE')
        self.admin.close()

    def saved_rows(self):
        with self.admin.cursor() as cur:
            cur.execute('SELECT dob_safety_violation_count,dob_safety_open_violations,dob_safety_last_checked IS NOT NULL FROM buildings ORDER BY id')
            return cur.fetchall()

    def test_complete_snapshot_updates_counts_and_zeros_absent_parcels(self):
        rows = [{'bbl': '1000010001', 'violation_status': 'Active', 'violation_count': '2'},
                {'bbl': '1000010001', 'violation_status': 'Dismissed', 'violation_count': '3'}]
        client = Mock()
        client.get_all.side_effect = [rows, [], [], [], []]
        conn = psycopg2.connect(self.dsn, options=f'-c search_path={self.schema}')
        try:
            with patch.object(safety, 'SocrataClient', return_value=client), \
                    patch.object(safety.psycopg2, 'connect', return_value=conn):
                safety.main()
        finally:
            conn.close()
        self.assertEqual(self.saved_rows(), [(5, 2, True), (0, 0, True)])

    def test_partial_snapshot_preserves_counts_and_freshness(self):
        client = Mock()
        client.get_all.side_effect = [
            [{'bbl': '1000010001', 'violation_status': 'Active', 'violation_count': '2'}],
            socrata.SocrataError('incomplete source'),
        ]
        with patch.object(safety, 'SocrataClient', return_value=client), \
                patch.object(safety.psycopg2, 'connect') as connect:
            with self.assertRaises(socrata.SocrataError):
                safety.main()
        connect.assert_not_called()
        self.assertEqual(self.saved_rows(), [(9, 8, False), (7, 6, False)])

    def test_database_write_failure_rolls_back_the_snapshot(self):
        with self.admin.cursor() as cur:
            cur.execute('ALTER TABLE buildings ADD CHECK (dob_safety_violation_count > 0)')
        client = Mock()
        client.get_all.side_effect = [[
            {'bbl': '1000010001', 'violation_status': 'Active', 'violation_count': '2'}
        ], [], [], [], []]
        conn = psycopg2.connect(self.dsn, options=f'-c search_path={self.schema}')
        try:
            with patch.object(safety, 'SocrataClient', return_value=client), \
                    patch.object(safety.psycopg2, 'connect', return_value=conn):
                with self.assertRaises(psycopg2.errors.CheckViolation):
                    safety.main()
        finally:
            conn.close()
        self.assertEqual(self.saved_rows(), [(9, 8, False), (7, 6, False)])


if __name__ == '__main__':
    unittest.main()
