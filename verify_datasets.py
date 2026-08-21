#!/usr/bin/env python3
import sys
import json

from socrata_client import SocrataClient, DATASETS, bbl_parts, soql_quote, where_block_lot, load_party_roles

GOLDEN_BBL = sys.argv[1] if len(sys.argv) > 1 else '1008350041'

client = SocrataClient()
boro, block, lot, block_p, lot_p = bbl_parts(GOLDEN_BBL)

print(f"NYC Open Data verification report — golden BBL {GOLDEN_BBL} "
      f"(boro={boro} block={block}/{block_p} lot={lot}/{lot_p})")
print("=" * 78)


def section(title):
    print(f"\n### {title}")


def safe(fn, label):
    try:
        return fn()
    except Exception as e:
        print(f"  !! {label}: {type(e).__name__}: {e}")
        return None


section("1. Dataset identities (registry ID -> live name, row freshness)")
import urllib.request
for key, dataset_id in sorted(DATASETS.items()):
    def fetch_meta(dataset_id=dataset_id):
        req = urllib.request.Request(
            f"https://data.cityofnewyork.us/api/views/{dataset_id}.json",
            headers={'User-Agent': 'dataset-verify/1.0'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    meta = safe(fetch_meta, f"{key} ({dataset_id})")
    if meta:
        print(f"  {key:20s} {dataset_id}  {meta.get('name', '?')[:52]}")

section("2. V1 — block/lot padding probe (padded vs stripped vs agnostic)")
for key, boro_field in [('dob_violations', 'boro'), ('ecb_violations', 'boro'),
                        ('tax_lien_sale', 'borough')]:
    for label, where in [
        ('stripped', f"{boro_field}={soql_quote(boro)} AND block={soql_quote(block)} AND lot={soql_quote(lot)}"),
        ('padded  ', f"{boro_field}={soql_quote(boro)} AND block={soql_quote(block_p)} AND lot={soql_quote(lot_p)}"),
        ('agnostic', where_block_lot(boro_field, 'block', 'lot', GOLDEN_BBL)),
    ]:
        rows = safe(lambda w=where, k=key: client.get(k, **{'$where': w, '$select': ':id', '$limit': 1000}), f"{key} {label}")
        if rows is not None:
            print(f"  {key:16s} {label}: {len(rows)} rows")

section("3. ACRIS party roles — live spot check on the golden BBL's latest deed")
def acris_check():
    legals = client.get('acris_legals', **{
        '$select': 'document_id',
        '$where': f"borough={soql_quote(boro)} AND block={soql_quote(block)} AND lot={soql_quote(lot)}",
        '$limit': 500})
    doc_ids = sorted({r['document_id'] for r in legals if r.get('document_id')})
    print(f"  legals: {len(doc_ids)} distinct documents")
    if not doc_ids:
        return
    masters = client.get_batched('acris_master', 'document_id', doc_ids,
                                 select='document_id,doc_type,document_date,document_amt')
    print(f"  master (batched): {len(masters)} rows for {len(doc_ids)} ids")
    deeds = sorted((m for m in masters if 'DEED' in (m.get('doc_type') or '')),
                   key=lambda m: m.get('document_date') or '', reverse=True)
    if not deeds:
        print("  no deeds on this BBL — rerun with a BBL that has sold")
        return
    deed = deeds[0]
    print(f"  latest deed {deed['document_id']} ({deed.get('document_date', '?')[:10]}, "
          f"${deed.get('document_amt', '?')})")
    parties = client.get('acris_parties', **{
        '$where': f"document_id={soql_quote(deed['document_id'])}", '$limit': 50})
    for p in parties:
        print(f"    party_type={p.get('party_type')}  {p.get('name', '')[:50]}")
    print("  -> confirm on acris.nyc.gov: party_type=1 should be the GRANTOR/seller,")
    print("     party_type=2 the GRANTEE/buyer for this document")
safe(acris_check, "acris")

section("4. ACRIS document control codes (role source)")
def codes_check():
    roles = load_party_roles(client)
    print(f"  {len(roles)} doc types loaded")
    for dt in ('DEED', 'MTGE', 'SAT', 'AGMT', 'ASST'):
        print(f"  {dt}: {roles.get(dt)}")
safe(codes_check, "doc codes")

section("5. V5 — RPAD year column and vintages for the golden BBL")
def rpad_check():
    cols = client.get_columns('rpad')
    year_cols = [c for c in cols if 'year' in c or c in ('yr', 'yr4', 'fin_yr')]
    print(f"  year-ish columns: {year_cols or 'NONE'}")
    rows = client.get('rpad', **{
        '$where': f"boro={soql_quote(boro)} AND block={soql_quote(block)} AND lot={soql_quote(lot)}",
        '$limit': 10})
    print(f"  rows for lot: {len(rows)}")
    for r in rows[:5]:
        keys = {k: r.get(k) for k in list(year_cols)[:2]}
        print(f"    owner={str(r.get('owner'))[:30]:30s} avtot={r.get('avtot')} {keys}")
safe(rpad_check, "rpad")

section("6. B4 — lien-sale cycle column sample values")
def lien_check():
    rows = client.get('tax_lien_sale', **{'$limit': 5, '$order': ':id DESC'})
    if rows:
        print(f"  columns: {sorted(rows[0].keys())}")
        for r in rows[:3]:
            print(f"  sample: month={r.get('month')} cycle={r.get('cycle')} water={r.get('water_debt_only')}")
safe(lien_check, "lien sale")

section("7. HPD complaints — distinct complaint counting sanity")
def complaints_check():
    rows = client.get('hpd_complaints', **{
        '$select': 'complaint_id,complaint_status',
        '$where': f"bbl={soql_quote(GOLDEN_BBL)}", '$limit': 2000})
    distinct = {r.get('complaint_id') for r in rows if r.get('complaint_id')}
    print(f"  problem rows: {len(rows)}  distinct complaints: {len(distinct)}")
safe(complaints_check, "complaints")

section("8. New dataset shapes (first-row keys, for field-mapping confirmation)")
for key in ('hpd_litigation', 'evictions', 'exemptions', 'speculation_watch',
            'dob_complaints', 'dob_co_bis', 'dob_co_now', 'fisp_facades',
            'll84_energy', 'rolling_sales', 'acris_deeds_view', 'acris_references',
            'acris_remarks'):
    rows = safe(lambda k=key: client.get(k, **{'$limit': 1}), key)
    if rows:
        print(f"  {key}: {sorted(rows[0].keys())}")
    elif rows is not None:
        print(f"  {key}: reachable, 0 rows returned")

section("9. Golden-BBL assertions (each should be > 0 for a busy Manhattan lot)")
checks = [
    ('pluto', {'$where': f"bbl={soql_quote(GOLDEN_BBL)}", '$limit': 1}),
    ('hpd_violations', {'$where': f"boroid={soql_quote(boro)} AND block={soql_quote(block)} AND lot={soql_quote(lot)}", '$select': ':id', '$limit': 1000}),
    ('dob_permits_bis', {'$where': f"borough='MANHATTAN' AND block={soql_quote(block_p)}", '$select': ':id', '$limit': 10}),
    ('dob_complaints', {'$limit': 1}),
]
for key, params in checks:
    rows = safe(lambda k=key, p=params: client.get(k, **p), key)
    if rows is not None:
        print(f"  {key}: {len(rows)} rows")

section("10. Stop-work-order dataset discovery (paste this list back)")
def swo_check():
    req = urllib.request.Request(
        "https://api.us.socrata.com/api/catalog/v1?domains=data.cityofnewyork.us&q=stop%20work%20order&limit=8",
        headers={'User-Agent': 'dataset-verify/1.0'})
    with urllib.request.urlopen(req, timeout=20) as resp:
        results = json.load(resp).get('results', [])
    for r in results:
        res = r.get('resource', {})
        print(f"  {res.get('id')}  {res.get('name', '')[:60]}")
safe(swo_check, "swo discovery")

print("\nDone. Paste this entire output back.")
