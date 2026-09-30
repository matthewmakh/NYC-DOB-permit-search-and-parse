"""Source evidence and team-owned review notes for property contact research.

Scraper rows are read-only here. An address is a reported contact address, never
an assertion about a person's residence. Cross-source grouping requires a full
name AND a complete matching reported address; a shared name alone is not an ID.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit

from psycopg2.extras import Json

from crm_service import get_db_connection
from owner_source_dates import owner_source_dates, source_date
from record_links import acris_document_url, owner_source_links
from socrata_client import is_ownership_party


STATUSES = {'not_researched', 'needs_review', 'contact_found', 'do_not_contact'}
MATCH_STATUSES = {'unreviewed', 'confirmed_match', 'possible_match', 'wrong_person'}
SCHEMA = ["""CREATE TABLE IF NOT EXISTS crm_owner_research (
    team_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    bbl TEXT NOT NULL,
    person_id TEXT NOT NULL,
    identity_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_snapshot JSONB NOT NULL,
    name_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'not_researched'
        CHECK (status IN ('not_researched','needs_review','contact_found','do_not_contact')),
    match_status TEXT NOT NULL DEFAULT 'unreviewed'
        CHECK (match_status IN ('unreviewed','confirmed_match','possible_match','wrong_person')),
    result_url TEXT NOT NULL DEFAULT '',
    phones JSONB NOT NULL DEFAULT '[]'::jsonb,
    emails JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (team_id, bbl, person_id)
)""", """CREATE INDEX IF NOT EXISTS idx_crm_owner_research_dnc
    ON crm_owner_research (team_id, bbl, name_key) WHERE status='do_not_contact'"""]


class ResearchError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def init_owner_research_tables():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(86753094)')
            for statement in SCHEMA:
                cur.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _text(value):
    return ' '.join(str(value or '').split())


def name_key(value):
    """Complete-name equality only, accounting for the LAST, FIRST convention."""
    value = _text(value).upper()
    parts = [part.strip() for part in value.split(',')]
    suffixes = {'JR', 'JR.', 'SR', 'SR.', 'II', 'III', 'IV', 'V'}
    if len(parts) in (2, 3) and parts[1] and parts[1] not in suffixes:
        value = ' '.join([parts[1], parts[0]] + parts[2:])
    return re.sub(r'\s+', ' ', value).strip()


def _id(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=True).encode()).hexdigest()[:32]


def _iso(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value or None


def _classify(name):
    from enrichment_service import classify_party_name
    return classify_party_name(name)['is_person']


def _complete_name(name):
    from nameparser import HumanName
    parsed = HumanName(name)
    return all(len(re.sub(r'[^A-Za-z]', '', part)) > 1 for part in (parsed.first, parsed.last))


def _date(value):
    parsed = source_date(value)
    return parsed.isoformat() if parsed else None


def _address_key(address):
    # A city or ZIP is useful for searching, but never proves shared identity.
    fields = [_text(address.get(k)).upper() for k in ('street', 'unit', 'city', 'state', 'zip_code')]
    if not all(fields[i] for i in (0, 2, 3, 4)):
        return None
    return tuple(fields)


def _source(key, label, record_id, dates, url=None):
    return dict(key=key, label=label, record_id=str(record_id or ''),
                reported_date=dates.get('reported_date'), date_label=dates.get('date_label'),
                period=dates.get('period'), url=url)


def source_observations(building, parties=()):
    """Pair every address with its own named source record before grouping."""
    bbl = str(building['bbl'])
    dates = owner_source_dates(building)
    links = owner_source_links(building)
    observations = []

    def add(name, role, key, label, record_id, address=None, date_info=None, kind=None):
        name = _text(name)
        if not name:
            return
        info = date_info or dates[key]
        source = _source(key, label, record_id, info,
                         (acris_document_url(record_id) if key == 'acris' else None)
                         or links.get(key, {}).get('url'))
        identity = _id(bbl, key, str(record_id or ''), role, name_key(name))
        address = address or {}
        locality = {k: _text(address.get(k)) for k in ('city', 'state', 'zip_code')}
        locations = []
        if locality['city'] or locality['zip_code']:
            location_label = ', '.join(filter(None, [locality['city'], locality['state']]))
            if locality['zip_code']:
                location_label = f"{location_label} {locality['zip_code']}".strip()
            locations.append(dict(id=_id(identity, locality), label=location_label,
                                  **locality, source=label, source_key=key,
                                  reported_date=info.get('reported_date'),
                                  kind=kind or 'Source-reported contact location', is_property=False))
        observations.append(dict(id=identity, identity_ids=[identity], name=name, role=role,
                                 is_person=_classify(name), sources=[source], locations=locations,
                                 default_location_id=locations[0]['id'] if locations else 'property',
                                 historical=False, _address_key=_address_key(address)))

    primary_parties = [p for p in parties if p.get('is_primary_deed')
                       and p.get('party_type') == 'buyer'
                       and is_ownership_party(p.get('doc_type'), 'buyer')]
    for p in primary_parties:
        add(p.get('party_name'), 'Deed grantee', 'acris', 'ACRIS deed grantee', p.get('document_id'),
            dict(street=p.get('address_1'), unit=p.get('address_2'), city=p.get('city'),
                 state=p.get('state'), zip_code=p.get('zip_code')),
            dict(dates['acris'], reported_date=_date(p.get('recorded_date'))),
            'Reported deed mailing location')
    if not primary_parties:
        add(building.get('sale_buyer_primary'), 'Deed grantee', 'acris', 'ACRIS deed grantee',
            building.get('sale_crfn') or dates['acris'].get('reported_date') or bbl)

    contacts = building.get('hpd_owner_contacts') or []
    if isinstance(contacts, str):
        try:
            contacts = json.loads(contacts)
        except ValueError:
            contacts = []
    matched_contacts = []
    for contact in contacts if isinstance(contacts, list) else []:
        if (not isinstance(contact, dict)
                or contact.get('role') not in ('CorporateOwner', 'IndividualOwner', 'JointOwner')
                or not building.get('hpd_registration_id') or not contact.get('registration_id')
                or str(contact.get('registration_id') or '') != str(building.get('hpd_registration_id') or '')
                or source_date(contact.get('reported_date')) != source_date(building.get('hpd_last_registration_date'))):
            continue
        matched_contacts.append(contact)
        official_id = contact.get('contact_id')
        # Without a contact ID retain the registration observation, not a global person identity.
        record_id = f"contact:{official_id}" if official_id else f"registration:{contact.get('registration_id')}:{contact.get('reported_date')}"
        add(contact.get('name'), {'CorporateOwner': 'Registered corporate owner',
                                 'IndividualOwner': 'Registered individual owner',
                                 'JointOwner': 'Registered joint owner'}[contact['role']],
            'hpd', 'HPD registered owner', record_id,
            dict(street=contact.get('address'), city=contact.get('city'), state=contact.get('state'),
                 zip_code=contact.get('zip_code')), kind='Registered business contact location')
    if not matched_contacts:
        add(building.get('owner_name_hpd'), 'Registered owner', 'hpd', 'HPD registered owner',
            f"{building.get('hpd_registration_id')}:{dates['hpd'].get('reported_date')}")
    add(building.get('current_owner_name'), 'Tax-lot owner', 'pluto', 'NYC PLUTO',
        f"{bbl}:{building.get('pluto_version') or ''}")
    add(building.get('owner_name_rpad'), 'Historical assessment owner', 'rpad', 'Historical RPAD assessment',
        f"{bbl}:{building.get('rpad_assessment_year') or ''}:{building.get('rpad_assessment_period') or ''}")
    add(building.get('ecb_respondent_name'), 'Violation respondent', 'ecb', 'ECB violation respondent',
        f"{bbl}:{dates['ecb'].get('reported_date') or ''}",
        dict(street=building.get('ecb_respondent_address'), city=building.get('ecb_respondent_city'),
             zip_code=building.get('ecb_respondent_zip')), kind='Reported respondent contact location')
    add(building.get('sos_principal_name'), _text(building.get('sos_principal_title')) or 'Registered entity contact',
        'sos', 'NY Secretary of State', building.get('sos_dos_id') or building.get('sos_entity_name'),
        dict(street=building.get('sos_principal_street'), city=building.get('sos_principal_city'),
             state=building.get('sos_principal_state'), zip_code=building.get('sos_principal_zip')),
        kind='Reported entity contact location')
    # Duplicate rows from one source do not create duplicate review controls.
    unique = {}
    for observation in observations:
        old = unique.get(observation['id'])
        if old:
            old['locations'] += [loc for loc in observation['locations'] if loc not in old['locations']]
            if old['_address_key'] != observation['_address_key']:
                old['_address_key'] = None
        else:
            unique[observation['id']] = observation
    return list(unique.values())


def empty_research():
    return dict(version=0, status='not_researched', match_status='unreviewed', result_url='',
                phones=[], emails=[], notes='', reviewed_at=None, reviewed_by=None)


def _review(row):
    return {**{key: row.get(key) for key in ('version', 'status', 'match_status', 'result_url', 'phones', 'emails', 'notes')},
            'reviewed_at': _iso(row.get('reviewed_at')),
            'reviewed_by': {'id': row.get('reviewed_by'), 'name': row.get('reviewer_name') or 'Team member'}
            if row.get('reviewed_by') else None}


def build_people(building, parties=(), saved=()):
    observations = source_observations(building, parties)
    saved_by_identity = defaultdict(list)
    for row in saved:
        for identity in row.get('identity_ids') or []:
            saved_by_identity[identity].append(row['person_id'])
    groups = []
    by_evidence = defaultdict(list)
    for observation in observations:
        prior = tuple(sorted(set(saved_by_identity[observation['id']])))
        evidence = (name_key(observation['name']), observation['_address_key'])
        # Separate existing manual reviews never silently overwrite one another.
        key = evidence if observation['is_person'] and _complete_name(observation['name']) and observation['_address_key'] else None
        group = next((candidate for candidate in by_evidence.get(key, [])
                      if len(set(prior) | set(candidate.get('_saved_ids', []))) <= 1), None) if key else None
        if group:
            group['identity_ids'] += observation['identity_ids']
            group['sources'] += observation['sources']
            group['locations'] += observation['locations']
            if observation['role'] not in group['role'].split(' · '):
                group['role'] += ' · ' + observation['role']
            group['_saved_ids'] = sorted(set(prior) | set(group['_saved_ids']))
        else:
            observation['_saved_ids'] = list(prior)
            groups.append(observation)
            if key is not None:
                by_evidence[key].append(observation)

    attached = set()
    for group in groups:
        group.pop('_address_key', None)
        group.pop('_saved_ids', None)
        group['locations'].sort(key=lambda loc: loc.get('reported_date') or '', reverse=True)
        group['default_location_id'] = group['locations'][0]['id'] if group['locations'] else 'property'
        matches = [row for row in saved if set(row.get('identity_ids') or []).intersection(group['identity_ids'])]
        available = [row for row in matches if row['person_id'] not in attached]
        if available:
            row = available[0]
            group['id'] = row['person_id']
            group['research'] = _review(row)
            # Preserve previous identities across a source refresh, never by name alone.
            group['identity_ids'] = sorted(set(group['identity_ids']) | set(row.get('identity_ids') or []))
            attached.add(row['person_id'])
        else:
            group['research'] = empty_research()
    for row in saved:
        if row['person_id'] in attached:
            continue
        snapshot = deepcopy(row['source_snapshot'])
        snapshot.update(id=row['person_id'], identity_ids=row['identity_ids'], historical=True, research=_review(row))
        groups.append(snapshot)

    conflicts = []
    by_name = defaultdict(list)
    for person in groups:
        by_name[name_key(person['name'])].append(person)
    for same_name in by_name.values():
        current = [p for p in same_name if not p['historical']]
        if len(current) > 1:
            conflicts.append(dict(kind='unconfirmed_identity', message=f"{current[0]['name']} appears in separate source records. A shared name does not confirm they are the same person.", person_ids=[p['id'] for p in current]))
        localities = {(loc['city'].upper(), loc['state'].upper(), loc['zip_code'].upper())
                      for person in current for loc in person['locations']}
        if len(localities) > 1:
            conflicts.append(dict(kind='different_reported_locations', message=f"Sources report different contact locations for {same_name[0]['name']}. Review the dates before choosing a search location.", person_ids=[p['id'] for p in current]))
        if any(p['research']['status'] == 'do_not_contact' for p in same_name):
            for person in same_name:
                person['do_not_contact'] = True
    owner_groups = [p for p in groups if not p['historical'] and any(s['key'] in ('acris', 'hpd', 'pluto') for s in p['sources'])]
    if len({name_key(p['name']) for p in owner_groups}) > 1:
        conflicts.append(dict(kind='different_reported_owners', message='Ownership sources report different names. Their roles and reporting dates may differ; no single source establishes that every listed contact is a current owner.', person_ids=[p['id'] for p in owner_groups]))
    if building.get('sos_principal_name'):
        from enrichment_service import owner_entity_match_quality
        quality, _ = owner_entity_match_quality(building)
        if quality == 'mismatch':
            conflicts.append(dict(kind='unverified_entity_contact', message='The Secretary of State entity does not match a recorded owner. Treat its contact as unverified.', person_ids=[p['id'] for p in groups if any(s['key'] == 'sos' for s in p['sources'])]))
    return groups, conflicts


def property_location(building):
    city = {'1': 'New York', '2': 'Bronx', '3': 'Brooklyn', '4': 'Queens', '5': 'Staten Island',
            'MANHATTAN': 'New York', 'BRONX': 'Bronx', 'BROOKLYN': 'Brooklyn',
            'QUEENS': 'Queens', 'STATEN ISLAND': 'Staten Island'}.get(_text(building.get('borough')).upper(), '')
    zip_code = _text(building.get('zip_code'))
    return dict(id='property', label=f"Property location · {zip_code or (city + ', NY' if city else 'NY')} (fallback)",
                city=city, state='NY', zip_code=zip_code, kind='Property location', is_property=True)


def _load(cur, bbl, ctx):
    cur.execute('SELECT to_jsonb(b) AS building FROM buildings b WHERE bbl=%s', (bbl,))
    row = cur.fetchone()
    if not row:
        raise ResearchError('Property not found', 404)
    building = row['building']
    cur.execute('''SELECT ap.party_name, ap.party_type, ap.address_1, ap.address_2,
                   ap.city, ap.state, ap.zip_code, at.doc_type, at.document_id,
                   at.is_primary_deed, at.recorded_date
        FROM acris_transactions at JOIN acris_parties ap ON ap.transaction_id=at.id
        WHERE at.building_id=%s AND at.is_primary_deed=TRUE ORDER BY at.document_id, ap.party_name''', (building['id'],))
    parties = list(cur.fetchall())
    cur.execute('''SELECT r.*, split_part(u.email, '@', 1) AS reviewer_name
        FROM crm_owner_research r LEFT JOIN users u ON u.id=r.reviewed_by
        WHERE r.team_id=%s AND r.bbl=%s ORDER BY r.created_at, r.person_id''', (ctx['team_id'], bbl))
    saved = list(cur.fetchall())
    return building, parties, saved


def _scope(bbl, ctx):
    if not re.fullmatch(r'[1-5]\d{9}', str(bbl)):
        raise ResearchError('Invalid property BBL')
    if not ctx or not ctx.get('user_id') or not ctx.get('team_id'):
        raise ResearchError('Authentication required', 401)


def get_owner_research(bbl, ctx):
    _scope(bbl, ctx)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            building, parties, saved = _load(cur, bbl, ctx)
            people, conflicts = build_people(building, parties, saved)
        return dict(success=True, people=people, conflicts=conflicts, property_location=property_location(building))
    finally:
        conn.close()


def validate_review(payload):
    allowed = {'version', 'status', 'match_status', 'result_url', 'phones', 'emails', 'notes'}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ResearchError('Only review fields may be updated')
    version = payload.get('version')
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise ResearchError('A nonnegative review version is required')
    if payload.get('status') not in STATUSES or payload.get('match_status') not in MATCH_STATUSES:
        raise ResearchError('Invalid research or match status')
    cleaned = dict(version=version, status=payload['status'], match_status=payload['match_status'])
    for key, limit in (('result_url', 2048), ('notes', 4000)):
        value = payload.get(key, '')
        if not isinstance(value, str) or len(value) > limit or '\x00' in value:
            raise ResearchError(f'Invalid {key.replace("_", " ")}')
        cleaned[key] = value.strip()
    if cleaned['result_url']:
        try:
            url = urlsplit(cleaned['result_url'])
            valid = (url.scheme == 'https' and url.hostname in ('truepeoplesearch.com', 'www.truepeoplesearch.com')
                     and not url.username and not url.password and url.port in (None, 443)
                     and not re.search(r'[\x00-\x20\\]', cleaned['result_url']))
        except ValueError:
            valid = False
        if not valid:
            raise ResearchError('Use an HTTPS TruePeopleSearch result link')
        cleaned['result_url'] = urlunsplit((url.scheme, url.netloc, url.path or '/', url.query, ''))
    for key in ('phones', 'emails'):
        values = payload.get(key, [])
        if not isinstance(values, list) or len(values) > 10:
            raise ResearchError(f'Enter at most 10 {key}')
        normalized = []
        for value in values:
            if not isinstance(value, str) or len(value) > (80 if key == 'phones' else 254):
                raise ResearchError(f'Invalid {key}')
            value = value.strip()
            if key == 'phones':
                from crm_service import normalize_phone_digits, split_phone_extension
                number, extension = split_phone_extension(value)
                digits = normalize_phone_digits(number)
                entered_digits = re.sub(r'\D', '', number)
                if (not re.fullmatch(r'[+\d().\s-]+', number) or len(digits) != 10
                        or not (len(entered_digits) == 10 or len(entered_digits) == 11 and entered_digits.startswith('1'))):
                    raise ResearchError('Enter a valid US phone number, with an optional extension')
                value = '+1' + digits + (f' ext. {extension}' if extension else '')
            else:
                if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
                    raise ResearchError('Enter a valid email address')
                value = value.lower()
            if value not in normalized:
                normalized.append(value)
        cleaned[key] = normalized
    if cleaned['status'] == 'contact_found' and not (cleaned['phones'] or cleaned['emails']):
        raise ResearchError('Add a reviewed phone or email before marking contact found')
    if cleaned['match_status'] == 'wrong_person' and (cleaned['phones'] or cleaned['emails']):
        raise ResearchError('Remove the other person’s contact details before marking a wrong match')
    return cleaned


def save_owner_research(bbl, person_id, payload, ctx):
    _scope(bbl, ctx)
    if not re.fullmatch(r'[a-f0-9]{32}', str(person_id)):
        raise ResearchError('Person record not found', 404)
    values = validate_review(payload)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Serialize this team/property's review writes, including first insert.
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                        (f"owner-research:{ctx['team_id']}:{bbl}",))
            building, parties, saved = _load(cur, bbl, ctx)
            people, _ = build_people(building, parties, saved)
            person = next((p for p in people if p['id'] == person_id), None)
            if not person:
                raise ResearchError('This source record changed. Reload the property before saving.', 409)
            if person['research']['version'] != values['version']:
                raise ResearchError('A teammate updated this review. Reload before saving.', 409)
            snapshot = {key: value for key, value in person.items() if key not in ('research', 'do_not_contact')}
            cur.execute('''INSERT INTO crm_owner_research
                (team_id,bbl,person_id,identity_ids,source_snapshot,name_key,status,match_status,
                 result_url,phones,emails,notes,reviewed_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (team_id,bbl,person_id) DO UPDATE SET
                 identity_ids=EXCLUDED.identity_ids,source_snapshot=EXCLUDED.source_snapshot,
                 name_key=EXCLUDED.name_key,status=EXCLUDED.status,match_status=EXCLUDED.match_status,
                 result_url=EXCLUDED.result_url,phones=EXCLUDED.phones,emails=EXCLUDED.emails,
                 notes=EXCLUDED.notes,reviewed_by=EXCLUDED.reviewed_by,reviewed_at=NOW(),
                 version=crm_owner_research.version+1
                RETURNING *''',
                (ctx['team_id'], bbl, person_id, Json(person['identity_ids']), Json(snapshot),
                 name_key(person['name']), values['status'], values['match_status'], values['result_url'],
                 Json(values['phones']), Json(values['emails']), values['notes'], ctx['user_id']))
            row = dict(cur.fetchone())
            row['reviewer_name'] = (ctx.get('email') or 'Team member').split('@')[0]
            person['research'] = _review(row)
            person['do_not_contact'] = values['status'] == 'do_not_contact' or any(
                saved_row['status'] == 'do_not_contact' and saved_row['person_id'] != person_id
                and saved_row['name_key'] == name_key(person['name']) for saved_row in saved)
        conn.commit()
        return dict(success=True, person=person)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def suppressed_owner_names(cur, team_id, bbls):
    """Conservative property/team suppression; never shares another team's choices."""
    if not bbls:
        return {}
    cur.execute('''SELECT bbl, name_key FROM crm_owner_research
        WHERE team_id=%s AND bbl=ANY(%s) AND status='do_not_contact' ''', (team_id, [str(b) for b in bbls]))
    result = defaultdict(set)
    for row in cur.fetchall():
        result[row['bbl']].add(row['name_key'])
    return dict(result)


def do_not_contact_for_owner(cur, team_id, bbl, owner_name=None, person_id=None):
    """Guard paid lookup/export; same-name ambiguity blocks rather than bypasses DNC."""
    if person_id:
        cur.execute('''SELECT 1 FROM crm_owner_research WHERE team_id=%s AND bbl=%s
            AND person_id=%s AND status='do_not_contact' LIMIT 1''', (team_id, str(bbl), person_id))
        if cur.fetchone():
            return True
    return bool(owner_name and name_key(owner_name) in suppressed_owner_names(cur, team_id, [bbl]).get(str(bbl), set()))
