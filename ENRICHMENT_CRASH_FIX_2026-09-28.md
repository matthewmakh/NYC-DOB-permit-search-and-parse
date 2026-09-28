# Enrichment pipeline failure: diagnosis and verification

The supplied run completed its remaining steps, then deliberately exited with
status 1 because `safety` and `step6` failed. It was not a container startup or
database connection failure.

## Causes and changes

- **DOB Safety:** the citywide grouped query exceeded the client's 20-second
  timeout on all four attempts. A read-only reproduction took 32.49 seconds
  just for its first page. `sync_dob_safety.py` now fetches five disjoint BBL
  ranges, with a 60-second timeout and three attempts per request. Persistently
  slow ranges can split another three levels; permanent query errors do not
  split. A complete, validated snapshot is still required before any write.
- **Certificates of occupancy:** querying placeholder BINs can match thousands
  of unrelated records and exceed the 2,000-row safety limit. The public legacy
  CO feed returned 3,959 rows for `3000000` and 2,839 for `4000000`; the new test
  reproduced the exact logged exception before the fix. Invalid, placeholder,
  or wrong-borough BINs now use the parcel fields. Valid BIN queries and the
  completeness guard retain their previous behavior. The production database
  credentials are absent locally, so the stored BINs of the six failing records
  were not inspected.
- **Parcel fallback:** both current CO feeds use borough names, and the legacy
  feed uses five-digit lots. The fallback now accepts borough names/codes and
  stripped/four-/five-digit lot forms, matching either the geocoded BBL or the
  original parcel fields.
- **Related errors fixed:** DOB NOW dates such as `06/18/25  9:43:09 AM` now
  participate in latest-CO selection. Placeholder BINs no longer produce
  unrelated DOB complaint or FISP matches. Complaint totals without a usable
  BIN remain unknown. Signal version 4 makes existing values eligible for
  recalculation in the pipeline's existing bounded batches.

## Verification

- **22 new regressions passed**, including real PostgreSQL tests for successful
  updates, clearing genuinely absent parcels, preserving old values after a
  partial fetch, and rolling back a failed database update. Timeout recovery
  also discards an incomplete original page sequence before splitting.
- **501 Python tests/checks passed in total** across the new regressions and
  existing pipeline, signal, reliability, geocoding, source-link, filter,
  owner, account, CRM, and prospecting suites. Optional database tests were
  enabled against a disposable local PostgreSQL server. Both JavaScript suites
  (`building_profile_permit_tests.js`, `properties_navigation_tests.js`) passed.
- **Live Safety sweep:** 230,246 grouped rows, 185,680 parcels, and 1,093,551
  violations in 70.2 seconds. The total exactly matched an independent
  `count(*)` query over the same BBL bounds.
- **Live CO checks:** parcel fallbacks completed for all six BBLs in the log.
  `3012420070` returned three records with the latest dated 2024-10-30; the other
  five returned no records. A valid BIN (`3128179`) returned two records.
  These checks used explicit placeholder inputs to exercise fallback behavior,
  rather than assuming access to the production BIN values.
- Live FISP fallback completed without an error. No production database writes,
  paid-provider requests, or deployment were performed.

To repeat the focused regression suite:

```sh
python -m unittest enrichment_crash_tests -v
```

Set `ENRICHMENT_TEST_DATABASE_URL` to a **disposable** PostgreSQL database to
enable its three database tests. Without it, those tests are explicitly skipped.

## Other findings to track

- The public [ACRIS master feed](https://data.cityofnewyork.us/resource/bnx9-e6tj.json?%24select=max(recorded_datetime)%20as%20latest)
  returned **2026-08-31** as its latest recording date at verification time.
  This explains why the run's September 18–28 window returned zero documents.
  An empty successful query currently does not alert on source freshness lag.
- The log contains three malformed source block/lot combinations (`00396` /
  `23,24`, `00400` / `16350`, `15589` / `70004`). They remain unlinked; guessing
  a parcel would risk attaching records to the wrong property.
- The geocoder finished successfully, but **458 permits still lacked
  coordinates**; 123 attempted addresses had no verified match in that run.
  These are outstanding data coverage gaps.
- The startup message `DATABASE_URL set: False` is misleading in isolation:
  the scripts also support `DB_*` variables, and the supplied log confirms
  successful database operations.

Deploy these changes through the normal release process, then run the scheduled
pipeline. Version 4 repairs existing signal values over subsequent batches;
the production run and historical repair have not been verified from this
workspace. Public-source checks cannot guarantee future upstream availability.
