#!/usr/bin/env python3
"""For every cohort mortgage, find later documents that reference it by CRFN
(satisfaction, assignment, consolidation/CEMA, modification) and store them."""
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

B = "https://data.cityofnewyork.us/resource"
DB = sys.argv[1]
CH = 200
S = requests.Session()
S.mount("https://", HTTPAdapter(max_retries=Retry(total=8, backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504)), pool_maxsize=16))


def get(url, params):
    for attempt in range(5):
        try:
            r = S.get(url, params=params, timeout=120)
            r.raise_for_status()
            return r.json()
        except Exception:  # noqa: BLE001
            if attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))


con = sqlite3.connect(DB)
con.execute("CREATE TABLE IF NOT EXISTS ref(crfn TEXT, ref_doc TEXT, doc_type TEXT, recorded TEXT)")
crfns = [r[0] for r in con.execute("SELECT DISTINCT crfn FROM master WHERE crfn IS NOT NULL AND crfn != ''")]
chunks = [crfns[i:i + CH] for i in range(0, len(crfns), CH)]


def refs(ch):
    inl = ",".join(f"'{c}'" for c in ch)
    return get(f"{B}/pwkr-dpni.json", {"$select": "document_id,reference_by_crfn_", "$limit": 50000,
               "$where": f"reference_by_crfn_ in ({inl})"})


by_doc = defaultdict(set)
with ThreadPoolExecutor(max_workers=6) as pool:
    for i, rows in enumerate(pool.map(refs, chunks), 1):
        for r in rows:
            by_doc[r["document_id"]].add(r["reference_by_crfn_"])
        if i % 50 == 0:
            print(f"refs {i}/{len(chunks)}", file=sys.stderr, flush=True)
    docs = list(by_doc)
    dchunks = [docs[i:i + CH] for i in range(0, len(docs), CH)]

    def masters(ch):
        inl = ",".join(f"'{d}'" for d in ch)
        return get(f"{B}/bnx9-e6tj.json", {"$select": "document_id,doc_type,recorded_datetime", "$limit": 50000,
                   "$where": f"document_id in ({inl})"})

    out = []
    for i, rows in enumerate(pool.map(masters, dchunks), 1):
        for m in rows:
            for c in by_doc[m["document_id"]]:
                out.append((c, m["document_id"], m.get("doc_type"), (m.get("recorded_datetime") or "")[:10]))
        if i % 50 == 0:
            print(f"ref masters {i}/{len(dchunks)}", file=sys.stderr, flush=True)
con.executemany("INSERT INTO ref VALUES (?,?,?,?)", out)
con.commit()
print("referencing docs:", len(out))
print(con.execute("SELECT doc_type, count(*) FROM ref GROUP BY doc_type ORDER BY 2 DESC LIMIT 12").fetchall())
