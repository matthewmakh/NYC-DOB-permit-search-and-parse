#!/usr/bin/env python3
"""
Migration: Create bulk_enrich_jobs table for background bulk enrichment jobs
"""
import os
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv('dashboard_html/.env')
load_dotenv()


def get_connection():
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
    )


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS bulk_enrich_jobs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending',
    owner_strategy TEXT NOT NULL DEFAULT 'recommended',
    filters_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    building_ids INTEGER[] NOT NULL DEFAULT '{}',
    total_properties INTEGER NOT NULL DEFAULT 0,
    total_owners_planned INTEGER NOT NULL DEFAULT 0,
    properties_processed INTEGER NOT NULL DEFAULT 0,
    owners_attempted INTEGER NOT NULL DEFAULT 0,
    owners_successful INTEGER NOT NULL DEFAULT 0,
    owners_failed INTEGER NOT NULL DEFAULT 0,
    owners_skipped INTEGER NOT NULL DEFAULT 0,
    cost_per_lookup NUMERIC(10, 4) NOT NULL DEFAULT 0.35,
    estimated_max_cost NUMERIC(10, 2) NOT NULL DEFAULT 0,
    total_charged NUMERIC(10, 2) NOT NULL DEFAULT 0,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_bulk_enrich_jobs_user ON bulk_enrich_jobs(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_bulk_enrich_jobs_status ON bulk_enrich_jobs(status)",
]


def run_migration():
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        print("Creating bulk_enrich_jobs table...")
        cur.execute(CREATE_TABLE_SQL)
        for idx_sql in CREATE_INDEXES_SQL:
            cur.execute(idx_sql)
        conn.commit()
        print("✅ Migration complete!")
    except Exception as e:
        conn.rollback()
        print(f"❌ Migration failed: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    run_migration()
