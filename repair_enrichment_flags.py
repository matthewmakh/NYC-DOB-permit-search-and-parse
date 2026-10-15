"""Preview or repair derived flags without altering their underlying source records."""
import argparse
from datetime import date, timedelta
import _pipeline_path  # noqa: F401
import psycopg2
from migrate_add_freshness_and_jobs import database_dsn
from step4_enrich_from_tax_liens import LIEN_RECENCY_MONTHS


def repair(conn, apply=False):
    cutoff = date.today() - timedelta(days=LIEN_RECENCY_MONTHS * 30)
    repairs = [
        ('unsupported_cash', 'is_cash_purchase=NULL',
         'is_cash_purchase=TRUE AND (sale_price IS NULL OR sale_price<1000 '
         'OR (sale_percent_transferred IS NOT NULL AND sale_percent_transferred<>100))', ()),
        ('stale_lien_notice', 'has_tax_delinquency=FALSE',
         'has_tax_delinquency=TRUE AND tax_delinquency_latest_date<%s', (cutoff,)),
    ]
    counts = {}
    with conn:
        with conn.cursor() as cur:
            for name, assignment, predicate, params in repairs:
                if apply:
                    cur.execute(f'UPDATE buildings SET {assignment} WHERE {predicate}', params)
                    counts[name] = cur.rowcount
                else:
                    cur.execute(f'SELECT COUNT(*) FROM buildings WHERE {predicate}', params)
                    counts[name] = cur.fetchone()[0]
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    conn = psycopg2.connect(database_dsn(), connect_timeout=10)
    try:
        print('Applied' if args.apply else 'Preview', repair(conn, args.apply))
    finally:
        conn.close()
