"""Focused regression coverage for CRM ownership, workflow, and UI additions."""

import pathlib
import unittest
from datetime import date

from jinja2 import Environment, FileSystemLoader

import crm_service


ROOT = pathlib.Path(__file__).resolve().parent


class CrmV3Tests(unittest.TestCase):
    def test_rep_scope_is_strictly_assigned(self):
        sql = crm_service.record_scope_sql(
            {'team_id': 10, 'user_id': 20, 'is_admin': False}, 'd')
        self.assertIn('d.assigned_to_id = %(user_id)s', sql)
        self.assertNotIn('d.added_by_id = %(user_id)s', sql)
        self.assertIn('d.team_id = %(team_id)s', sql)

    def test_admin_scope_is_team_wide(self):
        sql = crm_service.record_scope_sql(
            {'team_id': 10, 'user_id': 10, 'is_admin': True}, 'b')
        self.assertIn('b.team_id = %(team_id)s', sql)
        self.assertNotIn('assigned_to_id', sql)
        self.assertNotIn('added_by_id', sql)

    def test_rep_lists_are_strictly_assigned(self):
        sql = crm_service.list_scope_sql(
            {'team_id': 10, 'user_id': 20, 'is_admin': False}, 'l')
        self.assertIn('l.assigned_to_id = %(user_id)s', sql)
        self.assertNotIn('owner_id = %(user_id)s', sql)

    def test_row_visibility_does_not_treat_attribution_as_access(self):
        rep = {'user_id': 20, 'is_admin': False}
        self.assertTrue(crm_service.row_visible(
            rep, {'assigned_to_id': 20, 'added_by_id': 99}))
        self.assertFalse(crm_service.row_visible(
            rep, {'assigned_to_id': 99, 'added_by_id': 20}))

    def test_due_times_follow_new_york_dst(self):
        winter = crm_service.local_due_at(date(2026, 1, 15), '09:00')
        summer = crm_service.local_due_at(date(2026, 7, 15), '09:00')
        self.assertEqual((winter.hour, winter.minute), (14, 0))
        self.assertEqual((summer.hour, summer.minute), (13, 0))

    def test_schema_contains_core_v3_entities(self):
        ddl = '\n'.join(crm_service.CRM_SCHEMA_STATEMENTS)
        for table in ('crm_deals', 'crm_change_history', 'crm_notifications',
                      'crm_notification_preferences', 'crm_push_subscriptions'):
            self.assertIn(f'CREATE TABLE IF NOT EXISTS {table}', ddl)
        self.assertIn("'nurture'", ddl)
        self.assertIn('next_step_notified_at', ddl)
        self.assertIn('ON DELETE SET NULL', ddl)

    def test_all_crm_templates_parse(self):
        templates = ROOT / 'templates'
        env = Environment(loader=FileSystemLoader(str(templates)))
        for template in sorted((templates / 'crm').rglob('*.html')):
            with self.subTest(template=template.name):
                env.parse(template.read_text())


if __name__ == '__main__':
    unittest.main()
