#!/usr/bin/env python3
"""Export, verify, reconcile, and optionally retire contacts_old_backup."""

import argparse
import csv
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from contact_intelligence import database_connection
from migrate_contact_evidence import reconcile, table_exists


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--drop-after-verify", action="store_true")
    args = parser.parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "contacts_old_backup.csv.gz"
    manifest_path = output_dir / "manifest.json"

    conn = database_connection()
    conn.set_session(isolation_level="REPEATABLE READ", autocommit=False)
    try:
        with conn.cursor() as cur:
            if not table_exists(cur, "contacts_old_backup"):
                raise RuntimeError("contacts_old_backup has already been retired")
            cur.execute("LOCK TABLE contacts_old_backup IN ACCESS EXCLUSIVE MODE")
            cur.execute("SELECT * FROM contacts_old_backup ORDER BY id")
            columns = [item.name for item in cur.description]
            exported_rows = 0
            with gzip.open(csv_path, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(columns)
                while True:
                    rows = cur.fetchmany(1000)
                    if not rows:
                        break
                    writer.writerows(rows)
                    exported_rows += len(rows)

        with gzip.open(csv_path, "rt", encoding="utf-8", newline="") as handle:
            verified_rows = sum(1 for _ in csv.reader(handle)) - 1
        reconciliation = reconcile(conn)
        if exported_rows != verified_rows or not reconciliation["ok"]:
            raise RuntimeError(
                f"archive verification failed: exported={exported_rows}, "
                f"verified={verified_rows}, reconciliation={reconciliation}"
            )
        manifest = {
            "table": "contacts_old_backup",
            "archived_at_utc": datetime.now(timezone.utc).isoformat(),
            "columns": columns,
            "row_count": exported_rows,
            "csv_gzip_sha256": sha256(csv_path),
            "reconciliation": reconciliation,
            "backup_table_dropped": bool(args.drop_after_verify),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str) + "\n")
        if args.drop_after_verify:
            with conn.cursor() as cur:
                cur.execute("DROP TABLE contacts_old_backup")
        conn.commit()
        print(json.dumps(manifest, indent=2, default=str))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
