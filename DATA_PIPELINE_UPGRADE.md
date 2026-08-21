# Data pipeline upgrade — deploy runbook

This upgrade fixes four data-corruption bugs, rebuilds the ACRIS enricher,
and adds nine new signal sources. **Order matters** — follow the steps.

## What changed

| Area | Change |
|---|---|
| B1 | ACRIS party roles were inverted (buyers↔sellers, "lender" was the borrower). Roles now come from the Document Control Codes dataset per doc type. |
| B2 | Auto-add's PLUTO/RPAD/HPD steps crashed on a tuple/dict mismatch — every searched-in property was missing owner, units, value. Fixed. |
| B3 | `is_cash_purchase` compared the sale to the *most recent* mortgage ever; now it looks for a mortgage recorded −5/+90 days around the deed. |
| B4 | Tax delinquency flagged anything ever on any lien-sale list; now scoped to the most recent sale cycle (`LIEN_RECENCY_MONTHS`, default 24). |
| E1 | ACRIS fetches are batched (`document_id in (...)`): ~4-6 API calls per building instead of ~42. App token sent on every request via the shared `socrata_client.py`. |
| V-fixes | DOB/ECB queries are padding-agnostic; DOB open-violations use `violation_category`; HPD open-violations use `violationstatus` (matches the live endpoint); HPD complaints count distinct complaints, paginated; BIS permit sync matches filing OR issuance date; Enformion no longer defaults unknown cities to "Brooklyn, NY". |
| New data | PLUTO FAR/zoning/lat-lon, HPD owner mailing address + agent, ACRIS references/remarks + open-mortgage/free-and-clear, HPD litigation, marshal evictions, DOF exemptions, Speculation Watch List, DOB complaints, Certificates of Occupancy, FISP facades, LL84/LL97, DOF rolling sales. |

## Deploy order

```bash
# 1. Schema first (idempotent, safe to re-run)
python migrate_add_intel_signals.py

# 2. Sanity-check the live datasets (no writes; paste the output back if
#    anything looks off)
python verify_datasets.py            # optionally: verify_datasets.py <BBL>

# 3. Repair the historical data (dry-run first, then apply)
python fix_retroactive_data.py
python fix_retroactive_data.py --apply --backfill-new-fields

# 4. Refetch ACRIS under the corrected role mapping (force bypasses the
#    unchanged-count skip so lender rows + equity signals backfill)
ACRIS_FORCE_REFRESH=1 python step3_enrich_from_acris_parallel.py

# 5. Re-evaluate lien-sale status + refresh SOS lookups
python step4_enrich_from_tax_liens.py
python step5_enrich_from_sos.py

# 6. First signals run (litigation, evictions, exemptions, COs, ...)
python step6_enrich_signals.py
```

The nightly `run_enrichment_pipeline.py` now includes step 6. Add
`sync_recent_sales.py` as a daily cron — it sweeps deeds recorded citywide
in the last 10 days and flags matching buildings for immediate
re-enrichment, which is what keeps sale data from going stale.

## Offline tests

```bash
python test_pipeline_units.py    # 50 assertions, no network/DB needed
```

## Notes

- Everything degrades gracefully if the migration hasn't run (new columns
  are skipped, not crashed on) — but the new signals only persist after it.
- The pre-fix `sale_buyer_primary` fed `step5` SOS lookups, so SOS results
  sourced from it are cleared by the repair script and re-resolved against
  the true buyer on the next step5 run.
- Pre-foreclosure lis pendens is NOT on NYC Open Data (county court
  records / paid sources only).
- The LL97 flag is an estimate (building_sqft ≥ 25,000); the official
  covered-buildings list is only published as a DOB spreadsheet.
