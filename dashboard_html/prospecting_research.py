"""Read-only enrichment adapters. Never invoke building writers or paid lookups."""
import re
from datetime import datetime, timezone

import crm_service as crm
import prospecting_service as s
from record_links import owner_source_links, acris_document_url, permit_source_link

PROPERTY_FACTS = {
    'current_owner_name': ('pluto', 'PLUTO recorded owner'),
    'owner_name_rpad': ('rpad', 'Historical assessment owner (through FY2018/19)'),
    'owner_name_hpd': ('hpd', 'HPD registered owner'),
    'hpd_agent_name': ('hpd', 'HPD managing agent (not proof of ownership)'),
    'hpd_site_manager_name': ('hpd', 'HPD site manager'),
    'sale_buyer_primary': ('acris', 'Latest recorded deed grantee'),
    'ecb_respondent_name': ('ecb', 'ECB respondent (not proof of ownership)'),
    'hpd_open_violations': ('hpd', 'Open HPD violations'),
    'hpd_open_complaints': ('hpd', 'Open HPD complaints'),
    'ecb_open_violations': ('ecb', 'Open ECB violations'),
    'dob_violation_count': ('bis', 'DOB violation records'),
    'dob_safety_open_violations': ('dob_now', 'Open DOB NOW safety violations'),
    'tax_delinquency_count': ('tax', 'Historical lien-sale notice entries'),
    'tax_delinquency_latest_date': ('tax', 'Most recent lien-sale notice date'),
}


def norm(value):
    return re.sub(r'\s+', ' ', str(value or '').strip()).casefold()


def serial(value):
    return value.isoformat() if hasattr(value, 'isoformat') else str(value)


def source(label, url, hint='', **extra):
    return {'label': label, 'url': url, 'hint': hint, **extra}


def finding(label, value, origin, basis, *, safe=True, field=None, current=''):
    return {'kind': 'field' if field else 'research', 'field': field, 'label': label,
            'value': str(value)[:s.MAX_CELL], 'current': current, 'source': origin, 'basis': basis,
            'default_selected': bool(safe and (not field or not current)), 'conflict': bool(field and current)}


def _property_facts(record, basis, safe=True):
    links = owner_source_links(record)
    links['tax'] = source('NYC lien-sale notices', 'https://data.cityofnewyork.us/City-Government/Tax-Lien-Sale-Lists/9rz4-mjek/data_preview', 'Search BBL: '+record['bbl'])
    prefix = f"{record.get('address') or record['bbl']} · "
    return [finding(prefix+label, record[key], links[kind], basis, safe=safe)
            for key, (kind, label) in PROPERTY_FACTS.items()
            if record.get(key) not in (None, '') and kind in links]


def _has_table(cur, name):
    cur.execute('SELECT to_regclass(%s) AS name', (name,))
    return bool(cur.fetchone()['name'])


def _internal(ctx, fields):
    findings, checks, buildings = [], [], []
    matched = False
    name, company, address = (norm(fields.get(k)) for k in ('name', 'company', 'address'))
    bbl = str(fields.get('bbl') or '').strip()
    if not re.fullmatch(r'[1-5]\d{9}', bbl):
        bbl = fields.get('address', '') if re.fullmatch(r'[1-5]\d{9}', fields.get('address', '')) else ''
    with s.transaction() as cur:
        cur.execute(f"""SELECT c.id,c.name,c.company,c.title,c.email,c.do_not_contact,
            COALESCE((SELECT jsonb_agg(jsonb_build_object('number',p.number,'digits',p.digits,'extension',p.extension)
                ORDER BY p.is_primary DESC,p.id) FROM crm_phones p WHERE p.contact_id=c.id AND p.status='good'),'[]') AS phones
            FROM crm_contacts c WHERE {crm.record_scope_sql(ctx, 'c')} AND
            ((%(name)s<>'' AND LOWER(TRIM(c.name))=%(name)s) OR (%(company)s<>'' AND LOWER(TRIM(c.company))=%(company)s))
            ORDER BY c.id LIMIT 21""", {**ctx, 'name': name, 'company': company})
        contacts = [dict(r) for r in cur.fetchall()]
        checks.append('Accessible CRM contacts checked' + ('; first 20 matches shown' if len(contacts)>20 else ''))
        phone = crm.normalize_phone_digits(fields.get('phone', ''))
        if len(phone) != 10:
            phone = ''
        strong = [c for c in contacts if norm(c['name']) == name and name and (
            (company and norm(c['company']) == company) or
            (fields.get('email') and norm(c['email']) == norm(fields['email'])) or
            (phone and any(p['digits'] == phone for p in c['phones'])))]
        # Never make field proposals for multiple plausible people or for a
        # truncated candidate set. A shared phone/company alone is not identity.
        contact = strong[0] if len(strong) == 1 and len(contacts) <= 20 else None
        for c in contacts[:20]:
            same = c is contact
            origin = source('CRM contact', f"/crm/contacts/{c['id']}", kind='crm_contact', record_id=c['id'])
            basis = 'Exact name plus company, email or phone.' if same else 'Company/name candidate only. Verify this is the intended person.'
            findings.append(finding('Existing CRM contact', c['name'] + (' · '+c['company'] if c['company'] else '') +
                (' · DO NOT CONTACT' if c['do_not_contact'] else ''), origin, basis, safe=same and not c['do_not_contact']))
            if same and not c['do_not_contact']:
                matched = True
                values = {k: c.get(k) for k in ('company', 'title', 'email')}
                if c['phones']:
                    p = c['phones'][0]
                    values['phone'] = p['number'] + (' ext. '+p['extension'] if p.get('extension') else '')
                for field, value in values.items():
                    if value and norm(value) != norm(fields.get(field)):
                        findings.append(finding(s.FIELDS[field], value, origin, basis, field=field, current=fields.get(field, '')))
        if _has_table(cur, 'buildings'):
            # Public columns only: never select the shared paid-enrichment cache.
            keys = ['id', 'bbl', 'bin', 'address', 'borough', *PROPERTY_FACTS]
            projection = ','.join("'%s',to_jsonb(b)->'%s'" % (k, k) for k in keys)
            owners = ('current_owner_name', 'owner_name_hpd', 'owner_name_rpad', 'sale_buyer_primary', 'hpd_agent_name')
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='buildings'")
            columns = {r['column_name'] for r in cur.fetchall()}
            owner_where = ' OR '.join(f"LOWER(TRIM(b.{k}))=%(company)s" for k in owners if k in columns) or 'FALSE'
            cur.execute(f"""SELECT jsonb_build_object({projection}) AS record FROM buildings b WHERE
                (%(bbl)s<>'' AND b.bbl=%(bbl)s) OR (%(address)s<>'' AND LOWER(TRIM(b.address))=%(address)s)
                OR (%(company)s<>'' AND ({owner_where}))
                ORDER BY (b.bbl=%(bbl)s) DESC, (LOWER(TRIM(b.address))=%(address)s) DESC,b.id LIMIT 21""",
                {'bbl': bbl, 'address': address, 'company': company})
            buildings = [r['record'] for r in cur.fetchall()]
            checks.append('Existing property records checked' + ('; first 20 matches shown' if len(buildings)>20 else ''))
            for b in buildings[:20]:
                exact_bbl = bbl and b['bbl'] == bbl
                exact_address = address and norm(b['address']) == address
                count_address = sum(norm(x['address']) == address for x in buildings)
                safe = bool(exact_bbl or exact_address and count_address == 1 and len(buildings)<=20)
                matched = matched or safe
                basis = ('Exact BBL.' if exact_bbl else 'Exact stored address.' if safe else
                         'Company or address candidate; verify the property and role.') + ' Cached public record; may be outdated.'
                origin = source('Property in our system', '/property/'+str(b['bbl']), 'BBL: '+str(b['bbl']))
                findings.append(finding('Property record', b['address'] or b['bbl'], origin, basis, safe=safe))
                findings.extend(_property_facts(b, basis, safe))
                if safe and name and _has_table(cur, 'user_enrichments'):
                    cur.execute('SELECT owner_name_searched,enriched_phones,enriched_emails,enriched_at FROM user_enrichments WHERE user_id=%s AND building_id=%s',
                                (ctx['user_id'], b['id']))
                    for paid in cur.fetchall():
                        if norm(paid['owner_name_searched']) != name:
                            continue
                        origin = source('Previously unlocked owner lookup', '/property/'+str(b['bbl']),
                                        'Unlocked for your account; '+serial(paid['enriched_at']))
                        for field, key, value_keys in [('phone', 'enriched_phones', ('number','phone','phone_number')),
                                                       ('email', 'enriched_emails', ('email','address'))]:
                            values = paid[key] or []
                            if isinstance(values, str):
                                continue
                            candidates = list(dict.fromkeys(str(v if isinstance(v, str) else next((v.get(k) for k in value_keys if v.get(k)), '')) for v in values))
                            candidates = [v for v in candidates if v]
                            for value in candidates[:5]:
                                if norm(value) != norm(fields.get(field)):
                                    findings.append(finding(s.FIELDS[field], value, origin, 'Exact owner name and property. Previously unlocked; verify it is still current.',
                                        field=field if len(candidates)==1 else None, current=fields.get(field, ''), safe=len(candidates)==1))
        else:
            checks.append('No local property catalog is installed.')
        if (name or company) and _has_table(cur, 'permit_contact_directory'):
            cur.execute("""SELECT d.name,d.phone,d.role,d.verification_status,p.permit_no,p.job_number,p.api_source,p.bbl
                FROM permit_contact_directory d JOIN permits p ON p.id=d.permit_id
                WHERE (%s<>'' AND LOWER(TRIM(d.name))=%s) OR (%s<>'' AND LOWER(TRIM(d.name))=%s)
                ORDER BY p.id DESC LIMIT 21""", (name, name, company, company))
            contacts = [dict(r) for r in cur.fetchall()]
            checks.append('Public permit contact directory checked' + ('; first 20 matches shown' if len(contacts)>20 else ''))
            for c in contacts[:20]:
                origin = permit_source_link(c)
                basis = f"Name/company match on permit {c['permit_no']}, BBL {c['bbl']}. Verify identity and role. Phone status: {c['verification_status']}."
                findings.append(finding('Recorded permit '+c['role'], c['name']+(' · '+c['phone'] if c['phone'] else ''), origin, basis, safe=False))
    return {'findings': findings, 'checks': checks, 'errors': [], 'matched': matched}, buildings[:20], bbl


def _fetch_property(source_name, bbl):
    """Each source succeeds/fails independently; no source writes to the database."""
    if source_name in ('pluto', 'rpad', 'hpd'):
        import step2_enrich_from_pluto as step
        data, error = getattr(step, f'get_{source_name}_data_for_bbl')(bbl)
    elif source_name in ('tax', 'ecb', 'dob', 'safety'):
        import step4_enrich_from_tax_liens as step
        function = {'tax':'get_tax_delinquency_data', 'ecb':'get_ecb_violations_data',
                    'dob':'get_dob_violations_data', 'safety':'get_dob_safety_violations_data'}[source_name]
        data, error = getattr(step, function)(bbl)
    elif source_name == 'acris':
        import step3_enrich_from_acris as step
        history = step.get_acris_full_history(bbl)
        deed = step.find_primary_deed(history['transactions'])
        data = {'sale_buyer_primary': '; '.join(p['name'] for p in deed['buyers'] if p.get('name')),
                'document_id': deed['document_id'], 'sale_date': serial(deed['doc_date'] or deed['recorded_date'])} if deed else {}
        error = None
    else:
        raise ValueError('Unknown source')
    if error:
        raise RuntimeError('Source unavailable')
    return {k: v if isinstance(v, (str, int, float, bool, type(None))) else serial(v) for k, v in (data or {}).items()}


def _fetch_sos(company):
    from ny_sos_lookup import lookup_business
    result = lookup_business(company)
    if result.error:
        raise RuntimeError('Source unavailable')
    if not result.found:
        return {}
    return {'entity_name': result.entity_name, 'dos_id': result.dos_id, 'status': result.status,
            'quality': result.match_quality, 'people': [{'name': p.full_name, 'role': p.title} for p in result.people]}


def _resolve(address):
    from property_lookup import resolve_address_to_property
    result, error = resolve_address_to_property(address)
    return {'property': result, 'error': 'Address could not be resolved. Check borough/ZIP or supply a BBL.' if error else None}


def _fetch_permits(dataset, bbl):
    from socrata_client import SocrataClient, soql_quote
    from permit_sync import build_bbl
    client = SocrataClient()
    columns = client.get_columns(dataset)
    if not {'borough', 'block', 'lot'} <= columns:
        raise RuntimeError('Dataset parcel fields changed')
    boroughs = {'1': ('MANHATTAN','Manhattan','M'), '2': ('BRONX','Bronx','X'),
                '3': ('BROOKLYN','Brooklyn','B','K'), '4': ('QUEENS','Queens','Q'), '5': ('STATEN ISLAND','Staten Island','S','R')}
    values = (bbl[0], *boroughs[bbl[0]])
    where = 'borough in ('+','.join(soql_quote(v) for v in values)+')'
    for field, raw in (('block', bbl[1:6]), ('lot', bbl[6:])):
        where += f' AND {field} in ({soql_quote(raw)},{soql_quote(str(int(raw)))})'
    # Bounded evidence, not a misleading total. Use a stable newest-first field
    # where available, and explain the 20-record cap in the review.
    date_field = next((f for f in ('issued_date','issuance_date','filing_date') if f in columns), None)
    records = client.get(dataset, **{'$where': where, '$limit': 20, '$order': (date_field+' DESC, ' if date_field else '')+':id DESC'})
    result = []
    for r in records:
        if build_bbl(r.get('borough'), r.get('block'), r.get('lot')) != bbl:
            continue
        job = r.get('job_filing_number') or r.get('job__') or r.get('work_permit')
        result.append({'job': job, 'type': r.get('work_type') or r.get('job_type') or 'Filing',
                       'date': r.get(date_field, '') if date_field else '',
                       'business': r.get('owner_business_name') or r.get('owner_s_business_name') or '',
                       'api_source': 'nyc_open_data' if dataset=='dob_permits_bis' else dataset})
    return result


def research(ctx, fields, mode, cache):
    result, buildings, bbl = _internal(ctx, fields)
    if mode == 'internal':
        _flag_alternatives(result)
        return result
    checked = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    def fetch(key, fn, label):
        def attempt():
            try:
                return {'ok': True, 'data': fn()}
            except Exception:
                return {'ok': False}
        try:
            response = cache(key, attempt)
            if not response['ok']:
                raise RuntimeError('Source unavailable')
            value = response['data']
            result['checks'].append(label+' checked')
            return value
        except InterruptedError:
            raise
        except Exception:
            result['errors'].append(label+' unavailable. Retry with a new run; this is not a no-match result.')
            return None
    address = fields.get('address', '').strip()
    confirmed = bool(bbl)
    candidates = [b for b in buildings if address and norm(b['address']) == norm(address)]
    if not bbl and len(candidates) == 1:
        bbl = candidates[0]['bbl']; confirmed = True
    lookup = {'bbl': bbl, 'address': address}
    if not bbl and address:
        resolved = fetch('address:'+norm(address), lambda: _resolve(address), 'NYC address search')
        if resolved and resolved.get('property'):
            lookup = resolved['property']; bbl = str(lookup['bbl'])
            result['findings'].append(finding('Possible NYC property', f"{lookup.get('address', address)} · BBL {bbl}",
                owner_source_links(lookup)['pluto'], 'Geocoder candidate. Verify this is the intended building.', safe=False))
        elif resolved and resolved.get('error'):
            result['errors'].append(resolved['error'])
    companies = [fields.get('company', '').strip()]
    if bbl and re.fullmatch(r'[1-5]\d{9}', bbl):
        lookup['bbl'] = bbl
        for source_name in ('pluto', 'hpd', 'rpad', 'acris', 'tax', 'ecb', 'dob', 'safety'):
            record = fetch(source_name+':'+bbl, lambda n=source_name: _fetch_property(n, bbl), source_name.upper())
            if record is None:
                continue
            record = {**lookup, **record}
            basis = f"Public records checked {checked}. " + ('Matched property; source roles are not identity verification.' if confirmed else 'Unconfirmed address candidate; verify before saving.')
            facts = _property_facts(record, basis, confirmed)
            if source_name == 'acris':
                for fact in facts:
                    url = acris_document_url(record.get('document_id'))
                    if url:
                        fact['source'] = source('ACRIS recorded deed', url, 'Deed date: '+record.get('sale_date', ''))
            result['findings'].extend(facts)
            if confirmed and facts:
                result['matched'] = True
            for key in ('current_owner_name', 'owner_name_hpd', 'sale_buyer_primary'):
                # Joint parties are not one company. Avoid a broad/potentially
                # wrong registry query and never substitute agents for owners.
                value = record.get(key)
                if value and not any(separator in value for separator in (';', ' & ')):
                    companies.append(value)
        for dataset in ('dob_permits_bis', 'dob_now_filings', 'dob_now_permits'):
            permits = fetch(dataset+':'+bbl, lambda d=dataset: _fetch_permits(d, bbl), dataset.replace('_', ' ').upper())
            if permits is None:
                continue
            for permit in permits:
                origin = permit_source_link({'job_number': permit['job'], 'permit_no': permit['job'], 'api_source': permit['api_source']})
                value = ' · '.join(str(v) for v in (permit['job'], permit['type'], permit['date'], permit['business']) if v)
                result['findings'].append(finding(f"{lookup.get('address') or bbl} · permit/filing", value, origin,
                    f'BBL {bbl}; checked {checked}. Up to 20 recent records per permit source. A filing contact is not proof of current ownership.', safe=confirmed))
    elif not address:
        result['checks'].append('Building research needs a mapped NYC address or BBL.')
    seen = set()
    for company in companies:
        if not company or norm(company) in seen or len(seen) >= 4:
            continue
        # Company explicitly mapped by the user, or an entity-looking recorded
        # owner. Never run a person through business lookup as an owner shortcut.
        if company != fields.get('company', '').strip() and not re.search(r'\b(LLC|INC|CORP|LP|LTD|ASSOC|COMPANY)\b', company, re.I):
            continue
        seen.add(norm(company))
        company_data = fetch('sos:'+norm(company), lambda c=company: _fetch_sos(c), 'NY registry: '+company)
        if not company_data:
            continue
        safe = company_data['quality'] == 'exact' and company == fields.get('company', '').strip()
        origin = source('NY Secretary of State', 'https://apps.dos.ny.gov/publicInquiry/', 'Search DOS ID: '+company_data['dos_id'])
        basis = f"Registry match: {company_data['quality']}. Checked {checked}. Corporate roles do not establish building ownership."
        result['findings'].append(finding('Registered company', company_data['entity_name']+' · '+company_data['status'], origin, basis, safe=safe))
        result['matched'] = result['matched'] or safe
        for person in company_data['people']:
            result['findings'].append(finding(company_data['entity_name']+' · '+person['role'], person['name'], origin,
                basis + (' Agent for service/registration only; not an owner finding.' if 'agent' in person['role'].lower() else ''), safe=safe))
    # Keep sources that disagree visible. Collapse only identical findings from
    # the same source, favoring the fresh finding over a cached one.
    unique = {}
    for f in result['findings']:
        unique[(f['kind'], f['field'], f['label'], f['value'], f['source']['url'])] = f
    result['findings'] = list(unique.values())
    _flag_alternatives(result)
    return result


def _flag_alternatives(result):
    for f in result['findings']:
        if f['kind'] == 'field' and len({norm(other['value']) for other in result['findings']
                if other['kind'] == 'field' and other['field'] == f['field']}) > 1:
            f['default_selected'] = False
            f['basis'] += ' Sources propose different values; choose one.'
