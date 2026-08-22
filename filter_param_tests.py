"""Offline tests for the multi-select filter params.

The properties sidebar sends every category filter as a repeatable value, and
four different endpoints have to resolve a given set to the same properties.
These tests need no database and no network:

    python filter_param_tests.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard_html'))
os.environ.setdefault('DATABASE_URL', 'postgresql://unused:unused@localhost:1/unused')

from werkzeug.datastructures import MultiDict  # noqa: E402

import app as A  # noqa: E402

passed = 0
failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f'  ✅ {name}')
    else:
        failed += 1
        print(f'  ❌ {name}\n       got:  {got!r}\n       want: {want!r}')


def clauses(args):
    where, params = [], []
    A._append_category_filters(args, where, params)
    return where, params


print('— _multi_param input shapes —')
check('repeated query args',
      A._multi_param(MultiDict([('permit_type', 'PL'), ('permit_type', 'EW')]), 'permit_type'),
      ['PL', 'EW'])
check('comma-separated value',
      A._multi_param(MultiDict([('permit_type', 'PL,EW')]), 'permit_type'), ['PL', 'EW'])
check('JSON array body',
      A._multi_param({'permit_type': ['PL', 'EW']}, 'permit_type'), ['PL', 'EW'])
check('single scalar (pre-multi-select client)',
      A._multi_param(MultiDict([('permit_type', 'PL')]), 'permit_type'), ['PL'])
check('absent param', A._multi_param(MultiDict([]), 'permit_type'), [])
check('blanks dropped, duplicates collapsed',
      A._multi_param(MultiDict([('x', 'a,,a, b ')]), 'x'), ['a', 'b'])
check('case normalised for codes',
      A._multi_param(MultiDict([('x', 'c1')]), 'x', upper=True), ['C1'])
check('unknown values rejected by allowed set',
      A._multi_param(MultiDict([('x', 'residential,bogus')]), 'x', allowed={'residential'}),
      ['residential'])

print('— property type —')
where, params = clauses(MultiDict([('property_type', 'residential'), ('property_type', 'mixed')]))
check('two types OR together',
      where, ["(b.building_class ~ '^[ABCDR]' OR b.building_class ~ '^S')"])
check('no bound params needed', params, [])
check('unknown type contributes nothing',
      clauses(MultiDict([('property_type', 'bogus')]))[0], [])

print('— building class —')
where, params = clauses(MultiDict([('building_class', 'c1'), ('building_class', 'D')]))
check('prefix LIKE per value',
      where, ['(b.building_class LIKE %s OR b.building_class LIKE %s)'])
check('uppercased with wildcard', params, ['C1%', 'D%'])

print('— permit type —')
where, params = clauses(MultiDict([('permit_type', 'PL'), ('permit_type', 'EW')]))
check('single EXISTS with IN list',
      where,
      ['EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl'
       ' AND UPPER(btrim(p.permit_type)) IN (%s,%s))'])
check('values bound, not interpolated', params, ['PL', 'EW'])

print('— HPD violations —')
check('has violations',
      clauses(MultiDict([('has_violations', 'true')]))[0], ['b.hpd_open_violations > 0'])
check('no violations',
      clauses(MultiDict([('has_violations', 'false')]))[0],
      ['(b.hpd_open_violations = 0 OR b.hpd_open_violations IS NULL)'])
check('both states means no filter',
      clauses(MultiDict([('has_violations', 'true'), ('has_violations', 'false')]))[0], [])
check('empty value means no filter',
      clauses(MultiDict([('has_violations', '')]))[0], [])

print('— cross-caller agreement —')
combo_repeated = MultiDict([
    ('property_type', 'residential'), ('property_type', 'mixed'),
    ('building_class', 'C'), ('building_class', 'D4'),
    ('permit_type', 'PL'), ('permit_type', 'NB'),
    ('has_violations', 'true'),
])
combo_csv = MultiDict([
    ('property_type', 'residential,mixed'),
    ('building_class', 'C,D4'),
    ('permit_type', 'PL,NB'),
    ('has_violations', 'true'),
])
combo_json = {
    'property_type': ['residential', 'mixed'],
    'building_class': ['C', 'D4'],
    'permit_type': ['PL', 'NB'],
    'has_violations': ['true'],
}
baseline = clauses(combo_repeated)
check('comma-separated matches repeated', clauses(combo_csv), baseline)
check('JSON body matches repeated', clauses(combo_json), baseline)

print('— SQL safety —')
where, params = clauses(combo_repeated)
check('no bare % survives into SQL text (psycopg2 would choke)',
      [c for c in where if '%' in c.replace('%s', '')], [])
check('injection attempt is bound, never inlined',
      clauses(MultiDict([('permit_type', "PL'); DROP TABLE permits; --")]))[1],
      ["PL'); DROP TABLE PERMITS; --"])

PROPERTY_SORTS = {
    'address': 'b.address',
    'value': 'b.assessed_total_value',
    'sale_date': 'b.sale_date',
    'permits': 'pc.permit_count',
}

print('— multi-key sort —')
check('empty selection falls back to the default',
      A._order_by_sql(MultiDict([]), PROPERTY_SORTS, 'sale_date', 'desc'),
      'b.sale_date DESC NULLS LAST')
check('one key',
      A._order_by_sql(MultiDict([('sort_by', 'value')]), PROPERTY_SORTS, 'sale_date', 'desc'),
      'b.assessed_total_value DESC NULLS LAST')
check('keys applied in pick order as tiebreakers',
      A._order_by_sql(MultiDict([('sort_by', 'permits'), ('sort_by', 'value')]),
                      PROPERTY_SORTS, 'sale_date', 'desc'),
      'pc.permit_count DESC NULLS LAST, b.assessed_total_value DESC NULLS LAST')
check('sort_order applies to every key',
      A._order_by_sql(MultiDict([('sort_by', 'address'), ('sort_by', 'value')]),
                      PROPERTY_SORTS, 'sale_date', 'asc'),
      'b.address ASC NULLS LAST, b.assessed_total_value ASC NULLS LAST')
check('stable tiebreaker appended last',
      A._order_by_sql(MultiDict([('sort_by', 'value')]), PROPERTY_SORTS,
                      'sale_date', 'desc', tiebreaker='b.id'),
      'b.assessed_total_value DESC NULLS LAST, b.id')
check('unknown keys dropped, not interpolated',
      A._order_by_sql(MultiDict([('sort_by', 'value'), ('sort_by', 'b.id; DROP TABLE buildings')]),
                      PROPERTY_SORTS, 'sale_date', 'desc'),
      'b.assessed_total_value DESC NULLS LAST')
check('all-unknown falls back to the default',
      A._order_by_sql(MultiDict([('sort_by', 'nope')]), PROPERTY_SORTS, 'sale_date', 'desc'),
      'b.sale_date DESC NULLS LAST')
check('duplicate key collapses',
      A._order_by_sql(MultiDict([('sort_by', 'value'), ('sort_by', 'value')]),
                      PROPERTY_SORTS, 'sale_date', 'desc'),
      'b.assessed_total_value DESC NULLS LAST')
check('comma-separated keys',
      A._order_by_sql(MultiDict([('sort_by', 'permits,value')]),
                      PROPERTY_SORTS, 'sale_date', 'desc'),
      'pc.permit_count DESC NULLS LAST, b.assessed_total_value DESC NULLS LAST')

print('— borough —')
check('repeated borough args',
      A._parse_boroughs_param('', multi_source=MultiDict([('borough', '1'), ('borough', '3')])),
      ['1', '3'])
check('comma-separated boroughs',
      A._parse_boroughs_param('', multi_source=MultiDict([('borough', '1,3,5')])),
      ['1', '3', '5'])
check('invalid borough codes dropped',
      A._parse_boroughs_param('', multi_source=MultiDict([('borough', '1,9,x')])),
      ['1'])
check('JSON array body (bulk enrich)',
      A._parse_boroughs_param(['1', '3']), ['1', '3'])
check('JSON array with a comma-joined entry',
      A._parse_boroughs_param(['1,3', '5']), ['1', '3', '5'])
check('empty JSON array', A._parse_boroughs_param([]), [])

print('— permit predicates —')


def permit(args, **kw):
    return A._permit_predicates(args, **kw)


parts, prm = permit(MultiDict([('work_type', 'sf'), ('work_type', 'SH')]))
check('work type uppercased and IN-listed',
      (parts, prm), (['UPPER(btrim(p.work_type)) IN (%s,%s)'], ['SF', 'SH']))
parts, prm = permit(MultiDict([('job_type', 'A2')]))
check('job type', (parts, prm), (['UPPER(btrim(p.job_type)) IN (%s)'], ['A2']))
parts, prm = permit(MultiDict([('license_type', 'gc')]))
check('licence type',
      (parts, prm),
      (['UPPER(btrim(p.permittee_license_type)) IN (%s)'], ['GC']))
parts, prm = permit(MultiDict([('recent_permit_days', '30')]))
check('recency bound as a parameter', prm, ['30', '30'])
check('recency can be excluded',
      permit(MultiDict([('recent_permit_days', '30')]), include_recency=False)[0], [])
check('junk recency ignored',
      permit(MultiDict([('recent_permit_days', 'abc')]))[0], [])
check('negative recency ignored',
      permit(MultiDict([('recent_permit_days', '-5')]))[0], [])
check('alias is honoured',
      permit(MultiDict([('work_type', 'PL')]), alias='q')[0],
      ['UPPER(btrim(q.work_type)) IN (%s)'])
check('nothing set means no predicate', permit(MultiDict([]))[0], [])

print('— building-only vs full category filters —')
w1, p1 = [], []
A._append_building_only_filters(MultiDict([('property_type', 'residential'),
                                           ('work_type', 'PL')]), w1, p1)
check('building-only ignores permit attributes',
      any('permits' in c for c in w1), False)
w2, p2 = [], []
A._append_category_filters(MultiDict([('property_type', 'residential'),
                                      ('work_type', 'PL')]), w2, p2)
check('category filters add the permits EXISTS',
      any('FROM permits p' in c for c in w2), True)
check('and bind the work type', p2, ['PL'])

print('— SOS entity matching —')
import enrichment_service as E  # noqa: E402

check('normalizer strips suffixes and punctuation',
      E.normalize_entity_name('65 Spring Realty, L.L.C.'), '65 SPRING REALTY')
check('exact match after normalizing',
      E.entity_match_quality('65 SPRING REALTY LLC', ['65 Spring Realty, L.L.C.'])[0],
      'exact')
check('a different company is a mismatch',
      E.entity_match_quality('ELDCD DEVELOPMENT LLC', ['65 SPRING REALTY LLC'])[0],
      'mismatch')
check('longer registered name counts as prefix',
      E.entity_match_quality('65 SPRING REALTY HOLDINGS LLC', ['65 SPRING REALTY LLC'])[0],
      'prefix')
check('nothing to compare against is unknown',
      E.entity_match_quality('ANY LLC', [None, ''])[0], 'unknown')
check('no registered name is unknown',
      E.entity_match_quality(None, ['65 SPRING REALTY LLC'])[0], 'unknown')
check('match reports which owner field it matched',
      E.entity_match_quality('65 SPRING REALTY LLC',
                             ['MICHAEL MAKHARADZE', '65 SPRING REALTY LLC'])[1],
      '65 SPRING REALTY LLC')

print('— agent titles are not owners —')
check('service of process agent', E.is_sos_agent_title('Service of Process Agent'), True)
check('registered agent', E.is_sos_agent_title('REGISTERED AGENT'), True)
check('chief executive officer is an owner',
      E.is_sos_agent_title('Chief Executive Officer'), False)
check('missing title is not an agent', E.is_sos_agent_title(None), False)

print('— care-of tails are mailing instructions, not identity —')
check('C/O tail stripped',
      E.normalize_entity_name('65 SPRING REALTY LLC C/O FOX MGMT'),
      '65 SPRING REALTY')
check('ATTN tail stripped',
      E.normalize_entity_name('65 SPRING REALTY LLC ATTN: IRA FOX'),
      '65 SPRING REALTY')
check('percent-style care-of stripped',
      E.normalize_entity_name('65 SPRING REALTY LLC % FOX MGMT'),
      '65 SPRING REALTY')
check('a C/O-tailed owner name exact-matches the clean registration',
      E.entity_match_quality('65 SPRING REALTY LLC',
                             ['65 SPRING REALTY LLC C/O FOX MGMT'])[0],
      'exact')

print('— human vs organization classification —')
screenshot_entities = [
    'US BANK TRUST NATIONAL ASSOCIATION',
    'RCF 2 ACQUISITION TRUST',
    'LSF9 MASTER PARTICIPATION TRUST',
    'FEDERAL NATIONAL MORTGAGE ASSOCIATION',
    'AMERICAN MORTGAGE EXPRESS CORP.',
]
check('screenshot banks and trusts are organizations',
      [E.classify_party_name(name)['entity_kind'] for name in screenshot_entities],
      ['organization'] * len(screenshot_entities))
check('known bank aliases without legal suffixes are organizations',
      [E.classify_party_name(name)['is_person'] for name in
       ('FANNIE MAE', 'FREDDIE MAC', 'WELLS FARGO', 'JPMORGAN CHASE')],
      [False, False, False, False])
check('normal person formats remain enrichable',
      [E.classify_party_name(name)['is_person'] for name in
       ('MAKHARADZE, SAMANTHA', 'Michael Makharadze', 'CHURCH, CHARLOTTE')],
      [True, True, True])
check('care-of text is removed before human classification',
      E.classify_party_name('JANE DOE C/O ACME LLC')['entity_kind'], 'person')
check('joined parties are not sent as one person',
      E.classify_party_name('JANE DOE; JOHN DOE')['entity_kind'], 'multiple')
check('AND-joined names are not treated as one human',
      E.classify_party_name('JANE DOE AND JOHN DOE')['entity_kind'], 'multiple')
check('pipeline-joined deed names split safely',
      E.split_candidate_names('DOE, JANE; DOE, JOHN'), ['DOE, JANE', 'DOE, JOHN'])
check('organization does not produce an enrichment key',
      E.canonical_name_key('US BANK TRUST NATIONAL ASSOCIATION'), None)
mixed_candidates = [
    {'name': 'US BANK TRUST NATIONAL ASSOCIATION', 'is_person': True,
     'source': 'legacy row'},
    {'name': 'MAKHARADZE, SAMANTHA', 'is_person': True,
     'source': 'ACRIS Latest Deed Grantee'},
]
check('bulk strategy reclassifies and drops incorrectly tagged organizations',
      [o['name'] for o in E.filter_owners_by_strategy(mixed_candidates, 'all')],
      ['MAKHARADZE, SAMANTHA'])
check('recommended strategy picks the remaining human candidate',
      [o['name'] for o in E.filter_owners_by_strategy(mixed_candidates, 'recommended')],
      ['MAKHARADZE, SAMANTHA'])
check('owner provider guard rejects an organization before opening the database',
      E.enrich_owner(1, 'US BANK TRUST NATIONAL ASSOCIATION', '', 1)[0], False)
check('permit-contact provider guard rejects an organization before opening the database',
      E.enrich_permit_contact('4099660080', 1, 1,
                              'AMERICAN MORTGAGE EXPRESS CORP.', 'owner',
                              None, None, None, 1)[0], False)

print('— Apify owner-match safety —')
check('server maps NYC borough code 4 to Queens',
      E.resolve_owner_search_location(
          {'address': '18423 CAMBRIDGE RD', 'borough': '4', 'zip_code': '11432'},
          '18423 CAMBRIDGE RD, 4, NY 11432'),
      ('18423 CAMBRIDGE RD', 'QUEENS', 'NY', '11432'))
check('server also accepts an already textual borough',
      E.resolve_owner_search_location(
          {'address': '18423 CAMBRIDGE RD', 'borough': 'Queens',
           'zip_code': '11432'}),
      ('18423 CAMBRIDGE RD', 'QUEENS', 'NY', '11432'))

class _ApifyAddressCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = False
    def execute(self, _sql, _params):
        self.executed = True
    def fetchall(self):
        return self.rows

sos_location, sos_location_source = E._best_owner_search_location(
    _ApifyAddressCursor([]), 9, {
        'address': '18423 CAMBRIDGE RD', 'borough': '4', 'zip_code': '11432',
        'sale_buyer_primary': None,
        'sos_principal_name': 'JANE Q OWNER',
        'sos_principal_street': '10 Corporate Plaza',
        'sos_principal_city': 'Albany', 'sos_principal_state': 'NY',
        'sos_principal_zip': '12207',
    }, 'JANE OWNER')
check('SOS principal lookup uses the person mailing address', sos_location,
      ('10 Corporate Plaza', 'ALBANY', 'NY', '12207'))
check('SOS principal lookup records its location source', sos_location_source,
      'sos_principal_address')

acris_cursor = _ApifyAddressCursor([{
    'party_name': 'MAKHARADZE, SAMANTHA',
    'address_1': '55 OWNER MAILING RD', 'city': 'GARDEN CITY',
    'state': 'NY', 'zip_code': '11530-1234',
}])
acris_location, acris_location_source = E._best_owner_search_location(
    acris_cursor, 9, {
        'address': '18423 CAMBRIDGE RD', 'borough': '4', 'zip_code': '11432',
        'sale_buyer_primary': 'MAKHARADZE, SAMANTHA',
        'sos_principal_name': None,
    }, 'SAMANTHA MAKHARADZE')
check('ACRIS grantee lookup uses the deed mailing address', acris_location,
      ('55 OWNER MAILING RD', 'GARDEN CITY', 'NY', '11530'))
check('ACRIS grantee lookup records its location source', acris_location_source,
      'acris_grantee_address')
apify_input = E._build_apify_run_input(
    'SAMANTHA', 'MAKHARADZE', street_address='18423 CAMBRIDGE RD',
    city='QUEENS', state='NY', zipcode='11432', max_results=50)
check('Apify name query uses the name field',
      apify_input['name'], ['SAMANTHA MAKHARADZE; QUEENS, NY 11432'])
check('Apify address query uses street_citystatezip, not name',
      apify_input['street_citystatezip'],
      ['18423 CAMBRIDGE RD; QUEENS, NY 11432'])
check('Apify max_results is capped to actor maximum', apify_input['max_results'], 10)

correct_person = {
    'Search Option': 'Name Search', 'Input Given': 'SAMANTHA MAKHARADZE',
    'First Name': 'Samantha', 'Last Name': 'Makharadze',
    'Street Address': '184-23 Cambridge Road', 'Address Locality': 'Jamaica',
    'Address Region': 'NY', 'Postal Code': '11432',
    'Phone-1': '(718) 555-0101', 'Person Link': 'person-correct',
}
wrong_resident = {
    'Search Option': 'Address Search', 'First Name': 'Alex', 'Last Name': 'Tenant',
    'Street Address': '184-23 Cambridge Rd', 'Address Locality': 'Jamaica',
    'Address Region': 'NY', 'Postal Code': '11432',
    'Phone-1': '(718) 555-9999', 'Phone-1 Last Reported': 'August 2026',
    'Person Link': 'person-tenant',
}
best, evidence, error = E._pick_best_apify_item(
    [wrong_resident, correct_person], 'SAMANTHA', 'MAKHARADZE',
    '18423 CAMBRIDGE RD', 'QUEENS', 'NY', '11432')
check('newest-phone address resident cannot beat the named owner',
      best['Person Link'], 'person-correct')
check('ZIP/street evidence produces high confidence', evidence['confidence'], 'high')
check('verified selection has no error', error, None)

unrelated_same_name = {
    'Search Option': 'Name Search', 'First Name': 'Samantha',
    'Last Name': 'Makharadze', 'Street Address': '1 Ocean Ave',
    'Address Locality': 'Miami', 'Address Region': 'FL', 'Postal Code': '33101',
    'Phone-1': '(305) 555-0101', 'Person Link': 'person-florida',
}
check('same name with no property-location evidence is rejected',
      E._pick_best_apify_item(
          [unrelated_same_name], 'SAMANTHA', 'MAKHARADZE',
          '18423 CAMBRIDGE RD', 'QUEENS', 'NY', '11432')[0], None)

moved_owner = {
    **unrelated_same_name,
    'Person Link': 'person-moved',
    'Previous Addresses': [{
        'streetAddress': '184-23 Cambridge Rd', 'addressLocality': 'Jamaica',
        'addressRegion': 'NY', 'postalCode': '11432',
    }],
}
moved_best, moved_evidence, _ = E._pick_best_apify_item(
    [moved_owner], 'SAMANTHA', 'MAKHARADZE',
    '18423 CAMBRIDGE RD', 'QUEENS', 'NY', '11432')
check('historical property address can verify a moved owner',
      moved_best['Person Link'], 'person-moved')
check('historical match is labeled previous',
      moved_evidence['address_kind'], 'previous')

ambiguous = {**correct_person, 'Person Link': 'person-second',
             'Phone-1': '(718) 555-0202'}
check('equally strong distinct identities are rejected as ambiguous',
      E._pick_best_apify_item(
          [correct_person, ambiguous], 'SAMANTHA', 'MAKHARADZE',
          '18423 CAMBRIDGE RD', 'QUEENS', 'NY', '11432')[0], None)
address_ambiguous = {**ambiguous, 'Search Option': 'Address Search'}
check('search mode does not break a true identity tie',
      E._pick_best_apify_item(
          [correct_person, address_ambiguous], 'SAMANTHA', 'MAKHARADZE',
          '18423 CAMBRIDGE RD', 'QUEENS', 'NY', '11432')[0], None)

contact_fixture = {
    'Phone-1': '(718) 555-0101',
    'Phone-2': '1-718-555-0101',  # duplicate country-code spelling
    'Phone-3': '123',             # malformed
    'Email-1': 'Owner@Example.com',
    'Email-2': 'owner@example.com',  # case-insensitive duplicate
    'Email-3': 'not-an-email',
}
phones, emails, _ = E.extract_apify_contact_info(contact_fixture)
check('Apify duplicate and malformed phones are dropped',
      [p['number'] for p in phones], ['(718) 555-0101'])
check('Apify does not claim provider phones are independently validated',
      phones[0]['is_valid'], None)
check('Apify duplicate and malformed emails are dropped',
      [e['email'] for e in emails], ['Owner@Example.com'])

profile_js = open(os.path.join(os.path.dirname(__file__),
                               'dashboard_html/static/js/building_profile.js')).read()
confirm_enrich_source = profile_js.split('async function confirmEnrich', 1)[1].split(
    '// ============================================================================', 1)[0]
check('single-property request no longer sends a client-built address',
      'address: fullAddress' in confirm_enrich_source, False)
check('single-property request no longer defaults unknown boroughs to Brooklyn',
      "borough || 'Brooklyn'" in confirm_enrich_source, False)

enrichable_sql = A._enrichable_owner_sql()
check('SQL prefilter includes deed grantees',
      'b.sale_buyer_primary' in enrichable_sql, True)
check('SQL prefilter rejects mortgage organizations and SOS agents',
      ('MORTGAGE' in enrichable_sql and 'SERVICE OF PROCESS AGENT' in enrichable_sql),
      True)

print('— normalizer parity with the scraper —')
try:
    import ny_sos_lookup as S
except ImportError:
    print('  (ny_sos_lookup unavailable — httpx not installed; skipping)')
else:
    drifted = [n for n in ['65 SPRING REALTY LLC', '65 Spring Realty, L.L.C.',
                           'ACME HOLDINGS INC.', 'Acme Holdings Incorporated',
                           'BROOKLYN CO.', 'FOO BAR LP', 'X Y Z PLLC', 'TEST USA',
                           'SOME NAME - BROOKLYN, NY 11201', '',
                           '65 SPRING REALTY LLC C/O FOX MGMT',
                           '65 SPRING REALTY LLC ATTN: IRA FOX',
                           'ACME LLC % SMITH MGMT CO']
               if E.normalize_entity_name(n) != S.normalize_business_name(n)]
    check('web and scraper normalizers agree', drifted, [])

    print('— the lookup refuses a company it was not asked about —')
    import asyncio

    def row(name, dos, status):
        return {'dos_id': dos, 'entity_name': name, 'entity_status': status,
                'entity_type': 'LimitedLiabilityCompany', 'jurisdiction': 'NY',
                'formation_date': '1/1/2019'}

    def lookup(query, matches):
        async def run():
            c = S.AsyncNYSOSClient()
            c._client = object()

            async def search(_):
                return matches

            async def det(dos_id, name):
                m = next(x for x in matches if x['dos_id'] == dos_id)
                return {'dos_id': dos_id, 'entity_name': m['entity_name'],
                        'entity_type': 'LLC', 'status': m['entity_status'],
                        'jurisdiction': 'NY', 'formation_date': '1/1/2019',
                        'county': 'NY', 'people': [], 'raw_response': {}}

            c._search_business, c._get_business_details = search, det
            return await c.lookup(query)
        return asyncio.run(run())

    r = lookup('65 SPRING REALTY LLC',
               [row('ELDCD DEVELOPMENT LLC', '1', 'Active'),
                row('65 SPRING REALTY LLC', '2', 'Inactive')])
    check('an inactive exact match beats an active stranger',
          (r.found, r.entity_name, r.match_quality),
          (True, '65 SPRING REALTY LLC', 'exact'))

    r = lookup('65 SPRING REALTY LLC', [row('ZZZ CAPITAL LLC', '9', 'Active')])
    check('an unrelated company is refused outright',
          (r.found, r.match_quality), (False, 'none'))

    r = lookup('65 SPRING REALTY LLC',
               [row('65 SPRING REALTY LLC', '2', 'Inactive'),
                row('65 SPRING REALTY LLC', '3', 'Active')])
    check('among equal names the active registration wins',
          (r.dos_id, r.match_quality), ('3', 'exact'))

    check('search string drops the care-of tail (BeginsWith would find nothing)',
          S._clean_business_name_for_search('65 SPRING REALTY LLC C/O FOX MGMT'),
          '65 SPRING REALTY LLC')

    print('— LLC source priority (RPAD extract is frozen at FY2018/19) —')
    import step5_enrich_from_sos as S5
    check('deed buyer first',
          S5.get_best_llc_name({'sale_buyer_primary': 'NEW OWNER LLC',
                                'current_owner_name': 'PLUTO LLC',
                                'owner_name_rpad': 'OLD RPAD LLC'}),
          ('NEW OWNER LLC', 'sale_buyer_primary'))
    check('PLUTO outranks the stale RPAD extract',
          S5.get_best_llc_name({'current_owner_name': 'PLUTO LLC',
                                'owner_name_rpad': 'OLD RPAD LLC'}),
          ('PLUTO LLC', 'current_owner_name'))
    check('owner-class HPD beats the frozen RPAD extract',
          S5.get_best_llc_name({'owner_name_rpad': 'OLD RPAD LLC',
                                'owner_name_hpd': 'HPD LLC'}),
          ('HPD LLC', 'owner_name_hpd'))
    check('individual buyer falls through to the next LLC',
          S5.get_best_llc_name({'sale_buyer_primary': 'JIN PEI XIE',
                                'current_owner_name': 'PLUTO LLC'}),
          ('PLUTO LLC', 'current_owner_name'))

    print('— references dataset linkage (live schema has no doc-id column) —')
    import step3_enrich_from_acris as S3

    class _RefClient:
        def get_columns(self, ds):
            return ['document_id', 'good_through_date', 'record_type',
                    'reference_by_crfn_', 'reference_by_reel_borough',
                    'reference_by_reel_nbr', 'reference_by_reel_page',
                    'reference_by_reel_year']

        def get_batched(self, dataset, field, values, select=None, **kw):
            if dataset == 'acris_master':
                return [
                    {'document_id': 'MTGE1', 'doc_type': 'MTGE',
                     'document_amt': '500000', 'document_date': '2015-01-02T00:00:00',
                     'recorded_datetime': '2015-01-10T00:00:00',
                     'crfn': '2015000012345', 'percent_trans': ''},
                    {'document_id': 'SAT1', 'doc_type': 'SAT', 'document_amt': '0',
                     'document_date': '2020-06-01T00:00:00',
                     'recorded_datetime': '2020-06-10T00:00:00',
                     'crfn': '2020000099999', 'percent_trans': ''},
                    {'document_id': 'FT_165', 'doc_type': 'DEED', 'document_amt': '1',
                     'document_date': None, 'recorded_datetime': None,
                     'crfn': '', 'percent_trans': ''},
                    {'document_id': 'FT_165', 'doc_type': 'DEED', 'document_amt': '1',
                     'document_date': None, 'recorded_datetime': None,
                     'crfn': '', 'percent_trans': ''},
                ]
            if dataset == 'acris_references' and field == 'document_id':
                return [{'document_id': 'SAT1', 'reference_by_crfn_': '2015000012345'}]
            return []

    check('counterpart column resolves to the CRFN field',
          S3._reference_counterpart_field(_RefClient()), 'reference_by_crfn_')

    _o_client, _o_ids, _o_roles = (S3.get_client, S3.get_document_ids_for_bbl,
                                   S3.load_party_roles)
    S3.get_client = lambda: _RefClient()
    S3.get_document_ids_for_bbl = lambda bbl: ['MTGE1', 'SAT1', 'FT_165']
    S3.load_party_roles = lambda client: {}
    try:
        h = S3.get_acris_full_history('1000010001')
    finally:
        S3.get_client, S3.get_document_ids_for_bbl, S3.load_party_roles = (
            _o_client, _o_ids, _o_roles)

    check('reference resolves through CRFN to the mortgage',
          h['references'],
          [{'document_id': 'SAT1', 'referenced_document_id': 'MTGE1',
            'crfn': '2015000012345'}])
    check('duplicate legacy FT rows collapse to one transaction',
          [t['document_id'] for t in h['transactions']].count('FT_165'), 1)
    status = S3.derive_mortgage_status(h['transactions'], h['references'])
    check('satisfaction discharges the mortgage',
          'MTGE1' in status['satisfied_ids'], True)
    check('building reads free-and-clear', status['is_free_and_clear'], True)

try:
    import pglast
except ImportError:
    print('— pglast not installed; skipping parse check —')
else:
    print('— generated SQL parses —')
    for label, args in [('all four filters', combo_repeated),
                        ('violations only', MultiDict([('has_violations', 'false')])),
                        ('classes only', MultiDict([('building_class', 'A,B,C')]))]:
        where, _ = clauses(args)
        sql = 'SELECT 1 FROM buildings b WHERE ' + ' AND '.join(where)
        try:
            pglast.parse_sql(sql.replace('%s', "'x'"))
            check(label, True, True)
        except Exception as exc:  # noqa: BLE001
            check(label, str(exc), 'parses')

    for label, args in [('single sort key', MultiDict([('sort_by', 'value')])),
                        ('three sort keys',
                         MultiDict([('sort_by', 'permits'), ('sort_by', 'value'),
                                    ('sort_by', 'address')])),
                        ('sort with injection attempt',
                         MultiDict([('sort_by', 'value; DROP TABLE buildings')]))]:
        order = A._order_by_sql(args, PROPERTY_SORTS, 'sale_date', 'desc', tiebreaker='b.id')
        sql = ('SELECT 1 FROM buildings b LEFT JOIN (SELECT bbl, 1 AS permit_count'
               ' FROM permits GROUP BY bbl) pc ON b.bbl = pc.bbl ORDER BY ' + order)
        try:
            pglast.parse_sql(sql)
            check(label, True, True)
        except Exception as exc:  # noqa: BLE001
            check(label, str(exc), 'parses')

print('— contractor names survive the template and the router —')
with A.app.test_request_context():
    from flask import render_template
    rendered = render_template('contractor_profile.html',
                               contractor_name='T&S HOME IMPROVEMENT INC')
check('ampersand reaches JS unescaped',
      'const CONTRACTOR_NAME = "T\\u0026S HOME IMPROVEMENT INC"' in rendered, True)
check('no HTML entity inside the JS string',
      'CONTRACTOR_NAME = "T&amp;S' in rendered, False)

adapter = A.app.url_map.bind('localhost')
check('slash in contractor name routes',
      adapter.match('/contractor/D/B/A BUILDERS CORP')[0], 'contractor_profile')
check('api route takes slash names too',
      adapter.match('/api/contractor/A/C MECHANICAL')[0], 'api_contractor_profile')

print('— address resolution candidates —')
import property_lookup as PL  # noqa: E402

check('zip pulled from tail', PL._detect_zip('18423 cambridge rd 11432'), '11432')
check('zip+4 collapses to five', PL._detect_zip('90 BEDFORD ST 10014-4384'), '10014')
check('no zip means none', PL._detect_zip('18423 cambridge rd'), None)
check('queens hyphen guess added',
      PL._house_number_candidates('18423'), ['18423', '184-23'])
check('short numbers stay as typed', PL._house_number_candidates('141'), ['141'])
check('already hyphenated left alone',
      PL._house_number_candidates('184-23'), ['184-23'])

_calls = []


def _fake_geoclient(path, params):
    _calls.append((path, dict(params)))
    if path == 'search' and params['input'] == '184-23 cambridge rd':
        return {'results': [{'status': 'EXACT_MATCH', 'response': {
            'bbl': '4098765432', 'buildingIdentificationNumber': '4123456',
            'houseNumber': '184-23', 'firstStreetNameNormalized': 'CAMBRIDGE ROAD',
            'firstBoroughName': 'QUEENS', 'zipCode': '11432',
            'latitude': 40.71, 'longitude': -73.79,
            'bblTaxBlock': '09876', 'bblTaxLot': '0032',
        }}]}, None
    return {}, None


def _fake_geosearch_miss(text):
    _calls.append(('geosearch', text))
    return None, 'no feature with a PAD BBL'


PL._geoclient_get, _real_get = _fake_geoclient, PL._geoclient_get
PL._geosearch_get, _real_geosearch = _fake_geosearch_miss, PL._geosearch_get
PL.NYC_APP_ID, _real_app_id = 'test-key', PL.NYC_APP_ID
try:
    lookup, reason = PL.resolve_address_to_property('18423 cambridge rd')
    check('borough-less address resolves via search', (lookup or {}).get('bbl'), '4098765432')
    check('borough comes back from the match', (lookup or {}).get('borough'), 'Queens')
    check('canonical address assembled',
          (lookup or {}).get('address'), '184-23 CAMBRIDGE ROAD, QUEENS, NY 11432')
    check('no /address call without borough or zip',
          [c for c in _calls if c[0] == 'address'], [])
    check('as-typed form tried before the hyphen guess',
          [c[1]['input'] for c in _calls if c[0] == 'search'],
          ['18423 cambridge rd', '184-23 cambridge rd'])

    _calls.clear()
    lookup, reason = PL.resolve_address_to_property('18423 cambridge rd 11432')
    check('zip routes to the strict endpoint first',
          _calls[0], ('address', {'houseNumber': '18423',
                                  'street': 'cambridge rd', 'zip': '11432'})),
    check('zip stripped from street', 'cambridge rd' in _calls[0][1]['street'], True)

    _calls.clear()
    lookup, reason = PL.resolve_address_to_property('gibberish with no number')
    check('non-address rejected with guidance', 'street address' in (reason or ''), True)

    lookup, reason = PL.resolve_address_to_property('999 NOWHERE LANE')
    check('no match names the query in the reason', '999 NOWHERE LANE' in (reason or ''), True)
    check('borough-less miss still advises a borough',
          'adding the borough' in (reason or ''), True)

    _calls.clear()
    lookup, reason = PL.resolve_address_to_property('999 NOWHERE LANE, QUEENS')
    check('borough miss names the borough', 'Queens' in (reason or ''), True)
    check('borough miss does not advise adding a borough',
          'adding the borough' in (reason or ''), False)
    check('miss falls through to geosearch',
          any(c[0] == 'geosearch' for c in _calls), True)

    def _geoclient_with_message(path, params):
        if path == 'address':
            return {'address': {'message': 'CAMBRIDGE ROAD NOT FOUND IN QUEENS'}}, None
        return {}, None
    PL._geoclient_get = _geoclient_with_message
    lookup, reason = PL.resolve_address_to_property('18423 CAMBRIDGE RD, QUEENS')
    check('geosupport rejection is quoted to the user',
          'CAMBRIDGE ROAD NOT FOUND IN QUEENS' in (reason or ''), True)

    def _geoclient_denied(path, params):
        return None, 'HTTP 401: invalid subscription key'
    PL._geoclient_get = _geoclient_denied
    PL._geosearch_get = lambda text: (None, 'request failed (blocked)')
    lookup, reason = PL.resolve_address_to_property('18423 CAMBRIDGE RD, QUEENS')
    check('service failure is not called a bad address',
          'could not be reached' in (reason or ''), True)
    check('service failure names the rejection', 'HTTP 401' in (reason or ''), True)

    def _fake_geosearch_hit(text):
        return ({'bbl': '4098220015', 'bin': '4123456', 'latitude': 40.71,
                 'longitude': -73.78, 'address': '184-23 CAMBRIDGE RD, QUEENS, NY 11432',
                 'borough': 'Queens', 'block': '09822', 'lot': '0015'}, None)
    PL._geosearch_get = _fake_geosearch_hit
    lookup, reason = PL.resolve_address_to_property('18423 CAMBRIDGE RD, QUEENS')
    check('geosearch rescues a geoclient outage', (lookup or {}).get('bbl'), '4098220015')
finally:
    PL._geoclient_get = _real_get
    PL._geosearch_get = _real_geosearch
    PL.NYC_APP_ID = _real_app_id

PL.NYC_APP_ID = None
PL._geosearch_get = lambda text: (None, 'request failed (blocked)')
try:
    lookup, reason = PL.resolve_address_to_property('141 WYONA ST, BROOKLYN')
    check('missing api key is said out loud', 'NYC_GEOCLIENT_APP_ID' in (reason or ''), True)
finally:
    PL.NYC_APP_ID = _real_app_id
    PL._geosearch_get = _real_geosearch

PL.NYC_APP_ID = None
PL._geoclient_get = lambda path, params: ({}, None)
PL._geosearch_get = lambda text: (({'bbl': '3012340056', 'bin': None, 'latitude': None,
                                    'longitude': None, 'address': '141 WYONA ST, BROOKLYN, NY',
                                    'borough': 'Brooklyn', 'block': '01234', 'lot': '0056'}, None))
try:
    lookup, reason = PL.resolve_address_to_property('141 WYONA ST, BROOKLYN')
    check('no key still resolves through geosearch', (lookup or {}).get('bbl'), '3012340056')
finally:
    PL.NYC_APP_ID = _real_app_id
    PL._geoclient_get = _real_get
    PL._geosearch_get = _real_geosearch

print('— permit_sync mirrors the nightly scraper —')
import permit_sync as PS  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import permit_scraper_api as SCRAPER  # noqa: E402

check('BIS column order identical', PS.BIS_COLUMNS, SCRAPER.BIS_COLUMNS)
check('select fields identical', PS.SELECT_FIELDS, SCRAPER.NYCOpenDataClient.SELECT_FIELDS)

_fixture = {
    'job__': '440776739', 'job_type': 'A2', 'permit_type': 'PL',
    'permit_subtype': 'OT', 'bldg_type': '1', 'borough': 'QUEENS',
    'block': '09966', 'lot': '00080', 'bin__': '4213565',
    'house__': '184-23', 'street_name': 'CAMBRIDGE ROAD', 'zip_code': '11432',
    'filing_date': '03/02/2024 00:00:00', 'issuance_date': '03/05/2024 00:00:00',
    'expiration_date': '03/01/2025', 'job_start_date': '03/10/2024',
    'permit_status': 'ISSUED', 'filing_status': 'INITIAL',
    'permittee_s_business_name': 'T&S HOME IMPROVEMENT INC',
    'permittee_s_license_type': 'MP', 'permittee_s_license__': '0481522',
    'gis_latitude': '40.716', 'gis_longitude': '-73.783',
    'dobrundate': '03/06/2024 00:00:00', 'permit_si_no': '3899021',
}
ours, ours_skipped = PS.prepare_rows_bis([dict(_fixture)])
theirs, _ = SCRAPER.prepare_rows_bis([dict(_fixture)])
check('fixture row maps identically (minus timestamp)',
      ours[0][:-1], theirs[0][:-1])
check('mapped bbl assembled from parts', ours[0][14], '4099660080')
check('permit_no comes from NYC row identifier', ours[0][0], '3899021')
check('issue date parsed from issuance_date', str(ours[0][2]), '2024-03-05')

check('lot matches across paddings',
      [PS._lot_matches({'lot': v}, '80') for v in ('00080', '80', '080', '81')],
      [True, True, True, False])
check('missing row id falls back to a work-permit composite',
      PS.prepare_rows_bis([{'job__': '440776739', 'job_doc___': '01',
                            'work_type': 'PL', 'permit_sequence__': '02',
                            'borough': 'QUEENS', 'block': '9966', 'lot': '80'}])[0][0][0],
      '440776739_01_PL_02')

print()
print('=' * 50)
print(f'{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
