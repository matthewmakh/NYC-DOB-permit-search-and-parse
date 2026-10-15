"""CRM route round trips in a disposable schema; never load the production app."""
import os
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from flask import Flask
import crm_routes
import crm_service as crm
import prospecting_tests as fixtures


@unittest.skipUnless(os.getenv('PROSPECTING_TEST_DATABASE_URL'), 'Disposable PostgreSQL not configured')
class CrmWorkflowTests(unittest.TestCase):
    connect = fixtures.DatabaseTests.connect
    create = fixtures.DatabaseTests.create
    count = fixtures.DatabaseTests.count

    def setUp(self):
        if urlsplit(os.environ['PROSPECTING_TEST_DATABASE_URL']).hostname not in ('localhost', '127.0.0.1'):
            self.skipTest('This suite only uses a disposable localhost database')
        fixtures.DatabaseTests.setUp(self)
        self.user = {'id': 1, 'is_admin': True, 'email': 'admin@example.test'}
        root = Path(__file__).resolve().parent
        app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
        app.config.update(TESTING=True, SECRET_KEY='isolated-workflow-test')
        app.register_blueprint(crm_routes.crm_bp)
        app.add_url_rule('/auth/login', endpoint='auth.login', view_func=lambda: 'Test login')
        self.client = app.test_client()
        self.auth = patch('auth_service.validate_session', side_effect=lambda token: self.user)
        self.auth.start()
        self.maps = patch.object(crm, 'building_streetview', return_value=None)
        self.maps.start()

    def tearDown(self):
        self.auth.stop()
        self.maps.stop()
        fixtures.DatabaseTests.tearDown(self)

    def post(self, path, data):
        response = self.client.post('/crm/api/' + path, json=data)
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue(response.json['success'], response.json)
        return response.json

    def test_create_edit_followup_list_deal_and_exports(self):
        response = self.client.post('/crm/buildings/add', data={
            'address': '123 Synthetic Street', 'borough': 'BROOKLYN', 'first_note': 'Initial fixture note'})
        self.assertEqual(302, response.status_code)
        building_id = int(response.location.rsplit('/', 1)[1])
        contact_id = self.post('contact', {'name': 'Taylor Tester', 'building_id': building_id,
            'phone': '(212) 555-0199', 'email': 'taylor@example.test'})['contact_id']
        self.post(f'contact/{contact_id}/update', {'title': 'Property Manager'})
        self.post(f'building/{building_id}/update', {'owner_name': 'Synthetic Holdings LLC'})
        self.post('stage', {'building_id': building_id, 'stage': 'interested'})
        self.post('note', {'contact_id': contact_id, 'note': 'Fixture note after conversation'})
        follow_up_id = self.post('followup', {'contact_id': contact_id, 'title': 'Send proposal',
            'due_date': '2026-10-20', 'due_time': '10:30'})['follow_up_id']
        self.post('contacted', {'contact_id': contact_id, 'building_id': building_id,
            'method': 'call', 'outcome': 'meeting_set', 'complete_followup_id': follow_up_id})
        listing = self.post('list', {'name': 'Synthetic prospect list'})['list_id']
        self.post('list-item', {'list_id': listing, 'building_id': building_id})
        self.post('list-item', {'list_id': listing, 'contact_id': contact_id})
        response = self.client.post('/crm/deals/add', data={'name': 'Synthetic access control deal',
            'building_id': building_id, 'contact_id': contact_id, 'stage': 'prospect',
            'estimated_value': '2500', 'next_step': 'Send proposal'})
        self.assertEqual(302, response.status_code)
        deal_id = int(response.location.rsplit('/', 1)[1])
        self.post(f'deal/{deal_id}/update', {'stage': 'quoted', 'estimated_value': '3000'})
        self.assertEqual('interested', crm.get_building(fixtures.ADMIN, building_id)['stage'])
        self.assertEqual('Property Manager', crm.get_contact(fixtures.ADMIN, contact_id)['title'])
        self.assertEqual('quoted', crm.get_deal(fixtures.ADMIN, deal_id)['stage'])
        self.assertEqual(1, self.count('crm_follow_ups'))
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT status FROM crm_follow_ups WHERE id=%s', (follow_up_id,))
                self.assertEqual('done', cursor.fetchone()['status'])
        for path in ('', 'buildings', 'buildings?view=table', 'buildings?view=board',
                     f'buildings/{building_id}', 'contacts', f'contacts/{contact_id}',
                     'deals', f'deals/{deal_id}', f'deals/{deal_id}/edit', 'lists',
                     f'lists/{listing}', 'followups', 'focus', 'history', 'notifications', 'team'):
            with self.subTest(path=path):
                response = self.client.get('/crm/' + path)
                self.assertEqual(200, response.status_code)
                self.assertIn('/static/css/app.css', response.text)
        for name in ('buildings', 'activity', 'deals'):
            response = self.client.get(f'/crm/api/export/{name}.csv')
            self.assertEqual(200, response.status_code)
            self.assertEqual('text/csv', response.mimetype)
            self.assertIn('attachment', response.headers['Content-Disposition'])
            self.assertGreater(len(response.text.splitlines()), 1)

    def test_record_privacy_and_contact_validation(self):
        private_id = crm.create_contact(fixtures.OTHER_REP, name='Private fixture contact')
        self.user = {'id': 2, 'is_sponsored': True, 'sponsor_user_id': 1, 'email': 'rep@example.test'}
        response = self.client.post(f'/crm/api/contact/{private_id}/update', json={'name': 'Changed'})
        self.assertEqual(404, response.status_code)
        response = self.client.get('/crm/contacts?q=Private')
        self.assertNotIn('Private fixture contact', response.text)
        self.assertEqual(403, self.client.get('/crm/api/export/buildings.csv').status_code)
        self.assertEqual(400, self.client.post('/crm/api/contact', json={'name': ''}).status_code)
        contact_id = self.post('contact', {'name': 'Own fixture contact', 'phone': '2125550199'})['contact_id']
        duplicate = self.client.post('/crm/api/contact', json={'name': 'Duplicate', 'phone': '2125550199'})
        self.assertEqual(409, duplicate.status_code)
        self.assertTrue(duplicate.json['duplicate'])
        self.assertEqual(403, self.client.post(f'/crm/api/contact/{contact_id}/delete', json={}).status_code)
        self.user = {'id': 1, 'is_admin': True, 'email': 'admin@example.test'}
        self.post(f'contact/{contact_id}/delete', {})
        self.assertIsNone(crm.get_contact(fixtures.REP, contact_id))


if __name__ == '__main__':
    unittest.main()
