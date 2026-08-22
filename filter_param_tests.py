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

print('— normalizer parity with the scraper —')
try:
    import ny_sos_lookup as S
except ImportError:
    print('  (ny_sos_lookup unavailable — httpx not installed; skipping)')
else:
    drifted = [n for n in ['65 SPRING REALTY LLC', '65 Spring Realty, L.L.C.',
                           'ACME HOLDINGS INC.', 'Acme Holdings Incorporated',
                           'BROOKLYN CO.', 'FOO BAR LP', 'X Y Z PLLC', 'TEST USA',
                           'SOME NAME - BROOKLYN, NY 11201', '']
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

print()
print('=' * 50)
print(f'{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
