#!/usr/bin/env python3
"""Offline regression tests for prebuilt-filter signals and diagnostics.

No database or network is used. Run with::

    python3 signal_filter_tests.py
"""

import os
import re
import sys

os.environ.setdefault('DATABASE_URL', 'postgresql://test:test@localhost/test')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'dashboard_html'))

import step6_enrich_signals as signals
from plays import PLAYS, get_play, public_play
from socrata_client import SocrataClient, SocrataError


passed = 0
failed = 0


def check(name, condition, detail=''):
    global passed, failed
    if condition:
        passed += 1
        print(f'  PASS {name}')
    else:
        failed += 1
        print(f'  FAIL {name} {detail}')


class SignalClientStub:
    columns = {
        'hpd_litigation': {'bbl', 'casestatus', 'caseopendate', 'casetype'},
        'exemptions': {'parid', 'year', 'period', 'exmp_code', 'pstatus', 'exname'},
        'dob_co_bis': {
            'bin_number', 'c_o_issue_date', 'issue_type', 'job_number'},
        'dob_co_now': {
            'bin', 'c_of_o_issuance_date', 'c_of_o_filing_type',
            'application_number'},
        'll84_energy': {
            'nyc_borough_block_and_lot', 'energy_star_score',
            'site_eui_kbtu_ft', 'report_year'},
    }

    def get_columns(self, dataset):
        return self.columns[dataset]

    def get_all(self, dataset, **params):
        if dataset == 'hpd_litigation':
            check('litigation queries the live bbl field',
                  params['$where'] == "bbl='1008350041'", params['$where'])
            return [
                {'casestatus': 'OPEN', 'caseopendate': '2025-03-01',
                 'casetype': 'Heat'},
                {'casestatus': 'CLOSED', 'caseopendate': '2024-01-01',
                 'casetype': 'Other'},
            ]
        if dataset == 'exemptions':
            return [
                {'year': '2026', 'period': '3', 'exmp_code': '1015',
                 'pstatus': 'A', 'exname': 'CURRENT OWNER'},
                {'year': '2025', 'period': '3', 'exmp_code': '1019',
                 'pstatus': 'A', 'exname': 'OLD OWNER'},
            ]
        if dataset == 'dob_co_bis':
            return [{'c_o_issue_date': '2026-06-01', 'issue_type': 'Final',
                     'job_number': 'BIS-1'}]
        if dataset == 'dob_co_now':
            return [{'c_of_o_issuance_date': '2026-07-01',
                     'c_of_o_filing_type': 'Temporary',
                     'application_number': 'NOW-1'}]
        return []

    def get(self, dataset, **params):
        if dataset == 'll84_energy':
            check('LL84 queries the current BBL field',
                  params['$where'] ==
                  "nyc_borough_block_and_lot='1008350041'",
                  params['$where'])
            return [{'report_year': '2024', 'energy_star_score': '91',
                     'site_eui_kbtu_ft': '47.2'}]
        return []


print('\n-- live-schema mappings (stubbed rows) --')
old_client = signals.client
signals.client = SignalClientStub()

litigation = signals.fetch_litigation('1008350041', '1015862')
check('open litigation is counted', litigation['litigation_open_count'] == 1)

exemptions = signals.fetch_exemptions('1008350041', '1015862')
check('the official 1015 code detects current senior exemptions',
      exemptions['has_senior_exemption'] is True)
check('a prior-year 1019 code does not create a current DHE flag',
      exemptions['has_disabled_exemption'] is False)

co = signals.fetch_certificates_of_occupancy('1008350041', '1015862')
check('both CO feeds are counted', co['co_count'] == 2)
check('latest CO date uses the live field names',
      str(co['latest_co_date']) == '2026-07-01')
check('latest CO type uses c_of_o_filing_type',
      co['latest_co_type'] == 'Temporary')

ll84 = signals.fetch_ll84('1008350041', '1015862')
check('LL84 values use nyc_borough_block_and_lot',
      ll84 == {'energy_star_score': 91, 'site_eui': 47.2, 'll84_year': 2024})


class EmptySignalClient:
    columns = {
        'dob_co_bis': {'bin_number', 'c_o_issue_date'},
        'dob_co_now': {'bin', 'c_of_o_issuance_date'},
        'fisp_facades': {
            'bin', 'borough', 'block', 'lot', 'current_status', 'filing_date'},
        'll84_energy': {'nyc_borough_block_and_lot', 'report_year'},
        'rolling_sales': {
            'borough', 'block', 'lot', 'sale_date', 'sale_price'},
    }

    def get_columns(self, dataset):
        return self.columns[dataset]

    def get_all(self, _dataset, **_params):
        return []

    def get(self, _dataset, **_params):
        return []


signals.client = EmptySignalClient()
check('an empty CO feed clears old completion values',
      signals.fetch_certificates_of_occupancy('1008350041', '1015862') == {
          'co_count': 0, 'latest_co_date': None, 'latest_co_type': None,
          'latest_co_job_number': None})
check('an empty FISP feed clears old facade values',
      signals.fetch_fisp('1008350041', '1015862') == {
          'fisp_status': None, 'fisp_cycle': None, 'fisp_filing_date': None})
check('an empty LL84 feed clears old energy values',
      signals.fetch_ll84('1008350041', '1015862') == {
          'energy_star_score': None, 'site_eui': None, 'll84_year': None})
check('an empty rolling-sales feed clears old sale values',
      signals.fetch_rolling_sales('1008350041', '1015862') == {
          'rolling_sale_date': None, 'rolling_sale_price': None,
          'rolling_sale_ppsf': None})


print('\n-- partial-source failure semantics --')


def good_fetcher(_bbl, _bin):
    return {'eviction_count': 2}


def bad_fetcher(_bbl, _bin):
    raise RuntimeError('source unavailable')


old_fetchers = signals.FETCHERS
signals.FETCHERS = [('good', good_fetcher), ('bad', bad_fetcher)]
fields, errors = signals.enrich_signals_for_building(
    '1008350041', '1015862', 30000)
check('successful partial fields are retained', fields['eviction_count'] == 2)
check('LL97 estimate is still derived locally',
      fields['ll97_covered_estimated'] is True)
check('source errors remain explicit for retry',
      errors == ['bad: source unavailable'], errors)
signals.FETCHERS = old_fetchers
signals.client = old_client


print('\n-- schema/error hardening --')


class MetadataFailure:
    def get(self, *_args, **_kwargs):
        raise RuntimeError('metadata offline')


metadata_client = SocrataClient()
metadata_client.session = MetadataFailure()
try:
    metadata_client.get_columns('evictions')
    metadata_raised = False
except SocrataError:
    metadata_raised = True
check('metadata outage raises instead of becoming zero columns', metadata_raised)

city_play = next(play for play in PLAYS if play['id'] == 'city-pressure')
city_columns = set(city_play['required_columns'])
check('city-pressure requires the tax-delinquency column',
      'has_tax_delinquency' in city_columns)
check('city-pressure is unavailable when that column is missing',
      get_play(
          'city-pressure', city_columns - {'has_tax_delinquency'}, set()) is None)
check('coverage SQL is never exposed to the browser',
      'coverage_where' not in public_play(city_play)
      and all('permit_count_where' not in public_play(play) for play in PLAYS))

undeclared = []
for play in PLAYS:
    where_building = set(re.findall(r'\bb\.([a-z_][a-z0-9_]*)', play['where']))
    where_permit = set(re.findall(r'\bp\.([a-z_][a-z0-9_]*)', play['where']))
    missing_building = where_building - set(play['required_columns'])
    missing_permit = where_permit - set(play.get('required_permit_columns', []))
    if missing_building or missing_permit:
        undeclared.append((play['id'], missing_building, missing_permit))
check('every play declares every SQL column it references',
      not undeclared, undeclared)


print(f'\n{passed} passed, {failed} failed')
raise SystemExit(1 if failed else 0)
