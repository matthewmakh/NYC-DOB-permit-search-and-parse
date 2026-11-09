"""Offline regression tests for search_acris_parties. No network, no database.

    python -m unittest search_acris_parties_tests

Two bugs are pinned here:

1. Party roles were backwards. ACRIS stores only a party SLOT number (1/2/3)
   on each party row; the role it plays comes from the Document Control Codes
   (NYC Open Data 7isb-wh4c):
       DEED: 1 = GRANTOR/SELLER,       2 = GRANTEE/BUYER
       MTGE: 1 = MORTGAGOR/BORROWER,   2 = MORTGAGEE/LENDER
       ASST: 1 = ASSIGNOR/OLD LENDER,  2 = ASSIGNEE/NEW LENDER
       CERT: 1 = "PARTY 1",            2 = "PARTY 2"
   The module used to treat slot 1 as buyer and slot 2 as seller.

2. Amount and date were always blank. The Real Property Master dataset
   (bnx9-e6tj) names its columns ``document_amt`` / ``document_date``; the
   module read ``doc_amount`` / ``doc_date`` from those rows instead.

The ACRIS API calls and the control-codes lookup are stubbed throughout.
"""
import contextlib
import io
import os
import sys
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import search_acris_parties as sap  # noqa: E402
from socrata_client import _bucket_label  # noqa: E402  (importing the module put dashboard_html on sys.path)

# party1_type / party2_type strings exactly as the live control-codes table has them.
CONTROL_CODES = {
    'DEED':  ('GRANTOR/SELLER', 'GRANTEE/BUYER'),
    'DEEDO': ('GRANTOR', 'GRANTEE'),
    'MTGE':  ('MORTGAGOR/BORROWER', 'MORTGAGEE/LENDER'),
    'ASST':  ('ASSIGNOR/OLD LENDER', 'ASSIGNEE/NEW LENDER'),
    'CERT':  ('PARTY 1', 'PARTY 2'),
}
ROLES = {dt: {'1': _bucket_label(p1), '2': _bucket_label(p2), '3': 'other'}
         for dt, (p1, p2) in CONTROL_CODES.items()}

# A real recent DEED (recorded 2026-09-30, $7.35M), parties exactly as the
# ACRIS Real Property Parties dataset (636b-3b5g) returns them. Note party 2
# (the buyers) appears first and there are several of them.
DEED_ID = '2026092800714001'
SELLER = 'GARDEN HEIGHTS INVESTMENTS, LP'
BUYER = 'THE ARIA SUNDARAM 2026 TRUST'
DEED_PARTIES = [
    {'document_id': DEED_ID, 'party_type': '2', 'name': BUYER, 'address_1': '325 WEST BROADWAY'},
    {'document_id': DEED_ID, 'party_type': '1', 'name': SELLER, 'address_1': 'C/O: HOLLAND & KNIGHT LLP'},
    {'document_id': DEED_ID, 'party_type': '2', 'name': 'KETAN D. PAREKH 2026 IRREVOCABLE TRUST'},
    {'document_id': DEED_ID, 'party_type': '2', 'name': 'MONIKA H. PAREKH, AS TRUSTEE OF THE KETAN D. PAREK'},
]

# Synthetic mortgage on the same lot (no real-document claim).
MTGE_ID = 'SYNTH-MTGE-1'
MTGE_PARTIES = [
    {'document_id': MTGE_ID, 'party_type': '2', 'name': 'BIG BANK NA'},
    {'document_id': MTGE_ID, 'party_type': '1', 'name': BUYER},
]

# The Master row for a DEED: exactly the keys the live API returns (document_amt
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


def deed_txn_map():
    return {DEED_ID: {'doc_type': 'DEED', 'doc_type_name': 'Deed', 'recorded_date': '2026-09-30'}}


def quiet(fn, *args, **kwargs):
    """Call fn and swallow its progress prints; returns (result, captured stdout)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        return fn(*args, **kwargs), out.getvalue()


class StubbedApi:
    """Replacement for api_request that serves canned rows per ACRIS dataset."""

    def __init__(self, master=(), legals=(), parties=()):
        self.rows = {sap.ACRIS_MASTER_API: list(master),
                     sap.ACRIS_LEGALS_API: list(legals),
                     sap.ACRIS_PARTIES_API: list(parties)}

    def __call__(self, url, params, retries=sap.MAX_RETRIES):
        return self.rows[url]


# ---------------------------------------------------------------------------
# 1. Party roles
# ---------------------------------------------------------------------------

class PartyMapTests(unittest.TestCase):
    def test_slot_map_is_not_backwards(self):
        one, two = sap.PARTY_TYPES['1'], sap.PARTY_TYPES['2']
        self.assertIn('Seller', one)
        self.assertNotIn('Buyer', one)
        self.assertNotIn('Lender', one)
        self.assertIn('Buyer', two)
        self.assertNotIn('Seller', two)
        self.assertNotIn('Borrower', two)


class PartyLabelTests(unittest.TestCase):
    def test_deed_party1_is_seller_party2_is_buyer(self):
        self.assertEqual(sap.party_label('DEED', '1', ROLES), 'Seller/Grantor')
        self.assertEqual(sap.party_label('DEED', '2', ROLES), 'Buyer/Grantee')
        self.assertEqual(sap.party_label('DEEDO', '1', ROLES), 'Seller/Grantor')
        self.assertEqual(sap.party_label('DEEDO', '2', ROLES), 'Buyer/Grantee')

    def test_mortgage_party1_is_borrower_party2_is_lender(self):
        self.assertEqual(sap.party_label('MTGE', '1', ROLES), 'Borrower/Mortgagor')
        self.assertEqual(sap.party_label('MTGE', '2', ROLES), 'Lender/Mortgagee')

    def test_assignment_is_assignor_assignee(self):
        self.assertEqual(sap.party_label('ASST', '1', ROLES), 'Assignor')
        self.assertEqual(sap.party_label('ASST', '2', ROLES), 'Assignee')

    def test_cert_and_unknown_doc_type_stay_neutral(self):
        # CERT parties are literally "PARTY 1"/"PARTY 2"; never call them buyer/seller.
        self.assertEqual(sap.party_label('CERT', '1', ROLES), 'Party 1')
        self.assertEqual(sap.party_label('CERT', '2', ROLES), 'Party 2')
        # --no-details: no doc type at all.
        self.assertEqual(sap.party_label('', '1', ROLES), 'Party 1')
        self.assertEqual(sap.party_label('', '2', ROLES), 'Party 2')
        self.assertEqual(sap.party_label('', '3', ROLES), sap.PARTY_TYPES['3'])

    def test_offline_fallback_is_still_the_right_way_round(self):
        # Control-codes lookup unreachable -> load_party_roles() == {}
        self.assertEqual(sap.party_label('DEED', '1', {}), 'Seller/Grantor')
        self.assertEqual(sap.party_label('DEED', '2', {}), 'Buyer/Grantee')
        self.assertEqual(sap.party_label('MTGE', '2', {}), 'Lender/Mortgagee')


class BuildResultsTests(unittest.TestCase):
    def test_deed_party_rows_are_labelled_by_role(self):
        rows = sap.build_results(DEED_PARTIES, {}, deed_txn_map(), roles=ROLES)
        by_name = {r['party_name']: r['party_type'] for r in rows}
        self.assertEqual(by_name[SELLER], 'Seller/Grantor')
        self.assertEqual(by_name[BUYER], 'Buyer/Grantee')
        self.assertEqual(by_name['KETAN D. PAREKH 2026 IRREVOCABLE TRUST'], 'Buyer/Grantee')

    def test_roles_loaded_when_not_supplied(self):
        with patch.object(sap, 'load_party_roles', return_value=ROLES) as loader:
            rows = sap.build_results(DEED_PARTIES, {}, deed_txn_map())
        loader.assert_called_once()
        self.assertEqual({r['party_type'] for r in rows if r['party_name'] == SELLER}, {'Seller/Grantor'})

    def test_no_details_gives_neutral_slot_labels(self):
        rows = sap.build_results(DEED_PARTIES, {}, {}, roles=ROLES)
        by_name = {r['party_name']: r['party_type'] for r in rows}
        self.assertEqual(by_name[SELLER], 'Party 1')
        self.assertEqual(by_name[BUYER], 'Party 2')


class LinkedClosingsTests(unittest.TestCase):
    """find_linked_closings: CERT filings -> DEED/MTGE on the same lot."""

    def run_linked(self, roles):
        cert_id = 'CERT-1'
        cert_results = [{'document_id': cert_id, 'name': 'ACME TITLE AGENCY', 'party_type': '1'}]
        property_map = {cert_id: {'bbl': '1001230045', 'borough': '1', 'block': '00123', 'lot': '0045'}}
        transaction_map = {cert_id: {'doc_type': 'CERT', 'recorded_date': '2026-09-15'}}
        txns = [
            {'document_id': DEED_ID, 'doc_type': 'DEED', 'recorded_datetime': '2026-09-30T00:00:00.000'},
            {'document_id': MTGE_ID, 'doc_type': 'MTGE', 'recorded_datetime': '2026-09-30T00:00:00.000'},
        ]
        all_parties = DEED_PARTIES + MTGE_PARTIES

        def fake_api(url, params, retries=sap.MAX_RETRIES):
            if url == sap.ACRIS_LEGALS_API:
                return [{'document_id': d['document_id'], 'street_number': '1', 'street_name': 'MAIN ST'}
                        for d in txns]
            if url == sap.ACRIS_MASTER_API:
                return txns
            if url == sap.ACRIS_PARTIES_API:
                return [p for p in all_parties if f"'{p['document_id']}'" in params['$where']]
            raise AssertionError(f'unexpected url {url}')

        with patch.object(sap, 'api_request', side_effect=fake_api), \
                patch.object(sap.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            rows = sap.find_linked_closings(cert_results, property_map, transaction_map, roles=roles)
        return {r['document_id']: r for r in rows}

    def check_roles(self, roles):
        rows = self.run_linked(roles)

        deed = rows[DEED_ID]
        self.assertEqual(deed['seller'], SELLER)          # party 1
        self.assertEqual(deed['buyer'], BUYER)            # party 2 (first one returned)
        self.assertNotEqual(deed['buyer'], SELLER)
        self.assertEqual((deed['party_1'], deed['party_1_role']), (SELLER, 'Seller/Grantor'))
        self.assertEqual((deed['party_2'], deed['party_2_role']), (BUYER, 'Buyer/Grantee'))

        # A mortgage has no buyer/seller: its parties keep their real roles.
        mtge = rows[MTGE_ID]
        self.assertEqual((mtge['buyer'], mtge['seller']), ('', ''))
        self.assertEqual((mtge['party_1'], mtge['party_1_role']), (BUYER, 'Borrower/Mortgagor'))
        self.assertEqual((mtge['party_2'], mtge['party_2_role']), ('BIG BANK NA', 'Lender/Mortgagee'))

        # Every row has the same keys (save_csv takes its header from row 0).
        self.assertEqual(set(deed), set(mtge))

    def test_with_live_style_roles(self):
        self.check_roles(ROLES)

    def test_with_offline_fallback_roles(self):
        self.check_roles({})

    def test_summary_prints_role_labels(self):
        rows = list(self.run_linked(ROLES).values())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sap.print_summary(rows, ['ACME TITLE AGENCY'], linked_mode=True)
        text = out.getvalue()
        self.assertIn(f'Buyer: {BUYER} | Seller: {SELLER}', text)
        self.assertIn('Borrower/Mortgagor: ' + BUYER, text)
        self.assertIn('Lender/Mortgagee: BIG BANK NA', text)


# ---------------------------------------------------------------------------
# 2. Master amount / date columns
# ---------------------------------------------------------------------------

class MasterAmountAndDateTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(sap.time, 'sleep'))
        # build_results / find_linked_closings fetch the control codes when no
        # roles are passed; keep that offline and independent of test order.
        self.enterContext(patch.object(sap, 'load_party_roles', return_value=ROLES))

    def fetch_transactions(self, *rows):
        with patch.object(sap, 'api_request', StubbedApi(master=rows)):
            txns, _ = quiet(sap.get_transaction_details, {r['document_id'] for r in rows})
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
        (record,) = sap.build_results([party], props, txns, doc_types=['DEED'])
        self.assertEqual(record['doc_amount'], '7350000')
        self.assertEqual(record['doc_date'], '2026-09-24')

    def test_build_results_date_filter_falls_back_to_document_date(self):
        # No recorded_datetime, so the filter must use document_date; before the fix
        # doc_date was always '' and this old document slipped past --start-date.
        old = {'document_id': 'OLD', 'doc_type': 'DEED', 'document_date': '2019-05-01T00:00:00.000',
               'document_amt': '1'}
        txns = self.fetch_transactions(old)
        party = {'document_id': 'OLD', 'name': 'X', 'party_type': '1'}
        self.assertEqual(sap.build_results([party], {}, txns, start_date='2026-01-01'), [])
        self.assertEqual(len(sap.build_results([party], {}, txns, start_date='2019-01-01')), 1)

    def test_summary_shows_year_and_amount_instead_of_unknown_and_zero(self):
        txns = self.fetch_transactions(DEED_MASTER_ROW)
        party = {'document_id': DEED_ID, 'name': 'ARIA SUNDARAM 2026 TRUST', 'party_type': '2'}
        props = {DEED_ID: {'bbl': '1000010001', 'borough_name': 'Manhattan',
                           'street_number': '10', 'street_name': 'MAIN ST'}}
        results = sap.build_results([party], props, txns)
        _, out = quiet(sap.print_summary, results, ['ARIA SUNDARAM 2026 TRUST'])
        self.assertIn('2026: 1', out)
        self.assertNotIn('Unknown: 1', out)
        self.assertIn('| DEED | 2026-09-24 | $7350000', out)

    def test_linked_closings_read_master_document_amt_and_document_date(self):
        cert_id = 'CERTDOC1'
        cert_results = [{'document_id': cert_id, 'name': 'TITLE CO LLC'}]
        props = {cert_id: {'bbl': '1000010001', 'borough': '1', 'block': '00001', 'lot': '0001'}}
        txns = {cert_id: {'doc_type': 'CERT', 'recorded_date': '2026-09-28', 'doc_date': ''}}
        api = StubbedApi(master=[DEED_MASTER_ROW], legals=[LEGAL_ROW],
                         parties=[{'document_id': DEED_ID, 'name': 'SELLER LLC', 'party_type': '1'},
                                  {'document_id': DEED_ID, 'name': 'ARIA SUNDARAM 2026 TRUST', 'party_type': '2'}])
        with patch.object(sap, 'api_request', api):
            linked, _ = quiet(sap.find_linked_closings, cert_results, props, txns)
        (closing,) = linked
        self.assertEqual(closing['document_id'], DEED_ID)
        self.assertEqual(closing['doc_amount'], '7350000')
        self.assertEqual(closing['doc_date'], '2026-09-24')
        self.assertEqual(closing['recorded_date'], '2026-09-28')
        # Both fixes together: the closing carries the amount AND the right roles.
        self.assertEqual((closing['buyer'], closing['seller']), ('ARIA SUNDARAM 2026 TRUST', 'SELLER LLC'))

    def test_linked_closings_use_cert_document_date_when_recorded_date_missing(self):
        # find_linked_closings falls back to the CERT's doc_date from the transaction map.
        cert_id = 'CERTDOC1'
        cert_master = {'document_id': cert_id, 'doc_type': 'CERT',
                       'document_date': '2026-09-26T00:00:00.000'}
        txns = self.fetch_transactions(cert_master)
        props = {cert_id: {'bbl': '1000010001', 'borough': '1', 'block': '00001', 'lot': '0001'}}
        api = StubbedApi(master=[DEED_MASTER_ROW], legals=[LEGAL_ROW])
        with patch.object(sap, 'api_request', api):
            linked, _ = quiet(sap.find_linked_closings,
                              [{'document_id': cert_id, 'name': 'TITLE CO LLC'}], props, txns)
        self.assertEqual([c['cert_date'] for c in linked], ['2026-09-26'])


if __name__ == '__main__':
    unittest.main()
