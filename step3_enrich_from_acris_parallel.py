#!/usr/bin/env python3
"""
Step 3 (parallel): ACRIS enrichment with a thread pool.

All fetch/role/persistence logic lives in step3_enrich_from_acris — this
wrapper only adds concurrency. That means the B1 (party roles), B3 (cash
window), and E1 (batched fetch) fixes apply here automatically instead of
living in a second, drift-prone copy.
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

import _pipeline_path  # noqa: F401  (puts dashboard_html on sys.path)
from step3_enrich_from_acris import (
    DATABASE_URL, enrich_building_from_acris, get_document_ids_for_bbl,
)

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

load_dotenv()

MAX_WORKERS = int(os.getenv('MAX_WORKERS', '8'))

_print_lock = Lock()


def _process_building(building, position, total, force):
    building_id = building['id']
    bbl = building['bbl']
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        if not force:
            doc_ids = get_document_ids_for_bbl(bbl)
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(DISTINCT document_id) AS existing_count
                FROM acris_transactions WHERE building_id = %s
            """, (building_id,))
            existing = (cur.fetchone() or {}).get('existing_count') or 0
            if existing > 0 and len(doc_ids) == existing:
                cur.execute("""
                    UPDATE buildings
                    SET acris_last_enriched = CURRENT_TIMESTAMP,
                        last_updated = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (building_id,))
                conn.commit()
                cur.close()
                with _print_lock:
                    print(f"[{position}/{total}] BBL {bbl}: unchanged ({existing} docs)")
                return 'skipped'
            cur.close()

        count = enrich_building_from_acris(conn, building_id, bbl)
        with _print_lock:
            print(f"[{position}/{total}] BBL {bbl}: {count} transactions")
        return 'enriched' if count else 'no_data'
    except Exception as e:
        conn.rollback()
        with _print_lock:
            print(f"[{position}/{total}] BBL {bbl}: ❌ {e}")
        return 'failed'
    finally:
        conn.close()


def main():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, bbl, address
        FROM buildings
        WHERE bbl IS NOT NULL
        AND (acris_last_enriched IS NULL
             OR acris_last_enriched < NOW() - INTERVAL '30 days')
        ORDER BY id
    """)
    buildings = cur.fetchall()
    cur.close()
    conn.close()

    force = os.getenv('ACRIS_FORCE_REFRESH') == '1'
    print(f"Step 3 (parallel): {len(buildings)} buildings, {MAX_WORKERS} workers"
          + (" — FORCE REFRESH" if force else ""))

    if not buildings:
        print("Nothing to do.")
        return

    results = {'enriched': 0, 'skipped': 0, 'no_data': 0, 'failed': 0}
    started = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_process_building, b, i, len(buildings), force): b
            for i, b in enumerate(buildings, 1)
        }
        for future in as_completed(futures):
            results[future.result()] += 1

    elapsed = time.time() - started
    print(f"\nDone in {elapsed/60:.1f} min — "
          f"enriched {results['enriched']}, unchanged {results['skipped']}, "
          f"no data {results['no_data']}, failed {results['failed']}")


if __name__ == "__main__":
    main()
