#!/usr/bin/env python3
"""Cache NYC ACRIS mortgages (MTGE >= $100k, 24 months) with all parties and legals in SQLite."""
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

B = "https://data.cityofnewyork.us/resource"
DB = sys.argv[1]
SINCE, UNTIL = sys.argv[2], sys.argv[3]          # e.g. 2024-10-01 2026-09-30
MIN_AMT = int(sys.argv[4]) if len(sys.argv) > 4 else 100000
DOC_TYPE = sys.argv[5] if len(sys.argv) > 5 else "MTGE"
PAGE, CH = 50000, 200

S = requests.Session()
S.mount("https://", HTTPAdapter(max_retries=Retry(total=8, backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504)), pool_maxsize=16))


def get(url, params):
    for attempt in range(5):
        try:
            r = S.get(url, params=params, timeout=120)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            if attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))


con = sqlite3.connect(DB)
con.executescript("""
CREATE TABLE IF NOT EXISTS master(document_id TEXT PRIMARY KEY, crfn TEXT, doc_type TEXT, amt REAL,
    recorded TEXT, doc_date TEXT, rec_borough TEXT);
CREATE TABLE IF NOT EXISTS party(document_id TEXT, party_type TEXT, name TEXT, a1 TEXT, a2 TEXT,
    city TEXT, state TEXT, zip TEXT);
CREATE TABLE IF NOT EXISTS legal(document_id TEXT, borough TEXT, block TEXT, lot TEXT, property_type TEXT,
    street_number TEXT, street_name TEXT, unit TEXT);
CREATE INDEX IF NOT EXISTS ix_party_doc ON party(document_id);
CREATE INDEX IF NOT EXISTS ix_legal_doc ON legal(document_id);
""")

# 1. masters
offset, n = 0, 0
where = (f"doc_type='{DOC_TYPE}' AND document_amt >= {MIN_AMT} AND recorded_datetime >= '{SINCE}T00:00:00' "
         f"AND recorded_datetime <= '{UNTIL}T23:59:59'")
while True:
    rows = get(f"{B}/bnx9-e6tj.json", {"$where": where, "$order": "document_id", "$limit": PAGE, "$offset": offset,
               "$select": "document_id,crfn,doc_type,document_amt,recorded_datetime,document_date,recorded_borough"})
    con.executemany("INSERT OR IGNORE INTO master VALUES (?,?,?,?,?,?,?)",
                    [(r["document_id"], r.get("crfn"), r.get("doc_type"), float(r.get("document_amt") or 0),
                      (r.get("recorded_datetime") or "")[:10], (r.get("document_date") or "")[:10],
                      r.get("recorded_borough")) for r in rows])
    con.commit()
    n += len(rows)
    print(f"masters {n:,}", file=sys.stderr, flush=True)
    if len(rows) < PAGE:
        break
    offset += PAGE

ids = [r[0] for r in con.execute("SELECT document_id FROM master WHERE document_id NOT IN "
                                 "(SELECT DISTINCT document_id FROM party)")]
chunks = [ids[i:i + CH] for i in range(0, len(ids), CH)]


def parties(ch):
    inl = ",".join(f"'{d}'" for d in ch)
    return get(f"{B}/636b-3b5g.json", {"$where": f"document_id in ({inl})", "$limit": PAGE,
               "$select": "document_id,party_type,name,address_1,address_2,city,state,zip"})


def legals(ch):
    inl = ",".join(f"'{d}'" for d in ch)
    return get(f"{B}/8h5j-fqxa.json", {"$where": f"document_id in ({inl})", "$limit": PAGE,
               "$select": "document_id,borough,block,lot,property_type,street_number,street_name,unit"})


with ThreadPoolExecutor(max_workers=6) as pool:
    for i, rows in enumerate(pool.map(parties, chunks), 1):
        con.executemany("INSERT INTO party VALUES (?,?,?,?,?,?,?,?)",
                        [(r["document_id"], r.get("party_type"), r.get("name"), r.get("address_1"),
                          r.get("address_2"), r.get("city"), r.get("state"), r.get("zip")) for r in rows])
        if i % 50 == 0:
            con.commit()
            print(f"parties {i}/{len(chunks)}", file=sys.stderr, flush=True)
    con.commit()
    ids = [r[0] for r in con.execute("SELECT document_id FROM master WHERE document_id NOT IN "
                                     "(SELECT DISTINCT document_id FROM legal)")]
    chunks = [ids[i:i + CH] for i in range(0, len(ids), CH)]
    for i, rows in enumerate(pool.map(legals, chunks), 1):
        con.executemany("INSERT INTO legal VALUES (?,?,?,?,?,?,?,?)",
                        [(r["document_id"], r.get("borough"), r.get("block"), r.get("lot"), r.get("property_type"),
                          r.get("street_number"), r.get("street_name"), r.get("unit")) for r in rows])
        if i % 50 == 0:
            con.commit()
            print(f"legals {i}/{len(chunks)}", file=sys.stderr, flush=True)
    con.commit()

for t in ("master", "party", "legal"):
    print(t, con.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
