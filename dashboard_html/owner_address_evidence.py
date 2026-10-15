"""Keep reported contact addresses attached to their named source parties."""
from collections import defaultdict

from owner_source_dates import source_date
from socrata_client import is_ownership_party


OWNER_EXPORT_COLUMNS_SQL = """
    COALESCE(NULLIF(BTRIM(b.sale_buyer_primary), ''), NULLIF(BTRIM(b.current_owner_name), ''),
             NULLIF(BTRIM(b.owner_name_hpd), ''), NULLIF(BTRIM(b.owner_name_rpad), '')) AS owner_name,
    CASE WHEN NULLIF(BTRIM(b.sale_buyer_primary), '') IS NOT NULL THEN 'acris'
         WHEN NULLIF(BTRIM(b.current_owner_name), '') IS NOT NULL THEN 'pluto'
         WHEN NULLIF(BTRIM(b.owner_name_hpd), '') IS NOT NULL THEN 'hpd'
         WHEN NULLIF(BTRIM(b.owner_name_rpad), '') IS NOT NULL THEN 'rpad' END AS owner_source,
    b.sale_recorded_date, b.sale_crfn, b.hpd_registration_id,
    to_jsonb(b)->'hpd_owner_contacts' AS hpd_owner_contacts,
    to_jsonb(b)->>'hpd_last_registration_date' AS hpd_last_registration_date
"""
OWNER_ADDRESS_FIELDS = {
    'owner_address': ('Reported Owner Mailing Address', lambda p: p.get('owner_address') or ''),
    'owner_address_source': ('Owner Address Source', lambda p: p.get('owner_address_source') or ''),
    'owner_address_reported_date': ('Owner Address Reported Date', lambda p: p.get('owner_address_reported_date') or ''),
}


def address_export_fields(fields):
    """An exported address always includes its named party and provenance."""
    fields = list(fields)
    if 'owner_address' in fields:
        if 'owner_name' not in fields:
            fields.insert(fields.index('owner_address'), 'owner_name')
        for field in ('owner_address_source', 'owner_address_reported_date'):
            if field not in fields:
                fields.append(field)
    return fields


def _name_key(value):
    # Within one source we require the complete name, not a fuzzy person match.
    return ' '.join(str(value or '').upper().split())


def resolve_export_address(building, deed_parties=()):
    """Return an address only from the record that supplied the exported owner."""
    name = _name_key(building.get('owner_name'))
    if not name:
        return {}
    candidates = []
    if building.get('owner_source') == 'acris':
        for party in deed_parties:
            if (not party.get('is_primary_deed')
                    or party.get('party_type') != 'buyer'
                    or not is_ownership_party(party.get('doc_type'), 'buyer')
                    or _name_key(party.get('party_name')) != name):
                continue
            if building.get('sale_crfn') and str(party.get('crfn')) != str(building['sale_crfn']):
                continue
            if source_date(party.get('recorded_date')) != source_date(building.get('sale_recorded_date')):
                continue
            candidates.append(dict(
                street=party.get('address_1'), unit=party.get('address_2'),
                city=party.get('city'), state=party.get('state'), zip_code=party.get('zip_code'),
                source='ACRIS deed grantee mailing address', reported=party.get('recorded_date')))
    elif building.get('owner_source') == 'hpd':
        for contact in building.get('hpd_owner_contacts') or []:
            if (not isinstance(contact, dict) or _name_key(contact.get('name')) != name
                    or contact.get('role') not in ('CorporateOwner', 'IndividualOwner', 'JointOwner')
                    or not building.get('hpd_registration_id')
                    or str(contact.get('registration_id')) != str(building['hpd_registration_id'])
                    or source_date(contact.get('reported_date')) != source_date(building.get('hpd_last_registration_date'))):
                continue
            candidates.append(dict(
                street=contact.get('address'), city=contact.get('city'),
                state=contact.get('state'), zip_code=contact.get('zip_code'),
                source='HPD registered owner business address', reported=contact.get('reported_date')))

    addresses = {}
    for candidate in candidates:
        if not str(candidate.get('street') or '').strip():
            continue
        address = ', '.join(str(candidate.get(key) or '').strip()
                            for key in ('street', 'unit', 'city', 'state', 'zip_code')
                            if str(candidate.get(key) or '').strip())
        reported = source_date(candidate.get('reported'))
        addresses[_name_key(address)] = {
            'owner_address': address,
            'owner_address_source': candidate['source'],
            'owner_address_reported_date': reported.isoformat() if reported else '',
        }
    # Multiple conflicting addresses must be reviewed, never arbitrarily selected.
    return next(iter(addresses.values())) if len(addresses) == 1 else {}


def populate_export_addresses(cur, properties):
    by_building = defaultdict(list)
    ids = [p['id'] for p in properties if p.get('owner_source') == 'acris']
    if ids:
        cur.execute("""SELECT at.building_id, at.is_primary_deed, at.doc_type,
                   at.recorded_date, at.crfn, ap.party_type, ap.party_name,
                   ap.address_1, ap.address_2, ap.city, ap.state, ap.zip_code
            FROM acris_transactions at JOIN acris_parties ap ON ap.transaction_id=at.id
            WHERE at.building_id=ANY(%s) AND at.is_primary_deed=TRUE
              AND ap.party_type='buyer'""", (ids,))
        for row in cur.fetchall():
            by_building[row['building_id']].append(row)
    for prop in properties:
        prop.update(owner_address='', owner_address_source='', owner_address_reported_date='')
        prop.update(resolve_export_address(prop, by_building[prop['id']]))
