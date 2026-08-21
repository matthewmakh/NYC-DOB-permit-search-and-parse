#!/usr/bin/env python3
"""
One-time retroactive repair for data written by the pre-fix pipeline.

B1 — ACRIS party roles were inverted (party_type 1/2 hardcoded backwards):
  1. Swap buildings.sale_buyer_primary <-> sale_seller_primary
  2. Relabel acris_parties rows: 'buyer' <-> 'seller'
  3. Recompute is_lead on the (now correctly labeled) sellers
  4. Delete 'lender' party rows — they were actually the borrowers — and
     clear mortgage_lender_primary (real lenders backfill on re-enrichment)
  5. Clear SOS results that were derived from the swapped buyer field
     (they identified the principal of the SELLING LLC)
  6. Reset ACRIS enrichment eligibility so the corrected pipeline refetches
     everything (run with ACRIS_FORCE_REFRESH=1, see below)

B4 — tax delinquency flags included lien-sale notices from any year:
  7. Reset tax_lien_last_checked so step4 re-evaluates every building under
     the new most-recent-cycle rule (clears stale flags on its next run)

Optional --backfill-new-fields: fetches PLUTO + HPD for every building and
fills ONLY the new columns (FAR/zoning/lat-lon, HPD mailing address). Use
after running migrate_add_intel_signals.py; existing owner data is not
touched. ~5 API calls per building.

Dry-run by default. Pass --apply to execute.
Afterwards run:
    ACRIS_FORCE_REFRESH=1 python step3_enrich_from_acris_parallel.py
    python step4_enrich_from_tax_liens.py
    python step5_enrich_from_sos.py
"""

import argparse
import os
import sys
import time

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

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

SOS_RESET_FIELDS = [
    'sos_principal_name', 'sos_principal_title', 'sos_principal_street',
    'sos_principal_city', 'sos_principal_state', 'sos_principal_zip',
    'sos_entity_name', 'sos_entity_status', 'sos_dos_id',
    'sos_formation_date', 'sos_last_enriched', 'sos_lookup_source',
]


def count(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchone()[0]


def repair(conn, apply_changes):
    cur = conn.cursor()

    print("=" * 70)
    print("Retroactive repair — B1 (ACRIS roles) + B4 (lien-sale recency)")
    print(f"Mode: {'APPLY' if apply_changes else 'DRY RUN (pass --apply to execute)'}")
    print("=" * 70)

    n_names = count(cur, """
        SELECT COUNT(*) FROM buildings
        WHERE sale_buyer_primary IS NOT NULL OR sale_seller_primary IS NOT NULL
    """)
    n_buyer_rows = count(cur, "SELECT COUNT(*) FROM acris_parties WHERE party_type = 'buyer'")
    n_seller_rows = count(cur, "SELECT COUNT(*) FROM acris_parties WHERE party_type = 'seller'")
    n_lender_rows = count(cur, "SELECT COUNT(*) FROM acris_parties WHERE party_type = 'lender'")
    n_sos = count(cur, """
        SELECT COUNT(*) FROM buildings WHERE sos_lookup_source = 'sale_buyer_primary'
    """)
    n_acris_enriched = count(cur, "SELECT COUNT(*) FROM buildings WHERE acris_last_enriched IS NOT NULL")
    n_lien_checked = count(cur, "SELECT COUNT(*) FROM buildings WHERE tax_lien_last_checked IS NOT NULL")
    n_flagged = count(cur, "SELECT COUNT(*) FROM buildings WHERE has_tax_delinquency = TRUE")

    print(f"""
Plan:
  1. Swap buyer/seller name columns on {n_names} buildings
  2. Relabel {n_buyer_rows} 'buyer' and {n_seller_rows} 'seller' party rows (swap)
  3. Recompute lead flags on the corrected sellers
  4. Delete {n_lender_rows} mislabeled 'lender' rows + clear mortgage_lender_primary
  5. Clear SOS results on {n_sos} buildings looked up via the swapped buyer field
  6. Reset ACRIS eligibility on {n_acris_enriched} buildings (refetch fills lenders + equity signals)
  7. Reset tax-lien check on {n_lien_checked} buildings ({n_flagged} currently flagged delinquent)
""")

    if not apply_changes:
        cur.close()
        return

    # 1. Swap the summary name columns (single pass, row-wise swap).
    cur.execute("""
        UPDATE buildings
        SET sale_buyer_primary = sale_seller_primary,
            sale_seller_primary = sale_buyer_primary
        WHERE sale_buyer_primary IS NOT NULL OR sale_seller_primary IS NOT NULL
    """)
    print(f"  ✅ 1. Swapped names on {cur.rowcount} buildings")

    # 2. Swap the party labels.
    cur.execute("""
        UPDATE acris_parties
        SET party_type = CASE party_type
            WHEN 'buyer' THEN 'seller'
            WHEN 'seller' THEN 'buyer'
        END
        WHERE party_type IN ('buyer', 'seller')
    """)
    print(f"  ✅ 2. Relabeled {cur.rowcount} party rows")

    # 3. Lead flag belongs on sellers with a mailing address.
    cur.execute("""
        UPDATE acris_parties
        SET is_lead = (party_type = 'seller'
                       AND address_1 IS NOT NULL AND address_1 <> '')
        WHERE party_type IN ('buyer', 'seller')
    """)
    print(f"  ✅ 3. Recomputed lead flags on {cur.rowcount} rows")

    # 4. 'lender' rows were borrowers; real lenders come from re-enrichment.
    cur.execute("DELETE FROM acris_parties WHERE party_type = 'lender'")
    deleted = cur.rowcount
    cur.execute("""
        UPDATE buildings SET mortgage_lender_primary = NULL
        WHERE mortgage_lender_primary IS NOT NULL
    """)
    print(f"  ✅ 4. Deleted {deleted} borrower-as-lender rows, cleared {cur.rowcount} lender names")

    # 5. SOS lookups that chased the selling LLC's principal.
    set_nulls = ', '.join(f"{f} = NULL" for f in SOS_RESET_FIELDS)
    cur.execute(f"""
        UPDATE buildings SET {set_nulls}
        WHERE sos_lookup_source = 'sale_buyer_primary'
    """)
    print(f"  ✅ 5. Cleared SOS data on {cur.rowcount} buildings (step5 re-runs them)")

    # 6. Force ACRIS refetch under the corrected role mapping.
    cur.execute("UPDATE buildings SET acris_last_enriched = NULL WHERE acris_last_enriched IS NOT NULL")
    print(f"  ✅ 6. Reset ACRIS eligibility on {cur.rowcount} buildings")

    # 7. Re-evaluate lien-sale status under the recency rule.
    cur.execute("UPDATE buildings SET tax_lien_last_checked = NULL WHERE tax_lien_last_checked IS NOT NULL")
    print(f"  ✅ 7. Reset tax-lien checks on {cur.rowcount} buildings")

    conn.commit()
    cur.close()
    print("""
Committed. Now refetch with the corrected pipeline:
    ACRIS_FORCE_REFRESH=1 python step3_enrich_from_acris_parallel.py
    python step4_enrich_from_tax_liens.py
    python step5_enrich_from_sos.py
""")


def backfill_new_fields(conn, apply_changes):
    """Populate the new PLUTO/HPD columns for buildings that already have
    owner data (the nightly step2 only touches buildings missing owners)."""
    from step2_enrich_from_pluto import get_pluto_data_for_bbl, get_hpd_data_for_bbl

    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, bbl FROM buildings
        WHERE bbl IS NOT NULL AND unused_far IS NULL
        ORDER BY id
    """)
    buildings = cur.fetchall()
    print(f"\nBackfill: {len(buildings)} buildings missing the new PLUTO/HPD fields")
    if not apply_changes:
        print("  (dry run — pass --apply to execute)")
        cur.close()
        return

    updated = 0
    for i, b in enumerate(buildings, 1):
        fields = {}
        pluto, _ = get_pluto_data_for_bbl(b['bbl'])
        if pluto:
            fields.update({
                'zip_code': pluto['zip_code'],
                'latitude': pluto['latitude'],
                'longitude': pluto['longitude'],
                'zoning_district': pluto['zoning_district'],
                'built_far': pluto['built_far'],
                'max_resid_far': pluto['max_resid_far'],
                'max_comm_far': pluto['max_comm_far'],
                'unused_far': pluto['unused_far'],
                'pluto_owner_type': pluto['pluto_owner_type'],
            })
        hpd, _ = get_hpd_data_for_bbl(b['bbl'])
        if hpd:
            fields.update({
                'hpd_owner_business_address': hpd['hpd_owner_business_address'],
                'hpd_owner_business_city': hpd['hpd_owner_business_city'],
                'hpd_owner_business_state': hpd['hpd_owner_business_state'],
                'hpd_owner_business_zip': hpd['hpd_owner_business_zip'],
                'hpd_agent_name': hpd['hpd_agent_name'],
                'hpd_site_manager_name': hpd['hpd_site_manager_name'],
            })
        fields = {k: v for k, v in fields.items() if v is not None}
        if fields:
            set_clause = ', '.join(f"{k} = %s" for k in fields)
            cur.execute(f"UPDATE buildings SET {set_clause} WHERE id = %s",
                        list(fields.values()) + [b['id']])
            conn.commit()
            updated += 1
        if i % 50 == 0:
            print(f"  [{i}/{len(buildings)}] {updated} updated")
        time.sleep(0.05)

    print(f"  ✅ Backfilled {updated}/{len(buildings)} buildings")
    cur.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true',
                        help='Execute the repair (default is dry run)')
    parser.add_argument('--backfill-new-fields', action='store_true',
                        help='Also fetch PLUTO/HPD to fill the new columns on every building')
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    try:
        repair(conn, args.apply)
        if args.backfill_new_fields:
            backfill_new_fields(conn, args.apply)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
