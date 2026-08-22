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
      ['EXISTS (SELECT 1 FROM permits p'
       ' WHERE p.bbl = b.bbl AND p.permit_type IN (%s,%s))'])
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

print()
print('=' * 50)
print(f'{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
