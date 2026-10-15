"""Entity research: name matching, tiers, source adapters and read models.

No network or database: Socrata and the SOS lookup are replaced with fakes,
and SQL builders are checked against a recording cursor.
"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'dashboard_html'))

import entity_research as er  # noqa: E402
import entity_sources as es  # noqa: E402
import entity_routes as routes  # noqa: E402


class NameTests(unittest.TestCase):
    def test_entity_key_flips_last_first_and_ignores_punctuation(self):
        self.assertEqual(er.entity_key('SMITH, JOHN A'), 'JOHN A SMITH')
        self.assertEqual(er.entity_key('  john   a. smith '), 'JOHN A SMITH')
        self.assertEqual(er.entity_key('ACME REALTY, LLC'), 'ACME REALTY LLC')
        self.assertEqual(er.entity_key('SMITH, JOHN, JR'), 'JOHN SMITH JR')

    def test_care_of_tail_is_dropped(self):
        self.assertEqual(er.clean_name('123 MAIN ST LLC C/O JOHN DOE'), '123 MAIN ST LLC')
        self.assertEqual(er.clean_name('ACME LLC ATTN: ACCOUNTS'), 'ACME LLC')

    def test_classification_and_variants(self):
        self.assertEqual(er.classify('John Smith'), 'person')
        self.assertEqual(er.classify('ABC Realty LLC'), 'organization')
        self.assertEqual(er.name_variants('John A Smith', 'person')[:2], ['JOHN A SMITH', 'JOHN SMITH'])
        self.assertIn('SMITH, JOHN', er.name_variants('John Smith', 'person'))
        self.assertEqual(er.name_variants('ABC Realty LLC', 'organization'), ['ABC REALTY LLC', 'ABC REALTY'])

    def test_fulltext_tokens_skip_legal_suffixes_and_short_words(self):
        self.assertEqual(er.fulltext_tokens('ABC Realty LLC'), ['ABC', 'REALTY'])
        self.assertEqual(er.fulltext_tokens('Smith, John'), ['JOHN', 'SMITH'])
        self.assertEqual(er.fulltext_tokens('The Co of NY'), [])

    def test_matches_name_tiers(self):
        self.assertEqual(er.matches_name('SMITH, JOHN', 'John Smith', 'person'), 'exact')
        self.assertEqual(er.matches_name('JOHN SMITH JR', 'John Smith', 'person'), 'candidate')
        self.assertEqual(er.matches_name('ABC REALTY, L.L.C.', 'ABC Realty LLC', 'organization'), 'exact')
        self.assertEqual(er.matches_name('ABC REALTY HOLDINGS LLC', 'ABC Realty LLC', 'organization'), 'candidate')
        self.assertIsNone(er.matches_name('XYZ CORP', 'ABC Realty LLC', 'organization'))

    def test_address_key_normalizes_street_words_and_needs_a_locality(self):
        self.assertEqual(er.address_key('123 Main Street', 'Brooklyn', 'NY', '11201-1234'),
                         er.address_key('123 MAIN ST', None, None, '11201'))
        self.assertIsNone(er.address_key('123 Main St'))
        self.assertIsNone(er.address_key(None, 'Brooklyn', 'NY', '11201'))

    def test_bbl_helpers(self):
        self.assertEqual(er.bbl_from_parts('3', '1298', '66'), '3012980066')
        self.assertEqual(er.bbl_from_parts(er.borough_code('BROOKLYN'), '01298', '0066'), '3012980066')
        self.assertIsNone(er.bbl_from_parts('MANHATTAN', '1', '1'))
        self.assertIsNone(er.bbl_from_parts('3', None, '66'))

    def test_validate_name_rejects_numbers_and_short_input(self):
        self.assertEqual(er.validate_name('  Acme  Realty LLC '), 'Acme Realty LLC')
        for bad in ('ab', '3012980066', '!!!', 'x' * 200):
            with self.assertRaises(er.ResearchError):
                er.validate_name(bad)

    def test_clean_context_keeps_known_keys_only(self):
        ctx = er.clean_context({'bbl': '3-01298-0066', 'role': 'Deed grantee', 'address': '521 Montgomery St',
                                'evil': 'x', 'zip': '11225'})
        self.assertEqual(ctx, {'bbl': '3012980066', 'role': 'Deed grantee', 'address': '521 Montgomery St', 'zip': '11225'})
        self.assertEqual(er.clean_context({'bbl': 'nope'}), {})
        self.assertEqual(er.clean_context('garbage'), {})


class TierTests(unittest.TestCase):
    def dossier(self, **over):
        d = {'display_name': 'John Smith', 'entity_kind': 'person', 'contexts': [], 'name_key': 'JOHN SMITH'}
        d.update(over)
        return d

    def test_exact_strong_and_candidate(self):
        rows = [
            er.evidence('acris', '1', 'SMITH, JOHN', bbl='3012980066'),                 # lot from click context
            er.evidence('acris', '2', 'SMITH, JOHN', party_address={'street': '5 Elm St', 'zip': '10001'}),
            er.evidence('acris', '3', 'SMITH, JOHN', party_address={'street': '5 Elm Street', 'zip': '10001'}),
            er.evidence('acris', '4', 'SMITH, JOHN', party_address={'street': '9 Oak St', 'zip': '10002'}),
            er.evidence('acris', '5', 'SMITH, JOHN B'),
            er.evidence('hpd', '6', 'SMITH, JOHN', hop=1, via='ACME LLC', bbl='3012980066'),
        ]
        er.tier_rows(rows, self.dossier(contexts=[{'bbl': '3012980066'}]))
        self.assertEqual([r['match_tier'] for r in rows],
                         ['strong', 'strong', 'strong', 'exact', 'candidate', 'candidate'])

    def test_database_lot_corroborates_external_rows(self):
        rows = [er.evidence('db', 'x', 'JOHN SMITH', bbl='1000010001', in_database=True),
                er.evidence('acris', 'y', 'SMITH, JOHN', bbl='1000010001'),
                er.evidence('acris', 'z', 'SMITH, JOHN', bbl='1000010002')]
        er.tier_rows(rows, self.dossier())
        self.assertEqual([r['match_tier'] for r in rows], ['strong', 'strong', 'exact'])

    def test_single_address_is_not_enough(self):
        rows = [er.evidence('acris', '1', 'SMITH, JOHN', party_address={'street': '5 Elm St', 'zip': '10001'})]
        er.tier_rows(rows, self.dossier())
        self.assertEqual(rows[0]['match_tier'], 'exact')

    def test_evidence_key_is_stable_across_name_spellings(self):
        self.assertEqual(er.evidence_key('acris', '1', 'buyer', 'SMITH, JOHN'),
                         er.evidence_key('acris', '1', 'buyer', 'John Smith'))
        self.assertNotEqual(er.evidence_key('acris', '1', 'buyer', 'John Smith'),
                            er.evidence_key('acris', '1', 'buyer', 'John Smith', via='ACME LLC'))

    def test_external_freshness_window(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(er.external_is_fresh({'external_checked_at': now - timedelta(hours=1)}))
        self.assertFalse(er.external_is_fresh({'external_checked_at': now - timedelta(hours=25)}))
        self.assertFalse(er.external_is_fresh({'external_checked_at': None}))


class RecordingCursor:
    """Fakes psycopg2 just enough to check placeholder counts and scripted rows."""
    def __init__(self, columns, results):
        self.columns = columns
        self.results = list(results)
        self.executed = []
        self._pending = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if 'information_schema.columns' in sql:
            self._pending = [{'column_name': c} for c in self.columns.get(params[0], set())]
            return
        if sql.startswith('SET LOCAL'):
            return
        placeholders = sql.count('%s')
        self.executed.append((sql, params))
        assert placeholders == len(params or []), f'{placeholders} placeholders vs {len(params or [])} params'
        self._pending = self.results.pop(0) if self.results else []

    def fetchall(self):
        return self._pending


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, cursor_factory=None):
        return self._cursor

    def rollback(self):
        pass


class InternalLookupTests(unittest.TestCase):
    def test_buildings_query_binds_every_pattern_and_builds_evidence(self):
        columns = {'buildings': {'current_owner_name', 'owner_name_hpd', 'sale_buyer_primary', 'assessed_total_value'},
                   'acris_parties': set(), 'permits': set(), 'contacts': set(), 'contact_evidence': set()}
        building = {'id': 1, 'bbl': '3012980066', 'address': '521 MONTGOMERY ST', 'borough': 3,
                    'current_owner_name': 'ACME REALTY LLC', 'owner_name_hpd': None,
                    'sale_buyer_primary': 'ACME REALTY, L.L.C.', 'assessed_total_value': 1500000}
        cur = RecordingCursor(columns, [[building]])
        dossier = {'display_name': 'Acme Realty LLC', 'entity_kind': 'organization', 'contexts': [], 'name_key': 'ACME REALTY LLC'}
        rows = er.internal_lookup(FakeConn(cur), dossier)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['in_database'] and r['bbl'] == '3012980066' for r in rows))
        self.assertEqual({r['role'] for r in rows}, {'Tax-lot owner (PLUTO)', 'Latest deed grantee'})
        self.assertEqual(rows[0]['details']['assessed_total_value'], 1500000.0)
        self.assertEqual(rows[0]['source_url'], '/property/3012980066')

    def test_permit_query_pairs_first_and_last_name_columns(self):
        columns = {'buildings': set(), 'acris_parties': set(), 'contacts': set(), 'contact_evidence': set(),
                   'permits': {'applicant', 'owner_first_name', 'owner_last_name', 'owner_business_name', 'job_number',
                               'work_type', 'api_source', 'link'}}
        permit = {'id': 7, 'permit_no': 'P1', 'job_type': 'A2', 'issue_date': date(2024, 1, 2), 'address': '1 MAIN ST',
                  'bbl': '1000010001', 'name_0': 'ACME REALTY LLC', 'name_1': 'JOHN SMITH', 'name_2': None,
                  'job_number': 'J1', 'work_type': 'OT', 'api_source': 'dob_now_filings', 'link': None}
        cur = RecordingCursor(columns, [[permit]])
        dossier = {'display_name': 'John Smith', 'entity_kind': 'person', 'contexts': [], 'name_key': 'JOHN SMITH'}
        rows = er.internal_lookup(FakeConn(cur), dossier)
        self.assertEqual(len(rows), 2)
        roles = {r['role']: r for r in rows}
        self.assertIn('Owner', roles)
        self.assertEqual(roles['Owner']['details']['parties'], [{'name': 'ACME REALTY LLC', 'role': 'Owner (business)'}])
        self.assertIn("CONCAT_WS(' ', p.owner_first_name, p.owner_last_name)", cur.executed[0][0])

    def test_escape_like_neutralizes_wildcards(self):
        self.assertEqual(er.escape_like('100% _real_'), '100\\% \\_real\\_')


class FakeSocrata:
    """Scripted Socrata client keyed by dataset; records every call."""
    def __init__(self, data, reject_q=()):
        self.data = data
        self.reject_q = set(reject_q)
        self.calls = []

    def get(self, dataset, **params):
        self.calls.append((dataset, params))
        if '$q' in params and dataset in self.reject_q:
            raise es.SocrataError(f'HTTP 400 for {dataset}: no full text')
        return list(self.data.get(dataset, []))

    def get_batched(self, dataset, field, values, batch_size=50, select=None, extra_where=None):
        self.calls.append((dataset, {'batched': field, 'values': list(values)}))
        return [r for r in self.data.get(dataset, []) if str(r.get(field)) in {str(v) for v in values}]


class SourceAdapterTests(unittest.TestCase):
    def setUp(self):
        self.roles = {'DEED': {'1': 'seller', '2': 'buyer', '3': 'other'},
                      'MTGE': {'1': 'borrower', '2': 'lender', '3': 'other'}}

    def test_search_retries_without_fulltext_when_rejected(self):
        fake = FakeSocrata({'ecb_violations': [{'respondent_name': 'JOHN SMITH'}]}, reject_q={'ecb_violations'})
        with patch.object(es, 'client', return_value=fake):
            rows = es.search('ecb_violations', "upper(respondent_name) like '%JOHN SMITH%'", 'John Smith', 10)
        self.assertEqual(len(rows), 1)
        self.assertIn('$q', fake.calls[0][1])
        self.assertNotIn('$q', fake.calls[1][1])

    def test_where_builders(self):
        self.assertEqual(es._like_any('name', ['JOHN SMITH', "O'BRIEN"]),
                         "(upper(name) like '%JOHN SMITH%' OR upper(name) like '%O''BRIEN%')")
        self.assertEqual(es._eq_person('firstname', 'lastname', 'Smith, John A'),
                         "upper(lastname)='SMITH' AND (upper(firstname)='JOHN' OR upper(firstname) like 'JOHN %')")
        self.assertIsNone(es._eq_person('firstname', 'lastname', 'ACME LLC'))
        where = es._dob_where('John Smith', 'person', ['owner_s_business_name'], [('owner_s_first_name', 'owner_s_last_name')])
        self.assertIn("upper(owner_s_last_name)='SMITH'", where)
        self.assertIn("upper(owner_s_business_name) like '%JOHN SMITH%'", where)

    def test_acris_parties_resolves_lots_roles_and_counterparties(self):
        fake = FakeSocrata({
            'acris_parties': [
                {'document_id': 'D1', 'name': 'SMITH, JOHN', 'party_type': '2', 'address_1': '5 ELM ST', 'city': 'NEW YORK', 'state': 'NY', 'zip': '10001'},
                {'document_id': 'D1', 'name': 'ACME REALTY LLC', 'party_type': '1'},
                {'document_id': 'D2', 'name': 'SMITH, JOHN', 'party_type': '1'},
                {'document_id': 'D2', 'name': 'BIG BANK NA', 'party_type': '2'},
            ],
            'acris_master': [
                {'document_id': 'D1', 'doc_type': 'DEED', 'document_amt': '1250000', 'recorded_datetime': '2024-03-01T00:00:00.000', 'crfn': 'C1'},
                {'document_id': 'D2', 'doc_type': 'MTGE', 'document_amt': '900000', 'recorded_datetime': '2024-03-02T00:00:00.000'},
            ],
            'acris_legals': [
                {'document_id': 'D1', 'borough': '3', 'block': '1298', 'lot': '66', 'street_number': '521', 'street_name': 'MONTGOMERY STREET'},
                {'document_id': 'D2', 'borough': '3', 'block': '1298', 'lot': '66', 'street_number': '521', 'street_name': 'MONTGOMERY STREET'},
            ],
        })
        # The first `acris_parties` call is the name search; the batched one returns everyone on the docs.
        fake.data['acris_parties'] = fake.data['acris_parties']
        with patch.object(es, 'client', return_value=fake), patch.object(es, 'load_party_roles', return_value=self.roles), \
                patch.object(es.time, 'sleep'):
            rows, note = es.acris_parties('John Smith', 'person')
        self.assertIsNone(note)
        by_doc = {r['record_id']: r for r in rows if er.entity_key(r['name_as_written']) == 'JOHN SMITH'}
        self.assertEqual(set(by_doc), {'D1', 'D2'})
        self.assertEqual(by_doc['D1']['role'], 'buyer')
        self.assertTrue(by_doc['D1']['details']['ownership'])
        self.assertEqual(by_doc['D1']['bbl'], '3012980066')
        self.assertEqual(by_doc['D1']['address'], '521 MONTGOMERY STREET, Brooklyn')
        self.assertEqual(by_doc['D1']['details']['amount'], 1250000.0)
        self.assertEqual(by_doc['D1']['details']['parties'], [{'name': 'ACME REALTY LLC', 'role': 'seller'}])
        self.assertEqual(by_doc['D1']['party_address']['zip'], '10001')
        self.assertEqual(by_doc['D1']['record_date'], date(2024, 3, 1))
        self.assertIsNone(by_doc['D1']['source_url'])  # 'D1' is not a real 16-digit document id
        self.assertEqual(by_doc['D2']['role'], 'borrower')
        self.assertFalse(by_doc['D2']['details']['ownership'])

    def test_acris_caps_documents_and_reports_it(self):
        parties = [{'document_id': f'D{i:04d}', 'name': 'SMITH, JOHN', 'party_type': '2'} for i in range(es.ACRIS_DOC_CAP + 50)]
        fake = FakeSocrata({'acris_parties': parties, 'acris_master': [], 'acris_legals': []})
        with patch.object(es, 'client', return_value=fake), patch.object(es, 'load_party_roles', return_value=self.roles), \
                patch.object(es.time, 'sleep'):
            rows, note = es.acris_parties('John Smith', 'person')
        self.assertEqual(len(rows), es.ACRIS_DOC_CAP)
        self.assertIn(str(es.ACRIS_DOC_CAP), note)

    def test_hpd_contacts_join_registrations_and_co_contacts(self):
        fake = FakeSocrata({
            'hpd_contacts': [
                {'registrationcontactid': 'C1', 'registrationid': '900', 'type': 'HeadOfficer', 'firstname': 'JOHN', 'lastname': 'SMITH',
                 'businesshousenumber': '5', 'businessstreetname': 'ELM ST', 'businesscity': 'NEW YORK', 'businessstate': 'NY', 'businesszip': '10001'},
                {'registrationcontactid': 'C2', 'registrationid': '900', 'type': 'CorporateOwner', 'corporationname': 'ACME REALTY LLC'},
            ],
            'hpd_registrations': [{'registrationid': '900', 'boroid': '3', 'block': '1298', 'lot': '66', 'bin': '3034250',
                                   'housenumber': '521', 'streetname': 'MONTGOMERY STREET', 'lastregistrationdate': '2024-05-01T00:00:00.000'}],
        })
        with patch.object(es, 'client', return_value=fake), patch.object(es.time, 'sleep'):
            rows, note = es.hpd_contacts('John Smith', 'person')
        # The first call is the name search, which the fake answers with both contacts; only the
        # person-shaped row is the subject, the corporation is a co-contact.
        subject = [r for r in rows if er.entity_key(r['name_as_written']) == 'JOHN SMITH'][0]
        self.assertEqual(subject['role'], 'HeadOfficer')
        self.assertEqual(subject['bbl'], '3012980066')
        self.assertEqual(subject['address'], '521 MONTGOMERY STREET, Brooklyn')
        self.assertEqual(subject['party_address']['street'], '5 ELM ST')
        self.assertEqual(subject['details']['parties'], [{'name': 'ACME REALTY LLC', 'role': 'CorporateOwner'}])
        self.assertIn("upper(lastname)='SMITH'", fake.calls[0][1]['$where'])

    def test_dob_bis_only_keeps_fields_that_match_and_carries_owner_address(self):
        fake = FakeSocrata({'dob_permits_bis': [{
            'job__': '320000001', 'borough': 'BROOKLYN', 'block': '01298', 'lot': '00066', 'house__': '521', 'street_name': 'MONTGOMERY ST',
            'issuance_date': '2024-02-01T00:00:00.000', 'job_type': 'A2', 'permit_status': 'ISSUED',
            'owner_s_business_name': 'ACME REALTY LLC', 'owner_s_first_name': 'JOHN', 'owner_s_last_name': 'SMITH',
            'owner_s_house__': '5', 'owner_s_house_street_name': 'ELM ST', 'city': 'NEW YORK', 'state': 'NY', 'owner_s_zip_code': '10001',
            'permittee_s_business_name': 'FAST BUILDERS INC', 'permittee_s_first_name': 'MARIA', 'permittee_s_last_name': 'LOPEZ'}]})
        with patch.object(es, 'client', return_value=fake):
            rows, note = es.dob_bis('John Smith', 'person')
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['role'], 'Owner')
        self.assertEqual(row['bbl'], '3012980066')
        self.assertEqual(row['party_address'], {'street': '5 ELM ST', 'unit': None, 'city': 'NEW YORK', 'state': 'NY', 'zip': '10001'})
        self.assertEqual({p['name'] for p in row['details']['parties']}, {'ACME REALTY LLC', 'FAST BUILDERS INC', 'MARIA LOPEZ'})
        self.assertIn('passjobnumber=320000001', row['source_url'])
        self.assertIsNone(note)

    def test_sos_entity_rows_and_skip_for_people(self):
        class Person:
            def __init__(self, name, title, street='', city='', state='', zipcode=''):
                self.full_name, self.title, self.street, self.city, self.state, self.zipcode = name, title, street, city, state, zipcode

        class Result:
            error = ''
            found = True
            dos_id = '6719932'
            entity_name = 'ACME REALTY LLC'
            entity_type = 'LimitedLiabilityCompany'
            status = 'Active'
            jurisdiction = 'New York'
            county = 'Kings'
            match_quality = 'exact'
            formation_date = datetime(2015, 6, 1)
            people = [Person('JOHN SMITH', 'CEO', '5 Elm St', 'New York', 'NY', '10001'),
                      Person('LEGAL AGENTS INC', 'Registered Agent')]
        import ny_sos_lookup
        with patch.object(ny_sos_lookup, 'lookup_business', return_value=Result()):
            rows, note = es.sos_entity('Acme Realty LLC')
        self.assertIsNone(note)
        self.assertEqual(rows[0]['record_id'], '6719932')
        people = rows[0]['details']['people']
        self.assertEqual([p['is_agent'] for p in people], [False, True])
        self.assertTrue(people[0]['is_person'])
        self.assertEqual(rows[0]['party_address']['street'], '5 Elm St')
        steps = dict(es.research_steps({'display_name': 'John Smith', 'entity_kind': 'person'}))
        self.assertIsNone(steps['sos']())
        self.assertIn('entity name only', es.skip_reason('sos', {}))

    def test_expansion_targets_follow_principals_for_companies_and_companies_for_people(self):
        company = {'display_name': 'Acme Realty LLC', 'entity_kind': 'organization', 'name_key': 'ACME REALTY LLC'}
        rows = [{'source': 'sos', 'role': 'Registered entity', 'name_as_written': 'ACME REALTY LLC', 'match_tier': 'exact', 'bbl': None,
                 'details': {'people': [{'name': 'JOHN SMITH', 'role': 'CEO', 'is_agent': False},
                                        {'name': 'LEGAL AGENTS INC', 'role': 'Registered Agent', 'is_agent': True}]}},
                {'source': 'hpd', 'role': 'CorporateOwner', 'name_as_written': 'ACME REALTY LLC', 'match_tier': 'strong', 'bbl': '3012980066',
                 'details': {'parties': [{'name': 'MARIA LOPEZ', 'role': 'HeadOfficer'}, {'name': 'SITE CO', 'role': 'SiteManager'}]}}]
        targets = es.expansion_targets(company, rows)
        self.assertEqual([t['name'] for t in targets], ['JOHN SMITH', 'MARIA LOPEZ'])
        self.assertTrue(all(t['kind'] == 'person' for t in targets))

        person = {'display_name': 'John Smith', 'entity_kind': 'person', 'name_key': 'JOHN SMITH'}
        rows = [{'source': 'acris', 'role': 'buyer', 'name_as_written': 'SMITH, JOHN', 'match_tier': 'exact', 'bbl': '3012980066',
                 'details': {'ownership': True, 'parties': [{'name': 'ACME REALTY LLC', 'role': 'buyer'}, {'name': 'OLD OWNER LLC', 'role': 'seller'}]}},
                {'source': 'dob_bis', 'role': 'Owner', 'name_as_written': 'JOHN SMITH', 'match_tier': 'exact', 'bbl': None,
                 'details': {'parties': [{'name': 'ACME REALTY LLC', 'role': 'Owner (business)'}, {'name': 'FAST BUILDERS INC', 'role': 'Permittee (business)'}]}}]
        targets = es.expansion_targets(person, rows)
        self.assertEqual([t['name'] for t in targets], ['ACME REALTY LLC'])
        self.assertEqual(targets[0]['kind'], 'organization')
        self.assertEqual(len(targets[0]['reasons']), 2)


class ReadModelTests(unittest.TestCase):
    def rows(self):
        return [
            {'source': 'acris', 'record_id': 'D1', 'role': 'buyer', 'name_as_written': 'SMITH, JOHN', 'match_tier': 'strong',
             'bbl': '3012980066', 'address': '521 MONTGOMERY STREET, Brooklyn', 'record_date': date(2024, 3, 1), 'hop': 0,
             'in_database': True, 'via': None, 'details': {'parties': [{'name': 'ACME REALTY LLC', 'role': 'seller'}]}},
            {'source': 'hpd', 'record_id': 'C1', 'role': 'HeadOfficer', 'name_as_written': 'JOHN SMITH', 'match_tier': 'exact',
             'bbl': '3012980066', 'address': '521 MONTGOMERY ST', 'record_date': date(2024, 5, 1), 'hop': 0, 'in_database': False,
             'via': None, 'details': {'parties': [{'name': 'ACME REALTY LLC', 'role': 'CorporateOwner'}]}},
            {'source': 'ecb', 'record_id': 'E1', 'role': 'Respondent', 'name_as_written': 'JOHN SMITH JR', 'match_tier': 'candidate',
             'bbl': '1000010001', 'address': None, 'record_date': None, 'hop': 0, 'in_database': False, 'via': None, 'details': {}},
            {'source': 'acris', 'record_id': 'D9', 'role': 'buyer', 'name_as_written': 'ACME REALTY LLC', 'match_tier': 'candidate',
             'bbl': '4000010001', 'address': '9 OAK ST, Queens', 'record_date': date(2023, 1, 1), 'hop': 1, 'in_database': False,
             'via': 'ACME REALTY LLC', 'details': {'parties': []}},
        ]

    def test_group_properties_merges_rows_per_lot(self):
        props = routes.group_properties(self.rows())
        self.assertEqual([p['bbl'] for p in props], ['3012980066', '1000010001', '4000010001'])
        main = props[0]
        self.assertEqual(main['tier'], 'strong')
        self.assertEqual(main['records'], 2)
        self.assertTrue(main['in_database'])
        self.assertEqual(main['address'], '521 MONTGOMERY STREET, Brooklyn')
        self.assertEqual(main['latest_date'], '2024-05-01')
        self.assertEqual(main['names'], ['SMITH, JOHN', 'JOHN SMITH'])
        self.assertEqual(props[2]['hop'], 1)
        self.assertEqual(props[2]['via'], ['ACME REALTY LLC'])

    def test_connections_aggregate_counterparties_and_skip_self(self):
        dossier = {'name_key': 'JOHN SMITH'}
        conns = routes.connections(dossier, self.rows())
        self.assertEqual(len(conns), 1)
        self.assertEqual(conns[0]['name'], 'ACME REALTY LLC')
        self.assertEqual(conns[0]['records'], 2)
        self.assertEqual(conns[0]['lots'], 1)
        self.assertEqual(conns[0]['kind'], 'organization')
        self.assertIn('name=ACME+REALTY+LLC', conns[0]['research_url'])

    def test_expansions_group_by_via(self):
        groups = routes.expansions(self.rows())
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['via'], 'ACME REALTY LLC')
        self.assertEqual(groups[0]['lots'], 1)
        self.assertEqual(groups[0]['sources'], ['acris'])

    def test_serializers_are_json_safe(self):
        now = datetime.now(timezone.utc)
        d = routes.serialize_dossier({'id': 1, 'display_name': 'X Y', 'entity_kind': 'person', 'name_key': 'X Y',
                                      'contexts': [], 'summary': {}, 'permanent': False, 'saved_at': None,
                                      'expires_at': now, 'external_checked_at': now, 'expanded_at': None,
                                      'created_at': now, 'last_viewed_at': now})
        self.assertTrue(d['external_fresh'])
        self.assertEqual(d['retention_days'], 60)
        self.assertIsInstance(d['expires_at'], str)
        self.assertEqual(routes.serialize_row({'record_date': date(2024, 1, 2)})['record_date'], '2024-01-02')


if __name__ == '__main__':
    unittest.main(verbosity=1)
