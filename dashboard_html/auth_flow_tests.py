"""Account entry regressions without production data, email, or payment calls."""
import html
import os
import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from flask import Flask
import auth_routes as routes
import auth_service


class AuthFlowTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parent
        self.app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
        self.app.config.update(TESTING=True, SECRET_KEY='local-test-only')
        self.app.register_blueprint(routes.auth_bp)

        @self.app.get('/private')
        @auth_service.login_required
        def private():
            return 'Private workspace'

        for name in ('log_page_view', 'log_auth_event', 'validate_session'):
            mocked = patch.object(routes, name, return_value=None)
            self.addCleanup(mocked.stop)
            setattr(self, name, mocked.start())
        self.client = self.app.test_client()

    def sign_in(self, url):
        with patch.object(routes, 'authenticate_user', return_value=(True, '', {'id': 1})), \
                patch.object(routes, 'create_session', return_value='synthetic-session'):
            return self.client.post(url, data={'email': 'tester@example.test', 'password': 'synthetic-only'})

    def test_expired_session_preserves_workspace_and_filters_through_login_form(self):
        destination = '/private?stage=new&q=East%20Houston'
        with patch.object(auth_service, 'validate_session', return_value=None):
            blocked = self.client.get(destination)
        self.assertEqual(302, blocked.status_code)
        login = self.client.get(blocked.location)
        action = html.unescape(re.search(r'<form[^>]+action="([^"]+)"', login.text).group(1))
        self.assertEqual(destination, parse_qs(urlsplit(action).query)['next'][0])
        signed_in = self.sign_in(action)
        self.assertEqual(destination, signed_in.location)

    def test_login_rejects_external_and_browser_normalized_redirects(self):
        for target in ('https://example.test/', '//example.test/', '/\\example.test/', '/\n/example.test/'):
            with self.subTest(target=target):
                url = '/auth/login?' + urlencode({'next': target})
                self.assertEqual('/', self.sign_in(url).location)

    def test_already_signed_in_user_reaches_requested_workspace(self):
        with self.client.session_transaction() as session:
            session['session_token'] = 'synthetic-session'
        self.validate_session.return_value = {'id': 1}
        self.assertEqual('/crm/deals', self.client.get('/auth/login?next=/crm/deals').location)

    def test_recovery_page_renders_and_offers_administrator_contact(self):
        with patch.dict(os.environ, {'ADMIN_EMAIL': 'admin@example.test'}):
            response = self.client.get('/auth/forgot-password')
        self.assertEqual(200, response.status_code)
        self.assertIn('/static/css/app.css', response.text)
        self.assertIn('mailto:admin@example.test?', response.text)
        self.assertIn('Automatic password reset is currently unavailable', response.text)

    def test_legacy_reset_submission_never_claims_delivery_or_account_existence(self):
        for email in ('known@example.test', 'unknown@example.test'):
            response = self.client.post('/auth/forgot-password', data={'email': email})
            self.assertEqual(200, response.status_code)
            self.assertNotIn('reset link has been sent', response.text)
            self.assertFalse(self.log_auth_event.call_args.kwargs['success'])

    def test_legacy_verification_link_explains_auto_verification(self):
        response = self.client.get('/auth/resend-verification', follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertIn('Accounts are verified automatically', response.text)
        self.assertNotIn('Verification email resent', response.text)


if __name__ == '__main__':
    unittest.main()
