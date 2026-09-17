"""Record provenance and map destinations; no network/database required."""
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from dashboard_html.record_links import acris_document_url, owner_source_links, permit_source_link
from dashboard_html.streetview import payload, geosearch


class RecordLinksTests(unittest.TestCase):
    def test_ownership_uses_matching_deed_not_other_transactions_or_crfn_as_id(self):
        building = dict(bbl='3012980066', sale_crfn='2026000246222', sos_dos_id='6719932')
        txns = [dict(document_id='2026090100000001', crfn='other', is_primary_deed=True),
                dict(document_id='2026082601030001', crfn='2026000246222')]
        links = owner_source_links(building, txns)
        self.assertEqual(parse_qs(urlsplit(links['acris']['url']).query), {'doc_id': ['2026082601030001']})
        self.assertEqual(links['pluto']['url'], 'https://zola.planning.nyc.gov/l/lot/3/1298/66')
        self.assertIn('6719932', links['sos']['hint'])
        self.assertIn('3012980066', links['rpad']['hint'])
        self.assertIn('allblock=1298', links['ecb']['url'])
        self.assertIn('bblsearch.asp', owner_source_links(building, txns[:1])['acris']['url'])

    def test_missing_ids_and_legacy_acris_documents(self):
        self.assertIsNone(acris_document_url(None))
        self.assertIsNone(acris_document_url('2026000246222'))  # CRFN, not document ID
        self.assertIsNone(acris_document_url('javascript:alert(1)'))
        self.assertIn('FT_1590008598559', acris_document_url('FT_1590008598559'))
        self.assertNotIn('pluto', owner_source_links({'bbl': 'not-a-bbl'}))

    def test_dob_now_wins_over_stale_bis_link(self):
        for source in ('dob_now_approved', 'dob_now_filings', 'dob_now_electrical', 'dob_now_elevator'):
            with self.subTest(source=source):
                link = permit_source_link(dict(api_source=source, job_number='B01344580-P1',
                    link='https://a810-bisweb.nyc.gov/bisweb/JobsQueryByNumberServlet?passjobnumber=B01344580-P1'))
                self.assertIn('a810-dobnow.nyc.gov/publish/Index.html', link['url'])
                self.assertIn('enter B01344580.', link['hint'])
        self.assertIn('dobnow', permit_source_link({'permit_no':'B01344580-P1'})['url'])
        for number in ('EL:M01234567-I1', 'VT:M01234567-I1'):
            self.assertIn('enter M01234567.', permit_source_link({'permit_no':number})['hint'])

    def test_legacy_bis_uses_job_number_and_rejects_untrusted_urls(self):
        link = permit_source_link(dict(job_number='321234567', permit_no='321234567-01-PL'))
        self.assertIn('passjobnumber=321234567', link['url'])
        self.assertEqual(link['label'], 'View BIS job')
        for value in ('javascript:alert(1)', 'https://a810-bisweb.nyc.gov.evil.test/a'):
            self.assertNotEqual(permit_source_link({'link':value})['url'], value)


class MapsTests(unittest.TestCase):
    @patch('dashboard_html.streetview.embed_key', return_value='')
    def test_maps_link_never_forces_streetview(self, _):
        data = payload('521 MONTGOMERY STREET', 40.665471, -73.94731, borough='3', bbl='3012980066')
        self.assertEqual(data['open_url'], data['map_url'])
        self.assertEqual(data['open_kind'], 'map')
        self.assertEqual(parse_qs(urlsplit(data['map_url']).query),
                         {'api':['1'], 'query':['521 MONTGOMERY STREET, Brooklyn, NY']})
        self.assertIn('map_action=pano', data['streetview_url'])
        self.assertNotIn('layer=c', data['streetview_url'])

    @patch('dashboard_html.streetview.embed_key', return_value='')
    def test_missing_or_invalid_coordinates_and_addresses(self, _):
        data = payload('521 MONTGOMERY STREET, Brooklyn, NY 11225', borough='3')
        self.assertIsNone(data['streetview_url'])
        self.assertEqual(parse_qs(urlsplit(data['map_url']).query)['query'],
                         ['521 MONTGOMERY STREET, Brooklyn, NY 11225'])
        data = payload('BBL 3012980066', 40.665471, -73.94731, bbl='3012980066')
        self.assertEqual(parse_qs(urlsplit(data['map_url']).query)['query'], ['40.665471,-73.947310'])
        self.assertIsNone(payload('test', 0, 0)['streetview_url'])
        self.assertIsNone(payload('test', 40.865471, -73.89731, bbl='3012980066')['streetview_url'])

    @patch('dashboard_html.streetview.embed_key', return_value='test-key')
    def test_embedded_view_and_map_fallback(self, _):
        data = payload('521 MONTGOMERY STREET', 40.665471, -73.94731)
        self.assertEqual(data['embed_kind'], 'streetview')
        self.assertEqual(parse_qs(urlsplit(data['embed_url']).query)['radius'], ['100'])
        self.assertIn('/search/', data['open_url'])
        self.assertEqual(payload('521 MONTGOMERY STREET')['embed_kind'], 'map')

    @patch('dashboard_html.streetview._geosearch_request', return_value={})
    def test_geosearch_normalizes_borough(self, request):
        geosearch('521 MONTGOMERY STREET', '3', '3012980066')
        self.assertEqual(request.call_args.args[0]['text'], '521 MONTGOMERY STREET, Brooklyn, NY')


if __name__ == '__main__':
    unittest.main()
