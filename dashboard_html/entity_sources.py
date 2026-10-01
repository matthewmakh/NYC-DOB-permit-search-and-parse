"""Name-driven adapters over NYC public records for entity research.

Every adapter returns (rows, note) where rows are `entity_research.evidence`
dicts, or None when the source cannot answer this kind of name. Each one is
bounded (row limits, document caps) because a common name can match tens of
thousands of ACRIS parties; the note tells the page when a cap was hit.

Socrata queries combine the indexed full-text `$q` with a precise `$where`
on the name column. `$q` makes the scan cheap, `$where` keeps it exact. When
a dataset rejects `$q` the query is retried with `$where` alone.
"""
import logging
import re
import time

from socrata_client import (SocrataClient, SocrataError, soql_quote, in_clause,
                            load_party_roles, party_role, is_ownership_party)
from record_links import acris_document_url
from entity_research import (evidence, party_address_dict, name_variants, fulltext_tokens,
                             person_parts, entity_key, bbl_from_parts, borough_code,
                             BOROUGH_NAMES, classify, clean_name, is_organization)

log = logging.getLogger(__name__)

API_DELAY = float(__import__('os').getenv('API_DELAY', '0.1'))
ACRIS_PARTY_LIMIT = 2000      # party rows per name variant set
ACRIS_DOC_CAP = 600           # documents resolved to lots and instruments
HPD_LIMIT = 500
DOB_LIMIT = 500
ECB_LIMIT = 500
LITIGATION_LIMIT = 300
MAX_EXPANSION_TARGETS = 5

DOB_NOW_PORTAL = 'https://a810-dobnow.nyc.gov/publish/Index.html#!/'
BIS_JOB = 'https://a810-bisweb.nyc.gov/bisweb/JobsQueryByNumberServlet?passjobnumber={job}&passdocnumber=01'
HPD_ONLINE = 'https://hpdonline.nyc.gov/hpdonline/'
DOS_PORTAL = 'https://apps.dos.ny.gov/publicInquiry/'

_client = None


def client():
    global _client
    if _client is None:
        _client = SocrataClient(timeout=45, max_retries=3)
    return _client


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _like_any(field, variants):
    clauses = [f"upper({field}) like {soql_quote('%' + v.replace('%', '') + '%')}" for v in variants]
    return '(' + ' OR '.join(clauses) + ')'


def _eq_person(first_field, last_field, name):
    first, _, last = person_parts(name)
    if not first or not last:
        return None
    return (f"upper({last_field})={soql_quote(last)} AND "
            f"(upper({first_field})={soql_quote(first)} OR upper({first_field}) like {soql_quote(first + ' %')})")


def search(dataset, where, name, limit, order=None, select=None):
    """One bounded query; indexed $q first, plain $where if the dataset refuses it."""
    params = {'$where': where, '$limit': limit}
    if order:
        params['$order'] = order
    if select:
        params['$select'] = select
    tokens = fulltext_tokens(name)
    if tokens:
        try:
            return client().get(dataset, **params, **{'$q': ' '.join(tokens)})
        except SocrataError as exc:
            if 'HTTP 400' not in str(exc):
                raise
            log.info('%s rejected $q; retrying with $where only', dataset)
    return client().get(dataset, **params)


def _truncated(rows, limit):
    return f'Showing the first {limit} matching records; narrow the name for more.' if len(rows) >= limit else None


def _dob_now_address(row):
    return ' '.join(str(row.get(k) or '').strip() for k in ('house_no', 'street_name')).strip() or None


def _bis_address(row):
    return ' '.join(str(row.get(k) or '').strip() for k in ('house__', 'street_name')).strip() or None


def _full(first, last):
    return ' '.join(p for p in (str(first or '').strip(), str(last or '').strip()) if p) or None


# ---------------------------------------------------------------------------
# ACRIS
# ---------------------------------------------------------------------------

def acris_parties(name, kind, hop=0, via=None):
    variants = name_variants(name, kind)
    if not variants:
        return [], 'Name too short to search'
    parties = search('acris_parties', _like_any('name', variants), name, ACRIS_PARTY_LIMIT,
                     order='document_id DESC')
    if not parties:
        return [], None
    note = _truncated(parties, ACRIS_PARTY_LIMIT)
    doc_ids = []
    for p in parties:
        if p.get('document_id') and p['document_id'] not in doc_ids:
            doc_ids.append(p['document_id'])
    if len(doc_ids) > ACRIS_DOC_CAP:
        doc_ids = doc_ids[:ACRIS_DOC_CAP]
        note = f'Resolved the {ACRIS_DOC_CAP} most recent documents of {len(parties)} party records.'
    wanted = set(doc_ids)
    time.sleep(API_DELAY)
    masters = {m['document_id']: m for m in client().get_batched(
        'acris_master', 'document_id', doc_ids,
        select='document_id,doc_type,document_date,document_amt,recorded_datetime,crfn,recorded_borough,percent_trans')}
    time.sleep(API_DELAY)
    legals = {}
    for l in client().get_batched('acris_legals', 'document_id', doc_ids,
                                  select='document_id,borough,block,lot,street_number,street_name,unit,property_type'):
        legals.setdefault(l['document_id'], l)   # first lot wins; multi-lot deeds keep one
    time.sleep(API_DELAY)
    everyone = client().get_batched('acris_parties', 'document_id', doc_ids,
                                    select='document_id,name,party_type,address_1,address_2,city,state,zip')
    by_doc = {}
    for p in everyone:
        by_doc.setdefault(p['document_id'], []).append(p)
    roles = load_party_roles(client())

    rows = []
    for p in parties:
        doc_id = p.get('document_id')
        if doc_id not in wanted:
            continue
        master = masters.get(doc_id, {})
        legal = legals.get(doc_id, {})
        doc_type = (master.get('doc_type') or '').upper()
        role = party_role(doc_type, p.get('party_type'), roles)
        bbl = bbl_from_parts(legal.get('borough'), legal.get('block'), legal.get('lot'))
        address = ' '.join(str(legal.get(k) or '').strip() for k in ('street_number', 'street_name')).strip()
        if legal.get('unit'):
            address = f"{address} Unit {legal['unit']}".strip()
        if bbl:
            address = f"{address}, {BOROUGH_NAMES.get(bbl[0], '')}".strip(', ')
        others = []
        for other in by_doc.get(doc_id, []):
            if entity_key(other.get('name')) == entity_key(p.get('name')):
                continue
            others.append({'name': clean_name(other.get('name')),
                           'role': party_role(doc_type, other.get('party_type'), roles)})
        amount = master.get('document_amt')
        rows.append(evidence(
            'acris', doc_id, p.get('name'), role=role, bbl=bbl, address=address or None,
            party_address=party_address_dict(p.get('address_1'), p.get('address_2'), p.get('city'),
                                             p.get('state'), p.get('zip')),
            record_date=master.get('recorded_datetime') or master.get('document_date'),
            details={'origin': 'nyc_open_data', 'doc_type': doc_type or None,
                     'amount': float(amount) if amount not in (None, '') else None,
                     'crfn': master.get('crfn'), 'percent': master.get('percent_trans'),
                     'ownership': is_ownership_party(doc_type, role), 'parties': others[:12],
                     'property_type': legal.get('property_type')},
            source_url=acris_document_url(doc_id), hop=hop, via=via))
    return rows, note


# ---------------------------------------------------------------------------
# HPD registration contacts
# ---------------------------------------------------------------------------

def hpd_contacts(name, kind, hop=0, via=None):
    where = None
    if kind == 'person':
        where = _eq_person('firstname', 'lastname', name)
    if not where:
        where = _like_any('corporationname', name_variants(name, 'organization' if kind != 'person' else kind))
    contacts = search('hpd_contacts', where, name, HPD_LIMIT, order='registrationid DESC')
    if not contacts:
        return [], None
    note = _truncated(contacts, HPD_LIMIT)
    reg_ids = list(dict.fromkeys(str(c.get('registrationid')) for c in contacts if c.get('registrationid')))
    time.sleep(API_DELAY)
    regs = {str(r.get('registrationid')): r for r in client().get_batched(
        'hpd_registrations', 'registrationid', reg_ids,
        select='registrationid,boroid,block,lot,bin,housenumber,streetname,zip,lastregistrationdate')}
    time.sleep(API_DELAY)
    co = {}
    for c in client().get_batched('hpd_contacts', 'registrationid', reg_ids,
                                  select='registrationid,type,corporationname,firstname,lastname,title'):
        co.setdefault(str(c.get('registrationid')), []).append(c)
    rows = []
    for c in contacts:
        reg = regs.get(str(c.get('registrationid')), {})
        written = (c.get('corporationname') or '').strip() or _full(c.get('firstname'), c.get('lastname'))
        if not written:
            continue
        bbl = bbl_from_parts(reg.get('boroid'), reg.get('block'), reg.get('lot'))
        address = ' '.join(str(reg.get(k) or '').strip() for k in ('housenumber', 'streetname')).strip()
        if bbl:
            address = f"{address}, {BOROUGH_NAMES.get(bbl[0], '')}".strip(', ')
        others = []
        for other in co.get(str(c.get('registrationid')), []):
            other_name = (other.get('corporationname') or '').strip() or _full(other.get('firstname'), other.get('lastname'))
            if not other_name or entity_key(other_name) == entity_key(written):
                continue
            others.append({'name': other_name, 'role': other.get('type') or 'Contact'})
        rows.append(evidence(
            'hpd', c.get('registrationcontactid') or f"{c.get('registrationid')}:{c.get('type')}", written,
            role=c.get('type') or 'Registration contact', bbl=bbl, address=address or None,
            party_address=party_address_dict(
                ' '.join(str(c.get(k) or '').strip() for k in ('businesshousenumber', 'businessstreetname')).strip(),
                c.get('businessapartment'), c.get('businesscity'), c.get('businessstate'), c.get('businesszip')),
            record_date=reg.get('lastregistrationdate'),
            details={'origin': 'nyc_open_data', 'registration_id': c.get('registrationid'), 'title': c.get('title'),
                     'bin': reg.get('bin'), 'parties': others[:12]},
            source_url=HPD_ONLINE, hop=hop, via=via))
    return rows, note


# ---------------------------------------------------------------------------
# DOB permits and filings
# ---------------------------------------------------------------------------

def _dob_rows(dataset, records, name, kind, fields, address_fn, id_field, date_field, url_fn, hop, via, extra):
    rows = []
    for r in records:
        bbl = bbl_from_parts(borough_code(r.get('borough')), r.get('block'), r.get('lot'))
        address = address_fn(r)
        if bbl and address:
            address = f"{address}, {BOROUGH_NAMES.get(bbl[0], '')}"
        names = []
        for spec in fields:
            label, written = spec[0], (spec[1](r) if callable(spec[1]) else r.get(spec[1]))
            written = clean_name(written)
            if written:
                names.append((label, written))
        matched = [(label, w) for label, w in names
                   if any(entity_key(v) in entity_key(w) for v in name_variants(name, kind))]
        for label, written in matched:
            others = [{'name': w, 'role': l} for l, w in names if entity_key(w) != entity_key(written)]
            details = {'origin': 'nyc_open_data', 'parties': others[:8]}
            details.update(extra(r))
            pa = None
            if label.startswith('Owner'):
                pa = party_address_dict(details.pop('owner_street', None), None, details.pop('owner_city', None),
                                        details.pop('owner_state', None), details.pop('owner_zip', None))
            rows.append(evidence(dataset, f"{r.get(id_field)}:{label}", written, role=label, bbl=bbl,
                                 address=address, party_address=pa, record_date=r.get(date_field),
                                 details=details, source_url=url_fn(r), hop=hop, via=via))
    return rows


def _dob_where(name, kind, business_fields, person_fields):
    clauses = [_like_any(f, name_variants(name, kind)) for f in business_fields]
    if kind == 'person':
        for first, last in person_fields:
            eq = _eq_person(first, last, name)
            if eq:
                clauses.append(f'({eq})')
    return '(' + ' OR '.join(clauses) + ')'


def dob_bis(name, kind, hop=0, via=None):
    where = _dob_where(name, kind, ['owner_s_business_name', 'permittee_s_business_name'],
                       [('owner_s_first_name', 'owner_s_last_name'), ('permittee_s_first_name', 'permittee_s_last_name')])
    records = search('dob_permits_bis', where, name, DOB_LIMIT, order='issuance_date DESC')
    fields = [('Owner (business)', 'owner_s_business_name'),
              ('Owner', lambda r: _full(r.get('owner_s_first_name'), r.get('owner_s_last_name'))),
              ('Permittee (business)', 'permittee_s_business_name'),
              ('Permittee', lambda r: _full(r.get('permittee_s_first_name'), r.get('permittee_s_last_name')))]
    rows = _dob_rows('dob_bis', records, name, kind, fields, _bis_address, 'job__', 'issuance_date',
                     lambda r: BIS_JOB.format(job=r.get('job__')) if r.get('job__') else None, hop, via,
                     lambda r: {'job_number': r.get('job__'), 'job_type': r.get('job_type'), 'work_type': r.get('work_type'),
                                'permit_type': r.get('permit_type'), 'status': r.get('permit_status'),
                                'filing_date': r.get('filing_date'), 'bin': r.get('bin__'),
                                'owner_street': ' '.join(str(r.get(k) or '') for k in ('owner_s_house__', 'owner_s_house_street_name')).strip(),
                                'owner_city': r.get('city'), 'owner_state': r.get('state'), 'owner_zip': r.get('owner_s_zip_code')})
    return rows, _truncated(records, DOB_LIMIT)


def dob_now_filings(name, kind, hop=0, via=None):
    where = _dob_where(name, kind, ['owner_s_business_name', 'applicant_business_name',
                                    'filing_representative_business_name'],
                       [('owner_first_name', 'owner_last_name'), ('applicant_first_name', 'applicant_last_name'),
                        ('filing_representative_first_name', 'filing_representative_last_name')])
    records = search('dob_now_filings', where, name, DOB_LIMIT, order='filing_date DESC')
    fields = [('Owner (business)', 'owner_s_business_name'),
              ('Owner', lambda r: _full(r.get('owner_first_name'), r.get('owner_last_name'))),
              ('Applicant (business)', 'applicant_business_name'),
              ('Applicant', lambda r: _full(r.get('applicant_first_name'), r.get('applicant_last_name'))),
              ('Filing representative (business)', 'filing_representative_business_name'),
              ('Filing representative', lambda r: _full(r.get('filing_representative_first_name'),
                                                        r.get('filing_representative_last_name')))]
    rows = _dob_rows('dob_now_filings', records, name, kind, fields, _dob_now_address, 'job_filing_number',
                     'filing_date', lambda r: DOB_NOW_PORTAL, hop, via,
                     lambda r: {'job_number': r.get('job_filing_number'), 'job_type': r.get('job_type'),
                                'status': r.get('filing_status'), 'description': (r.get('job_description') or '')[:240] or None,
                                'cost': _num(r.get('initial_cost')), 'bin': r.get('bin'),
                                'owner_street': r.get('owner_s_street_name'), 'owner_city': r.get('city'),
                                'owner_state': r.get('state'), 'owner_zip': r.get('zip')})
    return rows, _truncated(records, DOB_LIMIT)


def dob_now_permits(name, kind, hop=0, via=None):
    where = _dob_where(name, kind, ['owner_business_name', 'owner_name', 'applicant_business_name',
                                    'filing_representative_business_name'],
                       [('applicant_first_name', 'applicant_last_name'),
                        ('filing_representative_first_name', 'filing_representative_last_name')])
    records = search('dob_now_permits', where, name, DOB_LIMIT, order='issued_date DESC')
    fields = [('Owner (business)', 'owner_business_name'), ('Owner', 'owner_name'),
              ('Applicant (business)', 'applicant_business_name'),
              ('Applicant', lambda r: _full(r.get('applicant_first_name'), r.get('applicant_last_name'))),
              ('Filing representative (business)', 'filing_representative_business_name'),
              ('Filing representative', lambda r: _full(r.get('filing_representative_first_name'),
                                                        r.get('filing_representative_last_name')))]
    rows = _dob_rows('dob_now_permits', records, name, kind, fields, _dob_now_address, 'work_permit',
                     'issued_date', lambda r: DOB_NOW_PORTAL, hop, via,
                     lambda r: {'job_number': r.get('job_filing_number'), 'permit_no': r.get('work_permit'),
                                'work_type': r.get('work_type'), 'status': r.get('permit_status'),
                                'description': (r.get('job_description') or '')[:240] or None, 'bin': r.get('bin'),
                                'owner_street': r.get('owner_street_address'), 'owner_city': r.get('owner_city'),
                                'owner_state': r.get('owner_state'), 'owner_zip': r.get('owner_zip_code')})
    return rows, _truncated(records, DOB_LIMIT)


def _num(value):
    try:
        return float(str(value).replace('$', '').replace(',', '')) if value not in (None, '') else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# ECB violations and HPD litigation
# ---------------------------------------------------------------------------

def ecb_violations(name, kind, hop=0, via=None):
    records = search('ecb_violations', _like_any('respondent_name', name_variants(name, kind)), name,
                     ECB_LIMIT, order='issue_date DESC')
    rows = []
    for r in records:
        bbl = bbl_from_parts(r.get('boro'), r.get('block'), r.get('lot'))
        rows.append(evidence(
            'ecb', r.get('ecb_violation_number') or r.get('isn_dob_bis_extract') or 'unknown', r.get('respondent_name'),
            role='Respondent', bbl=bbl, address=f"BIN {r.get('bin')}" if r.get('bin') else None,
            party_address=party_address_dict(
                ' '.join(str(r.get(k) or '') for k in ('respondent_house_number', 'respondent_street')).strip(),
                None, r.get('respondent_city'), None, r.get('respondent_zip')),
            record_date=r.get('issue_date'),
            details={'origin': 'nyc_open_data', 'status': r.get('ecb_violation_status'), 'hearing_status': r.get('hearing_status'),
                     'violation_type': r.get('violation_type'), 'severity': r.get('severity'),
                     'description': (r.get('section_law_description1') or r.get('violation_description') or '')[:240] or None,
                     'penalty': _num(r.get('penality_imposed')), 'balance_due': _num(r.get('balance_due')), 'bin': r.get('bin')},
            source_url=None, hop=hop, via=via))
    return rows, _truncated(records, ECB_LIMIT)


def hpd_litigation(name, kind, hop=0, via=None):
    records = search('hpd_litigation', _like_any('respondent', name_variants(name, kind)), name,
                     LITIGATION_LIMIT, order='caseopendate DESC')
    rows = []
    for r in records:
        bbl = re.sub(r'\D', '', str(r.get('bbl') or '')) or bbl_from_parts(r.get('boroid'), r.get('block'), r.get('lot'))
        address = ' '.join(str(r.get(k) or '').strip() for k in ('housenumber', 'streetname')).strip()
        if bbl and address:
            address = f"{address}, {BOROUGH_NAMES.get(bbl[0], '')}"
        rows.append(evidence(
            'hpd_litigation', r.get('litigationid') or 'unknown', r.get('respondent'), role='Respondent',
            bbl=bbl or None, address=address or None, record_date=r.get('caseopendate'),
            details={'origin': 'nyc_open_data', 'case_type': r.get('casetype'), 'status': r.get('casestatus'),
                     'open_judgement': r.get('openjudgement'), 'harassment_finding': r.get('findingofharassment'),
                     'penalty': _num(r.get('penalty'))},
            source_url=HPD_ONLINE, hop=hop, via=via))
    return rows, _truncated(records, LITIGATION_LIMIT)


# ---------------------------------------------------------------------------
# NY Secretary of State (entity names only)
# ---------------------------------------------------------------------------

def is_agent_title(title):
    t = str(title or '').upper()
    return 'AGENT' in t or 'PROCESS' in t


def sos_entity(name, hop=0, via=None):
    from ny_sos_lookup import lookup_business
    result = lookup_business(name)
    if result.error and not result.found:
        raise RuntimeError(result.error)
    if not result.found:
        return [], 'No registered entity with this name'
    people = []
    for p in result.people:
        people.append({'name': p.full_name, 'role': p.title or 'Principal', 'is_agent': is_agent_title(p.title),
                       'is_person': classify(p.full_name) == 'person',
                       'address': party_address_dict(p.street, None, p.city, p.state, p.zipcode)})
    principal = next((p for p in people if not p['is_agent']), None)
    row = evidence('sos', result.dos_id or result.entity_name, result.entity_name, role='Registered entity',
                   party_address=(principal or {}).get('address'),
                   record_date=result.formation_date,
                   details={'origin': 'ny_dos', 'dos_id': result.dos_id, 'entity_type': result.entity_type,
                            'status': result.status, 'jurisdiction': result.jurisdiction, 'county': result.county,
                            'match_quality': result.match_quality, 'people': people, 'parties': people},
                   source_url=DOS_PORTAL, hop=hop, via=via)
    note = None if result.match_quality == 'exact' else f'Closest registered name ({result.match_quality} match)'
    return [row], note


# ---------------------------------------------------------------------------
# Step plans
# ---------------------------------------------------------------------------

def research_steps(dossier):
    name, kind = dossier['display_name'], dossier['entity_kind']
    steps = [
        ('acris', lambda: acris_parties(name, kind)),
        ('hpd', lambda: hpd_contacts(name, kind)),
        ('dob_bis', lambda: dob_bis(name, kind)),
        ('dob_now_filings', lambda: dob_now_filings(name, kind)),
        ('dob_now_permits', lambda: dob_now_permits(name, kind)),
        ('ecb', lambda: ecb_violations(name, kind)),
        ('hpd_litigation', lambda: hpd_litigation(name, kind)),
    ]
    if kind == 'organization' or (kind == 'unknown' and is_organization(name)):
        steps.append(('sos', lambda: sos_entity(name)))
    else:
        steps.append(('sos', lambda: None))
    return steps


def skip_reason(key, dossier):
    if key == 'sos':
        return 'NY Department of State searches by entity name only; a person cannot be looked up directly.'
    return 'Not applicable to this name'


def expansion_targets(dossier, own_rows):
    """Connected names worth a second hop, with the record that connects them.

    Organizations: registered principals from NY DOS, plus people recorded as
    owners or officers alongside the company. People: companies they appear
    with on deeds, registrations and permits (an LLC they bought through, a
    corporation they are an officer of).
    """
    kind = dossier['entity_kind']
    want_people = kind in ('organization', 'unknown')
    scores = {}

    def add(name, via_source, reason, weight=1):
        name = clean_name(name)
        if not name or entity_key(name) == dossier['name_key']:
            return
        is_person = classify(name) == 'person'
        if want_people and not is_person:
            return
        if not want_people and is_person:
            return
        key = entity_key(name)
        item = scores.setdefault(key, {'name': name, 'kind': 'person' if is_person else 'organization',
                                       'score': 0, 'reasons': []})
        item['score'] += weight
        if len(item['reasons']) < 3:
            item['reasons'].append(f'{via_source}: {reason}')

    for row in own_rows:
        details = row.get('details') or {}
        source = row['source']
        if source == 'sos':
            for p in details.get('people') or []:
                if not p.get('is_agent'):
                    add(p['name'], 'NY DOS', p.get('role') or 'principal', weight=5)
        elif source in ('hpd', 'db') and row['role'] in ('HeadOfficer', 'Officer', 'IndividualOwner', 'JointOwner',
                                                           'CorporateOwner', 'Agent', 'Registered entity principal',
                                                           'HPD registered owner', 'HPD managing agent'):
            for p in details.get('parties') or []:
                if p.get('role') in ('CorporateOwner', 'HeadOfficer', 'Officer', 'IndividualOwner', 'JointOwner'):
                    add(p['name'], 'HPD registration', f"{p.get('role')} on the same registration", weight=3)
            if details.get('entity_name'):
                add(details['entity_name'], 'NY DOS', 'registered entity', weight=4)
        elif source == 'acris':
            for p in details.get('parties') or []:
                if p.get('role') == row['role'] and details.get('ownership'):
                    add(p['name'], 'ACRIS', f"co-{row['role']} on deed {row.get('record_id') or ''}".strip(), weight=3)
        elif source.startswith('dob'):
            for p in details.get('parties') or []:
                if p.get('role', '').startswith('Owner') and row['role'].startswith('Owner'):
                    add(p['name'], 'DOB', 'owner contact and owner business on the same job', weight=2)
    ranked = sorted(scores.values(), key=lambda t: (-t['score'], t['name']))
    return ranked[:MAX_EXPANSION_TARGETS]


def expand_steps(dossier, targets):
    def run(fn, label):
        def inner():
            rows, notes = [], []
            for t in targets:
                try:
                    got, note = fn(t)
                    rows += got
                    notes.append(f"{t['name']}: {len(got)}" + (f' ({note})' if note else ''))
                except Exception as exc:  # one bad target must not sink the others
                    log.warning('Expansion %s failed for %r: %s', label, t['name'], exc)
                    notes.append(f"{t['name']}: unavailable")
                time.sleep(API_DELAY)
            return rows, '; '.join(notes) or 'No targets'
        return inner

    def via(t):
        return t['name']

    return [
        ('hop_acris', run(lambda t: acris_parties(t['name'], t['kind'], hop=1, via=via(t)), 'acris')),
        ('hop_hpd', run(lambda t: hpd_contacts(t['name'], t['kind'], hop=1, via=via(t)), 'hpd')),
        ('hop_dob', run(lambda t: _dob_all(t['name'], t['kind'], via(t)), 'dob')),
        ('hop_sos', run(lambda t: sos_entity(t['name'], hop=1, via=via(t)) if t['kind'] == 'organization' else ([], 'person'),
                        'sos')),
    ]


def _dob_all(name, kind, via):
    rows, notes = [], []
    for fn in (dob_bis, dob_now_filings, dob_now_permits):
        got, note = fn(name, kind, hop=1, via=via)
        rows += got
        if note:
            notes.append(note)
    return rows, '; '.join(notes) or None
