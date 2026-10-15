#!/usr/bin/env python3
"""Add freshness/queue state and repair legacy BIS permit identities.

The schema changes and identity repair are idempotent, so the script is safe at
the start of every nightly pipeline run.
"""

import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()
load_dotenv('dashboard_html/.env')


def database_dsn():
    if os.getenv('DATABASE_URL'):
        return os.environ['DATABASE_URL']
    required = ('DB_HOST', 'DB_USER', 'DB_PASSWORD', 'DB_NAME')
    if not all(os.getenv(key) for key in required):
        raise ValueError('DATABASE_URL or all DB_* connection variables are required')
    return (f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}@"
            f"{os.environ['DB_HOST']}:{os.getenv('DB_PORT', '5432')}/"
            f"{os.environ['DB_NAME']}")


def main():
    conn = psycopg2.connect(database_dsn())
    cur = conn.cursor()
    try:
        cur.execute("""
            ALTER TABLE buildings
                ADD COLUMN IF NOT EXISTS property_last_attempted TIMESTAMP,
                ADD COLUMN IF NOT EXISTS property_last_enriched TIMESTAMP,
                ADD COLUMN IF NOT EXISTS property_last_error TEXT,
                ADD COLUMN IF NOT EXISTS acris_last_attempted TIMESTAMP,
                ADD COLUMN IF NOT EXISTS acris_last_error TEXT,
                ADD COLUMN IF NOT EXISTS acris_logic_version INTEGER NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS dob_safety_violation_count INTEGER NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS dob_safety_open_violations INTEGER NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS dob_safety_last_checked TIMESTAMP
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS property_enrichment_jobs (
                id BIGSERIAL PRIMARY KEY,
                building_id INTEGER NOT NULL REFERENCES buildings(id) ON DELETE CASCADE,
                bbl VARCHAR(10) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                locked_at TIMESTAMP,
                last_error TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (building_id)
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_property_enrichment_jobs_ready
            ON property_enrichment_jobs (status, available_at)
        """)

        # Older ingestion used Job # as permit_no. NYC explicitly identifies
        # PERMIT_SI_NO as the row key because one job can receive multiple work
        # permits. Preserve the surviving historical row under its correct key;
        # a DOB backfill can then insert the work permits that were previously
        # collapsed. The NOT EXISTS guard avoids collisions if a correct row
        # arrived before this migration ran.
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'permits' AND column_name = 'permit_si_no'
            )
        """)
        has_permit_si_no = cur.fetchone()[0]
        remapped = conflicts = 0
        if has_permit_si_no:
            cur.execute("""
                UPDATE permits AS legacy
                SET permit_no = TRIM(legacy.permit_si_no)
                WHERE legacy.api_source = 'nyc_open_data'
                  AND NULLIF(TRIM(legacy.permit_si_no), '') IS NOT NULL
                  AND legacy.permit_no IS DISTINCT FROM TRIM(legacy.permit_si_no)
                  AND NOT EXISTS (
                      SELECT 1 FROM permits AS correct
                      WHERE correct.id <> legacy.id
                        AND correct.permit_no = TRIM(legacy.permit_si_no)
                  )
            """)
            remapped = cur.rowcount
            cur.execute("""
                SELECT COUNT(*)
                FROM permits AS legacy
                WHERE legacy.api_source = 'nyc_open_data'
                  AND NULLIF(TRIM(legacy.permit_si_no), '') IS NOT NULL
                  AND legacy.permit_no IS DISTINCT FROM TRIM(legacy.permit_si_no)
            """)
            conflicts = cur.fetchone()[0]
        conn.commit()
        print('✅ Freshness columns and property enrichment queue are ready')
        print(f'✅ Remapped {remapped} legacy BIS permit identity row(s)')
        if conflicts:
            print(f'⚠️  {conflicts} legacy BIS row(s) already have a correct-key counterpart; '
                  'left untouched to preserve dependent records')
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    main()
