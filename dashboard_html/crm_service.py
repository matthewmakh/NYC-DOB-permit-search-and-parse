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
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor, Json


NY_TZ = ZoneInfo('America/New_York')

STAGES = ['prospect', 'contacted', 'interested', 'quoted', 'won', 'lost', 'client']
STAGE_LABELS = {
    'prospect': 'Prospect', 'contacted': 'Contacted', 'interested': 'Interested',
    'quoted': 'Quoted', 'won': 'Won', 'lost': 'Lost', 'client': 'Client',
}
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
]


def init_crm_tables():
    """Idempotently install the CRM schema on worker startup."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        for statement in CRM_SCHEMA_STATEMENTS:
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


def entity_in_team(ctx, *, building_id=None, contact_id=None):
    """True when every named entity is visible to this team. Guards the
    write APIs against cross-team ids arriving in a request body."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if building_id:
            cur.execute(
                f"SELECT 1 FROM crm_buildings WHERE id = %s AND {_TEAM_SCOPE_SQL}",
                (building_id, ctx['team_id']),
            )
            if not cur.fetchone():
                return False
        if contact_id:
            cur.execute(
                f"SELECT 1 FROM crm_contacts WHERE id = %s AND {_TEAM_SCOPE_SQL}",
                (contact_id, ctx['team_id']),
            )
            if not cur.fetchone():
                return False
        return bool(building_id or contact_id)
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
            f"SELECT id FROM crm_buildings WHERE bbl = %s AND {_TEAM_SCOPE_SQL}",
            (str(bbl), ctx['team_id']),
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
                f"SELECT id FROM crm_buildings WHERE bbl = %s AND {_TEAM_SCOPE_SQL}",
                (str(bbl), ctx['team_id']),
            )
            existing = cur.fetchone()
            if existing:
                return existing['id'], False
        cur.execute(
            """INSERT INTO crm_buildings
               (bbl, address, borough, zip_code, neighborhood, unit_count,
                year_built, num_floors, building_class, owner_name, source,
                added_by_id, team_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (str(bbl) if bbl else None, address.strip(), borough, zip_code,
             neighborhood, unit_count, year_built, num_floors, building_class,
             owner_name, source, ctx['user_id'], ctx['team_id']),
        )
        building_id = cur.fetchone()['id']
        cur.execute(
            """INSERT INTO crm_activity (type, note, building_id, user_id, team_id)
               VALUES ('system', %s, %s, %s, %s)""",
            ('Added to CRM' + (' from the permit database' if source == 'permit' else ''),
             building_id, ctx['user_id'], ctx['team_id']),
        )
        conn.commit()
        return building_id, True
    except psycopg2.errors.UniqueViolation:
        # Two reps importing the same BBL at once: fall back to the winner.
        conn.rollback()
        cur.execute(
            f"SELECT id FROM crm_buildings WHERE bbl = %s AND {_TEAM_SCOPE_SQL}",
            (str(bbl), ctx['team_id']),
        )
        row = cur.fetchone()
        if row:
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
        where = ["(b.team_id = %(team_id)s OR b.team_id IS NULL)"]
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
                   {_user_name_sql('au')} AS assigned_to_name
            FROM crm_buildings b
            LEFT JOIN crm_stars s
                   ON s.building_id = b.id AND s.user_id = %(user_id)s
            LEFT JOIN users au ON au.id = b.assigned_to_id
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
                WHERE (b.team_id = %s OR b.team_id IS NULL) GROUP BY stage""",
            (ctx['team_id'],),
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
            LEFT JOIN crm_stars s ON s.building_id = b.id AND s.user_id = %s
            LEFT JOIN users au ON au.id = b.assigned_to_id
            JOIN users ab ON ab.id = b.added_by_id
            WHERE b.id = %s AND (b.team_id = %s OR b.team_id IS NULL)
            """,
            (ctx['user_id'], building_id, ctx['team_id']),
        )
        building = cur.fetchone()
        if not building:
            return None
        building = dict(building)

        cur.execute(
            f"""
            SELECT su.id AS user_id, {_user_name_sql('su')} AS name
            FROM crm_stars st JOIN users su ON su.id = st.user_id
            WHERE st.building_id = %s ORDER BY st.created_at
            """,
            (building_id,),
        )
        building['starred_by'] = [dict(r) for r in cur.fetchall()]

        cur.execute(
            f"""
            SELECT c.*, bc.role AS building_role,
                   (st.id IS NOT NULL) AS starred
            FROM crm_building_contacts bc
            JOIN crm_contacts c ON c.id = bc.contact_id
            LEFT JOIN crm_stars st ON st.contact_id = c.id AND st.user_id = %s
            WHERE bc.building_id = %s
            ORDER BY CASE bc.role WHEN 'owner' THEN 0 WHEN 'property_manager' THEN 1
                     WHEN 'super' THEN 2 ELSE 3 END, c.name
            """,
            (ctx['user_id'], building_id),
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
                WHERE f.building_id = %s AND f.status = 'open'
                ORDER BY f.due_date""",
            (building_id,),
        )
        building['follow_ups'] = [dict(r) for r in cur.fetchall()]

        cur.execute(
            """SELECT l.id, l.name, l.color FROM crm_list_items li
               JOIN crm_lists l ON l.id = li.list_id
               WHERE li.building_id = %s ORDER BY l.name""",
            (building_id,),
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
            """UPDATE crm_buildings SET stage = %s, updated_at = NOW()
               WHERE id = %s AND (team_id = %s OR team_id IS NULL)
                 AND stage <> %s
               RETURNING id""",
            (stage, building_id, ctx['team_id'], stage),
        )
        if cur.fetchone():
            cur.execute(
                """INSERT INTO crm_activity (type, note, building_id, user_id, team_id)
                   VALUES ('stage_change', %s, %s, %s, %s)""",
                (f'Stage set to {STAGE_LABELS[stage]}', building_id,
                 ctx['user_id'], ctx['team_id']),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def assign_building(ctx, building_id, assignee_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE crm_buildings SET assigned_to_id = %s, updated_at = NOW()
               WHERE id = %s AND (team_id = %s OR team_id IS NULL)""",
            (assignee_id or None, building_id, ctx['team_id']),
        )
        conn.commit()
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
    """Street View / Maps payload for a CRM building (coords from permits)."""
    import streetview
    coords = None
    if bbl:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            coords = streetview.lookup_latlng(cur, bbl)
        except Exception:
            coords = None
        finally:
            cur.close()
            conn.close()
    lat, lng = coords if coords else (None, None)
    return streetview.payload(address, lat, lng, borough)


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
            """SELECT DISTINCT c.id, c.name, c.company, p.extension
               FROM crm_phones p JOIN crm_contacts c ON c.id = p.contact_id
               WHERE p.digits = %s AND (c.team_id = %s OR c.team_id IS NULL)
                 AND (%s IS NULL OR p.extension IS NULL OR p.extension = %s)
               ORDER BY c.name""",
            (digits, ctx['team_id'], extension, extension),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def create_contact(ctx, *, name, title=None, company=None, email=None,
                   source='manual', source_detail=None, building_id=None,
                   building_role='other', phone=None, phone_label=None,
                   phone_extension=None):
    """Create a contact, optionally with a first phone and a building link."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO crm_contacts
               (name, title, company, email, source, source_detail, added_by_id, team_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (name.strip(), title or None, company or None, email or None,
             source, source_detail or None, ctx['user_id'], ctx['team_id']),
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
            """UPDATE crm_phones p SET status = %s
               FROM crm_contacts c
               WHERE p.id = %s AND c.id = p.contact_id
                 AND (c.team_id = %s OR c.team_id IS NULL)""",
            (status, phone_id, ctx['team_id']),
        )
        conn.commit()
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
            """UPDATE crm_contacts SET do_not_contact = %s, updated_at = NOW()
               WHERE id = %s AND (team_id = %s OR team_id IS NULL)""",
            (bool(value), contact_id, ctx['team_id']),
        )
        conn.commit()
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
        where = ["(c.team_id = %(team_id)s OR c.team_id IS NULL)"]
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
                   (SELECT p.number || COALESCE(' ext. ' || p.extension, '') FROM crm_phones p
                    WHERE p.contact_id = c.id AND p.status = 'good'
                    ORDER BY p.is_primary DESC, p.created_at LIMIT 1) AS primary_phone,
                   (SELECT COUNT(*) FROM crm_building_contacts bc
                    WHERE bc.contact_id = c.id) AS building_count
            FROM crm_contacts c
            LEFT JOIN crm_stars st ON st.contact_id = c.id AND st.user_id = %(user_id)s
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
                   {_user_name_sql('ab')} AS added_by_name
            FROM crm_contacts c
            LEFT JOIN crm_stars st ON st.contact_id = c.id AND st.user_id = %s
            JOIN users ab ON ab.id = c.added_by_id
            WHERE c.id = %s AND (c.team_id = %s OR c.team_id IS NULL)
            """,
            (ctx['user_id'], contact_id, ctx['team_id']),
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
            """SELECT b.id, b.address, b.borough, b.stage, b.last_contacted_at,
                      bc.role AS building_role
               FROM crm_building_contacts bc
               JOIN crm_buildings b ON b.id = bc.building_id
               WHERE bc.contact_id = %s ORDER BY b.address""",
            (contact_id,),
        )
        contact['buildings'] = [dict(r) for r in cur.fetchall()]
        cur.execute(
            f"""SELECT f.*, {_user_name_sql('fu')} AS assigned_to_name
                FROM crm_follow_ups f JOIN users fu ON fu.id = f.assigned_to_id
                WHERE f.contact_id = %s AND f.status = 'open' ORDER BY f.due_date""",
            (contact_id,),
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
            """INSERT INTO crm_building_contacts (building_id, contact_id, role)
               VALUES (%s,%s,%s) ON CONFLICT (building_id, contact_id) DO NOTHING""",
            (building_id, contact_id, role),
        )
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

def log_contacted(ctx, *, building_id=None, contact_id=None, method='call',
                  outcome=None, note=None, phone_digits=None):
    """The Contacted button. One press = one permanent, attributed event.

    Side effects, all in one transaction: rollups on the building/contact,
    the prospect->contacted auto stage bump, and a wrong-number outcome
    marking the dialed phone bad.
    """
    if method not in CONTACT_METHODS:
        method = 'other'
    if outcome is not None and outcome not in CONTACT_OUTCOMES:
        outcome = None
    if not building_id and not contact_id:
        raise ValueError('contacted needs a building or a contact')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO crm_activity
               (type, method, outcome, note, phone_digits, building_id, contact_id,
                user_id, team_id)
               VALUES ('contacted', %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, created_at""",
            (method, outcome, (note or '').strip() or None, phone_digits or None,
             building_id, contact_id, ctx['user_id'], ctx['team_id']),
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
            """UPDATE crm_activity SET is_pinned = NOT is_pinned
               WHERE id = %s AND (team_id = %s OR team_id IS NULL)
               RETURNING is_pinned""",
            (activity_id, ctx['team_id']),
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
            age = datetime.utcnow() - activity['created_at']
            if age > timedelta(minutes=ACTIVITY_UNDO_MINUTES):
                return False, f'The {ACTIVITY_UNDO_MINUTES}-minute undo window has passed — ask an admin'
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


def get_timeline(ctx, *, building_id=None, contact_id=None, limit=100):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if building_id:
            scope_sql, scope_val = 'a.building_id = %s', building_id
        else:
            scope_sql, scope_val = 'a.contact_id = %s', contact_id
        cur.execute(
            f"""
            SELECT a.*, {_user_name_sql('u')} AS user_name,
                   c.name AS contact_name, b.address AS building_address
            FROM crm_activity a
            JOIN users u ON u.id = a.user_id
            LEFT JOIN crm_contacts c ON c.id = a.contact_id
            LEFT JOIN crm_buildings b ON b.id = a.building_id
            WHERE {scope_sql} AND (a.team_id = %s OR a.team_id IS NULL)
            ORDER BY a.created_at DESC
            LIMIT %s
            """,
            (scope_val, ctx['team_id'], limit),
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
        params = {'team_id': ctx['team_id'], 'limit': limit}
        if user_id:
            where.append("a.user_id = %(filter_user)s")
            params['filter_user'] = user_id
        if types:
            where.append("a.type = ANY(%(types)s)")
            params['types'] = list(types)
        cur.execute(
            f"""
            SELECT a.*, {_user_name_sql('u')} AS user_name,
                   c.name AS contact_name, b.address AS building_address, b.id AS b_id
            FROM crm_activity a
            JOIN users u ON u.id = a.user_id
            LEFT JOIN crm_contacts c ON c.id = a.contact_id
            LEFT JOIN crm_buildings b ON b.id = a.building_id
            WHERE {' AND '.join(where)}
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
        params = {'team_id': ctx['team_id']}
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
                   c.id AS contact_id, c.name AS contact_name, c.company,
                   c.last_contacted_at AS c_last_contacted
            FROM crm_stars s
            JOIN users su ON su.id = s.user_id
            LEFT JOIN crm_buildings b ON b.id = s.building_id
            LEFT JOIN crm_contacts c ON c.id = s.contact_id
            WHERE {user_sql}
              AND COALESCE(b.team_id, c.team_id, %(team_id)s) = %(team_id)s
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

def create_follow_up(ctx, *, title, due_date, note=None, building_id=None,
                     contact_id=None, assigned_to_id=None):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO crm_follow_ups
               (title, note, due_date, building_id, contact_id,
                assigned_to_id, created_by_id, team_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            ((title or 'Follow up').strip()[:255], (note or '').strip() or None,
             due_date, building_id, contact_id,
             assigned_to_id or ctx['user_id'], ctx['user_id'], ctx['team_id']),
        )
        follow_up_id = cur.fetchone()['id']
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
            """UPDATE crm_follow_ups
               SET status = %s,
                   completed_at = CASE WHEN %s IN ('done','skipped') THEN NOW() ELSE NULL END
               WHERE id = %s AND (team_id = %s OR team_id IS NULL)""",
            (status, status, follow_up_id, ctx['team_id']),
        )
        conn.commit()
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
            """UPDATE crm_follow_ups
               SET due_date = GREATEST(due_date, %s) + %s, status = 'open', completed_at = NULL
               WHERE id = %s AND (team_id = %s OR team_id IS NULL)""",
            (ny_today(), timedelta(days=days), follow_up_id, ctx['team_id']),
        )
        conn.commit()
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
        params = {'team_id': ctx['team_id'], 'today': today}
        scope = "f.team_id = %(team_id)s" if whole_team else "f.assigned_to_id = %(assignee)s"
        if not whole_team:
            params['assignee'] = ctx['user_id']
        cur.execute(
            f"""
            SELECT f.*, {_user_name_sql('fu')} AS assigned_to_name,
                   b.address AS building_address, b.id AS b_id,
                   c.name AS contact_name, c.id AS c_id
            FROM crm_follow_ups f
            JOIN users fu ON fu.id = f.assigned_to_id
            LEFT JOIN crm_buildings b ON b.id = f.building_id
            LEFT JOIN crm_contacts c ON c.id = f.contact_id
            WHERE {scope} AND f.status = 'open'
            ORDER BY f.due_date, f.id
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
                       c.name AS contact_name, c.id AS c_id
                FROM crm_follow_ups f
                JOIN users fu ON fu.id = f.assigned_to_id
                LEFT JOIN crm_buildings b ON b.id = f.building_id
                LEFT JOIN crm_contacts c ON c.id = f.contact_id
                WHERE {scope} AND f.status IN ('done','skipped')
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
                   (SELECT COUNT(*) FROM crm_list_items li WHERE li.list_id = l.id) AS item_count
            FROM crm_lists l
            JOIN users ou ON ou.id = l.owner_id
            LEFT JOIN users au ON au.id = l.assigned_to_id
            WHERE (l.team_id = %s OR l.team_id IS NULL)
            ORDER BY l.updated_at DESC
            """,
            (ctx['team_id'],),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def create_list(ctx, *, name, description=None, color=None, assigned_to_id=None):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO crm_lists (name, description, color, owner_id, assigned_to_id, team_id)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
            (name.strip()[:120], (description or '').strip() or None, color or None,
             ctx['user_id'], assigned_to_id or None, ctx['team_id']),
        )
        list_id = cur.fetchone()['id']
        conn.commit()
        return list_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def update_list(ctx, list_id, *, name=None, description=None, assigned_to_id='__keep__'):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        sets, params = ['updated_at = NOW()'], []
        if name is not None:
            sets.append('name = %s')
            params.append(name.strip()[:120])
        if description is not None:
            sets.append('description = %s')
            params.append(description.strip() or None)
        if assigned_to_id != '__keep__':
            sets.append('assigned_to_id = %s')
            params.append(assigned_to_id or None)
        params.extend([list_id, ctx['team_id']])
        cur.execute(
            f"""UPDATE crm_lists SET {', '.join(sets)}
                WHERE id = %s AND (team_id = %s OR team_id IS NULL)""",
            params,
        )
        conn.commit()
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
            "DELETE FROM crm_lists WHERE id = %s AND (team_id = %s OR team_id IS NULL)",
            (list_id, ctx['team_id']),
        )
        conn.commit()
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
            WHERE l.id = %s AND (l.team_id = %s OR l.team_id IS NULL)
            """,
            (list_id, ctx['team_id']),
        )
        lst = cur.fetchone()
        if not lst:
            return None
        lst = dict(lst)
        cur.execute(
            """
            SELECT li.id AS item_id, li.note AS item_note, li.created_at AS added_at,
                   b.id AS building_id, b.address, b.borough, b.stage,
                   b.last_contacted_at AS b_last_contacted, b.contact_count,
                   c.id AS contact_id, c.name AS contact_name, c.company,
                   c.last_contacted_at AS c_last_contacted,
                   (SELECT p.number || COALESCE(' ext. ' || p.extension, '') FROM crm_phones p
                    WHERE p.contact_id = c.id AND p.status = 'good'
                    ORDER BY p.is_primary DESC, p.created_at LIMIT 1) AS contact_phone
            FROM crm_list_items li
            LEFT JOIN crm_buildings b ON b.id = li.building_id
            LEFT JOIN crm_contacts c ON c.id = li.contact_id
            WHERE li.list_id = %s
            ORDER BY li.sort_order, li.id
            """,
            (list_id,),
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
        col = 'building_id' if building_id else 'contact_id'
        cur.execute(
            f"""INSERT INTO crm_list_items (list_id, {col}, note, added_by_id, sort_order)
                SELECT %s, %s, %s, %s,
                       COALESCE((SELECT MAX(sort_order) FROM crm_list_items WHERE list_id = %s), 0) + 1
                ON CONFLICT DO NOTHING""",
            (list_id, building_id or contact_id, (note or '').strip() or None,
             ctx['user_id'], list_id),
        )
        cur.execute("UPDATE crm_lists SET updated_at = NOW() WHERE id = %s", (list_id,))
        conn.commit()
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
            """DELETE FROM crm_list_items li
               USING crm_lists l
               WHERE li.id = %s AND l.id = li.list_id
                 AND (l.team_id = %s OR l.team_id IS NULL)""",
            (item_id, ctx['team_id']),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def list_saved_filters(ctx):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""SELECT sf.*, {_user_name_sql('ou')} AS owner_name
                FROM crm_saved_filters sf JOIN users ou ON ou.id = sf.owner_id
                WHERE (sf.team_id = %s OR sf.team_id IS NULL)
                ORDER BY sf.created_at DESC""",
            (ctx['team_id'],),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def save_filter(ctx, *, name, querystring):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO crm_saved_filters (name, querystring, owner_id, team_id)
               VALUES (%s,%s,%s,%s) RETURNING id""",
            (name.strip()[:120], querystring.strip().lstrip('?'),
             ctx['user_id'], ctx['team_id']),
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
            """
            SELECT b.id, b.address, b.borough, b.stage, b.last_contacted_at, b.created_at,
                   CASE WHEN b.stage IN ('contacted','interested','quoted')
                             AND COALESCE(b.last_contacted_at, b.created_at) < %s
                        THEN 'stale' ELSE 'never_contacted' END AS reason
            FROM crm_buildings b
            WHERE (b.team_id = %s OR b.team_id IS NULL)
              AND (
                (b.stage IN ('contacted','interested','quoted')
                 AND COALESCE(b.last_contacted_at, b.created_at) < %s)
                OR
                (b.stage = 'prospect' AND b.contact_count = 0 AND b.created_at < %s)
              )
            ORDER BY COALESCE(b.last_contacted_at, b.created_at)
            LIMIT %s
            """,
            (now - timedelta(days=stale_days), ctx['team_id'],
             now - timedelta(days=stale_days), now - timedelta(days=untouched_days),
             limit),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


def rep_performance(ctx):
    """Per-rep aggregates for the admin Team screen."""
    roster = get_team_roster(ctx['team_id'])
    if not roster:
        return []
    user_ids = [r['id'] for r in roster]
    today_start = ny_day_start_utc(0)
    week_start = ny_day_start_utc(-6)
    today = ny_today()
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT user_id,
                 COUNT(*) FILTER (WHERE type = 'contacted' AND created_at >= %s) AS contacted_today,
                 COUNT(*) FILTER (WHERE type = 'contacted' AND created_at >= %s) AS contacted_7d,
                 COUNT(*) FILTER (WHERE type = 'visit' AND created_at >= %s) AS visits_7d,
                 COUNT(*) FILTER (WHERE type = 'note' AND created_at >= %s) AS notes_7d
               FROM crm_activity WHERE user_id = ANY(%s)
               GROUP BY user_id""",
            (today_start, week_start, week_start, week_start, user_ids),
        )
        activity = {r['user_id']: dict(r) for r in cur.fetchall()}
        cur.execute(
            """SELECT assigned_to_id AS user_id,
                 COUNT(*) FILTER (WHERE status = 'open') AS followups_open,
                 COUNT(*) FILTER (WHERE status = 'open' AND due_date < %s) AS followups_overdue,
                 COUNT(*) FILTER (WHERE status = 'done' AND completed_at >= %s) AS followups_done_7d
               FROM crm_follow_ups WHERE assigned_to_id = ANY(%s)
               GROUP BY assigned_to_id""",
            (today, week_start, user_ids),
        )
        followups = {r['user_id']: dict(r) for r in cur.fetchall()}
        cur.execute(
            """SELECT user_id, COUNT(*) AS views_today
               FROM crm_view_events WHERE user_id = ANY(%s) AND created_at >= %s
               GROUP BY user_id""",
            (user_ids, today_start),
        )
        views = {r['user_id']: dict(r) for r in cur.fetchall()}
        out = []
        for rep in roster:
            row = dict(rep)
            row.update({'contacted_today': 0, 'contacted_7d': 0, 'visits_7d': 0,
                        'notes_7d': 0, 'followups_open': 0, 'followups_overdue': 0,
                        'followups_done_7d': 0, 'views_today': 0})
            row.update(activity.get(rep['id'], {}))
            row.update(followups.get(rep['id'], {}))
            row.update(views.get(rep['id'], {}))
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


# ============================================================
# v2: search, editing, merge, bulk, focus queue, reports
# ============================================================

def global_search(ctx, q, limit=6):
    """⌘K search across buildings, contacts (name/company/phone digits), lists."""
    q = (q or '').strip()
    if len(q) < 2:
        return {'buildings': [], 'contacts': [], 'lists': []}
    like = f'%{q}%'
    digits = normalize_phone_digits(q)
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT id, address, borough, stage, last_contacted_at
               FROM crm_buildings
               WHERE (team_id = %s OR team_id IS NULL)
                 AND (address ILIKE %s OR owner_name ILIKE %s OR bbl = %s)
               ORDER BY last_contacted_at DESC NULLS LAST, address LIMIT %s""",
            (ctx['team_id'], like, like, q, limit),
        )
        buildings = [dict(r) for r in cur.fetchall()]
        if digits and len(digits) >= 4:
            cur.execute(
                """SELECT DISTINCT c.id, c.name, c.company, c.title, c.last_contacted_at
                   FROM crm_contacts c
                   LEFT JOIN crm_phones p ON p.contact_id = c.id
                   WHERE (c.team_id = %s OR c.team_id IS NULL)
                     AND (c.name ILIKE %s OR c.company ILIKE %s OR p.digits LIKE %s)
                   ORDER BY c.last_contacted_at DESC NULLS LAST, c.name LIMIT %s""",
                (ctx['team_id'], like, like, f'%{digits}%', limit),
            )
        else:
            cur.execute(
                """SELECT c.id, c.name, c.company, c.title, c.last_contacted_at
                   FROM crm_contacts c
                   WHERE (c.team_id = %s OR c.team_id IS NULL)
                     AND (c.name ILIKE %s OR c.company ILIKE %s)
                   ORDER BY c.last_contacted_at DESC NULLS LAST, c.name LIMIT %s""",
                (ctx['team_id'], like, like, limit),
            )
        contacts = [dict(r) for r in cur.fetchall()]
        cur.execute(
            """SELECT id, name FROM crm_lists
               WHERE (team_id = %s OR team_id IS NULL) AND name ILIKE %s
               ORDER BY updated_at DESC LIMIT %s""",
            (ctx['team_id'], like, limit),
        )
        lists = [dict(r) for r in cur.fetchall()]
        return {'buildings': buildings, 'contacts': contacts, 'lists': lists}
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
    params.extend([row_id, ctx['team_id']])
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE {table} SET {', '.join(sets)} WHERE id = %s AND (team_id = %s OR team_id IS NULL)",
            params,
        )
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


def update_contact(ctx, contact_id, fields):
    if 'name' in fields and not (fields.get('name') or '').strip():
        raise ValueError('name is required')
    return _run_update('crm_contacts', CONTACT_EDITABLE, ctx, contact_id, fields)


def update_follow_up(ctx, follow_up_id, *, title=None, due_date=None, note=None,
                     assigned_to_id='__keep__'):
    sets, params = [], []
    if title is not None:
        sets.append('title = %s')
        params.append((title.strip() or 'Follow up')[:255])
    if due_date is not None:
        sets.append('due_date = %s')
        params.append(due_date)
    if note is not None:
        sets.append('note = %s')
        params.append(note.strip() or None)
    if assigned_to_id != '__keep__':
        sets.append('assigned_to_id = %s')
        params.append(assigned_to_id or ctx['user_id'])
    if not sets:
        return
    params.extend([follow_up_id, ctx['team_id']])
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"UPDATE crm_follow_ups SET {', '.join(sets)} WHERE id = %s AND (team_id = %s OR team_id IS NULL)",
            params,
        )
        conn.commit()
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
            "DELETE FROM crm_follow_ups WHERE id = %s AND (team_id = %s OR team_id IS NULL)",
            (follow_up_id, ctx['team_id']),
        )
        conn.commit()
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
            """SELECT p.id, p.contact_id FROM crm_phones p JOIN crm_contacts c ON c.id = p.contact_id
               WHERE p.id = %s AND (c.team_id = %s OR c.team_id IS NULL)""",
            (phone_id, ctx['team_id']),
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
            """DELETE FROM crm_phones p USING crm_contacts c
               WHERE p.id = %s AND c.id = p.contact_id AND (c.team_id = %s OR c.team_id IS NULL)""",
            (phone_id, ctx['team_id']),
        )
        conn.commit()
        return cur.rowcount > 0
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
            """DELETE FROM crm_building_contacts bc USING crm_buildings b
               WHERE bc.building_id = %s AND bc.contact_id = %s
                 AND b.id = bc.building_id AND (b.team_id = %s OR b.team_id IS NULL)""",
            (building_id, contact_id, ctx['team_id']),
        )
        conn.commit()
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
            """UPDATE crm_building_contacts bc SET role = %s FROM crm_buildings b
               WHERE bc.building_id = %s AND bc.contact_id = %s
                 AND b.id = bc.building_id AND (b.team_id = %s OR b.team_id IS NULL)""",
            (role, building_id, contact_id, ctx['team_id']),
        )
        conn.commit()
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
            "DELETE FROM crm_contacts WHERE id = %s AND (team_id = %s OR team_id IS NULL)",
            (contact_id, ctx['team_id']),
        )
        conn.commit()
        return cur.rowcount > 0
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
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id FROM crm_buildings WHERE id = ANY(%s) AND (team_id = %s OR team_id IS NULL)",
            (ids, ctx['team_id']),
        )
        visible = {r['id'] for r in cur.fetchall()}
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
        elif action == 'assign':
            cur.execute(
                "UPDATE crm_buildings SET assigned_to_id = %s, updated_at = NOW() WHERE id = ANY(%s)",
                (int(value) if value else None, ids),
            )
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
                   ON CONFLICT DO NOTHING""",
                (list_id, ctx['user_id'], list_id, ids),
            )
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

    def push(kind, ident, label, sub=None, followup_id=None, followup_title=None):
        key = (kind, ident)
        if key in seen or ident is None:
            return
        seen.add(key)
        items.append({'type': kind, 'id': ident, 'label': label, 'sub': sub,
                      'followup_id': followup_id, 'followup_title': followup_title})

    if source == 'list' and list_id:
        lst = get_list(ctx, list_id)
        for it in (lst or {}).get('items', []):
            if it['building_id']:
                push('building', it['building_id'], it['address'], it['borough'])
            elif it['contact_id']:
                push('contact', it['contact_id'], it['contact_name'], it['company'])
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
                     f'Follow-up: {f["title"]}', f['id'], f['title'])
            elif f['c_id']:
                push('contact', f['c_id'], f['contact_name'],
                     f'Follow-up: {f["title"]}', f['id'], f['title'])
        for b in needs_attention(ctx, limit=limit):
            push('building', b['id'], b['address'],
                 'Stale' if b['reason'] == 'stale' else 'Never contacted')
    return items[:limit]


def due_count(ctx):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT COUNT(*) AS n FROM crm_follow_ups
               WHERE assigned_to_id = %s AND status = 'open' AND due_date <= %s""",
            (ctx['user_id'], ny_today()),
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
