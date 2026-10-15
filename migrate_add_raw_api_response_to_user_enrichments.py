#!/usr/bin/env python3
"""
Migration: Add raw_api_response JSONB column to user_enrichments.

We were paying for full TruePeopleSearch / Enformion responses (age, DOB,
previous addresses, relatives, associates, per-phone provider + last-reported
date) and discarding everything except phones/emails. Persisting the raw
response lets us backfill new fields later without re-paying, and lets us
debug match-quality issues by looking at what the API actually returned.
"""
import os
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv('dashboard_html/.env')


def _connect():
    """Connect using DATABASE_URL (Railway-style) when present, else fall back
    to discrete DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME (local .env style).
    Lets `railway run python3 migrate_*.py` Just Work without needing to
    re-shape env vars."""
    url = os.getenv('DATABASE_URL')
    if url:
        return psycopg2.connect(url)
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'),
    )


def run_migration():
    conn = _connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        print("Adding raw_api_response column to user_enrichments...")
        cur.execute("""
            ALTER TABLE user_enrichments
            ADD COLUMN IF NOT EXISTS raw_api_response JSONB
        """)
        conn.commit()

        cur.execute("""
            SELECT column_name, data_type FROM information_schema.columns
            WHERE table_name = 'user_enrichments'
            ORDER BY ordinal_position
        """)
        print("\nuser_enrichments columns:")
        for row in cur.fetchall():
            print(f"  {row['column_name']:30} {row['data_type']}")
        print("\nMigration complete.")
    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    run_migration()
