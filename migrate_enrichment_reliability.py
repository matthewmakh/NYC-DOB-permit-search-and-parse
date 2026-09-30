"""Add refresh checkpoints and remove excess saved enrichment payloads.

No upstream source backfill or paid lookup runs here.
"""
import _pipeline_path  # noqa: F401
import psycopg2
from migrate_add_freshness_and_jobs import database_dsn
from property_source_refresh import SCHEMA_SQL, BUILDING_COLUMNS_SQL
from enrichment_privacy import migrate_privacy


def main():
    with psycopg2.connect(database_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(72108,0)')
            cur.execute(SCHEMA_SQL)
            cur.execute(BUILDING_COLUMNS_SQL)
            migrate_privacy(cur)
    print('Enrichment reliability schema and privacy cleanup ready')


if __name__ == '__main__':
    main()
