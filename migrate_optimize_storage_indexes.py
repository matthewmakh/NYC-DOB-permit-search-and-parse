#!/usr/bin/env python3
"""Replace redundant/low-value indexes with query-shaped indexes.

Runs online by default via CONCURRENTLY. ACRIS compaction is a separate,
explicit option because VACUUM FULL takes exclusive table locks.
"""

import argparse
import os
import sys

import psycopg2


CREATE_INDEXES = [
    (
        "idx_acris_deed_sales_recent",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_acris_deed_sales_recent
           ON acris_transactions
             (recorded_date DESC NULLS LAST, doc_amount DESC NULLS LAST, id)
           WHERE doc_type LIKE '%DEED%'""",
        "Recent deed timeline and seller-lead ordering",
    ),
    (
        "idx_permits_bbl_issue_date",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_permits_bbl_issue_date
           ON permits (bbl, issue_date DESC NULLS LAST)
           WHERE bbl IS NOT NULL""",
        "Property permit history lookup and ordering",
    ),
    (
        "idx_projects_bbl_status_date",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_projects_bbl_status_date
           ON projects (bbl, current_status_date DESC NULLS LAST)
           WHERE bbl IS NOT NULL""",
        "Property project history lookup and status ordering",
    ),
    (
        "idx_permits_borough_filing_date",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_permits_borough_filing_date
           ON permits (borough, filing_date DESC NULLS LAST)""",
        "Borough prospecting constrained by filing recency",
    ),
]


DROP_INDEXES = [
    # Exact duplicates of unique indexes/constraints.
    "idx_buildings_bbl",
    "idx_contacts_phone",
    "idx_auth_users_email",
    "idx_user_sessions_token",
    "idx_auth_sessions_token",
    "idx_users_email",
    "idx_building_metrics_bbl",
    # Prefix-redundant or nonselective indexes with better replacements.
    "idx_acris_trans_building",
    "idx_acris_parties_type",
    "idx_acris_parties_is_lead",
    "idx_acris_trans_date",
    "idx_permits_block",
    "idx_permits_lot",
    "idx_permits_bbl",
    "idx_permits_borough",
    "idx_projects_bbl",
]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--compact-acris",
        action="store_true",
        help="Run blocking VACUUM FULL on ACRIS tables after index changes.",
    )
    return parser.parse_args()


def database_url():
    value = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_PUBLIC_URL or DATABASE_URL is required")
    return value


def index_size(cur, names):
    cur.execute(
        """
        SELECT COALESCE(SUM(pg_relation_size(c.oid)), 0)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = ANY(%s)
        """,
        (names,),
    )
    return cur.fetchone()[0]


def pretty(cur, value):
    cur.execute("SELECT pg_size_pretty(%s::bigint)", (value,))
    return cur.fetchone()[0]


def main():
    args = parse_args()
    print("Replacement indexes:")
    for name, _sql, purpose in CREATE_INDEXES:
        print(f"  + {name}: {purpose}")
    print("Indexes approved for removal:")
    for name in DROP_INDEXES:
        print(f"  - {name}")
    if not args.apply:
        print("Dry run only. Use --apply to execute.")
        return 0

    conn = psycopg2.connect(database_url())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")
            cur.execute("SET lock_timeout = '30s'")
            old_size = index_size(cur, DROP_INDEXES)
            print(f"Existing approved indexes occupy {pretty(cur, old_size)}")

            for name, sql, purpose in CREATE_INDEXES:
                print(f"Creating {name} concurrently...", flush=True)
                cur.execute(sql)
                cur.execute(f"COMMENT ON INDEX {name} IS %s", (purpose,))

            for name in DROP_INDEXES:
                print(f"Dropping {name} concurrently if present...", flush=True)
                cur.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")

            new_names = [name for name, _sql, _purpose in CREATE_INDEXES]
            new_size = index_size(cur, new_names)
            cur.execute(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_index i ON i.indexrelid = c.oid
                WHERE c.relname = ANY(%s) AND NOT i.indisvalid
                """,
                (new_names,),
            )
            invalid = [row[0] for row in cur.fetchall()]
            if invalid:
                raise RuntimeError(f"Replacement indexes are invalid: {invalid}")
            print(f"Replacement indexes occupy {pretty(cur, new_size)}")

            if args.compact_acris:
                for table in ("acris_parties", "acris_transactions", "acris_references"):
                    print(f"Compacting {table}; ACRIS queries will briefly wait...", flush=True)
                    cur.execute(f"VACUUM (FULL, ANALYZE) {table}")
            else:
                print("ACRIS compaction skipped; use --compact-acris during a maintenance window.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
