#!/usr/bin/env python3
"""
Offline unit tests for the data-pipeline fixes. No network, no database —
API responses are stubbed, so this runs anywhere:

    python test_pipeline_units.py

Covers:
- B1: party roles resolve correctly per document type (both from live
  control-code rows and from the offline fallback map)
- B3: cash-purchase detection uses the purchase window, not the most
  recent mortgage on record
- B4: lien-sale flags are scoped to the most recent cycle
- E1: document fetches are batched, not one call per document
- References linkage -> open-mortgage / free-and-clear derivation
- SoQL helpers (padding-agnostic block/lot, quoting)
"""

import os
import sys
from datetime import date

os.environ.setdefault('DATABASE_URL', 'postgresql://test:test@localhost/test')

import _pipeline_path  # noqa: F401  (puts dashboard_html on sys.path)
import socrata_client
from socrata_client import (
    SocrataClient, bbl_parts, soql_quote, in_clause, where_block_lot,
    party_role, is_deed, is_ownership_party, is_mortgage, is_satisfaction,
    _bucket_label, normalize_pluto_record,
)

PASS = 0
FAIL = 0


def check(name, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


# ---------------------------------------------------------------------------
print("\n— SoQL helpers —")

boro, block, lot, block_p, lot_p = bbl_parts('3053170021')
check("bbl_parts splits correctly",
      (boro, block, lot, block_p, lot_p) == ('3', '5317', '21', '05317', '0021'))

check("soql_quote escapes quotes", soql_quote("O'HARA") == "'O''HARA'")
check("in_clause quotes each value",
      in_clause('block', ['5317', '05317']) == "block in ('5317','05317')")

clause = where_block_lot('boro', 'block', 'lot', '3053170021')
check("where_block_lot includes both padded and stripped forms",
      "'5317'" in clause and "'05317'" in clause and "'21'" in clause
      and "'0021'" in clause and "boro='3'" in clause)

clause_low = where_block_lot('boro', 'block', 'lot', '1000010001')
check("where_block_lot handles low block/lot numbers",
      "'1'" in clause_low and "'00001'" in clause_low)

print("\n— PLUTO building fact normalization —")

pluto = normalize_pluto_record({
    'bbl': '1016560001.00000000', 'unitstotal': '1268', 'unitsres': '1267',
    'numbldgs': '12', 'numfloors': '20.0000000', 'bldgarea': '846700',
    'lotarea': '282839', 'resarea': '744000', 'comarea': '102700',
    'yearbuilt': '1959', 'yearalter1': '0', 'zonedist1': 'R7-2',
    'overlay1': 'C1-5', 'spdist1': 'TA', 'splitzone': False,
    'builtfar': '2.99', 'residfar': '3.44', 'commfar': '0',
    'facilfar': '6.5', 'irrlotcode': False, 'firm07_flag': '1',
    'ownername': 'NYC HOUSING DEVELOPMENT CORP.', 'ownertype': 'X',
})
check("PLUTO keeps tax-lot units and building count",
      (pluto['total_units'], pluto['residential_units'],
       pluto['non_residential_units'], pluto['number_of_buildings'])
      == (1268, 1267, 1, 12))
check("PLUTO zero alteration years become unknown",
      pluto['year_built'] == 1959 and pluto['year_altered'] is None)
check("PLUTO zoning lists and FAR are normalized",
      pluto['zoning_districts'] == ['R7-2']
      and pluto['commercial_overlays'] == ['C1-5']
      and pluto['special_districts'] == ['TA']
      and pluto['unused_far'] == 0.45)
check("PLUTO booleans preserve false and true flags",
      pluto['split_zone'] is False and pluto['irregular_lot'] is False
      and pluto['fema_2007_flood_zone'] is True)

# ---------------------------------------------------------------------------
print("\n— B1: party roles —")

check("label bucketing: GRANTOR/SELLER -> seller", _bucket_label('GRANTOR/SELLER') == 'seller')
check("label bucketing: GRANTEE/BUYER -> buyer", _bucket_label('GRANTEE/BUYER') == 'buyer')
check("label bucketing: MORTGAGEE/LENDER -> lender", _bucket_label('MORTGAGEE/LENDER') == 'lender')
check("label bucketing: MORTGAGOR/BORROWER -> borrower", _bucket_label('MORTGAGOR/BORROWER') == 'borrower')
check("label bucketing: ASSIGNOR stays a loan-transfer role",
      _bucket_label('ASSIGNOR/OLD LENDER') == 'assignor')
check("label bucketing: ASSIGNEE stays a loan-transfer role",
      _bucket_label('ASSIGNEE/NEW LENDER') == 'assignee')

# Offline fallback path (no control codes available)
socrata_client._doc_roles_cache = {}
check("DEED party 1 is the SELLER (was inverted)", party_role('DEED', '1') == 'seller')
check("DEED party 2 is the BUYER (was inverted)", party_role('DEED', '2') == 'buyer')
check("MTGE party 2 is the LENDER (was the borrower)", party_role('MTGE', '2') == 'lender')
check("MTGE party 1 is the borrower", party_role('MTGE', '1') == 'borrower')
check("M&CON party 2 is the lender", party_role('M&CON', '2') == 'lender')
check("ASST parties are not labeled property buyers/sellers",
      party_role('ASST', '1') == 'assignor'
      and party_role('ASST', '2') == 'assignee')
check("only deed buyers/sellers are ownership parties",
      is_ownership_party('DEED', 'seller')
      and not is_ownership_party('ASST', 'assignor')
      and not is_ownership_party('MTGE', 'lender'))
check("Deed variants inherit deed roles", party_role('DEEDO', '1') == 'seller')
check("Unknown doc types map to other", party_role('ZZZZ', '1') == 'other')

# Live control-code path (simulated rows)
socrata_client._doc_roles_cache = None


class DocCodeStub:
    def get_all(self, dataset, **kw):
        assert dataset == 'acris_doc_codes'
        return [
            {'doc__type': 'DEED', 'party1_type': 'GRANTOR/SELLER', 'party2_type': 'GRANTEE/BUYER'},
            {'doc__type': 'MTGE', 'party1_type': 'MORTGAGOR/BORROWER', 'party2_type': 'MORTGAGEE/LENDER'},
            {'doc__type': 'ASST', 'party1_type': 'ASSIGNOR/OLD LENDER', 'party2_type': 'ASSIGNEE/NEW LENDER'},
        ]


roles = socrata_client.load_party_roles(DocCodeStub())
check("control codes: DEED roles parsed", roles['DEED'] == {'1': 'seller', '2': 'buyer', '3': 'other'})
check("control codes: MTGE roles parsed", roles['MTGE']['2'] == 'lender')
check("party_role uses loaded assignment roles",
      party_role('ASST', '2', roles) == 'assignee')
socrata_client._doc_roles_cache = None

# ---------------------------------------------------------------------------
print("\n— E1: batching —")


class RecordingClient(SocrataClient):
    def __init__(self):
        super().__init__()
        self.calls = []

    def get(self, dataset, **params):
        self.calls.append((dataset, params))
        where = params.get('$where', '')
        # echo one row per requested id
        ids = [tok.strip("'") for tok in
               where.split('(')[-1].rstrip(')').split(',')] if 'in (' in where else []
        return [{'document_id': i} for i in ids]


rc = RecordingClient()
ids = [f"DOC{i:03d}" for i in range(120)]
rows = rc.get_batched('acris_master', 'document_id', ids, batch_size=50)
check("120 ids fetched in 3 calls, not 120", len(rc.calls) == 3, f"got {len(rc.calls)}")
check("batched fetch returns every row", len(rows) == 120, f"got {len(rows)}")

# ---------------------------------------------------------------------------
print("\n— step3: assembly, B3 cash logic, references —")

import step3_enrich_from_acris as step3

check("cash purchase persists as a zero financing ratio",
      step3.purchase_financing_ratio(1_000_000, None) == 0)
check("ordinary purchase financing persists as a ratio",
      step3.purchase_financing_ratio(
          1_000_000, {'doc_amount': 650_000}) == 0.65)
check("nominal-deed ratios cannot poison every retry",
      step3.purchase_financing_ratio(
          1, {'doc_amount': 2_000_000}) is None)


class AcrisStub:
    """Canned ACRIS: one deed (2015, $1M), purchase mortgage (2015 +20d),
    a refi (2022) later satisfied, and remarks on the deed."""

    def get_all(self, dataset, **kw):
        if dataset == 'acris_legals':
            return [{'document_id': d} for d in
                    ('OLD1', 'DEED1', 'MTG1', 'MTG2', 'SAT2')]
        if dataset == 'acris_doc_codes':
            return DocCodeStub().get_all(dataset)
        return []

    def get_batched(self, dataset, field, values, **kw):
        if dataset == 'acris_master':
            rows = [
                {'document_id': 'OLD1', 'doc_type': 'DEED', 'document_amt': '10000',
                 'document_date': '1999-01-01T00:00:00.000',
                 'recorded_datetime': '1999-01-20T00:00:00.000',
                 'crfn': 'OLDCRFN', 'percent_trans': '100'},
                {'document_id': 'DEED1', 'doc_type': 'DEED', 'document_amt': '1000000',
                 'document_date': '2015-06-01T00:00:00.000', 'recorded_datetime': '2015-06-20T00:00:00.000',
                 'crfn': 'CRFN1', 'percent_trans': '100'},
                {'document_id': 'MTG1', 'doc_type': 'MTGE', 'document_amt': '800000',
                 'document_date': '2015-06-21T00:00:00.000', 'recorded_datetime': '2015-07-01T00:00:00.000',
                 'crfn': 'CRFN2', 'percent_trans': ''},
                {'document_id': 'MTG2', 'doc_type': 'MTGE', 'document_amt': '900000',
                 'document_date': '2022-03-10T00:00:00.000', 'recorded_datetime': '2022-03-20T00:00:00.000',
                 'crfn': 'CRFN3', 'percent_trans': ''},
                {'document_id': 'SAT2', 'doc_type': 'SAT', 'document_amt': '0',
                 'document_date': '2024-01-05T00:00:00.000', 'recorded_datetime': '2024-01-15T00:00:00.000',
                 'crfn': 'CRFN4', 'percent_trans': ''},
            ]
            rows = [row for row in rows if row['document_id'] in values]
            if kw.get('extra_where'):
                rows = [row for row in rows
                        if not row.get('recorded_datetime')
                        or row['recorded_datetime'][:10] >= '2000-01-01']
            if kw.get('select') == 'document_id':
                return [{'document_id': row['document_id']} for row in rows]
            return rows
        if dataset == 'acris_parties':
            return [
                {'document_id': 'DEED1', 'party_type': '1', 'name': 'OLD OWNER LLC',
                 'address_1': '1 SELLER WAY', 'city': 'NY', 'state': 'NY', 'zip': '10001'},
                {'document_id': 'DEED1', 'party_type': '2', 'name': 'NEW OWNER LLC',
                 'address_1': '2 BUYER AVE', 'city': 'NY', 'state': 'NY', 'zip': '10002'},
                {'document_id': 'MTG1', 'party_type': '1', 'name': 'NEW OWNER LLC'},
                {'document_id': 'MTG1', 'party_type': '2', 'name': 'BIG BANK NA'},
                {'document_id': 'MTG2', 'party_type': '1', 'name': 'NEW OWNER LLC'},
                {'document_id': 'MTG2', 'party_type': '2', 'name': 'REFI BANK NA'},
            ]
        if dataset == 'acris_references':
            if field == 'document_id':
                return [{'document_id': 'SAT2', 'reference_by_doc_id': 'MTG2', 'crfn': ''}]
            return []
        if dataset == 'acris_remarks':
            return [{'document_id': 'DEED1', 'remark_text': 'LOT 21 ALSO KNOWN AS...'}]
        return []

    def get_columns(self, dataset):
        if dataset == 'acris_references':
            return {'document_id', 'reference_by_doc_id', 'crfn'}
        return set()


socrata_client._doc_roles_cache = None
step3._client = AcrisStub()
history = step3.get_acris_full_history('3053170021')
txns = history['transactions']
by_id = {t['document_id']: t for t in txns}

check("all four retained documents assembled", len(txns) == 4)
check("confirmed pre-2000 documents stay out of the live history",
      'OLD1' not in by_id)
check("deed buyer is the GRANTEE (party 2)",
      by_id['DEED1']['buyers'][0]['name'] == 'NEW OWNER LLC',
      f"got {by_id['DEED1']['buyers']}")
check("deed seller is the GRANTOR (party 1)",
      by_id['DEED1']['sellers'][0]['name'] == 'OLD OWNER LLC')
check("mortgage lender is the MORTGAGEE (party 2)",
      by_id['MTG1']['lenders'][0]['name'] == 'BIG BANK NA',
      f"got {by_id['MTG1']['lenders']}")
check("mortgage borrower captured separately",
      by_id['MTG1']['borrowers'][0]['name'] == 'NEW OWNER LLC')
check("remarks attached to the deed", by_id['DEED1']['remarks'].startswith('LOT 21'))
check("references captured", history['references'] ==
      [{'document_id': 'SAT2', 'referenced_document_id': 'MTG2', 'crfn': ''}])

primary_deed = step3.find_primary_deed(txns)
primary_mortgage = step3.find_primary_mortgage(txns)
check("primary deed is the 2015 sale", primary_deed['document_id'] == 'DEED1')
check("primary mortgage is the most recent (2022 refi)",
      primary_mortgage['document_id'] == 'MTG2')

# B3: the 2022 refi must NOT make the 2015 purchase look like cash
purchase_mtg = step3.find_purchase_mortgage(txns, primary_deed['doc_date'])
check("purchase mortgage found in the window (MTG1, +20 days)",
      purchase_mtg is not None and purchase_mtg['document_id'] == 'MTG1')

only_refi = [t for t in txns if t['document_id'] in ('DEED1', 'MTG2')]
check("with only a 7-years-later refi, purchase IS cash",
      step3.find_purchase_mortgage(only_refi, primary_deed['doc_date']) is None)

check("mortgage 3 days BEFORE the deed still counts as financing",
      step3.find_purchase_mortgage(
          [{'document_id': 'M', 'doc_type': 'MTGE', 'doc_date': date(2015, 5, 29),
            'recorded_date': None}],
          date(2015, 6, 1)) is not None)

status = step3.derive_mortgage_status(txns, history['references'])
check("refi satisfied via reference linkage", 'MTG2' in status['satisfied_ids'])
check("purchase mortgage still open", status['open_mortgage_count'] == 1)
check("not free and clear with an open mortgage", status['is_free_and_clear'] is False)
check("last satisfaction date recorded",
      status['last_satisfaction_date'] == date(2024, 1, 5))

both_satisfied = history['references'] + [
    {'document_id': 'MTG1', 'referenced_document_id': 'SAT2', 'crfn': ''}]
status2 = step3.derive_mortgage_status(txns, both_satisfied)
check("reference direction doesn't matter", 'MTG1' in status2['satisfied_ids'])
check("free and clear once every mortgage is satisfied",
      status2['is_free_and_clear'] is True and status2['has_open_mortgage'] is False)

# Screenshot regression: the 2010 $55k MTGE was satisfied in 2014; the 2019
# M&CON is the apparently-open principal instrument that belongs in summary.
screenshot_txns = [
    {'document_id': 'OLD55', 'doc_type': 'MTGE', 'doc_amount': 55000,
     'doc_date': date(2010, 11, 26), 'recorded_date': date(2010, 12, 29),
     'crfn': '2010000434729'},
    {'document_id': 'SAT55', 'doc_type': 'SAT', 'doc_amount': 0,
     'doc_date': date(2013, 12, 31), 'recorded_date': date(2014, 1, 9),
     'crfn': '2014000011264'},
    {'document_id': 'MCON654', 'doc_type': 'M&CON', 'doc_amount': 654891.10,
     'doc_date': date(2018, 8, 10), 'recorded_date': date(2019, 6, 18),
     'crfn': '2019000190676'},
]
screenshot_refs = [
    {'document_id': 'SAT55', 'referenced_document_id': 'OLD55', 'crfn': ''},
]
screenshot_status = step3.derive_mortgage_status(screenshot_txns, screenshot_refs)
screenshot_primary = step3.find_primary_mortgage(
    screenshot_txns, screenshot_status['open_ids'])
check("satisfied $55k mortgage is not summarized",
      screenshot_primary['document_id'] != 'OLD55')
check("M&CON is recognized as the open principal instrument",
      screenshot_primary['document_id'] == 'MCON654')

chain_txns = screenshot_txns + [
    {'document_id': 'OLDER', 'doc_type': 'MTGE', 'doc_amount': 200000,
     'doc_date': date(2006, 1, 1), 'recorded_date': date(2006, 1, 2),
     'crfn': '2006000000001'},
    {'document_id': 'ASSIGN', 'doc_type': 'ASST', 'doc_amount': 0,
     'doc_date': date(2020, 1, 1), 'recorded_date': date(2020, 1, 2),
     'crfn': '2020000000001'},
]
chain_refs = screenshot_refs + [
    {'document_id': 'MCON654', 'referenced_document_id': 'OLDER', 'crfn': ''},
    {'document_id': 'ASSIGN', 'referenced_document_id': 'OLDER', 'crfn': ''},
    {'document_id': 'ASSIGN', 'referenced_document_id': 'MCON654', 'crfn': ''},
]
chain_status = step3.derive_mortgage_status(chain_txns, chain_refs)
check("linked mortgage generations count as one open debt chain",
      chain_status['open_mortgage_count'] == 1)

closed_chain_txns = chain_txns + [{
    'document_id': 'SATMCON', 'doc_type': 'SAT', 'doc_amount': 0,
    'doc_date': date(2024, 2, 1), 'recorded_date': date(2024, 2, 5),
    'crfn': '2024000000001',
}]
closed_chain_refs = chain_refs + [
    {'document_id': 'SATMCON', 'referenced_document_id': 'MCON654', 'crfn': ''},
]
closed_chain_status = step3.derive_mortgage_status(
    closed_chain_txns, closed_chain_refs)
check("satisfying a terminal consolidation closes its predecessor chain",
      closed_chain_status['open_mortgage_count'] == 0
      and closed_chain_status['is_free_and_clear'] is True)

legacy_unknown = [{
    'document_id': 'FT_OLD', 'doc_type': 'MTGE', 'doc_amount': 1,
    'doc_date': date(1998, 1, 1), 'recorded_date': date(1998, 1, 2), 'crfn': '',
}]
legacy_status = step3.derive_mortgage_status(legacy_unknown, [])
check("reel-era mortgage without reference linkage is status-unknown",
      legacy_status['unknown_ids'] == {'FT_OLD'}
      and legacy_status['open_mortgage_count'] == 0
      and legacy_status['is_free_and_clear'] is False)

# ---------------------------------------------------------------------------
print("\n— B4: lien-sale cycle scoping —")

import step4_enrich_from_tax_liens as step4

check("cycle date parses ISO", step4._parse_cycle_date('2024-12-17T00:00:00.000') == date(2024, 12, 17))
check("cycle date parses 'December 2017'", step4._parse_cycle_date('December 2017') == date(2017, 12, 1))
check("cycle date parses MM/DD/YYYY", step4._parse_cycle_date('12/17/2024') == date(2024, 12, 17))


class LienStub:
    def __init__(self, rows):
        self.rows = rows

    def get(self, dataset, **kw):
        return self.rows

    def get_all(self, dataset, **kw):
        return self.rows


old_notices = [
    {'month': '2017-05-01T00:00:00.000', 'water_debt_only': 'NO'},
    {'month': '2016-05-01T00:00:00.000', 'water_debt_only': 'NO'},
]
step4._client = LienStub(old_notices)
result, err = step4.get_tax_delinquency_data('3053170021')
check("2017-only notices do NOT flag delinquency today",
      err is None and result['has_tax_delinquency'] is False, f"{result} {err}")
check("but the latest cycle date is still recorded",
      result['tax_delinquency_latest_date'] == date(2017, 5, 1))

recent = date.today().strftime('%Y-%m-01T00:00:00.000')
step4._client = LienStub(old_notices + [
    {'month': recent, 'water_debt_only': 'YES'},
    {'month': recent, 'water_debt_only': 'YES'},
])
result, err = step4.get_tax_delinquency_data('3053170021')
check("current-cycle notices DO flag delinquency",
      err is None and result['has_tax_delinquency'] is True)
check("count is scoped to the current cycle only",
      result['tax_delinquency_count'] == 2, f"got {result['tax_delinquency_count']}")
check("water-only computed on the current cycle",
      result['tax_delinquency_water_only'] is True)

step4._client = LienStub([])
result, err = step4.get_tax_delinquency_data('3053170021')
check("no rows means no delinquency", result['has_tax_delinquency'] is False)

print("\n— DOB NOW Safety violation freshness —")
check("ACTIVE Safety violation remains open",
      step4.safety_violation_is_open('Active') is True)
check("WAIVED/PENDING Safety violation remains open",
      step4.safety_violation_is_open('Waived/Pending') is True)
check("cured and dismissed Safety violations are closed",
      not step4.safety_violation_is_open('Cured')
      and not step4.safety_violation_is_open('Dismissed'))


class SafetyStub:
    def __init__(self):
        self.calls = []

    def get_all(self, dataset, **kwargs):
        self.calls.append((dataset, kwargs))
        return [
            {'violation_status': 'Active', 'violation_count': '3'},
            {'violation_status': 'Waived/Pending', 'violation_count': '2'},
            {'violation_status': 'Dismissed', 'violation_count': '7'},
            {'violation_status': 'Cured', 'violation_count': '4'},
        ]


safety_stub = SafetyStub()
step4._client = safety_stub
result, err = step4.get_dob_safety_violations_data('3053170021')
check("Safety aggregate counts every status",
      err is None and result['dob_safety_violation_count'] == 16, f"{result} {err}")
check("Safety aggregate counts only active/pending as open",
      result['dob_safety_open_violations'] == 5, f"got {result}")
check("Safety query uses the new daily dataset and exact numeric BBL",
      safety_stub.calls[0][0] == 'dob_safety_violations'
      and safety_stub.calls[0][1]['$where'] == 'bbl=3053170021')
check("successful Safety refresh receives its own timestamp",
      result['dob_safety_last_checked'] is not None)
bad_result, bad_error = step4.get_dob_safety_violations_data('not-a-bbl')
check("invalid BBL is rejected before a Safety request",
      bad_result is None and 'invalid BBL' in bad_error and len(safety_stub.calls) == 1)

# ---------------------------------------------------------------------------
print("\n— permit ingestion identities and owner updates —")

import permit_scraper_api as permits_api

all_selected = (set(permits_api.NYCOpenDataClient.SELECT_FIELDS)
                | set(permits_api.DOBNowFilingsClient.SELECT_FIELDS))
check("removed invalid NYC Open Data fields",
      not {'owner_s_phone__', 'owner_s_street_name'} & all_selected)

filing_rows, skipped = permits_api.prepare_rows_dob_now_filings([{
    'job_filing_number': 'B00000001-I1', 'filing_date': '2026-01-01T00:00:00',
    'owner_first_name': 'JANE', 'owner_last_name': 'DOE',
    'owner_type': 'Individual', 'bbl': '3000010001',
}])
filing = dict(zip(permits_api.FILINGS_COLUMNS, filing_rows[0]))
check("DOB NOW individual owner is retained",
      filing['owner_first_name'] == 'JANE'
      and filing['owner_last_name'] == 'DOE'
      and filing['owner_business_type'] == 'Individual')

approved_rows, skipped = permits_api.prepare_rows_dob_now_approved([
    {'job_filing_number': 'B00000001-I1', 'work_permit': 'B00000001-I1-GC',
     'issued_date': '2026-01-02T00:00:00', 'owner_name': 'Jane Doe'},
    {'job_filing_number': 'B00000001-I1', 'work_permit': 'B00000001-I1-PL',
     'issued_date': '2026-01-03T00:00:00', 'owner_name': 'Jane Doe'},
])
approved_ids = [dict(zip(permits_api.APPROVED_COLUMNS, row))['permit_no']
                for row in approved_rows]
check("multiple work permits under one filing stay distinct",
      approved_ids == ['B00000001-I1-GC', 'B00000001-I1-PL'])
bis_rows, _ = permits_api.prepare_rows_bis([
    {'job__': '100000001', 'permit_si_no': '7001', 'work_type': 'PL'},
    {'job__': '100000001', 'permit_si_no': '7002', 'work_type': 'MH'},
])
check("BIS work permits under one job stay distinct",
      [row[0] for row in bis_rows] == ['7001', '7002'])
assignments = permits_api.PermitDatabase._update_assignments(
    permits_api.APPROVED_COLUMNS)
check("upsert refreshes owner fields, not just dates/status",
      'owner_first_name = EXCLUDED.owner_first_name' in assignments
      and 'owner_street_name = EXCLUDED.owner_street_name' in assignments)

# ---------------------------------------------------------------------------
print("\n— doc-type classifiers —")
check("is_deed matches variants", is_deed('DEED') and is_deed('DEEDO') and not is_deed('MTGE'))
check("is_mortgage exact family",
      is_mortgage('MTGE') and is_mortgage('M&CON') and not is_mortgage('AGMT')
      and not is_mortgage('DEED'))
check("is_satisfaction family", is_satisfaction('SAT') and is_satisfaction('SATS') and not is_satisfaction('ASST'))

# ---------------------------------------------------------------------------
print("\n— SOS bounded-retry semantics —")
from types import SimpleNamespace
import step5_enrich_from_sos as step5

check("SOS transport failures remain retryable",
      step5.sos_result_needs_retry(
          SimpleNamespace(error='Connection error: timed out')))
check("SOS accurate name misses are completed lookups",
      not step5.sos_result_needs_retry(SimpleNamespace(
          error="No entity matching 'ABC LLC' (closest of 1: 'XYZ LLC')")))


class SosSelectionCursor:
    def __init__(self):
        self.query = None
        self.params = None
        self.description = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchall(self):
        return []


class SosSelectionConnection:
    def __init__(self):
        self.selection = SosSelectionCursor()

    def cursor(self):
        return self.selection


sos_conn = SosSelectionConnection()
step5.get_buildings_needing_sos(sos_conn, limit=5000)
selection = sos_conn.selection
check("SOS selection filters corporate owners before the batch limit",
      "concat_ws(' ', sale_buyer_primary" in selection.query
      and selection.params == [step5.CORPORATE_OWNER_SQL_REGEX, 5000])
check("SOS completed no-match rows do not repeat nightly",
      "sos_principal_name IS NULL" not in selection.query)

# ---------------------------------------------------------------------------
print(f"\n{'=' * 50}")
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
