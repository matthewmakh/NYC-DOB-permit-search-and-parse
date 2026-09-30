"""Offline ownership selection and date-provenance regressions."""
import os
os.environ['DATABASE_URL'] = 'postgresql://unused@localhost:1/unused'
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
import unittest
from datetime import date, datetime
from unittest.mock import Mock, patch
import _pipeline_path  # noqa: F401
from owner_source_dates import owner_source_dates, latest_hpd_registration, source_date
import property_source_refresh as refresh
import step2_enrich_from_pluto as property_api
import step3_enrich_from_acris as acris
import step4_enrich_from_tax_liens as tax


class OwnerSourceTests(unittest.TestCase):
    def test_hpd_uses_processed_date_not_expiry_or_missing_date(self):
        rows = [
            dict(registrationid='99', lastregistrationdate=None, registrationenddate='2030-01-01'),
            dict(registrationid='1', lastregistrationdate='2024-01-01', registrationenddate='2029-01-01'),
            dict(registrationid='2', lastregistrationdate='2026-09-01', registrationenddate='2027-01-01'),
        ]
        self.assertEqual(latest_hpd_registration(rows)['registrationid'], '2')
        client = Mock()
        client.get_all.side_effect = [rows, [dict(type='IndividualOwner', firstname='Newest', lastname='Owner')], [], []]
        with patch.object(property_api, '_get_client', return_value=client), patch.object(property_api.time, 'sleep'):
            data, error = property_api.get_hpd_data_for_bbl('3012980066')
        self.assertIsNone(error)
        self.assertEqual((data['owner_name_hpd'], data['hpd_last_registration_date']),
                         ('Newest Owner', date(2026, 9, 1)))
        self.assertEqual(client.get_all.call_args_list[1].kwargs['registrationid'], '2')

    def test_rpad_latest_year_final_roll_wins_and_keeps_its_period(self):
        client = Mock()
        client.get_columns.return_value = {'year', 'period'}
        client.get_all.return_value = [
            dict(year='2018/19', period='TENTATIVE', owner='Tentative Owner'),
            dict(year='2017/18', period='FINAL', owner='Older Owner'),
            dict(year='2018/19', period='FINAL', owner='Latest Final Owner'),
        ]
        with patch.object(property_api, '_get_client', return_value=client), patch.object(property_api.time, 'sleep'):
            data, error = property_api.get_rpad_data_for_bbl('3012980066')
        self.assertIsNone(error)
        self.assertEqual(data['owner_name_rpad'], 'Latest Final Owner')
        self.assertEqual(data['rpad_assessment_year'], '2018/19')
        self.assertEqual(data['rpad_assessment_period'], 'FINAL')
        self.assertEqual(refresh.source_fields('rpad', data)['rpad_assessment_year'], '2018/19')

    def test_rpad_cannot_guess_without_a_year(self):
        client = Mock()
        client.get_columns.return_value = {'owner'}
        with patch.object(property_api, '_get_client', return_value=client):
            data, error = property_api.get_rpad_data_for_bbl('3012980066')
        self.assertIsNone(data)
        self.assertIn('cannot select newest', error)
        client.get_all.assert_not_called()

    def test_ecb_name_and_date_come_from_same_latest_named_violation(self):
        client = Mock()
        client.get_all.return_value = [
            dict(respondent_name='Undated', issue_date=None),
            dict(respondent_name='Older', issue_date='20200101', hearing_date='20300101'),
            dict(respondent_name='', issue_date='20260920'),
            dict(respondent_name='Latest Named', issue_date='20260901', respondent_city='Brooklyn'),
        ]
        with patch.object(tax, '_get_client', return_value=client), patch.object(tax.time, 'sleep'):
            data, error = tax.get_ecb_violations_data('3012980066')
        self.assertIsNone(error)
        self.assertEqual(data['ecb_respondent_name'], 'Latest Named')
        self.assertEqual(data['ecb_respondent_issue_date'], date(2026, 9, 1))
        self.assertIsInstance(data['ecb_last_checked'], datetime)
        cursor = Mock()
        with patch('owner_source_history.capture_source_snapshot', return_value={'records': []}), \
                patch('owner_source_history.record_source_snapshot'):
            tax.update_building_tax_lien_data(cursor, 1, data)
        self.assertIn('ecb_respondent_issue_date = %s', cursor.execute.call_args.args[0])
        self.assertIn(date(2026, 9, 1), cursor.execute.call_args.args[1])

    def test_ecb_empty_and_failed_responses_do_not_invent_report_dates(self):
        client = Mock()
        client.get_all.return_value = []
        with patch.object(tax, '_get_client', return_value=client), patch.object(tax.time, 'sleep'):
            data, error = tax.get_ecb_violations_data('3012980066')
            self.assertIsNone(error)
            self.assertIsNone(data['ecb_respondent_issue_date'])
            self.assertIsNone(data['ecb_respondent_name'])
            client.get_all.side_effect = RuntimeError('offline')
            data, error = tax.get_ecb_violations_data('3012980066')
            self.assertIsNone(data)
            self.assertIn('offline', error)

    def test_acris_uses_recording_order_even_for_older_executed_deed(self):
        rows = [dict(doc_type='DEED', document_id='1', doc_date=date(2026, 8, 1), recorded_date=date(2026, 8, 5)),
                dict(doc_type='DEED', document_id='2', doc_date=date(2026, 7, 1), recorded_date=date(2026, 9, 1)),
                dict(doc_type='MTGE', document_id='3', doc_date=date(2026, 9, 15), recorded_date=date(2026, 9, 20))]
        self.assertEqual(acris.find_primary_deed(rows)['document_id'], '2')

    def test_fetch_dates_formation_and_hearing_dates_never_become_report_dates(self):
        building = dict(sale_recorded_date=date(2026, 9, 1), acris_last_enriched=datetime(2026, 9, 30),
                        sos_formation_date=date(2000, 1, 1), sos_last_enriched=datetime(2026, 9, 29),
                        ecb_most_recent_hearing_date=date(2030, 1, 1), last_updated=datetime(2026, 9, 30),
                        owner_source_fields=dict(pluto_version='26v2', rpad_assessment_year='2018/19',
                                                 rpad_assessment_period='FINAL'))
        result = owner_source_dates(building, [dict(source='pluto', checked_at=datetime(2026, 9, 28), error='offline')])
        self.assertEqual(result['acris']['reported_date'], '2026-09-01')
        self.assertEqual(result['pluto']['period'], 'PLUTO 26v2')
        self.assertEqual(result['rpad']['period'], 'FY 2018/19 · Final')
        for source in ['pluto', 'rpad', 'sos', 'ecb']:
            self.assertIsNone(result[source]['reported_date'])
        self.assertEqual(result['sos']['checked_at'], '2026-09-29T00:00:00')
        self.assertTrue(result['pluto']['refresh_failed'])

    def test_normalizes_dates_and_clears_dates_with_authoritative_empty_name(self):
        self.assertEqual(source_date('20260901'), date(2026, 9, 1))
        self.assertEqual(source_date('2026-09-01T00:00:00'), date(2026, 9, 1))
        self.assertIsNone(source_date('not a date'))
        self.assertIsNone(refresh.source_fields('hpd', None)['hpd_last_registration_date'])
        self.assertIsNone(refresh.source_fields('pluto', None)['pluto_version'])


if __name__ == '__main__':
    unittest.main()
