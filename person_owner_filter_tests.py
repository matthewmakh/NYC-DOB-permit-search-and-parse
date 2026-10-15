"""Owner filter regression tests; optional PostgreSQL tests use temporary tables.

Run: python person_owner_filter_tests.py
Set PERSON_OWNER_TEST_DATABASE_URL to run the real SQL/endpoint tests too.
"""
import csv
from io import StringIO
import inspect
import os
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'dashboard_html'))
os.environ.setdefault('DATABASE_URL', 'postgresql://unused:unused@localhost:1/unused')

from person_owner_filter import contains_person_name, has_person_owner
from enrichment_service import is_sos_agent_title


class PersonOwnerTests(unittest.TestCase):
    def test_people_and_organizations(self):
        for name in ('JOHN SMITH', 'SMITH, JOHN', 'JIN PEI XIE', 'XIE, JIN PEI',
                     'MARÍA GARCÍA', 'ANNE-MARIE O’NEILL', 'CHURCH, CHARLOTTE',
                     'DAVID VAN DER BERG', 'JOHN SMITH JR', 'J. SMITH',
                     'JOHN & JANE SMITH', 'JOHN SMITH AND JANE DOE',
                     'EXAMPLE LLC; JANE DOE', 'EXAMPLE LLC\nJANE DOE'):
            with self.subTest(name=name):
                self.assertTrue(contains_person_name(name))
        for name in (None, '', '   ', 'JOHN', 'UNKNOWN OWNER', 'CURRENT OWNER',
                     '123 MAIN LLC', 'JOHN SMITH L.L.C.', 'CITY OF NEW YORK',
                     'FANNIE MAE', 'WELLS FARGO', 'GOLDMAN SACHS',
                     'ACME INC C/O JOHN SMITH', 'ESTATE OF JOHN SMITH',
                     'SMITH & SONS LLC'):
            with self.subTest(name=name):
                self.assertFalse(contains_person_name(name))

    def test_agent_only_vs_agent_and_person(self):
        row = dict(current_owner_name='EXAMPLE LLC', sos_entity_name='EXAMPLE LLC',
                   sos_principal_name='JOHN SMITH', sos_principal_title='Registered Agent')
        self.assertFalse(has_person_owner(row))
        for field in ('sale_buyer_primary', 'current_owner_name', 'owner_name_hpd', 'owner_name_rpad'):
            with self.subTest(field=field):
                self.assertTrue(has_person_owner({**row, field: 'JANE DOE'}))
                # Independently recorded ownership counts even for the same person.
                self.assertTrue(has_person_owner({**row, field: 'JOHN SMITH'}))
        self.assertTrue(has_person_owner({**row, 'sos_principal_title': 'CEO'}))
        self.assertFalse(has_person_owner({**row, 'sos_principal_title': 'CEO',
                                          'sos_entity_name': 'UNRELATED LLC'}))

    def test_non_owner_people_do_not_count(self):
        self.assertFalse(has_person_owner(dict(
            current_owner_name='EXAMPLE LLC', hpd_agent_name='JOHN SMITH',
            hpd_site_manager_name='JANE DOE', sale_seller_primary='ALICE COOPER')))
        self.assertFalse(has_person_owner({}))

    def test_agent_title_variants(self):
        for title in ('Registered Agent', ' registered agent ', 'REGISTERED-AGENT',
                      'Service of Process Agent', 'Service-of-process agent',
                      'Agent for Service of Process', 'Registered Agent / Attorney'):
            with self.subTest(title=title):
                self.assertTrue(is_sos_agent_title(title))
                self.assertFalse(has_person_owner(dict(
                    sos_principal_name='JOHN SMITH', sos_principal_title=title)))
        self.assertFalse(is_sos_agent_title('Managing Member'))


@unittest.skipUnless(os.getenv('PERSON_OWNER_TEST_DATABASE_URL'), 'local PostgreSQL DSN not supplied')
class PostgreSQLFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2
        from psycopg2.extras import RealDictCursor
        import app
        cls.app = app
        cls.conn = psycopg2.connect(os.environ['PERSON_OWNER_TEST_DATABASE_URL'])
        cur = cls.conn.cursor()
        text_fields = '''bbl address borough borough_code current_owner_name owner_name_rpad owner_name_hpd
            building_class sale_buyer_primary sale_seller_primary mortgage_lender_primary
            sos_principal_name sos_principal_title sos_entity_name hpd_agent_name hpd_site_manager_name
            sos_principal_street ecb_respondent_address'''.split()
        number_fields = '''total_units residential_units building_sqft year_built year_altered
            assessed_land_value assessed_total_value sale_price mortgage_amount financing_ratio
            hpd_open_violations hpd_total_complaints acris_deed_count acris_mortgage_count
            acris_total_transactions lot_sqft hpd_total_violations'''.split()
        definitions = ['id integer PRIMARY KEY'] + [f'{f} text' for f in text_fields]
        definitions += [f'{f} numeric' for f in number_fields]
        definitions += ['sale_date date', 'last_updated timestamp', 'is_cash_purchase boolean',
                        'enriched_phones jsonb', 'enriched_emails jsonb']
        cur.execute('CREATE TEMP TABLE buildings (' + ','.join(definitions) + ')')
        cur.execute('CREATE TEMP TABLE permits (bbl text, filing_date date, issue_date date)')
        cur.execute('CREATE TEMP TABLE user_enrichments (user_id integer, building_id integer)')
        fixtures = [
            (1, 'EXAMPLE LLC', None, 'JOHN SMITH', 'Registered Agent', 'EXAMPLE LLC'),
            (2, 'EXAMPLE LLC', 'JANE DOE', 'JOHN SMITH', 'Registered Agent', 'EXAMPLE LLC'),
            (3, 'EXAMPLE LLC', None, 'JOHN SMITH', 'CEO', 'EXAMPLE LLC'),
            (4, 'CITY OF NEW YORK', None, None, None, None),
            (5, 'JOHN SMITH', None, 'JOHN SMITH', 'Registered Agent', 'EXAMPLE LLC'),
            (6, 'EXAMPLE LLC; JANE DOE', None, None, None, None),
            (7, 'EXAMPLE LLC', None, 'JOHN SMITH', 'CEO', 'UNRELATED LLC'),
            (8, 'ACME INC C/O JOHN SMITH', None, None, None, None),
        ]
        for row in fixtures:
            cur.execute('''INSERT INTO buildings (id, current_owner_name, owner_name_hpd,
                sos_principal_name, sos_principal_title, sos_entity_name, bbl, address, sale_date)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'2026-01-01')''',
                row + (str(1000000000 + row[0]), f'{row[0]} TEST STREET'))
        cur.execute("INSERT INTO permits VALUES ('1000000002', CURRENT_DATE, CURRENT_DATE)")
        cur.close()

        @contextmanager
        def database():
            cursor = cls.conn.cursor(cursor_factory=RealDictCursor)
            try:
                yield cursor
            finally:
                cursor.close()
        cls.patches = [patch.object(app, 'DatabaseConnection', database),
                       patch.object(app, '_buildings_columns', return_value=set()),
                       patch.object(app, '_signal_select_sql', return_value='')]
        for p in cls.patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        cls.app.cache.clear()
        for p in reversed(cls.patches):
            p.stop()
        cls.conn.rollback()
        cls.conn.close()

    def setUp(self):
        self.app.cache.clear()

    def call(self, endpoint, query):
        with self.app.app.test_request_context('/api/properties?' + query):
            self.app.g.user = {'id': 1, 'is_admin': True}
            result = inspect.unwrap(endpoint)()
            if isinstance(result, tuple):
                self.fail(str(result))
            return result

    def test_pagination_count_and_bulk_match(self):
        query = 'has_person_owner=true&sort_by=address&sort_order=asc&per_page=2'
        first = self.call(self.app.api_properties, query).get_json()
        second = self.call(self.app.api_properties, query + '&page=2').get_json()
        self.assertEqual(first['pagination']['total_count'], 4)
        self.assertEqual(first['pagination']['total_pages'], 2)
        self.assertEqual([p['id'] for p in first['properties'] + second['properties']], [2,3,5,6])
        from werkzeug.datastructures import MultiDict
        self.assertEqual(self.app._resolve_filter_building_ids(
            MultiDict({'has_person_owner': 'true'})), [2,3,5,6])
        self.assertEqual(self.app._resolve_filter_building_ids(
            {'has_person_owner': True}, limit=2), [2,3])
        unfiltered = self.call(self.app.api_properties, '').get_json()
        self.assertEqual(unfiltered['pagination']['total_count'], 8)

    def test_export_and_estimate_match(self):
        response = self.call(self.app.api_properties_export, 'has_person_owner=true&fields=bbl')
        rows = list(csv.DictReader(StringIO(response.get_data(as_text=True))))
        self.assertEqual({row['BBL'] for row in rows},
                         {'1000000002','1000000003','1000000005','1000000006'})
        with patch('enrichment_service.get_enrichable_permit_contacts', return_value=[]):
            estimate = self.call(self.app.api_properties_export_enrichment_estimate,
                                 'has_person_owner=true').get_json()
        self.assertEqual(estimate['total_properties'], 4)
        # Exercise the paid-export route with all external enrichment/payment
        # functions disabled; only the selected property set is under test.
        with patch('enrichment_service.get_enrichable_permit_contacts', return_value=[]), \
                patch('stripe_service.charge_enrichment_fee', side_effect=AssertionError('unexpected payment')):
            response = self.call(self.app.api_properties_export_with_enrichment,
                                 'has_person_owner=true&fields=bbl')
        paid_rows = list(csv.DictReader(StringIO(response.get_data(as_text=True))))
        self.assertEqual({row['BBL'] for row in paid_rows}, {row['BBL'] for row in rows})

    def test_combination_and_empty_results(self):
        query = 'has_person_owner=true&with_permits=true'
        data = self.call(self.app.api_properties, query).get_json()
        self.assertEqual([p['id'] for p in data['properties']], [2])
        self.assertEqual(self.app._resolve_filter_building_ids(
            {'has_person_owner': True, 'with_permits': True}), [2])
        data = self.call(self.app.api_properties,
                         'has_person_owner=true&owner_kind=llc').get_json()
        self.assertEqual({p['id'] for p in data['properties']}, {2,3,6})
        data = self.call(self.app.api_properties,
                         'has_person_owner=true&search=DOES-NOT-EXIST').get_json()
        self.assertEqual(data['pagination']['total_count'], 0)
        self.assertEqual(data['properties'], [])

    def test_pagination_reuses_name_scan(self):
        with patch.object(self.app, '_person_owner_building_ids',
                          wraps=self.app._person_owner_building_ids) as resolver:
            self.call(self.app.api_properties, 'has_person_owner=true&per_page=1')
            # The cache must ignore the caller's different cursor object.
            with patch('person_owner_filter.has_person_owner', side_effect=AssertionError('cache miss')):
                self.call(self.app.api_properties, 'has_person_owner=true&per_page=1&page=2')
            self.assertEqual(resolver.call_count, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
