#!/usr/bin/env python3
"""Create one daily change digest per enabled sales watchlist.

The digest is always stored in PostgreSQL.  If WATCHLIST_DIGEST_WEBHOOK_URL is
configured, the same payload is also sent to an email/Slack/automation relay.
This script is safe to schedule after the permit sync: the period key is unique
and repeated runs update rather than duplicate the digest.
"""

import argparse
import json
import os
from datetime import date, datetime, timedelta
from decimal import Decimal

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import Json, RealDictCursor

from project_intelligence import ensure_project_intelligence_schema


load_dotenv()
load_dotenv("dashboard_html/.env")

BUYER_KEY_SQL = (
    "UPPER(REGEXP_REPLACE(COALESCE(pr.owner_business_name, ''), "
    "'[^A-Za-z0-9]+', '', 'g'))"
)


def connect():
    database_url = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME", "railway"),
    )


def json_ready(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def main(store_only=False):
    conn = connect()
    ensure_project_intelligence_schema(conn)
    now = datetime.now().replace(microsecond=0)
    webhook_url = (
        "" if store_only
        else os.getenv("WATCHLIST_DIGEST_WEBHOOK_URL", "").strip()
    )
    webhook_token = os.getenv("WATCHLIST_DIGEST_WEBHOOK_BEARER_TOKEN", "").strip()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT w.id, w.name, w.user_id, w.last_digest_at, u.email
                FROM watchlists w
                LEFT JOIN users u ON u.id = w.user_id
                WHERE w.digest_enabled = TRUE
                ORDER BY w.id
            """)
            watchlists = cur.fetchall()

            total_events = 0
            created_count = 0
            for watchlist in watchlists:
                if (watchlist['last_digest_at'] and
                        now - watchlist['last_digest_at'] < timedelta(hours=20)):
                    continue
                period_start = watchlist['last_digest_at'] or (now - timedelta(days=1))
                cur.execute(f"""
                    SELECT DISTINCT sa.id, sa.alert_type, sa.title, sa.summary,
                           sa.event_at, pr.project_key, pr.job_number, pr.address,
                           pr.bbl, pr.owner_business_name, pr.current_status,
                           pr.initial_cost
                    FROM sales_alerts sa
                    JOIN projects pr ON pr.id = sa.project_id
                    WHERE sa.event_at > %s AND sa.event_at <= %s
                      AND EXISTS (
                          SELECT 1 FROM watchlist_items wi
                          WHERE wi.watchlist_id = %s AND (
                              (wi.entity_type = 'project' AND wi.entity_key = pr.project_key)
                              OR (wi.entity_type = 'property' AND wi.entity_key = pr.bbl)
                              OR (wi.entity_type = 'buyer' AND wi.entity_key = {BUYER_KEY_SQL})
                          )
                      )
                    ORDER BY sa.event_at DESC
                """, (period_start, now, watchlist['id']))
                events = [
                    {key: json_ready(value) for key, value in dict(row).items()}
                    for row in cur.fetchall()
                ]
                cur.execute("""
                    SELECT id, source_record_id, signal_type, title, agency_name,
                           category, selection_method, pin, notice_date, due_date,
                           contact_name, contact_phone, contact_email, vendor_name,
                           contract_amount, relevance_score, relevance_reasons,
                           source_url
                    FROM external_project_signals
                    WHERE source = 'city_record'
                      AND relevance_score >= 25
                      AND notice_date >= %s::date
                      AND notice_date <= %s::date
                      AND review_status = 'new'
                    ORDER BY relevance_score DESC, due_date ASC NULLS LAST
                    LIMIT 100
                """, (period_start, now))
                external_signals = [
                    {key: json_ready(value) for key, value in dict(row).items()}
                    for row in cur.fetchall()
                ]
                payload = {
                    'watchlist_id': watchlist['id'],
                    'watchlist_name': watchlist['name'],
                    'recipient': watchlist['email'],
                    'period_start': period_start.isoformat(),
                    'period_end': now.isoformat(),
                    'event_count': len(events) + len(external_signals),
                    'events': events,
                    'external_signals': external_signals,
                }
                delivery = 'stored'
                if webhook_url:
                    headers = {'Content-Type': 'application/json'}
                    if webhook_token:
                        headers['Authorization'] = f'Bearer {webhook_token}'
                    try:
                        response = requests.post(
                            webhook_url, json=payload, headers=headers, timeout=30
                        )
                        response.raise_for_status()
                        delivery = 'delivered'
                    except requests.RequestException as exc:
                        delivery = 'delivery_failed'
                        print(f"Watchlist {watchlist['id']} webhook failed: {exc}")

                cur.execute("""
                    INSERT INTO watchlist_digests
                        (watchlist_id, period_start, period_end, event_count,
                         payload, delivery_status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (watchlist_id, period_start, period_end)
                    DO UPDATE SET event_count = EXCLUDED.event_count,
                                  payload = EXCLUDED.payload,
                                  delivery_status = EXCLUDED.delivery_status
                """, (watchlist['id'], period_start, now,
                      len(events) + len(external_signals),
                      Json(payload), delivery))
                cur.execute("""
                    UPDATE watchlists SET last_digest_at = %s,
                        updated_at = CURRENT_TIMESTAMP WHERE id = %s
                """, (now, watchlist['id']))
                total_events += len(events) + len(external_signals)
                created_count += 1
            conn.commit()
            print(f"Created {created_count} watchlist digests with {total_events} events")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Store watchlist digests and optionally deliver them to a webhook"
    )
    parser.add_argument(
        "--store-only", action="store_true",
        help="Write digests to PostgreSQL without sending an external webhook",
    )
    main(store_only=parser.parse_args().store_only)
