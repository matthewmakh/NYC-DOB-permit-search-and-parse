"""Refresh daily DOB Safety totals after a complete, partitioned citywide sweep."""
import _pipeline_path  # noqa: F401
import psycopg2
from psycopg2.extras import execute_values
from migrate_add_freshness_and_jobs import database_dsn
from socrata_client import SocrataClient, SocrataError, SocrataTransientError
from step4_enrich_from_tax_liens import safety_violation_is_open


def aggregate_safety(rows):
    if not rows:
        raise SocrataError('Empty citywide Safety snapshot; refusing to clear existing counts')
    totals = {}
    for row in rows:
        bbl = str(int(row['bbl']))
        if len(bbl) != 10 or bbl[0] not in '12345':
            raise SocrataError('Invalid parcel in citywide Safety snapshot')
        count = int(row['violation_count'])
        if count < 0:
            raise SocrataError('Negative Safety count')
        total, opened = totals.get(bbl, (0, 0))
        totals[bbl] = (total + count, opened + (count if safety_violation_is_open(row.get('violation_status')) else 0))
    return [(bbl, *counts) for bbl, counts in totals.items()]


def fetch_safety_snapshot(client):
    """Bound each aggregation by BBL range instead of regrouping the whole city.

    A slow range can split three times; a sustained outage still fails the run.
    Failed pages are discarded by get_all, so a split cannot double-count them.
    Every range must finish before main opens a database connection.
    """
    def fetch_range(lower, upper, splits_left=3):
        try:
            return client.get_all('dob_safety_violations', page_size=10000, max_rows=2000000, **{
                '$where': f'bbl >= {lower} AND bbl < {upper}',
                '$select': 'bbl, violation_status, count(*) AS violation_count',
                '$group': 'bbl, violation_status', '$order': 'bbl, violation_status',
            })
        except SocrataTransientError:
            if not splits_left:
                raise
            midpoint = (lower + upper) // 2
            print(f'DOB Safety range {lower}–{upper} timed out or unavailable; splitting query', flush=True)
            return (fetch_range(lower, midpoint, splits_left - 1)
                    + fetch_range(midpoint, upper, splits_left - 1))

    rows = []
    for borough in range(1, 6):
        rows.extend(fetch_range(borough * 1000000000, (borough + 1) * 1000000000))
        if len(rows) > 2000000:
            raise SocrataError('Citywide Safety snapshot exceeds the 2000000 row safety limit')
        print(f'DOB Safety borough {borough}/5 fetched ({len(rows):,} aggregate rows so far)', flush=True)
    return rows


def main():
    # Citywide aggregates can exceed the shared client's 20-second default.
    client = SocrataClient(timeout=60, max_retries=3)
    rows = fetch_safety_snapshot(client)
    totals = aggregate_safety(rows)
    # Fetch completes before any write; outage/truncation cannot erase counts.
    with psycopg2.connect(database_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_xact_lock(72103, 0)')
            if not cur.fetchone()[0]:
                print('Another Safety refresh is already saving this snapshot')
                return
            cur.execute('CREATE TEMP TABLE safety_snapshot (bbl TEXT PRIMARY KEY, total INTEGER, opened INTEGER) ON COMMIT DROP')
            execute_values(cur, 'INSERT INTO safety_snapshot VALUES %s', totals, page_size=5000)
            cur.execute("""UPDATE buildings b SET
                dob_safety_violation_count=COALESCE(s.total,0),
                dob_safety_open_violations=COALESCE(s.opened,0),
                dob_safety_last_checked=NOW()
                FROM (SELECT b2.id,s2.total,s2.opened FROM buildings b2
                      LEFT JOIN safety_snapshot s2 ON s2.bbl=b2.bbl) s
                WHERE b.id=s.id""")
            print(f'DOB Safety refreshed for {cur.rowcount:,} properties from {len(totals):,} source parcels')


if __name__ == '__main__':
    main()
