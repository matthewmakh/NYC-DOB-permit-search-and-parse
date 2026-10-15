#!/usr/bin/env python3
"""
Migration: columns and tables for the data-intake overhaul.

Adds (all idempotent — IF NOT EXISTS everywhere):
- PLUTO development-upside + geometry fields (FAR, zoning, lat/lon)
- HPD registered-owner mailing address + agent/site-manager names
- ACRIS equity signals (open mortgages, free-and-clear, satisfactions)
  and the acris_references linkage table + remarks column
- Tax lien-sale cycle date
- step6 signal columns: HPD litigation, evictions, DOF exemptions,
  Speculation Watch List, DOB complaints, Certificates of Occupancy,
  FISP facade status, LL84 energy, rolling sales

Run BEFORE deploying the updated pipeline (the pipeline degrades
gracefully without these columns, but only stores the new signals once
they exist).
"""

import os
import sys
import psycopg2
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

BUILDINGS_COLUMNS = [
    # PLUTO development upside + geometry
    ("zip_code", "VARCHAR(10)"),
    ("latitude", "NUMERIC(9,6)"),
    ("longitude", "NUMERIC(9,6)"),
    ("zoning_district", "VARCHAR(20)"),
    ("built_far", "NUMERIC(8,2)"),
    ("max_resid_far", "NUMERIC(8,2)"),
    ("max_comm_far", "NUMERIC(8,2)"),
    ("unused_far", "NUMERIC(8,2)"),
    ("pluto_owner_type", "VARCHAR(5)"),

    # HPD registered-owner mailing address (free skip-trace data)
    ("hpd_owner_business_address", "VARCHAR(255)"),
    ("hpd_owner_business_city", "VARCHAR(100)"),
    ("hpd_owner_business_state", "VARCHAR(10)"),
    ("hpd_owner_business_zip", "VARCHAR(10)"),
    ("hpd_agent_name", "VARCHAR(255)"),
    ("hpd_site_manager_name", "VARCHAR(255)"),

    # ACRIS equity signals
    ("has_open_mortgage", "BOOLEAN"),
    ("is_free_and_clear", "BOOLEAN"),
    ("open_mortgage_count", "INTEGER DEFAULT 0"),
    ("last_satisfaction_date", "DATE"),

    # Tax lien sale (cycle-scoped)
    ("tax_delinquency_latest_date", "DATE"),

    # HPD housing litigation
    ("litigation_count", "INTEGER DEFAULT 0"),
    ("litigation_open_count", "INTEGER DEFAULT 0"),
    ("litigation_last_case_type", "VARCHAR(100)"),
    ("litigation_last_open_date", "DATE"),

    # Marshal evictions
    ("eviction_count", "INTEGER DEFAULT 0"),
    ("eviction_last_date", "DATE"),

    # DOF exemptions (senior/disabled owner-occupants = motivated sellers)
    ("exemption_count", "INTEGER DEFAULT 0"),
    ("exemption_codes", "TEXT"),
    ("has_senior_exemption", "BOOLEAN DEFAULT FALSE"),
    ("has_disabled_exemption", "BOOLEAN DEFAULT FALSE"),

    # HPD Speculation Watch List
    ("on_speculation_watch_list", "BOOLEAN DEFAULT FALSE"),
    ("speculation_watch_date", "DATE"),

    # DOB complaints
    ("dob_complaint_count", "INTEGER DEFAULT 0"),
    ("dob_active_complaint_count", "INTEGER DEFAULT 0"),
    ("dob_last_complaint_date", "DATE"),

    # Certificates of Occupancy (completion / freshness signal)
    ("co_count", "INTEGER DEFAULT 0"),
    ("latest_co_date", "DATE"),
    ("latest_co_type", "VARCHAR(30)"),
    ("latest_co_job_number", "VARCHAR(30)"),

    # FISP / LL11 facade compliance
    ("fisp_status", "VARCHAR(40)"),
    ("fisp_cycle", "VARCHAR(10)"),
    ("fisp_filing_date", "DATE"),

    # LL84 energy + LL97 exposure
    ("energy_star_score", "INTEGER"),
    ("site_eui", "NUMERIC(10,1)"),
    ("ll84_year", "INTEGER"),
    ("ll97_covered_estimated", "BOOLEAN DEFAULT FALSE"),

    # DOF rolling sales cross-check
    ("rolling_sale_price", "NUMERIC"),
    ("rolling_sale_date", "DATE"),
    ("rolling_sale_ppsf", "NUMERIC(10,2)"),

    ("signals_last_enriched", "TIMESTAMP"),
    # Versioned freshness prevents a code/schema fix from inheriting a stale
    # "success" timestamp written by an older, incomplete fetcher. Step 6
    # advances this only after every source succeeds.
    ("signals_enrichment_version", "INTEGER NOT NULL DEFAULT 0"),
    ("signals_last_error", "TEXT"),
    ("signals_last_error_at", "TIMESTAMP"),
]

INDEXES = [
    ("idx_buildings_free_and_clear", "buildings", "is_free_and_clear"),
    ("idx_buildings_unused_far", "buildings", "unused_far"),
    ("idx_buildings_speculation", "buildings", "on_speculation_watch_list"),
    ("idx_buildings_senior_exemption", "buildings", "has_senior_exemption"),
    ("idx_buildings_litigation_open", "buildings", "litigation_open_count"),
    ("idx_buildings_eviction_count", "buildings", "eviction_count"),
    ("idx_buildings_latest_co", "buildings", "latest_co_date"),
    ("idx_buildings_lien_latest", "buildings", "tax_delinquency_latest_date"),
    ("idx_buildings_signals_version", "buildings", "signals_enrichment_version"),
    ("idx_acris_references_building", "acris_references", "building_id"),
    ("idx_acris_references_doc", "acris_references", "document_id"),
]


def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    print("Migration: intel signals")
    print("=" * 60)

    added = 0
    for column, ddl in BUILDINGS_COLUMNS:
        cur.execute(f"ALTER TABLE buildings ADD COLUMN IF NOT EXISTS {column} {ddl}")
        added += 1
    print(f"✅ buildings: ensured {added} columns")

    cur.execute("ALTER TABLE acris_transactions ADD COLUMN IF NOT EXISTS remarks TEXT")
    print("✅ acris_transactions.remarks")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS acris_references (
            id SERIAL PRIMARY KEY,
            building_id INTEGER REFERENCES buildings(id) ON DELETE CASCADE,
            bbl VARCHAR(10),
            document_id VARCHAR(30) NOT NULL,
            referenced_document_id VARCHAR(30) NOT NULL,
            crfn VARCHAR(30),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("✅ acris_references table")

    for name, table, column in INDEXES:
        cur.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})")
    print(f"✅ {len(INDEXES)} indexes ensured")

    conn.commit()
    cur.close()
    conn.close()
    print("\nDone. Safe to re-run at any time.")


if __name__ == "__main__":
    main()
