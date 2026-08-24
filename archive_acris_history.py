#!/usr/bin/env python3
"""Archive and optionally remove ACRIS history before a retention cutoff.

The archive is a consistent, gzip-compressed CSV snapshot with row counts,
schemas, and SHA-256 checksums. Deletion is deliberately opt-in and is
limited to transaction IDs included in that snapshot.
"""

import argparse
import gzip
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import psycopg2


TABLE_QUERIES = {
    "acris_transactions": """
        SELECT t.*
        FROM acris_transactions t
        WHERE t.recorded_date < DATE %(cutoff)s
          AND t.id <= %(max_transaction_id)s
        ORDER BY t.id
    """,
    "acris_parties": """
        SELECT p.*
        FROM acris_parties p
        JOIN acris_transactions t ON t.id = p.transaction_id
        WHERE t.recorded_date < DATE %(cutoff)s
          AND t.id <= %(max_transaction_id)s
        ORDER BY p.id
    """,
    "acris_references": """
        SELECT r.*
        FROM acris_references r
        WHERE EXISTS (
          SELECT 1
          FROM acris_transactions t
          WHERE t.building_id = r.building_id
            AND t.recorded_date < DATE %(cutoff)s
            AND t.id <= %(max_transaction_id)s
            AND t.document_id IN (r.document_id, r.referenced_document_id)
        )
        ORDER BY r.id
    """,
}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cutoff", default="2000-01-01")
    parser.add_argument("--archive-dir", required=True)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume deletion from an existing verified manifest.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete only rows proven present in the completed archive.",
    )
    parser.add_argument("--batch-size", type=int, default=10_000)
    return parser.parse_args()


def database_url():
    value = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_PUBLIC_URL or DATABASE_URL is required")
    return value


def columns_for(cur, table):
    cur.execute(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    return [dict(zip(("name", "type", "nullable"), row)) for row in cur.fetchall()]


def gzip_row_count_and_sha256(path):
    compressed_sha = hashlib.sha256()
    with path.open("rb") as raw:
        for chunk in iter(lambda: raw.read(1024 * 1024), b""):
            compressed_sha.update(chunk)

    newlines = 0
    with gzip.open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            newlines += chunk.count(b"\n")
    return max(0, newlines - 1), compressed_sha.hexdigest()


def export_table(cur, table, query, params, archive_dir, expected_rows):
    path = archive_dir / f"{table}.csv.gz"
    if path.exists():
        raise RuntimeError(f"Refusing to overwrite existing archive file: {path}")
    rendered = cur.mogrify(query, params).decode("utf-8")
    started = time.monotonic()
    with gzip.open(path, "wt", encoding="utf-8", newline="", compresslevel=6) as target:
        cur.copy_expert(f"COPY ({rendered}) TO STDOUT WITH (FORMAT CSV, HEADER TRUE)", target)
    actual_rows, checksum = gzip_row_count_and_sha256(path)
    if actual_rows != expected_rows:
        raise RuntimeError(
            f"Archive verification failed for {table}: expected {expected_rows}, got {actual_rows}"
        )
    return {
        "file": path.name,
        "rows": actual_rows,
        "compressed_bytes": path.stat().st_size,
        "sha256": checksum,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }


def archive_snapshot(url, cutoff, archive_dir):
    conn = psycopg2.connect(url)
    conn.set_session(isolation_level="REPEATABLE READ", readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = 0")
            cur.execute(
                """
                SELECT COUNT(*), COALESCE(MAX(id), 0)
                FROM acris_transactions
                WHERE recorded_date < DATE %s
                """,
                (cutoff,),
            )
            transaction_count, max_transaction_id = cur.fetchone()
            params = {"cutoff": cutoff, "max_transaction_id": max_transaction_id}
            counts = {"acris_transactions": transaction_count}
            for table in ("acris_parties", "acris_references"):
                cur.execute(f"SELECT COUNT(*) FROM ({TABLE_QUERIES[table]}) archived", params)
                counts[table] = cur.fetchone()[0]

            files = {}
            schemas = {}
            for table, query in TABLE_QUERIES.items():
                print(f"Archiving {table}: {counts[table]:,} rows", flush=True)
                schemas[table] = columns_for(cur, table)
                files[table] = export_table(
                    cur, table, query, params, archive_dir, counts[table]
                )
            conn.commit()
            return max_transaction_id, counts, schemas, files
    finally:
        conn.close()


def write_connection(url):
    conn = psycopg2.connect(
        url,
        keepalives=1,
        keepalives_idle=10,
        keepalives_interval=5,
        keepalives_count=3,
    )
    conn.autocommit = False
    return conn


def delete_archived_rows(url, cutoff, max_transaction_id, batch_size, expected_count):
    conn = write_connection(url)
    removed_references = 0
    removed_transactions = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")
            cur.execute("SET lock_timeout = '30s'")
            cur.execute(
                """
                DELETE FROM acris_references r
                WHERE EXISTS (
                  SELECT 1
                  FROM acris_transactions t
                  WHERE t.building_id = r.building_id
                    AND t.recorded_date < DATE %s
                    AND t.id <= %s
                    AND t.document_id IN (r.document_id, r.referenced_document_id)
                )
                """,
                (cutoff, max_transaction_id),
            )
            removed_references = cur.rowcount
            conn.commit()

        retries = 0
        while True:
            try:
                with conn.cursor() as cur:
                    cur.execute("SET statement_timeout = 0")
                    cur.execute("SET lock_timeout = '30s'")
                    cur.execute(
                        """
                        WITH doomed AS MATERIALIZED (
                          SELECT id
                          FROM acris_transactions
                          WHERE recorded_date < DATE %s AND id <= %s
                          ORDER BY id
                          LIMIT %s
                        )
                        DELETE FROM acris_transactions t
                        USING doomed d
                        WHERE t.id = d.id
                        """,
                        (cutoff, max_transaction_id, batch_size),
                    )
                    removed = cur.rowcount
                    conn.commit()
                    removed_transactions += removed
                    retries = 0
                    if removed:
                        print(
                            f"Deleted {removed_transactions:,} rows in this run "
                            "(party rows cascade automatically)",
                            flush=True,
                        )
                    if removed < batch_size:
                        break
            except psycopg2.OperationalError as exc:
                retries += 1
                try:
                    conn.close()
                except Exception:
                    pass
                if retries > 8:
                    raise RuntimeError("Database proxy repeatedly interrupted cleanup") from exc
                print(f"Database connection interrupted; reconnecting ({retries}/8)...", flush=True)
                time.sleep(min(2 ** retries, 15))
                conn = write_connection(url)

        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")
            cur.execute("SET lock_timeout = '30s'")
            cur.execute(
                """
                UPDATE buildings
                SET acris_logic_version = GREATEST(COALESCE(acris_logic_version, 0), 5)
                WHERE acris_last_enriched IS NOT NULL
                  AND COALESCE(acris_logic_version, 0) < 5
                """
            )
            logic_rows = cur.rowcount
            conn.commit()

            cur.execute(
                """
                SELECT COUNT(*)
                FROM acris_transactions
                WHERE recorded_date < DATE %s AND id <= %s
                """,
                (cutoff, max_transaction_id),
            )
            remaining_archived_rows = cur.fetchone()[0]
            cur.execute(
                """
                SELECT COUNT(*)
                FROM acris_transactions
                WHERE recorded_date < DATE %s AND id > %s
                """,
                (cutoff, max_transaction_id),
            )
            post_snapshot_rows = cur.fetchone()[0]
            if remaining_archived_rows:
                raise RuntimeError(f"{remaining_archived_rows} archived transaction rows remain")
            return {
                "references": expected_count["acris_references"],
                "transactions": expected_count["acris_transactions"],
                "transactions_removed_this_run": removed_transactions,
                "buildings_marked_logic_v5": logic_rows,
                "post_snapshot_old_rows_preserved": post_snapshot_rows,
            }
    finally:
        conn.close()


def write_manifest(path, manifest):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def main():
    args = parse_args()
    cutoff = date.fromisoformat(args.cutoff)
    archive_dir = Path(args.archive_dir).resolve()
    archive_dir.mkdir(parents=True, exist_ok=True)

    url = database_url()
    parsed = urlparse(url)
    manifest_path = archive_dir / "manifest.json"
    if args.resume:
        if not manifest_path.exists():
            raise RuntimeError(f"Resume manifest does not exist: {manifest_path}")
        manifest = json.loads(manifest_path.read_text())
        if manifest["cutoff_exclusive"] != cutoff.isoformat():
            raise RuntimeError("Requested cutoff does not match the archive manifest")
        max_id = manifest["max_archived_transaction_id"]
        counts = manifest["row_counts"]
        for table, metadata in manifest["files"].items():
            path = archive_dir / metadata["file"]
            actual_rows, checksum = gzip_row_count_and_sha256(path)
            if actual_rows != metadata["rows"] or checksum != metadata["sha256"]:
                raise RuntimeError(f"Existing archive verification failed: {path}")
        print("Existing archive manifest, row counts, and checksums verified.", flush=True)
    else:
        if any(archive_dir.iterdir()):
            raise RuntimeError(f"Archive directory must be empty: {archive_dir}")
        started_at = datetime.now(timezone.utc)
        manifest = {
            "archive_format_version": 1,
            "cutoff_exclusive": cutoff.isoformat(),
            "database_host": parsed.hostname,
            "database_name": parsed.path.lstrip("/"),
            "started_at": started_at.isoformat(),
            "deletion_requested": args.apply,
        }
        max_id, counts, schemas, files = archive_snapshot(url, cutoff, archive_dir)
        manifest.update(
            {
                "max_archived_transaction_id": max_id,
                "row_counts": counts,
                "schemas": schemas,
                "files": files,
                "archive_verified_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        write_manifest(manifest_path, manifest)

    if args.apply:
        deletion = delete_archived_rows(
            url, cutoff, max_id, args.batch_size, expected_count=counts
        )
        if deletion["transactions"] != counts["acris_transactions"]:
            raise RuntimeError(
                "Deletion count did not match the verified transaction archive: "
                f"{deletion['transactions']} != {counts['acris_transactions']}"
            )
        manifest["deletion"] = deletion
        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
        write_manifest(manifest_path, manifest)
        print("Archive verified and archived rows deleted. Table compaction is still required.")
    else:
        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
        write_manifest(manifest_path, manifest)
        print("Archive verified. No database rows were deleted (use --apply to opt in).")

    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
