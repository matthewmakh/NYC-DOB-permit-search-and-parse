"""Sales CRM data layer.

The golden rule of this module: the scraper-owned permit tables (permits,
contacts, buildings, ...) are read-only to humans; every piece of
human-entered data lives in crm_* tables, joined to permit data by BBL.
Scrapers never overwrite a rep-found phone number; the CRM never dirties
permit data.

Team model — deliberately reuses the existing account_sponsorships system
instead of inventing a parallel roles table:

* A "team" is a sponsor account plus its active sponsored members.
  team_id on every CRM row is the sponsor's user id (or the user's own id
  for an unsponsored account, which forms a team of one).
* Sponsored members are the reps; the sponsor (and any is_admin account)
  is the team's CRM admin.

All timestamps are stored naive-UTC (matching the rest of the app on
Railway); "today" for follow-ups and counters is computed in
America/New_York so a rep's evening does not roll into tomorrow at 8pm.
"""

import os
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor, Json


NY_TZ = ZoneInfo('America/New_York')

STAGES = ['prospect', 'contacted', 'interested', 'quoted', 'nurture', 'won', 'lost', 'client']
STAGE_LABELS = {
    'prospect': 'Prospect', 'contacted': 'Contacted', 'interested': 'Interested',
    'quoted': 'Quoted', 'nurture': 'Nurture', 'won': 'Won', 'lost': 'Lost',
    'client': 'Client',
}
DEAL_STAGES = list(STAGES)
CONTACT_METHODS = ['call', 'text', 'email', 'in_person', 'other']
CONTACT_OUTCOMES = [
    'spoke', 'voicemail', 'no_answer', 'callback_requested',
    'meeting_set', 'wrong_number', 'not_interested',
]
OUTCOME_LABELS = {
    'spoke': 'Spoke', 'voicemail': 'Voicemail', 'no_answer': 'No answer',
    'callback_requested': 'Callback requested', 'meeting_set': 'Meeting set',
    'wrong_number': 'Wrong number', 'not_interested': 'Not interested',
}
METHOD_LABELS = {
    'call': 'Call', 'text': 'Text', 'email': 'Email',
    'in_person': 'In person', 'other': 'Other',
}
BUILDING_CONTACT_ROLES = [
    'owner', 'property_manager', 'super', 'board', 'tenant', 'contractor', 'other',
]

# A rep can delete their own fat-fingered activity inside this window;
# team admins can always delete. Deletions recompute the rollups.
ACTIVITY_UNDO_MINUTES = 15

# One view event per user+entity per this many minutes.
VIEW_DEBOUNCE_MINUTES = 30


def get_db_connection():
    """Short-lived connection using the same environment precedence as the app."""
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


# ============================================================
# Schema
# ============================================================

CRM_SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS crm_buildings (
        id SERIAL PRIMARY KEY,
        bbl VARCHAR(10),
        address TEXT NOT NULL,
        borough VARCHAR(30),
        zip_code VARCHAR(15),
        neighborhood VARCHAR(120),
        unit_count INTEGER,
        year_built INTEGER,
        num_floors INTEGER,
        building_class VARCHAR(10),
        owner_name VARCHAR(500),
        stage VARCHAR(20) NOT NULL DEFAULT 'prospect'
            CHECK (stage IN ('prospect','contacted','interested','quoted','won','lost','client')),
        source VARCHAR(20) NOT NULL DEFAULT 'manual'
            CHECK (source IN ('permit','manual','import')),
        last_contacted_at TIMESTAMP,
        last_visited_at TIMESTAMP,
        contact_count INTEGER NOT NULL DEFAULT 0,
        assigned_to_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        added_by_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        team_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_crm_buildings_team_bbl
       ON crm_buildings (team_id, bbl) WHERE bbl IS NOT NULL""",
    """CREATE INDEX IF NOT EXISTS idx_crm_buildings_team_stage
       ON crm_buildings (team_id, stage)""",
    """CREATE INDEX IF NOT EXISTS idx_crm_buildings_team_contacted
       ON crm_buildings (team_id, last_contacted_at DESC NULLS LAST)""",

    """CREATE TABLE IF NOT EXISTS crm_contacts (
        id SERIAL PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        title VARCHAR(150),
        company VARCHAR(255),
        email VARCHAR(255),
        source VARCHAR(30) NOT NULL DEFAULT 'manual'
            CHECK (source IN ('permit','manual','rep_found','import','website')),
        source_detail VARCHAR(255),
        do_not_contact BOOLEAN NOT NULL DEFAULT FALSE,
        last_contacted_at TIMESTAMP,
        added_by_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        team_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS idx_crm_contacts_team ON crm_contacts (team_id, name)""",
    """DO $$ BEGIN
         IF NOT EXISTS (
             SELECT 1 FROM information_schema.columns
             WHERE table_schema = current_schema() AND table_name = 'crm_contacts'
               AND column_name = 'assigned_to_id'
         ) THEN
             ALTER TABLE crm_contacts ADD COLUMN assigned_to_id INTEGER REFERENCES users(id) ON DELETE SET NULL;
             UPDATE crm_contacts SET assigned_to_id = added_by_id WHERE assigned_to_id IS NULL;
         END IF;
       END $$""",
    """CREATE INDEX IF NOT EXISTS idx_crm_contacts_assignee ON crm_contacts (team_id, assigned_to_id)""",

    """CREATE TABLE IF NOT EXISTS crm_phones (
        id SERIAL PRIMARY KEY,
        contact_id INTEGER NOT NULL REFERENCES crm_contacts(id) ON DELETE CASCADE,
        number VARCHAR(50) NOT NULL,
        digits VARCHAR(15) NOT NULL,
        label VARCHAR(50),
        source VARCHAR(30) NOT NULL DEFAULT 'manual',
        source_detail VARCHAR(255),
        status VARCHAR(20) NOT NULL DEFAULT 'good'
            CHECK (status IN ('good','bad','do_not_call')),
        is_primary BOOLEAN NOT NULL DEFAULT FALSE,
        added_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        UNIQUE (contact_id, digits)
    )""",
    """ALTER TABLE crm_phones ADD COLUMN IF NOT EXISTS extension VARCHAR(12)""",
    """CREATE INDEX IF NOT EXISTS idx_crm_phones_digits ON crm_phones (digits)""",

    """CREATE TABLE IF NOT EXISTS crm_building_contacts (
        id SERIAL PRIMARY KEY,
        building_id INTEGER NOT NULL REFERENCES crm_buildings(id) ON DELETE CASCADE,
        contact_id INTEGER NOT NULL REFERENCES crm_contacts(id) ON DELETE CASCADE,
        role VARCHAR(30) NOT NULL DEFAULT 'other'
            CHECK (role IN ('owner','property_manager','super','board','tenant','contractor','other')),
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        UNIQUE (building_id, contact_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_crm_bc_contact ON crm_building_contacts (contact_id)""",

    """CREATE TABLE IF NOT EXISTS crm_activity (
        id SERIAL PRIMARY KEY,
        type VARCHAR(20) NOT NULL
            CHECK (type IN ('contacted','visit','note','stage_change','system')),
        method VARCHAR(20)
            CHECK (method IS NULL OR method IN ('call','text','email','in_person','other')),
        outcome VARCHAR(30)
            CHECK (outcome IS NULL OR outcome IN ('spoke','voicemail','no_answer',
                'callback_requested','meeting_set','wrong_number','not_interested')),
        note TEXT,
        is_pinned BOOLEAN NOT NULL DEFAULT FALSE,
        phone_digits VARCHAR(15),
        building_id INTEGER REFERENCES crm_buildings(id) ON DELETE CASCADE,
        contact_id INTEGER REFERENCES crm_contacts(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        team_id INTEGER,
        meta JSONB,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS idx_crm_activity_building
       ON crm_activity (building_id, created_at DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_crm_activity_contact
       ON crm_activity (contact_id, created_at DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_crm_activity_team
       ON crm_activity (team_id, created_at DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_crm_activity_user
       ON crm_activity (user_id, created_at DESC)""",

    """CREATE TABLE IF NOT EXISTS crm_stars (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        building_id INTEGER REFERENCES crm_buildings(id) ON DELETE CASCADE,
        contact_id INTEGER REFERENCES crm_contacts(id) ON DELETE CASCADE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        CHECK ((building_id IS NULL) <> (contact_id IS NULL))
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_crm_stars_building
       ON crm_stars (user_id, building_id) WHERE building_id IS NOT NULL""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_crm_stars_contact
       ON crm_stars (user_id, contact_id) WHERE contact_id IS NOT NULL""",

    """CREATE TABLE IF NOT EXISTS crm_follow_ups (
        id SERIAL PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        note TEXT,
        due_date DATE NOT NULL,
        status VARCHAR(10) NOT NULL DEFAULT 'open'
            CHECK (status IN ('open','done','skipped')),
        building_id INTEGER REFERENCES crm_buildings(id) ON DELETE CASCADE,
        contact_id INTEGER REFERENCES crm_contacts(id) ON DELETE CASCADE,
        assigned_to_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        team_id INTEGER,
        completed_at TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS idx_crm_followups_assignee
       ON crm_follow_ups (assigned_to_id, status, due_date)""",
    """CREATE INDEX IF NOT EXISTS idx_crm_followups_team
       ON crm_follow_ups (team_id, status, due_date)""",

    """CREATE TABLE IF NOT EXISTS crm_lists (
        id SERIAL PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        description TEXT,
        color VARCHAR(20),
        owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        assigned_to_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        team_id INTEGER,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS idx_crm_lists_team ON crm_lists (team_id)""",

    """CREATE TABLE IF NOT EXISTS crm_list_items (
        id SERIAL PRIMARY KEY,
        list_id INTEGER NOT NULL REFERENCES crm_lists(id) ON DELETE CASCADE,
        building_id INTEGER REFERENCES crm_buildings(id) ON DELETE CASCADE,
        contact_id INTEGER REFERENCES crm_contacts(id) ON DELETE CASCADE,
        note VARCHAR(255),
        sort_order INTEGER NOT NULL DEFAULT 0,
        added_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        CHECK ((building_id IS NULL) <> (contact_id IS NULL))
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_crm_list_items_building
       ON crm_list_items (list_id, building_id) WHERE building_id IS NOT NULL""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_crm_list_items_contact
       ON crm_list_items (list_id, contact_id) WHERE contact_id IS NOT NULL""",

    """CREATE TABLE IF NOT EXISTS crm_saved_filters (
        id SERIAL PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        querystring TEXT NOT NULL,
        owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        team_id INTEGER,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",

    """CREATE TABLE IF NOT EXISTS crm_view_events (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        entity_type VARCHAR(10) NOT NULL CHECK (entity_type IN ('building','contact','list')),
        entity_id INTEGER NOT NULL,
        label VARCHAR(255),
        team_id INTEGER,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS idx_crm_views_user
       ON crm_view_events (user_id, created_at DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_crm_views_entity
       ON crm_view_events (entity_type, entity_id, created_at DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_crm_views_team
       ON crm_view_events (team_id, created_at DESC)""",

    # A deal is intentionally separate from a building. A building can produce
    # several opportunities over time, each with its own value, service, owner,
    # close target, next step, and loss reason.
    """CREATE TABLE IF NOT EXISTS crm_deals (
        id SERIAL PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        service_type VARCHAR(120),
        stage VARCHAR(20) NOT NULL DEFAULT 'prospect'
            CHECK (stage IN ('prospect','contacted','interested','quoted','nurture','won','lost','client')),
        estimated_value NUMERIC(14,2),
        expected_close_date DATE,
        next_step VARCHAR(255),
        next_step_at TIMESTAMP,
        next_step_notified_at TIMESTAMP,
        lost_reason TEXT,
        source VARCHAR(40) NOT NULL DEFAULT 'manual',
        building_id INTEGER REFERENCES crm_buildings(id) ON DELETE SET NULL,
        contact_id INTEGER REFERENCES crm_contacts(id) ON DELETE SET NULL,
        assigned_to_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        added_by_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        team_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        won_at TIMESTAMP,
        lost_at TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS idx_crm_deals_team_stage ON crm_deals (team_id, stage)""",
    """CREATE INDEX IF NOT EXISTS idx_crm_deals_assignee ON crm_deals (team_id, assigned_to_id)""",
    """CREATE INDEX IF NOT EXISTS idx_crm_deals_next_step ON crm_deals (assigned_to_id, next_step_at)""",
    """ALTER TABLE crm_deals ADD COLUMN IF NOT EXISTS next_step_notified_at TIMESTAMP""",

    """ALTER TABLE crm_activity ADD COLUMN IF NOT EXISTS deal_id INTEGER REFERENCES crm_deals(id) ON DELETE SET NULL""",
    """ALTER TABLE crm_activity DROP CONSTRAINT IF EXISTS crm_activity_deal_id_fkey""",
    """ALTER TABLE crm_activity ADD CONSTRAINT crm_activity_deal_id_fkey
       FOREIGN KEY (deal_id) REFERENCES crm_deals(id) ON DELETE SET NULL""",
    """CREATE INDEX IF NOT EXISTS idx_crm_activity_deal ON crm_activity (deal_id, created_at DESC)""",
    """ALTER TABLE crm_follow_ups ADD COLUMN IF NOT EXISTS deal_id INTEGER REFERENCES crm_deals(id) ON DELETE CASCADE""",
    """ALTER TABLE crm_follow_ups ADD COLUMN IF NOT EXISTS due_at TIMESTAMP""",
    """ALTER TABLE crm_follow_ups ADD COLUMN IF NOT EXISTS reminder_minutes INTEGER NOT NULL DEFAULT 15""",
    """ALTER TABLE crm_follow_ups ADD COLUMN IF NOT EXISTS notified_at TIMESTAMP""",
    """UPDATE crm_follow_ups SET due_at = (due_date + TIME '09:00')
       AT TIME ZONE 'America/New_York' AT TIME ZONE 'UTC' WHERE due_at IS NULL""",
    """CREATE INDEX IF NOT EXISTS idx_crm_followups_due_at
       ON crm_follow_ups (assigned_to_id, status, due_at)""",

    # Admin-only, append-only change history. Old/new values are structured so
    # reporting and future integrations do not have to parse prose.
    """CREATE TABLE IF NOT EXISTS crm_change_history (
        id BIGSERIAL PRIMARY KEY,
        entity_type VARCHAR(30) NOT NULL,
        entity_id INTEGER,
        entity_label VARCHAR(255),
        action VARCHAR(40) NOT NULL,
        field_name VARCHAR(80),
        old_value JSONB,
        new_value JSONB,
        actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        team_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS idx_crm_history_team
       ON crm_change_history (team_id, created_at DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_crm_history_entity
       ON crm_change_history (entity_type, entity_id, created_at DESC)""",

    # Notifications work in-app everywhere. Push subscriptions add desktop and
    # installed iPhone delivery when VAPID is configured on the server.
    """CREATE TABLE IF NOT EXISTS crm_notifications (
        id BIGSERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        team_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        kind VARCHAR(40) NOT NULL,
        title VARCHAR(255) NOT NULL,
        body TEXT,
        url TEXT,
        dedupe_key VARCHAR(255),
        read_at TIMESTAMP,
        pushed_at TIMESTAMP,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_crm_notifications_dedupe
       ON crm_notifications (user_id, dedupe_key) WHERE dedupe_key IS NOT NULL""",
    """CREATE INDEX IF NOT EXISTS idx_crm_notifications_inbox
       ON crm_notifications (user_id, read_at, created_at DESC)""",
    """CREATE TABLE IF NOT EXISTS crm_notification_preferences (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        followups BOOLEAN NOT NULL DEFAULT TRUE,
        assignments BOOLEAN NOT NULL DEFAULT TRUE,
        deal_changes BOOLEAN NOT NULL DEFAULT TRUE,
        admin_activity BOOLEAN NOT NULL DEFAULT FALSE,
        quiet_start TIME,
        quiet_end TIME,
        timezone VARCHAR(80) NOT NULL DEFAULT 'America/New_York',
        reminder_minutes INTEGER NOT NULL DEFAULT 15,
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS crm_push_subscriptions (
        id BIGSERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        endpoint TEXT NOT NULL UNIQUE,
        p256dh TEXT NOT NULL,
        auth TEXT NOT NULL,
        user_agent TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )""",

    # Saved searches. The v1 table only stored a name and a querystring; a
    # saved search now also knows which page it belongs to, who may see it,
    # and how often it gets used, so the Properties page can list, rank and
    # manage them without a second table.
    """ALTER TABLE crm_saved_filters ADD COLUMN IF NOT EXISTS page VARCHAR(32) NOT NULL DEFAULT 'properties'""",
    """ALTER TABLE crm_saved_filters ADD COLUMN IF NOT EXISTS visibility VARCHAR(10) NOT NULL DEFAULT 'team'""",
    """ALTER TABLE crm_saved_filters ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN NOT NULL DEFAULT FALSE""",
    """ALTER TABLE crm_saved_filters ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMP""",
    """ALTER TABLE crm_saved_filters ADD COLUMN IF NOT EXISTS use_count INTEGER NOT NULL DEFAULT 0""",
    """ALTER TABLE crm_saved_filters ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT NOW()""",
    """CREATE INDEX IF NOT EXISTS idx_crm_saved_filters_scope
       ON crm_saved_filters (team_id, page, is_pinned DESC)""",

    # Safely widen legacy checks installed by v1. PostgreSQL generated these
    # names from the table and column, so the migration is idempotent.
    """ALTER TABLE crm_buildings DROP CONSTRAINT IF EXISTS crm_buildings_stage_check""",
    """ALTER TABLE crm_buildings ADD CONSTRAINT crm_buildings_stage_check
       CHECK (stage IN ('prospect','contacted','interested','quoted','nurture','won','lost','client'))""",
    """ALTER TABLE crm_view_events DROP CONSTRAINT IF EXISTS crm_view_events_entity_type_check""",
    """ALTER TABLE crm_view_events ADD CONSTRAINT crm_view_events_entity_type_check
       CHECK (entity_type IN ('building','contact','list','deal'))""",
]


def init_crm_tables():
    """Idempotently install the CRM schema on worker startup."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Multiple Railway workers may boot together; serialize additive DDL.
        cur.execute("SELECT pg_advisory_xact_lock(86753091)")
        for statement in CRM_SCHEMA_STATEMENTS:
            cur.execute(statement)
        from prospecting_service import SCHEMA as PROSPECTING_SCHEMA
        for statement in PROSPECTING_SCHEMA:
            cur.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# ============================================================
# Small shared helpers
# ============================================================

# "x204", "ext 204", "ext. 204", "extension 204", "#204" trailing an entered number.
EXTENSION_RE = re.compile(
    r'(?:[,;]|(?:extension|extn|ext|x)\.?|#)\s*(\d{1,10})\s*$',
    re.IGNORECASE,
)


def split_phone_extension(raw):
    """Split a typed number into (number_without_extension, extension or None).

    Office lines are usually entered as one string — "(212) 555-0100 x204" —
    and the extension digits must never be folded into the 10-digit key, or
    the number itself comes out wrong.
    """
    text = str(raw or '').strip()
    if not text:
        return '', None
    match = EXTENSION_RE.search(text)
    if not match:
        return text, None
    base = text[:match.start()].strip(' ,;.-')
    extension = match.group(1)
    # Only treat it as an extension when a real number precedes it.
    if len(re.sub(r'\D', '', base)) < 7:
        return text, None
    return base, extension


def normalize_extension(raw):
    digits = re.sub(r'\D', '', str(raw or ''))
    return digits[:12] or None


def normalize_phone_digits(raw):
    """US-normalized digit key: last 10 digits (drops a leading country 1).

    Any trailing extension is stripped first so it never corrupts the key.
    """
    base, _ = split_phone_extension(raw)
    digits = re.sub(r'\D', '', base)
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits[-10:] if len(digits) > 10 else digits


def format_phone(digits, extension=None):
    digits = str(digits or '')
    formatted = f'({digits[0:3]}) {digits[3:6]}-{digits[6:]}' if len(digits) == 10 else digits
    if extension:
        formatted = f'{formatted} ext. {extension}'
    return formatted


def tel_href(digits, extension=None):
    """RFC 3966 tel: URI — iOS and Android both dial the extension from ;ext=."""
    digits = str(digits or '')
    if not digits:
        return ''
    href = f'+1{digits}' if len(digits) == 10 else digits
    return f'tel:{href};ext={extension}' if extension else f'tel:{href}'


def ny_now():
    return datetime.now(timezone.utc).astimezone(NY_TZ)


def ny_today():
    return ny_now().date()


def _utc_naive(aware):
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def ny_day_start_utc(offset_days=0):
    """Naive-UTC timestamp of an NY-local midnight, for created_at comparisons."""
    d = ny_today() + timedelta(days=offset_days)
    return _utc_naive(datetime(d.year, d.month, d.day, tzinfo=NY_TZ))


def local_due_at(due_date, due_time=None):
    """Turn an NY-local date/time into the app's naive-UTC storage format."""
    if not due_date:
        return None
    if isinstance(due_time, str):
        try:
            hour, minute = [int(v) for v in due_time.split(':')[:2]]
            due_time = time(hour, minute)
        except (TypeError, ValueError):
            due_time = None
    due_time = due_time if isinstance(due_time, time) else time(9, 0)
    return _utc_naive(datetime.combine(due_date, due_time, tzinfo=NY_TZ))


def crm_context(user):
    """Team identity for one request, derived from the access context.

    Sponsored member -> rep on the sponsor's team; anyone else owns a team
    of their own (which for the admin account is the whole company's team,
    since every employee is sponsored by it).
    """
    sponsored = bool(user.get('is_sponsored')) and user.get('sponsor_user_id')
    team_id = user['sponsor_user_id'] if sponsored else user['id']
    return {
        'user_id': user['id'],
        'team_id': team_id,
        'is_admin': bool(user.get('is_admin')) or not sponsored,
        'is_rep': bool(sponsored),
        'email': user.get('email'),
    }


# Display name for any user id: the sponsorship display name when the
# admin set one, else the email's local part.
USER_NAME_SQL = """
    COALESCE(
        (SELECT sp.display_name FROM account_sponsorships sp
         WHERE sp.member_user_id = {alias}.id AND sp.status = 'active'
           AND sp.display_name IS NOT NULL
         ORDER BY sp.accepted_at DESC NULLS LAST LIMIT 1),
        split_part({alias}.email, '@', 1)
    )
"""


def _user_name_sql(alias='u'):
    return USER_NAME_SQL.format(alias=alias)


def get_team_roster(team_id):
    """The sponsor account plus its active sponsored members."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT DISTINCT u.id, u.email, u.last_login, u.is_admin,
                   {_user_name_sql('u')} AS name,
                   (u.id = %s) AS is_team_owner
            FROM users u
            LEFT JOIN account_sponsorships sp
                   ON sp.member_user_id = u.id AND sp.status = 'active'
            WHERE u.id = %s OR sp.sponsor_user_id = %s
            ORDER BY is_team_owner DESC, name
            """,
            (team_id, team_id, team_id),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def display_name_for(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT {_user_name_sql('u')} AS name FROM users u WHERE u.id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        return row['name'] if row else None
    finally:
        cur.close()
        conn.close()


_TEAM_SCOPE_SQL = "(team_id = %s OR team_id IS NULL)"


class RecordClaimedError(ValueError):
    """Raised when a rep tries to add a team record owned by another rep."""


def record_scope_sql(ctx, alias, *, assignee='assigned_to_id', creator='added_by_id'):
    """Named-parameter SQL enforcing the CRM's record privacy boundary.

    Admins can see the team. Reps can only see records currently assigned to
    them. `added_by_id` remains immutable attribution for admins; it is not an
    access grant. This helper is deliberately reused by list, detail, search,
    bulk, and write queries so privacy does not depend on the UI.
    """
    team = f"({alias}.team_id = %(team_id)s OR {alias}.team_id IS NULL)"
    if ctx['is_admin']:
        return team
    return f"{team} AND {alias}.{assignee} = %(user_id)s"


def list_scope_sql(ctx, alias='l'):
    team = f"({alias}.team_id = %(team_id)s OR {alias}.team_id IS NULL)"
    if ctx['is_admin']:
        return team
    return f"{team} AND {alias}.assigned_to_id = %(user_id)s"


def scoped_roster(ctx):
    """Assignee choices the current user is allowed to make."""
    if ctx['is_admin']:
        return get_team_roster(ctx['team_id'])
    return [u for u in get_team_roster(ctx['team_id']) if u['id'] == ctx['user_id']]


def assignee_allowed(ctx, user_id):
    if user_id in (None, ctx['user_id']):
        return True
    return bool(ctx['is_admin'] and any(u['id'] == user_id for u in get_team_roster(ctx['team_id'])))


def row_visible(ctx, row):
    return bool(ctx['is_admin'] or row.get('assigned_to_id') == ctx['user_id'])


def entity_in_team(ctx, *, building_id=None, contact_id=None, deal_id=None):
    """True when every named entity is visible to this user. Guards every
    write API against cross-team and other-rep ids arriving in a request."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if building_id:
            cur.execute(
                f"SELECT 1 FROM crm_buildings b WHERE b.id = %(id)s AND {record_scope_sql(ctx, 'b')}",
                {'id': building_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
            )
            if not cur.fetchone():
                return False
        if contact_id:
            cur.execute(
                f"SELECT 1 FROM crm_contacts c WHERE c.id = %(id)s AND {record_scope_sql(ctx, 'c')}",
                {'id': contact_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
            )
            if not cur.fetchone():
                return False
        if deal_id:
            cur.execute(
                f"SELECT 1 FROM crm_deals d WHERE d.id = %(id)s AND {record_scope_sql(ctx, 'd')}",
                {'id': deal_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
            )
            if not cur.fetchone():
                return False
        return bool(building_id or contact_id or deal_id)
    finally:
        cur.close()
        conn.close()


# ============================================================
# Buildings
# ============================================================

def find_building_by_bbl(ctx, bbl):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT b.id FROM crm_buildings b WHERE b.bbl = %(bbl)s AND {record_scope_sql(ctx, 'b')}",
            {'bbl': str(bbl), 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        row = cur.fetchone()
        return row['id'] if row else None
    finally:
        cur.close()
        conn.close()


def create_building(ctx, *, address, bbl=None, borough=None, zip_code=None,
                    neighborhood=None, unit_count=None, year_built=None,
                    num_floors=None, building_class=None, owner_name=None,
                    source='manual'):
    """Create a building; returns (building_id, created). Idempotent on BBL."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if bbl:
            cur.execute(
                """SELECT id, assigned_to_id, added_by_id FROM crm_buildings
                   WHERE bbl = %s AND (team_id = %s OR team_id IS NULL)""",
                (str(bbl), ctx['team_id']),
            )
            existing = cur.fetchone()
            if existing:
                if not row_visible(ctx, existing):
                    raise RecordClaimedError('This building is already claimed by another rep. Ask an admin to transfer it.')
                return existing['id'], False
        cur.execute(
            """INSERT INTO crm_buildings
               (bbl, address, borough, zip_code, neighborhood, unit_count,
                year_built, num_floors, building_class, owner_name, source,
                assigned_to_id, added_by_id, team_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (str(bbl) if bbl else None, address.strip(), borough, zip_code,
             neighborhood, unit_count, year_built, num_floors, building_class,
             owner_name, source, ctx['user_id'], ctx['user_id'], ctx['team_id']),
        )
        building_id = cur.fetchone()['id']
        cur.execute(
            """INSERT INTO crm_activity (type, note, building_id, user_id, team_id)
               VALUES ('system', %s, %s, %s, %s)""",
            ('Added to CRM' + (' from the permit database' if source == 'permit' else ''),
             building_id, ctx['user_id'], ctx['team_id']),
        )
        audit_change(cur, ctx, 'building', building_id, address, 'created',
                     new_value={'source': source, 'assigned_to_id': ctx['user_id']})
        conn.commit()
        return building_id, True
    except psycopg2.errors.UniqueViolation:
        # Two reps importing the same BBL at once: fall back to the winner.
        conn.rollback()
        cur.execute(
            """SELECT id, assigned_to_id, added_by_id FROM crm_buildings
               WHERE bbl = %s AND (team_id = %s OR team_id IS NULL)""",
            (str(bbl), ctx['team_id']),
        )
        row = cur.fetchone()
        if row:
            if not row_visible(ctx, row):
                raise RecordClaimedError('This building was claimed by another rep while you were adding it.')
            return row['id'], False
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


BUILDING_SORTS = {
    'recent': 'GREATEST(COALESCE(b.last_contacted_at, %(epoch)s), COALESCE(b.last_visited_at, %(epoch)s), b.created_at) DESC',
    'last_contacted': 'b.last_contacted_at DESC NULLS LAST',
    'last_visited': 'b.last_visited_at DESC NULLS LAST',
    'newest': 'b.created_at DESC',
    'address': 'b.address ASC',
    'most_contacted': 'b.contact_count DESC',
}


def list_buildings(ctx, *, stage=None, q=None, borough=None, starred=False,
                   cold=False, mine=False, sort='recent', limit=200):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        where = [record_scope_sql(ctx, 'b')]
        params = {
            'team_id': ctx['team_id'], 'user_id': ctx['user_id'],
            'epoch': datetime(1970, 1, 1), 'limit': limit,
        }
        if stage and stage in STAGES:
            where.append("b.stage = %(stage)s")
            params['stage'] = stage
        if borough:
            where.append("b.borough ILIKE %(borough)s")
            params['borough'] = borough
        if q:
            where.append("(b.address ILIKE %(q)s OR b.owner_name ILIKE %(q)s OR b.bbl = %(q_raw)s)")
            params['q'] = f'%{q}%'
            params['q_raw'] = q.strip()
        if starred:
            where.append("s.id IS NOT NULL")
        if cold:
            where.append("(b.last_contacted_at IS NULL OR b.last_contacted_at < %(cold_cutoff)s)")
            params['cold_cutoff'] = datetime.utcnow() - timedelta(days=30)
        if mine:
            where.append("b.assigned_to_id = %(user_id)s")
        order_sql = BUILDING_SORTS.get(sort, BUILDING_SORTS['recent'])
        cur.execute(
            f"""
            SELECT b.*, (s.id IS NOT NULL) AS starred,
                   {_user_name_sql('au')} AS assigned_to_name,
                   {_user_name_sql('ab')} AS added_by_name
            FROM crm_buildings b
            LEFT JOIN crm_stars s
                   ON s.building_id = b.id AND s.user_id = %(user_id)s
            LEFT JOIN users au ON au.id = b.assigned_to_id
            JOIN users ab ON ab.id = b.added_by_id
            WHERE {' AND '.join(where)}
            ORDER BY {order_sql}
            LIMIT %(limit)s
            """,
            params,
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def building_stage_counts(ctx):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT stage, COUNT(*) AS n FROM crm_buildings b
                WHERE {record_scope_sql(ctx, 'b')} GROUP BY stage""",
            {'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        counts = {r['stage']: r['n'] for r in cur.fetchall()}
        counts['all'] = sum(counts.values())
        return counts
    finally:
        cur.close()
        conn.close()


def get_building(ctx, building_id):
    """Full building detail: row, people, follow-ups, lists, stars, last touch."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT b.*, (s.id IS NOT NULL) AS starred,
                   {_user_name_sql('au')} AS assigned_to_name,
                   {_user_name_sql('ab')} AS added_by_name
            FROM crm_buildings b
            LEFT JOIN crm_stars s ON s.building_id = b.id AND s.user_id = %(user_id)s
            LEFT JOIN users au ON au.id = b.assigned_to_id
            JOIN users ab ON ab.id = b.added_by_id
            WHERE b.id = %(building_id)s AND {record_scope_sql(ctx, 'b')}
            """,
            {'user_id': ctx['user_id'], 'building_id': building_id, 'team_id': ctx['team_id']},
        )
        building = cur.fetchone()
        if not building:
            return None
        building = dict(building)

        cur.execute(
            f"""
            SELECT su.id AS user_id, {_user_name_sql('su')} AS name
            FROM crm_stars st JOIN users su ON su.id = st.user_id
            WHERE st.building_id = %(building_id)s
              AND (%(is_admin)s OR st.user_id = %(user_id)s)
            ORDER BY st.created_at
            """,
            {'building_id': building_id, 'is_admin': ctx['is_admin'],
             'user_id': ctx['user_id']},
        )
        building['starred_by'] = [dict(r) for r in cur.fetchall()]

        cur.execute(
            f"""
            SELECT c.*, bc.role AS building_role,
                   (st.id IS NOT NULL) AS starred
            FROM crm_building_contacts bc
            JOIN crm_contacts c ON c.id = bc.contact_id
            LEFT JOIN crm_stars st ON st.contact_id = c.id AND st.user_id = %(user_id)s
            WHERE bc.building_id = %(building_id)s AND {record_scope_sql(ctx, 'c')}
            ORDER BY CASE bc.role WHEN 'owner' THEN 0 WHEN 'property_manager' THEN 1
                     WHEN 'super' THEN 2 ELSE 3 END, c.name
            """,
            {'user_id': ctx['user_id'], 'building_id': building_id, 'team_id': ctx['team_id']},
        )
        contacts = [dict(r) for r in cur.fetchall()]
        if contacts:
            ids = [c['id'] for c in contacts]
            cur.execute(
                f"""SELECT p.*, {_user_name_sql('pu')} AS added_by_name
                    FROM crm_phones p LEFT JOIN users pu ON pu.id = p.added_by_id
                    WHERE p.contact_id = ANY(%s)
                    ORDER BY p.is_primary DESC, p.created_at""",
                (ids,),
            )
            phones_by_contact = {}
            for p in cur.fetchall():
                phones_by_contact.setdefault(p['contact_id'], []).append(dict(p))
            for c in contacts:
                c['phones'] = phones_by_contact.get(c['id'], [])
        building['contacts'] = contacts

        cur.execute(
            f"""SELECT f.*, {_user_name_sql('fu')} AS assigned_to_name
                FROM crm_follow_ups f JOIN users fu ON fu.id = f.assigned_to_id
                WHERE f.building_id = %(building_id)s AND f.status = 'open'
                  AND (%(is_admin)s OR f.assigned_to_id = %(user_id)s)
                ORDER BY COALESCE(f.due_at, f.due_date::timestamp), f.id""",
            {'building_id': building_id, 'is_admin': ctx['is_admin'], 'user_id': ctx['user_id']},
        )
        building['follow_ups'] = [dict(r) for r in cur.fetchall()]

        cur.execute(
            f"""SELECT l.id, l.name, l.color FROM crm_list_items li
               JOIN crm_lists l ON l.id = li.list_id
               WHERE li.building_id = %(building_id)s AND {list_scope_sql(ctx, 'l')}
               ORDER BY l.name""",
            {'building_id': building_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        building['lists'] = [dict(r) for r in cur.fetchall()]

        cur.execute(
            f"""SELECT a.created_at, {_user_name_sql('auu')} AS user_name, a.user_id
                FROM crm_activity a JOIN users auu ON auu.id = a.user_id
                WHERE a.building_id = %s AND a.type = 'contacted'
                ORDER BY a.created_at DESC LIMIT 1""",
            (building_id,),
        )
        building['last_contact'] = dict(cur.fetchone() or {}) or None
        return building
    finally:
        cur.close()
        conn.close()


def update_building_stage(ctx, building_id, stage):
    if stage not in STAGES:
        raise ValueError('invalid stage')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT b.address, b.stage FROM crm_buildings b
                WHERE b.id = %(id)s AND {record_scope_sql(ctx, 'b')} FOR UPDATE""",
            {'id': building_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        before = cur.fetchone()
        if not before:
            return False
        if before['stage'] != stage:
            cur.execute(
                "UPDATE crm_buildings SET stage = %s, updated_at = NOW() WHERE id = %s",
                (stage, building_id),
            )
            cur.execute(
                """INSERT INTO crm_activity (type, note, building_id, user_id, team_id)
                   VALUES ('stage_change', %s, %s, %s, %s)""",
                (f'Stage set to {STAGE_LABELS[stage]}', building_id,
                 ctx['user_id'], ctx['team_id']),
            )
            audit_change(cur, ctx, 'building', building_id, before['address'],
                         'stage_changed', 'stage', before['stage'], stage)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def assign_building(ctx, building_id, assignee_id):
    if not ctx['is_admin'] and assignee_id != ctx['user_id']:
        raise PermissionError('Only an admin can transfer records between reps')
    if not assignee_allowed(ctx, assignee_id):
        raise ValueError('That assignee is not on this team')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT b.address, b.assigned_to_id FROM crm_buildings b
                WHERE b.id = %(id)s AND {record_scope_sql(ctx, 'b')} FOR UPDATE""",
            {'id': building_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        before = cur.fetchone()
        if before:
            cur.execute(
                "UPDATE crm_buildings SET assigned_to_id = %s, updated_at = NOW() WHERE id = %s",
                (assignee_id or None, building_id),
            )
            # Unassigned linked people follow the building so the new rep can
            # work the complete account without seeing unrelated contacts.
            if assignee_id:
                cur.execute(
                    """UPDATE crm_contacts c SET assigned_to_id = %s, updated_at = NOW()
                       FROM crm_building_contacts bc
                       WHERE bc.contact_id = c.id AND bc.building_id = %s
                         AND c.assigned_to_id IS NULL""",
                    (assignee_id, building_id),
                )
            if before['assigned_to_id'] != assignee_id:
                if before['assigned_to_id']:
                    cur.execute(
                        "DELETE FROM crm_notifications WHERE user_id = %s AND url = %s",
                        (before['assigned_to_id'], f'/crm/buildings/{building_id}'),
                    )
                if assignee_id:
                    create_notification(cur, ctx, assignee_id, 'assignment',
                                        'A building was assigned to you', before['address'],
                                        f'/crm/buildings/{building_id}',
                                        f'building-assigned:{building_id}:{assignee_id}')
                audit_change(cur, ctx, 'building', building_id, before['address'],
                             'assigned', 'assigned_to_id', before['assigned_to_id'], assignee_id)
        conn.commit()
        return bool(before)
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def permit_snapshot(bbl):
    """Read-only intelligence for the detail rail, straight from scraper tables."""
    if not bbl:
        return None
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        snapshot = {'bbl': bbl}
        cur.execute(
            """SELECT current_owner_name, owner_name_rpad, owner_name_hpd,
                      estimated_value, purchase_date, purchase_price,
                      hpd_open_violations, hpd_open_complaints,
                      total_units, year_built, num_floors, building_class
               FROM buildings WHERE bbl = %s""",
            (str(bbl),),
        )
        row = cur.fetchone()
        snapshot['enrichment'] = dict(row) if row else None
        cur.execute(
            """SELECT permit_no, permit_type, work_type, filing_date, issue_date,
                      permit_status, LEFT(COALESCE(work_description, ''), 220) AS work_description
               FROM permits WHERE bbl = %s
               ORDER BY COALESCE(filing_date, issue_date) DESC NULLS LAST
               LIMIT 5""",
            (str(bbl),),
        )
        snapshot['recent_permits'] = [dict(r) for r in cur.fetchall()]
        return snapshot
    except Exception:
        # The rail is decoration; a failed lookup must never break the page.
        return {'bbl': bbl, 'enrichment': None, 'recent_permits': []}
    finally:
        cur.close()
        conn.close()


def building_streetview(bbl, address, borough=None):
    """Street View / Maps payload for a CRM building (see streetview.resolve)."""
    import streetview
    loc = None
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        loc = streetview.resolve(cur, bbl, address, borough)
        conn.commit()  # keeps the geocode cache
    except Exception as e:
        conn.rollback()
        print(f"[crm] streetview lookup failed for {address!r}: {e}", flush=True)
        loc = None
    finally:
        cur.close()
        conn.close()
    loc = loc or {}
    return streetview.payload(address, loc.get('lat'), loc.get('lng'), borough, loc.get('source'), bbl=bbl)


def permit_building_prefill(bbl):
    """Best-available address/facts for one BBL from the scraper tables."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT address, CAST(borough AS TEXT) AS borough, total_units,
                      year_built, num_floors, building_class,
                      COALESCE(current_owner_name, owner_name_rpad, owner_name_hpd) AS owner_name
               FROM buildings WHERE bbl = %s""",
            (str(bbl),),
        )
        enriched = cur.fetchone()
        cur.execute(
            """SELECT address, borough, zip_code, total_dwelling_units,
                      owner_business_name, owner_first_name, owner_last_name
               FROM permits WHERE bbl = %s
               ORDER BY COALESCE(filing_date, issue_date) DESC NULLS LAST
               LIMIT 1""",
            (str(bbl),),
        )
        permit = cur.fetchone()
        if not enriched and not permit:
            return None
        owner_from_permit = None
        if permit:
            owner_from_permit = permit['owner_business_name'] or ' '.join(
                x for x in (permit['owner_first_name'], permit['owner_last_name']) if x
            ) or None
        return {
            'bbl': str(bbl),
            'address': (enriched and enriched['address']) or (permit and permit['address']) or f'BBL {bbl}',
            'borough': (enriched and enriched['borough']) or (permit and permit['borough']),
            'zip_code': permit and permit['zip_code'],
            'unit_count': (enriched and enriched['total_units']) or (permit and permit['total_dwelling_units']),
            'year_built': enriched and enriched['year_built'],
            'num_floors': enriched and enriched['num_floors'],
            'building_class': enriched and enriched['building_class'],
            'owner_name': (enriched and enriched['owner_name']) or owner_from_permit,
        }
    finally:
        cur.close()
        conn.close()


# ============================================================
# Contacts & phones
# ============================================================

def find_contacts_by_digits(ctx, digits, extension=None):
    """Duplicate-defense lookup: existing team contacts holding this number.

    An office main line shared by several people is normal, so a row whose
    extension differs from the one being entered is not a duplicate.
    """
    if not digits:
        return []
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT DISTINCT c.id, c.name, c.company, p.extension,
                              c.assigned_to_id, c.added_by_id
               FROM crm_phones p JOIN crm_contacts c ON c.id = p.contact_id
               WHERE p.digits = %s AND (c.team_id = %s OR c.team_id IS NULL)
                 AND (%s IS NULL OR p.extension IS NULL OR p.extension = %s)
               ORDER BY c.name""",
            (digits, ctx['team_id'], extension, extension),
        )
        rows = [dict(r) for r in cur.fetchall()]
        if ctx['is_admin']:
            return rows
        return [r if row_visible(ctx, r) else {
            'id': None, 'name': 'Another rep’s contact', 'company': None,
            'extension': r.get('extension'), 'claimed': True,
        } for r in rows]
    finally:
        cur.close()
        conn.close()


def create_contact(ctx, *, name, title=None, company=None, email=None,
                   source='manual', source_detail=None, building_id=None,
                   building_role='other', phone=None, phone_label=None,
                   phone_extension=None, assigned_to_id=None):
    """Create a contact, optionally with a first phone and a building link."""
    assignee = assigned_to_id or ctx['user_id']
    if not assignee_allowed(ctx, assignee):
        raise PermissionError('Only an admin can assign a contact to another rep')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO crm_contacts
               (name, title, company, email, source, source_detail,
                assigned_to_id, added_by_id, team_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (name.strip(), title or None, company or None, email or None,
             source, source_detail or None, assignee,
             ctx['user_id'], ctx['team_id']),
        )
        contact_id = cur.fetchone()['id']
        if phone:
            digits = normalize_phone_digits(phone)
            extension = normalize_extension(phone_extension) or split_phone_extension(phone)[1]
            if digits:
                cur.execute(
                    """INSERT INTO crm_phones
                       (contact_id, number, digits, extension, label, source, source_detail,
                        is_primary, added_by_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE,%s)
                       ON CONFLICT (contact_id, digits) DO NOTHING""",
                    (contact_id, format_phone(digits), digits, extension, phone_label or None,
                     source, source_detail or None, ctx['user_id']),
                )
        if building_id:
            role = building_role if building_role in BUILDING_CONTACT_ROLES else 'other'
            cur.execute(
                """INSERT INTO crm_building_contacts (building_id, contact_id, role)
                   VALUES (%s,%s,%s) ON CONFLICT (building_id, contact_id) DO NOTHING""",
                (building_id, contact_id, role),
            )
        audit_change(cur, ctx, 'contact', contact_id, name.strip(), 'created',
                     new_value={'source': source, 'assigned_to_id': assignee})
        conn.commit()
        return contact_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def add_phone(ctx, contact_id, *, number, extension=None, label=None,
              source='rep_found', source_detail=None, make_primary=False):
    digits = normalize_phone_digits(number)
    extension = normalize_extension(extension) or split_phone_extension(number)[1]
    if not digits:
        raise ValueError('phone number needs digits')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if make_primary:
            cur.execute(
                "UPDATE crm_phones SET is_primary = FALSE WHERE contact_id = %s",
                (contact_id,),
            )
        cur.execute(
            """INSERT INTO crm_phones
               (contact_id, number, digits, extension, label, source, source_detail,
                is_primary, added_by_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (contact_id, digits) DO UPDATE
                   SET label = COALESCE(EXCLUDED.label, crm_phones.label),
                       extension = COALESCE(EXCLUDED.extension, crm_phones.extension)
               RETURNING id""",
            (contact_id, format_phone(digits), digits, extension, label or None, source,
             source_detail or None, bool(make_primary), ctx['user_id']),
        )
        phone_id = cur.fetchone()['id']
        cur.execute("SELECT name FROM crm_contacts WHERE id = %s", (contact_id,))
        contact = cur.fetchone()
        audit_change(cur, ctx, 'contact', contact_id,
                     contact['name'] if contact else None, 'phone_added', 'phone',
                     new_value={'number': format_phone(digits), 'extension': extension,
                                'label': label, 'source': source})
        conn.commit()
        return phone_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def set_phone_status(ctx, phone_id, status):
    if status not in ('good', 'bad', 'do_not_call'):
        raise ValueError('invalid phone status')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT p.contact_id, p.status, p.number, c.name
                FROM crm_phones p JOIN crm_contacts c ON c.id = p.contact_id
                WHERE p.id = %(phone_id)s AND {record_scope_sql(ctx, 'c')}
                FOR UPDATE""",
            {'phone_id': phone_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        before = cur.fetchone()
        if not before:
            return False
        cur.execute("UPDATE crm_phones SET status = %s WHERE id = %s", (status, phone_id))
        if before['status'] != status:
            audit_change(cur, ctx, 'contact', before['contact_id'], before['name'],
                         'phone_updated', 'phone_status', before['status'], status)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def set_do_not_contact(ctx, contact_id, value):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT c.name, c.do_not_contact FROM crm_contacts c
                WHERE c.id = %(id)s AND {record_scope_sql(ctx, 'c')} FOR UPDATE""",
            {'id': contact_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        before = cur.fetchone()
        if not before:
            return False
        value = bool(value)
        cur.execute(
            "UPDATE crm_contacts SET do_not_contact = %s, updated_at = NOW() WHERE id = %s",
            (value, contact_id),
        )
        if before['do_not_contact'] != value:
            audit_change(cur, ctx, 'contact', contact_id, before['name'], 'updated',
                         'do_not_contact', before['do_not_contact'], value)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def list_contacts(ctx, *, q=None, starred=False, cold=False, limit=200):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        where = [record_scope_sql(ctx, 'c')]
        params = {'team_id': ctx['team_id'], 'user_id': ctx['user_id'], 'limit': limit}
        if q:
            digits = normalize_phone_digits(q)
            if digits and len(digits) >= 4:
                where.append(
                    """(c.name ILIKE %(q)s OR c.company ILIKE %(q)s
                        OR EXISTS (SELECT 1 FROM crm_phones pq
                                   WHERE pq.contact_id = c.id AND pq.digits LIKE %(digits)s))"""
                )
                params['digits'] = f'%{digits}%'
            else:
                where.append("(c.name ILIKE %(q)s OR c.company ILIKE %(q)s)")
            params['q'] = f'%{q}%'
        if starred:
            where.append("st.id IS NOT NULL")
        if cold:
            where.append("(c.last_contacted_at IS NULL OR c.last_contacted_at < %(cold_cutoff)s)")
            params['cold_cutoff'] = datetime.utcnow() - timedelta(days=30)
        cur.execute(
            f"""
            SELECT c.*, (st.id IS NOT NULL) AS starred,
                   {_user_name_sql('au')} AS assigned_to_name,
                   {_user_name_sql('ab')} AS added_by_name,
                   (SELECT p.number || COALESCE(' ext. ' || p.extension, '') FROM crm_phones p
                    WHERE p.contact_id = c.id AND p.status = 'good'
                    ORDER BY p.is_primary DESC, p.created_at LIMIT 1) AS primary_phone,
                   (SELECT COUNT(*) FROM crm_building_contacts bc
                    JOIN crm_buildings linked_b ON linked_b.id = bc.building_id
                    WHERE bc.contact_id = c.id
                      AND {record_scope_sql(ctx, 'linked_b')}) AS building_count
            FROM crm_contacts c
            LEFT JOIN crm_stars st ON st.contact_id = c.id AND st.user_id = %(user_id)s
            LEFT JOIN users au ON au.id = c.assigned_to_id
            JOIN users ab ON ab.id = c.added_by_id
            WHERE {' AND '.join(where)}
            ORDER BY c.last_contacted_at DESC NULLS LAST, c.created_at DESC
            LIMIT %(limit)s
            """,
            params,
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def get_contact(ctx, contact_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT c.*, (st.id IS NOT NULL) AS starred,
                   {_user_name_sql('ab')} AS added_by_name,
                   {_user_name_sql('au')} AS assigned_to_name
            FROM crm_contacts c
            LEFT JOIN crm_stars st ON st.contact_id = c.id AND st.user_id = %(user_id)s
            JOIN users ab ON ab.id = c.added_by_id
            LEFT JOIN users au ON au.id = c.assigned_to_id
            WHERE c.id = %(contact_id)s AND {record_scope_sql(ctx, 'c')}
            """,
            {'user_id': ctx['user_id'], 'contact_id': contact_id, 'team_id': ctx['team_id']},
        )
        contact = cur.fetchone()
        if not contact:
            return None
        contact = dict(contact)
        cur.execute(
            f"""SELECT p.*, {_user_name_sql('pu')} AS added_by_name
                FROM crm_phones p LEFT JOIN users pu ON pu.id = p.added_by_id
                WHERE p.contact_id = %s
                ORDER BY p.is_primary DESC, p.created_at""",
            (contact_id,),
        )
        contact['phones'] = [dict(r) for r in cur.fetchall()]
        cur.execute(
            f"""SELECT b.id, b.address, b.borough, b.stage, b.last_contacted_at,
                      bc.role AS building_role
               FROM crm_building_contacts bc
               JOIN crm_buildings b ON b.id = bc.building_id
               WHERE bc.contact_id = %(contact_id)s AND {record_scope_sql(ctx, 'b')}
               ORDER BY b.address""",
            {'contact_id': contact_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        contact['buildings'] = [dict(r) for r in cur.fetchall()]
        cur.execute(
            f"""SELECT f.*, {_user_name_sql('fu')} AS assigned_to_name
                FROM crm_follow_ups f JOIN users fu ON fu.id = f.assigned_to_id
                WHERE f.contact_id = %(contact_id)s AND f.status = 'open'
                  AND (%(is_admin)s OR f.assigned_to_id = %(user_id)s)
                ORDER BY COALESCE(f.due_at, f.due_date::timestamp), f.id""",
            {'contact_id': contact_id, 'is_admin': ctx['is_admin'], 'user_id': ctx['user_id']},
        )
        contact['follow_ups'] = [dict(r) for r in cur.fetchall()]
        cur.execute(
            f"""SELECT a.created_at, {_user_name_sql('auu')} AS user_name
                FROM crm_activity a JOIN users auu ON auu.id = a.user_id
                WHERE a.contact_id = %s AND a.type = 'contacted'
                ORDER BY a.created_at DESC LIMIT 1""",
            (contact_id,),
        )
        contact['last_contact'] = dict(cur.fetchone() or {}) or None
        return contact
    finally:
        cur.close()
        conn.close()


def link_contact_to_building(ctx, contact_id, building_id, role='other'):
    role = role if role in BUILDING_CONTACT_ROLES else 'other'
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT b.address, c.name
                FROM crm_buildings b CROSS JOIN crm_contacts c
                WHERE b.id = %(building_id)s AND c.id = %(contact_id)s
                  AND {record_scope_sql(ctx, 'b')}
                  AND {record_scope_sql(ctx, 'c')}""",
            {'building_id': building_id, 'contact_id': contact_id,
             'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        labels = cur.fetchone()
        if not labels:
            raise PermissionError('Building or contact not found')
        cur.execute(
            """INSERT INTO crm_building_contacts (building_id, contact_id, role)
               VALUES (%s,%s,%s) ON CONFLICT (building_id, contact_id) DO NOTHING
               RETURNING role""",
            (building_id, contact_id, role),
        )
        if cur.fetchone():
            audit_change(cur, ctx, 'building', building_id, labels['address'],
                         'contact_linked', 'contact',
                         new_value={'contact_id': contact_id, 'contact_name': labels['name'],
                                    'role': role})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# ============================================================
# Activity: the one append-only stream
# ============================================================

def log_contacted(ctx, *, building_id=None, contact_id=None, deal_id=None,
                  method='call', outcome=None, note=None, phone_digits=None):
    """The Contacted button. One press = one permanent, attributed event.

    Side effects, all in one transaction: rollups on the building/contact,
    the prospect->contacted auto stage bump, and a wrong-number outcome
    marking the dialed phone bad.
    """
    if method not in CONTACT_METHODS:
        method = 'other'
    if outcome is not None and outcome not in CONTACT_OUTCOMES:
        outcome = None
    if not building_id and not contact_id and not deal_id:
        raise ValueError('contacted needs a building, contact, or deal')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        scope_params = {'team_id': ctx['team_id'], 'user_id': ctx['user_id']}
        if building_id:
            cur.execute(
                f"SELECT 1 FROM crm_buildings b WHERE b.id = %(id)s "
                f"AND {record_scope_sql(ctx, 'b')} FOR SHARE",
                {**scope_params, 'id': building_id},
            )
            if not cur.fetchone():
                raise ValueError('This building is no longer assigned to you')
        if contact_id:
            cur.execute(
                f"SELECT c.do_not_contact FROM crm_contacts c WHERE c.id = %(id)s "
                f"AND {record_scope_sql(ctx, 'c')} FOR SHARE",
                {**scope_params, 'id': contact_id},
            )
            contact_guard = cur.fetchone()
            if not contact_guard:
                raise ValueError('This contact is no longer assigned to you')
            if contact_guard and contact_guard['do_not_contact']:
                raise ValueError('This contact is marked do not contact')
        if deal_id:
            cur.execute(
                f"""SELECT d.contact_id, c.do_not_contact AS contact_do_not_contact
                    FROM crm_deals d
                    LEFT JOIN crm_contacts c ON c.id = d.contact_id
                    WHERE d.id = %(id)s AND {record_scope_sql(ctx, 'd')}
                    FOR SHARE OF d""",
                {**scope_params, 'id': deal_id},
            )
            deal_guard = cur.fetchone()
            if not deal_guard:
                raise ValueError('This deal is no longer assigned to you')
            if (deal_guard['contact_do_not_contact']
                    and (not contact_id or deal_guard['contact_id'] == contact_id)):
                raise ValueError('This deal’s contact is marked do not contact')
        cur.execute(
            """INSERT INTO crm_activity
               (type, method, outcome, note, phone_digits, building_id, contact_id,
                deal_id, user_id, team_id)
               VALUES ('contacted', %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, created_at""",
            (method, outcome, (note or '').strip() or None, phone_digits or None,
             building_id, contact_id, deal_id, ctx['user_id'], ctx['team_id']),
        )
        activity = cur.fetchone()
        if building_id:
            cur.execute(
                """UPDATE crm_buildings
                   SET last_contacted_at = %s, contact_count = contact_count + 1,
                       updated_at = NOW()
                   WHERE id = %s""",
                (activity['created_at'], building_id),
            )
            cur.execute(
                """UPDATE crm_buildings SET stage = 'contacted', updated_at = NOW()
                   WHERE id = %s AND stage = 'prospect' RETURNING id""",
                (building_id,),
            )
            if cur.fetchone():
                cur.execute(
                    """INSERT INTO crm_activity (type, note, building_id, user_id, team_id)
                       VALUES ('stage_change', 'Stage moved to Contacted automatically after first touch', %s, %s, %s)""",
                    (building_id, ctx['user_id'], ctx['team_id']),
                )
        if contact_id:
            cur.execute(
                """UPDATE crm_contacts SET last_contacted_at = %s, updated_at = NOW()
                   WHERE id = %s""",
                (activity['created_at'], contact_id),
            )
            if outcome == 'wrong_number' and phone_digits:
                cur.execute(
                    """UPDATE crm_phones SET status = 'bad'
                       WHERE contact_id = %s AND digits = %s""",
                    (contact_id, phone_digits),
                )
        if deal_id:
            cur.execute(
                """UPDATE crm_deals SET stage = 'contacted', updated_at = NOW()
                   WHERE id = %s AND stage = 'prospect' RETURNING name""",
                (deal_id,),
            )
            moved = cur.fetchone()
            if moved:
                audit_change(cur, ctx, 'deal', deal_id, moved['name'],
                             'stage_changed', 'stage', 'prospect', 'contacted')
        conn.commit()
        return activity['id']
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def log_visit(ctx, *, building_id, note=None, visited_on=None):
    """A building visit; visited_on (date) lets reps backfill yesterday's stop."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        created_at = None
        if visited_on:
            # Noon NY on the visit date, stored naive-UTC like everything else.
            created_at = _utc_naive(datetime(
                visited_on.year, visited_on.month, visited_on.day, 12, tzinfo=NY_TZ))
        cur.execute(
            """INSERT INTO crm_activity
               (type, note, building_id, user_id, team_id, created_at)
               VALUES ('visit', %s, %s, %s, %s, COALESCE(%s, NOW()))
               RETURNING created_at""",
            ((note or '').strip() or None, building_id, ctx['user_id'],
             ctx['team_id'], created_at),
        )
        visit_ts = cur.fetchone()['created_at']
        cur.execute(
            """UPDATE crm_buildings
               SET last_visited_at = GREATEST(COALESCE(last_visited_at, %s), %s),
                   updated_at = NOW()
               WHERE id = %s""",
            (visit_ts, visit_ts, building_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def add_note(ctx, *, building_id=None, contact_id=None, note):
    if not (note or '').strip():
        raise ValueError('empty note')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO crm_activity (type, note, building_id, contact_id, user_id, team_id)
               VALUES ('note', %s, %s, %s, %s, %s) RETURNING id""",
            (note.strip(), building_id, contact_id, ctx['user_id'], ctx['team_id']),
        )
        note_id = cur.fetchone()['id']
        conn.commit()
        return note_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def toggle_pin(ctx, activity_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""UPDATE crm_activity a SET is_pinned = NOT a.is_pinned
               WHERE a.id = %(id)s AND (a.team_id = %(team_id)s OR a.team_id IS NULL)
                 AND (%(is_admin)s OR a.user_id = %(user_id)s)
                 AND (a.building_id IS NULL OR EXISTS (
                      SELECT 1 FROM crm_buildings b WHERE b.id = a.building_id
                        AND {record_scope_sql(ctx, 'b')}))
                 AND (a.contact_id IS NULL OR EXISTS (
                      SELECT 1 FROM crm_contacts c WHERE c.id = a.contact_id
                        AND {record_scope_sql(ctx, 'c')}))
                 AND (a.deal_id IS NULL OR EXISTS (
                      SELECT 1 FROM crm_deals d WHERE d.id = a.deal_id
                        AND {record_scope_sql(ctx, 'd')}))
               RETURNING a.is_pinned""",
            {'id': activity_id, 'team_id': ctx['team_id'],
             'is_admin': ctx['is_admin'], 'user_id': ctx['user_id']},
        )
        row = cur.fetchone()
        conn.commit()
        return bool(row and row['is_pinned'])
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def delete_activity(ctx, activity_id):
    """Correction policy for the append-only stream: authors get a short undo
    window, team admins can always delete. Rollups are recomputed."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT * FROM crm_activity
               WHERE id = %s AND (team_id = %s OR team_id IS NULL)""",
            (activity_id, ctx['team_id']),
        )
        activity = cur.fetchone()
        if not activity:
            return False, 'Not found'
        if activity['type'] not in ('contacted', 'visit', 'note'):
            return False, 'System events cannot be deleted'
        if not ctx['is_admin']:
            if activity['user_id'] != ctx['user_id']:
                return False, 'Only the author or an admin can delete this'
            for table, field, alias in (
                    ('crm_buildings', 'building_id', 'b'),
                    ('crm_contacts', 'contact_id', 'c'),
                    ('crm_deals', 'deal_id', 'd')):
                if activity.get(field):
                    cur.execute(
                        f"SELECT 1 FROM {table} {alias} WHERE {alias}.id = %(id)s "
                        f"AND {record_scope_sql(ctx, alias)}",
                        {'id': activity[field], 'team_id': ctx['team_id'],
                         'user_id': ctx['user_id']},
                    )
                    if not cur.fetchone():
                        return False, 'That record is no longer assigned to you'
            age = datetime.utcnow() - activity['created_at']
            if age > timedelta(minutes=ACTIVITY_UNDO_MINUTES):
                return False, f'The {ACTIVITY_UNDO_MINUTES}-minute undo window has passed — ask an admin'
        audit_change(cur, ctx, 'activity', activity_id,
                     activity.get('note') or activity['type'], 'deleted',
                     new_value={'type': activity['type'], 'building_id': activity['building_id'],
                                'contact_id': activity['contact_id'], 'deal_id': activity['deal_id']})
        cur.execute("DELETE FROM crm_activity WHERE id = %s", (activity_id,))
        if activity['building_id']:
            cur.execute(
                """UPDATE crm_buildings b SET
                     contact_count = (SELECT COUNT(*) FROM crm_activity a
                                      WHERE a.building_id = b.id AND a.type = 'contacted'),
                     last_contacted_at = (SELECT MAX(a.created_at) FROM crm_activity a
                                          WHERE a.building_id = b.id AND a.type = 'contacted'),
                     last_visited_at = (SELECT MAX(a.created_at) FROM crm_activity a
                                        WHERE a.building_id = b.id AND a.type = 'visit'),
                     updated_at = NOW()
                   WHERE b.id = %s""",
                (activity['building_id'],),
            )
        if activity['contact_id']:
            cur.execute(
                """UPDATE crm_contacts c SET
                     last_contacted_at = (SELECT MAX(a.created_at) FROM crm_activity a
                                          WHERE a.contact_id = c.id AND a.type = 'contacted'),
                     updated_at = NOW()
                   WHERE c.id = %s""",
                (activity['contact_id'],),
            )
        conn.commit()
        return True, None
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def get_timeline(ctx, *, building_id=None, contact_id=None, deal_id=None, limit=100):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if building_id:
            scope_sql, scope_val = 'a.building_id = %(scope_id)s', building_id
        elif contact_id:
            scope_sql, scope_val = 'a.contact_id = %(scope_id)s', contact_id
        else:
            scope_sql, scope_val = 'a.deal_id = %(scope_id)s', deal_id
        cur.execute(
            f"""
            SELECT a.*, {_user_name_sql('u')} AS user_name,
                   c.name AS contact_name, b.address AS building_address,
                   d.name AS deal_name
            FROM crm_activity a
            JOIN users u ON u.id = a.user_id
            LEFT JOIN crm_contacts c ON c.id = a.contact_id
              AND {record_scope_sql(ctx, 'c')}
            LEFT JOIN crm_buildings b ON b.id = a.building_id
              AND {record_scope_sql(ctx, 'b')}
            LEFT JOIN crm_deals d ON d.id = a.deal_id
              AND {record_scope_sql(ctx, 'd')}
            WHERE {scope_sql} AND (a.team_id = %(team_id)s OR a.team_id IS NULL)
            ORDER BY a.created_at DESC
            LIMIT %(limit)s
            """,
            {'scope_id': scope_val, 'team_id': ctx['team_id'],
             'user_id': ctx['user_id'], 'limit': limit},
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def team_feed(ctx, *, user_id=None, types=None, limit=50):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        where = ["a.team_id = %(team_id)s", "a.type <> 'system'"]
        params = {'team_id': ctx['team_id'], 'limit': limit,
                  'current_user': ctx['user_id'], 'user_id': ctx['user_id']}
        if not ctx['is_admin']:
            where.append("a.user_id = %(current_user)s")
        if user_id:
            where.append("a.user_id = %(filter_user)s")
            params['filter_user'] = user_id
        if types:
            where.append("a.type = ANY(%(types)s)")
            params['types'] = list(types)
        cur.execute(
            f"""
            SELECT a.*, {_user_name_sql('u')} AS user_name,
                   c.name AS contact_name, b.address AS building_address, b.id AS b_id,
                   d.name AS deal_name
            FROM crm_activity a
            JOIN users u ON u.id = a.user_id
            LEFT JOIN crm_contacts c ON c.id = a.contact_id
              AND {record_scope_sql(ctx, 'c')}
            LEFT JOIN crm_buildings b ON b.id = a.building_id
              AND {record_scope_sql(ctx, 'b')}
            LEFT JOIN crm_deals d ON d.id = a.deal_id
              AND {record_scope_sql(ctx, 'd')}
            WHERE {' AND '.join(where)}
              AND (a.building_id IS NULL OR b.id IS NOT NULL)
              AND (a.contact_id IS NULL OR c.id IS NOT NULL)
              AND (a.deal_id IS NULL OR d.id IS NOT NULL)
            ORDER BY a.created_at DESC
            LIMIT %(limit)s
            """,
            params,
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


# ============================================================
# Stars
# ============================================================

def toggle_star(ctx, *, building_id=None, contact_id=None):
    if not building_id and not contact_id:
        raise ValueError('star needs a target')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if building_id:
            col, val = 'building_id', building_id
        else:
            col, val = 'contact_id', contact_id
        cur.execute(
            f"DELETE FROM crm_stars WHERE user_id = %s AND {col} = %s RETURNING id",
            (ctx['user_id'], val),
        )
        if cur.fetchone():
            starred = False
        else:
            cur.execute(
                f"INSERT INTO crm_stars (user_id, {col}) VALUES (%s, %s)",
                (ctx['user_id'], val),
            )
            starred = True
        conn.commit()
        return starred
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def starred_overview(ctx, *, everyone=False, for_user_id=None):
    """My starred buildings/contacts, or (admin) anyone's / everyone's."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        params = {'team_id': ctx['team_id'], 'user_id': ctx['user_id']}
        record_sql = 'TRUE' if ctx['is_admin'] else """(
            (b.id IS NOT NULL AND b.assigned_to_id = %(user_id)s)
            OR (c.id IS NOT NULL AND c.assigned_to_id = %(user_id)s)
        )"""
        if everyone:
            user_sql = """s.user_id IN (
                SELECT u2.id FROM users u2
                LEFT JOIN account_sponsorships sp2
                       ON sp2.member_user_id = u2.id AND sp2.status = 'active'
                WHERE u2.id = %(team_id)s OR sp2.sponsor_user_id = %(team_id)s)"""
        else:
            user_sql = "s.user_id = %(star_user)s"
            params['star_user'] = for_user_id or ctx['user_id']
        cur.execute(
            f"""
            SELECT s.created_at AS starred_at, s.user_id,
                   {_user_name_sql('su')} AS starred_by_name,
                   b.id AS building_id, b.address, b.borough, b.stage,
                   b.last_contacted_at AS b_last_contacted,
                   c.id AS contact_id, c.name AS contact_name, c.company, c.do_not_contact,
                   c.last_contacted_at AS c_last_contacted
            FROM crm_stars s
            JOIN users su ON su.id = s.user_id
            LEFT JOIN crm_buildings b ON b.id = s.building_id
            LEFT JOIN crm_contacts c ON c.id = s.contact_id
            WHERE {user_sql}
              AND COALESCE(b.team_id, c.team_id, %(team_id)s) = %(team_id)s
              AND {record_sql}
            ORDER BY s.created_at DESC
            """,
            params,
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


# ============================================================
# Follow-ups
# ============================================================

def create_follow_up(ctx, *, title, due_date, due_time=None, note=None,
                     building_id=None, contact_id=None, deal_id=None,
                     assigned_to_id=None, reminder_minutes=15):
    assignee = assigned_to_id or ctx['user_id']
    if not assignee_allowed(ctx, assignee):
        raise PermissionError('Only an admin can assign follow-ups to another rep')
    reminder_minutes = max(0, min(int(reminder_minutes or 0), 10080))
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        scope_params = {'team_id': ctx['team_id'], 'user_id': ctx['user_id']}
        for table, alias, row_id in (
                ('crm_buildings', 'b', building_id),
                ('crm_contacts', 'c', contact_id),
                ('crm_deals', 'd', deal_id)):
            if not row_id:
                continue
            cur.execute(
                f"SELECT 1 FROM {table} {alias} WHERE {alias}.id = %(id)s "
                f"AND {record_scope_sql(ctx, alias)} FOR SHARE",
                {**scope_params, 'id': row_id},
            )
            if not cur.fetchone():
                raise PermissionError('Linked record is no longer assigned to you')
        cur.execute(
            """INSERT INTO crm_follow_ups
               (title, note, due_date, due_at, reminder_minutes, building_id,
                contact_id, deal_id, assigned_to_id, created_by_id, team_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            ((title or 'Follow up').strip()[:255], (note or '').strip() or None,
             due_date, local_due_at(due_date, due_time), reminder_minutes,
             building_id, contact_id, deal_id, assignee, ctx['user_id'], ctx['team_id']),
        )
        follow_up_id = cur.fetchone()['id']
        audit_change(cur, ctx, 'follow_up', follow_up_id,
                     (title or 'Follow up').strip()[:255], 'created',
                     new_value={'due_date': str(due_date), 'due_time': str(due_time or '09:00'),
                                'assigned_to_id': assignee})
        conn.commit()
        return follow_up_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def resolve_follow_up(ctx, follow_up_id, status):
    if status not in ('done', 'skipped', 'open'):
        raise ValueError('invalid follow-up status')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT f.title, f.status FROM crm_follow_ups f
               WHERE f.id = %(id)s AND (f.team_id = %(team_id)s OR f.team_id IS NULL)
                 AND (%(is_admin)s OR f.assigned_to_id = %(user_id)s) FOR UPDATE""",
            {'id': follow_up_id, 'team_id': ctx['team_id'],
             'is_admin': ctx['is_admin'], 'user_id': ctx['user_id']},
        )
        before = cur.fetchone()
        if not before:
            return False
        cur.execute(
            """UPDATE crm_follow_ups
               SET status = %s,
                   completed_at = CASE WHEN %s IN ('done','skipped') THEN NOW() ELSE NULL END
               WHERE id = %s""",
            (status, status, follow_up_id),
        )
        if before['status'] != status:
            audit_change(cur, ctx, 'follow_up', follow_up_id, before['title'],
                         'status_changed', 'status', before['status'], status)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def snooze_follow_up(ctx, follow_up_id, days):
    days = max(1, min(int(days), 90))
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT f.title, f.due_date FROM crm_follow_ups f
               WHERE f.id = %(id)s AND (f.team_id = %(team_id)s OR f.team_id IS NULL)
                 AND (%(is_admin)s OR f.assigned_to_id = %(user_id)s) FOR UPDATE""",
            {'id': follow_up_id, 'team_id': ctx['team_id'],
             'is_admin': ctx['is_admin'], 'user_id': ctx['user_id']},
        )
        before = cur.fetchone()
        if not before:
            return False
        cur.execute(
            """UPDATE crm_follow_ups f
               SET due_date = GREATEST(due_date, %(today)s) + %(delta)s,
                   due_at = GREATEST(COALESCE(due_at, NOW() AT TIME ZONE 'UTC'),
                                     NOW() AT TIME ZONE 'UTC') + %(delta)s,
                   status = 'open', completed_at = NULL, notified_at = NULL
               WHERE f.id = %(id)s AND (f.team_id = %(team_id)s OR f.team_id IS NULL)
                 AND (%(is_admin)s OR f.assigned_to_id = %(user_id)s)
               RETURNING f.due_date""",
            {'today': ny_today(), 'delta': timedelta(days=days), 'id': follow_up_id,
             'team_id': ctx['team_id'], 'is_admin': ctx['is_admin'], 'user_id': ctx['user_id']},
        )
        after = cur.fetchone()
        audit_change(cur, ctx, 'follow_up', follow_up_id, before['title'],
                     'snoozed', 'due_date', before['due_date'], after['due_date'])
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def follow_up_queues(ctx, *, whole_team=False, include_done=False):
    """Overdue / due today / upcoming, plus recently completed if asked."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        today = ny_today()
        params = {'team_id': ctx['team_id'], 'today': today,
                  'user_id': ctx['user_id']}
        whole_team = bool(whole_team and ctx['is_admin'])
        scope = "f.team_id = %(team_id)s" if whole_team else "f.assigned_to_id = %(assignee)s"
        if not whole_team:
            params['assignee'] = ctx['user_id']
        cur.execute(
            f"""
            SELECT f.*, {_user_name_sql('fu')} AS assigned_to_name,
                   b.address AS building_address, b.id AS b_id,
                   c.name AS contact_name, c.id AS c_id,
                   c.do_not_contact AS contact_do_not_contact,
                   d.name AS deal_name, d.id AS d_id,
                   db.id AS deal_b_id, db.address AS deal_building_address,
                   dc.id AS deal_c_id, dc.name AS deal_contact_name,
                   dc.do_not_contact AS deal_contact_do_not_contact
            FROM crm_follow_ups f
            JOIN users fu ON fu.id = f.assigned_to_id
            LEFT JOIN crm_buildings b ON b.id = f.building_id
              AND {record_scope_sql(ctx, 'b')}
            LEFT JOIN crm_contacts c ON c.id = f.contact_id
              AND {record_scope_sql(ctx, 'c')}
            LEFT JOIN crm_deals d ON d.id = f.deal_id
              AND {record_scope_sql(ctx, 'd')}
            LEFT JOIN crm_buildings db ON db.id = d.building_id
              AND {record_scope_sql(ctx, 'db')}
            LEFT JOIN crm_contacts dc ON dc.id = d.contact_id
              AND {record_scope_sql(ctx, 'dc')}
            WHERE {scope} AND f.status = 'open'
              AND (f.building_id IS NULL OR b.id IS NOT NULL)
              AND (f.contact_id IS NULL OR c.id IS NOT NULL)
              AND (f.deal_id IS NULL OR d.id IS NOT NULL)
            ORDER BY COALESCE(f.due_at, f.due_date::timestamp), f.id
            """,
            params,
        )
        overdue, due_today, upcoming = [], [], []
        for row in cur.fetchall():
            row = dict(row)
            if row['due_date'] < today:
                overdue.append(row)
            elif row['due_date'] == today:
                due_today.append(row)
            else:
                upcoming.append(row)
        result = {'overdue': overdue, 'due_today': due_today, 'upcoming': upcoming}
        if include_done:
            cur.execute(
                f"""
                SELECT f.*, {_user_name_sql('fu')} AS assigned_to_name,
                       b.address AS building_address, b.id AS b_id,
                       c.name AS contact_name, c.id AS c_id,
                       c.do_not_contact AS contact_do_not_contact,
                       d.name AS deal_name, d.id AS d_id,
                       db.id AS deal_b_id, db.address AS deal_building_address,
                       dc.id AS deal_c_id, dc.name AS deal_contact_name,
                       dc.do_not_contact AS deal_contact_do_not_contact
                FROM crm_follow_ups f
                JOIN users fu ON fu.id = f.assigned_to_id
                LEFT JOIN crm_buildings b ON b.id = f.building_id
                  AND {record_scope_sql(ctx, 'b')}
                LEFT JOIN crm_contacts c ON c.id = f.contact_id
                  AND {record_scope_sql(ctx, 'c')}
                LEFT JOIN crm_deals d ON d.id = f.deal_id
                  AND {record_scope_sql(ctx, 'd')}
                LEFT JOIN crm_buildings db ON db.id = d.building_id
                  AND {record_scope_sql(ctx, 'db')}
                LEFT JOIN crm_contacts dc ON dc.id = d.contact_id
                  AND {record_scope_sql(ctx, 'dc')}
                WHERE {scope} AND f.status IN ('done','skipped')
                  AND (f.building_id IS NULL OR b.id IS NOT NULL)
                  AND (f.contact_id IS NULL OR c.id IS NOT NULL)
                  AND (f.deal_id IS NULL OR d.id IS NOT NULL)
                ORDER BY f.completed_at DESC NULLS LAST
                LIMIT 50
                """,
                params,
            )
            result['completed'] = [dict(r) for r in cur.fetchall()]
        return result
    finally:
        cur.close()
        conn.close()


# ============================================================
# Lists & saved lead filters
# ============================================================

def list_lists(ctx):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT l.*, {_user_name_sql('ou')} AS owner_name,
                   {_user_name_sql('au')} AS assigned_to_name,
                   (SELECT COUNT(*) FROM crm_list_items li
                    WHERE li.list_id = l.id AND (
                      (li.building_id IS NOT NULL AND EXISTS (
                        SELECT 1 FROM crm_buildings item_b
                        WHERE item_b.id = li.building_id
                          AND {record_scope_sql(ctx, 'item_b')}))
                      OR
                      (li.contact_id IS NOT NULL AND EXISTS (
                        SELECT 1 FROM crm_contacts item_c
                        WHERE item_c.id = li.contact_id
                          AND {record_scope_sql(ctx, 'item_c')}))
                    )) AS item_count
            FROM crm_lists l
            JOIN users ou ON ou.id = l.owner_id
            LEFT JOIN users au ON au.id = l.assigned_to_id
            WHERE {list_scope_sql(ctx, 'l')}
            ORDER BY l.updated_at DESC
            """,
            {'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def create_list(ctx, *, name, description=None, color=None, assigned_to_id=None):
    if not assignee_allowed(ctx, assigned_to_id):
        raise PermissionError('Only an admin can assign a list to another rep')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO crm_lists (name, description, color, owner_id, assigned_to_id, team_id)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
            (name.strip()[:120], (description or '').strip() or None, color or None,
             ctx['user_id'], assigned_to_id or ctx['user_id'], ctx['team_id']),
        )
        list_id = cur.fetchone()['id']
        audit_change(cur, ctx, 'list', list_id, name.strip()[:120], 'created',
                     new_value={'assigned_to_id': assigned_to_id or ctx['user_id']})
        conn.commit()
        return list_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def update_list(ctx, list_id, *, name=None, description=None, assigned_to_id='__keep__'):
    if assigned_to_id != '__keep__' and not assignee_allowed(ctx, assigned_to_id):
        raise PermissionError('Only an admin can transfer a list')
    if not ctx['is_admin'] and assigned_to_id != '__keep__' and assigned_to_id != ctx['user_id']:
        raise PermissionError('Only an admin can transfer a list')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT name, description, assigned_to_id FROM crm_lists l "
            f"WHERE l.id = %(id)s AND {list_scope_sql(ctx, 'l')} FOR UPDATE",
            {'id': list_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        before = cur.fetchone()
        if not before:
            return False
        sets, params = ['updated_at = NOW()'], []
        if name is not None:
            if not name.strip():
                raise ValueError('List name is required')
            sets.append('name = %s')
            params.append(name.strip()[:120])
        if description is not None:
            sets.append('description = %s')
            params.append(description.strip() or None)
        if assigned_to_id != '__keep__':
            sets.append('assigned_to_id = %s')
            params.append(assigned_to_id or None)
        params.append(list_id)
        cur.execute(
            f"UPDATE crm_lists SET {', '.join(sets)} WHERE id = %s",
            params,
        )
        changes = {
            'name': name.strip()[:120] if name is not None else before['name'],
            'description': ((description or '').strip() or None)
            if description is not None else before['description'],
            'assigned_to_id': (assigned_to_id or None)
            if assigned_to_id != '__keep__' else before['assigned_to_id'],
        }
        for field, value in changes.items():
            if before[field] != value:
                audit_change(cur, ctx, 'list', list_id, before['name'],
                             'assigned' if field == 'assigned_to_id' else 'updated',
                             field, before[field], value)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def delete_list(ctx, list_id):
    """Deletes the list; member buildings/contacts survive (items cascade)."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"DELETE FROM crm_lists l WHERE l.id = %(id)s AND {list_scope_sql(ctx, 'l')} "
            f"RETURNING l.name",
            {'id': list_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        deleted = cur.fetchone()
        if deleted:
            audit_change(cur, ctx, 'list', list_id, deleted['name'], 'deleted')
        conn.commit()
        return bool(deleted)
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def get_list(ctx, list_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT l.*, {_user_name_sql('ou')} AS owner_name,
                   {_user_name_sql('au')} AS assigned_to_name
            FROM crm_lists l
            JOIN users ou ON ou.id = l.owner_id
            LEFT JOIN users au ON au.id = l.assigned_to_id
            WHERE l.id = %(list_id)s AND {list_scope_sql(ctx, 'l')}
            """,
            {'list_id': list_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        lst = cur.fetchone()
        if not lst:
            return None
        lst = dict(lst)
        cur.execute(
            f"""
            SELECT li.id AS item_id, li.note AS item_note, li.created_at AS added_at,
                   b.id AS building_id, b.address, b.borough, b.stage,
                   b.last_contacted_at AS b_last_contacted, b.contact_count,
                   c.id AS contact_id, c.name AS contact_name, c.company,
                   c.do_not_contact,
                   c.last_contacted_at AS c_last_contacted,
                   (SELECT p.number || COALESCE(' ext. ' || p.extension, '') FROM crm_phones p
                    WHERE p.contact_id = c.id AND p.status = 'good'
                    ORDER BY p.is_primary DESC, p.created_at LIMIT 1) AS contact_phone
            FROM crm_list_items li
            LEFT JOIN crm_buildings b ON b.id = li.building_id
              AND ({record_scope_sql(ctx, 'b')})
            LEFT JOIN crm_contacts c ON c.id = li.contact_id
              AND ({record_scope_sql(ctx, 'c')})
            WHERE li.list_id = %(list_id)s
              AND (li.building_id IS NULL OR b.id IS NOT NULL)
              AND (li.contact_id IS NULL OR c.id IS NOT NULL)
            ORDER BY li.sort_order, li.id
            """,
            {'list_id': list_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        lst['items'] = [dict(r) for r in cur.fetchall()]
        return lst
    finally:
        cur.close()
        conn.close()


def add_list_item(ctx, list_id, *, building_id=None, contact_id=None, note=None):
    if not building_id and not contact_id:
        raise ValueError('list item needs a target')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT 1 FROM crm_lists l WHERE l.id = %(id)s AND {list_scope_sql(ctx, 'l')}",
            {'id': list_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        if not cur.fetchone():
            raise PermissionError('List not found')
        if building_id:
            cur.execute(
                f"SELECT b.address AS label FROM crm_buildings b WHERE b.id = %(id)s "
                f"AND {record_scope_sql(ctx, 'b')}",
                {'id': building_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
            )
        else:
            cur.execute(
                f"SELECT c.name AS label FROM crm_contacts c WHERE c.id = %(id)s "
                f"AND {record_scope_sql(ctx, 'c')}",
                {'id': contact_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
            )
        target = cur.fetchone()
        if not target:
            raise PermissionError('Record not found')
        col = 'building_id' if building_id else 'contact_id'
        cur.execute(
            f"""INSERT INTO crm_list_items (list_id, {col}, note, added_by_id, sort_order)
                SELECT %s, %s, %s, %s,
                       COALESCE((SELECT MAX(sort_order) FROM crm_list_items WHERE list_id = %s), 0) + 1
                ON CONFLICT DO NOTHING RETURNING id""",
            (list_id, building_id or contact_id, (note or '').strip() or None,
             ctx['user_id'], list_id),
        )
        item = cur.fetchone()
        if item:
            cur.execute("SELECT name FROM crm_lists WHERE id = %s", (list_id,))
            list_row = cur.fetchone()
            audit_change(cur, ctx, 'list', list_id, list_row['name'], 'item_added',
                         col, new_value={'item_id': item['id'], 'record_id': building_id or contact_id,
                                        'label': target['label']})
        cur.execute("UPDATE crm_lists SET updated_at = NOW() WHERE id = %s", (list_id,))
        conn.commit()
        return bool(item)
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def remove_list_item(ctx, item_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT li.list_id, li.building_id, li.contact_id, l.name
                FROM crm_list_items li JOIN crm_lists l ON l.id = li.list_id
                WHERE li.id = %(id)s AND {list_scope_sql(ctx, 'l')}
                FOR UPDATE OF li""",
            {'id': item_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        item = cur.fetchone()
        if not item:
            return False
        cur.execute("DELETE FROM crm_list_items WHERE id = %s", (item_id,))
        audit_change(cur, ctx, 'list', item['list_id'], item['name'], 'item_removed',
                     'building_id' if item['building_id'] else 'contact_id',
                     old_value={'item_id': item_id,
                                'record_id': item['building_id'] or item['contact_id']})
        cur.execute("UPDATE crm_lists SET updated_at = NOW() WHERE id = %s", (item['list_id'],))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# A saved search is private to its owner or shared with the whole team.
SAVED_FILTER_VISIBILITIES = ('team', 'private')


def _visibility(value, default='team'):
    value = (value or '').strip().lower()
    return value if value in SAVED_FILTER_VISIBILITIES else default


def _clean_querystring(raw):
    """Store the filters, not the reader's place in them.

    `page` is where the person happened to be scrolled to when they saved,
    never part of what they meant to save — keeping it would drop everyone
    who opens the search onto page 7 of a list they have not seen yet.
    """
    from urllib.parse import parse_qsl, urlencode
    pairs = [(k, v) for k, v in parse_qsl(str(raw or '').strip().lstrip('?'), keep_blank_values=False)
             if k != 'page']
    return urlencode(pairs)


def list_saved_filters(ctx, page=None):
    """Saved searches this user may see, most useful first.

    Pinned ones lead, then whatever was run most recently; a brand new
    search sorts by when it was created, so it lands at the top of the list
    the moment it is saved.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT sf.*, {_user_name_sql('ou')} AS owner_name,
                       (sf.owner_id = %s) AS is_mine
                FROM crm_saved_filters sf JOIN users ou ON ou.id = sf.owner_id
                WHERE (sf.team_id = %s OR sf.team_id IS NULL)
                  AND (sf.visibility = 'team' OR sf.owner_id = %s)
                  AND (%s::text IS NULL OR sf.page = %s)
                ORDER BY sf.is_pinned DESC,
                         COALESCE(sf.last_used_at, sf.created_at) DESC,
                         sf.id DESC""",
            (ctx['user_id'], ctx['team_id'], ctx['user_id'], page, page),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def save_filter(ctx, *, name, querystring, page='properties', visibility='team'):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO crm_saved_filters
                   (name, querystring, owner_id, team_id, page, visibility)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
            (name.strip()[:120], _clean_querystring(querystring),
             ctx['user_id'], ctx['team_id'], (page or 'properties')[:32],
             _visibility(visibility)),
        )
        filter_id = cur.fetchone()['id']
        conn.commit()
        return filter_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def update_saved_filter(ctx, filter_id, *, name='__keep__', querystring='__keep__',
                        visibility='__keep__', is_pinned='__keep__'):
    """Rename a saved search, point it at the current filters, share it, or
    pin it. Owners edit their own; a team admin can edit any of the team's.
    Returns False when the row is not theirs to touch."""
    sets, params = [], []
    if name != '__keep__' and str(name).strip():
        sets.append('name = %s')
        params.append(str(name).strip()[:120])
    if querystring != '__keep__':
        sets.append('querystring = %s')
        params.append(_clean_querystring(querystring))
    if visibility != '__keep__':
        sets.append('visibility = %s')
        params.append(_visibility(visibility))
    if is_pinned != '__keep__':
        sets.append('is_pinned = %s')
        params.append(bool(is_pinned))
    if not sets:
        return True
    sets.append('updated_at = NOW()')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""UPDATE crm_saved_filters SET {', '.join(sets)}
                WHERE id = %s AND (team_id = %s OR team_id IS NULL)
                  AND (owner_id = %s OR %s)""",
            (*params, filter_id, ctx['team_id'], ctx['user_id'], ctx['is_admin']),
        )
        changed = cur.rowcount > 0
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def touch_saved_filter(ctx, filter_id):
    """Record that someone ran this search, so the list stays ordered by what
    the team actually uses. Decoration only — never breaks the page."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """UPDATE crm_saved_filters
                   SET last_used_at = NOW(), use_count = use_count + 1
                   WHERE id = %s AND (team_id = %s OR team_id IS NULL)
                     AND (visibility = 'team' OR owner_id = %s)""",
                (filter_id, ctx['team_id'], ctx['user_id']),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        print(f"[crm] saved-search touch skipped: {e}", flush=True)


def delete_saved_filter(ctx, filter_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """DELETE FROM crm_saved_filters
               WHERE id = %s AND (team_id = %s OR team_id IS NULL)
                 AND (owner_id = %s OR %s)""",
            (filter_id, ctx['team_id'], ctx['user_id'], ctx['is_admin']),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# ============================================================
# View tracking (the admin X-ray's passive half)
# ============================================================

def log_view(ctx, entity_type, entity_id, label=None):
    """Debounced page-view event; must never break the page it decorates."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cutoff = datetime.utcnow() - timedelta(minutes=VIEW_DEBOUNCE_MINUTES)
            cur.execute(
                """INSERT INTO crm_view_events (user_id, entity_type, entity_id, label, team_id)
                   SELECT %s, %s, %s, %s, %s
                   WHERE NOT EXISTS (
                       SELECT 1 FROM crm_view_events
                       WHERE user_id = %s AND entity_type = %s AND entity_id = %s
                         AND created_at > %s
                   )""",
                (ctx['user_id'], entity_type, entity_id, (label or '')[:255],
                 ctx['team_id'], ctx['user_id'], entity_type, entity_id, cutoff),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        print(f'crm view logging skipped: {e}', flush=True)


def view_log(ctx, *, user_id=None, limit=200):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        where = ["v.team_id = %(team_id)s"]
        params = {'team_id': ctx['team_id'], 'limit': limit}
        if user_id:
            where.append("v.user_id = %(user_id)s")
            params['user_id'] = user_id
        cur.execute(
            f"""SELECT v.*, {_user_name_sql('u')} AS user_name
                FROM crm_view_events v JOIN users u ON u.id = v.user_id
                WHERE {' AND '.join(where)}
                ORDER BY v.created_at DESC LIMIT %(limit)s""",
            params,
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


# ============================================================
# Counters, nudges, and the admin performance table
# ============================================================

def today_counters(ctx):
    start = ny_day_start_utc(0)
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT
                 COUNT(*) FILTER (WHERE type = 'contacted') AS contacted_today,
                 COUNT(*) FILTER (WHERE type = 'visit') AS visits_today,
                 COUNT(*) FILTER (WHERE type = 'note') AS notes_today
               FROM crm_activity
               WHERE user_id = %s AND created_at >= %s""",
            (ctx['user_id'], start),
        )
        return dict(cur.fetchone())
    finally:
        cur.close()
        conn.close()


def needs_attention(ctx, *, stale_days=14, untouched_days=7, limit=12):
    """Stale mid-pipeline buildings and never-contacted prospects."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        now = datetime.utcnow()
        cur.execute(
            f"""
            SELECT b.id, b.address, b.borough, b.stage, b.last_contacted_at, b.created_at,
                   CASE WHEN b.stage IN ('contacted','interested','quoted','nurture')
                             AND COALESCE(b.last_contacted_at, b.created_at) < %(stale)s
                        THEN 'stale' ELSE 'never_contacted' END AS reason
            FROM crm_buildings b
            WHERE {record_scope_sql(ctx, 'b')}
              AND (
                (b.stage IN ('contacted','interested','quoted','nurture')
                 AND COALESCE(b.last_contacted_at, b.created_at) < %(stale)s)
                OR
                (b.stage = 'prospect' AND b.contact_count = 0 AND b.created_at < %(untouched)s)
              )
            ORDER BY COALESCE(b.last_contacted_at, b.created_at)
            LIMIT %(limit)s
            """,
            {'stale': now - timedelta(days=stale_days),
             'untouched': now - timedelta(days=untouched_days), 'limit': limit,
             'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def rep_performance(ctx, days=30):
    """Per-rep activity, pipeline, conversion, and hygiene for admin reports."""
    days = max(1, min(int(days), 365))
    roster = get_team_roster(ctx['team_id'])
    if not roster:
        return []
    user_ids = [r['id'] for r in roster]
    today_start = ny_day_start_utc(0)
    period_start = ny_day_start_utc(-(days - 1))
    today = ny_today()
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT user_id,
                 COUNT(*) FILTER (WHERE type = 'contacted' AND created_at >= %s) AS contacted_today,
                 COUNT(*) FILTER (WHERE type = 'contacted' AND created_at >= %s) AS contacted_period,
                 COUNT(*) FILTER (WHERE type = 'contacted'
                                  AND outcome IN ('spoke','callback_requested','meeting_set')
                                  AND created_at >= %s) AS conversations,
                 COUNT(*) FILTER (WHERE type = 'contacted' AND outcome = 'meeting_set' AND created_at >= %s) AS meetings,
                 COUNT(*) FILTER (WHERE type = 'visit' AND created_at >= %s) AS visits_period,
                 COUNT(*) FILTER (WHERE type = 'note' AND created_at >= %s) AS notes_period
               FROM crm_activity
               WHERE user_id = ANY(%s) AND (team_id = %s OR team_id IS NULL)
               GROUP BY user_id""",
            (today_start, period_start, period_start, period_start,
             period_start, period_start, user_ids, ctx['team_id']),
        )
        activity = {r['user_id']: dict(r) for r in cur.fetchall()}
        cur.execute(
            """SELECT assigned_to_id AS user_id,
                 COUNT(*) FILTER (WHERE status = 'open') AS followups_open,
                 COUNT(*) FILTER (WHERE status = 'open' AND due_date < %s) AS followups_overdue,
                 COUNT(*) FILTER (WHERE status = 'done' AND completed_at >= %s) AS followups_done_period
               FROM crm_follow_ups
               WHERE assigned_to_id = ANY(%s) AND (team_id = %s OR team_id IS NULL)
               GROUP BY assigned_to_id""",
            (today, period_start, user_ids, ctx['team_id']),
        )
        followups = {r['user_id']: dict(r) for r in cur.fetchall()}
        cur.execute(
            """SELECT user_id, COUNT(*) AS views_today
               FROM crm_view_events
               WHERE user_id = ANY(%s) AND created_at >= %s AND team_id = %s
               GROUP BY user_id""",
            (user_ids, today_start, ctx['team_id']),
        )
        views = {r['user_id']: dict(r) for r in cur.fetchall()}
        cur.execute(
            """SELECT assigned_to_id AS user_id,
                      COUNT(*) FILTER (WHERE stage NOT IN ('won','lost','client')) AS open_deals,
                      COALESCE(SUM(estimated_value) FILTER (WHERE stage NOT IN ('won','lost')), 0) AS open_value,
                      COUNT(*) FILTER (WHERE stage = 'nurture') AS nurture_deals,
                      COUNT(*) FILTER (WHERE stage IN ('won','client') AND won_at >= %s) AS wins,
                      COALESCE(SUM(estimated_value) FILTER (WHERE stage IN ('won','client') AND won_at >= %s), 0) AS won_value,
                      COUNT(*) FILTER (WHERE stage = 'lost' AND lost_at >= %s) AS losses,
                      COUNT(*) FILTER (WHERE stage NOT IN ('won','lost','client') AND next_step_at IS NULL) AS no_next_step
               FROM crm_deals
               WHERE assigned_to_id = ANY(%s) AND (team_id = %s OR team_id IS NULL)
               GROUP BY assigned_to_id""",
            (period_start, period_start, period_start, user_ids, ctx['team_id']),
        )
        deals = {r['user_id']: dict(r) for r in cur.fetchall()}
        cur.execute(
            """SELECT added_by_id AS user_id, COUNT(*) FILTER (WHERE created_at >= %s) AS records_added
               FROM (
                   SELECT added_by_id, created_at FROM crm_buildings WHERE team_id = %s
                   UNION ALL SELECT added_by_id, created_at FROM crm_contacts WHERE team_id = %s
                   UNION ALL SELECT added_by_id, created_at FROM crm_deals WHERE team_id = %s
               ) records
               WHERE added_by_id = ANY(%s) GROUP BY added_by_id""",
            (period_start, ctx['team_id'], ctx['team_id'], ctx['team_id'], user_ids),
        )
        records = {r['user_id']: dict(r) for r in cur.fetchall()}
        out = []
        for rep in roster:
            row = dict(rep)
            row.update({'contacted_today': 0, 'contacted_period': 0, 'conversations': 0,
                        'meetings': 0, 'visits_period': 0, 'notes_period': 0,
                        'followups_open': 0, 'followups_overdue': 0,
                        'followups_done_period': 0, 'views_today': 0,
                        'open_deals': 0, 'open_value': 0, 'nurture_deals': 0,
                        'wins': 0, 'won_value': 0, 'losses': 0,
                        'no_next_step': 0, 'records_added': 0})
            row.update(activity.get(rep['id'], {}))
            row.update(followups.get(rep['id'], {}))
            row.update(views.get(rep['id'], {}))
            row.update(deals.get(rep['id'], {}))
            row.update(records.get(rep['id'], {}))
            row['contacted_7d'] = row['contacted_period']
            row['visits_7d'] = row['visits_period']
            row['notes_7d'] = row['notes_period']
            row['followups_done_7d'] = row['followups_done_period']
            row['conversation_rate'] = round(100 * row['conversations'] / row['contacted_period'], 1) if row['contacted_period'] else 0
            row['win_rate'] = round(100 * row['wins'] / (row['wins'] + row['losses']), 1) if row['wins'] + row['losses'] else 0
            out.append(row)
        return out
    finally:
        cur.close()
        conn.close()


# ============================================================
# CSV exports
# ============================================================

def export_buildings_rows(ctx):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT b.bbl, b.address, b.borough, b.zip_code, b.stage, b.source,
                       b.owner_name, b.unit_count, b.year_built,
                       b.contact_count, b.last_contacted_at, b.last_visited_at,
                       {_user_name_sql('au')} AS assigned_to, b.created_at
                FROM crm_buildings b
                LEFT JOIN users au ON au.id = b.assigned_to_id
                WHERE (b.team_id = %s OR b.team_id IS NULL)
                ORDER BY b.address""",
            (ctx['team_id'],),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def export_activity_rows(ctx, *, days=90):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT a.created_at, a.type, a.method, a.outcome,
                       {_user_name_sql('u')} AS rep,
                       b.address AS building, c.name AS contact, a.note
                FROM crm_activity a
                JOIN users u ON u.id = a.user_id
                LEFT JOIN crm_buildings b ON b.id = a.building_id
                LEFT JOIN crm_contacts c ON c.id = a.contact_id
                WHERE a.team_id = %s AND a.created_at >= %s AND a.type <> 'system'
                ORDER BY a.created_at DESC""",
            (ctx['team_id'], datetime.utcnow() - timedelta(days=days)),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def export_deals_rows(ctx):
    """Admin-ready opportunity export with ownership and sourcing attribution."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT d.name, d.service_type, d.stage, d.estimated_value,
                       d.expected_close_date, d.next_step, d.next_step_at,
                       d.lost_reason, d.source, b.address AS building,
                       c.name AS contact, {_user_name_sql('au')} AS assigned_to,
                       {_user_name_sql('ab')} AS added_by, d.created_at, d.updated_at
                FROM crm_deals d
                LEFT JOIN crm_buildings b ON b.id = d.building_id
                LEFT JOIN crm_contacts c ON c.id = d.contact_id
                LEFT JOIN users au ON au.id = d.assigned_to_id
                JOIN users ab ON ab.id = d.added_by_id
                WHERE d.team_id = %s ORDER BY d.updated_at DESC""",
            (ctx['team_id'],),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


# ============================================================
# v2: search, editing, merge, bulk, focus queue, reports
# ============================================================

def global_search(ctx, q, limit=6):
    """⌘K search across every record the current user may access."""
    q = (q or '').strip()
    if len(q) < 2:
        return {'buildings': [], 'contacts': [], 'deals': [], 'lists': []}
    like = f'%{q}%'
    digits = normalize_phone_digits(q)
    params = {'team_id': ctx['team_id'], 'user_id': ctx['user_id'], 'like': like,
              'raw': q, 'digits': f'%{digits}%', 'limit': limit}
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT b.id, b.address, b.borough, b.stage, b.last_contacted_at
               FROM crm_buildings b
               WHERE {record_scope_sql(ctx, 'b')}
                 AND (b.address ILIKE %(like)s OR b.owner_name ILIKE %(like)s OR b.bbl = %(raw)s)
               ORDER BY b.last_contacted_at DESC NULLS LAST, b.address LIMIT %(limit)s""",
            params,
        )
        buildings = [dict(r) for r in cur.fetchall()]
        if digits and len(digits) >= 4:
            cur.execute(
                f"""SELECT DISTINCT c.id, c.name, c.company, c.title, c.last_contacted_at
                   FROM crm_contacts c
                   LEFT JOIN crm_phones p ON p.contact_id = c.id
                   WHERE {record_scope_sql(ctx, 'c')}
                     AND (c.name ILIKE %(like)s OR c.company ILIKE %(like)s OR p.digits LIKE %(digits)s)
                   ORDER BY c.last_contacted_at DESC NULLS LAST, c.name LIMIT %(limit)s""",
                params,
            )
        else:
            cur.execute(
                f"""SELECT c.id, c.name, c.company, c.title, c.last_contacted_at
                   FROM crm_contacts c
                   WHERE {record_scope_sql(ctx, 'c')}
                     AND (c.name ILIKE %(like)s OR c.company ILIKE %(like)s)
                   ORDER BY c.last_contacted_at DESC NULLS LAST, c.name LIMIT %(limit)s""",
                params,
            )
        contacts = [dict(r) for r in cur.fetchall()]
        cur.execute(
            f"""SELECT l.id, l.name FROM crm_lists l
               WHERE {list_scope_sql(ctx, 'l')} AND l.name ILIKE %(like)s
               ORDER BY l.updated_at DESC LIMIT %(limit)s""",
            params,
        )
        lists = [dict(r) for r in cur.fetchall()]
        cur.execute(
            f"""SELECT d.id, d.name, d.service_type, d.stage, d.estimated_value
                FROM crm_deals d WHERE {record_scope_sql(ctx, 'd')}
                  AND (d.name ILIKE %(like)s OR d.service_type ILIKE %(like)s)
                ORDER BY d.updated_at DESC LIMIT %(limit)s""",
            params,
        )
        deals = [dict(r) for r in cur.fetchall()]
        return {'buildings': buildings, 'contacts': contacts, 'deals': deals, 'lists': lists}
    finally:
        cur.close()
        conn.close()


BUILDING_EDITABLE = ('address', 'borough', 'zip_code', 'neighborhood', 'unit_count',
                     'year_built', 'num_floors', 'building_class', 'owner_name')
CONTACT_EDITABLE = ('name', 'title', 'company', 'email')


def _run_update(table, allowed, ctx, row_id, fields, int_fields=()):
    sets, params = [], []
    for key in allowed:
        if key not in fields:
            continue
        value = fields[key]
        if isinstance(value, str):
            value = value.strip() or None
        if key in int_fields and value is not None:
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = None
        sets.append(f'{key} = %s')
        params.append(value)
    if not sets:
        return False
    sets.append('updated_at = NOW()')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        alias = 'b' if table == 'crm_buildings' else 'c'
        label_col = 'address' if table == 'crm_buildings' else 'name'
        cur.execute(
            f"SELECT {', '.join(allowed)}, {label_col} AS _label FROM {table} {alias} "
            f"WHERE {alias}.id = %(id)s AND {record_scope_sql(ctx, alias)} FOR UPDATE",
            {'id': row_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        before = cur.fetchone()
        if not before:
            return False
        params.append(row_id)
        cur.execute(
            f"UPDATE {table} SET {', '.join(sets)} WHERE id = %s",
            params,
        )
        for key in allowed:
            if key in fields and before.get(key) != fields.get(key):
                audit_change(cur, ctx, 'building' if table == 'crm_buildings' else 'contact',
                             row_id, before['_label'], 'updated', key,
                             before.get(key), fields.get(key))
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def update_building(ctx, building_id, fields):
    if 'address' in fields and not (fields.get('address') or '').strip():
        raise ValueError('address is required')
    return _run_update('crm_buildings', BUILDING_EDITABLE, ctx, building_id, fields,
                       int_fields=('unit_count', 'year_built', 'num_floors'))


def delete_building(ctx, building_id):
    """Delete one team building while retaining an admin audit tombstone."""
    if not ctx['is_admin']:
        raise PermissionError('Admin access required')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT address FROM crm_buildings
               WHERE id = %s AND (team_id = %s OR team_id IS NULL)
               FOR UPDATE""",
            (building_id, ctx['team_id']),
        )
        row = cur.fetchone()
        if not row:
            return False
        audit_change(cur, ctx, 'building', building_id, row['address'], 'deleted')
        cur.execute("DELETE FROM crm_buildings WHERE id = %s", (building_id,))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def update_contact(ctx, contact_id, fields):
    if 'name' in fields and not (fields.get('name') or '').strip():
        raise ValueError('name is required')
    return _run_update('crm_contacts', CONTACT_EDITABLE, ctx, contact_id, fields)


def update_follow_up(ctx, follow_up_id, *, title=None, due_date=None, due_time=None,
                     note=None, assigned_to_id='__keep__', reminder_minutes=None):
    sets, params = [], []
    if title is not None:
        sets.append('title = %s')
        params.append((title.strip() or 'Follow up')[:255])
    if due_date is not None:
        sets.append('due_date = %s')
        params.append(due_date)
        sets.append('due_at = %s')
        params.append(local_due_at(due_date, due_time))
        sets.append('notified_at = NULL')
    if note is not None:
        sets.append('note = %s')
        params.append(note.strip() or None)
    if assigned_to_id != '__keep__':
        if not assignee_allowed(ctx, assigned_to_id or ctx['user_id']):
            raise PermissionError('Only an admin can transfer a follow-up')
        sets.append('assigned_to_id = %s')
        params.append(assigned_to_id or ctx['user_id'])
    if reminder_minutes is not None:
        sets.append('reminder_minutes = %s')
        params.append(max(0, min(int(reminder_minutes), 10080)))
    if not sets:
        return
    params.extend([follow_up_id, ctx['team_id'], ctx['is_admin'], ctx['user_id']])
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT title, due_date, due_at, note, assigned_to_id, reminder_minutes
               FROM crm_follow_ups
               WHERE id = %s AND (team_id = %s OR team_id IS NULL)
                 AND (%s OR assigned_to_id = %s)
               FOR UPDATE""",
            (follow_up_id, ctx['team_id'], ctx['is_admin'], ctx['user_id']),
        )
        before = cur.fetchone()
        if not before:
            return False
        cur.execute(
            f"""UPDATE crm_follow_ups SET {', '.join(sets)}
                WHERE id = %s AND (team_id = %s OR team_id IS NULL)
                  AND (%s OR assigned_to_id = %s)""",
            params,
        )
        changed = {}
        if title is not None:
            changed['title'] = (title.strip() or 'Follow up')[:255]
        if due_date is not None:
            changed['due_date'] = due_date
            changed['due_at'] = local_due_at(due_date, due_time)
        if note is not None:
            changed['note'] = note.strip() or None
        if assigned_to_id != '__keep__':
            changed['assigned_to_id'] = assigned_to_id or ctx['user_id']
        if reminder_minutes is not None:
            changed['reminder_minutes'] = max(0, min(int(reminder_minutes), 10080))
        for field, value in changed.items():
            if _json_value(before.get(field)) != _json_value(value):
                audit_change(cur, ctx, 'follow_up', follow_up_id, before['title'],
                             'updated', field, before.get(field), value)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def delete_follow_up(ctx, follow_up_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """DELETE FROM crm_follow_ups WHERE id = %s
               AND (team_id = %s OR team_id IS NULL)
               AND (%s OR assigned_to_id = %s)
               RETURNING title""",
            (follow_up_id, ctx['team_id'], ctx['is_admin'], ctx['user_id']),
        )
        row = cur.fetchone()
        if row:
            audit_change(cur, ctx, 'follow_up', follow_up_id, row['title'], 'deleted')
        conn.commit()
        return bool(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def update_phone(ctx, phone_id, *, label='__keep__', extension='__keep__', make_primary=None):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT p.id, p.contact_id, p.number, p.label, p.extension,
                       p.is_primary, c.name
                FROM crm_phones p JOIN crm_contacts c ON c.id = p.contact_id
               WHERE p.id = %(id)s AND {record_scope_sql(ctx, 'c')}""",
            {'id': phone_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        row = cur.fetchone()
        if not row:
            return False
        if label != '__keep__':
            cur.execute("UPDATE crm_phones SET label = %s WHERE id = %s",
                        ((label or '').strip() or None, phone_id))
        if extension != '__keep__':
            cur.execute("UPDATE crm_phones SET extension = %s WHERE id = %s",
                        (normalize_extension(extension), phone_id))
        if make_primary:
            cur.execute("UPDATE crm_phones SET is_primary = FALSE WHERE contact_id = %s", (row['contact_id'],))
            cur.execute("UPDATE crm_phones SET is_primary = TRUE WHERE id = %s", (phone_id,))
        old_phone = {'number': row['number'], 'label': row['label'],
                     'extension': row['extension'], 'is_primary': row['is_primary']}
        new_phone = {
            'number': row['number'],
            'label': row['label'] if label == '__keep__' else ((label or '').strip() or None),
            'extension': row['extension'] if extension == '__keep__' else normalize_extension(extension),
            'is_primary': True if make_primary else row['is_primary'],
        }
        if old_phone != new_phone:
            audit_change(cur, ctx, 'contact', row['contact_id'], row['name'],
                         'phone_updated', 'phone', old_phone, new_phone)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def delete_phone(ctx, phone_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT p.contact_id, p.number, p.extension, c.name
                FROM crm_phones p JOIN crm_contacts c ON c.id = p.contact_id
                WHERE p.id = %(id)s AND {record_scope_sql(ctx, 'c')}
                FOR UPDATE""",
            {'id': phone_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        row = cur.fetchone()
        if not row:
            return False
        cur.execute("DELETE FROM crm_phones WHERE id = %s", (phone_id,))
        audit_change(cur, ctx, 'contact', row['contact_id'], row['name'],
                     'phone_deleted', 'phone',
                     old_value={'number': row['number'], 'extension': row['extension']})
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def unlink_contact(ctx, building_id, contact_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT bc.role, b.address, c.name
                FROM crm_building_contacts bc
                JOIN crm_buildings b ON b.id = bc.building_id
                JOIN crm_contacts c ON c.id = bc.contact_id
                WHERE bc.building_id = %(building_id)s AND bc.contact_id = %(contact_id)s
                  AND {record_scope_sql(ctx, 'b')} AND {record_scope_sql(ctx, 'c')}
                FOR UPDATE OF bc""",
            {'building_id': building_id, 'contact_id': contact_id,
             'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        before = cur.fetchone()
        if not before:
            return False
        cur.execute("DELETE FROM crm_building_contacts WHERE building_id = %s AND contact_id = %s",
                    (building_id, contact_id))
        audit_change(cur, ctx, 'building', building_id, before['address'],
                     'contact_unlinked', 'contact',
                     old_value={'contact_id': contact_id, 'contact_name': before['name'],
                                'role': before['role']})
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def set_building_contact_role(ctx, building_id, contact_id, role):
    role = role if role in BUILDING_CONTACT_ROLES else 'other'
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT bc.role, b.address, c.name
                FROM crm_building_contacts bc
                JOIN crm_buildings b ON b.id = bc.building_id
                JOIN crm_contacts c ON c.id = bc.contact_id
                WHERE bc.building_id = %(building_id)s AND bc.contact_id = %(contact_id)s
                  AND {record_scope_sql(ctx, 'b')} AND {record_scope_sql(ctx, 'c')}
                FOR UPDATE OF bc""",
            {'building_id': building_id, 'contact_id': contact_id,
             'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        before = cur.fetchone()
        if not before:
            return False
        cur.execute("UPDATE crm_building_contacts SET role = %s WHERE building_id = %s AND contact_id = %s",
                    (role, building_id, contact_id))
        if before['role'] != role:
            audit_change(cur, ctx, 'building', building_id, before['address'],
                         'contact_role_changed', 'contact_role', before['role'],
                         {'role': role, 'contact_id': contact_id, 'contact_name': before['name']})
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def delete_contact(ctx, contact_id):
    """Removes a person and everything hanging off them (cascade). Admin-only."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """DELETE FROM crm_contacts
               WHERE id = %s AND (team_id = %s OR team_id IS NULL)
               RETURNING name""",
            (contact_id, ctx['team_id']),
        )
        row = cur.fetchone()
        if row:
            audit_change(cur, ctx, 'contact', contact_id, row['name'], 'deleted')
        conn.commit()
        return bool(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def merge_contacts(ctx, source_id, target_id):
    """Fold `source` into `target`: phones, building links, activity, follow-ups,
    stars, list items move over (deduped); empty target fields fill from the
    source; the source is deleted and the merge is logged on the target."""
    if source_id == target_id:
        raise ValueError('pick two different people')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT id, name, title, company, email, last_contacted_at FROM crm_contacts
               WHERE id IN (%s, %s) AND (team_id = %s OR team_id IS NULL)""",
            (source_id, target_id, ctx['team_id']),
        )
        rows = {r['id']: dict(r) for r in cur.fetchall()}
        if len(rows) != 2:
            return False
        source, target = rows[source_id], rows[target_id]
        cur.execute(
            """UPDATE crm_phones SET contact_id = %s, is_primary = FALSE
               WHERE contact_id = %s AND digits NOT IN
                     (SELECT digits FROM crm_phones WHERE contact_id = %s)""",
            (target_id, source_id, target_id),
        )
        cur.execute(
            """UPDATE crm_building_contacts SET contact_id = %s
               WHERE contact_id = %s AND building_id NOT IN
                     (SELECT building_id FROM crm_building_contacts WHERE contact_id = %s)""",
            (target_id, source_id, target_id),
        )
        cur.execute("UPDATE crm_activity SET contact_id = %s WHERE contact_id = %s", (target_id, source_id))
        cur.execute("UPDATE crm_follow_ups SET contact_id = %s WHERE contact_id = %s", (target_id, source_id))
        cur.execute(
            """UPDATE crm_stars SET contact_id = %s
               WHERE contact_id = %s AND user_id NOT IN
                     (SELECT user_id FROM crm_stars WHERE contact_id = %s)""",
            (target_id, source_id, target_id),
        )
        cur.execute(
            """UPDATE crm_list_items SET contact_id = %s
               WHERE contact_id = %s AND list_id NOT IN
                     (SELECT list_id FROM crm_list_items WHERE contact_id = %s)""",
            (target_id, source_id, target_id),
        )
        cur.execute(
            """UPDATE crm_contacts SET
                 title = COALESCE(title, %s), company = COALESCE(company, %s),
                 email = COALESCE(email, %s),
                 last_contacted_at = GREATEST(COALESCE(last_contacted_at, %s), COALESCE(%s, last_contacted_at)),
                 updated_at = NOW()
               WHERE id = %s""",
            (source['title'], source['company'], source['email'],
             source['last_contacted_at'], source['last_contacted_at'], target_id),
        )
        # Remaining source rows (true duplicates) go with the source via cascade.
        cur.execute("DELETE FROM crm_contacts WHERE id = %s", (source_id,))
        cur.execute(
            """INSERT INTO crm_activity (type, note, contact_id, user_id, team_id, meta)
               VALUES ('system', %s, %s, %s, %s, %s)""",
            (f"Merged in duplicate record “{source['name']}”", target_id,
             ctx['user_id'], ctx['team_id'], Json({'merged_from': source_id})),
        )
        audit_change(cur, ctx, 'contact', target_id, target['name'], 'merged',
                     new_value={'merged_from_id': source_id,
                                'merged_from_name': source['name']})
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def find_duplicate_contacts(ctx, limit=20):
    """Numbers shared by more than one contact on the team."""
    if not ctx['is_admin']:
        return []
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT p.digits, array_agg(DISTINCT c.id) AS ids, array_agg(DISTINCT c.name) AS names
               FROM crm_phones p JOIN crm_contacts c ON c.id = p.contact_id
               WHERE (c.team_id = %s OR c.team_id IS NULL)
               GROUP BY p.digits HAVING COUNT(DISTINCT c.id) > 1
               ORDER BY COUNT(DISTINCT c.id) DESC LIMIT %s""",
            (ctx['team_id'], limit),
        )
        out = []
        for r in cur.fetchall():
            out.append({'digits': r['digits'], 'number': format_phone(r['digits']),
                        'ids': list(r['ids']), 'names': list(r['names'])})
        return out
    finally:
        cur.close()
        conn.close()


def bulk_update_buildings(ctx, building_ids, action, value=None):
    """Bulk stage / assign / star / list membership for a set of buildings."""
    ids = [int(i) for i in building_ids][:500]
    if not ids:
        return 0
    if action == 'assign':
        assignee = int(value) if value else None
        if not ctx['is_admin'] or not assignee_allowed(ctx, assignee):
            raise PermissionError('Only an admin can transfer records')
    if action == 'list' and not get_list(ctx, int(value)):
        raise PermissionError('List not found')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT b.id, b.address, b.stage, b.assigned_to_id
                FROM crm_buildings b WHERE b.id = ANY(%(ids)s)
                AND {record_scope_sql(ctx, 'b')} FOR UPDATE""",
            {'ids': ids, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        visible_rows = {r['id']: dict(r) for r in cur.fetchall()}
        visible = set(visible_rows)
        # Keep the caller's selection order — it becomes the list order.
        ids = [i for i in ids if i in visible]
        if not ids:
            return 0
        if action == 'stage':
            if value not in STAGES:
                raise ValueError('invalid stage')
            cur.execute(
                """UPDATE crm_buildings SET stage = %s, updated_at = NOW()
                   WHERE id = ANY(%s) AND stage <> %s RETURNING id""",
                (value, ids, value),
            )
            changed = [r['id'] for r in cur.fetchall()]
            for bid in changed:
                cur.execute(
                    """INSERT INTO crm_activity (type, note, building_id, user_id, team_id)
                       VALUES ('stage_change', %s, %s, %s, %s)""",
                    (f'Stage set to {STAGE_LABELS[value]} (bulk)', bid, ctx['user_id'], ctx['team_id']),
                )
                before = visible_rows[bid]
                audit_change(cur, ctx, 'building', bid, before['address'], 'stage_changed',
                             'stage', before['stage'], value)
        elif action == 'assign':
            assignee = int(value) if value else None
            cur.execute(
                "UPDATE crm_buildings SET assigned_to_id = %s, updated_at = NOW() WHERE id = ANY(%s)",
                (assignee, ids),
            )
            if assignee:
                cur.execute(
                    """UPDATE crm_contacts c SET assigned_to_id = %s, updated_at = NOW()
                       FROM crm_building_contacts bc
                       WHERE bc.contact_id = c.id AND bc.building_id = ANY(%s)
                         AND c.assigned_to_id IS NULL""",
                    (assignee, ids),
                )
            for bid in ids:
                before = visible_rows[bid]
                if before['assigned_to_id'] == assignee:
                    continue
                if before['assigned_to_id']:
                    cur.execute(
                        "DELETE FROM crm_notifications WHERE user_id = %s AND url = %s",
                        (before['assigned_to_id'], f'/crm/buildings/{bid}'),
                    )
                audit_change(cur, ctx, 'building', bid, before['address'], 'assigned',
                             'assigned_to_id', before['assigned_to_id'], assignee)
                if assignee:
                    create_notification(cur, ctx, assignee, 'assignment',
                                        'A building was assigned to you', before['address'],
                                        f'/crm/buildings/{bid}',
                                        f'building-assigned:{bid}:{assignee}')
        elif action == 'star':
            cur.execute(
                """INSERT INTO crm_stars (user_id, building_id)
                   SELECT %s, b FROM unnest(%s::int[]) AS b
                   ON CONFLICT DO NOTHING""",
                (ctx['user_id'], ids),
            )
        elif action == 'unstar':
            cur.execute("DELETE FROM crm_stars WHERE user_id = %s AND building_id = ANY(%s)",
                        (ctx['user_id'], ids))
        elif action == 'list':
            list_id = int(value)
            cur.execute(
                """INSERT INTO crm_list_items (list_id, building_id, added_by_id, sort_order)
                   SELECT %s, t.b, %s,
                          COALESCE((SELECT MAX(sort_order) FROM crm_list_items WHERE list_id = %s), 0) + t.ord
                   FROM unnest(%s::int[]) WITH ORDINALITY AS t(b, ord)
                   ON CONFLICT DO NOTHING RETURNING building_id""",
                (list_id, ctx['user_id'], list_id, ids),
            )
            added_ids = [r['building_id'] for r in cur.fetchall()]
            if added_ids:
                cur.execute("SELECT name FROM crm_lists WHERE id = %s", (list_id,))
                list_row = cur.fetchone()
                for bid in added_ids:
                    audit_change(cur, ctx, 'list', list_id, list_row['name'], 'item_added',
                                 'building_id',
                                 new_value={'record_id': bid,
                                            'label': visible_rows[bid]['address']})
            cur.execute("UPDATE crm_lists SET updated_at = NOW() WHERE id = %s", (list_id,))
        else:
            raise ValueError('unknown bulk action')
        conn.commit()
        return len(ids)
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def focus_queue(ctx, source='today', list_id=None, limit=40):
    """Ordered call queue for Focus mode.

    today:     my overdue + due-today follow-ups, then needs-attention buildings
    list:<id>: a work list in its order
    attention: stale / never-contacted buildings
    cold:      buildings untouched 30d+
    """
    items, seen = [], set()

    def push(kind, ident, label, sub=None, followup_id=None, followup_title=None,
             deal_id=None):
        key = (kind, ident)
        if key in seen or ident is None:
            return
        seen.add(key)
        items.append({'type': kind, 'id': ident, 'label': label, 'sub': sub,
                      'followup_id': followup_id, 'followup_title': followup_title,
                      'deal_id': deal_id})

    if source == 'list' and list_id:
        lst = get_list(ctx, list_id)
        for it in (lst or {}).get('items', []):
            if it['building_id']:
                push('building', it['building_id'], it['address'], it['borough'])
            elif it['contact_id'] and not it.get('do_not_contact'):
                push('contact', it['contact_id'], it['contact_name'], it['company'])
    elif source == 'nurture':
        for d in list_deals(ctx, stage='nurture', limit=limit):
            if d.get('contact_id') and not d.get('contact_do_not_contact'):
                push('contact', d['contact_id'], d.get('contact_name') or d['name'],
                     f'Nurture: {d["name"]}', deal_id=d['id'])
            elif d.get('building_id'):
                push('building', d['building_id'], d.get('building_address') or d['name'],
                     f'Nurture: {d["name"]}', deal_id=d['id'])
    elif source == 'next_steps':
        for d in list_deals(ctx, missing_next_step=True, limit=limit):
            if d.get('contact_id') and not d.get('contact_do_not_contact'):
                push('contact', d['contact_id'], d.get('contact_name') or d['name'],
                     f'No next step: {d["name"]}', deal_id=d['id'])
            elif d.get('building_id'):
                push('building', d['building_id'], d.get('building_address') or d['name'],
                     f'No next step: {d["name"]}', deal_id=d['id'])
        for r in records_missing_next_step(ctx, limit=limit):
            if r['kind'] == 'building':
                push('building', r['id'], r['label'], 'No next step scheduled')
    elif source == 'cold':
        for b in list_buildings(ctx, cold=True, sort='last_contacted', limit=limit):
            push('building', b['id'], b['address'], 'Untouched 30d+')
    elif source == 'attention':
        for b in needs_attention(ctx, limit=limit):
            push('building', b['id'], b['address'],
                 'Stale' if b['reason'] == 'stale' else 'Never contacted')
    else:
        q = follow_up_queues(ctx)
        for f in q['overdue'] + q['due_today']:
            if f['b_id']:
                push('building', f['b_id'], f['building_address'],
                     f'Follow-up: {f["title"]}', f['id'], f['title'], f.get('d_id'))
            elif f['c_id'] and not f.get('contact_do_not_contact'):
                push('contact', f['c_id'], f['contact_name'],
                     f'Follow-up: {f["title"]}', f['id'], f['title'], f.get('d_id'))
            elif f.get('deal_c_id') and not f.get('deal_contact_do_not_contact'):
                push('contact', f['deal_c_id'], f['deal_contact_name'],
                     f'Deal follow-up: {f["deal_name"]}', f['id'], f['title'], f['d_id'])
            elif f.get('deal_b_id'):
                push('building', f['deal_b_id'], f['deal_building_address'],
                     f'Deal follow-up: {f["deal_name"]}', f['id'], f['title'], f['d_id'])
        for d in due_deal_steps(ctx, limit=limit):
            if d.get('contact_id') and not d.get('do_not_contact'):
                push('contact', d['contact_id'], d['contact_name'],
                     f'Deal next step: {d["next_step"] or d["name"]}', deal_id=d['id'])
            elif d.get('building_id'):
                push('building', d['building_id'], d['building_address'],
                     f'Deal next step: {d["next_step"] or d["name"]}', deal_id=d['id'])
        for b in needs_attention(ctx, limit=limit):
            push('building', b['id'], b['address'],
                 'Stale' if b['reason'] == 'stale' else 'Never contacted')
    return items[:limit]


def due_count(ctx):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT
                 (SELECT COUNT(*) FROM crm_follow_ups
                  WHERE assigned_to_id = %(user_id)s AND status = 'open'
                    AND due_date <= %(today)s)
                 +
                 (SELECT COUNT(*) FROM crm_deals d
                  WHERE {record_scope_sql(ctx, 'd')}
                    AND d.stage NOT IN ('won','lost','client')
                    AND d.next_step_at IS NOT NULL
                    AND d.next_step_at < %(tomorrow)s) AS n""",
            {'user_id': ctx['user_id'], 'team_id': ctx['team_id'],
             'today': ny_today(), 'tomorrow': ny_day_start_utc(1)},
        )
        return cur.fetchone()['n']
    finally:
        cur.close()
        conn.close()


def touches_per_day(ctx, days=14, user_id=None):
    """Contacted events per NY calendar day, zero-filled, oldest first."""
    start = ny_day_start_utc(-(days - 1))
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        where = "team_id = %s AND type = 'contacted' AND created_at >= %s"
        params = [ctx['team_id'], start]
        if user_id:
            where += " AND user_id = %s"
            params.append(user_id)
        cur.execute(
            f"""SELECT ((created_at AT TIME ZONE 'UTC') AT TIME ZONE 'America/New_York')::date AS day,
                       COUNT(*) AS n
                FROM crm_activity WHERE {where} GROUP BY day""",
            params,
        )
        counts = {r['day']: r['n'] for r in cur.fetchall()}
        today = ny_today()
        return [{'day': today - timedelta(days=i), 'n': counts.get(today - timedelta(days=i), 0)}
                for i in range(days - 1, -1, -1)]
    finally:
        cur.close()
        conn.close()


def outcome_mix(ctx, days=30):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT COALESCE(outcome, 'unspecified') AS outcome, COUNT(*) AS n
               FROM crm_activity
               WHERE team_id = %s AND type = 'contacted' AND created_at >= %s
               GROUP BY outcome ORDER BY n DESC""",
            (ctx['team_id'], datetime.utcnow() - timedelta(days=days)),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def stage_funnel(ctx):
    counts = building_stage_counts(ctx)
    return [{'stage': s, 'label': STAGE_LABELS[s], 'n': counts.get(s, 0)} for s in STAGES]


def contacts_alpha_groups(contacts):
    """Group a contact list Apple-Contacts style: letter -> rows, '#' for non-letters."""
    groups = {}
    for c in sorted(contacts, key=lambda c: (c['name'] or '').upper()):
        first = (c['name'] or '#').strip()[:1].upper()
        key = first if first.isalpha() else '#'
        groups.setdefault(key, []).append(c)
    ordered = sorted(k for k in groups if k != '#')
    if '#' in groups:
        ordered.append('#')
    return [(k, groups[k]) for k in ordered]


# ============================================================
# v3: owned records, deals, notifications, reporting & offboarding
# ============================================================

def _json_value(value):
    """JSON-safe audit value without losing dates or Decimal-like numbers."""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    try:
        from decimal import Decimal
        if isinstance(value, Decimal):
            return float(value)
    except ImportError:
        pass
    return value


def audit_change(cur, ctx, entity_type, entity_id, entity_label, action,
                 field_name=None, old_value=None, new_value=None):
    """Append one admin-visible audit entry inside the caller's transaction."""
    cur.execute(
        """INSERT INTO crm_change_history
           (entity_type, entity_id, entity_label, action, field_name,
            old_value, new_value, actor_user_id, team_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (entity_type, entity_id, (entity_label or '')[:255] or None, action,
         field_name, Json(_json_value(old_value)) if old_value is not None else None,
         Json(_json_value(new_value)) if new_value is not None else None,
         ctx['user_id'], ctx['team_id']),
    )


def create_notification(cur, ctx, user_id, kind, title, body=None, url=None,
                        dedupe_key=None):
    if not user_id:
        return None
    preference_column = {
        'followup': 'followups',
        'assignment': 'assignments',
        'deal_change': 'deal_changes',
        'admin_activity': 'admin_activity',
    }.get(kind)
    if preference_column:
        cur.execute(
            f"""SELECT enabled, {preference_column} AS kind_enabled
                FROM crm_notification_preferences WHERE user_id = %s""",
            (user_id,),
        )
        preference = cur.fetchone()
        if preference and (not preference['enabled'] or not preference['kind_enabled']):
            return None
    cur.execute(
        """INSERT INTO crm_notifications
           (user_id, team_id, kind, title, body, url, dedupe_key)
           VALUES (%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (user_id, dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
           RETURNING id""",
        (user_id, ctx['team_id'], kind, title[:255], (body or '').strip() or None,
         (url or '').strip() or None, dedupe_key),
    )
    row = cur.fetchone()
    return row['id'] if row else None


def assign_contact(ctx, contact_id, assignee_id):
    if not ctx['is_admin'] and assignee_id != ctx['user_id']:
        raise PermissionError('Only an admin can transfer records')
    if not assignee_allowed(ctx, assignee_id):
        raise ValueError('That assignee is not on this team')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT c.name, c.assigned_to_id FROM crm_contacts c
                WHERE c.id = %(id)s AND {record_scope_sql(ctx, 'c')} FOR UPDATE""",
            {'id': contact_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        before = cur.fetchone()
        if before:
            cur.execute(
                "UPDATE crm_contacts SET assigned_to_id = %s, updated_at = NOW() WHERE id = %s",
                (assignee_id, contact_id),
            )
            if before['assigned_to_id'] != assignee_id:
                if before['assigned_to_id']:
                    cur.execute(
                        "DELETE FROM crm_notifications WHERE user_id = %s AND url = %s",
                        (before['assigned_to_id'], f'/crm/contacts/{contact_id}'),
                    )
                audit_change(cur, ctx, 'contact', contact_id, before['name'], 'assigned',
                             'assigned_to_id', before['assigned_to_id'], assignee_id)
                if assignee_id:
                    create_notification(cur, ctx, assignee_id, 'assignment',
                                        'A contact was assigned to you', before['name'],
                                        f'/crm/contacts/{contact_id}',
                                        f'contact-assigned:{contact_id}:{assignee_id}')
        conn.commit()
        return bool(before)
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def list_deals(ctx, *, stage=None, q=None, assignee_id=None,
               missing_next_step=False, limit=400):
    params = {'team_id': ctx['team_id'], 'user_id': ctx['user_id'], 'limit': limit}
    where = [record_scope_sql(ctx, 'd')]
    if stage in DEAL_STAGES:
        where.append('d.stage = %(stage)s')
        params['stage'] = stage
    if q:
        where.append("(d.name ILIKE %(q)s OR d.service_type ILIKE %(q)s OR b.address ILIKE %(q)s OR c.name ILIKE %(q)s)")
        params['q'] = f'%{q}%'
    if assignee_id and ctx['is_admin']:
        where.append('d.assigned_to_id = %(assignee_id)s')
        params['assignee_id'] = assignee_id
    if missing_next_step:
        where.append("d.stage NOT IN ('won','lost','client') AND d.next_step_at IS NULL")
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT d.*, b.address AS building_address, c.name AS contact_name,
                       c.do_not_contact AS contact_do_not_contact,
                       {_user_name_sql('au')} AS assigned_to_name,
                       {_user_name_sql('ab')} AS added_by_name,
                       (d.stage NOT IN ('won','lost','client') AND d.next_step_at IS NULL) AS needs_next_step
                FROM crm_deals d
                LEFT JOIN crm_buildings b ON b.id = d.building_id
                  AND {record_scope_sql(ctx, 'b')}
                LEFT JOIN crm_contacts c ON c.id = d.contact_id
                  AND {record_scope_sql(ctx, 'c')}
                LEFT JOIN users au ON au.id = d.assigned_to_id
                JOIN users ab ON ab.id = d.added_by_id
                WHERE {' AND '.join(where)}
                ORDER BY CASE WHEN d.next_step_at IS NULL THEN 1 ELSE 0 END,
                         d.next_step_at, d.updated_at DESC
                LIMIT %(limit)s""",
            params,
        )
        rows = [dict(r) for r in cur.fetchall()]
        if not ctx['is_admin']:
            for row in rows:
                if row.get('building_id') and not row.get('building_address'):
                    row['building_id'] = None
                if row.get('contact_id') and not row.get('contact_name'):
                    row['contact_id'] = None
        return rows
    finally:
        cur.close()
        conn.close()


def deal_stage_counts(ctx):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT d.stage, COUNT(*) AS n,
                       COALESCE(SUM(d.estimated_value), 0) AS value
                FROM crm_deals d WHERE {record_scope_sql(ctx, 'd')}
                GROUP BY d.stage""",
            {'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        counts = {r['stage']: {'n': r['n'], 'value': r['value']} for r in cur.fetchall()}
        stage_rows = list(counts.values())
        counts['all'] = {
            'n': sum(v['n'] for v in stage_rows),
            'value': sum((v['value'] for v in stage_rows), 0),
        }
        open_stages = [counts[s] for s in DEAL_STAGES
                       if s not in ('won', 'lost', 'client') and s in counts]
        counts['open'] = {
            'n': sum(v['n'] for v in open_stages),
            'value': sum((v['value'] for v in open_stages), 0),
        }
        return counts
    finally:
        cur.close()
        conn.close()


def get_deal(ctx, deal_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT d.*, b.address AS building_address, b.borough,
                       c.name AS contact_name, c.company AS contact_company,
                       {_user_name_sql('au')} AS assigned_to_name,
                       {_user_name_sql('ab')} AS added_by_name
                FROM crm_deals d
                LEFT JOIN crm_buildings b ON b.id = d.building_id
                  AND {record_scope_sql(ctx, 'b')}
                LEFT JOIN crm_contacts c ON c.id = d.contact_id
                  AND {record_scope_sql(ctx, 'c')}
                LEFT JOIN users au ON au.id = d.assigned_to_id
                JOIN users ab ON ab.id = d.added_by_id
                WHERE d.id = %(id)s AND {record_scope_sql(ctx, 'd')}""",
            {'id': deal_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        row = cur.fetchone()
        if not row:
            return None
        deal = dict(row)
        if not ctx['is_admin']:
            if deal.get('building_id') and not deal.get('building_address'):
                deal['building_id'] = None
            if deal.get('contact_id') and not deal.get('contact_name'):
                deal['contact_id'] = None
        cur.execute(
            f"""SELECT f.*, {_user_name_sql('u')} AS assigned_to_name,
                       b.address AS building_address, b.id AS b_id,
                       c.name AS contact_name, c.id AS c_id,
                       d.name AS deal_name, d.id AS d_id
                FROM crm_follow_ups f JOIN users u ON u.id = f.assigned_to_id
                LEFT JOIN crm_buildings b ON b.id = f.building_id
                  AND {record_scope_sql(ctx, 'b')}
                LEFT JOIN crm_contacts c ON c.id = f.contact_id
                  AND {record_scope_sql(ctx, 'c')}
                LEFT JOIN crm_deals d ON d.id = f.deal_id
                  AND {record_scope_sql(ctx, 'd')}
                WHERE f.deal_id = %(deal_id)s AND f.status = 'open'
                  AND (%(is_admin)s OR f.assigned_to_id = %(user_id)s)
                ORDER BY COALESCE(f.due_at, f.due_date::timestamp)""",
            {'deal_id': deal_id, 'is_admin': ctx['is_admin'],
             'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        deal['follow_ups'] = [dict(r) for r in cur.fetchall()]
        return deal
    finally:
        cur.close()
        conn.close()


def deals_for_entity(ctx, *, building_id=None, contact_id=None):
    rows = list_deals(ctx, limit=500)
    key, value = ('building_id', building_id) if building_id else ('contact_id', contact_id)
    return [r for r in rows if r.get(key) == value]


def create_deal(ctx, *, name, service_type=None, stage='prospect', estimated_value=None,
                expected_close_date=None, next_step=None, next_step_at=None,
                lost_reason=None, source='manual', building_id=None, contact_id=None,
                assigned_to_id=None):
    name = (name or '').strip()
    if not name:
        raise ValueError('Deal name is required')
    if stage not in DEAL_STAGES:
        raise ValueError('Invalid stage')
    if stage == 'lost' and not (lost_reason or '').strip():
        raise ValueError('Add a lost reason so the team can learn from it')
    if (building_id or contact_id) and not entity_in_team(
            ctx, building_id=building_id, contact_id=contact_id):
        raise PermissionError('Linked record not found')
    assignee = assigned_to_id or ctx['user_id']
    if not assignee_allowed(ctx, assignee):
        raise PermissionError('Only an admin can assign a deal to another rep')
    try:
        value = float(estimated_value) if estimated_value not in (None, '') else None
    except (TypeError, ValueError):
        raise ValueError('Estimated value must be a number')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO crm_deals
               (name, service_type, stage, estimated_value, expected_close_date,
                next_step, next_step_at, lost_reason, source, building_id,
                contact_id, assigned_to_id, added_by_id, team_id, won_at, lost_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       CASE WHEN %s IN ('won','client') THEN NOW() END,
                       CASE WHEN %s = 'lost' THEN NOW() END)
               RETURNING id""",
            (name[:255], (service_type or '').strip()[:120] or None, stage, value,
             expected_close_date, (next_step or '').strip()[:255] or None,
             next_step_at, (lost_reason or '').strip() or None, (source or 'manual')[:40],
             building_id, contact_id, assignee, ctx['user_id'], ctx['team_id'], stage, stage),
        )
        deal_id = cur.fetchone()['id']
        audit_change(cur, ctx, 'deal', deal_id, name, 'created',
                     new_value={'stage': stage, 'estimated_value': value,
                                'assigned_to_id': assignee})
        if assignee != ctx['user_id']:
            create_notification(cur, ctx, assignee, 'assignment', 'A deal was assigned to you',
                                name, f'/crm/deals/{deal_id}',
                                f'deal-assigned:{deal_id}:{assignee}')
        conn.commit()
        return deal_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


DEAL_EDITABLE = ('name', 'service_type', 'stage', 'estimated_value',
                 'expected_close_date', 'next_step', 'next_step_at',
                 'lost_reason', 'source', 'building_id', 'contact_id',
                 'assigned_to_id')


def update_deal(ctx, deal_id, fields):
    if fields.get('building_id') and not entity_in_team(
            ctx, building_id=fields['building_id']):
        raise PermissionError('Linked building not found')
    if fields.get('contact_id') and not entity_in_team(
            ctx, contact_id=fields['contact_id']):
        raise PermissionError('Linked contact not found')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT d.* FROM crm_deals d WHERE d.id = %(id)s AND {record_scope_sql(ctx, 'd')} FOR UPDATE",
            {'id': deal_id, 'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        before = cur.fetchone()
        if not before:
            return False
        stage = fields.get('stage', before['stage'])
        if stage not in DEAL_STAGES:
            raise ValueError('Invalid stage')
        lost_reason = fields.get('lost_reason', before['lost_reason'])
        if stage == 'lost' and not (lost_reason or '').strip():
            raise ValueError('Add a lost reason so the team can learn from it')
        assignee = fields.get('assigned_to_id', before['assigned_to_id'])
        if not assignee_allowed(ctx, assignee):
            raise PermissionError('Only an admin can transfer a deal')
        if not ctx['is_admin'] and assignee != before['assigned_to_id']:
            raise PermissionError('Only an admin can transfer a deal')
        sets, values = [], []
        for field in DEAL_EDITABLE:
            if field not in fields:
                continue
            value = fields[field]
            if field == 'estimated_value':
                value = float(value) if value not in (None, '') else None
            if isinstance(value, str):
                value = value.strip() or None
            sets.append(f'{field} = %s')
            values.append(value)
        if not sets:
            return True
        if 'next_step_at' in fields:
            sets.append('next_step_notified_at = NULL')
        sets.extend([
            "won_at = CASE WHEN %s IN ('won','client') AND stage NOT IN ('won','client') THEN NOW() ELSE won_at END",
            "lost_at = CASE WHEN %s = 'lost' AND stage <> 'lost' THEN NOW() ELSE lost_at END",
            'updated_at = NOW()',
        ])
        values.extend([stage, stage, deal_id])
        cur.execute(f"UPDATE crm_deals SET {', '.join(sets)} WHERE id = %s", values)
        for field in DEAL_EDITABLE:
            if field in fields and _json_value(before.get(field)) != _json_value(fields[field]):
                action = 'stage_changed' if field == 'stage' else ('assigned' if field == 'assigned_to_id' else 'updated')
                audit_change(cur, ctx, 'deal', deal_id, before['name'], action, field,
                             before.get(field), fields[field])
        if assignee and assignee != before['assigned_to_id']:
            if before['assigned_to_id']:
                cur.execute(
                    "DELETE FROM crm_notifications WHERE user_id = %s AND url = %s",
                    (before['assigned_to_id'], f'/crm/deals/{deal_id}'),
                )
            create_notification(cur, ctx, assignee, 'assignment', 'A deal was assigned to you',
                                fields.get('name') or before['name'], f'/crm/deals/{deal_id}',
                                f'deal-assigned:{deal_id}:{assignee}')
        elif assignee != before['assigned_to_id'] and before['assigned_to_id']:
            cur.execute(
                "DELETE FROM crm_notifications WHERE user_id = %s AND url = %s",
                (before['assigned_to_id'], f'/crm/deals/{deal_id}'),
            )
        elif ('stage' in fields and fields['stage'] != before['stage']
              and assignee and assignee != ctx['user_id']):
            create_notification(
                cur, ctx, assignee, 'deal_change',
                f'Deal moved to {STAGE_LABELS[fields["stage"]]}',
                fields.get('name') or before['name'], f'/crm/deals/{deal_id}',
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def delete_deal(ctx, deal_id):
    if not ctx['is_admin']:
        raise PermissionError('Admin access required')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT name FROM crm_deals WHERE id = %s AND team_id = %s", (deal_id, ctx['team_id']))
        row = cur.fetchone()
        if not row:
            return False
        audit_change(cur, ctx, 'deal', deal_id, row['name'], 'deleted')
        cur.execute("DELETE FROM crm_deals WHERE id = %s", (deal_id,))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def records_missing_next_step(ctx, limit=50):
    """Active deals and buildings with no dated next action."""
    conn = get_db_connection()
    cur = conn.cursor()
    params = {'team_id': ctx['team_id'], 'user_id': ctx['user_id'], 'limit': limit}
    try:
        cur.execute(
            f"""SELECT 'deal' AS kind, d.id, d.name AS label, d.stage,
                       b.address AS sub, d.updated_at AS age_at
                FROM crm_deals d LEFT JOIN crm_buildings b ON b.id = d.building_id
                  AND {record_scope_sql(ctx, 'b')}
                WHERE {record_scope_sql(ctx, 'd')}
                  AND d.stage NOT IN ('won','lost','client') AND d.next_step_at IS NULL
                  AND NOT EXISTS (SELECT 1 FROM crm_follow_ups f
                                  WHERE f.deal_id = d.id AND f.status = 'open')
                UNION ALL
                SELECT 'building', b.id, b.address, b.stage, b.borough, b.updated_at
                FROM crm_buildings b
                WHERE {record_scope_sql(ctx, 'b')}
                  AND b.stage NOT IN ('won','lost','client')
                  AND NOT EXISTS (SELECT 1 FROM crm_follow_ups f
                                  WHERE f.building_id = b.id AND f.status = 'open')
                ORDER BY age_at LIMIT %(limit)s""",
            params,
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def due_deal_steps(ctx, limit=50):
    """Active deal actions due by the end of the current NY day."""
    conn = get_db_connection()
    cur = conn.cursor()
    params = {'team_id': ctx['team_id'], 'user_id': ctx['user_id'],
              'tomorrow': ny_day_start_utc(1), 'limit': limit}
    try:
        cur.execute(
            f"""SELECT d.id, d.name, d.next_step, d.next_step_at, d.stage,
                       b.id AS building_id, b.address AS building_address,
                       c.id AS contact_id, c.name AS contact_name,
                       c.do_not_contact
                FROM crm_deals d
                LEFT JOIN crm_buildings b ON b.id = d.building_id
                  AND {record_scope_sql(ctx, 'b')}
                LEFT JOIN crm_contacts c ON c.id = d.contact_id
                  AND {record_scope_sql(ctx, 'c')}
                WHERE {record_scope_sql(ctx, 'd')}
                  AND d.stage NOT IN ('won','lost','client')
                  AND d.next_step_at IS NOT NULL
                  AND d.next_step_at < %(tomorrow)s
                ORDER BY d.next_step_at, d.id LIMIT %(limit)s""",
            params,
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def notification_inbox(ctx, limit=60):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT * FROM crm_notifications WHERE user_id = %s
               ORDER BY created_at DESC LIMIT %s""",
            (ctx['user_id'], limit),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def unread_notification_count(ctx):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) AS n FROM crm_notifications WHERE user_id = %s AND read_at IS NULL",
                    (ctx['user_id'],))
        return cur.fetchone()['n']
    finally:
        cur.close()
        conn.close()


def mark_notifications_read(ctx, notification_id=None):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if notification_id:
            cur.execute("UPDATE crm_notifications SET read_at = NOW() WHERE id = %s AND user_id = %s",
                        (notification_id, ctx['user_id']))
        else:
            cur.execute("UPDATE crm_notifications SET read_at = NOW() WHERE user_id = %s AND read_at IS NULL",
                        (ctx['user_id'],))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def notification_preferences(ctx):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO crm_notification_preferences (user_id)
               VALUES (%s) ON CONFLICT (user_id) DO NOTHING""",
            (ctx['user_id'],),
        )
        cur.execute("SELECT * FROM crm_notification_preferences WHERE user_id = %s", (ctx['user_id'],))
        row = dict(cur.fetchone())
        cur.execute("SELECT COUNT(*) AS n FROM crm_push_subscriptions WHERE user_id = %s", (ctx['user_id'],))
        row['push_subscriptions'] = cur.fetchone()['n']
        conn.commit()
        return row
    finally:
        cur.close()
        conn.close()


def update_notification_preferences(ctx, fields):
    allowed = ('enabled', 'followups', 'assignments', 'deal_changes', 'admin_activity',
               'quiet_start', 'quiet_end', 'timezone', 'reminder_minutes')
    sets, values = [], []
    for key in allowed:
        if key in fields:
            sets.append(f'{key} = %s')
            values.append(fields[key])
    if not sets:
        return notification_preferences(ctx)
    notification_preferences(ctx)
    values.append(ctx['user_id'])
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"UPDATE crm_notification_preferences SET {', '.join(sets)}, updated_at = NOW() WHERE user_id = %s",
                    values)
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return notification_preferences(ctx)


def save_push_subscription(ctx, subscription, user_agent=None):
    endpoint = (subscription or {}).get('endpoint')
    keys = (subscription or {}).get('keys') or {}
    if not endpoint or not keys.get('p256dh') or not keys.get('auth'):
        raise ValueError('Invalid push subscription')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO crm_push_subscriptions
               (user_id, endpoint, p256dh, auth, user_agent)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (endpoint) DO UPDATE SET user_id = EXCLUDED.user_id,
                   p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth,
                   user_agent = EXCLUDED.user_agent, updated_at = NOW()""",
            (ctx['user_id'], endpoint, keys['p256dh'], keys['auth'], (user_agent or '')[:500]),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def remove_push_subscription(ctx, endpoint):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM crm_push_subscriptions WHERE user_id = %s AND endpoint = %s",
                    (ctx['user_id'], endpoint))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def push_public_key():
    return (os.getenv('CRM_VAPID_PUBLIC_KEY') or '').strip()


def dispatch_due_notifications():
    """Create due reminders, then deliver every queued Web Push notification.

    Safe to run every few minutes: follow-up notified_at and notification
    dedupe keys make the operation idempotent.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    created = 0
    try:
        cur.execute(
            """SELECT f.id, f.title, f.assigned_to_id, f.team_id, f.due_at,
                      b.address, c.name AS contact_name, d.id AS deal_id,
                      d.name AS deal_name,
                      COALESCE(p.enabled, TRUE) AS notifications_enabled,
                      COALESCE(p.followups, TRUE) AS followups_enabled
               FROM crm_follow_ups f
               JOIN users fu ON fu.id = f.assigned_to_id
               LEFT JOIN crm_notification_preferences p ON p.user_id = f.assigned_to_id
               LEFT JOIN crm_buildings b ON b.id = f.building_id
                 AND (b.assigned_to_id = f.assigned_to_id
                      OR f.assigned_to_id = f.team_id OR fu.is_admin)
               LEFT JOIN crm_contacts c ON c.id = f.contact_id
                 AND (c.assigned_to_id = f.assigned_to_id
                      OR f.assigned_to_id = f.team_id OR fu.is_admin)
               LEFT JOIN crm_deals d ON d.id = f.deal_id
                 AND (d.assigned_to_id = f.assigned_to_id
                      OR f.assigned_to_id = f.team_id OR fu.is_admin)
               WHERE f.status = 'open' AND f.notified_at IS NULL
                 AND (f.building_id IS NULL OR b.id IS NOT NULL)
                 AND (f.contact_id IS NULL OR c.id IS NOT NULL)
                 AND (f.deal_id IS NULL OR d.id IS NOT NULL)
                 AND COALESCE(f.due_at, f.due_date::timestamp) <= (NOW() AT TIME ZONE 'UTC')
                       + (COALESCE(f.reminder_minutes, p.reminder_minutes, 15) * INTERVAL '1 minute')
               FOR UPDATE OF f SKIP LOCKED"""
        )
        for f in cur.fetchall():
            if f['notifications_enabled'] and f['followups_enabled']:
                label = f['deal_name'] or f['contact_name'] or f['address'] or ''
                url = (f'/crm/deals/{f["deal_id"]}' if f['deal_id'] else '/crm/followups')
                nid = create_notification(
                    cur, {'team_id': f['team_id']}, f['assigned_to_id'], 'followup',
                    f['title'], f'Due soon{(" · " + label) if label else ""}', url,
                    f'followup-due:{f["id"]}:{f["due_at"]}',
                )
                created += bool(nid)
            cur.execute("UPDATE crm_follow_ups SET notified_at = NOW() WHERE id = %s", (f['id'],))
        cur.execute(
            """SELECT d.id, d.name, d.next_step, d.next_step_at,
                      d.assigned_to_id, d.team_id, b.address,
                      c.name AS contact_name,
                      COALESCE(p.enabled, TRUE) AS notifications_enabled,
                      COALESCE(p.followups, TRUE) AS followups_enabled,
                      COALESCE(p.reminder_minutes, 15) AS reminder_minutes
               FROM crm_deals d
               LEFT JOIN crm_notification_preferences p ON p.user_id = d.assigned_to_id
               LEFT JOIN crm_buildings b ON b.id = d.building_id
                 AND b.assigned_to_id = d.assigned_to_id
               LEFT JOIN crm_contacts c ON c.id = d.contact_id
                 AND c.assigned_to_id = d.assigned_to_id
               WHERE d.stage NOT IN ('won','lost','client')
                 AND d.assigned_to_id IS NOT NULL
                 AND d.next_step_at IS NOT NULL
                 AND d.next_step_notified_at IS NULL
                 AND d.next_step_at <= (NOW() AT TIME ZONE 'UTC')
                       + (COALESCE(p.reminder_minutes, 15) * INTERVAL '1 minute')
               FOR UPDATE OF d SKIP LOCKED"""
        )
        for deal in cur.fetchall():
            if deal['notifications_enabled'] and deal['followups_enabled']:
                label = deal['contact_name'] or deal['address'] or deal['name']
                nid = create_notification(
                    cur, {'team_id': deal['team_id']}, deal['assigned_to_id'],
                    'followup', deal['next_step'] or f'Next step for {deal["name"]}',
                    f'Deal next step due · {label}', f'/crm/deals/{deal["id"]}',
                    f'deal-next-step:{deal["id"]}:{deal["next_step_at"]}',
                )
                created += bool(nid)
            cur.execute(
                "UPDATE crm_deals SET next_step_notified_at = NOW() WHERE id = %s",
                (deal['id'],),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
    return {'created': created, **send_pending_push_notifications()}


def send_pending_push_notifications(limit=200):
    public_key = push_public_key()
    private_key = (os.getenv('CRM_VAPID_PRIVATE_KEY') or '').strip()
    if not public_key or not private_key:
        return {'pushed': 0, 'push_skipped': 'VAPID not configured'}
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return {'pushed': 0, 'push_skipped': 'pywebpush not installed'}
    conn = get_db_connection()
    cur = conn.cursor()
    pushed = 0
    try:
        cur.execute(
            """SELECT n.id, n.title, n.body, n.url, s.id AS subscription_id,
                      s.endpoint, s.p256dh, s.auth
               FROM crm_notifications n
               JOIN crm_push_subscriptions s ON s.user_id = n.user_id
               LEFT JOIN crm_notification_preferences p ON p.user_id = n.user_id
               WHERE n.pushed_at IS NULL AND n.read_at IS NULL
                 AND COALESCE(p.enabled, TRUE)
                 AND (n.kind <> 'followup' OR COALESCE(p.followups, TRUE))
                 AND (n.kind <> 'assignment' OR COALESCE(p.assignments, TRUE))
                 AND (n.kind <> 'deal_change' OR COALESCE(p.deal_changes, TRUE))
                 AND (n.kind <> 'admin_activity' OR COALESCE(p.admin_activity, FALSE))
               ORDER BY n.created_at LIMIT %s""",
            (limit,),
        )
        import json
        completed = set()
        for row in cur.fetchall():
            try:
                webpush(
                    subscription_info={
                        'endpoint': row['endpoint'],
                        'keys': {'p256dh': row['p256dh'], 'auth': row['auth']},
                    },
                    data=json.dumps({'title': row['title'], 'body': row['body'],
                                     'url': row['url'] or '/crm'}),
                    vapid_private_key=private_key,
                    vapid_claims={'sub': os.getenv('CRM_VAPID_SUBJECT', 'mailto:admin@example.com')},
                    ttl=86400,
                )
                pushed += 1
                completed.add(row['id'])
            except WebPushException as exc:
                if getattr(exc.response, 'status_code', None) in (404, 410):
                    cur.execute("DELETE FROM crm_push_subscriptions WHERE id = %s", (row['subscription_id'],))
        if completed:
            cur.execute("UPDATE crm_notifications SET pushed_at = NOW() WHERE id = ANY(%s)",
                        (list(completed),))
        conn.commit()
        return {'pushed': pushed}
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def list_change_history(ctx, *, entity_type=None, actor_user_id=None, limit=300):
    if not ctx['is_admin']:
        return []
    where = ['h.team_id = %(team_id)s']
    params = {'team_id': ctx['team_id'], 'limit': limit}
    if entity_type:
        where.append('h.entity_type = %(entity_type)s')
        params['entity_type'] = entity_type
    if actor_user_id:
        where.append('h.actor_user_id = %(actor)s')
        params['actor'] = actor_user_id
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT h.*, {_user_name_sql('u')} AS actor_name
                FROM crm_change_history h LEFT JOIN users u ON u.id = h.actor_user_id
                WHERE {' AND '.join(where)} ORDER BY h.created_at DESC LIMIT %(limit)s""",
            params,
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def admin_report(ctx, days=30):
    if not ctx['is_admin']:
        return {}
    days = max(1, min(int(days), 365))
    since = datetime.utcnow() - timedelta(days=days)
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT
                 COUNT(*) FILTER (WHERE type = 'contacted') AS touches,
                 COUNT(*) FILTER (WHERE type = 'contacted'
                                  AND outcome IN ('spoke','callback_requested','meeting_set')) AS conversations,
                 COUNT(*) FILTER (WHERE type = 'contacted' AND outcome = 'meeting_set') AS meetings,
                 COUNT(*) FILTER (WHERE type = 'visit') AS visits
               FROM crm_activity WHERE team_id = %s AND created_at >= %s""",
            (ctx['team_id'], since),
        )
        result = dict(cur.fetchone())
        cur.execute(
            """SELECT COUNT(*) FILTER (WHERE created_at >= %s) AS created,
                      COUNT(*) FILTER (WHERE stage IN ('won','client') AND won_at >= %s) AS won,
                      COUNT(*) FILTER (WHERE stage = 'lost' AND lost_at >= %s) AS lost,
                      COUNT(*) FILTER (WHERE stage = 'nurture') AS nurture,
                      COUNT(*) FILTER (WHERE stage NOT IN ('won','lost','client') AND next_step_at IS NULL) AS no_next_step,
                      COALESCE(SUM(estimated_value) FILTER (WHERE stage NOT IN ('won','lost','client')), 0) AS open_value,
                      COALESCE(SUM(estimated_value) FILTER (WHERE stage IN ('won','client') AND won_at >= %s), 0) AS won_value
               FROM crm_deals WHERE team_id = %s""",
            (since, since, since, since, ctx['team_id']),
        )
        result.update(dict(cur.fetchone()))
        cur.execute(
            """SELECT COUNT(*) FILTER (WHERE status = 'open' AND due_date < %s) AS overdue,
                      COUNT(*) FILTER (WHERE status = 'done' AND completed_at >= %s) AS completed
               FROM crm_follow_ups WHERE team_id = %s""",
            (ny_today(), since, ctx['team_id']),
        )
        result.update(dict(cur.fetchone()))
        result['days'] = days
        result['conversation_rate'] = round(100 * result['conversations'] / result['touches'], 1) if result['touches'] else 0
        result['win_rate'] = round(100 * result['won'] / (result['won'] + result['lost']), 1) if result['won'] + result['lost'] else 0
        return result
    finally:
        cur.close()
        conn.close()


def nurture_stats(ctx):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT d.assigned_to_id AS user_id, {_user_name_sql('u')} AS rep_name,
                       COUNT(*) AS deals,
                       COALESCE(SUM(d.estimated_value), 0) AS value,
                       COUNT(*) FILTER (WHERE d.next_step_at IS NULL) AS missing_next_step,
                       MIN(d.next_step_at) AS next_action
                FROM crm_deals d LEFT JOIN users u ON u.id = d.assigned_to_id
                WHERE {record_scope_sql(ctx, 'd')} AND d.stage = 'nurture'
                GROUP BY d.assigned_to_id, u.id ORDER BY deals DESC""",
            {'team_id': ctx['team_id'], 'user_id': ctx['user_id']},
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def offboard_rep(ctx, rep_id, transfer_to_id, revoke_access=True):
    """Atomically transfer live work, preserve authorship, then revoke access."""
    if not ctx['is_admin']:
        raise PermissionError('Admin access required')
    if not rep_id or not transfer_to_id:
        raise ValueError('Choose the rep leaving and an active teammate to receive the work')
    if rep_id == ctx['team_id'] or rep_id == transfer_to_id:
        raise ValueError('Choose a different active rep to receive the work')
    if not assignee_allowed(ctx, transfer_to_id):
        raise ValueError('Transfer destination is not an active team member')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT s.id AS sponsorship_id, s.member_user_id,
                       COALESCE(s.display_name, split_part(s.member_email, '@', 1)) AS name
                FROM account_sponsorships s
                WHERE s.sponsor_user_id = %s AND s.member_user_id = %s AND s.status = 'active'
                FOR UPDATE""",
            (ctx['team_id'], rep_id),
        )
        rep = cur.fetchone()
        if not rep:
            raise ValueError('That rep is not active on this team')
        counts = {}
        for table in ('crm_buildings', 'crm_contacts', 'crm_deals', 'crm_follow_ups', 'crm_lists'):
            cur.execute(f"""UPDATE {table} SET assigned_to_id = %s
                            WHERE (team_id = %s OR team_id IS NULL)
                              AND assigned_to_id = %s""",
                        (transfer_to_id, ctx['team_id'], rep_id))
            counts[table.removeprefix('crm_')] = cur.rowcount
        cur.execute("""UPDATE crm_lists SET owner_id = %s
                       WHERE (team_id = %s OR team_id IS NULL) AND owner_id = %s""",
                    (transfer_to_id, ctx['team_id'], rep_id))
        counts['list_ownership'] = cur.rowcount
        cur.execute(
            """UPDATE crm_saved_filters SET owner_id = %s, updated_at = NOW()
               WHERE (team_id = %s OR team_id IS NULL) AND owner_id = %s""",
            (transfer_to_id, ctx['team_id'], rep_id),
        )
        counts['saved_searches'] = cur.rowcount
        if revoke_access:
            cur.execute(
                """UPDATE account_sponsorships SET status = 'revoked', revoked_at = NOW(),
                          invite_token_hash = NULL, invite_expires_at = NULL, updated_at = NOW()
                   WHERE id = %s""",
                (rep['sponsorship_id'],),
            )
            cur.execute("DELETE FROM user_sessions WHERE user_id = %s", (rep_id,))
            cur.execute("DELETE FROM crm_push_subscriptions WHERE user_id = %s", (rep_id,))
            cur.execute(
                """UPDATE crm_notifications SET read_at = COALESCE(read_at, NOW()),
                          pushed_at = COALESCE(pushed_at, NOW())
                   WHERE user_id = %s""",
                (rep_id,),
            )
        audit_change(cur, ctx, 'team_member', rep_id, rep['name'], 'offboarded',
                     new_value={'transferred_to_id': transfer_to_id,
                                'access_revoked': bool(revoke_access), 'counts': counts})
        create_notification(cur, ctx, transfer_to_id, 'assignment',
                            f'{rep["name"]}’s work was transferred to you',
                            f'{sum(counts.values())} CRM records reassigned', '/crm',
                            f'offboard:{rep_id}:{transfer_to_id}')
        conn.commit()
        return counts
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
