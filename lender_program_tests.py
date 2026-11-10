#!/usr/bin/env python3
"""Tests for investor loan program tagging (DSCR / RTL).

Offline tests need no database or network. The database suite runs against a
disposable PostgreSQL schema when ENRICHMENT_TEST_DATABASE_URL is set::

    python3 lender_program_tests.py
    ENRICHMENT_TEST_DATABASE_URL=postgresql://... python3 lender_program_tests.py
"""
import os
import sys
import unittest
import uuid
from datetime import date
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'dashboard_html'))
sys.path.insert(0, HERE)
os.environ.setdefault('DATABASE_URL', 'postgresql://unused:unused@localhost:1/unused')

import lender_programs as L  # noqa: E402


def lender(name, a1='', a2='', zip_code=''):
    return {'name': name, 'address_1': a1, 'address_2': a2, 'zip_code': zip_code}


class LenderListTests(unittest.TestCase):
    def test_list_loads_and_is_versioned(self):
        lenders = L.load_lenders()
        self.assertGreaterEqual(len(lenders), 100)
        self.assertTrue(all(l.program in L.PROGRAMS for l in lenders))
        self.assertTrue(all(l.confidence in ('high', 'medium') for l in lenders))
        self.assertRegex(L.list_version(), r'^\d+-[0-9a-f]{10}$')

    def test_every_lender_has_product_evidence(self):
        import csv
        with open(L.LIST_PATH, newline='') as handle:
            rows = list(csv.DictReader(handle))
        missing = [r['lender'] for r in rows if not (r['evidence_1_url'] and r['evidence_1_quote'])]
        self.assertEqual(missing, [])


class MatchTests(unittest.TestCase):
    def name(self, *args):
        found, _ = L.match_lender(*args)
        return found.name if found else None

    def test_word_boundaries(self):
        self.assertIn('Visio', self.name('VISIO FINANCIAL SERVICES INC.'))
        self.assertIsNone(self.name('VISION REAL ESTATE GROUP, LLC'))
        self.assertIsNone(self.name('VISIONS FEDERAL CREDIT UNION'))
        self.assertIsNone(self.name('PARKVIEW FINANCIAL REIT, LP'))
        self.assertIsNone(self.name('GENESIS CAPITAL ONE, LLC ISAOA/ATIMA'))

    def test_recorded_entity_names_not_brands(self):
        self.assertIn('Acra', self.name('CITADEL SERVICING CORPORATION DBA ACRA LENDING'))
        self.assertIn('LoanStream', self.name('OCMBC, INC.'))
        self.assertIn('Visio', self.name('INVESTOR MORTGAGE FINANCE, LLC, ISAOA/ATIMA'))
        self.assertIn('Emporium', self.name('EMPOROIUM TPO LLC'))
        self.assertIn('Velocity', self.name('VELOCITY COMMERICAL CAPITAL, LLC'))

    def test_mers_nominee_rows(self):
        self.assertIn('A&D', self.name('MERS, AS NOMINEE FOR A & D MORTGAGE LLC'))
        self.assertIn('FM Home', self.name('MERS, INC., AS NOMINEEE FOR FM HOME LOANS, LLC'))
        self.assertIn('Logan', self.name('MERS, INC., AS NOMINNE FOR LOGAN FINANCE CORP.'))
        self.assertIn('Cake', self.name('MERS FOR CAKE MORTGAGE CORP.'))
        self.assertIn('Nationwide', self.name('NATIONWIDE MORTGAGE BANKERS INC (MERS)'))
        self.assertIsNone(self.name('MORTGAGE ELECTRONIC REGISTRATION SYSTEMS, INC.'))
        self.assertIsNone(self.name('MERS INC AS NOMINEE'))

    def test_roc_address_rule_needs_address_and_zip(self):
        roc = 'Roc360'
        self.assertIn(roc, self.name('LOAN FUNDER LLC, SERIES 63164'))
        self.assertIn(roc, self.name('BRANCH LENDING LLC', '645 MADISON AVENUE', '19TH FLOOR', '10022'))
        self.assertIn(roc, self.name('MERS', '645 MADISON AVENUE 19TH FLOOR', '', '10022'))
        self.assertIsNone(self.name('BRANCH LENDING LLC', '98 CUTTER MILL ROAD', '', '11021'))
        self.assertIsNone(self.name('BRANCH LENDING LLC', '645 MADISON AVENUE', '', '10065'))
        _, basis = L.match_lender('BRANCH LENDING LLC', '645 MADISON AVENUE', '', '10022')
        self.assertEqual(basis, 'lender_address')


class ClassifyTests(unittest.TestCase):
    def test_programs(self):
        self.assertEqual(L.classify_mortgage([lender('MANHATTAN BRIDGE CAPITAL INC')], ['DOE, JANE'])['program'], L.RTL)
        self.assertEqual(L.classify_mortgage([lender('VELOCITY COMMERCIAL CAPITAL, LLC')], ['X LLC'])['program'],
                         L.DSCR_OR_RTL)
        self.assertEqual(L.classify_mortgage([lender('EMIGRANT FUNDING CORPORATION')], ['X LLC'])['program'], L.DSCR)
        self.assertEqual(L.classify_mortgage([lender('MAXIM CREDIT GROUP LLC')], ['X LLC'])['program'],
                         L.COMMERCIAL_BRIDGE)

    def test_consumer_lenders_need_an_entity_borrower(self):
        uwm = [lender('UNITED WHOLESALE MORTGAGE LLC')]
        self.assertIsNone(L.classify_mortgage(uwm, ['SMITH, JOHN']))
        tag = L.classify_mortgage(uwm, ['SMITH, JOHN', '123 MAIN STREET LLC'])
        self.assertEqual(tag['program'], L.DSCR)
        self.assertEqual(tag['basis'], 'lender_name+entity_borrower')
        # Figure Lending also makes consumer HELOCs.
        self.assertIsNone(L.classify_mortgage([lender('FIGURE LENDING LLC')], ['DOE, JANE']))

    def test_unlisted_and_mers_only(self):
        self.assertIsNone(L.classify_mortgage([lender('JPMORGAN CHASE BANK, N.A.')], ['X LLC']))
        self.assertIsNone(L.classify_mortgage([lender('MORTGAGE ELECTRONIC REGISTRATION SYSTEMS INC')], ['X LLC']))
        self.assertIsNone(L.classify_mortgage([], ['X LLC']))

    def test_name_match_beats_address_match(self):
        tag = L.classify_mortgage([
            lender('SOME PRIVATE LENDER LLC', '645 MADISON AVENUE', '19TH FLOOR', '10022'),
            lender('MANHATTAN BRIDGE CAPITAL INC', '60 CUTTER MILL ROAD', '', '11021'),
        ], ['X LLC'])
        self.assertEqual(tag['lender'], 'Manhattan Bridge Capital')

    def test_summary_picks_latest_and_per_program_dates(self):
        tagged = [
            {'program': L.RTL, 'lender': 'A', 'confidence': 'high', 'amount': 500000, 'recorded_date': date(2025, 3, 1)},
            {'program': L.DSCR, 'lender': 'B', 'confidence': 'high', 'amount': 700000, 'recorded_date': date(2026, 1, 5)},
            {'program': L.RTL, 'lender': 'C', 'confidence': 'medium', 'amount': None, 'recorded_date': date(2024, 6, 1)},
        ]
        s = L.summarize(tagged)
        self.assertEqual((s['investor_loan_program'], s['investor_loan_lender']), (L.DSCR, 'B'))
        self.assertEqual(s['investor_loan_count'], 3)
        self.assertEqual(s['rtl_loan_last_date'], date(2025, 3, 1))
        self.assertEqual(s['dscr_loan_last_date'], date(2026, 1, 5))
        self.assertEqual(L.summarize([])['investor_loan_count'], 0)


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as A
        cls.A = A

    def clause(self, pairs, columns=frozenset({'loan_program'})):
        from werkzeug.datastructures import MultiDict
        where, params = [], []
        with mock.patch.object(self.A, '_table_columns', return_value=set(columns)):
            self.A._append_building_only_filters(MultiDict(pairs), where, params)
        return where, params

    def test_filter_clause(self):
        where, params = self.clause([('loan_program', 'rtl'), ('loan_program', 'DSCR'), ('loan_program', 'bogus')])
        self.assertEqual(len(where), 1)
        self.assertIn('EXISTS (SELECT 1 FROM acris_transactions lp WHERE lp.building_id = b.id', where[0])
        self.assertEqual(params, [['DSCR', 'RTL']])

    def test_filter_window_is_clamped(self):
        where, params = self.clause([('loan_program', 'dscr_or_rtl'), ('loan_program_days', '99999')])
        self.assertIn("CURRENT_DATE - %s * INTERVAL '1 day'", where[0])
        self.assertEqual(params, [['DSCR_OR_RTL'], 3650])
        _, params = self.clause([('loan_program', 'rtl'), ('loan_program_days', 'abc')])
        self.assertEqual(params, [['RTL']])

    def test_no_filter_without_program_and_false_before_migration(self):
        self.assertEqual(self.clause([('loan_program_days', '365')]), ([], []))
        self.assertEqual(self.clause([('loan_program', 'rtl')], columns=set())[0], ['FALSE'])

    def test_plays_require_the_new_columns(self):
        from plays import available_plays, get_play
        ids = {p['id'] for p in available_plays({'is_free_and_clear', 'acris_total_transactions'})}
        self.assertNotIn('investor-rehab-loans', ids)
        cols = {'rtl_loan_last_date', 'dscr_loan_last_date', 'investor_loan_date'}
        self.assertIsNotNone(get_play('investor-rehab-loans', cols))
        self.assertIsNotNone(get_play('dscr-landlords', cols))

    def test_export_fields(self):
        row = {'investor_loan_program': 'RTL', 'investor_loan_lender': 'Manhattan Bridge Capital',
               'investor_loan_date': '2026-03-04', 'investor_loan_amount': '450000.00'}
        out = {k: f(row) for k, (_, f) in self.A.INVESTOR_LOAN_EXPORT_FIELDS.items()}
        self.assertEqual(out['investor_loan_program'], 'Fix & flip / bridge loan')
        self.assertEqual(out['investor_loan_lender'], 'Manhattan Bridge Capital')


class PipelineWiringTests(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(HERE, name)) as handle:
            return handle.read()

    def test_pipeline_runs_migration_and_step_3b_after_acris(self):
        text = self.read('run_enrichment_pipeline.py')
        self.assertLess(text.index("'migrate_add_lender_programs.py'"), text.index("'step1_link_permits_to_buildings.py'"))
        self.assertLess(text.index("'step3_enrich_from_acris.py'"), text.index("'step3b_tag_lender_programs.py'"))
        self.assertLess(text.index("'step3b_tag_lender_programs.py'"), text.index("'step4_enrich_from_tax_liens.py'"))
        critical = text[text.index('critical_steps = ['):]
        self.assertIn("'step3b'", critical)
        self.assertIn("'lender_programs_migration'", critical)

    def test_scraper_cron_runs_tagging_without_failing_the_scrape(self):
        text = self.read('run_permit_intelligence_cron.py')
        call = text[text.index('step3b_tag_lender_programs.py'):]
        self.assertIn('check=False', call[:200])

    def test_acris_refresh_tags_the_building(self):
        text = self.read('dashboard_html/step3_enrich_from_acris.py')
        self.assertIn('lender_programs.tag_building(cur, building_id)', text)


@unittest.skipUnless(os.getenv('ENRICHMENT_TEST_DATABASE_URL'), 'disposable PostgreSQL DSN not supplied')
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        import psycopg2
        import psycopg2.extras
        self.psycopg2 = psycopg2
        self.schema = 'lender_' + uuid.uuid4().hex[:12]
        self.admin = psycopg2.connect(os.environ['ENRICHMENT_TEST_DATABASE_URL'])
        self.admin.autocommit = True
        with self.admin.cursor() as cur:
            cur.execute(f'CREATE SCHEMA {self.schema}')
        self.conn = self.connect()
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE buildings (id SERIAL PRIMARY KEY, bbl VARCHAR(10), acris_last_enriched TIMESTAMP);
                CREATE TABLE acris_transactions (
                    id SERIAL PRIMARY KEY, building_id INT REFERENCES buildings(id) ON DELETE CASCADE,
                    document_id VARCHAR(50) NOT NULL, doc_type VARCHAR(20), doc_amount NUMERIC, doc_date DATE,
                    recorded_date DATE, percent_transferred INT, crfn VARCHAR(50),
                    is_primary_deed BOOLEAN DEFAULT FALSE, is_primary_mortgage BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW(), bbl VARCHAR(10), remarks TEXT,
                    CONSTRAINT unique_doc_per_building UNIQUE (building_id, document_id));
                CREATE TABLE acris_parties (
                    id SERIAL PRIMARY KEY, transaction_id INT REFERENCES acris_transactions(id) ON DELETE CASCADE,
                    party_type VARCHAR(20), party_name VARCHAR(255), address_1 VARCHAR(255), address_2 VARCHAR(255),
                    city VARCHAR(100), state VARCHAR(2), zip_code VARCHAR(10), country VARCHAR(2),
                    is_lead BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT NOW(), building_id INT);
                INSERT INTO buildings (id, bbl, acris_last_enriched) VALUES
                    (1, '3012340001', NOW()), (2, '3012340002', NOW()), (3, '3012340003', NULL);
            """)
        self.conn.commit()
        L._SCHEMA_READY.clear()

    def tearDown(self):
        self.conn.close()
        with self.admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA {self.schema} CASCADE')
        self.admin.close()

    def connect(self, real_dict=False):
        import psycopg2.extras
        return self.psycopg2.connect(
            os.environ['ENRICHMENT_TEST_DATABASE_URL'], options=f'-c search_path={self.schema}',
            cursor_factory=psycopg2.extras.RealDictCursor if real_dict else None)

    def migrate(self):
        import migrate_add_lender_programs as M
        with self.conn.cursor() as cur:
            M.apply(cur)
        self.conn.commit()

    def add_mortgage(self, building_id, doc_id, lender_name, borrower, recorded, amount=500000,
                     doc_type='MTGE', lender_address=('', '', '')):
        with self.conn.cursor() as cur:
            cur.execute("""INSERT INTO acris_transactions (building_id, document_id, doc_type, doc_amount,
                           recorded_date) VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                        (building_id, doc_id, doc_type, amount, recorded))
            txn = cur.fetchone()[0]
            cur.execute("""INSERT INTO acris_parties (transaction_id, party_type, party_name, address_1,
                           address_2, zip_code, building_id) VALUES (%s, 'lender', %s, %s, %s, %s, %s),
                           (%s, 'borrower', %s, '', '', '', %s)""",
                        (txn, lender_name, *lender_address, building_id, txn, borrower, building_id))
        self.conn.commit()
        return txn

    def fetch(self, sql, params=()):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def test_migration_is_idempotent_and_lock_free_on_rerun(self):
        self.migrate()
        import migrate_add_lender_programs as M
        class Recorder:
            def __init__(self, cur):
                self.cur, self.statements = cur, []

            def execute(self, sql, params=None):
                self.statements.append(sql)
                return self.cur.execute(sql, params)

            def fetchall(self):
                return self.cur.fetchall()

        with self.conn.cursor() as cur:
            recorder = Recorder(cur)
            M.apply(recorder)
        self.assertFalse(any('ALTER TABLE' in s or 'CREATE INDEX' in s for s in recorder.statements),
                         recorder.statements)
        self.conn.commit()

    def test_batch_tags_summarizes_and_is_incremental(self):
        self.migrate()
        self.add_mortgage(1, 'D1', 'MANHATTAN BRIDGE CAPITAL INC', 'FLIP 1 LLC', date(2026, 2, 1), 450000)
        self.add_mortgage(1, 'D2', 'UNITED WHOLESALE MORTGAGE LLC', 'SMITH, JOHN', date(2026, 5, 1))
        self.add_mortgage(2, 'D3', 'MERS AS NOMINEE FOR LOAN FUNDER LLC, SERIES 99', 'RENTAL 2 LLC', date(2025, 9, 9),
                          lender_address=('645 MADISON AVENUE', '19TH FLOOR', '10022'))
        self.add_mortgage(2, 'D4', 'JPMORGAN CHASE BANK, N.A.', 'RENTAL 2 LLC', date(2024, 1, 1))
        self.add_mortgage(2, 'D5', 'VELOCITY COMMERCIAL CAPITAL LLC', 'RENTAL 2 LLC', date(2024, 1, 1),
                          doc_type='ASST')  # an assignment is not an origination
        stats = L.run_batch(self.conn, log=lambda *_: None)
        self.assertEqual(stats['buildings'], 2)          # building 3 has no ACRIS data yet
        self.assertEqual(stats['mortgages_tagged'], 2)
        tags = dict(self.fetch("SELECT document_id, loan_program FROM acris_transactions"))
        self.assertEqual(tags, {'D1': 'RTL', 'D2': None, 'D3': 'DSCR_OR_RTL', 'D4': None, 'D5': None})
        b1 = self.fetch("""SELECT investor_loan_program, investor_loan_lender, investor_loan_amount::int,
                                  investor_loan_date, rtl_loan_last_date, investor_loan_count,
                                  lender_programs_version FROM buildings WHERE id = 1""")[0]
        self.assertEqual(b1[:6], ('RTL', 'Manhattan Bridge Capital', 450000, date(2026, 2, 1), date(2026, 2, 1), 1))
        self.assertEqual(b1[6], L.list_version())
        self.assertEqual(L.run_batch(self.conn, log=lambda *_: None)['buildings'], 0)

        # A refreshed ACRIS snapshot or a new list version brings buildings back.
        with self.conn.cursor() as cur:
            cur.execute("UPDATE buildings SET acris_last_enriched = NOW() + INTERVAL '1 minute' WHERE id = 2")
        self.conn.commit()
        self.assertEqual(L.run_batch(self.conn, log=lambda *_: None)['buildings'], 1)
        with mock.patch.object(L, 'list_version', return_value='99-newlist'):
            self.assertEqual(L.run_batch(self.conn, log=lambda *_: None)['buildings'], 2)

    def test_stale_tags_are_cleared(self):
        self.migrate()
        txn = self.add_mortgage(1, 'D1', 'MANHATTAN BRIDGE CAPITAL INC', 'FLIP 1 LLC', date(2026, 2, 1))
        L.run_batch(self.conn, log=lambda *_: None)
        with self.conn.cursor() as cur:
            cur.execute("UPDATE acris_parties SET party_name = 'SOME BANK' WHERE transaction_id = %s AND "
                        "party_type = 'lender'", (txn,))
            L.tag_buildings(cur, [1])
        self.conn.commit()
        self.assertEqual(self.fetch("SELECT loan_program FROM acris_transactions")[0][0], None)
        self.assertEqual(self.fetch("SELECT investor_loan_program, investor_loan_count FROM buildings WHERE id = 1")[0],
                         (None, 0))

    def test_hook_is_a_noop_before_migration_and_never_breaks_the_transaction(self):
        self.add_mortgage(1, 'D1', 'MANHATTAN BRIDGE CAPITAL INC', 'FLIP 1 LLC', date(2026, 2, 1))
        with self.conn.cursor() as cur:
            self.assertIsNone(L.tag_building(cur, 1))
            cur.execute('SELECT 1')                       # transaction still usable
        self.conn.commit()
        self.migrate()
        with self.conn.cursor() as cur:
            with mock.patch.object(L, 'tag_buildings', side_effect=RuntimeError('boom')):
                self.assertIsNone(L.tag_building(cur, 1))
            cur.execute('SELECT count(*) FROM acris_transactions')
            self.assertEqual(cur.fetchone()[0], 1)
            self.assertEqual(L.tag_building(cur, 1), 1)
        self.conn.commit()

    def test_step3_save_path_feeds_tagging(self):
        """Rows written by step 3's own save function carry the roles tagging reads."""
        self.migrate()
        # Load the canonical module; the repo-root file of the same name is a
        # launcher that runs the whole step on import.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'acris_step3_under_test', os.path.join(HERE, 'dashboard_html', 'step3_enrich_from_acris.py'))
        step3 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(step3)
        txn = {'document_id': 'S1', 'doc_type': 'MTGE', 'doc_amount': 650000.0, 'doc_date': date(2026, 4, 1),
               'recorded_date': date(2026, 4, 3), 'crfn': '2026000000001', 'percent_transferred': None,
               'remarks': None, 'parties': [
                   {'role': 'borrower', 'name': '77 BEDFORD LLC', 'address1': '1 MAIN ST', 'address2': '',
                    'city': 'BROOKLYN', 'state': 'NY', 'zip': '11211', 'country': 'US'},
                   {'role': 'lender', 'name': 'MERS, AS NOMINEE FOR A & D MORTGAGE LLC',
                    'address1': '899 W CYPRESS CREEK ROAD', 'address2': '', 'city': 'FT LAUDERDALE',
                    'state': 'FL', 'zip': '33309', 'country': 'US'}],
               'buyers': [], 'sellers': [], 'lenders': [], 'borrowers': []}
        conn = self.connect(real_dict=True)
        try:
            with conn.cursor() as cur:
                step3.save_transactions_and_parties(cur, 1, '3012340001', [txn])
                self.assertEqual(L.tag_building(cur, 1), 1)
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.fetch("SELECT loan_program, loan_program_lender FROM acris_transactions")[0],
                         ('DSCR', 'A&D Mortgage'))

    def test_dashboard_filter_sql_runs(self):
        self.migrate()
        self.add_mortgage(1, 'D1', 'MANHATTAN BRIDGE CAPITAL INC', 'FLIP 1 LLC', date.today())
        self.add_mortgage(2, 'D3', 'EMIGRANT FUNDING CORPORATION', 'RENTAL 2 LLC', date(2020, 1, 1))
        L.run_batch(self.conn, log=lambda *_: None)
        import app as A
        from werkzeug.datastructures import MultiDict

        def ids(pairs):
            where, params = [], []
            with mock.patch.object(A, '_table_columns', return_value={'loan_program'}):
                A._append_building_only_filters(MultiDict(pairs), where, params)
            return [r[0] for r in self.fetch(f"SELECT b.id FROM buildings b WHERE {' AND '.join(where)} ORDER BY 1",
                                             params)]
        self.assertEqual(ids([('loan_program', 'rtl')]), [1])
        self.assertEqual(ids([('loan_program', 'rtl'), ('loan_program', 'dscr')]), [1, 2])
        self.assertEqual(ids([('loan_program', 'dscr'), ('loan_program_days', '365')]), [])
        from plays import get_play
        play = get_play('investor-rehab-loans', {'rtl_loan_last_date', 'investor_loan_date'})
        self.assertEqual([r[0] for r in self.fetch(f"SELECT b.id FROM buildings b WHERE {play['where']}")], [1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
