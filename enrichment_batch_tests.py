"""Offline regressions for the October 4 cron timeout and transient failures."""
import os
os.environ.setdefault('DATABASE_URL', 'postgresql://unused@localhost:1/unused')

import unittest
from unittest.mock import MagicMock, Mock, patch

import _pipeline_path  # noqa: F401
import enrichment_batch as batch
import property_source_refresh as properties
import run_enrichment_pipeline as pipeline
import step6_enrich_signals as signals
import psycopg2


class BatchTests(unittest.TestCase):
    def run_batch(self, rows, process, clock=None, **kwargs):
        clock = clock if clock is not None else [0]
        with patch.dict(os.environ, ENRICHMENT_BATCH_SECONDS='100'), \
                patch.object(batch.time, 'monotonic', side_effect=lambda: clock[0]):
            return batch.run_checkpointed_batch(rows, process, workers=1,
                                                 label='Test', **kwargs)

    def test_only_failed_records_are_retried(self):
        process = Mock(side_effect=lambda row, retry: row != 2 or retry)
        result = self.run_batch([1, 2, 3], process)
        self.assertEqual(result, batch.BatchResult(3, 0, 0))
        self.assertEqual([call.args for call in process.call_args_list],
                         [(1, False), (2, False), (3, False), (2, True)])

    def test_unresolved_failure_is_not_hidden(self):
        process = Mock(return_value=False)
        self.assertEqual(self.run_batch([1], process), batch.BatchResult(0, 1, 0))
        self.assertEqual(process.call_count, 2)

    def test_worker_exception_can_recover(self):
        self.assertEqual(self.run_batch([1], Mock(side_effect=[RuntimeError('offline'), True])),
                         batch.BatchResult(1, 0, 0))

    def test_budget_drains_active_work_without_starting_remaining_records(self):
        clock = [0]
        def process(row, retry):
            clock[0] = 91
            return True
        fn = Mock(side_effect=process)
        self.assertEqual(self.run_batch([1, 2, 3], fn, clock), batch.BatchResult(1, 0, 2))
        fn.assert_called_once_with(1, False)

    def test_retry_has_reserved_time_after_initial_budget_expires(self):
        clock = [0]
        def process(row, retry):
            clock[0] = 91
            return retry
        self.assertEqual(self.run_batch([1, 2], process, clock), batch.BatchResult(1, 0, 1))

    def test_expired_retry_budget_keeps_failure_visible(self):
        clock = [0]
        def process(row, retry):
            clock[0] = 101
            return False
        fn = Mock(side_effect=process)
        self.assertEqual(self.run_batch([1, 2], fn, clock), batch.BatchResult(0, 1, 1))
        fn.assert_called_once_with(1, False)

    def test_outage_stops_admitting_work_at_error_limit(self):
        process = Mock(return_value=False)
        self.assertEqual(self.run_batch(list(range(20)), process, error_limit=2),
                         batch.BatchResult(0, 2, 18))
        self.assertEqual(process.call_count, 4)


class SignalWriteTests(unittest.TestCase):
    building = dict(id=1, bbl='3012980066', bin='3000000', building_sqft=2000)

    def test_connection_and_commit_retry_do_not_repeat_external_lookups(self):
        broken, healthy = MagicMock(), MagicMock()
        broken.commit.side_effect = psycopg2.OperationalError('connection dropped')
        fetched = False
        def fetch(*args):
            nonlocal fetched
            fetched = True
            return {'eviction_count': 7}, []
        responses = iter([psycopg2.OperationalError('restarting'), broken, healthy])
        def connect(*args, **kwargs):
            self.assertTrue(fetched, 'Opened database before external requests finished')
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response
        with patch.object(signals, 'enrich_signals_for_building', side_effect=fetch) as fetcher, \
                patch.object(signals.psycopg2, 'connect', side_effect=connect) as connector, \
                patch.object(signals.time, 'sleep'):
            self.assertTrue(signals._process_building(self.building, 1, 1))
        fetcher.assert_called_once()
        self.assertEqual(connector.call_count, 3)
        broken.close.assert_called_once()
        broken.rollback.assert_not_called()
        healthy.commit.assert_called_once()
        cur = healthy.cursor.return_value.__enter__.return_value
        sql, values = cur.execute.call_args.args
        self.assertIn('signals_last_enriched = NOW()', sql)
        self.assertEqual(values, [7, signals.SIGNALS_ENRICHMENT_VERSION, 1])

    def test_partial_source_failure_preserves_failed_source_data_and_freshness(self):
        conn = MagicMock()
        with patch.object(signals.psycopg2, 'connect', return_value=conn):
            signals._write_signal_fields(self.building, {'eviction_count': 7}, ['fisp: HTTP 503'])
        sql, values = conn.cursor.return_value.__enter__.return_value.execute.call_args.args
        self.assertNotIn('signals_last_enriched', sql)
        self.assertNotIn('signals_enrichment_version', sql)
        self.assertNotIn('fisp_', sql)
        self.assertEqual(values, [7, 'fisp: HTTP 503', 1])

    def test_persistent_database_outage_returns_failure(self):
        with patch.object(signals.psycopg2, 'connect', side_effect=psycopg2.OperationalError('offline')), \
                patch.object(signals, 'enrich_signals_for_building', return_value=({}, [])), \
                patch.object(signals.time, 'sleep'):
            self.assertFalse(signals._process_building(self.building, 1, 1))

    def test_non_connection_error_is_not_retried_as_connection_failure(self):
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.execute.side_effect = psycopg2.ProgrammingError('bad SQL')
        with patch.object(signals.psycopg2, 'connect', return_value=conn) as connect:
            with self.assertRaises(psycopg2.ProgrammingError):
                signals._write_signal_fields(self.building, {}, [])
        connect.assert_called_once()
        conn.close.assert_called_once()


class EntryPointTests(unittest.TestCase):
    def test_pipeline_soft_budget_is_below_custom_hard_timeout(self):
        with patch.dict(os.environ, PIPELINE_STEP_TIMEOUT_SECONDS='200', ENRICHMENT_BATCH_SECONDS='999'), \
                patch.object(pipeline.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertTrue(pipeline.run_script('step2_enrich_from_pluto.py', 'test'))
        self.assertEqual(run.call_args.kwargs['timeout'], 200)
        self.assertEqual(float(run.call_args.kwargs['env']['ENRICHMENT_BATCH_SECONDS']), 150)

    def test_property_step_exits_nonzero_when_retry_fails(self):
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [(1, '3012980066', None)]
        with patch.object(batch, 'run_checkpointed_batch', return_value=batch.BatchResult(0, 1, 0)):
            with self.assertRaises(SystemExit) as exc:
                properties.run_property_refresh(lambda: conn)
        self.assertEqual(exc.exception.code, 1)

    def test_signal_step_exits_nonzero_when_retry_fails(self):
        conn = MagicMock()
        conn.cursor.return_value.fetchall.side_effect = [[{}] * 4, [SignalWriteTests.building]]
        conn.cursor.return_value.fetchone.return_value = (1,)
        with patch.object(signals.psycopg2, 'connect', return_value=conn), \
                patch.object(signals, 'preflight_signal_sources'), \
                patch.object(signals, 'run_checkpointed_batch', return_value=batch.BatchResult(0, 1, 0)):
            with self.assertRaises(SystemExit) as exc:
                signals.main()
        self.assertEqual(exc.exception.code, 1)


if __name__ == '__main__':
    unittest.main()
