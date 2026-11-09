# DSCR / RTL lender list for NYC mortgages

A curated list of lenders that make **DSCR loans** (long-term investor rental loans, qualified on
rent) and **RTL loans** (residential transition loans: fix-and-flip, bridge, rehab, ground-up
construction), with rules for finding their loans in NYC ACRIS mortgage records.

ACRIS does not record loan type, term, rate or maturity, so a mortgage can only be tagged by
**who the lender is**. Each lender on the list is backed by a verbatim quote from a page showing the
product, plus the URL of that page.

## Files

| File | What it is |
|---|---|
| `dscr_rtl_lenders.csv` | **The list: 127 lenders** (119 high confidence, 8 medium), with match rules and evidence |
| `excluded_lenders.csv` | 50 lenders researched and left off, with the reason (consumer-only, defunct, unverifiable, out of scope) |
| `nyc_unconfirmed_lenders.csv` | 55 NYC lender groups with real volume whose products could not be confirmed; loan behaviour only, **not tagged** |
| `research/*.jsonl` | Raw research records: every product claim with URL and verbatim quote, legal entities, HQ address, notes |
| `test_results/` | Test report, per-lender behaviour, every name match with its ZIP check, tagged loans |
| `*.py` | Scripts to rebuild the caches, the list and the tests (see "Re-running") |

### Columns in `dscr_rtl_lenders.csv`

- `group`
  - `RTL` (21): short-term investor lending only, including ground-up construction.
  - `DSCR+RTL` (46): makes both. A loan from one of these can't be split by lender name alone.
  - `DSCR` (5): investor rental lending only.
  - `CONSUMER+DSCR` (44): also makes owner-occupied home loans, so only **entity-borrower** loans are tagged.
  - `COMMERCIAL_BRIDGE` (11): larger commercial or multifamily bridge lenders.
- `dscr`, `rtl`, `construction`, `commercial_bridge`, `owner_occupied`: Y/N per product, from the evidence.
- `acris_regex`: regex for the lender name as recorded. Run it on the name upper-cased, with punctuation other than `&` replaced by spaces.
- `acris_address_regex` and `acris_zips`: address rule. It is used for the Roc360 platform, whose loans are recorded under many private-lender names at Roc's address.
- `acris_zips`: the ZIPs the lender records under, used for the precision check.
- `verified_alt_streets`: other addresses confirmed on the lender's own site.
- `evidence_1_url` / `evidence_1_quote` (and `_2_`): product evidence. `sources` points to the full record in `research/`.

### Loan-level rule

1. Match the lender (party type 2 on a `MTGE` document) against `acris_regex`. For Roc, the address rule also counts.
2. If the lender's group is `CONSUMER+DSCR`, tag the loan **DSCR only when the borrower (party type 1) is an entity** (LLC, Inc, LP...). Loans to individuals from these lenders are mostly ordinary home loans.
3. `DSCR+RTL` lenders tell you the loan is one of the two, not which. The loan-life numbers below show that signals such as a purchase deed recorded alongside, or an early payoff, separate the two on average.

## How the list was built

1. **NYC lender universe.** I took every NYC mortgage of $100k or more recorded Oct 2024 – Sep 2026, 74,165 in all, with lenders, borrowers and property types, from NYC Open Data ACRIS. I ranked lenders by the number of loans made to entity borrowers, since DSCR and RTL loans go overwhelmingly to LLCs. That gave 150 non-bank lender groups with 10 or more such loans. 29 of them were set aside as clearly out of scope (agency multifamily, CMBS, CDFIs, a timeshare lender), and that is labelled as my own classification. The other 121 were researched.
2. **National lists.** National DSCR lenders (non-QM and investor) and national RTL lenders were researched from seed lists plus rankings.
3. **Evidence standard.** A product counts only if a fetched page states it, and the quote is recorded verbatim. Identity is confirmed by matching the address printed on the NYC mortgages to the company's published address. Nothing was filled in from memory. Where a site blocked access, the field was left empty.
4. **Match rules.** These were written from the names actually recorded on NYC mortgages, which are often not the brand. Examples:
   - Acra records as *Citadel Servicing*.
   - LoanStream records as *OCMBC*.
   - Newfi records as *Nexera Holding*.
   - Visio records as *Investor Mortgage Finance*.
   - Brick City records as *Lev Cap*.
   - Kiavi's DSCR loans close as *Figure Lending* from 2026-09.

## Test results (pre-registered thresholds)

Thresholds were written into `tag_and_test.py` before the first run. Full output: `test_results/test_report.txt`.

| Test | Result |
|---|---|
| **E1** name-match precision: ≥98% of matched rows carry the lender's known ZIP | **FAIL, 95.1%** |
| **E1b** same check after a rule-based review of every off-ZIP row, counting every unresolved row as a false match | **FAIL, 97.4%** (344 unresolved rows) |
| **E2** loans from pure investor lenders go to entity borrowers (≥85%; citywide baseline 35.5%) | **PASS, 97.1%** |
| **E3** RTL loans paid off within 24 months at ≥2× the DSCR rate (2021-07..2023-06 cohort) | **PASS: RTL 29% vs DSCR 2%** (DSCR-proxy 5%) |
| **E4** RTL loans more often recorded with a purchase deed on the same lot within 5 days | **PASS: RTL 56% vs DSCR 39%** |

**About E1.** None of the off-ZIP rows reviewed so far is a different company with the same name. The breakdown:
- **Same street, mistyped ZIP:** 197 rows.
- **Shared processing addresses:** 50 rows (an address used by 3+ unrelated lenders, e.g. First American's document centre in Idaho Falls).
- **Alternate addresses confirmed on the lender's own site:** 45 rows.

The unresolved rows are mostly the same company names at an unconfirmed second address:
- IceCap's *Ice Lender Holdings LLC* at 31 W 34th St: 177 rows.
- Meadowbrook in Hicksville: 34 rows.
- Accolend in Teaneck: 25 rows.

An independent check of those addresses is in progress. `test_results/unresolved_matches.csv` lists every unresolved row.

**Loan behaviour by group.** "Assigned" includes both refinances (NY refinances usually assign and consolidate the old mortgage rather than satisfy it) and loan sales into securitisations.

| Group | Paid off ≤24 mo | Assigned | Purchase deed alongside |
|---|---|---|---|
| RTL | 29% | 80% | 56% |
| DSCR+RTL | 17% | 58% | 58% |
| DSCR | 2% | 14% | 39% |
| CONSUMER+DSCR | 3% | 9% | 59% |
| Commercial bridge | 7% | 66% | 45% |

## Corrections made during checking

- An earlier count of "Visio ~330 NYC rows" was wrong. The prefix match also caught *Vision Real Estate Group*, *Visions Federal Credit Union* and others. Every rule now uses word boundaries.
- A `W FINANCIAL` pattern also matched *Parkview Financial REIT*, so it was tightened.
- *Figure Lending* was first forced into the DSCR group. Its NYC loans are 0% to entities (consumer HELOCs), so it was moved to `CONSUMER+DSCR`.
- *Clout Capital* inherited a consumer flag from the research record it shares with Cliffco, a consumer lender in the same building. Its loans are 100% to entities and 83% paid off within 24 months, so it is now `DSCR+RTL`.
- One NYC research group combined three firms at 60 Cutter Mill Rd. Only Manhattan Bridge Capital is on the list.

## Coverage and limits

- **Coverage.** The list tags 26% of the 26,311 NYC mortgages to entity borrowers. The rest:
  - 8,538 are bank loans.
  - 2,531 name **only MERS** as mortgagee, so the real lender is not in the public data at all.
  - The remainder is housing agencies, life insurers, CMBS conduits, and the unconfirmed private lenders below.
- **Unconfirmed private lenders.** Some of the largest NYC private lenders publish nothing about their products. Examples: *Broadview Capital* (675 loans in 24 months), *Bullpath Capital*, *YK 2021 / PBandJ*, *Goten*, *Five Towns Funding*, *Sterling Capital Funding*. They are in `nyc_unconfirmed_lenders.csv` with loan behaviour only, and are not tagged.
- **Roc360 platform.** Nearly 1,000 NYC mortgages in 24 months are recorded at 645 Madison Ave, 19th Floor, which is Roc Capital's published address. They are under *Loan Funder LLC, Series n* and private-lender names (Branch Lending, Insula, Metro View, Swift Capital...). Roc describes itself as a capital provider to private lenders. These are tagged at **medium** confidence: the address match is exact, and the link between Loan Funder LLC and Roc360 comes from a single third-party record.
- **Search limit.** The research hit its search limit. About 20 small NYC lenders (10–20 loans each) got no search at all and stay `unknown`.
- **Unreadable sites.** Several lender sites blocked automated reading: The Loan Store, Oaktree Funding, Quontic Bank, Carrington's sites and Visio's site. Carrington and Visio are on the list at medium confidence from third-party sources. The other three are excluded.
- **Products change.** The list reflects the lenders' sites as of November 2026.

## Re-running

```bash
python3 build_cache.py nyc_mtge.db 2024-10-01 2026-09-30 100000 MTGE
python3 build_cache.py nyc_deed.db 2024-09-01 2026-09-30 100000 DEED
python3 build_cache.py nyc_cohort.db 2021-07-01 2023-06-30 100000 MTGE
python3 link_payoffs.py nyc_cohort.db
python3 build_list.py dscr_rtl_lenders.csv
python3 build_excluded.py
python3 tag_and_test.py dscr_rtl_lenders.csv nyc_mtge.db nyc_deed.db nyc_cohort.db test_results
python3 unconfirmed_behaviour.py . nyc_unconfirmed_lenders.csv
python3 lender_universe.py nyc_mtge.db nyc_lenders.csv
```

Everything reads public NYC Open Data. Set `NYC_OPEN_DATA_APP_TOKEN` for higher rate limits.
