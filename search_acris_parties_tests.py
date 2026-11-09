"""search_acris_parties: party roles come out the right way round.

No network: the ACRIS API calls and the control-codes lookup are stubbed.

ACRIS only stores a party SLOT number (1/2/3) on each party row; the role it
plays comes from the Document Control Codes (NYC Open Data 7isb-wh4c):
    DEED: 1 = GRANTOR/SELLER,       2 = GRANTEE/BUYER
    MTGE: 1 = MORTGAGOR/BORROWER,   2 = MORTGAGEE/LENDER
    ASST: 1 = ASSIGNOR/OLD LENDER,  2 = ASSIGNEE/NEW LENDER
    CERT: 1 = "PARTY 1",            2 = "PARTY 2"
This module used to treat slot 1 as buyer and slot 2 as seller.

    python -m unittest search_acris_parties_tests
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


def deed_txn_map():
    return {DEED_ID: {'doc_type': 'DEED', 'doc_type_name': 'Deed', 'recorded_date': '2026-09-30'}}


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


if __name__ == '__main__':
    unittest.main()
