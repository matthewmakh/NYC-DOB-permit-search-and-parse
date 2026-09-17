# Geocoding and SOS recovery

The September 17 run completed its tax/violation checks but exited unsuccessfully
because 16 SOS lookups remained unresolved. Successful updates were committed.
The old geocoder also accepted unrelated global search results after Geoclient
returned HTTP 401. Its reported geocoding successes need to be rechecked.

## Geoclient configuration

Set `NYC_GEOCLIENT_SUBSCRIPTION_KEY` to a **Geoclient v2** subscription key from
[NYC's API portal](https://api-portal.nyc.gov/), in both the pipeline and dashboard
services. Per the [Geoclient guide](https://mlipper.github.io/geoclient/#section-7.1),
requests send it in `Ocp-Apim-Subscription-Key`.

For existing deployments, `NYC_GEOCLIENT_APP_KEY`, then `NYC_GEOCLIENT_APP_ID`,
are fallback environment variable names. They must contain an actual v2 key;
an old application ID is not converted into a key. A rejected key stops further
Geoclient calls in that geocoding run. No local v2 key was available during these
checks, so the configured Railway key still needs verification after deployment.

The fallback is now [NYC GeoSearch](https://geosearch.planninglabs.nyc/), which
uses the city's Property Address Directory. A result must match the requested
BBL and pass NYC/borough coordinate checks. Without a BBL, GeoSearch must match
the house number, street and borough exactly. Ambiguous results stay unresolved.

Ordinary runs resume missing coordinates, including legacy `geocode_failed`
rows. Service errors retry after one hour; confirmed no-matches after seven
days. Duplicate successful lookups and confirmed misses are cached within a run.
The script adds `geocode_last_attempted` idempotently when run.

## Retry the SOS failures only

With the intended database configured, inspect saved errors first:

```sh
python step5_enrich_from_sos.py --retry-failures --dry-run
python step5_enrich_from_sos.py --retry-failures
```

The dry run displays the first ten affected owners and their previous errors.
The client retries timeouts, connection/read failures, invalid responses and
HTTP 408/429/500/502/503/504, up to three attempts. It respects bounded Retry-After
hints and logs the failing stage/status. The batch log identifies each unresolved
BBL and owner. Failed lookups retain their previous success timestamp and remain
eligible for retry; a real no-match is a completed lookup. Unresolved failures
still produce a failing exit status rather than silently claiming success.

## Recheck coordinates from an old log

This targets only records explicitly logged as successful permit geocodes.
It also verifies that the current coordinates still equal the rounded values
in that log. It never reruns the entire enrichment pipeline.

```sh
# Preview: reads the database and NYC APIs; no database writes.
python geocode_permits.py --repair-log saved-pipeline.log --report repair-preview.json

# Apply after reviewing the preview, with a new audit filename.
python geocode_permits.py --repair-log saved-pipeline.log --report repair-applied.json --apply
```

Both commands require the intended `DATABASE_URL` or `DB_*` configuration.
The apply command rechecks live results and writes a durable JSON report with
old/new coordinates **before** altering records. It replaces verified matches,
clears unverified coordinates when the city services confirm no match, and
leaves records alone during service failures. A conditional update also protects
against another worker changing coordinates while the repair is running.
Existing report files are never overwritten. Reports describe proposed actions;
the console reports how many conditional updates actually applied.

No repair has been applied to Railway as part of the local implementation.

## Regression checks

```sh
python geocoding_pipeline_tests.py
python pipeline_unit_tests.py
# Optional integration checks use temporary tables in a disposable PostgreSQL DB:
GEOCODE_TEST_DATABASE_URL=postgresql://localhost/test python geocoding_pipeline_tests.py
```
