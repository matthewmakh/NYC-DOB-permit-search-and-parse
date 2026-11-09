#!/usr/bin/env python3
"""Merge the research records into one curated DSCR / RTL lender list with ACRIS match rules.

Every lender in the output must have product evidence (verbatim quote + URL) from a research
record at high or medium confidence. Match rules (recorded-name regex, ZIPs) are hand-written
from the names that actually appear on NYC mortgages. Regexes run on names normalised to
upper case with punctuation (except &) replaced by spaces.
"""
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "dscr_rtl_lenders.csv")


def load(path, key):
    out = {}
    for line in open(os.path.join(HERE, "research", path)):
        r = json.loads(line)
        out[str(r[key])] = r
    return out


NAT = {**load("national_dscr.jsonl", "lender")}
for k, v in load("national_rtl.jsonl", "lender").items():
    NAT["RTL:" + k] = v
NYC = {}
for b in (1, 2, 3):
    NYC.update(load(f"nyc_batch_{b}_results.jsonl", "idx"))

# (display name, [source keys], acris name regex, ZIPs, overrides)
#   source keys: "D:<lender>" national DSCR file, "R:<lender>" national RTL file, "N:<idx>" NYC batch
#   overrides: oo=True marks a lender that also makes owner-occupied consumer loans (loan-level DSCR
#   tag then requires an entity borrower); group= forces the product group; addr= address regex.
S = [
    # ---- national non-QM / DSCR wholesale ----
    ("Angel Oak Mortgage Solutions", ["D:Angel Oak Mortgage Solutions"], r"\bANGEL OAK MORTGAGE SOLUTIONS\b", "30328", {}),
    ("Deephaven Mortgage", ["D:Deephaven Mortgage"], r"\bDEEPHAVEN MORTGAGE\b", "28277", {}),
    ("Acra Lending (Citadel Servicing)", ["D:Acra Lending"], r"\bCITADEL SERVICING\b|\bACRA LENDING\b", "92618", {"oo": True}),
    ("A&D Mortgage", ["D:A&D Mortgage"], r"\bA ?& ?D MORTGAGE\b", "33309|33020", {"oo": True}),
    ("NQM Funding", ["D:NQM Funding"], r"\bNQM FUNDING\b", "33431", {"oo": True}),
    ("HomeXpress Mortgage", ["D:HomeXpress Mortgage"], r"\bHOMEXPRESS MORTGAGE\b", "92705", {}),
    ("Newfi Wholesale (Nexera Holding)", ["D:Newfi Wholesale"], r"\bNEXERA HOLDING\b|\bNEWFI\b", "94608", {"oo": True}),
    ("Defy Mortgage", ["D:Defy Mortgage"], r"\bDEFY MORTGAGE\b", "37212", {}),
    ("Change Lending (Change Wholesale)", ["D:Change Lending"], r"\bCHANGE LENDING\b", "92808", {}),
    ("Carrington Mortgage Services", ["D:Carrington Mortgage Services"], r"\bCARRINGTON MORTGAGE SERVICES?\b", "92806|92868", {"oo": True}),
    ("Champions Funding", ["D:Champions Funding"], r"\bCHAMPIONS FUNDING\b", "85297", {"oo": True}),
    ("LoanStream (OCMBC)", ["D:LoanStream Mortgage"], r"\bOCMBC\b|\bLOANSTREAM\b", "92612", {"oo": True}),
    ("Emporium TPO", ["D:Emporium TPO"], r"\bEMPOR[OI]+UM TPO\b", "75034", {}),
    ("Arc Home", ["D:Arc Home"], r"\bARC HOME\b", "08057", {"oo": True}),
    ("Verus (Verus Residential Loanco)", ["D:Verus Mortgage Capital"], r"\bVERUS RESIDENTIAL LOANCO\b", "20036|55425", {}),
    ("Newrez", ["D:Newrez"], r"\bNEWREZ\b", "19034|29601", {"oo": True}),
    ("Griffin Funding", ["D:Griffin Funding"], r"\bGRIFFIN FUNDING\b", "92101", {}),
    ("Truss Financial Group", ["D:Truss Financial Group"], r"\bTRUSS FINANCIAL\b", "92630", {}),
    ("Dominion Financial Services", ["D:Dominion Financial Services", "R:Dominion Financial"], r"\bDOMINION FINANCIAL SERVICES\b", "21202", {}),
    ("LendSure Mortgage", ["D:LendSure Mortgage"], r"\bLENDSURE MORTGAGE\b", "92128", {}),
    ("CV3 Financial", ["D:CV3 Financial"], r"\bCV3 FINANCIAL\b", "90245", {}),
    ("Kind Lending", ["D:Kind Lending"], r"\bKIND LENDING\b", "92614", {"oo": True}),
    ("Hometown Equity Mortgage (theLender)", ["D:Hometown Equity Mortgage"], r"\bHOMETOWN EQUITY MORTGAGE\b", "92630", {"oo": True}),
    ("Plaza Home Mortgage", ["D:Plaza Home Mortgage"], r"\bPLAZA HOME MORTGAGE\b", "92121", {"oo": True}),
    ("Velocity Commercial Capital", ["D:Velocity Commercial Capital", "R:Velocity Mortgage Capital"], r"\bVELOCITY COMM\w* CAPITAL\b", "91361|92653", {}),
    ("Visio Lending (Visio Financial Services / Investor Mortgage Finance)", ["D:Visio Lending", "N:36"], r"\bVISIO FINANCIAL SERVICES\b|\bINVESTOR MORTGAGE FINANCE\b", "78758", {}),
    ("Kiavi", ["R:Kiavi", "D:Kiavi"], r"\bKIAVI FUNDING\b", "94105|15212", {}),
    ("Figure Lending (Kiavi DSCR from 2026-09; also consumer HELOCs)", ["D:Kiavi"], r"\bFIGURE LENDING\b", "28202|28217", {"oo": True}),
    ("Asset Based Lending (ABL)", ["R:Asset Based Lending (ABL)", "D:Asset Based Lending"], r"\bABL RPC RESIDENTIAL CREDIT\b|\bASSET BASED LENDING\b", "07302|08002", {}),
    ("Brick City Capital (Lev Cap)", ["D:Brick City Capital"], r"\bLEV CAP\b", "07102", {}),
    ("Lima One Capital", ["R:Lima One Capital", "D:Lima One Capital"], r"\bLIMA ONE CAPITAL\b", "29601|92612", {}),
    ("CoreVest Finance", ["R:CoreVest Finance", "D:CoreVest Finance"], r"\bCOREVEST AMERICAN FINAN\w* LEND\w*\b", "92614|10111", {}),
    ("RCN Capital", ["R:RCN Capital", "D:RCN Capital"], r"\bRCN CAPITAL\b", "06074|28209", {}),
    ("Roc360 platform (Roc Capital, Loan Funder LLC series, lenders at 645 Madison Ave 19th Fl)",
     ["R:Roc Capital / Roc360", "D:Roc Capital"], r"\bROC CAPITAL\b|\bLOAN FUNDERS?\b", "10022",
     {"addr": r"\b645 MADISON\b", "conf": "medium",
      "note": "Address-based: Roc's own terms page gives 645 Madison Avenue, 19th Floor; Roc says it is a capital provider to private lenders. Loan Funder LLC linked to Roc360 by a PincusCo record."}),
    ("Sharestates", ["R:Sharestates", "D:Sharestates"], r"\bSHARESTATES\b", "11021", {}),
    ("LendingOne", ["R:LendingOne", "D:LendingOne"], r"\bLENDINGONE\b", "33431|33487", {}),
    ("Easy Street Capital", ["R:Easy Street Capital", "D:Easy Street Capital"], r"\bEASY STREET CAPITAL\b", "78702", {}),
    ("Axos Bank", ["D:Axos Bank"], r"\bAXOS BANK\b", "92122", {"oo": True}),
    ("CIVIC Financial Services (Roc360)", ["R:CIVIC Financial Services", "D:CIVIC Financial Services"], r"\bCIVIC FINANCIAL SERVICES\b", "10022", {}),
    ("Northpointe Bank", ["D:Northpointe Bank"], r"\bNORTHPOINTE BANK\b", "49546", {"oo": True}),
    ("Mega Capital Funding", ["D:Mega Capital Funding"], r"\bMEGA CAPITAL FUNDING\b", "91302", {"oo": True}),
    ("Temple View Capital", ["R:Temple View Capital", "D:Temple View Capital"], r"\bTEMPLE VIEW CAPITAL\b", "20814", {}),
    ("5th Street Capital", ["D:5th Street Capital"], r"\b5TH STREET CAPITAL\b", "92108", {}),
    ("Allied Mortgage Group", ["D:Allied Mortgage Group"], r"\bALLIED MORTGAGE GROUP\b", "19004", {"oo": True}),
    ("Park Place Finance", ["R:Park Place Finance", "D:Park Place Finance"], r"\bPARK PLACE FINANCE\b", "78746|78731", {}),
    ("eLEND (American Financial Resources)", ["D:eLEND (American Financial Resources)"], r"\bAMERICAN FINANCIAL RESOURCES\b", "07054|08701", {"oo": True}),
    ("Lendz Financial", ["D:Lendz Financial"], r"\bLENDZ FINANCIAL\b", "33301", {"oo": True}),
    # ---- national RTL / investor lenders ----
    ("Anchor Loans", ["R:Anchor Loans"], r"\bANCHOR LOANS\b|\bANCHOR ASSETS\b", "91362", {}),
    ("Groundfloor", ["R:Groundfloor"], r"\bGROUNDFLOOR\b", "30361", {}),
    ("Upright (Fund That Flip)", ["R:Upright (formerly Fund That Flip)"], r"\bFUND THAT FLIP\b", "44114", {}),
    ("Builders Capital (Construction Loan Services II)", ["R:Builders Capital"], r"\bCONSTRUCTION LOAN SERVICES II\b", "98374|33301", {}),
    ("New Silver Lending", ["R:New Silver"], r"\bNEW SILVER LENDING\b", "06107|06117|06177", {}),
    ("Genesis Capital", ["R:Genesis Capital"], r"\bGENESIS CAPITAL\b(?! ONE)", "91403|28203", {}),
    ("Toorak Capital Partners (loan buyer)", ["R:Toorak Capital Partners"], r"\bTOORAK CAPITAL PAR\w*\b", "07901|33602", {"alt": "1001 WATER"}),
    ("Rehab Financial Group", ["R:Rehab Financial Group"], r"\bREHAB FINANCIAL GROUP\b", "19010", {}),
    ("Sachem Capital", ["R:Sachem Capital"], r"\bSACHEM CAPITAL\b", "06405", {}),
    ("Kennedy Funding", ["R:Kennedy Funding"], r"\bKENNEDY FUNDING\b", "07631", {}),
    ("Constructive Capital (Constructive Loans / BPL Mortgage)", ["R:Constructive Capital (Constructive Loans)", "N:23"], r"\bCONSTRUCTIVE LOANS\b|\bBPL MORTGAGE\b", "60181", {}),
    ("Center Street Lending", ["R:Center Street Lending"], r"\bCENTER STREET LENDING\b", "92612", {}),
    ("ArchWest Capital", ["R:ArchWest Capital"], r"\bARCHWEST (FUNDING|LENDING|CAPITAL)\b", "10018|92612", {}),
    ("Noble Mortgage & Investments", ["R:Noble Mortgage & Investments"], r"\bNOBLE MORTGAGE & INVESTMENTS\b", "77079", {"oo": True}),
    ("Express Capital Financing (Express Capital Holdings)", ["R:Express Capital Financing"], r"\bEXPRESS CAPITAL (HOLDINGS|FINANCING)\b", "11232|11235|07764", {}),
    ("Stratton Equities", ["R:Stratton Equities"], r"\bSTRATTON EQUITIES\b", "07058", {}),
    ("Arixa Capital", ["R:Arixa Capital"], r"\bARIXA\b", "85018", {}),
    ("Churchill Real Estate (Churchill Finance)", ["R:Churchill Real Estate / Churchill Finance"], r"\bCHURCHILL (FINANCE|FUNDING I|MRA FUNDING I)\b", "28209|28203", {"alt": "450 W 14TH"}),
    ("Ignite Funding", ["R:Ignite Funding"], r"\bIGNITE FUNDING\b", "89119", {}),
    ("Vaster", ["R:Vaster"], r"\bVASTER\b", "", {"alt": "1000 BRICKELL"}),
    ("Sherman Bridge", ["R:Sherman Bridge"], r"\bSHERMAN BRIDGE\b", "75039", {}),
    ("EquityMax", ["R:EquityMax"], r"\bEQUITYMAX\b", "", {}),
    ("Cogo Capital (Secured Investment Corp)", ["R:Cogo Capital"], r"\bSECURED INVESTMENT CORP\b|\bCOGO CAPITAL\b", "", {}),
    ("Gelt Financial", ["R:Gelt Financial"], r"\bGELT FINANCIAL\b", "33487", {}),
    ("HouseMax Funding", ["R:HouseMax Funding"], r"\bHOUSEMAX FUNDING\b", "78746", {}),
    # ---- NYC-area lenders identified from ACRIS ----
    ("IceCap Group (Ice Lender Holdings)", ["N:1"], r"\bICE LENDER HOLDINGS\b", "10018", {}),
    ("S3 Capital", ["N:2", "N:34"], r"\bS3 (SB REAL ESTATE CREDIT|RE FUNDING|RE PERPETUAL YIELD FUNDING)\b", "10022", {}),
    ("Battalion Lending", ["N:4"], r"\bBATTALION LENDING\b", "11226", {}),
    ("Manhattan Bridge Capital", ["N:5"], r"\bMANHATTAN BRIDGE CAPITAL\b", "11021", {}),
    ("FM Home Loans", ["N:7"], r"\bFM HOME LOANS\b", "11210", {"oo": True}),
    ("States Capital / We Lend", ["N:9"], r"\bSTATES CAPITAL\b|\bWE LEND\b", "11418|11428|10022", {}),
    ("Washington Equity & Funding", ["N:10"], r"\bWASHINGTON EQUITY (AND|&) FUNDING\b", "11023", {}),
    ("Bayport Funding", ["N:11"], r"\bBAYPORT FUNDING\b", "11021", {}),
    ("Logan Finance", ["N:12"], r"\bLOGAN FINANCE\b", "11788", {"oo": True}),
    ("Emigrant Funding", ["N:13"], r"\bEMIGRANT FUNDING\b", "10017", {}),
    ("BX Capital (Broad X Funding)", ["N:14"], r"\bBROAD X FUNDING\b", "10018", {}),
    ("Cake Mortgage", ["N:17"], r"\bCAKE MORTGAGE\b", "91311", {"oo": True}),
    ("CrossCountry Mortgage", ["N:18"], r"\bCROSS ?COUNTRY MORTGAGE\b", "44114", {"oo": True}),
    ("American Financial Network / Orion Lending", ["N:19"], r"\bAMERICAN FINANCIAL NETWORK\b|\bORION LENDING\b", "92868", {"oo": True, "alt": "10 POINTE"}),
    ("Derby Copeland Capital", ["N:20", "N:24"], r"\b(DCC|DERBY) REMMCF\b|\bFCR DC JV HOLDINGS\b", "10010", {}),
    ("Conventus", ["N:21"], r"\bCONVENTUS\b", "94104", {"alt": "111 PONTRERO|111 POTRERO"}),
    ("Stormfield Capital", ["N:22"], r"\bSTORMFIELD (SPV|CAPITAL FUNDING)\b|\bSECURE BRIDGE FUND\b", "06890", {}),
    ("Insula Capital Group", ["N:25"], r"\bINSULA CAPITAL GROUP\b", "10022", {"alt": "627 HORSEBLOCK"}),
    ("STG Mortgage", ["N:26"], r"\bSTG MORTGAGE\b", "92612", {"oo": True}),
    ("Wisdom Capital", ["N:27"], r"\bWISDOM (EQUITIES|VENTURES)\b", "11042", {}),
    ("Maxim Capital Group", ["N:28"], r"\bMAXIM CREDIT GROUP\b", "10022", {}),
    ("BridgeCity Capital", ["N:31"], r"\bBCC FUNDING (CRF|II)\b|\bBRIDGECITY\b|\bBRIDGE CITY FUNDING\b", "11232", {}),
    ("Clout Capital", ["N:32"], r"\bCLOUT CAPITAL\b", "11553|11514",
     {"oo": False, "note": "Shares a research record with Cliffco (same building); Clout's own site offers investor DSCR and fix-and-flip loans, so the consumer flag is not applied."}),
    ("Cliffco Mortgage Bankers", ["N:32"], r"\bCLIFFCO\b", "11553", {"oo": True}),
    ("Community Preservation Corp (CPC)", ["N:33"], r"\bCPC FUNDING SPE\b|\bCOMMUNITY PRESERVATION CORP\w*\b", "10017", {}),
    ("Foundation Mortgage", ["N:35"], r"\bFOUNDATION MORTGAGE\b", "33139", {"oo": True}),
    ("Essencap", ["N:38"], r"\bESSENCAP FUNDING\b|\bCOMMERCIAL ELEMENT\b", "11021", {}),
    ("Accolend", ["N:40"], r"\bACCOLEND\b", "07652", {}),
    ("United Wholesale Mortgage", ["N:42"], r"\bUNITED WHOLESALE MORTGAGE\b", "48341", {"oo": True}),
    ("Arion Fund", ["N:43"], r"\bARION FUND\b", "10977", {}),
    ("Hakimian Capital", ["N:46"], r"\bHAKIMIAN PARTNERS\b", "11021", {}),
    ("Rosewood Lending", ["N:47"], r"\bROSEWOOD LENDING\b", "10021", {}),
    ("Nationwide Mortgage Bankers", ["N:51"], r"\bNATIONWIDE MORTGAGE BANKERS\b", "11747", {"oo": True}),
    ("Ark Mortgage", ["N:52"], r"\bARK MORTGAGE\b", "10901", {"oo": True}),
    ("Meadowbrook Financial Mortgage Bankers", ["N:55"], r"\bMEADOWBROOK FINANCIAL MORTGAGE BANKERS\b", "11590", {"oo": True}),
    ("Lendvent", ["N:56"], r"\bLENDVENT\b", "33154", {}),
    ("Emerald Creek Capital", ["N:58"], r"\bEMERALD CREEK CAPITAL\b", "10022", {}),
    ("Flatiron Realty Capital", ["N:60"], r"\bFRC VTX\b|\bFLATIRON REALTY CAPITAL\b", "11021", {}),
    ("YJV Capital (Yellow Jacket Ventures)", ["N:66"], r"\bYJV CAPITAL\b", "11210", {}),
    ("Salman Capital Funding", ["N:67"], r"\bSCF LN HLDGS\b|\bSALMAN CAPITAL\b", "07306", {}),
    ("Malve Capital", ["N:68"], r"\bMALVE (FUND|CAPITAL)\b", "11050", {}),
    ("Golden Bridge Funding", ["N:71"], r"\bGOLDEN BRIDGE R2\b|\bGOLDEN BRIDGE FUNDING\b", "11375", {}),
    ("American Heritage Lending", ["N:79"], r"\bAMERICAN HERITAGE LENDING\b", "92612", {"oo": True}),
    ("Fairbridge", ["N:88"], r"\bFAIRBRIDGE CREDIT\b", "06820", {}),
    ("Nvestor Funding", ["N:90"], r"\bNVESTOR FUNDING\b", "85283", {}),
    ("RBI Private Lending", ["N:91"], r"\bRBI PRIVATE LENDING\b", "33180", {}),
    ("Luxury Mortgage", ["N:94"], r"\bLUXURY MORTGAGE\b", "06901", {"oo": True}),
    ("Crosby Lending", ["N:95"], r"\bCROSBY LENDING\b", "33139", {}),
    ("Planet Capital (Planet Management Group)", ["N:108"], r"\bPLANET MANAGEMENT GROUP\b", "11747", {}),
    ("Unitas Funding", ["N:113"], r"\bUNITAS FUNDING\b", "07016", {}),
    ("Fortress Credit", ["N:119"], r"\bFORTRESS CREDIT\b", "10105", {}),
    ("Smart Capital", ["N:126"], r"\bSMART CAPITAL NY\b", "11211", {}),
    ("MF1 Capital", ["N:129"], r"\bMF1 CAPITAL\b", "02108", {}),
    ("JG Funding", ["N:133"], r"\bJG FUNDING\b", "10314", {}),
    ("Spring Garden Lending", ["N:146"], r"\bSPRING GARDEN LENDING\b", "19123", {}),
]

PRODUCTS = ("dscr_rental", "rtl_fix_flip_bridge", "ground_up_construction", "owner_occupied_residential", "commercial_bridge")
RANK = {"high": 3, "medium": 2, "low": 1}


def rec(key):
    kind, k = key.split(":", 1)
    if kind == "D":
        return NAT.get(k)
    if kind == "R":
        return NAT.get("RTL:" + k)
    return NYC.get(k)


rows, problems = [], []
for name, keys, rx, zips, ov in S:
    re.compile(rx)
    recs = [rec(k) for k in keys]
    missing = [k for k, r in zip(keys, recs) if r is None]
    recs = [r for r in recs if r]
    if missing:
        problems.append(f"{name}: missing source {missing}")
    if not recs:
        continue
    prods = {p: any((r.get("products") or {}).get(p) is True for r in recs) for p in PRODUCTS}
    ev = [e for r in recs for e in (r.get("evidence") or []) if e.get("url") and e.get("quote")]
    conf = max((r.get("confidence") or "low" for r in recs), key=lambda c: RANK.get(c, 0))
    conf = ov.get("conf", conf)
    cats = {r.get("category") for r in recs if r.get("category")}
    if "oo" in ov:
        oo = ov["oo"]          # explicit override (e.g. a record shared by two firms at one address)
    else:
        oo = prods["owner_occupied_residential"] or bool(cats & {"non_qm_wholesale", "consumer_mortgage"})
    dscr, rtl = prods["dscr_rental"], prods["rtl_fix_flip_bridge"] or prods["ground_up_construction"]
    if ov.get("group"):
        group = ov["group"]
    elif oo and dscr:
        group = "CONSUMER+DSCR"
    elif cats & {"commercial_bridge", "commercial_permanent"} and not dscr:
        group = "COMMERCIAL_BRIDGE"
    elif dscr and rtl:
        group = "DSCR+RTL"
    elif dscr:
        group = "DSCR"
    elif rtl:
        group = "RTL"
    else:
        group = "NONE"
    if conf not in ("high", "medium") or group == "NONE":
        problems.append(f"{name}: excluded (confidence={conf}, group={group})")
        continue
    website = next((r.get("website") for r in recs if r.get("website")), "")
    ents = []
    for r in recs:
        for e in r.get("legal_entities") or []:
            ents.append(e.get("name") if isinstance(e, dict) else str(e))
    ev_prod = [e for e in ev if e.get("product") in PRODUCTS]
    rows.append({
        "lender": name, "group": group, "confidence": conf,
        "dscr": "Y" if dscr else "N", "rtl": "Y" if prods["rtl_fix_flip_bridge"] else "N",
        "construction": "Y" if prods["ground_up_construction"] else "N",
        "commercial_bridge": "Y" if prods["commercial_bridge"] else "N",
        "owner_occupied": "Y" if oo else "N",
        "acris_regex": rx, "acris_address_regex": ov.get("addr", ""), "acris_zips": zips,
        "verified_alt_streets": ov.get("alt", ""),
        "website": website or "",
        "legal_entities": " ; ".join(dict.fromkeys(ents))[:300],
        "evidence_1_url": ev_prod[0]["url"] if ev_prod else "", "evidence_1_quote": ev_prod[0]["quote"] if ev_prod else "",
        "evidence_2_url": ev_prod[1]["url"] if len(ev_prod) > 1 else "", "evidence_2_quote": ev_prod[1]["quote"] if len(ev_prod) > 1 else "",
        "evidence_count": len(ev_prod),
        "sources": "|".join(keys),
        "notes": (ov.get("note", "") + " " + " ".join((r.get("notes") or "")[:200] for r in recs)).strip()[:500],
    })

with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(f"{len(rows)} lenders written to {OUT}")
from collections import Counter
print(Counter(r["group"] for r in rows), Counter(r["confidence"] for r in rows))
print("lenders with < 1 product quote:", [r["lender"] for r in rows if r["evidence_count"] < 1])
for p in problems:
    print("NOTE:", p)
