#!/usr/bin/env python3
"""Tag NYC mortgages with the curated DSCR/RTL lender list, then test the tags.

Pre-registered expectations (written before looking at results):
  E1 name-match precision: >= 98% of name matches carry the lender's known ZIP or a MERS ZIP.
  E2 business purpose: >= 85% of loans from pure business-purpose lenders (no owner-occupied
     product) go to entity borrowers; the citywide baseline is far lower.
  E3 loan life: loans from RTL lenders are paid off within 24 months at >= 2x the rate of
     loans from DSCR lenders (2021-07..2023-06 cohort, satisfactions linked by CRFN).
  E4 purchase loans: RTL-only lenders have a higher share of purchase-money mortgages (a deed on the
     same lot recorded within 5 days) than DSCR-only lenders.
"""
import csv
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date

LIST, MTGE_DB, DEED_DB, COHORT_DB, OUTDIR = sys.argv[1:6]
MERS_ZIPS = {"48501", "68164", "20190", "48502", "65201"}
ENTITY_RE = re.compile(r"\b(LLC|L L C|INC|CORP|CORPORATION|LP|L P|LTD|LLP|PLLC|COMPANY|HOLDINGS?|PARTNERS|"
                       r"ASSOCIATES|REALTY|PROPERT(Y|IES)|EQUIT(Y|IES)|VENTURES?|GROUP|DEVELOPMENT|MANAGEMENT)\b")


def norm(name):
    n = re.sub(r"[^\w& ]", " ", (name or "").upper())
    return re.sub(r"\s+", " ", n).strip()


lenders = []
for r in csv.DictReader(open(LIST)):
    r["rx"] = re.compile(r["acris_regex"])
    r["zips"] = {z.strip() for z in r["acris_zips"].split("|") if z.strip()}
    r["arx"] = re.compile(r["acris_address_regex"]) if r.get("acris_address_regex") else None
    lenders.append(r)


def street2(a1, a2):
    """House number + first street word, e.g. '2945 TOWNSGATE' (ignores which field it was keyed in)."""
    for f in (a1, a2):
        t = norm(f).split()
        if len(t) >= 2 and t[0][0].isdigit():
            return " ".join(t[:2])
    return norm(a1 or a2)[:24]


def match(name, addr="", z5=""):
    n, a = norm(name), norm(addr)
    return [l for l in lenders if l["rx"].search(n) or (l["arx"] and l["arx"].search(a) and z5 in l["zips"])]


def tag_db(db_path):
    """document_id -> (lender row, zip_ok). Also returns per-lender name review rows."""
    con = sqlite3.connect(db_path)
    hits, review = {}, defaultdict(Counter)
    for doc, name, a1, a2, z in con.execute("SELECT document_id, name, a1, a2, zip FROM party WHERE party_type='2'"):
        z5 = (z or "")[:5]
        for l in match(name, f"{a1 or ''} {a2 or ''}", z5):
            ok = z5 in l["zips"] or z5 in MERS_ZIPS
            review[l["lender"]][(norm(name), z5, ok, street2(a1, a2))] += 1
            prev = hits.get(doc)
            if prev is None or (ok and not prev[1]):
                hits[doc] = (l, ok)
    return con, hits, review


def borrower_is_entity(con, doc):
    return any(ENTITY_RE.search(norm(n)) for (n,) in
               con.execute("SELECT name FROM party WHERE document_id=? AND party_type='1'", (doc,)))


def lots(con, doc):
    return {(b, bl.lstrip("0"), lo.lstrip("0")) for b, bl, lo in
            con.execute("SELECT borough, block, lot FROM legal WHERE document_id=?", (doc,)) if bl and lo}


def d(s):
    return date.fromisoformat(s[:10])


def group_of(l):
    return l["group"]


def loan_tag(g, ent):
    if g == "CONSUMER+DSCR":
        return "DSCR (entity borrower, lender also does consumer loans)" if ent else "consumer lender, individual borrower (not tagged)"
    if g == "DSCR+RTL":
        return "DSCR or RTL (lender makes both)"
    return g


out = open(f"{OUTDIR}/test_report.txt", "w")


def say(*a):
    print(*a)
    print(*a, file=out)


say(__doc__)

# ---- current 24-month mortgages -------------------------------------------------
con, hits, review = tag_db(MTGE_DB)
total = con.execute("SELECT count(*) FROM master").fetchone()[0]
all_docs = [r[0] for r in con.execute("SELECT document_id FROM master")]
base_ent = sum(borrower_is_entity(con, doc) for doc in all_docs)
say(f"NYC mortgages >= $100k, 2024-10..2026-09: {total:,}; entity-borrower share citywide {base_ent / total:.1%}")
say(f"Tagged by the list: {len(hits):,} ({len(hits) / total:.1%})")

# E1 precision
say("\nE1 name-match precision (ZIP check)")
n_ok = n_all = 0
with open(f"{OUTDIR}/name_match_review.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["lender", "matched_name", "zip", "zip_ok", "street", "rows"])
    for lender, c in sorted(review.items()):
        for (name, z5, ok, st), k in c.most_common():
            w.writerow([lender, name, z5, ok, st, k])
            n_all += k
            n_ok += k * ok
say(f"  {n_ok:,}/{n_all:,} matched rows carry a known lender ZIP or MERS ZIP = {n_ok / max(n_all, 1):.1%}"
    f"  -> {'PASS' if n_ok / max(n_all, 1) >= 0.98 else 'FAIL'} (threshold 98%)")
# E1b: classify every off-ZIP row by rule (no judgement calls)
known_streets = defaultdict(set)
lenders_at = defaultdict(set)
for l, c in review.items():
    for (nm, z, ok, st), k in c.items():
        lenders_at[st].add(l)
        if ok:
            known_streets[l].add(st)
alt = {l["lender"]: [a for a in l.get("verified_alt_streets", "").split("|") if a] for l in lenders}
cls = Counter()
unresolved = []
for l, c in review.items():
    for (nm, z, ok, st), k in c.items():
        if ok:
            continue
        if st in known_streets[l]:
            cls["same street as confirmed rows (ZIP typo)"] += k
        elif any(st.startswith(a) for a in alt.get(l, [])):
            cls["alternate address verified on lender's own site"] += k
        elif len(lenders_at[st]) >= 3:
            cls["shared processing address (3+ unrelated lenders)"] += k
        else:
            cls["unresolved"] += k
            unresolved.append((k, l, nm, z, st))
say("E1b off-ZIP rows classified by rule:")
for kname, k in cls.most_common():
    say(f"    {k:>5,}  {kname}")
prec = 1 - cls["unresolved"] / max(n_all, 1)
say(f"  precision counting every unresolved row as a false match: {prec:.1%} -> {'PASS' if prec >= 0.98 else 'FAIL'} (threshold 98%)")
with open(f"{OUTDIR}/unresolved_matches.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["rows", "lender", "matched_name", "zip", "street"])
    for row in sorted(unresolved, reverse=True):
        w.writerow(row)
for k, l, nm, z, st in sorted(unresolved, reverse=True)[:12]:
    say(f"    unresolved: x{k:<4} {l[:34]:<34} {nm[:40]:<40} {z} {st}")

# deeds for E4
dcon = sqlite3.connect(DEED_DB)
deed_lots = defaultdict(list)
for doc, rec in dcon.execute("SELECT document_id, recorded FROM master"):
    for lot in lots(dcon, doc):
        deed_lots[lot].append(d(rec))

per = defaultdict(lambda: Counter())
loan_tags = Counter()
with open(f"{OUTDIR}/tagged_loans.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["document_id", "recorded", "amount", "lender", "lender_group", "loan_tag", "entity_borrower",
                "purchase_money", "zip_ok"])
    for doc, (l, ok) in hits.items():
        amt, rec = con.execute("SELECT amt, recorded FROM master WHERE document_id=?", (doc,)).fetchone()
        ent = borrower_is_entity(con, doc)
        pm = any(abs((x - d(rec)).days) <= 5 for lot in lots(con, doc) for x in deed_lots.get(lot, []))
        g = group_of(l)
        tag = loan_tag(g, ent)
        loan_tags[tag] += 1
        p = per[l["lender"]]
        p["loans"] += 1
        p["entity"] += ent
        p["pm"] += pm
        p["group:" + g] = 1
        w.writerow([doc, rec, int(amt), l["lender"], g, tag, ent, pm, ok])
say("\nLoan-level tags (24 months):")
for t, k in loan_tags.most_common():
    say(f"  {k:>6,}  {t}")

# E2 business purpose
say("\nE2 entity-borrower share by lender group (citywide baseline {:.1%})".format(base_ent / total))
grp = defaultdict(Counter)
for lname, p in per.items():
    g = next(k[6:] for k in p if k.startswith("group:"))
    grp[g]["loans"] += p["loans"]
    grp[g]["entity"] += p["entity"]
    grp[g]["pm"] += p["pm"]
for g, c in sorted(grp.items()):
    say(f"  {g:<34} loans {c['loans']:>6,}  entity {c['entity'] / c['loans']:.0%}  purchase-money {c['pm'] / c['loans']:.0%}")
pure = [g for g in grp if g != "CONSUMER+DSCR"]
ent_pure = sum(grp[g]["entity"] for g in pure) / max(sum(grp[g]["loans"] for g in pure), 1)
say(f"  pure business-purpose lenders entity share {ent_pure:.1%} -> {'PASS' if ent_pure >= 0.85 else 'FAIL'} (threshold 85%)")

# E4
r_pm = grp["RTL"]["pm"] / max(grp["RTL"]["loans"], 1)
d_pm = grp["DSCR"]["pm"] / max(grp["DSCR"]["loans"], 1)
say(f"\nE4 purchase-money share: RTL-only {r_pm:.0%} vs DSCR-only {d_pm:.0%} -> {'PASS' if r_pm > d_pm else 'FAIL'}")

# ---- E3 loan life on the 2021-23 cohort ----------------------------------------------
ccon, chits, _ = tag_db(COHORT_DB)
life = defaultdict(Counter)
per_life = defaultdict(Counter)
for doc, (l, ok) in chits.items():
    crfn, rec = ccon.execute("SELECT crfn, recorded FROM master WHERE document_id=?", (doc,)).fetchone()
    refs = ccon.execute("SELECT doc_type, recorded FROM ref WHERE crfn=?", (crfn,)).fetchall()
    sats = [d(r) for t, r in refs if t == "SAT" and r]
    first_sat = min(sats) if sats else None
    g = group_of(l)
    tagc = loan_tag(g, borrower_is_entity(ccon, doc))
    proxy = "DSCR-proxy (DSCR lenders + entity loans of consumer/DSCR lenders)" if tagc in (
        "DSCR", "DSCR (entity borrower, lender also does consumer loans)") else None
    for bucket in (life[g], per_life[l["lender"]]) + ((life[proxy],) if proxy else ()):
        bucket["n"] += 1
        bucket["sat24"] += bool(first_sat and (first_sat - d(rec)).days <= 730)
        bucket["sat_any"] += bool(first_sat)
        bucket["asst"] += any(t in ("ASST", "ASPM") for t, _ in refs)
        bucket["cons"] += any(t in ("M&CON", "MCON", "AGMT", "SPRD") for t, _ in refs)
say("\nE3 loan life, mortgages recorded 2021-07..2023-06, status as of 2026-09")
for g, c in sorted(life.items()):
    say(f"  {g:<34} n {c['n']:>5,}  paid off <=24mo {c['sat24'] / c['n']:.0%}  paid off ever {c['sat_any'] / c['n']:.0%}"
        f"  assigned {c['asst'] / c['n']:.0%}  consolidated/modified {c['cons'] / c['n']:.0%}")
r24 = life["RTL"]["sat24"] / max(life["RTL"]["n"], 1)
for dk in ("DSCR", "DSCR-proxy (DSCR lenders + entity loans of consumer/DSCR lenders)"):
    d24 = life[dk]["sat24"] / max(life[dk]["n"], 1)
    say(f"  RTL {r24:.0%} vs {dk} {d24:.0%} -> {'PASS' if r24 >= 2 * max(d24, 0.001) else 'FAIL'} (needs >= 2x)")

# per-lender behaviour table for self-check
with open(f"{OUTDIR}/lender_behaviour.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["lender", "group", "loans_24mo", "entity_share", "purchase_money_share",
                "cohort_n", "paid_off_24mo", "paid_off_ever", "assigned", "consolidated"])
    for l in lenders:
        p, c = per.get(l["lender"], Counter()), per_life.get(l["lender"], Counter())
        w.writerow([l["lender"], group_of(l), p["loans"], round(p["entity"] / p["loans"], 2) if p["loans"] else "",
                    round(p["pm"] / p["loans"], 2) if p["loans"] else "", c["n"],
                    round(c["sat24"] / c["n"], 2) if c["n"] else "", round(c["sat_any"] / c["n"], 2) if c["n"] else "",
                    round(c["asst"] / c["n"], 2) if c["n"] else "", round(c["cons"] / c["n"], 2) if c["n"] else ""])
out.close()
