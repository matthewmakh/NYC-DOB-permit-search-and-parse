#!/usr/bin/env python3
"""Check that production tagging reproduces the research results.

Loads the cached NYC mortgages (build_cache.py output) into a DISPOSABLE
PostgreSQL database in the table layout step 3 writes, runs the production
migration and lender_programs.run_batch, and compares every document's
program with tag_and_test.py's test_results/tagged_loans.csv.

    python3 verify_production_tagging.py nyc_mtge.db postgresql://user:pass@localhost/scratch_db

The target database's buildings / acris_transactions / acris_parties tables
are dropped and recreated: never point this at a real database.
"""
import csv
import os
import sqlite3
import sys
from collections import Counter

import psycopg2
from psycopg2.extras import execute_values

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'dashboard_html'))
sys.path.insert(0, os.path.join(HERE, '..'))
import lender_programs  # noqa: E402
import migrate_add_lender_programs  # noqa: E402

RESEARCH_TAG = {
    'RTL': 'RTL', 'DSCR': 'DSCR', 'COMMERCIAL_BRIDGE': 'COMMERCIAL_BRIDGE',
    'DSCR or RTL (lender makes both)': 'DSCR_OR_RTL',
    'DSCR (entity borrower, lender also does consumer loans)': 'DSCR',
    'consumer lender, individual borrower (not tagged)': None,
}


def load(cache, dsn):
    src, dst = sqlite3.connect(cache), psycopg2.connect(dsn)
    cur = dst.cursor()
    cur.execute("""
    DROP TABLE IF EXISTS acris_parties, acris_transactions, buildings CASCADE;
    CREATE TABLE buildings (id SERIAL PRIMARY KEY, bbl VARCHAR(10) UNIQUE, acris_last_enriched TIMESTAMP);
    CREATE TABLE acris_transactions (
        id SERIAL PRIMARY KEY, building_id INT REFERENCES buildings(id) ON DELETE CASCADE,
        document_id VARCHAR(50) NOT NULL, doc_type VARCHAR(20), doc_amount NUMERIC, doc_date DATE,
        recorded_date DATE, crfn VARCHAR(50), bbl VARCHAR(10),
        CONSTRAINT unique_doc_per_building UNIQUE (building_id, document_id));
    CREATE TABLE acris_parties (
        id SERIAL PRIMARY KEY, transaction_id INT REFERENCES acris_transactions(id) ON DELETE CASCADE,
        party_type VARCHAR(20), party_name VARCHAR(255), address_1 VARCHAR(255), address_2 VARCHAR(255),
        city VARCHAR(100), state VARCHAR(2), zip_code VARCHAR(10), building_id INT);
    CREATE INDEX ON acris_parties(transaction_id);
    """)
    first_lot = {}
    for doc, b, bl, lo in src.execute("SELECT document_id, borough, block, lot FROM legal ORDER BY rowid"):
        if doc not in first_lot and b and bl and lo:
            first_lot[doc] = f"{b}{int(bl):05d}{int(lo):04d}"
    execute_values(cur, "INSERT INTO buildings (bbl, acris_last_enriched) VALUES %s",
                   [(b, '2026-01-01') for b in sorted(set(first_lot.values()))], page_size=5000)
    cur.execute("SELECT bbl, id FROM buildings")
    building = dict(cur.fetchall())
    rows = [(building[first_lot[d]], d, t, a, dd or None, rd or None, c, first_lot[d])
            for d, t, a, dd, rd, c in src.execute(
                "SELECT document_id, doc_type, amt, doc_date, recorded, crfn FROM master") if d in first_lot]
    execute_values(cur, """INSERT INTO acris_transactions (building_id, document_id, doc_type, doc_amount,
                           doc_date, recorded_date, crfn, bbl) VALUES %s""", rows, page_size=5000)
    cur.execute("SELECT document_id, id, building_id FROM acris_transactions")
    txn = {d: (i, b) for d, i, b in cur.fetchall()}
    role = {'1': 'borrower', '2': 'lender'}       # ACRIS MTGE: party 1 borrower, party 2 lender
    parties = [(txn[d][0], role[pt], (n or '')[:255], a1, a2, city, (st or '')[:2], (z or '')[:10], txn[d][1])
               for d, pt, n, a1, a2, city, st, z in src.execute(
                   "SELECT document_id, party_type, name, a1, a2, city, state, zip FROM party")
               if d in txn and pt in role]
    execute_values(cur, """INSERT INTO acris_parties (transaction_id, party_type, party_name, address_1,
                           address_2, city, state, zip_code, building_id) VALUES %s""", parties, page_size=5000)
    dst.commit()
    return dst, len(rows)


def main():
    cache, dsn = sys.argv[1], sys.argv[2]
    research = sys.argv[3] if len(sys.argv) > 3 else os.path.join(HERE, 'test_results', 'tagged_loans.csv')
    conn, mortgages = load(cache, dsn)
    with conn.cursor() as cur:
        migrate_add_lender_programs.apply(cur)
    conn.commit()
    stats = lender_programs.run_batch(conn, log=lambda *_: None)
    with conn.cursor() as cur:
        cur.execute("SELECT document_id, loan_program FROM acris_transactions")
        production = dict(cur.fetchall())
    expected = {r['document_id']: RESEARCH_TAG[r['loan_tag']] for r in csv.DictReader(open(research))}
    disagree = Counter((expected[d], production.get(d)) for d in expected if production.get(d) != expected[d])
    extra = [d for d, p in production.items() if p and d not in expected]
    print(f"{mortgages:,} mortgages · {stats['buildings']:,} buildings tagged · "
          f"{stats['mortgages_tagged']:,} investor mortgages")
    print(f"agree on {len(expected) - sum(disagree.values()):,}/{len(expected):,} research-tagged mortgages; "
          f"disagreements {dict(disagree) or 'none'}; production-only tags {len(extra)}")
    sys.exit(1 if disagree or extra else 0)


if __name__ == '__main__':
    main()
