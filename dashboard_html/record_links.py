"""Public-facing source pages, with lookup hints for session-based portals.

Do not manufacture deep links for DOB NOW or NY DOS: their record views
depend on a search session. ACRIS document IDs are not CRFNs.
"""
import re
from urllib.parse import urlencode, urlsplit

DOB_NOW = 'https://a810-dobnow.nyc.gov/publish/Index.html#!/'
BIS = 'https://a810-bisweb.nyc.gov/bisweb/'


def acris_document_url(document_id):
    value = str(document_id or '').strip()
    if not re.fullmatch(r'(?:\d{16}|FT_\d+)', value):
        return None
    return 'https://a836-acris.nyc.gov/DS/DocumentSearch/DocumentDetail?' + urlencode({'doc_id': value})


def owner_source_links(building, transactions=()):
    bbl = str(building.get('bbl') or '').strip()
    parcel = re.fullmatch(r'([1-5])(\d{5})(\d{4})', bbl)
    borough_names = {'1': 'Manhattan', '2': 'Bronx', '3': 'Brooklyn',
                     '4': 'Queens', '5': 'Staten Island'}
    borough_name = borough_names.get(str(building.get('borough') or '')) or borough_names.get(bbl[:1])
    address = str(building.get('address') or '').strip()
    hpd_lookup = (f'{address}, {borough_name}'
                  if address and borough_name and borough_name.lower() not in address.lower()
                  else address)
    links = {
        'sos': {
            'url': 'https://apps.dos.ny.gov/publicInquiry/',
            'label': 'Search NY Secretary of State',
            'hint': (f"Choose Search By → DOS ID, then enter {building['sos_dos_id']}."
                     if building.get('sos_dos_id') else
                     f"Search entity name: {building.get('sos_entity_name') or ''}"),
            'lookup_value': str(building.get('sos_dos_id') or building.get('sos_entity_name') or ''),
            'lookup_label': 'DOS ID' if building.get('sos_dos_id') else 'entity name',
        },
        'hpd': {'url': 'https://hpdonline.nyc.gov/hpdonline/', 'label': 'Search HPD Online',
                'hint': f'Search address: {hpd_lookup}' if hpd_lookup else 'Search this property by address.',
                'lookup_value': hpd_lookup, 'lookup_label': 'address'},
        'rpad': {
            'url': 'https://data.cityofnewyork.us/City-Government/Property-Valuation-and-Assessment-Data/yjxr-fw8i/data_preview',
            'label': 'Historical assessment records', 'hint': f'Search BBLE: {bbl}',
            'lookup_value': bbl, 'lookup_label': 'BBL',
        },
        'dob_now': {'url': DOB_NOW, 'label': 'DOB NOW public portal', 'hint': ''},
    }
    if parcel:
        borough, block, lot = map(int, parcel.groups())
        links['pluto'] = {'url': f'https://zola.planning.nyc.gov/l/lot/{borough}/{block}/{lot}',
                          'label': 'ZoLa property record', 'hint': ''}
        parcel_url = BIS + 'PropertyBrowseByBBLServlet?' + urlencode({
                'allborough': borough, 'allblock': block, 'alllot': lot, 'requestid': 0})
        links['bis'] = {'url': parcel_url, 'label': 'BIS property record',
                        'hint': 'Choose the matching building on this lot.'}
        links['ecb'] = {'url': parcel_url, 'label': 'BIS property record',
                        'hint': 'Choose the building, then OATH/ECB violations.'}
        bin_value = str(building.get('bin') or '').strip()
        if re.fullmatch(r'\d{7}', bin_value):
            links['bis'] = {
                'url': BIS + 'PropertyProfileOverviewServlet?' + urlencode({
                    'bin': bin_value, 'requestid': 1}),
                'label': 'BIS building profile', 'hint': '',
            }
            links['ecb'] = {
                'url': BIS + 'ECBQueryByLocationServlet?' + urlencode({
                    'requestid': 2, 'allbin': bin_value}),
                'label': 'BIS OATH/ECB violations', 'hint': '',
            }
        links['acris_parcel'] = {
            'url': 'https://a836-acris.nyc.gov/bblsearch/bblsearch.asp?' + urlencode({
                'borough': borough, 'block': block, 'lot': lot}),
            'label': 'ACRIS parcel search', 'hint': '',
        }
        links['acris'] = links['acris_parcel']

    crfn = str(building.get('sale_crfn') or '').strip()
    deed = next((t for t in transactions if (
        str(t.get('crfn') or '').strip() == crfn if crfn else t.get('is_primary_deed') is True
    )), None)
    url = acris_document_url(deed.get('document_id')) if deed else None
    if url:
        links['acris'] = {'url': url, 'label': 'ACRIS recorded deed', 'hint': ''}
    return links


def permit_source_link(permit):
    source = str(permit.get('api_source') or '').lower()
    number = str(permit.get('job_number') or permit.get('permit_no') or '').strip()
    # Older records may lack api_source. DOB NOW Build numbers carry a
    # borough letter; Electrical/Elevator imports use namespaced identities.
    is_now = source.startswith('dob_now') or bool(re.match(r'^(?:[BMQSX]\d{8}|(?:EL|VT)[:\-])', number, re.I))
    if is_now:
        number = re.sub(r'^(?:EL|VT)[:\-]', '', number, flags=re.I)
        match = re.match(r'^([BMQSX]\d{8})(?:-|$)', number, re.I)
        job = match.group(1).upper() if match else number
        return {'url': DOB_NOW, 'label': 'Open in DOB NOW',
                'hint': f'Under Search the Public Portal, choose Job Number and enter {job}.',
                'lookup_value': job, 'lookup_label': 'job number'}
    if re.fullmatch(r'\d{9}(?:-.*)?', number):
        return {'url': BIS + 'JobsQueryByNumberServlet?' + urlencode({
            'passjobnumber': number[:9], 'requestid': 0}),
            'label': 'View BIS job', 'hint': ''}
    # Keep trustworthy record links for legacy records without a job number.
    link = str(permit.get('link') or '').strip()
    try:
        parsed = urlsplit(link)
    except ValueError:
        parsed = urlsplit('')
    if parsed.scheme in ('http', 'https') and parsed.hostname in (
            'a810-bisweb.nyc.gov', 'a810-dobnow.nyc.gov'):
        return {'url': link, 'label': 'View DOB record', 'hint': ''}
    return {'url': 'https://www.nyc.gov/site/buildings/dob/building-information-search.page',
            'label': 'Search DOB records', 'hint': f'Search permit or job number: {number}',
            'lookup_value': number, 'lookup_label': 'permit or job number'}
