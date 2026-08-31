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
    "ALTER TABLE bulk_enrich_jobs ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'enformion_fallback'",
    "ALTER TABLE bulk_enrich_jobs ADD COLUMN IF NOT EXISTS billing_user_id INTEGER REFERENCES users(id)",
]

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_bulk_enrich_jobs_user ON bulk_enrich_jobs(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_bulk_enrich_jobs_status ON bulk_enrich_jobs(status)",
]


def init_bulk_enrich_jobs_table():
    """Idempotently create the bulk_enrich_jobs table. Safe to call at every worker start."""
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(CREATE_TABLE_SQL)
        for alter_sql in ALTER_TABLE_SQL:
            cur.execute(alter_sql)
        for idx_sql in CREATE_INDEXES_SQL:
            cur.execute(idx_sql)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[bulk_enrich] init_bulk_enrich_jobs_table error: {e}")


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
        if job_id in _RUNNING_JOBS:
            return False
        _RUNNING_JOBS.add(job_id)

    t = threading.Thread(target=_run_job_safe, args=(job_id,), daemon=True,
                         name=f"bulk-enrich-job-{job_id}")
    t.start()
    return True


def _run_job_safe(job_id):
    try:
        _run_job(job_id)
    except Exception as e:
        traceback.print_exc()
        try:
            _set_status(job_id, 'failed', error_message=str(e))
        except Exception:
            pass
    finally:
        with _RUNNING_JOBS_LOCK:
            _RUNNING_JOBS.discard(job_id)


def _run_job(job_id):
    """Process every building_id in the job, enriching owners per the chosen strategy.

    Lazy-imports of enrichment_service / stripe_service avoid a circular import
    at module load time.
    """
    from enrichment_service import (
        get_available_owners_for_enrichment,
        enrich_owner,
        filter_owners_by_strategy,
        revoke_owner_enrichment_access,
    )
    from stripe_service import charge_batch_enrichment_total

    job = get_job(job_id)
    if not job:
        return

    user_id = job['user_id']
    is_admin = job['is_admin']
    owner_strategy = job['owner_strategy']
    provider = job.get('provider') or 'enformion_fallback'
    building_ids = list(job['building_ids'] or [])

    _set_status(job_id, 'running')

    enriched_building_ids = []
    enrichment_details = []  # for the final Stripe charge metadata

    for bid in building_ids:
        if _check_cancel_requested(job_id):
            break

        try:
            owners = get_available_owners_for_enrichment(bid, user_id)
            available = [o for o in owners if not o.get('already_enriched')]
            chosen = filter_owners_by_strategy(available, owner_strategy)
        except Exception as e:
            print(f"[bulk_enrich job {job_id}] owner lookup failed for building {bid}: {e}")
            _increment_counters(job_id, properties_processed=1)
            continue

        if not chosen:
            _increment_counters(job_id, properties_processed=1, skipped=len(available))
            continue

        any_success_for_building = False
        for owner in chosen:
            if _check_cancel_requested(job_id):
                break
            try:
                # enrich_owner resolves authoritative street/borough/ZIP from
                # the building row; no client/worker-composed address needed.
                success, _data, _msg = enrich_owner(
                    bid, owner['name'], '', user_id, provider=provider)
                if success:
                    any_success_for_building = True
                    _increment_counters(job_id, attempted=1, successful=1)
                    enrichment_details.append({'building_id': bid, 'owner': owner['name']})
                else:
                    _increment_counters(job_id, attempted=1, failed=1)
            except Exception as e:
                print(f"[bulk_enrich job {job_id}] enrich_owner crashed for {owner['name']} @ {bid}: {e}")
                _increment_counters(job_id, attempted=1, failed=1)
            # Be gentle with the upstream API; tweak if you have a real rate limit.
            time.sleep(0.05)

        if any_success_for_building:
            enriched_building_ids.append(bid)
        _increment_counters(job_id, properties_processed=1)

    # Determine terminal status
    final_status = 'cancelled' if _check_cancel_requested(job_id) else 'completed'

    # Charge once at the end for everything that succeeded
    job_after = get_job(job_id) or {}
    successful = int(job_after.get('owners_successful', 0))

    charge_message = None
    total_charged = 0.0
    if successful > 0 and not is_admin:
        try:
            ok, msg, _charge_id = charge_batch_enrichment_total(
                user_id, enriched_building_ids, successful, enrichment_details,
                idempotency_key=f'bulk-enrich-job-{job_id}',
            )
            if ok:
                calculated = successful * float(job_after.get('cost_per_lookup', 0.35))
                total_charged = max(calculated, 0.50)
                charge_message = msg
            else:
                charge_message = f"Enrichment finished but payment failed: {msg}"
                print(f"[bulk_enrich job {job_id}] CHARGE FAILED: {msg}")
                for detail in enrichment_details:
                    revoke_owner_enrichment_access(
                        user_id, detail['building_id'], detail['owner'])
        except Exception as e:
            charge_message = f"Charge error: {e}"
            print(f"[bulk_enrich job {job_id}] CHARGE ERROR: {e}")
            for detail in enrichment_details:
                try:
                    revoke_owner_enrichment_access(
                        user_id, detail['building_id'], detail['owner'])
                except Exception:
                    pass

    # Final state write
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE bulk_enrich_jobs
            SET status = %s,
                total_charged = %s,
                error_message = COALESCE(error_message, %s),
                completed_at = NOW(),
                last_updated_at = NOW()
            WHERE id = %s
            """,
            (final_status, total_charged, charge_message, job_id),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def resume_orphaned_jobs():
    """On worker startup, look for jobs stuck in 'running' (e.g., previous container crashed)
    and mark them 'failed'. We do NOT auto-resume because we cannot guarantee that
    in-flight charges weren't issued, and partial double-charging is worse than a
    user re-kicking the job."""
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE bulk_enrich_jobs
            SET status = 'failed',
                error_message = COALESCE(error_message, 'Worker restarted before job completed'),
                completed_at = NOW(),
                last_updated_at = NOW()
            WHERE status IN ('pending', 'running', 'cancel_requested')
              AND last_updated_at < NOW() - INTERVAL '5 minutes'
            """
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[bulk_enrich] resume_orphaned_jobs error: {e}")
