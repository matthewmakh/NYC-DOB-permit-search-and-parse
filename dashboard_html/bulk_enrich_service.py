"""
Background bulk enrichment job service.

Runs long-running enrichments in a background daemon thread per worker.
Job state is persisted in `bulk_enrich_jobs` so the UI can poll progress
even across page reloads. The actual Enformion API calls happen in
`enrichment_service.enrich_owner`.
"""

import json
import os
import threading
import time
import traceback
from datetime import datetime

import psycopg2
import psycopg2.extras


# ---------------------------------------------------------------------------
# DB connection helpers
# ---------------------------------------------------------------------------
# We deliberately open a fresh connection per write rather than share the
# Flask request pool. This thread runs outside any Flask request context, and
# long-lived workers should not hold pool connections for tens of minutes.

def _get_conn():
    # DATABASE_URL wins when set, matching app.py; connect_timeout so a busy
    # database fails this worker's write fast instead of hanging the thread.
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        return psycopg2.connect(database_url, connect_timeout=10)
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        connect_timeout=10,
    )


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS bulk_enrich_jobs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    billing_user_id INTEGER REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'pending',
    owner_strategy TEXT NOT NULL DEFAULT 'recommended',
    provider TEXT NOT NULL DEFAULT 'enformion_fallback',
    filters_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    building_ids INTEGER[] NOT NULL DEFAULT '{}',
    total_properties INTEGER NOT NULL DEFAULT 0,
    total_owners_planned INTEGER NOT NULL DEFAULT 0,
    properties_processed INTEGER NOT NULL DEFAULT 0,
    owners_attempted INTEGER NOT NULL DEFAULT 0,
    owners_successful INTEGER NOT NULL DEFAULT 0,
    owners_failed INTEGER NOT NULL DEFAULT 0,
    owners_skipped INTEGER NOT NULL DEFAULT 0,
    cost_per_lookup NUMERIC(10, 4) NOT NULL DEFAULT 0.35,
    estimated_max_cost NUMERIC(10, 2) NOT NULL DEFAULT 0,
    total_charged NUMERIC(10, 2) NOT NULL DEFAULT 0,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# Run after CREATE so the column also lands on existing tables from earlier deploys.
ALTER_TABLE_SQL = [
    "ALTER TABLE bulk_enrich_jobs ADD COLUMN IF NOT EXISTS prepared_at TIMESTAMPTZ",
    "ALTER TABLE bulk_enrich_jobs ADD COLUMN IF NOT EXISTS billing_started_at TIMESTAMPTZ",
    "ALTER TABLE bulk_enrich_jobs ADD COLUMN IF NOT EXISTS payment_intent_id TEXT",
    "ALTER TABLE bulk_enrich_jobs ADD COLUMN IF NOT EXISTS final_status TEXT",

    "ALTER TABLE bulk_enrich_jobs ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'enformion_fallback'",
    "ALTER TABLE bulk_enrich_jobs ADD COLUMN IF NOT EXISTS billing_user_id INTEGER REFERENCES users(id)",
]

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_bulk_enrich_jobs_user ON bulk_enrich_jobs(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_bulk_enrich_jobs_status ON bulk_enrich_jobs(status)",
]


def init_bulk_enrich_jobs_table():
    """Idempotently create the bulk_enrich_jobs table. Safe to call at every worker start."""
    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute('SELECT pg_advisory_xact_lock(72108,0)')
        cur.execute(CREATE_TABLE_SQL)
        for alter_sql in ALTER_TABLE_SQL:
            cur.execute(alter_sql)
        for idx_sql in CREATE_INDEXES_SQL:
            cur.execute(idx_sql)
        from paid_enrichment_store import SCHEMA_SQL
        cur.execute(SCHEMA_SQL)
        cur.execute("""CREATE TABLE IF NOT EXISTS bulk_enrich_items (
            job_id INTEGER REFERENCES bulk_enrich_jobs(id) ON DELETE CASCADE,
            building_id INTEGER REFERENCES buildings(id) ON DELETE CASCADE,
            owner_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            error TEXT, PRIMARY KEY(job_id,building_id,owner_name))""")
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[bulk_enrich] init_bulk_enrich_jobs_table error: {e}")
        raise
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# Job CRUD
# ---------------------------------------------------------------------------

def create_job(user_id, filters, building_ids, total_owners_planned,
               estimated_max_cost, cost_per_lookup, is_admin, owner_strategy,
               billing_user_id=None,
               provider='enformion_fallback'):
    """Insert a new job row in 'pending' status. Returns the job id."""
    conn = _get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            INSERT INTO bulk_enrich_jobs
                (user_id, billing_user_id, status, owner_strategy, provider, filters_json, building_ids,
                 total_properties, total_owners_planned,
                 cost_per_lookup, estimated_max_cost, is_admin)
            VALUES (%s, %s, 'pending', %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                user_id, billing_user_id, owner_strategy, provider,
                json.dumps(filters or {}), list(building_ids),
                len(building_ids), total_owners_planned,
                cost_per_lookup, estimated_max_cost, is_admin,
            ),
        )
        job_id = cur.fetchone()['id']
        conn.commit()
        return job_id
    finally:
        cur.close()
        conn.close()


def get_job(job_id, user_id=None):
    """Fetch a job row. If user_id is provided, restricts to that user (for auth checks)."""
    conn = _get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        if user_id is not None:
            cur.execute(
                "SELECT * FROM bulk_enrich_jobs WHERE id = %s AND user_id = %s",
                (job_id, user_id),
            )
        else:
            cur.execute("SELECT * FROM bulk_enrich_jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()
        conn.close()


def request_cancel(job_id, user_id):
    """Mark the job as 'cancel_requested'. The worker thread will see this and stop."""
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE bulk_enrich_jobs
            SET status = 'cancel_requested', last_updated_at = NOW()
            WHERE id = %s AND user_id = %s AND status IN ('pending', 'running')
            RETURNING id
            """,
            (job_id, user_id),
        )
        updated = cur.fetchone()
        conn.commit()
        return updated is not None
    finally:
        cur.close()
        conn.close()


def _set_status(job_id, status, error_message=None):
    conn = _get_conn()
    cur = conn.cursor()
    try:
        if status == 'running':
            cur.execute(
                """
                UPDATE bulk_enrich_jobs
                SET status = %s, started_at = COALESCE(started_at, NOW()), last_updated_at = NOW()
                WHERE id = %s
                """,
                (status, job_id),
            )
        elif status in ('completed', 'failed', 'cancelled'):
            cur.execute(
                """
                UPDATE bulk_enrich_jobs
                SET status = %s, completed_at = NOW(), last_updated_at = NOW(),
                    error_message = COALESCE(%s, error_message)
                WHERE id = %s
                """,
                (status, error_message, job_id),
            )
        else:
            cur.execute(
                "UPDATE bulk_enrich_jobs SET status = %s, last_updated_at = NOW() WHERE id = %s",
                (status, job_id),
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _check_cancel_requested(job_id):
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT status FROM bulk_enrich_jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        return row and row[0] == 'cancel_requested'
    finally:
        cur.close()
        conn.close()


def _increment_counters(job_id, successful=0, failed=0, skipped=0, properties_processed=0,
                       attempted=0):
    """Atomically bump the counters and refresh last_updated_at."""
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE bulk_enrich_jobs
            SET owners_successful   = owners_successful   + %s,
                owners_failed       = owners_failed       + %s,
                owners_skipped      = owners_skipped      + %s,
                owners_attempted    = owners_attempted    + %s,
                properties_processed = properties_processed + %s,
                last_updated_at = NOW()
            WHERE id = %s
            """,
            (successful, failed, skipped, attempted, properties_processed, job_id),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def list_recent_jobs_for_user(user_id, limit=10):
    """Return the user's most recent jobs (for showing 'resume' UI on page load)."""
    conn = _get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            """
            SELECT id, status, owner_strategy, provider, total_properties, total_owners_planned,
                   properties_processed, owners_attempted, owners_successful, owners_failed, owners_skipped,
                   estimated_max_cost, total_charged, is_admin, created_at, completed_at, error_message
            FROM bulk_enrich_jobs
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (user_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

_RUNNING_JOBS = set()
_RUNNING_JOBS_LOCK = threading.Lock()


def start_job_worker(job_id):
    """Spawn a daemon thread to run the job. Idempotent: ignores duplicates."""
    with _RUNNING_JOBS_LOCK:
        if job_id in _RUNNING_JOBS or len(_RUNNING_JOBS) >= max(1, int(os.getenv('BULK_ENRICH_WORKERS','2'))):
            return False
        _RUNNING_JOBS.add(job_id)

    t = threading.Thread(target=_run_job_safe, args=(job_id,), daemon=True,
                         name=f"bulk-enrich-job-{job_id}")
    t.start()
    return True


def _run_job_safe(job_id):
    try:
        _run_job(job_id)
    except Exception:
        # Keep durable progress recoverable, including an uncertain payment.
        traceback.print_exc()
    finally:
        with _RUNNING_JOBS_LOCK:
            _RUNNING_JOBS.discard(job_id)


def _run_job(job_id):
    from enrichment_service import (get_available_owners_for_enrichment,
                                    filter_owners_by_strategy, enrich_owner)
    from stripe_service import charge_batch_enrichment_total
    from paid_enrichment_store import grant_result

    conn = _get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute('SELECT pg_try_advisory_lock(72104,%s) AS acquired', (job_id,))
        if not cur.fetchone()['acquired']:
            return
        conn.commit()
        job = get_job(job_id)
        if not job or job['status'] not in ('pending','running','cancel_requested','charging'):
            return
        user_id = job['user_id']
        if not job['prepared_at'] and job['status'] != 'cancel_requested':
            # Freeze the payable set before any provider requests; never exceed
            # the quantity the user reviewed when creating this job.
            items = []
            for bid in job['building_ids']:
                owners = get_available_owners_for_enrichment(bid, user_id)
                available = [o for o in owners if not o.get('already_enriched')]
                for owner in filter_owners_by_strategy(available, job['owner_strategy']):
                    items.append((job_id,bid,owner['name'].strip().upper()))
            items = list(dict.fromkeys(items))[:job['total_owners_planned']]
            for item in items:
                cur.execute("""INSERT INTO bulk_enrich_items(job_id,building_id,owner_name)
                    VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""", item)
            cur.execute("""UPDATE bulk_enrich_jobs SET prepared_at=NOW(),
                status=CASE WHEN status='cancel_requested' THEN status ELSE 'running' END,
                started_at=COALESCE(started_at,NOW()) WHERE id=%s""", (job_id,))
            conn.commit()

        if job['status'] != 'charging':
            cur.execute("""SELECT building_id,owner_name FROM bulk_enrich_items
                WHERE job_id=%s AND status='pending' ORDER BY building_id,owner_name""", (job_id,))
            items = cur.fetchall()
            conn.commit()
            for item in items:
                if _check_cancel_requested(job_id):
                    break
                success, _data, message = enrich_owner(
                    item['building_id'], item['owner_name'], '', user_id,
                    provider=job['provider'], bulk_job_id=job_id)
                cur.execute("""UPDATE bulk_enrich_items SET status=%s,error=%s
                    WHERE job_id=%s AND building_id=%s AND owner_name=%s""",
                    ('succeeded' if success else 'failed', None if success else message,
                     job_id,item['building_id'],item['owner_name']))
                cur.execute("""UPDATE bulk_enrich_jobs SET
                    owners_successful=(SELECT COUNT(*) FROM bulk_enrich_items WHERE job_id=%s AND status='succeeded'),
                    owners_failed=(SELECT COUNT(*) FROM bulk_enrich_items WHERE job_id=%s AND status='failed'),
                    owners_attempted=(SELECT COUNT(*) FROM bulk_enrich_items WHERE job_id=%s AND status<>'pending'),
                    properties_processed=(SELECT COUNT(DISTINCT building_id) FROM bulk_enrich_items WHERE job_id=%s AND status<>'pending'),
                    last_updated_at=NOW() WHERE id=%s""", (job_id,)*5)
                conn.commit()
            cur.execute("""UPDATE bulk_enrich_jobs SET
                final_status=CASE WHEN status='cancel_requested' THEN 'cancelled' ELSE 'completed' END,
                status='charging', billing_started_at=COALESCE(billing_started_at,NOW()),
                last_updated_at=NOW() WHERE id=%s""", (job_id,))
            conn.commit()

        job = get_job(job_id)
        cur.execute("""SELECT building_id,owner_name FROM bulk_enrich_items
            WHERE job_id=%s AND status='succeeded' ORDER BY building_id,owner_name""", (job_id,))
        results = cur.fetchall()
        conn.commit()
        payment_id = job['payment_intent_id']
        count = len(results)
        if count and not job['is_admin'] and not payment_id:
            details = [{'building_id':r['building_id'],'owner':r['owner_name']} for r in results]
            ok, message, payment_id = charge_batch_enrichment_total(
                user_id, sorted({r['building_id'] for r in results}), count, details,
                idempotency_key=f'bulk-enrich-job-{job_id}')
            if not ok:
                if 'reconciliation' in message.lower():
                    _set_status(job_id, 'failed', message)
                    return
                cur.execute("""UPDATE bulk_enrich_jobs SET error_message=%s,last_updated_at=NOW()
                    WHERE id=%s""", (f'Payment pending: {message}',job_id))
                conn.commit()
                return
            cur.execute("""UPDATE bulk_enrich_jobs SET payment_intent_id=%s,
                total_charged=%s WHERE id=%s""",
                (payment_id,max(round(count*0.35,2),0.50),job_id))
            conn.commit()
        receipt = payment_id or 'admin_free'
        for result in results:
            grant_result(cur,user_id,result['building_id'],result['owner_name'],receipt)
        cur.execute("""UPDATE bulk_enrich_jobs SET status=%s,completed_at=NOW(),
            last_updated_at=NOW(),error_message=NULL,owners_successful=%s,
            properties_processed=CASE WHEN %s='completed' THEN total_properties ELSE properties_processed END
            WHERE id=%s""", (job['final_status'],count,job['final_status'],job_id))
        conn.commit()
    finally:
        conn.close()  # Releases the cross-worker lock, including after a crash.


_RECOVERY_STARTED = False


def resume_orphaned_jobs():
    """Recover persisted work at startup and after transient worker failures."""
    global _RECOVERY_STARTED
    if _RECOVERY_STARTED:
        return
    _RECOVERY_STARTED = True

    def recover():
        while True:
            try:
                conn = _get_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute("""SELECT id FROM bulk_enrich_jobs
                            WHERE status IN ('pending','running','cancel_requested','charging')
                            ORDER BY last_updated_at LIMIT 25""")
                        jobs = cur.fetchall()
                    for (job_id,) in jobs:
                        start_job_worker(job_id)
                finally:
                    conn.close()
            except Exception as exc:
                print(f'[bulk_enrich] recovery error: {exc}')
            time.sleep(60)

    threading.Thread(target=recover,daemon=True,name='bulk-enrich-recovery').start()
