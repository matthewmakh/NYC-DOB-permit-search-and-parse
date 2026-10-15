#!/usr/bin/env python3
"""Backfill DOB NOW project intelligence in bounded, restartable windows.

Unlike the legacy backfill script, this uses the current bulk-upsert methods
and does not retain an entire multi-year response in memory.
"""

import argparse
import time
from datetime import date, datetime, timedelta

import psycopg2

from permit_scraper_api import (
    API_BATCH_SIZE,
    APPROVED_COLUMNS,
    DB_CONFIG,
    ELECTRICAL_COLUMNS,
    ELECTRICAL_DETAIL_COLUMNS,
    ELEVATOR_COLUMNS,
    FILINGS_COLUMNS,
    CITY_RECORD_COLUMNS,
    NYC_APP_TOKEN,
    CityRecordClient,
    DOBNowApprovedClient,
    DOBNowElectricalClient,
    DOBNowFilingsClient,
    ElectricalDetailsClient,
    ElevatorApplicationsClient,
    PermitDatabase,
    prepare_city_record_rows,
    prepare_rows_dob_now_approved,
    prepare_rows_dob_now_electrical,
    prepare_rows_dob_now_filings,
    prepare_electrical_detail_rows,
    prepare_elevator_rows,
)
from project_intelligence import sync_project_intelligence


def windows(start: date, end: date, days: int):
    cursor = start
    while cursor <= end:
        window_end = min(end, cursor + timedelta(days=days - 1))
        yield cursor, window_end
        cursor = window_end + timedelta(days=1)


def reconnect_database(db, context: str):
    try:
        db.close()
    except Exception:
        pass
    last_error = None
    for attempt in range(1, 6):
        time.sleep(min(2 ** attempt, 30))
        try:
            db.connect()
            return
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
            last_error = exc
            print(
                f"   {context} reconnect attempt {attempt}/5 failed: {exc}",
                flush=True,
            )
    raise last_error


def refresh_existing_projects(db, batch_size: int, after_project_key: str = ""):
    """Consolidate every existing permit project without refetching source data."""
    last_key = after_project_key
    totals = {"projects": 0, "alerts": 0}
    consecutive_disconnects = 0
    while True:
        try:
            db.cursor.execute("""
                SELECT DISTINCT project_key
                FROM permits
                WHERE project_key IS NOT NULL AND project_key > %s
                ORDER BY project_key
                LIMIT %s
            """, (last_key, batch_size))
            keys = [row[0] for row in db.cursor.fetchall()]
            if not keys:
                break
            result = sync_project_intelligence(db.conn, keys)
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
            consecutive_disconnects += 1
            if consecutive_disconnects > 5:
                raise
            print(
                f"   database disconnected after {last_key or 'start'}; "
                f"reconnecting (attempt {consecutive_disconnects}/5): {exc}",
                flush=True,
            )
            reconnect_database(db, "project refresh")
            continue
        consecutive_disconnects = 0
        totals["projects"] += result["projects"]
        totals["alerts"] += result["alerts"]
        last_key = keys[-1]
        print(
            f"   through {last_key}: {result['projects']} projects, "
            f"{result['alerts']} alerts",
            flush=True,
        )
    return totals


def ingest_pages(db, client, start, end, source):
    offset = 0
    touched = set()
    fetched = upserted = skipped = 0
    while True:
        if source == "filings":
            records = client.fetch_filings(
                start.isoformat(), end.isoformat(), limit=API_BATCH_SIZE, offset=offset
            )
            rows, bad = prepare_rows_dob_now_filings(records)
            affected, failed = db.upsert_dob_now_filings(rows)
            columns = FILINGS_COLUMNS
        elif source == "approved":
            records = client.fetch_permits(
                start.isoformat(), end.isoformat(), limit=API_BATCH_SIZE, offset=offset
            )
            rows, bad = prepare_rows_dob_now_approved(records)
            affected, failed = db.upsert_dob_now_approved(rows)
            columns = APPROVED_COLUMNS
        elif source == "electrical":
            records = client.fetch_applications(
                start.isoformat(), end.isoformat(), limit=API_BATCH_SIZE, offset=offset
            )
            rows, bad = prepare_rows_dob_now_electrical(records)
            affected, failed = db.upsert_dob_now_electrical(rows)
            columns = ELECTRICAL_COLUMNS
        elif source == "elevator":
            records = client.fetch_applications(
                start.isoformat(), end.isoformat(), limit=API_BATCH_SIZE, offset=offset
            )
            rows, bad = prepare_elevator_rows(records)
            affected, failed = db.upsert_dob_now_elevator(rows)
            columns = ELEVATOR_COLUMNS
        else:
            records = client.fetch_notices(
                start.isoformat(), end.isoformat(), limit=API_BATCH_SIZE, offset=offset
            )
            rows, bad = prepare_city_record_rows(records)
            affected, failed = db.upsert_city_record(rows)
            columns = CITY_RECORD_COLUMNS

        if failed:
            raise RuntimeError(f"{source}: {failed} database chunk(s) failed")
        if "project_key" in columns:
            key_index = columns.index("project_key")
            touched.update(row[key_index] for row in rows if row[key_index])

        # Electrical Details is keyed from the parent filings because the
        # details feed itself publishes no date suitable for windowed backfill.
        detail_fetched = detail_upserted = detail_bad = 0
        if source == "electrical" and records:
            detail_records = ElectricalDetailsClient(NYC_APP_TOKEN).fetch_for_filings(
                record.get("job_filing_number") for record in records
            )
            detail_rows, detail_bad = prepare_electrical_detail_rows(detail_records)
            detail_upserted, detail_failed = db.upsert_electrical_details(detail_rows)
            if detail_failed:
                raise RuntimeError(
                    f"electrical details: {detail_failed} database chunk(s) failed"
                )
            detail_key_index = ELECTRICAL_DETAIL_COLUMNS.index("project_key")
            touched.update(
                row[detail_key_index] for row in detail_rows if row[detail_key_index]
            )
            detail_fetched = len(detail_records)
        fetched += len(records) + detail_fetched
        upserted += affected + detail_upserted
        skipped += bad + detail_bad
        if len(records) < API_BATCH_SIZE:
            break
        offset += API_BATCH_SIZE
    return touched, fetched, upserted, skipped


def sync_projects_with_reconnect(db, touched):
    for attempt in range(1, 7):
        try:
            return sync_project_intelligence(db.conn, touched)
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            if attempt > 5:
                raise
            print(
                f"   project refresh disconnected; reconnecting ({attempt}/5)",
                flush=True,
            )
            reconnect_database(db, "project refresh")


def main():
    parser = argparse.ArgumentParser(description="Backfill DOB NOW project intelligence")
    parser.add_argument("--start", default="2016-01-01", help="YYYY-MM-DD")
    parser.add_argument("--end", default=date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument("--window-days", type=int, default=31)
    parser.add_argument(
        "--refresh-existing-only", action="store_true",
        help="Consolidate all permit rows already in PostgreSQL without API fetches",
    )
    parser.add_argument("--project-batch-size", type=int, default=10000)
    parser.add_argument(
        "--after-project-key", default="",
        help="Resume --refresh-existing-only after this confirmed project key",
    )
    parser.add_argument(
        "--sources", nargs="+",
        choices=("filings", "approved", "electrical", "elevator", "city_record"),
        default=("filings", "approved", "electrical", "elevator", "city_record"),
    )
    args = parser.parse_args()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    if (start > end or args.window_days < 1 or
            args.project_batch_size < 1):
        parser.error("dates and batch/window sizes must be valid positive values")

    db = PermitDatabase(DB_CONFIG)
    db.connect()
    if args.refresh_existing_only:
        try:
            totals = refresh_existing_projects(
                db, args.project_batch_size, args.after_project_key
            )
        finally:
            db.close()
        print(
            "\n✅ Existing project consolidation complete: "
            f"{totals['projects']} projects, {totals['alerts']} alerts"
        )
        return

    totals = {"fetched": 0, "upserted": 0, "skipped": 0, "projects": 0}
    try:
        clients = {
            "filings": DOBNowFilingsClient(),
            "approved": DOBNowApprovedClient(),
            "electrical": DOBNowElectricalClient(NYC_APP_TOKEN),
            "elevator": ElevatorApplicationsClient(NYC_APP_TOKEN),
            "city_record": CityRecordClient(NYC_APP_TOKEN),
        }
        for window_start, window_end in windows(start, end, args.window_days):
            print(f"\n📅 {window_start} through {window_end}")
            touched = set()
            for source in args.sources:
                keys, fetched, upserted, skipped = ingest_pages(
                    db, clients[source], window_start, window_end, source
                )
                touched.update(keys)
                totals["fetched"] += fetched
                totals["upserted"] += upserted
                totals["skipped"] += skipped
                print(f"   {source}: {fetched} fetched, {upserted} upserted, {skipped} skipped")
            result = sync_projects_with_reconnect(db, touched)
            totals["projects"] += result["projects"]
            print(f"   projects refreshed: {result['projects']}")
    finally:
        db.close()

    print("\n✅ Backfill complete")
    print("   " + ", ".join(f"{key}: {value}" for key, value in totals.items()))


if __name__ == "__main__":
    main()
