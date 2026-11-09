#!/usr/bin/env python3
"""Find investor and private-capital candidates from public ACRIS records.

Reads only NYC Open Data (no database needed), so it can be run anywhere:

    python3 discover_investors.py --months 12 --out investor_discovery_out

Two views, both built from recorded documents:

* Non-bank lenders: mortgage (MTGE) lenders that are not on the institutional
  list. Hard-money lenders, debt funds and private lenders live here.
* Repeat buyers: deed (DEED) buyers that purchase more than once in the window,
  grouped by name and, more usefully, by mailing address, because sponsors
  reuse one address across many single-purpose LLCs.

ACRIS party types (verified against the ACRIS document control codes table):
DEED: 1 = seller, 2 = buyer.  MTGE: 1 = borrower, 2 = lender.

UCC filings are NOT in the ACRIS open-data feed, so they are not used here.

Individuals are left out of the output unless --include-individuals is given.
"""

import argparse
import csv
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://data.cityofnewyork.us/resource"
MASTER = f"{BASE}/bnx9-e6tj.json"
PARTIES = f"{BASE}/636b-3b5g.json"
LEGALS = f"{BASE}/8h5j-fqxa.json"
PAGE = 50000
ID_CHUNK = 250

BOROUGHS = {"1": "Manhattan", "2": "Bronx", "3": "Brooklyn", "4": "Queens", "5": "Staten Island"}

# Lenders that are institutions, servicers or government, not private capital.
# "CAPITAL", "FUNDING", "LENDING", "FINANCE" are deliberately NOT here: that is
# where hard-money lenders and debt funds are.
INSTITUTION_RE = re.compile(
    r"\b(BANK|BANC|BANCORP|NATIONAL ASSOCIATION|N\.?A\.?|SAVINGS|CREDIT UNION|FEDERAL|"
    r"MORTGAGE ELECTRONIC|MERS|FANNIE|FREDDIE|FHLMC|FNMA|SECRETARY OF|HOUSING|"
    r"CITY OF NEW YORK|STATE OF NEW YORK|DORMITORY|HFA|HDC|NYC |NEW YORK CITY|"
    r"CITI|CITIBANK|CHASE|WELLS FARGO|JPMORGAN|MORGAN STANLEY|GOLDMAN|HSBC|SANTANDER|TRUST COMPANY|"
    r"SIGNATURE|FLAGSTAR|QUICKEN|ROCKET|PENNYMAC|LOANDEPOT|NEWREZ|SERVIC|TRUSTEE|"
    r"CAPITAL ONE|TD |PNC|KEYBANK|M&T|CITIZENS|AMALGAMATED)\b"
)
# Residential retail originators: real lenders, but not the private-capital kind we are after.
RETAIL_LENDER_RE = re.compile(r"\b(MORTGAGE|HOME LOANS?|HOME LENDING|WHOLESALE|BANKERS|RESIDENTIAL)\b")
# Registered-agent / mail-drop ZIPs that show up as "mailing addresses" for many unrelated LLCs.
AGENT_ZIPS = {"12207", "19801", "19808", "19711", "10005"}
ENTITY_RE = re.compile(
    r"\b(LLC|L\.?L\.?C\.?|INC|CORP|CORPORATION|LP|L\.?P\.?|LLP|LTD|TRUST|FUND|FUNDS|CAPITAL|"
    r"HOLDINGS?|PARTNERS|PARTNERSHIP|REALTY|PROPERT(Y|IES)|GROUP|VENTURES?|EQUIT(Y|IES)|"
    r"ASSOCIATES|DEVELOPMENT|MANAGEMENT|INVESTORS?|INVESTMENTS?|ACQUISITIONS?|FUNDING|"
    r"LENDING|FINANCE|FINANCIAL|ASSETS?|COMPANY|CO)\b"
)
# Buyers that are not investors in the sense we want: lenders taking back REO,
# government and housing agencies, co-op/condo boards.
NON_INVESTOR_BUYER_RE = re.compile(
    r"\b(BANK|MORTGAGE|CITIMORTGAGE|TRUSTEE|NATIONAL ASSOCIATION|CITY OF NEW YORK|STATE OF NEW YORK|HOUSING|"
    r"HPD|HDC|HDFC|CONDOMINIUM|BOARD OF MANAGERS|OWNERS CORP|COOPERATIVE|SECRETARY OF|"
    r"FEDERAL|MORTGAGE ELECTRONIC|FANNIE|FREDDIE|CHURCH|SYNAGOGUE|CONGREGATION)\b"
)
# Mailing addresses that belong to counsel or title agents rather than the sponsor.
COUNSEL_ADDR_RE = re.compile(
    r"\b(ESQ|ESQUIRE|ATTORNEY|ATTY|LAW OFFICES?|LAW FIRM|P\.?C\.?|PLLC|TITLE|ABSTRACT|"
    r"LAW GROUP|LLP)\b"
)


def make_session():
    s = requests.Session()
    retry = Retry(total=6, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
    token = os.getenv("NYC_OPEN_DATA_APP_TOKEN")
    if token:
        s.headers["X-App-Token"] = token
    return s


SESSION = make_session()


def soql(url, params):
    r = SESSION.get(url, params=params, timeout=90)
    r.raise_for_status()
    return r.json()


def paged(url, params):
    out, offset = [], 0
    while True:
        batch = soql(url, {**params, "$limit": PAGE, "$offset": offset})
        out.extend(batch)
        if len(batch) < PAGE:
            return out
        offset += PAGE


def norm_name(name):
    n = re.sub(r"[^\w&/ ]", " ", (name or "").upper())
    return re.sub(r"\s+", " ", n).strip()


def norm_street(a1, a2, zip_code):
    """Street-level key. ACRIS often puts 'C/O X' in address_1 and the street in address_2."""
    cand = [x for x in (a1, a2) if x]
    street = next((x for x in cand if re.match(r"\s*\d", x)), cand[0] if cand else "")
    s = re.sub(r"[^\w ]", " ", street.upper())
    s = re.sub(r"\b(SUITE|STE|FLOOR|FL|APT|UNIT|ROOM|RM)\b.*$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    z = (zip_code or "")[:5]
    return f"{s}|{z}" if s else ""


def is_metro_zip(key):
    """True when the street key ends in a NY / NJ / CT ZIP (private NYC lenders are local;
    retail originators are mostly out of state)."""
    z = key.split("|")[-1]
    return z[:2] in {"10", "11", "12", "13", "14", "06", "07", "08"}


def is_entity(name):
    return bool(ENTITY_RE.search(norm_name(name)))


def fetch_master(doc_type, since, min_amt):
    where = (f"doc_type='{doc_type}' AND recorded_datetime >= '{since}T00:00:00' "
             f"AND document_amt >= {min_amt}")
    rows = paged(MASTER, {"$select": "document_id,document_amt,recorded_datetime,recorded_borough,percent_trans",
                          "$where": where, "$order": "document_id"})
    return {r["document_id"]: r for r in rows}


def fetch_parties(doc_ids, party_type):
    ids = list(doc_ids)
    chunks = [ids[i:i + ID_CHUNK] for i in range(0, len(ids), ID_CHUNK)]

    def one(chunk):
        in_list = ",".join(f"'{d}'" for d in chunk)
        return soql(PARTIES, {"$where": f"party_type='{party_type}' AND document_id in ({in_list})",
                              "$select": "document_id,name,address_1,address_2,city,state,zip",
                              "$limit": PAGE})

    out = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for n, res in enumerate(pool.map(one, chunks), 1):
            out.extend(res)
            if n % 20 == 0:
                print(f"    parties {n}/{len(chunks)} chunks", file=sys.stderr)
    return out


def fetch_buildings(doc_ids):
    """document_id -> set of (borough, street_number, street_name) from ACRIS Legals."""
    ids = list(doc_ids)
    chunks = [ids[i:i + ID_CHUNK] for i in range(0, len(ids), ID_CHUNK)]

    def one(chunk):
        in_list = ",".join(f"'{d}'" for d in chunk)
        return soql(LEGALS, {"$where": f"document_id in ({in_list})",
                             "$select": "document_id,borough,street_number,street_name,block,lot",
                             "$limit": PAGE})

    out = defaultdict(set)
    with ThreadPoolExecutor(max_workers=6) as pool:
        for rows in pool.map(one, chunks):
            for r in rows:
                street = (r.get("street_number") or "").strip(), (r.get("street_name") or "").strip().upper()
                key = (r.get("borough"),) + street if any(street) else (r.get("borough"), r.get("block"), "")
                out[r["document_id"]].add(key)
    return out


def money(x):
    return int(float(x)) if x not in (None, "") else 0


def lender_view(since, min_amt, include_individuals):
    print("Mortgages: fetching masters...", file=sys.stderr)
    masters = fetch_master("MTGE", since, min_amt)
    print(f"  {len(masters):,} mortgages >= ${min_amt:,}", file=sys.stderr)
    parties = fetch_parties(masters, "2")
    groups = defaultdict(list)
    for p in parties:
        name = norm_name(p.get("name"))
        if not name or INSTITUTION_RE.search(name):
            continue
        if not include_individuals and not is_entity(name):
            continue
        groups[name].append(p)
    rows = []
    for name, ps in groups.items():
        amts = [money(masters[p["document_id"]].get("document_amt")) for p in ps]
        boros = Counter(BOROUGHS.get(masters[p["document_id"]].get("recorded_borough"), "?") for p in ps)
        addr = Counter(norm_street(p.get("address_1"), p.get("address_2"), p.get("zip")) for p in ps)
        top_addr = addr.most_common(1)[0][0]
        rows.append({
            "kind": ("retail-mortgage" if RETAIL_LENDER_RE.search(name)
                     else "private/bridge candidate" if is_metro_zip(top_addr)
                     else "out-of-state (likely national/retail)"),
            "lender": name, "loans": len(ps), "total_usd": sum(amts),
            "median_usd": int(statistics.median(amts)), "max_usd": max(amts),
            "recorded_at": "; ".join(f"{b}:{n}" for b, n in boros.most_common()),
            "top_address": top_addr, "sample_document_id": ps[0]["document_id"],
        })
    rows.sort(key=lambda r: (r["kind"] != "private/bridge candidate", -r["loans"], -r["total_usd"]))
    return rows, len(masters), len(parties)


def buyer_view(since, min_amt, include_individuals, min_deals):
    print("Deeds: fetching masters...", file=sys.stderr)
    masters = fetch_master("DEED", since, min_amt)
    print(f"  {len(masters):,} deeds >= ${min_amt:,}", file=sys.stderr)
    parties = fetch_parties(masters, "2")
    by_name, by_addr = defaultdict(list), defaultdict(list)
    skipped_counsel = 0
    for p in parties:
        name = norm_name(p.get("name"))
        if not name or NON_INVESTOR_BUYER_RE.search(name):
            continue
        if not include_individuals and not is_entity(name):
            continue
        by_name[name].append(p)
        raw_addr = f"{p.get('address_1') or ''} {p.get('address_2') or ''}".upper()
        key = norm_street(p.get("address_1"), p.get("address_2"), p.get("zip"))
        if key:
            if COUNSEL_ADDR_RE.search(raw_addr):
                skipped_counsel += 1
            else:
                by_addr[key].append(p)

    def summarize(ps):
        amts = [money(masters[p["document_id"]].get("document_amt")) for p in ps]
        boros = Counter(BOROUGHS.get(masters[p["document_id"]].get("recorded_borough"), "?") for p in ps)
        return amts, boros

    # Candidate groups first, then one legals lookup for just their documents, so a
    # developer selling 18 condo units in one building is not counted as 18 deals.
    cand_names = {n: ps for n, ps in by_name.items() if len({p["document_id"] for p in ps}) >= min_deals}
    cand_addrs = {k: ps for k, ps in by_addr.items()
                  if len({p["document_id"] for p in ps}) >= min_deals
                  and len({norm_name(p.get("name")) for p in ps}) >= 2}
    needed = {p["document_id"] for ps in list(cand_names.values()) + list(cand_addrs.values()) for p in ps}
    buildings = fetch_buildings(needed)

    def distinct_buildings(ps):
        return len({b for p in ps for b in buildings.get(p["document_id"], ())})

    name_rows = []
    for name, ps in cand_names.items():
        nb = distinct_buildings(ps)
        if nb < min_deals:
            continue
        amts, boros = summarize(ps)
        name_rows.append({"buyer": name, "purchases": len({p["document_id"] for p in ps}),
                          "distinct_buildings": nb, "total_usd": sum(amts),
                          "recorded_at": "; ".join(f"{b}:{n}" for b, n in boros.most_common()),
                          "sample_document_id": ps[0]["document_id"]})
    name_rows.sort(key=lambda r: (-r["distinct_buildings"], -r["total_usd"]))

    addr_rows = []
    for key, ps in cand_addrs.items():
        nb = distinct_buildings(ps)
        if nb < min_deals:
            continue
        entities = {norm_name(p.get("name")) for p in ps}
        trusty = sum(bool(re.search(r"\b(TRUST|ESTATE|FAMILY|REVOCABLE)\b", e)) for e in entities)
        flags = []
        if trusty / len(entities) >= 0.3:
            flags.append("trust-heavy: likely law/estate firm")
        if key.split("|")[-1] in AGENT_ZIPS:
            flags.append("registered-agent zip")
        amts, boros = summarize(ps)
        addr_rows.append({"flags": "; ".join(flags), "mailing_address": key,
                          "distinct_buildings": nb, "distinct_entities": len(entities),
                          "total_usd": sum(amts),
                          "recorded_at": "; ".join(f"{b}:{n}" for b, n in boros.most_common()),
                          "example_entities": " | ".join(sorted(entities)[:4]),
                          "sample_document_id": ps[0]["document_id"]})
    addr_rows.sort(key=lambda r: (bool(r["flags"]), -r["distinct_buildings"], -r["total_usd"]))
    return name_rows, addr_rows, len(masters), len(parties), skipped_counsel


def write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--min-amount", type=int, default=500000, help="minimum document amount in USD")
    ap.add_argument("--min-deals", type=int, default=3, help="minimum purchases to count as a repeat buyer")
    ap.add_argument("--out", default="investor_discovery_out")
    ap.add_argument("--include-individuals", action="store_true")
    ap.add_argument("--skip", choices=["lenders", "buyers"], help="skip one of the two views")
    args = ap.parse_args()

    latest = soql(MASTER, {"$select": "max(recorded_datetime) as latest"})[0]["latest"][:10]
    since = (datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=30 * args.months)).strftime("%Y-%m-%d")
    print(f"ACRIS data through {latest}; window {since} -> {latest}", file=sys.stderr)
    os.makedirs(args.out, exist_ok=True)

    if args.skip != "lenders":
        rows, n_docs, n_parties = lender_view(since, args.min_amount, args.include_individuals)
        write_csv(os.path.join(args.out, "non_bank_lenders.csv"), rows)
        print(f"\n== Non-bank lenders ({n_docs:,} mortgages, {n_parties:,} lender rows, {len(rows):,} distinct) ==")
        for r in [x for x in rows if x["kind"].startswith("private")][:25]:
            print(f"{r['loans']:>4} loans  ${r['total_usd']:>13,}  {r['lender'][:48]:<48}  {r['top_address'][:40]}")

    if args.skip != "buyers":
        names, addrs, n_docs, n_parties, skipped = buyer_view(
            since, args.min_amount, args.include_individuals, args.min_deals)
        write_csv(os.path.join(args.out, "repeat_buyers_by_name.csv"), names)
        write_csv(os.path.join(args.out, "repeat_buyers_by_address.csv"), addrs)
        print(f"\n== Repeat buyers by name ({n_docs:,} deeds, {n_parties:,} buyer rows) ==")
        for r in names[:15]:
            print(f"{r['distinct_buildings']:>4} bldgs  ${r['total_usd']:>13,}  {r['buyer'][:60]}")
        print(f"\n== Sponsor clusters: one mailing address, several entities "
              f"({skipped:,} buyer rows at counsel/title addresses excluded) ==")
        for r in addrs[:25]:
            print(f"{r['distinct_buildings']:>3} bldgs {r['distinct_entities']:>3} ent  ${r['total_usd']:>13,}  "
                  f"{r['mailing_address'][:40]:<40} {('[' + r['flags'][:18] + ']') if r['flags'] else ''} "
                  f"{r['example_entities'][:60]}")
    print(f"\nCSV files written to {args.out}/", file=sys.stderr)


if __name__ == "__main__":
    main()
