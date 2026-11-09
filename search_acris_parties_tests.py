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
        self.assertIn('| DEED | 2026-09-24 | $7,350,000', out)

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
        # party_type 1 is the grantor/seller and 2 the grantee/buyer (checked on live DEEDs)
        self.assertEqual((closing['buyer'], closing['seller']),
                         ('ARIA SUNDARAM 2026 TRUST', 'SELLER LLC'))

    def test_linked_closings_only_consider_certs_inside_the_search_window(self):
        cert_id = 'CERTDOC1'
        props = {cert_id: {'bbl': '1000010001', 'borough': '1', 'block': '00001', 'lot': '0001'}}
        txns = {cert_id: {'doc_type': 'CERT', 'recorded_date': '2019-05-01', 'doc_date': ''}}
        certs = [{'document_id': cert_id, 'name': 'TITLE CO LLC'}]
        api = StubbedApi(master=[DEED_MASTER_ROW], legals=[LEGAL_ROW])
        with patch.object(acris, 'api_request', api):
            outside, _ = quiet(acris.find_linked_closings, certs, props, txns,
                               start_date='2026-01-01', end_date='2026-12-31')
            inside, _ = quiet(acris.find_linked_closings, certs, props, txns,
                              start_date='2019-01-01', end_date='2019-12-31')
            unbounded, _ = quiet(acris.find_linked_closings, certs, props, txns)
        self.assertEqual(outside, [])
        self.assertEqual(len(inside), 1)
        self.assertEqual(len(unbounded), 1)

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


CERT_ID = 'CERTDOC1'
CERT_MASTER_ROW = {'document_id': CERT_ID, 'doc_type': 'CERT', 'crfn': 'C1',
                   'document_date': '2026-09-26T00:00:00.000', 'document_amt': '0',
                   'recorded_datetime': '2026-09-28T00:00:00.000'}
CERT_LEGAL_ROW = dict(LEGAL_ROW, document_id=CERT_ID)


def fake_acris(url, params, retries=acris.MAX_RETRIES):
    """A tiny ACRIS world: one title-company CERT and the DEED recorded on the same lot."""
    where = params['$where']
    if url == acris.ACRIS_PARTIES_API:
        if 'LIKE' in where:  # the party-name search
            return [{'document_id': CERT_ID, 'name': 'TITLE CO LLC', 'party_type': '3'}]
        return [{'name': 'SELLER LLC', 'party_type': '1'},
                {'name': 'ARIA SUNDARAM 2026 TRUST', 'party_type': '2'}]
    if url == acris.ACRIS_LEGALS_API:
        if where.startswith('document_id IN'):
            return [CERT_LEGAL_ROW]
        return [LEGAL_ROW, CERT_LEGAL_ROW]  # borough/block/lot lookup
    if "doc_type IN ('DEED'" in where:  # closings on the lot
        return [DEED_MASTER_ROW]
    return [CERT_MASTER_ROW]


class CliEndToEndTests(unittest.TestCase):
    """Run main() the way the shell does, with the network replaced by fake_acris."""

    def run_cli(self, *argv, output):
        import csv
        import json
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, output)
            with patch.object(acris, 'api_request', fake_acris), \
                    patch.object(acris.time, 'sleep'), \
                    patch('sys.argv', ['search_acris_parties.py', 'TITLE CO', '--start', '2026-09-01',
                                       '--end', '2026-09-30', *argv, '--output', path]):
                _, console = quiet(acris.main)
            with open(path, encoding='utf-8') as f:
                rows = json.load(f) if output.endswith('.json') else list(csv.DictReader(f))
        return rows, console

    def test_plain_search_writes_cert_amount_and_date_to_csv(self):
        (row,), console = self.run_cli(output='out.csv')
        self.assertEqual((row['doc_type'], row['doc_date'], row['recorded_date']),
                         ('CERT', '2026-09-26', '2026-09-28'))
        self.assertEqual(row['party_type'], 'Other (Title Co/Attorney/etc)')
        self.assertNotIn('Unknown: 1', console)

    def test_link_closings_writes_deed_amount_date_and_parties_to_json(self):
        (row,), console = self.run_cli('--link-closings', output='out.json')
        self.assertEqual((row['document_id'], row['doc_type']), (DEED_ID, 'DEED'))
        self.assertEqual((row['doc_amount'], row['doc_date']), ('7350000', '2026-09-24'))
        self.assertEqual((row['buyer'], row['seller']), ('ARIA SUNDARAM 2026 TRUST', 'SELLER LLC'))
        self.assertIn('| DEED | 2026-09-24 | $7,350,000', console)

    def test_link_closings_with_no_cert_in_range_says_nothing_was_linked(self):
        rows, console = self.run_cli('--link-closings', '--start', '2020-01-01', '--end', '2020-01-31',
                                     output='out.json')
        self.assertEqual(rows, [])
        self.assertIn('No linked closings found', console)

    def test_link_closings_with_no_details_explains_it_was_ignored(self):
        _, console = self.run_cli('--link-closings', '--no-details', output='out.csv')
        self.assertIn('ignored because of --no-details', console)


class QueryAndDisplayTests(unittest.TestCase):
    def test_apostrophes_are_escaped_so_socrata_does_not_reject_the_query(self):
        self.assertEqual(acris.build_fuzzy_where_clause("O'Brien Title"),
                         "upper(name) LIKE '%O''BRIEN TITLE%'")
        fuzzy = acris.build_fuzzy_where_clause("D'Angelo", fuzzy=True)
        self.assertIn("'%D''ANGELO%'", fuzzy)
        # every quote inside a literal is doubled, so no lone apostrophe remains
        self.assertNotRegex(fuzzy.replace("''", ''), r"[A-Z]'[A-Z]")

    def test_fuzzy_search_always_includes_the_exact_term_first(self):
        # Variations used to come from a set, so which ones survived the "first 8"
        # cut changed from run to run and the exact term was often not searched.
        for term in ('EXCALIBUR', 'Fidelity National Title', "O'Brien"):
            with self.subTest(term=term):
                variations = acris.generate_fuzzy_variations(term)
                self.assertEqual(variations[0], term.upper())
                self.assertEqual(len(variations), len(set(variations)))
                self.assertEqual(variations, acris.generate_fuzzy_variations(term))
                clause = acris.build_fuzzy_where_clause(term, fuzzy=True)
                self.assertIn(f"LIKE '%{acris.soql_escape(term.upper())}%'", clause)

    def test_party_type_labels_match_acris(self):
        self.assertEqual(acris.PARTY_TYPES['1'], 'Seller/Grantor/Borrower')
        self.assertEqual(acris.PARTY_TYPES['2'], 'Buyer/Grantee/Lender')

    def test_format_amount_handles_api_strings_and_blanks(self):
        self.assertEqual(acris.format_amount('7350000'), '$7,350,000')
        self.assertEqual(acris.format_amount('1250000.50'), '$1,250,000')
        for blank in ('', None, '0', 'n/a'):
            self.assertEqual(acris.format_amount(blank), '$0')


if __name__ == '__main__':
    unittest.main()
