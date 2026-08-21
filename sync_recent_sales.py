#!/usr/bin/env python3
"""
Recent-sales sync — the staleness fixer.

The per-building ACRIS refresh only re-checks each building every 30 days,
so a sale recorded yesterday can sit invisible for weeks. This script works
the other way around: it sweeps ALL deeds recorded citywide in the last
LOOKBACK_DAYS (via the pre-filtered "ACRIS DEEDs" view), maps them to BBLs
through the Legals dataset, and immediately flags matching buildings for
re-enrichment so the nightly pipeline refreshes them on its next run.

Run daily or weekly (cheap: one paginated sweep + batched legals lookups).

    python sync_recent_sales.py            # last 10 days
    LOOKBACK_DAYS=30 python sync_recent_sales.py
"""

import os
import sys
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from socrata_client import SocrataClient

sys.stdout.reconfigure(line_buffering=True)

load_dotenv()
load_dotenv('dashboard_html/.env')

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME')
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        raise ValueError("Either DATABASE_URL or DB_HOST/DB_USER/DB_PASSWORD/DB_NAME must be set")
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

LOOKBACK_DAYS = int(os.getenv('LOOKBACK_DAYS', '10'))


def make_bbl(borough, block, lot):
    try:
        return f"{int(borough)}{int(block):05d}{int(lot):04d}"
    except (ValueError, TypeError):
        return None


def main():
    client = SocrataClient()
    since = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime('%Y-%m-%dT00:00:00')

    print(f"Recent-sales sync: deeds recorded since {since[:10]}")

    # 1. Every deed recorded citywide in the window (pre-filtered view of
    #    the ACRIS master, so no doc_type filtering needed).
    deeds = client.get_all('acris_deeds_view', page_size=1000, max_rows=200000, **{
        '$select': 'document_id,recorded_datetime,document_amt',
        '$where': f"recorded_datetime >= '{since}'",
    })
    doc_ids = sorted({d['document_id'] for d in deeds if d.get('document_id')})
    print(f"   {len(doc_ids)} deeds recorded citywide")
    if not doc_ids:
        return

    # 2. Map documents to lots through Legals (batched).
    legals = client.get_batched('acris_legals', 'document_id', doc_ids,
                                select='document_id,borough,block,lot')
    bbls = {make_bbl(l.get('borough'), l.get('block'), l.get('lot')) for l in legals}
    bbls.discard(None)
    print(f"   {len(bbls)} distinct BBLs touched")

    # 3. Flag the ones we track for immediate re-enrichment.
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        UPDATE buildings
        SET acris_last_enriched = NULL
        WHERE bbl = ANY(%s)
        AND (acris_last_enriched IS NOT NULL)
    """, (sorted(bbls),))
    flagged = cur.rowcount
    cur.execute("SELECT COUNT(*) FROM buildings WHERE bbl = ANY(%s)", (sorted(bbls),))
    tracked = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    print(f"   ✅ {tracked} of those BBLs are in our database; "
          f"{flagged} flagged for re-enrichment on the next pipeline run")
    print(f"   ℹ️  {len(bbls) - tracked} recently-sold properties are NOT tracked yet "
          f"(potential lead-source expansion)")


if __name__ == "__main__":
    main()
