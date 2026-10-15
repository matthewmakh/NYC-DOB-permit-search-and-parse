#!/usr/bin/env python3
"""Read-only database audit for the Properties page prebuilt filters.

Uses the same server-owned predicates as the app, but executes each count
independently so one broken play cannot hide the rest. The connection is
explicitly read-only. Run with DATABASE_URL set::

    python3 audit_play_counts.py
"""

import os
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'dashboard_html'))
from plays import available_plays


def columns(cur, table):
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = %s
    """, (table,))
    return {row['column_name'] for row in cur.fetchall()}


def main():
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        raise SystemExit('DATABASE_URL is required')

    conn = psycopg2.connect(
        database_url,
        cursor_factory=RealDictCursor,
        connect_timeout=10,
        options='-c statement_timeout=30000',
    )
    conn.set_session(readonly=True, autocommit=True)
    try:
        cur = conn.cursor()
        building_columns = columns(cur, 'buildings')
        permit_columns = columns(cur, 'permits')
        if not building_columns or not permit_columns:
            raise SystemExit('required buildings/permits tables were not found')

        cur.execute('SELECT COUNT(*) AS count FROM buildings')
        total_buildings = int(cur.fetchone()['count'])
        cur.execute('SELECT COUNT(*) AS count FROM permits')
        total_permits = int(cur.fetchone()['count'])
        print(f'Buildings: {total_buildings:,}  Permits: {total_permits:,}')
        freshness_fields = [
            ('property_last_enriched', 'Property facts'),
            ('acris_last_enriched', 'ACRIS history'),
            ('signals_last_enriched', 'Legacy signal attempts'),
        ]
        for field, label in freshness_fields:
            if field not in building_columns:
                continue
            cur.execute(
                f"""SELECT COUNT(*) FILTER (WHERE {field} IS NOT NULL) AS covered,
                           MAX({field}) AS latest
                    FROM buildings""")
            row = cur.fetchone()
            print(f"{label}: {int(row['covered'] or 0):,}/{total_buildings:,}"
                  f"  latest={row['latest'] or 'never'}")
        if 'signals_enrichment_version' in building_columns:
            cur.execute("""
                SELECT COUNT(*) FILTER (WHERE signals_enrichment_version >= 2) AS current,
                       COUNT(*) FILTER (WHERE signals_last_error IS NOT NULL) AS errors
                FROM buildings
            """)
            row = cur.fetchone()
            print(f"Current signal version: {int(row['current'] or 0):,}/{total_buildings:,}"
                  f"  source errors={int(row['errors'] or 0):,}")
        print('-' * 96)
        print(f'{"Play":34s} {"Matches":>12s}  {"Coverage":>14s}  Status')
        print('-' * 96)

        plays = available_plays(building_columns, permit_columns)
        for play in plays:
            try:
                cur.execute(
                    f"SELECT COUNT(*) AS count FROM buildings b WHERE {play['where']}")
                match_text = f"{int(cur.fetchone()['count']):,}"
                status = 'ok'
            except Exception as exc:
                match_text = 'ERROR'
                status = f'{type(exc).__name__}: {exc}'.replace('\n', ' ')[:120]

            coverage_text = 'n/a'
            required_building = set(play.get('coverage_required_columns', []))
            required_permit = set(play.get('coverage_required_permit_columns', []))
            if (play.get('coverage_where')
                    and required_building <= building_columns
                    and required_permit <= permit_columns):
                try:
                    cur.execute(
                        f"SELECT COUNT(*) AS count FROM buildings b "
                        f"WHERE {play['coverage_where']}")
                    covered = int(cur.fetchone()['count'])
                    coverage_text = f'{covered:,}/{total_buildings:,}'
                except Exception as exc:
                    coverage_text = 'ERROR'
                    if status == 'ok':
                        status = f'coverage {type(exc).__name__}: {exc}'[:120]

            print(f"{play['id']:34s} {match_text:>12s}  {coverage_text:>14s}  {status}")

        available_count = len(plays)
        print('-' * 96)
        print(f'{available_count} plays available for the current schema.')
        cur.close()
    finally:
        conn.close()


if __name__ == '__main__':
    main()
