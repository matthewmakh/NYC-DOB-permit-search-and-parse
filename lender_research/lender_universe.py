#!/usr/bin/env python3
"""Rank NYC mortgage lenders from the cache, with the signals that separate business-purpose
(DSCR / RTL) lenders from consumer lenders: share of loans to entity borrowers, property mix."""
import csv
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict

DB, OUT = sys.argv[1], sys.argv[2]
con = sqlite3.connect(DB)

MERS_RE = re.compile(r"MORTGAGE ELECTRONIC REGISTRATION|\bMERS\b")
ENTITY_RE = re.compile(r"\b(LLC|L L C|INC|CORP|CORPORATION|LP|L P|LTD|LLP|PLLC|COMPANY|HOLDINGS?|PARTNERS|"
                       r"ASSOCIATES|REALTY|PROPERT(Y|IES)|EQUIT(Y|IES)|VENTURES?|GROUP|DEVELOPMENT|MANAGEMENT)\b")
SUFFIX_RE = re.compile(r"\b(ISAOA|ATIMA|ISAOA ATIMA|ITS SUCCESSORS AND( OR)? ASSIGNS.*|AS NOMINEE.*|"
                       r"A [A-Z ]*(CORPORATION|LIMITED LIABILITY COMPANY|BANKING CORPORATION)$)")
TAIL_RE = re.compile(r"\b(LLC|L L C|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LP|L P|LTD|N A|NA|FSB|DBA)\b")
BANK_RE = re.compile(r"\b(BANK|BANCORP|SAVINGS|CREDIT UNION|NATIONAL ASSOCIATION|FEDERAL|TRUST COMPANY|"
                     r"CITY OF NEW YORK|HOUSING|HPD|HDC|SECRETARY OF)\b")
RESI = {"D1", "D2", "D3", "D4", "D5", "D6", "F1", "F4", "F5", "FS", "RG", "RP", "RV", "SC", "MC", "SM",
        "AP", "R1", "R2", "VI", "VR"}
ONE_TO_FOUR = {"D1", "D2", "D3", "D4", "F1", "FS", "RG", "RP", "RV", "R1"}


def norm(name):
    n = re.sub(r"[^\w& ]", " ", (name or "").upper())
    n = re.sub(r"\s+", " ", n).strip()
    return SUFFIX_RE.sub("", n).strip()


def key(name):
    k = TAIL_RE.sub(" ", norm(name))
    return re.sub(r"\s+", " ", k).strip()


def street_key(a1, a2, z):
    cand = [x for x in (a1, a2) if x]
    st = next((x for x in cand if re.match(r"\s*\d", x)), cand[0] if cand else "")
    st = re.sub(r"[^\w ]", " ", st.upper())
    st = re.sub(r"\b(SUITE|STE|FLOOR|FL|APT|UNIT|ROOM|RM)\b.*$", "", st)
    st = re.sub(r"\s+", " ", st).strip()
    return f"{st}|{(z or '')[:5]}"


docs = {r[0]: {"amt": r[1], "rec": r[2]} for r in con.execute("SELECT document_id, amt, recorded FROM master")}
lenders, borrowers = defaultdict(list), defaultdict(list)
for doc, pt, name, a1, a2, city, st, z in con.execute(
        "SELECT document_id, party_type, name, a1, a2, city, state, zip FROM party"):
    if pt == "2":
        lenders[doc].append((name, street_key(a1, a2, z), st))
    elif pt == "1":
        borrowers[doc].append(name)
ptype = defaultdict(set)
for doc, t in con.execute("SELECT document_id, property_type FROM legal"):
    ptype[doc].add(t)

agg = defaultdict(lambda: {"loans": 0, "entity": 0, "mers": 0, "amts": [], "resi": 0, "one4": 0,
                           "names": Counter(), "addrs": Counter(), "states": Counter()})
for doc, ls in lenders.items():
    has_mers = any(MERS_RE.search(n or "") for n, _, _ in ls)
    ent = any(ENTITY_RE.search(norm(b)) for b in borrowers.get(doc, []))
    seen = set()
    for name, addr, st in ls:
        if not name or MERS_RE.search(name):
            continue
        k = key(name)
        if not k or k in seen:
            continue
        seen.add(k)
        a = agg[k]
        a["loans"] += 1
        a["entity"] += ent
        a["mers"] += has_mers
        a["amts"].append(docs[doc]["amt"])
        a["resi"] += bool(ptype[doc] & RESI)
        a["one4"] += bool(ptype[doc] & ONE_TO_FOUR)
        a["names"][norm(name)] += 1
        a["addrs"][addr] += 1
        a["states"][st or ""] += 1

rows = []
for k, a in agg.items():
    if a["loans"] < 3:
        continue
    rows.append({
        "lender_key": k, "loans": a["loans"], "entity_borrower_loans": a["entity"],
        "entity_share": round(a["entity"] / a["loans"], 3), "mers_share": round(a["mers"] / a["loans"], 3),
        "residential_share": round(a["resi"] / a["loans"], 3), "one_to_four_share": round(a["one4"] / a["loans"], 3),
        "median_amt": int(statistics.median(a["amts"])), "total_amt": int(sum(a["amts"])),
        "is_bank_like": bool(BANK_RE.search(k)), "top_state": a["states"].most_common(1)[0][0],
        "top_address": a["addrs"].most_common(1)[0][0],
        "name_variants": " | ".join(n for n, _ in a["names"].most_common(6)),
    })
rows.sort(key=lambda r: -r["entity_borrower_loans"])
with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(f"{len(docs):,} mortgages, {len(rows):,} lenders with >=3 loans -> {OUT}")
