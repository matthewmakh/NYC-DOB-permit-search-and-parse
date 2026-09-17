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
    links = {
        'sos': {
            'url': 'https://apps.dos.ny.gov/publicInquiry/',
            'label': 'NY Secretary of State',
            'hint': (f"Search DOS ID: {building['sos_dos_id']}" if building.get('sos_dos_id')
                     else f"Search entity: {building.get('sos_entity_name') or ''}"),
        },
        'hpd': {'url': 'https://hpdonline.nyc.gov/hpdonline/', 'label': 'HPD Online',
                'hint': f"Search address: {building.get('address') or bbl}"},
        'rpad': {
            'url': 'https://data.cityofnewyork.us/City-Government/Property-Valuation-and-Assessment-Data/yjxr-fw8i/data_preview',
            'label': 'Historical assessment records', 'hint': f'Search BBLE: {bbl}',
        },
    }
    if parcel:
        borough, block, lot = map(int, parcel.groups())
        links['pluto'] = {'url': f'https://zola.planning.nyc.gov/l/lot/{borough}/{block}/{lot}',
                          'label': 'ZoLa property record', 'hint': ''}
        links['ecb'] = {
            'url': BIS + 'PropertyBrowseByBBLServlet?' + urlencode({
                'allborough': borough, 'allblock': block, 'alllot': lot, 'requestid': 0}),
            'label': 'BIS property record', 'hint': 'Open OATH/ECB violations in BIS',
        }
        links['acris'] = {
            'url': 'https://a836-acris.nyc.gov/bblsearch/bblsearch.asp?' + urlencode({
                'borough': borough, 'block': block, 'lot': lot}),
            'label': 'ACRIS property records', 'hint': '',
        }

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
                'hint': f'Under Search the Public Portal, choose Job Number and enter {job}.'}
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
            'label': 'Search DOB records', 'hint': f'Search permit or job number: {number}'}
