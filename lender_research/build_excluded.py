#!/usr/bin/env python3
"""List every researched lender that is NOT on dscr_rtl_lenders.csv, with the reason.

Reasons come from three places:
  * research records judged consumer-only, defunct/acquired, or unverifiable (low confidence)
  * NYC lenders set aside before research as clearly out of scope (agency multifamily, CMBS
    conduits, CDFIs, a timeshare lender...). Those reasons are the analyst's classification,
    not researched, and are labelled as such.
NYC lenders whose identity or products could not be confirmed are in nyc_unconfirmed_lenders.csv.
"""
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(HERE, "research")
listed = {s for row in csv.DictReader(open(os.path.join(HERE, "..", "dashboard_html", "data", "dscr_rtl_lenders.csv"))) for s in row["sources"].split("|")}

rows = []
for fname, tag in (("national_dscr.jsonl", "D"), ("national_rtl.jsonl", "R")):
    for line in open(os.path.join(R, fname)):
        r = json.loads(line)
        if f"{tag}:{r['lender']}" in listed:
            continue
        # a lender may be listed under the other national file's key
        if any(k.split(":", 1)[1] == r["lender"] for k in listed if k[0] in "DR"):
            continue
        rows.append({"lender": r["lender"], "source": "national research", "status": r.get("status") or "",
                     "confidence": r.get("confidence") or "", "reason": (r.get("notes") or "")[:300]})
for b in (1, 2, 3):
    for line in open(os.path.join(R, f"nyc_batch_{b}_results.jsonl")):
        r = json.loads(line)
        if f"N:{r['idx']}" in listed:
            continue
        if r.get("category") in ("consumer_mortgage", "commercial_permanent", "other") and r.get("confidence") in ("high", "medium"):
            rows.append({"lender": r.get("company") or " | ".join(r["acris_entities"]), "source": "NYC research",
                         "status": r.get("status") or "", "confidence": r.get("confidence") or "",
                         "reason": f"category {r.get('category')}: " + (r.get("notes") or "")[:260]})
for x in json.load(open(os.path.join(R, "nyc_excluded.json"))):
    rows.append({"lender": " | ".join(x["entities"]), "source": "NYC pre-research screen",
                 "status": "", "confidence": "", "reason": "analyst classification, not researched: " + x["reason"]})
out = os.path.join(HERE, "excluded_lenders.csv")
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(len(rows), "excluded lenders ->", out)
