"""Real SQL regressions for permit controls and contractor pagination on localhost."""
import ast
import os
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, request
import prospecting_tests as fixtures


@unittest.skipUnless(os.getenv('PROSPECTING_TEST_DATABASE_URL'), 'Disposable PostgreSQL not configured')
class PermitWorkflowTests(unittest.TestCase):
    connect = fixtures.DatabaseTests.connect
    create = fixtures.DatabaseTests.create
    count = fixtures.DatabaseTests.count

    def setUp(self):
        if urlsplit(os.environ['PROSPECTING_TEST_DATABASE_URL']).hostname not in ('localhost', '127.0.0.1'):
            self.skipTest('This suite only uses a disposable localhost database')
        fixtures.DatabaseTests.setUp(self)
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('''CREATE TABLE permits (
                    id INTEGER PRIMARY KEY, permit_no TEXT, job_type TEXT, address TEXT,
                    borough TEXT, issue_date DATE, filing_date DATE, bbl TEXT, bin TEXT, applicant TEXT,
                    permittee_business_name TEXT, owner_business_name TEXT, permittee_phone TEXT,
                    owner_phone TEXT, latitude NUMERIC, longitude NUMERIC, work_type TEXT, permit_type TEXT)''')
                cursor.execute('CREATE TABLE buildings(bbl TEXT PRIMARY KEY, assessed_total_value NUMERIC, purchase_price NUMERIC)')
                cursor.execute('''CREATE TABLE permit_participants(permit_id INTEGER, business_name TEXT,
                    person_name TEXT, license_number TEXT, license_type TEXT, role TEXT,
                    role_confidence NUMERIC, contractor_confidence NUMERIC)''')
                today = date.today()
                for index in range(1, 25):
                    cursor.execute('''INSERT INTO permits(id, permit_no, job_type, address, borough, issue_date,
                        filing_date, bbl, applicant, permittee_phone, work_type)
                        VALUES(%s,%s,%s,%s,'BROOKLYN',%s,%s,%s,%s,%s,'GC')''',
                        (index, str(index), 'NB' if index % 2 else 'A2', f'{index:02} Test Street',
                         today - timedelta(days=index), today - timedelta(days=index), '3000010001',
                         f'Applicant {25-index:02}', '917-555-0100' if index % 2 else None))
                    cursor.execute("INSERT INTO permit_participants VALUES(%s,%s,NULL,NULL,'GC','permittee',1,1)",
                                   (index, 'Alpha Construction' if index % 2 else 'Beta Construction'))
                cursor.execute("INSERT INTO permits(id,permit_no,issue_date,bbl) VALUES(100,'Future',%s,'3000010001'),(101,'Undated',NULL,'3000010001')", (today + timedelta(days=10000),))
                cursor.execute("INSERT INTO permit_participants VALUES(100,'Future Construction',NULL,NULL,'GC','permittee',1,1)")
                cursor.execute("INSERT INTO buildings VALUES('3000010001',100000,500000)")

        @contextmanager
        def database_cursor():
            connection = self.connect()
            try:
                with connection.cursor() as cursor:
                    yield cursor
            finally:
                connection.close()

        # Execute the actual route/helper definitions without importing app.py,
        # loading its .env, or starting unrelated production initialization.
        names = {'calculate_lead_score', '_construction_time_filter', '_construction_lead_score_sql',
                 'get_construction_permits', 'get_construction_stats', '_recent_permit_date_sql', '_order_by_sql', '_multi_param',
                 'api_contractors_search', '_attach_work_mix'}
        tree = ast.parse((Path(__file__).resolve().parent / 'app.py').read_text())
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        for node in nodes:
            node.decorator_list = []
        self.env = {'datetime': datetime, 'request': request, 'jsonify': jsonify,
                    'DatabaseConnection': database_cursor, 'WORK_TYPE_LABELS': {},
                    'PERMIT_TYPE_LABELS': {}, 'JOB_TYPE_LABELS': {},
                    '_permit_predicates': lambda *args, **kwargs: ([], []),
                    '_parse_boroughs_param': lambda value: [],
                    '_append_building_only_filters': lambda *args: None}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'actual-permit-routes', 'exec'), self.env)
        app = Flask(__name__)
        app.config['TESTING'] = True
        app.add_url_rule('/permits', view_func=self.env['get_construction_permits'])
        app.add_url_rule('/contractors', view_func=self.env['api_contractors_search'])
        app.add_url_rule('/stats', view_func=self.env['get_construction_stats'])
        self.client = app.test_client()

    def tearDown(self):
        fixtures.DatabaseTests.tearDown(self)

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue(response.json['success'])
        return response.json

    def test_sort_filter_and_pagination_apply_to_the_full_result_set(self):
        newest = self.get('/permits?days=90&sort=date&limit=1')
        oldest = self.get('/permits?days=90&sort=date_asc&limit=1')
        self.assertEqual(1, newest['permits'][0]['id'])
        self.assertEqual(24, oldest['permits'][0]['id'])
        self.assertEqual(24, newest['total_count'])
        self.assertEqual(1, self.get('/permits?days=90&sort=address&limit=1')['permits'][0]['id'])
        self.assertEqual(24, self.get('/permits?days=90&sort=applicant&limit=1')['permits'][0]['id'])
        self.assertEqual('A2', self.get('/permits?days=90&sort=type&limit=1')['permits'][0]['job_type'])
        scores = self.get('/permits?days=90&sort=score&limit=24')['permits']
        self.assertEqual(sorted((row['lead_score'] for row in scores), reverse=True), [row['lead_score'] for row in scores])
        hot = self.get('/permits?days=90&min_score=70&limit=1')
        self.assertEqual(12, hot['total_count'])
        self.assertEqual(1, len(hot['permits']))
        self.assertGreaterEqual(hot['permits'][0]['lead_score'], 70)
        self.assertEqual(12, self.get('/permits?days=90&has_contact=true')['total_count'])
        self.assertEqual(25, self.get('/permits?days=all')['total_count'])
        self.assertEqual(1, len(self.get('/permits?days=90&limit=0&offset=-1')['permits']))

    def test_sql_score_matches_display_score_and_future_dates_are_not_recent(self):
        with self.connect() as connection:
            with connection.cursor() as cursor:
                score_sql = self.env['_construction_lead_score_sql']('p')
                cursor.execute(f'''SELECT p.*, {score_sql} AS sql_score,
                    CASE WHEN NULLIF(permittee_phone,'') IS NOT NULL THEN 1 ELSE 0 END AS contact_count
                    FROM permits p''')
                for row in cursor.fetchall():
                    self.assertEqual(row['sql_score'], self.env['calculate_lead_score'](row))
                recent = self.env['_recent_permit_date_sql']('p')
                cursor.execute(f'SELECT id FROM permits p WHERE {recent}', ['90', '90'])
                self.assertNotIn(100, [row['id'] for row in cursor.fetchall()])

    def test_timeline_aggregates_actual_dates_and_handles_empty_windows(self):
        recent = self.get('/stats?days=90')['stats']
        self.assertEqual('day', recent['timeline_unit'])
        self.assertEqual(24, len(recent['timeline']))
        self.assertEqual(recent['total_permits'], sum(point['count'] for point in recent['timeline']))
        self.assertEqual(sorted(point['date'] for point in recent['timeline']), [point['date'] for point in recent['timeline']])
        self.assertTrue(all(date.fromisoformat(point['date']) <= date.today() for point in recent['timeline']))
        historical = self.get('/stats?days=all')['stats']
        self.assertEqual('month', historical['timeline_unit'])
        self.assertEqual(24, sum(point['count'] for point in historical['timeline']))
        self.assertEqual([], self.get('/stats?days=90&permit_activity_mode=inactive')['stats']['timeline'])

    def test_contractor_total_survives_pagination_filters_and_empty_pages(self):
        first = self.get('/contractors?per_page=1')
        second = self.get('/contractors?per_page=1&page=2')
        self.assertEqual(3, first['pagination']['total'])
        self.assertNotEqual(first['contractors'][0]['contractor_name'], second['contractors'][0]['contractor_name'])
        self.assertNotIn('directory_total', first['contractors'][0])
        self.assertEqual(3, self.get('/contractors?per_page=1&page=99')['pagination']['total'])
        filtered = self.get('/contractors?min_jobs=10&per_page=1')
        self.assertEqual(2, filtered['pagination']['total'])
        self.assertEqual(2, self.get('/contractors?min_jobs=10&per_page=1&page=99')['pagination']['total'])
        self.assertEqual(0, self.get('/contractors?search=nonexistent')['pagination']['total'])
        future = self.get('/contractors?search=Future')['contractors'][0]
        self.assertEqual(0, future['active_jobs'])
        self.assertIsNone(future['most_recent_job'])


if __name__ == '__main__':
    unittest.main()
