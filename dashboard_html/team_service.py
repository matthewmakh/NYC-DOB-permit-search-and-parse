"""Sponsored team-account access and billing ownership.

An active sponsorship deliberately answers two separate questions:

* Access: the member may use the application without buying another subscription.
* Billing: paid enrichment initiated by the member is charged to the sponsor.

Keeping those decisions separate prevents an admin sponsor's complimentary personal
access from accidentally making every employee lookup complimentary as well.
"""

import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta

import psycopg2
from psycopg2.extras import RealDictCursor


ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', 'matt@tyeny.com').strip().lower()
INVITE_LIFETIME_DAYS = 7
EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


CREATE_SPONSORSHIPS_SQL = """
CREATE TABLE IF NOT EXISTS account_sponsorships (
    id SERIAL PRIMARY KEY,
    sponsor_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    member_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    member_email VARCHAR(255) NOT NULL,
    display_name VARCHAR(120),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'revoked', 'expired')),
    subscription_bypass BOOLEAN NOT NULL DEFAULT TRUE,
    bill_usage_to_sponsor BOOLEAN NOT NULL DEFAULT TRUE,
    invite_token_hash VARCHAR(64),
    invite_expires_at TIMESTAMP,
    accepted_at TIMESTAMP,
    revoked_at TIMESTAMP,
    created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (sponsor_user_id <> member_user_id)
)
"""

TEAM_SCHEMA_STATEMENTS = [
    CREATE_SPONSORSHIPS_SQL,
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_account_sponsorships_live_email
       ON account_sponsorships ((LOWER(member_email)))
       WHERE status IN ('pending', 'active')""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_account_sponsorships_active_member
       ON account_sponsorships (member_user_id)
       WHERE status = 'active' AND member_user_id IS NOT NULL""",
    """CREATE INDEX IF NOT EXISTS idx_account_sponsorships_sponsor
       ON account_sponsorships (sponsor_user_id, status, created_at DESC)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_account_sponsorships_invite_token
       ON account_sponsorships (invite_token_hash)
       WHERE invite_token_hash IS NOT NULL""",
    """DO $$ BEGIN
           IF to_regclass('public.enrichment_transactions') IS NOT NULL THEN
               ALTER TABLE enrichment_transactions
               ADD COLUMN IF NOT EXISTS billing_user_id INTEGER REFERENCES users(id);
           END IF;
       END $$""",
    """DO $$ BEGIN
           IF to_regclass('public.bulk_enrich_jobs') IS NOT NULL THEN
               ALTER TABLE bulk_enrich_jobs
               ADD COLUMN IF NOT EXISTS billing_user_id INTEGER REFERENCES users(id);
           END IF;
       END $$""",
]


def get_db_connection():
    """Open a short-lived connection using the same environment precedence as the app."""
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        return psycopg2.connect(
            database_url,
            connect_timeout=5,
            options='-c statement_timeout=30000',
            cursor_factory=RealDictCursor,
        )
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        connect_timeout=5,
        options='-c statement_timeout=30000',
        cursor_factory=RealDictCursor,
    )


def init_team_tables():
    """Idempotently install the sponsored-account schema on worker startup."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        for statement in TEAM_SCHEMA_STATEMENTS:
            cur.execute(statement)
        # The requested owner account remains authoritative even if it pre-dates
        # ADMIN_EMAIL handling in signup.
        cur.execute(
            """UPDATE users
               SET is_admin = TRUE, subscription_status = 'active'
               WHERE LOWER(email) = %s""",
            (ADMIN_EMAIL,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def normalize_email(email):
    return str(email or '').strip().lower()


def hash_invite_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def build_access_context(row):
    """Turn one joined user/sponsorship row into access and payer decisions."""
    if not row:
        return None

    context = dict(row)
    is_admin = bool(context.get('is_admin'))
    own_active = context.get('subscription_status') == 'active'
    sponsorship_active = (
        context.get('sponsorship_status') == 'active'
        and bool(context.get('subscription_bypass'))
        and (
            bool(context.get('sponsor_is_admin'))
            or context.get('sponsor_subscription_status') == 'active'
        )
    )

    if is_admin:
        access_source = 'admin'
    elif sponsorship_active:
        access_source = 'sponsored'
    elif own_active:
        access_source = 'direct'
    else:
        access_source = 'none'

    sponsored_billing = (
        sponsorship_active
        and bool(context.get('bill_usage_to_sponsor'))
        and context.get('sponsor_user_id') is not None
    )
    if is_admin:
        billing_user_id = context.get('id')
        should_charge = False
    elif sponsored_billing:
        billing_user_id = context.get('sponsor_user_id')
        should_charge = True
    else:
        billing_user_id = context.get('id')
        should_charge = own_active

    context.update({
        'has_access': access_source != 'none',
        'access_source': access_source,
        'is_sponsored': sponsorship_active,
        'billing_user_id': billing_user_id,
        'billing_email': (
            context.get('sponsor_email') if sponsored_billing else context.get('email')
        ),
        'should_charge_usage': should_charge,
    })
    return context


ACCESS_CONTEXT_SQL = """
SELECT u.id, u.email, u.password_hash, u.is_admin, u.is_verified,
       u.subscription_status, u.stripe_customer_id, u.stripe_subscription_id,
       u.created_at, u.last_login,
       team.sponsorship_id, team.sponsorship_status,
       team.subscription_bypass, team.bill_usage_to_sponsor,
       team.sponsor_user_id, team.sponsor_email, team.sponsor_is_admin,
       team.sponsor_subscription_status, team.sponsor_stripe_customer_id
FROM users u
LEFT JOIN LATERAL (
    SELECT s.id AS sponsorship_id, s.status AS sponsorship_status,
           s.subscription_bypass, s.bill_usage_to_sponsor,
           sponsor.id AS sponsor_user_id, sponsor.email AS sponsor_email,
           sponsor.is_admin AS sponsor_is_admin,
           sponsor.subscription_status AS sponsor_subscription_status,
           sponsor.stripe_customer_id AS sponsor_stripe_customer_id
    FROM account_sponsorships s
    JOIN users sponsor ON sponsor.id = s.sponsor_user_id
    WHERE s.member_user_id = u.id AND s.status = 'active'
    ORDER BY s.accepted_at DESC NULLS LAST, s.id DESC
    LIMIT 1
) team ON TRUE
"""


def get_access_context(user_id=None, email=None, connection=None):
    """Return effective access and usage payer information for one user."""
    if user_id is None and not email:
        return None
    owns_connection = connection is None
    conn = connection or get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        params = (user_id,) if user_id is not None else (normalize_email(email),)
        where_sql = " WHERE u.id = %s" if user_id is not None else " WHERE LOWER(u.email) = %s"
        try:
            cur.execute(ACCESS_CONTEXT_SQL + where_sql, params)
        except psycopg2.errors.UndefinedTable:
            # Makes the feature self-installing for a local first request that
            # reaches /auth/login before app.init_db_pool has run.
            conn.rollback()
            init_team_tables()
            cur.execute(ACCESS_CONTEXT_SQL + where_sql, params)
        return build_access_context(cur.fetchone())
    finally:
        cur.close()
        if owns_connection:
            conn.close()


def list_sponsored_accounts(sponsor_user_id):
    """List current, pending, and revoked accounts with attributable spend."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE account_sponsorships
               SET status = 'expired', updated_at = NOW()
               WHERE sponsor_user_id = %s AND status = 'pending'
                 AND invite_expires_at < NOW()""",
            (sponsor_user_id,),
        )
        cur.execute(
            """
            SELECT s.id, s.member_user_id, s.member_email, s.display_name, s.status,
                   s.subscription_bypass, s.bill_usage_to_sponsor,
                   s.invite_expires_at, s.accepted_at, s.revoked_at, s.created_at,
                   u.last_login,
                   COALESCE(spend.total_spend, 0) AS total_spend,
                   COALESCE(spend.spend_30d, 0) AS spend_30d,
                   COALESCE(spend.lookup_count, 0) AS lookup_count
            FROM account_sponsorships s
            LEFT JOIN users u ON u.id = s.member_user_id
            LEFT JOIN LATERAL (
                SELECT SUM(et.amount) AS total_spend,
                       SUM(et.amount) FILTER (
                           WHERE et.created_at >= NOW() - INTERVAL '30 days'
                       ) AS spend_30d,
                       COUNT(*) AS lookup_count
                FROM enrichment_transactions et
                WHERE et.user_id = s.member_user_id
                  AND COALESCE(et.billing_user_id, et.user_id) = s.sponsor_user_id
                  AND et.status IN ('succeeded', 'requires_capture', 'processing')
            ) spend ON TRUE
            WHERE s.sponsor_user_id = %s
            ORDER BY CASE s.status WHEN 'active' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                     s.created_at DESC
            """,
            (sponsor_user_id,),
        )
        rows = [dict(row) for row in cur.fetchall()]
        conn.commit()
        return rows
    finally:
        cur.close()
        conn.close()


def create_sponsorship(sponsor_user_id, email, display_name=None, created_by_user_id=None):
    """Create an active link for an existing user or a claim link for a new one."""
    member_email = normalize_email(email)
    display_name = str(display_name or '').strip()[:120] or None
    if not EMAIL_RE.match(member_email):
        return False, 'Enter a valid employee email address.', None

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, email, is_admin FROM users WHERE id = %s", (sponsor_user_id,))
        sponsor = cur.fetchone()
        if not sponsor:
            return False, 'Sponsor account not found.', None
        if not sponsor['is_admin']:
            return False, 'Only an admin account can sponsor employees.', None
        if member_email == normalize_email(sponsor['email']):
            return False, 'You cannot add your own account as a team member.', None

        cur.execute(
            """SELECT id, sponsor_user_id, status, member_user_id
               FROM account_sponsorships
               WHERE LOWER(member_email) = %s AND status IN ('pending', 'active')
               FOR UPDATE""",
            (member_email,),
        )
        current = cur.fetchone()
        if current and current['sponsor_user_id'] != sponsor_user_id:
            return False, 'That account already belongs to another sponsor.', None
        if current and current['status'] == 'active':
            return False, 'That employee already has active sponsored access.', None

        cur.execute(
            """SELECT id, is_admin, subscription_status, stripe_subscription_id
               FROM users WHERE LOWER(email) = %s""",
            (member_email,),
        )
        member = cur.fetchone()
        if member and member['is_admin']:
            return False, 'Admin accounts cannot be added as sponsored employees.', None
        if member and member['subscription_status'] == 'active' and member['stripe_subscription_id']:
            return False, (
                'That account has its own paid subscription. Cancel it before moving the user '
                'under sponsored access so they are not billed twice.'
            ), None

        now = datetime.now()
        if member:
            if current:
                cur.execute(
                    """UPDATE account_sponsorships
                       SET member_user_id = %s, display_name = %s, status = 'active',
                           invite_token_hash = NULL, invite_expires_at = NULL,
                           accepted_at = %s, revoked_at = NULL, updated_at = %s
                       WHERE id = %s RETURNING id""",
                    (member['id'], display_name, now, now, current['id']),
                )
                sponsorship_id = cur.fetchone()['id']
            else:
                cur.execute(
                    """INSERT INTO account_sponsorships
                       (sponsor_user_id, member_user_id, member_email, display_name,
                        status, accepted_at, created_by_user_id)
                       VALUES (%s, %s, %s, %s, 'active', %s, %s)
                       RETURNING id""",
                    (sponsor_user_id, member['id'], member_email, display_name,
                     now, created_by_user_id or sponsor_user_id),
                )
                sponsorship_id = cur.fetchone()['id']
            conn.commit()
            return True, 'Existing account linked. Sponsored access is active now.', {
                'id': sponsorship_id,
                'status': 'active',
                'email': member_email,
                'invite_token': None,
            }

        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_invite_token(raw_token)
        expires_at = now + timedelta(days=INVITE_LIFETIME_DAYS)
        if current:
            cur.execute(
                """UPDATE account_sponsorships
                   SET display_name = %s, status = 'pending', invite_token_hash = %s,
                       invite_expires_at = %s, updated_at = %s
                   WHERE id = %s RETURNING id""",
                (display_name, token_hash, expires_at, now, current['id']),
            )
            sponsorship_id = cur.fetchone()['id']
        else:
            cur.execute(
                """INSERT INTO account_sponsorships
                   (sponsor_user_id, member_email, display_name, status,
                    invite_token_hash, invite_expires_at, created_by_user_id)
                   VALUES (%s, %s, %s, 'pending', %s, %s, %s)
                   RETURNING id""",
                (sponsor_user_id, member_email, display_name, token_hash,
                 expires_at, created_by_user_id or sponsor_user_id),
            )
            sponsorship_id = cur.fetchone()['id']
        conn.commit()
        return True, 'Invite created. Copy the secure setup link for the employee.', {
            'id': sponsorship_id,
            'status': 'pending',
            'email': member_email,
            'invite_token': raw_token,
            'invite_expires_at': expires_at,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def regenerate_invitation(sponsor_user_id, sponsorship_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        raw_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=INVITE_LIFETIME_DAYS)
        cur.execute(
            """UPDATE account_sponsorships
               SET status = 'pending', invite_token_hash = %s, invite_expires_at = %s,
                   revoked_at = NULL, updated_at = NOW()
               WHERE id = %s AND sponsor_user_id = %s AND member_user_id IS NULL
               RETURNING id, member_email""",
            (hash_invite_token(raw_token), expires_at, sponsorship_id, sponsor_user_id),
        )
        row = cur.fetchone()
        conn.commit()
        if not row:
            return False, 'Only an unclaimed invitation can be regenerated.', None
        return True, 'A new setup link was created; the previous link no longer works.', {
            'id': row['id'], 'email': row['member_email'], 'invite_token': raw_token,
            'invite_expires_at': expires_at,
        }
    finally:
        cur.close()
        conn.close()


def revoke_sponsorship(sponsor_user_id, sponsorship_id):
    """Revoke sponsored access and invalidate all live sessions for the member."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE account_sponsorships
               SET status = 'revoked', invite_token_hash = NULL,
                   invite_expires_at = NULL, revoked_at = NOW(), updated_at = NOW()
               WHERE id = %s AND sponsor_user_id = %s AND status IN ('pending', 'active')
               RETURNING member_user_id, member_email""",
            (sponsorship_id, sponsor_user_id),
        )
        row = cur.fetchone()
        if not row:
            return False, 'Team account not found or already revoked.', None
        if row['member_user_id']:
            cur.execute("DELETE FROM user_sessions WHERE user_id = %s", (row['member_user_id'],))
        conn.commit()
        return True, 'Sponsored access revoked.', dict(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def reactivate_sponsorship(sponsor_user_id, sponsorship_id):
    """Re-enable an already-claimed team account."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE account_sponsorships
               SET status = 'active', revoked_at = NULL, updated_at = NOW()
               WHERE id = %s AND sponsor_user_id = %s
                 AND status = 'revoked' AND member_user_id IS NOT NULL
               RETURNING id, member_email""",
            (sponsorship_id, sponsor_user_id),
        )
        row = cur.fetchone()
        conn.commit()
        if not row:
            return False, 'Only a previously claimed account can be re-enabled.', None
        return True, 'Sponsored access restored.', dict(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def get_invitation(raw_token):
    token_hash = hash_invite_token(str(raw_token or ''))
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT s.id, s.member_email, s.display_name, s.invite_expires_at,
                      sponsor.email AS sponsor_email
               FROM account_sponsorships s
               JOIN users sponsor ON sponsor.id = s.sponsor_user_id
               WHERE s.invite_token_hash = %s AND s.status = 'pending'""",
            (token_hash,),
        )
        row = cur.fetchone()
        if not row or not row['invite_expires_at'] or row['invite_expires_at'] < datetime.now():
            return None
        return dict(row)
    finally:
        cur.close()
        conn.close()


def accept_invitation(raw_token, password):
    """Claim a pending invitation by creating the employee's login atomically."""
    token_hash = hash_invite_token(str(raw_token or ''))
    if len(password or '') < 8:
        return False, 'Password must be at least 8 characters.', None

    # Import here to avoid an auth_service -> team_service import cycle.
    from auth_service import hash_password

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT id, member_email, invite_expires_at
               FROM account_sponsorships
               WHERE invite_token_hash = %s AND status = 'pending'
               FOR UPDATE""",
            (token_hash,),
        )
        invite = cur.fetchone()
        if not invite or not invite['invite_expires_at'] or invite['invite_expires_at'] < datetime.now():
            return False, 'This setup link is invalid or expired.', None

        cur.execute("SELECT id FROM users WHERE LOWER(email) = %s", (invite['member_email'],))
        if cur.fetchone():
            return False, 'An account now exists for this email. Ask the admin to link it again.', None

        cur.execute(
            """INSERT INTO users
               (email, password_hash, is_admin, is_verified, subscription_status)
               VALUES (%s, %s, FALSE, TRUE, 'inactive')
               RETURNING id""",
            (invite['member_email'], hash_password(password)),
        )
        user_id = cur.fetchone()['id']
        cur.execute(
            """UPDATE account_sponsorships
               SET member_user_id = %s, status = 'active', accepted_at = NOW(),
                   invite_token_hash = NULL, invite_expires_at = NULL, updated_at = NOW()
               WHERE id = %s""",
            (user_id, invite['id']),
        )
        conn.commit()
        return True, 'Account ready.', user_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
