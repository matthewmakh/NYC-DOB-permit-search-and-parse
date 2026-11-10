#!/usr/bin/env python3
"""Migration: investor loan program tags (DSCR / RTL) on recorded mortgages.

Adds, idempotently (IF NOT EXISTS everywhere):
- acris_transactions.loan_program / loan_program_lender / loan_program_confidence /
  loan_program_basis: the program of the lender on each mortgage, written by
  dashboard_html/lender_programs.py (pipeline step 3b and every ACRIS refresh)
- buildings summary columns for filtering, sorting and the plays:
  investor_loan_program / _lender / _confidence / _amount / _date / _count,
  rtl_loan_last_date, dscr_loan_last_date, lender_programs_version,
  lender_programs_tagged_at

The pipeline runs this before step 1; the dashboard reads the columns
defensively, so deploy order does not matter.
"""

import sys

import psycopg2
from dotenv import load_dotenv

from migrate_add_freshness_and_jobs import database_dsn

sys.stdout.reconfigure(line_buffering=True)
load_dotenv()
load_dotenv('dashboard_html/.env')

TRANSACTION_COLUMNS = [
    ('loan_program', 'VARCHAR(30)'),
    ('loan_program_lender', 'VARCHAR(160)'),
    ('loan_program_confidence', 'VARCHAR(10)'),
    ('loan_program_basis', 'VARCHAR(40)'),
]
BUILDING_COLUMNS = [
    ('investor_loan_program', 'VARCHAR(30)'),
    ('investor_loan_lender', 'VARCHAR(160)'),
    ('investor_loan_confidence', 'VARCHAR(10)'),
    ('investor_loan_amount', 'NUMERIC(14,2)'),
    ('investor_loan_date', 'DATE'),
    ('investor_loan_count', 'INTEGER'),
    ('rtl_loan_last_date', 'DATE'),
    ('dscr_loan_last_date', 'DATE'),
    ('lender_programs_version', 'VARCHAR(30)'),
    ('lender_programs_tagged_at', 'TIMESTAMP'),
]
INDEXES = [
    """CREATE INDEX IF NOT EXISTS idx_acris_trans_loan_program
       ON acris_transactions (building_id, loan_program) WHERE loan_program IS NOT NULL""",
    """CREATE INDEX IF NOT EXISTS idx_buildings_investor_loan_date
       ON buildings (investor_loan_date DESC) WHERE investor_loan_program IS NOT NULL""",
    """CREATE INDEX IF NOT EXISTS idx_buildings_rtl_loan_last_date
       ON buildings (rtl_loan_last_date DESC) WHERE rtl_loan_last_date IS NOT NULL""",
]


def _existing(cur, sql, params):
    cur.execute(sql, params)
    return {row[0] for row in cur.fetchall()}


def apply(cur):
    """Only touch what is missing, so the nightly re-run takes no table locks."""
    column_sql = """SELECT column_name FROM information_schema.columns
                    WHERE table_schema = current_schema() AND table_name = %s"""
    for table, columns in (('acris_transactions', TRANSACTION_COLUMNS), ('buildings', BUILDING_COLUMNS)):
        have = _existing(cur, column_sql, (table,))
        for name, sql_type in columns:
            if name not in have:
                cur.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {sql_type}')
    have_indexes = _existing(cur, """SELECT indexname FROM pg_indexes
                                     WHERE schemaname = current_schema()""", ())
    for statement in INDEXES:
        name = statement.split('IF NOT EXISTS', 1)[1].split()[0]
        if name not in have_indexes:
            cur.execute(statement)


def main():
    conn = psycopg2.connect(database_dsn(), connect_timeout=15)
    try:
        with conn.cursor() as cur:
            # ALTER TABLE needs a brief exclusive lock; fail fast rather than queue
            # behind a long dashboard query and block traffic behind us.
            cur.execute("SET lock_timeout = '30s'")
            apply(cur)
        conn.commit()
        print('✅ Lender program columns and indexes are in place')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
