"""Offline regressions for search_acris_parties amount/date handling; no network required.

The ACRIS Real Property Master dataset (bnx9-e6tj) names its columns
``document_amt`` and ``document_date``. The CLI used to read ``doc_amount`` /
``doc_date`` from those rows, so every emitted record had a blank amount and date.
"""
import contextlib
import io
import unittest
from unittest.mock import patch

import search_acris_parties as acris

DEED_ID = '2026092800714001'

# A real Master row: these are exactly the keys the live API returns (document_amt
# is a string there, document_date/recorded_datetime are floating timestamps).
DEED_MASTER_ROW = {
    'document_id': DEED_ID,
    'record_type': 'A',
    'crfn': '2026000300001',
    'recorded_borough': '1',
    'doc_type': 'DEED',
    'document_date': '2026-09-24T00:00:00.000',
    'document_amt': '7350000',
    'recorded_datetime': '2026-09-28T09:15:00.000',
    'modified_date': '2026-09-29T00:00:00.000',
    'reel_yr': '0',
    'reel_nbr': '0',
    'reel_pg': '0',
    'percent_trans': '100',
    'good_through_date': '2026-09-27T00:00:00.000',
}
LEGAL_ROW = {
    'document_id': DEED_ID, 'borough': '1', 'block': '1', 'lot': '1',
    'street_number': '10', 'street_name': 'MAIN ST', 'unit': '',
}


class StubbedApi:
    """Replacement for api_request that serves canned rows per ACRIS dataset."""

    def __init__(self, master=(), legals=(), parties=()):
        self.rows = {acris.ACRIS_MASTER_API: list(master),
                     acris.ACRIS_LEGALS_API: list(legals),
                     acris.ACRIS_PARTIES_API: list(parties)}

    def __call__(self, url, params, retries=acris.MAX_RETRIES):
        return self.rows[url]


def quiet(fn, *args, **kwargs):
    """Call fn and swallow its progress prints; returns (result, captured stdout)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        return fn(*args, **kwargs), out.getvalue()


class MasterAmountAndDateTests(unittest.TestCase):
    def setUp(self):
        patch.object(acris.time, 'sleep').start()
        self.addCleanup(patch.stopall)

    def fetch_transactions(self, *rows):
        with patch.object(acris, 'api_request', StubbedApi(master=rows)):
            txns, _ = quiet(acris.get_transaction_details, {r['document_id'] for r in rows})
        return txns

    def test_transaction_details_read_master_document_amt_and_document_date(self):
        txn = self.fetch_transactions(DEED_MASTER_ROW)[DEED_ID]
        self.assertEqual(txn['doc_amount'], '7350000')  # string, as the API returns it
        self.assertEqual(txn['doc_date'], '2026-09-24')
        self.assertEqual(txn['recorded_date'], '2026-09-28')
        self.assertEqual((txn['doc_type'], txn['crfn']), ('DEED', '2026000300001'))

    def test_master_rows_without_amount_or_date_stay_blank(self):
        bare = {'document_id': 'BARE', 'doc_type': 'CERT', 'recorded_datetime': '2026-09-28T00:00:00.000'}
        txn = self.fetch_transactions(bare)['BARE']
        self.assertEqual((txn['doc_amount'], txn['doc_date']), ('', ''))

    def test_build_results_keeps_doc_amount_and_doc_date_output_keys(self):
        txns = self.fetch_transactions(DEED_MASTER_ROW)
        party = {'document_id': DEED_ID, 'name': 'ARIA SUNDARAM 2026 TRUST', 'party_type': '2'}
        props = {DEED_ID: {'bbl': '1000010001', 'borough': '1', 'borough_name': 'Manhattan',
                           'block': '00001', 'lot': '0001', 'street_number': '10', 'street_name': 'MAIN ST'}}
        (record,) = acris.build_results([party], props, txns, doc_types=['DEED'])
        self.assertEqual(record['doc_amount'], '7350000')
        self.assertEqual(record['doc_date'], '2026-09-24')

    def test_build_results_date_filter_falls_back_to_document_date(self):
        # No recorded_datetime, so the filter must use document_date; before the fix
        # doc_date was always '' and this old document slipped past --start-date.
        old = {'document_id': 'OLD', 'doc_type': 'DEED', 'document_date': '2019-05-01T00:00:00.000',
               'document_amt': '1'}
        txns = self.fetch_transactions(old)
        party = {'document_id': 'OLD', 'name': 'X', 'party_type': '1'}
        self.assertEqual(acris.build_results([party], {}, txns, start_date='2026-01-01'), [])
        self.assertEqual(len(acris.build_results([party], {}, txns, start_date='2019-01-01')), 1)

    def test_summary_shows_year_and_amount_instead_of_unknown_and_zero(self):
        txns = self.fetch_transactions(DEED_MASTER_ROW)
        party = {'document_id': DEED_ID, 'name': 'ARIA SUNDARAM 2026 TRUST', 'party_type': '2'}
        props = {DEED_ID: {'bbl': '1000010001', 'borough_name': 'Manhattan',
                           'street_number': '10', 'street_name': 'MAIN ST'}}
        results = acris.build_results([party], props, txns)
        _, out = quiet(acris.print_summary, results, ['ARIA SUNDARAM 2026 TRUST'])
        self.assertIn('2026: 1', out)
        self.assertNotIn('Unknown: 1', out)
        self.assertIn('| DEED | 2026-09-24 | $7350000', out)

    def test_linked_closings_read_master_document_amt_and_document_date(self):
        cert_id = 'CERTDOC1'
        cert_results = [{'document_id': cert_id, 'name': 'TITLE CO LLC'}]
        props = {cert_id: {'bbl': '1000010001', 'borough': '1', 'block': '00001', 'lot': '0001'}}
        txns = {cert_id: {'doc_type': 'CERT', 'recorded_date': '2026-09-28', 'doc_date': ''}}
        api = StubbedApi(master=[DEED_MASTER_ROW], legals=[LEGAL_ROW],
                         parties=[{'name': 'SELLER LLC', 'party_type': '1'},
                                  {'name': 'ARIA SUNDARAM 2026 TRUST', 'party_type': '2'}])
        with patch.object(acris, 'api_request', api):
            linked, _ = quiet(acris.find_linked_closings, cert_results, props, txns)
        (closing,) = linked
        self.assertEqual(closing['document_id'], DEED_ID)
        self.assertEqual(closing['doc_amount'], '7350000')
        self.assertEqual(closing['doc_date'], '2026-09-24')
        self.assertEqual(closing['recorded_date'], '2026-09-28')

    def test_linked_closings_use_cert_document_date_when_recorded_date_missing(self):
        # find_linked_closings falls back to the CERT's doc_date from the transaction map.
        cert_id = 'CERTDOC1'
        cert_master = {'document_id': cert_id, 'doc_type': 'CERT',
                       'document_date': '2026-09-26T00:00:00.000'}
        txns = self.fetch_transactions(cert_master)
        props = {cert_id: {'bbl': '1000010001', 'borough': '1', 'block': '00001', 'lot': '0001'}}
        api = StubbedApi(master=[DEED_MASTER_ROW], legals=[LEGAL_ROW])
        with patch.object(acris, 'api_request', api):
            linked, _ = quiet(acris.find_linked_closings,
                              [{'document_id': cert_id, 'name': 'TITLE CO LLC'}], props, txns)
        self.assertEqual([c['cert_date'] for c in linked], ['2026-09-26'])


if __name__ == '__main__':
    unittest.main()
