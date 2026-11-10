#!/usr/bin/env python3
"""Behaviour of NYC lenders that were researched but whose products could not be confirmed.
These are NOT tagged DSCR/RTL; the numbers only describe how their recorded loans behave."""
import csv, json, re, sqlite3, sys
from collections import Counter
from datetime import date

HERE = sys.argv[1]
OUT = sys.argv[2]
cands = {i: c for i, c in enumerate(json.load(open(f"{HERE}/research/nyc_candidates.json")))}
res = {}
for b in (1, 2, 3):
    for line in open(f"{HERE}/research/nyc_batch_{b}_results.jsonl"):
        r = json.loads(line)
        res[int(r["idx"])] = r
curated = {s.split(":")[1] for row in csv.DictReader(open(f"{HERE}/../dashboard_html/data/dscr_rtl_lenders.csv"))
           for s in row["sources"].split("|") if s.startswith("N:")}
EXCLUDE_CATS = {"consumer_mortgage", "commercial_permanent"}


def norm(n):
    return re.sub(r"\s+", " ", re.sub(r"[^\w& ]", " ", (n or "").upper())).strip()


con = sqlite3.connect(f"{HERE}/nyc_cohort.db")
lender_docs = {}
for doc, n in con.execute("SELECT document_id, name FROM party WHERE party_type='2'"):
    lender_docs.setdefault(norm(n), set()).add(doc)
rows = []
for idx, r in sorted(res.items()):
    if str(idx) in curated or r.get("category") in EXCLUDE_CATS:
        continue
    c = cands[idx]
    rx = re.compile(r"\b(" + "|".join(re.escape(e) for e in c["entities"]) + r")\b")
    docs = set().union(*[d for n, d in lender_docs.items() if rx.search(n)] or [set()])
    n = s24 = asg = 0
    for doc in docs:
        crfn, rec = con.execute("SELECT crfn, recorded FROM master WHERE document_id=?", (doc,)).fetchone()
        refs = con.execute("SELECT doc_type, recorded FROM ref WHERE crfn=?", (crfn,)).fetchall()
        sats = [date.fromisoformat(x[:10]) for t, x in refs if t == "SAT" and x]
        n += 1
        s24 += bool(sats and (min(sats) - date.fromisoformat(rec)).days <= 730)
        asg += any(t in ("ASST", "ASPM") for t, _ in refs)
    rows.append({
        "acris_entities": " | ".join(c["entities"]), "acris_address": c["acris_address"],
        "research_result": r.get("category"), "research_confidence": r.get("confidence"),
        "company_if_identified": r.get("company") or "", "research_note": (r.get("notes") or "")[:250],
        "nyc_loans_24mo": c["nyc_loans_24mo"], "entity_borrower_share": c["entity_borrower_share"],
        "median_loan": c["median_loan"], "one_to_four_family_share": c["one_to_four_share"],
        "cohort_2021_23_loans": n, "paid_off_within_24mo": round(s24 / n, 2) if n else "",
        "assigned": round(asg / n, 2) if n else "",
    })
rows.sort(key=lambda x: -x["nyc_loans_24mo"])
with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(len(rows), "unconfirmed lender groups ->", OUT)
for x in rows[:15]:
    print(f"  {x['nyc_loans_24mo']:>4} {x['acris_entities'][:40]:<40} {x['research_result']:<12} cohort={x['cohort_2021_23_loans']:>4} paid24={x['paid_off_within_24mo']} asst={x['assigned']}")
