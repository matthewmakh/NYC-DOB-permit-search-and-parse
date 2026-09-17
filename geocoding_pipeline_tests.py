#!/usr/bin/env python3
"""Offline regression tests for geocoding and SOS recovery (no live services)."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
import requests
import psycopg2
import psycopg2.extras

import _pipeline_path  # noqa: F401
import nyc_geocoding as geo
import ny_sos_lookup as sos
import geocode_permits as job


def response(payload, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    return result


def feature(bbl='3012980066', house='521', street='MONTGOMERY STREET',
            coords=(-73.949363, 40.664236), match='exact'):
    return {'properties': {'addendum': {'pad': {'bbl': bbl}}, 'housenumber': house,
                           'street': street, 'match_type': match},
            'geometry': {'coordinates': list(coords)}}


class GeocoderTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_subscription_header_and_legacy_config(self):
        for env_name in ('NYC_GEOCLIENT_SUBSCRIPTION_KEY', 'NYC_GEOCLIENT_APP_KEY', 'NYC_GEOCLIENT_APP_ID'):
            with patch.dict(os.environ, {env_name: 'test-key'}, clear=True):
                self.assertEqual(geo.geoclient_headers(), {'Ocp-Apim-Subscription-Key': 'test-key'})
        with patch.dict(os.environ, {'NYC_GEOCLIENT_SUBSCRIPTION_KEY': 'v2', 'NYC_GEOCLIENT_APP_KEY': 'old'}):
            self.assertEqual(geo.geoclient_key(), 'v2')

    @patch('nyc_geocoding.requests.get')
    def test_auto_add_uses_the_same_authentication(self, get):
        with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://test:test@localhost/test',
                                     'NYC_GEOCLIENT_SUBSCRIPTION_KEY': 'test-key'}):
            import property_lookup
            get.return_value = response({'address': {}})
            data, error = property_lookup._geoclient_get('address', {'houseNumber': '1'})
            self.assertIsNone(error)
            self.assertEqual(data, {'address': {}})
            self.assertEqual(get.call_args.kwargs['headers'], {'Ocp-Apim-Subscription-Key': 'test-key'})

    def test_street_names_keep_borough_words_and_queens_house_numbers(self):
        self.assertEqual(geo.parse_address('1 MANHATTAN PLAZA'), ('1', 'MANHATTAN PLAZA', None))
        self.assertEqual(geo.parse_address('47-22 141TH ST, QUEENS, NY 11355'),
                         ('47-22', '141TH ST', 'Queens'))
        self.assertEqual(geo._street('141TH St.'), geo._street('141 Street'))

    @patch('nyc_geocoding.requests.get')
    def test_geoclient_sends_key_and_checks_bbl(self, get):
        os.environ['NYC_GEOCLIENT_APP_KEY'] = 'test-key'
        get.return_value = response({'address': {'bbl': '3012980066', 'latitude': 40.664236, 'longitude': -73.949363}})
        result = geo.PermitGeocoder().lookup('521 Montgomery St', '3012980066')
        self.assertTrue(result.found)
        self.assertEqual(get.call_args.kwargs['headers'], {'Ocp-Apim-Subscription-Key': 'test-key'})
        self.assertEqual(get.call_args.kwargs['params']['borough'], 'Brooklyn')

    @patch('nyc_geocoding.requests.get')
    def test_wrong_geoclient_parcel_is_rejected_and_bbl_endpoint_recovers(self, get):
        os.environ['NYC_GEOCLIENT_SUBSCRIPTION_KEY'] = 'key'
        data = {'bbl': '3012980067', 'latitude': 40.664236, 'longitude': -73.949363}
        get.side_effect = [response({'address': data}), response({'bbl': {**data, 'bbl': '3012980066'}})]
        self.assertTrue(geo.PermitGeocoder().lookup('521 Montgomery St', '3012980066').found)
        self.assertTrue(get.call_args.args[0].endswith('/bbl'))

    @patch('nyc_geocoding.requests.get')
    def test_unauthorized_geoclient_is_not_repeated(self, get):
        os.environ['NYC_GEOCLIENT_APP_KEY'] = 'key'
        get.side_effect = [response({}, 401), response({'features': []}), response({'features': []})]
        client = geo.PermitGeocoder()
        for address in ('1 CENTRE STREET', '2 CENTRE STREET'):
            result = client.lookup(address, '1000010001')
            self.assertIn('401', result.error)
        self.assertEqual(sum('api.nyc.gov' in call.args[0] for call in get.call_args_list), 1)

    @patch('nyc_geocoding.requests.get')
    def test_fallback_recovers_unauthorized_without_error(self, get):
        os.environ['NYC_GEOCLIENT_APP_KEY'] = 'key'
        get.side_effect = [response({}, 401), response({'features': [feature()]})]
        hit = geo.PermitGeocoder().lookup('521 Montgomery St', '3012980066')
        self.assertTrue(hit.found)
        self.assertFalse(hit.error)

    @patch('nyc_geocoding.requests.get')
    def test_matching_parcel_is_cached(self, get):
        get.return_value = response({'features': [feature()]})
        client = geo.PermitGeocoder()
        for _ in range(2):
            self.assertTrue(client.lookup('521 Montgomery St', '3012980066').found)
        get.assert_called_once()
        self.assertIn('Brooklyn', get.call_args.kwargs['params']['text'])

    @patch('nyc_geocoding.requests.get')
    def test_wrong_parcel_or_outside_borough_never_saved(self, get):
        candidates = [feature(bbl='3012980067'), feature(coords=(-74.416319, 41.440573)),
                      feature(coords=(-74.20, 40.58)), feature(coords=(float('nan'), 40.664236))]
        get.return_value = response({'features': candidates})
        self.assertFalse(geo.PermitGeocoder().lookup('521 Montgomery St', '3012980066').found)

    @patch('nyc_geocoding.requests.get')
    def test_no_bbl_requires_exact_full_address_and_borough(self, get):
        for candidate, expected in [(feature(), True), (feature(house='523'), False),
                                    (feature(street='MONTGOMERY AVENUE'), False),
                                    (feature(match='fallback'), False),
                                    (feature(bbl='1012980066'), False)]:
            get.return_value = response({'features': [candidate]})
            hit = geo.PermitGeocoder().lookup('521 Montgomery St, Brooklyn')
            self.assertEqual(hit.found, expected)
        get.return_value = response({'features': [feature()]})
        self.assertFalse(geo.PermitGeocoder().lookup('521 Montgomery St').found)

    @patch('nyc_geocoding.requests.get')
    def test_service_errors_retry_but_real_misses_are_cached(self, get):
        client = geo.PermitGeocoder()
        get.side_effect = [requests.Timeout(), response({'features': []})]
        self.assertTrue(client.lookup('521 Montgomery St', '3012980066').error)
        self.assertFalse(client.lookup('521 Montgomery St', '3012980066').error)
        client.lookup('521 Montgomery St', '3012980066')
        self.assertEqual(get.call_count, 2)

    @patch('nyc_geocoding.requests.get')
    def test_invalid_json_shape_is_a_service_error(self, get):
        get.return_value = response({'message': 'Service unavailable'})
        self.assertTrue(geo.PermitGeocoder().lookup('521 Montgomery St', '3012980066').error)
        get.return_value = response({'features': [{'properties': ['invalid'], 'geometry': {}}]})
        self.assertTrue(geo.PermitGeocoder().lookup('521 Montgomery St', '3012980066').error)


class RepairTests(unittest.TestCase):
    def test_old_log_parser_only_selects_geocoder_successes(self):
        text = '[1/2] Permit #12: ABC\n✅ Success: 40.123456, -73.234567\n[2/2] Permit #13: DEF\nCould not geocode address\n'
        self.assertEqual(job.logged_geocodes(text), {12: (40.123456, -73.234567)})

    def run_repair(self, apply=False, changed=False, hit=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        log = Path(temp.name) / 'run.log'
        log.write_text('[1/1] Permit #12: 80 SPRAGUE AVENUE\n✅ Success: 41.440573, -74.416319\n')
        report = Path(temp.name) / 'report.json'
        conn = Mock()
        cur = Mock()
        conn.cursor.return_value.__enter__ = Mock(return_value=cur)
        conn.cursor.return_value.__exit__ = Mock(return_value=False)
        cur.fetchall.return_value = [{'id':12, 'address':'80 SPRAGUE AVENUE', 'bbl':'5078260220',
                                     'latitude':40.5034 if changed else 41.440573,
                                     'longitude':-74.416319}]
        cur.rowcount = 1
        with patch.object(job, 'get_db_connection', return_value=conn), \
             patch.object(job, 'PermitGeocoder') as cls, patch.object(job.time, 'sleep'):
            cls.return_value.lookup.return_value = hit or geo.GeocodeResult(40.5034, -74.2372, 'NYC GeoSearch')
            status = job.repair_logged_geocodes(log, report, apply)
        return cur, conn, json.loads(report.read_text()), status

    def test_repair_preview_never_writes(self):
        cur, conn, report, status = self.run_repair()
        self.assertEqual(status, 0)
        conn.commit.assert_not_called()
        self.assertEqual(cur.execute.call_count, 1)
        self.assertEqual(report['repairs'][0]['latitude'], 41.440573)
        self.assertEqual(report['repairs'][0]['action'], 'replace')

    def test_repair_preserves_a_newer_fix(self):
        cur, _, report, _ = self.run_repair(apply=True, changed=True)
        self.assertEqual(report['repairs'][0]['action'], 'skip_changed_since_log')
        self.assertFalse(any('UPDATE permits' in call.args[0] for call in cur.execute.call_args_list))

    def test_repair_apply_compares_saved_values(self):
        cur, _, _, _ = self.run_repair(apply=True)
        update = cur.execute.call_args
        self.assertIn('latitude IS NOT DISTINCT FROM', update.args[0])
        self.assertEqual(update.args[1], (40.5034, -74.2372, False, 12, 41.440573, -74.416319))

    def test_repair_outage_does_not_clear_old_values(self):
        cur, _, report, status = self.run_repair(apply=True, hit=geo.GeocodeResult(error='GeoSearch HTTP 503'))
        self.assertEqual(status, 1)
        self.assertEqual(report['repairs'][0]['action'], 'unresolved_service_error')
        self.assertFalse(any('UPDATE permits' in call.args[0] for call in cur.execute.call_args_list))

    def test_confirmed_no_match_clears_unverified_coordinates(self):
        cur, _, report, _ = self.run_repair(apply=True, hit=geo.GeocodeResult())
        self.assertEqual(report['repairs'][0]['action'], 'clear_unverified')
        self.assertEqual(cur.execute.call_args.args[1][:3], (None, None, True))


class SOSTests(unittest.IsolatedAsyncioTestCase):
    async def lookup(self, events):
        seen = []
        def handler(request):
            seen.append(request.url.path)
            event = events.pop(0)
            if isinstance(event, Exception):
                raise event
            status, data, *headers = event
            return httpx.Response(status, json=data, headers=headers[0] if headers else {})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            lookup = sos.AsyncNYSOSClient()
            lookup._client = client
            with patch.object(sos.asyncio, 'sleep', new_callable=AsyncMock) as sleep:
                result = await lookup.lookup('ABC LLC')
                return result, seen, sleep

    @staticmethod
    def search():
        return 200, {'entitySearchResultList': [{'dosID':'123', 'entityName':'ABC LLC'}]}

    @staticmethod
    def details():
        return 200, {'entityGeneralInfo': {'entityName':'ABC LLC', 'entityStatus':'Active'}}

    async def test_temporary_http_failures_recover(self):
        for status in (408, 429, 500, 502, 503, 504):
            result, _, sleep = await self.lookup([(status, {}, {'Retry-After':'7'}), self.search(), self.details()])
            self.assertTrue(result.found)
            self.assertFalse(result.error)
            sleep.assert_awaited_once_with(7.0)

    async def test_exhausted_retries_keep_last_error_and_do_not_sleep_again(self):
        result, seen, sleep = await self.lookup([(503, {})] * 3)
        self.assertFalse(result.found)
        self.assertIn('HTTP 503 (attempt 3/3)', result.error)
        self.assertEqual(len(seen), 3)
        self.assertEqual(sleep.await_count, 2)

    async def test_permanent_http_error_is_not_retried(self):
        result, seen, sleep = await self.lookup([(403, {})])
        self.assertIn('HTTP 403', result.error)
        sleep.assert_not_awaited()
        self.assertEqual(len(seen), 1)

    async def test_transport_failure_during_details_recovers(self):
        result, _, sleep = await self.lookup([self.search(), httpx.ReadError('connection reset'), self.search(), self.details()])
        self.assertTrue(result.found)
        self.assertFalse(result.error)
        self.assertEqual(sleep.await_count, 1)

    async def test_invalid_payload_is_not_saved_as_a_successful_miss(self):
        result, _, _ = await self.lookup([(200, {'error':'upstream problem'})] * 3)
        self.assertFalse(result.found)
        self.assertIn('ValueError', result.error)

    async def test_invalid_details_are_not_saved_as_found(self):
        result, _, _ = await self.lookup([self.search(), (200, {})] * 3)
        self.assertFalse(result.found)
        self.assertIn('details: ValueError', result.error)

    async def test_actual_no_matches_are_complete(self):
        result, _, sleep = await self.lookup([(200, {'entitySearchResultList': []})])
        self.assertFalse(result.found)
        self.assertFalse(result.error)
        sleep.assert_not_awaited()

    def test_retry_after_is_bounded(self):
        with patch.object(sos.random, 'random', return_value=0):
            self.assertEqual(sos._retry_delay(0, httpx.Response(429, headers={'Retry-After':'999'})), 60)
            self.assertEqual(sos._retry_delay(0, httpx.Response(429, headers={'Retry-After':'bad'})), 1)


@unittest.skipUnless(os.getenv('GEOCODE_TEST_DATABASE_URL'), 'optional disposable PostgreSQL test database')
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg2.connect(os.environ['GEOCODE_TEST_DATABASE_URL'],
                                     cursor_factory=psycopg2.extras.RealDictCursor)
        self.addCleanup(self.conn.close)
        with self.conn.cursor() as cur:
            cur.execute('''CREATE TEMP TABLE permits (
                id INTEGER PRIMARY KEY, address TEXT, bbl TEXT,
                latitude DOUBLE PRECISION, longitude DOUBLE PRECISION,
                geocode_failed BOOLEAN DEFAULT FALSE)''')
        self.conn.commit()
        self.proxy = Mock(wraps=self.conn)
        self.proxy.close = Mock()
        self.connection_patch = patch.object(job, 'get_db_connection', return_value=self.proxy)
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)

    def test_sos_failure_selection_and_checkpoint_preservation(self):
        import step5_enrich_from_sos as step5
        with self.conn.cursor() as cur:
            cur.execute('''CREATE TEMP TABLE buildings (
                id INTEGER, bbl TEXT, address TEXT, sale_buyer_primary TEXT,
                sale_date DATE, sale_recorded_date TIMESTAMPTZ, current_owner_name TEXT,
                owner_name_rpad TEXT, owner_name_hpd TEXT, sos_last_error TEXT,
                sos_last_error_at TIMESTAMPTZ, sos_last_enriched TIMESTAMPTZ,
                sos_lookup_attempted BOOLEAN, sos_entity_name TEXT)''')
            cur.execute("INSERT INTO buildings(id,bbl,current_owner_name,sos_last_enriched,sos_lookup_attempted,sos_last_error) VALUES (1,'3012980066','ABC LLC',NOW(),TRUE,'HTTP 503'),(2,'3012980067','XYZ LLC',NOW(),TRUE,NULL),(3,'3012980068','NEW LLC',NULL,FALSE,NULL)")
        self.conn.commit()
        # SOS selection uses ordinary tuple cursors, unlike the permit geocoder.
        proxy = Mock(wraps=self.conn)
        proxy.cursor.side_effect = lambda: self.conn.cursor(cursor_factory=psycopg2.extensions.cursor)
        rows = step5.get_buildings_needing_sos(proxy, retry_failures=True)
        self.assertEqual([row['id'] for row in rows], [1])
        self.assertEqual({row['id'] for row in step5.get_buildings_needing_sos(proxy)}, {1, 3})
        with self.conn.cursor() as cur:
            cur.execute('SELECT sos_last_enriched FROM buildings WHERE id=1')
            before = cur.fetchone()['sos_last_enriched']
        step5.record_sos_failures(self.conn, [{'building_id': 1, 'error': 'details: HTTP 502'}])
        with self.conn.cursor() as cur:
            cur.execute('SELECT sos_last_error,sos_last_enriched FROM buildings WHERE id=1')
            row = cur.fetchone()
            self.assertEqual(row['sos_last_error'], 'details: HTTP 502')
            self.assertEqual(row['sos_last_enriched'], before)

    def test_retry_schedule_recovers_legacy_failures_and_distinguishes_outages(self):
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO permits(id,address,bbl,geocode_failed) VALUES (1,'521 MONTGOMERY ST','3012980066',TRUE), (2,'2 EXAMPLE ST','3012980066',FALSE), (3,'3 EXAMPLE ST','3012980066',FALSE)")
        self.conn.commit()
        with patch.object(job, 'PermitGeocoder') as cls, patch.object(job.time, 'sleep'):
            cls.return_value.lookup.side_effect = [geo.GeocodeResult(40.664236, -73.949363, 'test'),
                                                    geo.GeocodeResult(error='GeoSearch HTTP 503'), geo.GeocodeResult()]
            self.assertEqual(job.geocode_permits(10), 1)
            self.assertEqual(cls.return_value.lookup.call_count, 3)
        with self.conn.cursor() as cur:
            cur.execute('SELECT * FROM permits ORDER BY id')
            rows = cur.fetchall()
            self.assertEqual(rows[0]['latitude'], 40.664236)
            self.assertFalse(rows[0]['geocode_failed'])
            self.assertFalse(rows[1]['geocode_failed'])
            self.assertTrue(rows[2]['geocode_failed'])
        with patch.object(job, 'PermitGeocoder') as cls:
            self.assertEqual(job.geocode_permits(10), 0)
            cls.return_value.lookup.assert_not_called()
        with self.conn.cursor() as cur:
            cur.execute("UPDATE permits SET geocode_last_attempted=NOW()-INTERVAL '2 hours' WHERE id=2")
            cur.execute("UPDATE permits SET geocode_last_attempted=NOW()-INTERVAL '6 days' WHERE id=3")
        self.conn.commit()
        with patch.object(job, 'PermitGeocoder') as cls:
            cls.return_value.lookup.return_value = geo.GeocodeResult(40.664236, -73.949363, 'test')
            self.assertEqual(job.geocode_permits(10), 0)
            cls.return_value.lookup.assert_called_once_with('2 EXAMPLE ST', '3012980066')

    def test_repair_preview_apply_and_concurrent_update_guard(self):
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO permits(id,address,bbl,latitude,longitude) VALUES (12,'80 SPRAGUE AVENUE','5078260220',41.440573,-74.416319)")
        self.conn.commit()
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'run.log'
            log.write_text('[1/1] Permit #12: 80 SPRAGUE AVENUE\n✅ Success: 41.440573, -74.416319\n')
            with patch.object(job, 'PermitGeocoder') as cls, patch.object(job.time, 'sleep'):
                cls.return_value.lookup.return_value = geo.GeocodeResult(40.5034, -74.2372, 'test')
                job.repair_logged_geocodes(log, Path(directory) / 'preview.json')
                with self.conn.cursor() as cur:
                    cur.execute('SELECT latitude FROM permits WHERE id=12')
                    self.assertEqual(cur.fetchone()['latitude'], 41.440573)
                job.repair_logged_geocodes(log, Path(directory) / 'apply.json', apply=True)
                with self.conn.cursor() as cur:
                    cur.execute('SELECT latitude,geocode_failed FROM permits WHERE id=12')
                    self.assertEqual(cur.fetchone(), {'latitude':40.5034, 'geocode_failed':False})
                    cur.execute('UPDATE permits SET latitude=41.440573,longitude=-74.416319 WHERE id=12')
                self.conn.commit()
                def concurrent_change(*_):
                    with self.conn.cursor() as cur:
                        cur.execute('UPDATE permits SET latitude=40.504139,longitude=-74.237448 WHERE id=12')
                    self.conn.commit()
                    return geo.GeocodeResult(40.5034, -74.2372, 'test')
                cls.return_value.lookup.side_effect = concurrent_change
                job.repair_logged_geocodes(log, Path(directory) / 'race.json', apply=True)
                with self.conn.cursor() as cur:
                    cur.execute('SELECT latitude FROM permits WHERE id=12')
                    self.assertEqual(cur.fetchone()['latitude'], 40.504139)


if __name__ == '__main__':
    unittest.main()
