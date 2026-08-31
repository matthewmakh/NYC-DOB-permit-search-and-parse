"""Regression checks for sponsored access and sponsor-funded usage."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from team_service import build_access_context
import stripe_service


ROOT = Path(__file__).resolve().parent


def user_row(**overrides):
    row = {
        'id': 20,
        'email': 'employee@example.com',
        'is_admin': False,
        'subscription_status': 'inactive',
        'sponsorship_status': None,
        'subscription_bypass': None,
        'bill_usage_to_sponsor': None,
        'sponsor_user_id': None,
        'sponsor_email': None,
        'sponsor_is_admin': None,
        'sponsor_subscription_status': None,
    }
    row.update(overrides)
    return row


class SponsoredAccessTests(unittest.TestCase):
    def test_admin_has_complimentary_personal_usage(self):
        context = build_access_context(user_row(is_admin=True, id=1, email='matt@tyeny.com'))
        self.assertTrue(context['has_access'])
        self.assertEqual('admin', context['access_source'])
        self.assertFalse(context['should_charge_usage'])
        self.assertEqual(1, context['billing_user_id'])

    def test_employee_inherits_access_but_sponsor_is_payer(self):
        context = build_access_context(user_row(
            sponsorship_status='active',
            subscription_bypass=True,
            bill_usage_to_sponsor=True,
            sponsor_user_id=1,
            sponsor_email='matt@tyeny.com',
            sponsor_is_admin=True,
            sponsor_subscription_status='active',
        ))
        self.assertTrue(context['has_access'])
        self.assertTrue(context['is_sponsored'])
        self.assertEqual('sponsored', context['access_source'])
        self.assertTrue(context['should_charge_usage'])
        self.assertEqual(1, context['billing_user_id'])
        self.assertEqual('matt@tyeny.com', context['billing_email'])

    def test_ineligible_sponsor_does_not_grant_access(self):
        context = build_access_context(user_row(
            sponsorship_status='active',
            subscription_bypass=True,
            bill_usage_to_sponsor=True,
            sponsor_user_id=1,
            sponsor_is_admin=False,
            sponsor_subscription_status='past_due',
        ))
        self.assertFalse(context['has_access'])
        self.assertFalse(context['should_charge_usage'])

    def test_direct_subscriber_remains_own_payer(self):
        context = build_access_context(user_row(subscription_status='active'))
        self.assertEqual('direct', context['access_source'])
        self.assertTrue(context['should_charge_usage'])
        self.assertEqual(20, context['billing_user_id'])

    def test_revoked_sponsorship_no_longer_grants_access(self):
        context = build_access_context(user_row(
            sponsorship_status='revoked',
            subscription_bypass=True,
            bill_usage_to_sponsor=True,
            sponsor_user_id=1,
            sponsor_is_admin=True,
        ))
        self.assertFalse(context['has_access'])
        self.assertEqual('none', context['access_source'])


class TeamFeatureContractTests(unittest.TestCase):
    def test_admin_ui_and_setup_page_exist(self):
        self.assertTrue((ROOT / 'templates' / 'admin_team.html').is_file())
        self.assertTrue((ROOT / 'templates' / 'team_setup.html').is_file())
        nav = (ROOT / 'templates' / '_site_nav.html').read_text()
        self.assertIn('href="/admin/team"', nav)

    def test_mutating_admin_routes_require_csrf(self):
        app_source = (ROOT / 'app.py').read_text()
        self.assertGreaterEqual(app_source.count("if not _valid_admin_csrf():"), 5)
        self.assertIn("@app.route('/api/admin/team/invitations', methods=['POST'])", app_source)

    def test_charge_records_actor_and_payer(self):
        stripe_source = (ROOT / 'stripe_service.py').read_text()
        self.assertIn("'initiated_by_user_id': str(user_id)", stripe_source)
        self.assertIn("'billing_user_id': str(payer['id'])", stripe_source)
        self.assertIn('(user_id, billing_user_id, building_id', stripe_source)

    def test_schema_has_sponsorship_and_payer_attribution(self):
        source = (ROOT / 'team_service.py').read_text()
        self.assertIn('CREATE TABLE IF NOT EXISTS account_sponsorships', source)
        self.assertIn('bill_usage_to_sponsor BOOLEAN NOT NULL DEFAULT TRUE', source)
        self.assertIn('ALTER TABLE enrichment_transactions', source)
        self.assertIn('ADD COLUMN IF NOT EXISTS billing_user_id', source)


class FakeCursor:
    def __init__(self, connection, fail_audit=False):
        self.connection = connection
        self.fail_audit = fail_audit
        self.executions = []
        self.next_row = None

    def execute(self, sql, params=None):
        self.executions.append((sql, params))
        if 'FROM users WHERE id = %s' in sql:
            self.next_row = {
                'id': 1, 'email': 'matt@tyeny.com',
                'stripe_customer_id': 'cus_sponsor',
            }
        elif 'INSERT INTO enrichment_transactions' in sql and self.fail_audit:
            raise RuntimeError('temporary ledger error')

    def fetchone(self):
        return self.next_row

    def close(self):
        pass


class FakeConnection:
    def __init__(self, fail_audit=False):
        self.cursor_instance = FakeCursor(self, fail_audit=fail_audit)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, **_kwargs):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class StripeSponsoredBillingTests(unittest.TestCase):
    def sponsored_context(self):
        return {
            'id': 20, 'email': 'employee@example.com', 'has_access': True,
            'is_sponsored': True, 'should_charge_usage': True,
            'billing_user_id': 1,
        }

    def test_employee_charge_uses_sponsor_customer_and_keeps_actor(self):
        connection = FakeConnection()
        payment_intent = SimpleNamespace(id='pi_team', status='succeeded')
        with patch.object(stripe_service, 'get_db_connection', return_value=connection), \
             patch.object(stripe_service, 'get_access_context', return_value=self.sponsored_context()), \
             patch.object(stripe_service, '_default_payment_method', return_value='pm_sponsor'), \
             patch.object(stripe_service.stripe.PaymentIntent, 'create', return_value=payment_intent) as create:
            success, _message, charge_id = stripe_service.charge_enrichment_fee(
                20, 99, 'Alex Rivera', charge_scope='owner_enrichment')

        self.assertTrue(success)
        self.assertEqual('pi_team', charge_id)
        kwargs = create.call_args.kwargs
        self.assertEqual('cus_sponsor', kwargs['customer'])
        self.assertEqual('pm_sponsor', kwargs['payment_method'])
        self.assertEqual('20', kwargs['metadata']['initiated_by_user_id'])
        self.assertEqual('1', kwargs['metadata']['billing_user_id'])
        ledger_params = connection.cursor_instance.executions[-1][1]
        self.assertEqual(20, ledger_params[0])
        self.assertEqual(1, ledger_params[1])

    def test_admin_personal_lookup_remains_complimentary(self):
        connection = FakeConnection()
        admin_context = {
            'id': 1, 'has_access': True, 'is_sponsored': False,
            'should_charge_usage': False, 'billing_user_id': 1,
        }
        with patch.object(stripe_service, 'get_db_connection', return_value=connection), \
             patch.object(stripe_service, 'get_access_context', return_value=admin_context), \
             patch.object(stripe_service.stripe.PaymentIntent, 'create') as create:
            success, message, charge_id = stripe_service.charge_enrichment_fee(
                1, 99, 'Alex Rivera')
        self.assertTrue(success)
        self.assertEqual('Admin bypass', message)
        self.assertEqual('admin_free', charge_id)
        create.assert_not_called()

    def test_successful_stripe_charge_is_not_reversed_by_audit_failure(self):
        connection = FakeConnection(fail_audit=True)
        payment_intent = SimpleNamespace(id='pi_paid', status='succeeded')
        with patch.object(stripe_service, 'get_db_connection', return_value=connection), \
             patch.object(stripe_service, 'get_access_context', return_value=self.sponsored_context()), \
             patch.object(stripe_service, '_default_payment_method', return_value='pm_sponsor'), \
             patch.object(stripe_service.stripe.PaymentIntent, 'create', return_value=payment_intent):
            success, _message, charge_id = stripe_service.charge_enrichment_fee(
                20, 99, 'Alex Rivera')
        self.assertTrue(success)
        self.assertEqual('pi_paid', charge_id)
        self.assertEqual(1, connection.rollbacks)


if __name__ == '__main__':
    unittest.main()
