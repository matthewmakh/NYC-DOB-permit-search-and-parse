"""Real PostgreSQL equivalence, invalidation, and publication regressions."""
import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from unittest.mock import patch

import contractor_directory as directory
from permit_workflow_tests import PermitWorkflowTests


class ContractorDirectoryTests(PermitWorkflowTests):
    def setUp(self):
        super().setUp()
        with self.connect() as connection:
            with connection.cursor() as cur:
                cur.execute('ALTER TABLE permits ADD COLUMN permittee_license_type TEXT')
                cur.execute('''ALTER TABLE buildings ADD COLUMN total_units INTEGER,
                    ADD COLUMN building_class TEXT, ADD COLUMN hpd_open_violations INTEGER''')
                cur.execute("UPDATE permits SET permittee_license_type='GC'")
                cur.execute("UPDATE buildings SET total_units=10,building_class='C1',hpd_open_violations=2")
                # The same person can have several roles on the same permit:
                # job counts are distinct; property-value sums retain old semantics.
                cur.execute("INSERT INTO permit_participants VALUES(1,'Alpha Construction',NULL,NULL,'GC','owner',1,.05)")
                directory.ensure_schema(cur)
        self.refresh()
        source = ast.parse((Path(__file__).parent / 'app.py').read_text())
        helpers = {'_permit_predicates', '_permit_activity_settings', '_recent_permit_date_sql',
                   '_older_permit_date_sql', '_append_building_only_filters', '_parse_boroughs_param'}
        nodes = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in helpers]
        self.env.update(_PROPERTY_TYPE_SQL={'residential': "b.building_class ~ '^[ABCDR]'",
                        'commercial': "b.building_class ~ '^[KOEFG]'", 'mixed': "b.building_class ~ '^S'"},
                        _PERMIT_ACTIVITY_MODES={'within', 'inactive'})
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'actual-directory-filters', 'exec'), self.env)

    def refresh(self, force=True):
        connection = self.connect()
        try:
            return directory.refresh(connection, force=force)
        finally:
            connection.close()

    def state(self):
        with self.connect() as connection:
            with connection.cursor() as cur:
                cur.execute('SELECT * FROM contractor_directory_state')
                return cur.fetchone()

    def test_indexed_and_live_paths_match_all_filters_and_sorts(self):
        queries = ['', 'search=Alpha', 'search=nonexistent', 'per_page=1&page=99',
                   'min_jobs=10&max_jobs=12', 'min_active_jobs=12', 'min_properties=1&max_properties=1',
                   'role=permittee', 'role=owner', 'probable_gc=true', 'borough=3',
                   'borough=1', 'property_type=residential', 'property_type=commercial',
                   'building_class=C', 'min_units=5&max_units=20', 'min_value=50000&max_value=200000',
                   'has_violations=true', 'has_violations=false', 'work_type=GC',
                   'permit_type=EW', 'job_type=NB,A2', 'license_type=GC',
                   'recent_permit_days=10', 'recent_permit_days=10&permit_activity_mode=inactive',
                   'role=owner&min_jobs=2&page=99', 'borough=&borough=1', 'work_type=&work_type=PL']
        queries += [f'sort_by={key}&sort_order={order}'
                    for key in ('total_jobs', 'active_jobs', 'total_value', 'largest_project',
                                'unique_properties', 'most_recent_job') for order in ('asc', 'desc')]
        for query in queries:
            with self.subTest(query=query):
                indexed = self.get('/contractors?' + query)
                with patch.object(directory, 'prepare', return_value=None):
                    live = self.get('/contractors?' + query)
                self.assertEqual(live, indexed)

    def test_source_changes_refresh_and_failures_preserve_previous_generation(self):
        before = self.get('/contractors?search=Alpha')
        generation = self.state()['generation']
        with self.connect() as connection:
            with connection.cursor() as cur:
                cur.execute('UPDATE buildings SET assessed_total_value=200000')
        self.assertNotEqual(self.state()['source_version'], self.state()['indexed_version'])
        self.assertFalse(self.refresh(force=False))  # Debounce a just-written batch.
        self.assertEqual(before, self.get('/contractors?search=Alpha'))
        with patch.object(directory, 'FACT_SELECT', 'SELECT nonexistent_column FROM permits'):
            with self.assertRaises(Exception):
                self.refresh()
        self.assertEqual(generation, self.state()['generation'])
        self.assertEqual(before, self.get('/contractors?search=Alpha'))
        self.assertTrue(self.refresh())
        after = self.get('/contractors?search=Alpha')
        self.assertEqual(int(before['contractors'][0]['total_value']) * 2, int(after['contractors'][0]['total_value']))
        self.assertEqual(self.state()['source_version'], self.state()['indexed_version'])
        with self.connect() as connection:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM permits WHERE id=1')
        self.assertTrue(self.refresh())
        self.assertEqual(11, self.get('/contractors?search=Alpha')['contractors'][0]['total_jobs'])

    def test_unrelated_building_enrichment_does_not_rebuild_directory(self):
        before = self.state()['source_version']
        with self.connect() as connection:
            with connection.cursor() as cur:
                cur.execute('UPDATE buildings SET purchase_price=750000')
        self.assertEqual(before, self.state()['source_version'])
        self.assertFalse(self.refresh(force=False))

    def test_daily_aging_and_view_recreation(self):
        self.assertFalse(self.refresh(force=False))
        with self.connect() as connection:
            with connection.cursor() as cur:
                cur.execute("UPDATE contractor_directory_state SET indexed_date=CURRENT_DATE-1")
                # No permanent dependency on the pipeline's recreated view.
                cur.execute('ALTER TABLE permit_participants RENAME TO participant_rows')
                cur.execute('CREATE VIEW permit_participants AS SELECT * FROM participant_rows')
                cur.execute('DROP VIEW permit_participants')
                cur.execute('CREATE VIEW permit_participants AS SELECT * FROM participant_rows')
        self.assertTrue(self.refresh(force=False))
        self.assertEqual(date.today(), self.state()['indexed_date'])

    def test_publication_waits_for_existing_reader_and_single_refresher(self):
        reader = self.connect()
        try:
            with reader.cursor() as cur:
                generation = directory.prepare(cur)
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.refresh)
                # Observe the refresh reaching publication, without guessing a sleep.
                observer = self.connect()
                try:
                    import time
                    deadline = time.monotonic() + 5
                    blocked = False
                    while time.monotonic() < deadline:
                        with observer.cursor() as cur:
                            cur.execute("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=72119 AND objid=1 AND NOT granted) AS blocked")
                            blocked = cur.fetchone()['blocked']
                        observer.commit()
                        if blocked:
                            break
                    self.assertTrue(blocked)
                    self.assertFalse(self.refresh())  # A second builder cannot race.
                    with reader.cursor() as cur:
                        cur.execute('SELECT COUNT(*) AS n FROM contractor_directory_stats WHERE generation=%s', [generation])
                        self.assertEqual(3, cur.fetchone()['n'])
                    reader.commit()
                    self.assertTrue(future.result(timeout=5))
                finally:
                    reader.rollback()  # Release lock even on a failed assertion.
                    observer.close()
        finally:
            reader.close()
