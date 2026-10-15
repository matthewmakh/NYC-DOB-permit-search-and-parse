# Contractor directory read model

`contractor_directory.py` builds narrow participant facts and contractor totals
from the existing `permit_participants`, `permits`, and `buildings` sources.
Default/name/contractor-scale searches read the totals; permit, role, and property
filters aggregate the indexed facts. The same aggregate and existing filter
helpers preserve directory semantics, including distinct jobs/properties and
participant roles. Contractor profiles read their aggregate from the same pinned
facts and fetch full permit/building records through the indexed permit IDs.
Work categories are computed for the returned names only.

Each application worker starts a daemon that checks for changes every 10 seconds.
A PostgreSQL advisory lock permits only one builder. Statement triggers invalidate
the model after permit writes or relevant building writes; contact enrichment
alone does not invalidate it. After writes settle for 15 seconds the directory
rebuilds; continuous imports cause a rebuild at most five minutes after the last
publication. Counts also refresh daily as the 90-day activity window advances.
The old five-minute per-process HTTP cache is removed.

A refresh builds the next generation in a transaction while searches continue
reading the published generation. A short exclusive advisory lock publishes it
after existing readers finish. Older generations are cleaned at the start of the
next build, outside publication. Failed refreshes roll back and retain the last
complete generation. Source writes arriving during a build remain marked dirty.
The application logs refresh failures and retries automatically. Before the
initial model exists, the directory falls back to source queries with JIT off.

The source view has no permanent schema dependency on these tables, so the daily
pipeline can still drop/recreate `permit_participants`. Source data is never
modified by the builder. Initialization is idempotent; schema DDL has a five-second
lock timeout. Two complete generations are retained, plus the in-flight build.

To inspect freshness (read-only):

```sql
SELECT generation, source_version, indexed_version, changed_at, indexed_at,
       indexed_date
FROM contractor_directory_state;
```

For a forced repair, use a dedicated `RealDictCursor` connection with the app's
existing database configuration and call `contractor_directory.refresh(conn,
force=True)`. Do not use a request's pooled connection for maintenance.

Tests in `contractor_directory_tests.py` use a disposable localhost database,
compare indexed and source results across filter/sort/page combinations, exercise
source invalidation and daily refresh, simulate failed builds, and verify reader
publication locks and single-builder election.
