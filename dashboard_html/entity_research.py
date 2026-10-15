"""Entity research: one dossier per person or company name.

A dossier gathers evidence rows (who said this name appears on which record)
from the local database and from NYC public records. Rows are evidence, not
identity: a shared name alone never proves two records describe the same
person. Each row carries a match tier so the page can say how sure we are.

Lifecycle
- A dossier is created on demand from a name plus optional click context
  (the BBL, role and address it was clicked from).
- External sources run on a background worker; progress lives in
  entity_research_jobs.steps so the page can tick sources off.
- External results are cached for 24 hours per dossier.
- Dossiers expire 60 days after their last use unless someone keeps them.
  Nothing is written to the buildings table unless a user explicitly asks to
  add a property permanently.
- CRM data is never persisted into a dossier (dossiers are shared across
  teams); it is joined live, scoped to the requesting user's team.
"""
import hashlib
import json
import logging
import os
import re
import sys
import threading
from datetime import date, datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from psycopg2.extras import Json, RealDictCursor

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

log = logging.getLogger(__name__)

RETENTION_DAYS = 60
CACHE_HOURS = 24
JOB_LEASE_MINUTES = 30
MAX_ACTIVE_JOBS_PER_USER = 12
MIN_NAME_LENGTH = 3
MAX_NAME_LENGTH = 160
TIERS = ('strong', 'exact', 'candidate')

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS entity_dossiers (
        id SERIAL PRIMARY KEY,
        name_key TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        entity_kind TEXT NOT NULL DEFAULT 'unknown'
            CHECK (entity_kind IN ('person','organization','multiple','unknown')),
        contexts JSONB NOT NULL DEFAULT '[]'::jsonb,
        summary JSONB NOT NULL DEFAULT '{}'::jsonb,
        permanent BOOLEAN NOT NULL DEFAULT FALSE,
        saved_by INTEGER,
        saved_at TIMESTAMPTZ,
        expires_at TIMESTAMPTZ,
        external_checked_at TIMESTAMPTZ,
        expanded_at TIMESTAMPTZ,
        created_by INTEGER,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_viewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE INDEX IF NOT EXISTS idx_entity_dossiers_expiry
        ON entity_dossiers (expires_at) WHERE permanent = FALSE""",
    """CREATE TABLE IF NOT EXISTS entity_research_jobs (
        id SERIAL PRIMARY KEY,
        dossier_id INTEGER NOT NULL REFERENCES entity_dossiers(id) ON DELETE CASCADE,
        kind TEXT NOT NULL DEFAULT 'research' CHECK (kind IN ('research','expand')),
        status TEXT NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued','running','complete','failed')),
        force BOOLEAN NOT NULL DEFAULT FALSE,
        steps JSONB NOT NULL DEFAULT '{}'::jsonb,
        requested_by INTEGER,
        attempts INTEGER NOT NULL DEFAULT 0,
        locked_at TIMESTAMPTZ,
        error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at TIMESTAMPTZ
    )""",
    """CREATE INDEX IF NOT EXISTS idx_entity_jobs_queue
        ON entity_research_jobs (status, id)""",
    """CREATE INDEX IF NOT EXISTS idx_entity_jobs_dossier
        ON entity_research_jobs (dossier_id, id DESC)""",
    """CREATE TABLE IF NOT EXISTS entity_evidence (
        id BIGSERIAL PRIMARY KEY,
        dossier_id INTEGER NOT NULL REFERENCES entity_dossiers(id) ON DELETE CASCADE,
        evidence_key TEXT NOT NULL,
        source TEXT NOT NULL,
        record_id TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT '',
        name_as_written TEXT NOT NULL,
        match_tier TEXT NOT NULL DEFAULT 'candidate'
            CHECK (match_tier IN ('strong','exact','candidate')),
        bbl TEXT,
        address TEXT,
        party_address JSONB,
        record_date DATE,
        details JSONB NOT NULL DEFAULT '{}'::jsonb,
        source_url TEXT,
        hop INTEGER NOT NULL DEFAULT 0,
        via TEXT,
        in_database BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (dossier_id, evidence_key)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_entity_evidence_dossier
        ON entity_evidence (dossier_id, source)""",
    """CREATE INDEX IF NOT EXISTS idx_entity_evidence_bbl
        ON entity_evidence (bbl) WHERE bbl IS NOT NULL""",
]

SOURCE_LABELS = {
    'db': 'Our database',
    'dob_db': 'Our permit records',
    'acris': 'ACRIS property records',
    'hpd': 'HPD registration contacts',
    'dob_bis': 'DOB BIS permits',
    'dob_now_filings': 'DOB NOW job filings',
    'dob_now_permits': 'DOB NOW approved permits',
    'ecb': 'ECB / OATH violations',
    'hpd_litigation': 'HPD housing litigation',
    'sos': 'NY Secretary of State',
    'permit_contact': 'Permit contact records',
}

# Step order for the progress UI. Internal first because it is instant.
RESEARCH_STEPS = ('db', 'acris', 'hpd', 'dob_bis', 'dob_now_filings',
                  'dob_now_permits', 'ecb', 'hpd_litigation', 'sos')
EXPAND_STEPS = ('targets', 'hop_acris', 'hop_hpd', 'hop_dob', 'hop_sos')


class ResearchError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

_CARE_OF = re.compile(r'\s+(?:C/O|C\.O\.|ATTN:?|%)\s+.*$', re.IGNORECASE)
_SUFFIXES = {'JR', 'SR', 'II', 'III', 'IV', 'V', 'ESQ', 'MD', 'PHD'}
_ORG_STOPWORDS = {'LLC', 'L.L.C.', 'INC', 'CORP', 'LTD', 'LP', 'LLP', 'CO', 'THE',
                  'OF', 'AND', 'A', 'AN', 'COMPANY', 'CORPORATION', 'LIMITED', 'TRUST'}


def clean_name(value):
    """Collapse whitespace and drop care-of / attention tails."""
    text = ' '.join(str(value or '').replace(' ', ' ').split())
    return _CARE_OF.sub('', text).strip(' ,;')


def flip_last_first(value):
    """'SMITH, JOHN A' -> 'JOHN A SMITH'. Leaves 'ACME, INC' style names alone."""
    parts = [p.strip() for p in value.split(',')]
    if len(parts) in (2, 3) and parts[1] and parts[1].upper().rstrip('.') not in _SUFFIXES \
            and not is_organization(value):
        return ' '.join([parts[1], parts[0]] + parts[2:])
    return value


def entity_key(value):
    """Complete-name equality key: upper case, LAST, FIRST flipped, punctuation out."""
    text = flip_last_first(clean_name(value)).upper()
    text = re.sub(r'[^\w&\s]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def org_key(value):
    """Company key with legal suffixes removed, so 'ABC REALTY LLC' == 'ABC REALTY, L.L.C.'."""
    from ny_sos_lookup import normalize_business_name
    return normalize_business_name(clean_name(value))


def is_organization(value):
    from enrichment_service import is_business_entity
    return bool(value) and is_business_entity(clean_name(value))


def classify(value):
    """person | organization | multiple | unknown (same model the property page uses)."""
    from enrichment_service import classify_party_name
    return classify_party_name(clean_name(value))['entity_kind']


def person_parts(value):
    """(first, middle, last) for a person-shaped name, else (None, None, None)."""
    from nameparser import HumanName
    if classify(value) != 'person':
        return None, None, None
    parsed = HumanName(flip_last_first(clean_name(value)))
    first, last = (parsed.first or '').strip(), (parsed.last or '').strip()
    if not first or not last:
        return None, None, None
    return first.upper(), (parsed.middle or '').strip().upper(), last.upper()


def name_variants(value, kind=None):
    """Strings worth matching with LIKE, most specific first.

    Persons: FIRST LAST, LAST, FIRST and LAST FIRST (ACRIS and HPD vary).
    Organizations: the full cleaned name plus the suffix-stripped form when it
    is still specific enough to be useful.
    """
    kind = kind or classify(value)
    cleaned = clean_name(value).upper()
    variants = []
    if kind == 'person':
        first, middle, last = person_parts(value)
        if first and last:
            variants += [f'{first} {last}', f'{last}, {first}', f'{last} {first}']
            if middle:
                variants.insert(0, f'{first} {middle} {last}')
    if cleaned and cleaned not in variants:
        variants.append(cleaned)
    if kind == 'organization':
        stripped = org_key(value)
        if stripped and stripped != cleaned and len(stripped) >= 6 and stripped not in variants:
            variants.append(stripped)
    return [v for v in dict.fromkeys(v for v in variants if len(v) >= MIN_NAME_LENGTH)]


def fulltext_tokens(value, limit=3):
    """Tokens for Socrata's indexed $q; legal suffixes and tiny words add nothing."""
    tokens = []
    for token in re.findall(r"[A-Za-z][A-Za-z'&-]{2,}", flip_last_first(clean_name(value))):
        upper = token.upper().strip("'&-")
        if upper and upper not in _ORG_STOPWORDS and upper not in tokens:
            tokens.append(upper)
    return tokens[:limit]


def address_key(street=None, city=None, state=None, zip_code=None):
    """Mailing-address equality key; needs a street plus a ZIP or city."""
    street = re.sub(r'[^\w\s]', ' ', str(street or '').upper())
    street = re.sub(r'\b(STREET)\b', 'ST', street)
    street = re.sub(r'\b(AVENUE)\b', 'AVE', street)
    street = re.sub(r'\b(ROAD)\b', 'RD', street)
    street = re.sub(r'\b(BOULEVARD)\b', 'BLVD', street)
    street = re.sub(r'\b(PLACE)\b', 'PL', street)
    street = re.sub(r'\b(DRIVE)\b', 'DR', street)
    street = re.sub(r'\b(EAST)\b', 'E', street)
    street = re.sub(r'\b(WEST)\b', 'W', street)
    street = re.sub(r'\b(NORTH)\b', 'N', street)
    street = re.sub(r'\b(SOUTH)\b', 'S', street)
    street = re.sub(r'\s+', ' ', street).strip()
    zip5 = re.sub(r'\D', '', str(zip_code or ''))[:5]
    city = re.sub(r'\s+', ' ', str(city or '').upper()).strip()
    if not street or not (zip5 or city):
        return None
    return f'{street}|{zip5 or city}'


def matches_name(written, query_name, kind):
    """exact when the complete names agree, candidate when the query is only contained."""
    if not written:
        return None
    if entity_key(written) == entity_key(query_name):
        return 'exact'
    if kind == 'organization' and org_key(written) and org_key(written) == org_key(query_name):
        return 'exact'
    w = entity_key(written)
    return 'candidate' if any(entity_key(v) and entity_key(v) in w for v in name_variants(query_name, kind)) else None


def evidence_key(source, record_id, role, name_as_written, via=None):
    raw = json.dumps([source, str(record_id), role or '', entity_key(name_as_written), via or ''])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def to_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or '').strip()[:10]
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def iso(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def bbl_from_parts(boro, block, lot):
    boro = str(boro or '').strip()
    block = re.sub(r'\D', '', str(block or ''))
    lot = re.sub(r'\D', '', str(lot or ''))
    if boro not in '12345' or not boro or not block or not lot:
        return None
    return f'{boro}{block.zfill(5)[-5:]}{lot.zfill(4)[-4:]}'


BOROUGH_CODES = {'MANHATTAN': '1', 'MN': '1', 'NEW YORK': '1', 'BRONX': '2', 'BX': '2',
                 'BROOKLYN': '3', 'BK': '3', 'KINGS': '3', 'QUEENS': '4', 'QN': '4',
                 'STATEN ISLAND': '5', 'SI': '5', 'RICHMOND': '5',
                 '1': '1', '2': '2', '3': '3', '4': '4', '5': '5'}
BOROUGH_NAMES = {'1': 'Manhattan', '2': 'Bronx', '3': 'Brooklyn', '4': 'Queens', '5': 'Staten Island'}


def borough_code(value):
    return BOROUGH_CODES.get(str(value or '').strip().upper())


# ---------------------------------------------------------------------------
# Evidence rows
# ---------------------------------------------------------------------------

def evidence(source, record_id, name_as_written, role='', bbl=None, address=None,
             party_address=None, record_date=None, details=None, source_url=None,
             hop=0, via=None, in_database=False):
    """Build one evidence row dict. match_tier is assigned later by `tier_rows`."""
    return {
        'source': source, 'record_id': str(record_id or '').strip() or 'unknown',
        'role': role or '', 'name_as_written': clean_name(name_as_written),
        'bbl': bbl, 'address': clean_name(address) or None,
        'party_address': party_address or None, 'record_date': to_date(record_date),
        'details': details or {}, 'source_url': source_url, 'hop': hop, 'via': via,
        'in_database': in_database,
    }


def party_address_dict(street=None, unit=None, city=None, state=None, zip_code=None):
    values = dict(street=clean_name(street) or None, unit=clean_name(unit) or None,
                  city=clean_name(city) or None, state=clean_name(state) or None,
                  zip=clean_name(zip_code) or None)
    return values if any(values.values()) else None


def tier_rows(rows, dossier):
    """Assign strong / exact / candidate to each row.

    exact: the complete name agrees with the dossier name.
    strong: exact, plus a corroborating address or lot: the record's party
            address matches an address already linked to this name, or the
            record is on a lot the click context or our database already ties
            to it.
    candidate: the dossier name is only contained in the written name.
    """
    name, kind = dossier['display_name'], dossier['entity_kind']
    known_bbls = {c.get('bbl') for c in dossier.get('contexts') or [] if c.get('bbl')}
    known_addresses = set()
    for c in dossier.get('contexts') or []:
        key = address_key(c.get('address'), c.get('city'), c.get('state'), c.get('zip'))
        if key:
            known_addresses.add(key)

    exact_rows = []
    for row in rows:
        row['match_tier'] = matches_name(row['name_as_written'], name, kind) or 'candidate'
        if row['hop']:
            # Second-hop rows describe a connected entity, never the dossier
            # subject; they stay candidates so they are never counted as the
            # subject's own records.
            row['match_tier'] = 'candidate'
            continue
        if row['match_tier'] == 'exact':
            exact_rows.append(row)
            if row['in_database'] and row['bbl']:
                known_bbls.add(row['bbl'])
    # An address seen on two separate exact records is treated as linked to
    # the name. One record is not enough: a single typo'd deed would do.
    seen = {}
    for row in exact_rows:
        pa = row.get('party_address') or {}
        key = address_key(pa.get('street'), pa.get('city'), pa.get('state'), pa.get('zip'))
        if key:
            seen[key] = seen.get(key, 0) + 1
    known_addresses |= {k for k, n in seen.items() if n >= 2}
    for row in exact_rows:
        pa = row.get('party_address') or {}
        key = address_key(pa.get('street'), pa.get('city'), pa.get('state'), pa.get('zip'))
        if (key and key in known_addresses) or (row.get('bbl') and row['bbl'] in known_bbls):
            row['match_tier'] = 'strong'
    return rows


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def connect_default():
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        return psycopg2.connect(database_url, connect_timeout=5,
                                options='-c statement_timeout=60000')
    return psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'),
                            user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
                            dbname=os.getenv('DB_NAME'), connect_timeout=5,
                            options='-c statement_timeout=60000')


def init_tables(conn):
    with conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(86753095)')
        for statement in SCHEMA:
            cur.execute(statement)
    conn.commit()


def _columns(cur, table):
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", (table,))
    return {r[0] if not isinstance(r, dict) else r['column_name'] for r in cur.fetchall()}


def escape_like(value):
    return str(value).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def like_patterns(name, kind):
    return [f'%{escape_like(v)}%' for v in name_variants(name, kind)]


def _ilike_any(column, count):
    return '(' + ' OR '.join([f"{column} ILIKE %s"] * count) + ')'


# ---------------------------------------------------------------------------
# Dossiers
# ---------------------------------------------------------------------------

def validate_name(name):
    cleaned = clean_name(name)
    if len(cleaned) < MIN_NAME_LENGTH:
        raise ResearchError('Enter at least three characters of a name.')
    if len(cleaned) > MAX_NAME_LENGTH:
        raise ResearchError('That name is too long to search.')
    if re.fullmatch(r'[\d\s\-]+', cleaned):
        raise ResearchError('That looks like a number, not a name. Use the property search for a BBL.')
    if not re.search(r'[A-Za-z]{2}', cleaned):
        raise ResearchError('Enter a person or company name.')
    return cleaned


def clean_context(context):
    """Keep only the click context we understand; never trust it as identity."""
    context = context if isinstance(context, dict) else {}
    out = {}
    bbl = re.sub(r'\D', '', str(context.get('bbl') or ''))
    if re.fullmatch(r'[1-5]\d{9}', bbl):
        out['bbl'] = bbl
    for key in ('role', 'address', 'city', 'state', 'zip', 'source'):
        value = clean_name(context.get(key))
        if value:
            out[key] = value[:200]
    return out


def get_or_create_dossier(conn, name, context=None, user_id=None):
    """Find the dossier for this name key or create it. Returns (row, created)."""
    cleaned = validate_name(name)
    key = entity_key(cleaned)
    kind = classify(cleaned)
    context = clean_context(context)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM entity_dossiers WHERE name_key=%s FOR UPDATE", (key,))
        row = cur.fetchone()
        created = row is None
        if created:
            cur.execute("""INSERT INTO entity_dossiers (name_key, display_name, entity_kind, contexts,
                                expires_at, created_by)
                           VALUES (%s,%s,%s,%s, NOW() + make_interval(days => %s), %s) RETURNING *""",
                        (key, cleaned, kind, Json([context] if context else []), RETENTION_DAYS, user_id))
            row = cur.fetchone()
        elif context and context not in (row['contexts'] or []):
            contexts = (row['contexts'] or []) + [context]
            cur.execute("UPDATE entity_dossiers SET contexts=%s, updated_at=NOW() WHERE id=%s RETURNING *",
                        (Json(contexts[-20:]), row['id']))
            row = cur.fetchone()
    conn.commit()
    return dict(row), created


def touch_dossier(conn, dossier_id):
    """Viewing or researching a dossier restarts its 60-day clock."""
    with conn.cursor() as cur:
        cur.execute("""UPDATE entity_dossiers SET last_viewed_at=NOW(),
                          expires_at = CASE WHEN permanent THEN NULL
                                            ELSE NOW() + make_interval(days => %s) END
                       WHERE id=%s""", (RETENTION_DAYS, dossier_id))
    conn.commit()


def set_permanent(conn, dossier_id, permanent, user_id=None):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""UPDATE entity_dossiers
                       SET permanent=%s,
                           saved_by = CASE WHEN %s THEN %s ELSE NULL END,
                           saved_at = CASE WHEN %s THEN NOW() ELSE NULL END,
                           expires_at = CASE WHEN %s THEN NULL
                                             ELSE NOW() + make_interval(days => %s) END,
                           updated_at=NOW()
                       WHERE id=%s RETURNING *""",
                    (bool(permanent), bool(permanent), user_id, bool(permanent), bool(permanent),
                     RETENTION_DAYS, dossier_id))
        row = cur.fetchone()
    conn.commit()
    if not row:
        raise ResearchError('Research not found.', 404)
    return dict(row)


def purge_expired(conn):
    """Delete unsaved dossiers past their expiry. Evidence and jobs cascade."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM entity_dossiers WHERE permanent=FALSE AND expires_at IS NOT NULL AND expires_at < NOW()")
        count = cur.rowcount
    conn.commit()
    return count


def external_is_fresh(dossier):
    checked = dossier.get('external_checked_at')
    if not checked:
        return False
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return checked > datetime.now(timezone.utc) - timedelta(hours=CACHE_HOURS)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

_wake = threading.Event()


def enqueue_job(conn, dossier_id, kind='research', user_id=None, force=False):
    """Queue work for the worker. Reuses an already queued job of the same kind."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if user_id is not None:
            cur.execute("""SELECT COUNT(*) AS n FROM entity_research_jobs
                           WHERE requested_by=%s AND status IN ('queued','running')""", (user_id,))
            if cur.fetchone()['n'] >= MAX_ACTIVE_JOBS_PER_USER:
                raise ResearchError('Too many searches are already running. Wait for them to finish.', 429)
        cur.execute("""SELECT * FROM entity_research_jobs
                       WHERE dossier_id=%s AND kind=%s AND status IN ('queued','running')
                       ORDER BY id DESC LIMIT 1""", (dossier_id, kind))
        existing = cur.fetchone()
        if existing and not (force and not existing['force']):
            conn.commit()
            return dict(existing), False
        cur.execute("""INSERT INTO entity_research_jobs (dossier_id, kind, requested_by, force)
                       VALUES (%s,%s,%s,%s) RETURNING *""", (dossier_id, kind, user_id, bool(force)))
        job = dict(cur.fetchone())
    conn.commit()
    _wake.set()
    return job, True


def get_job(conn, job_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM entity_research_jobs WHERE id=%s", (job_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def latest_jobs(conn, dossier_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT DISTINCT ON (kind) * FROM entity_research_jobs
                       WHERE dossier_id=%s ORDER BY kind, id DESC""", (dossier_id,))
        return {r['kind']: dict(r) for r in cur.fetchall()}


def _claim_job(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""UPDATE entity_research_jobs SET status='running', locked_at=NOW(),
                              attempts=attempts+1, updated_at=NOW()
                       WHERE id = (SELECT id FROM entity_research_jobs
                                   WHERE status='queued'
                                      OR (status='running' AND locked_at < NOW() - make_interval(mins => %s)
                                          AND attempts < 3)
                                   ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED)
                       RETURNING *""", (JOB_LEASE_MINUTES,))
        row = cur.fetchone()
    conn.commit()
    return dict(row) if row else None


def _step(conn, job_id, key, **state):
    state.setdefault('at', datetime.now(timezone.utc).isoformat())
    with conn.cursor() as cur:
        cur.execute("""UPDATE entity_research_jobs SET steps = steps || %s::jsonb, updated_at=NOW(),
                              locked_at=NOW() WHERE id=%s""", (Json({key: state}), job_id))
    conn.commit()


def _finish(conn, job_id, status, error=None):
    with conn.cursor() as cur:
        cur.execute("""UPDATE entity_research_jobs SET status=%s, error=%s, finished_at=NOW(),
                              updated_at=NOW() WHERE id=%s""", (status, error, job_id))
    conn.commit()


def load_dossier(conn, dossier_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM entity_dossiers WHERE id=%s", (dossier_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def upsert_evidence(conn, dossier_id, rows):
    """Insert or refresh evidence rows. Returns the number written."""
    if not rows:
        return 0
    values = []
    for row in rows:
        key = evidence_key(row['source'], row['record_id'], row['role'], row['name_as_written'], row.get('via'))
        values.append((dossier_id, key, row['source'], row['record_id'], row['role'], row['name_as_written'],
                       row.get('match_tier', 'candidate'), row.get('bbl'), row.get('address'),
                       Json(row['party_address']) if row.get('party_address') else None,
                       row.get('record_date'), Json(row.get('details') or {}), row.get('source_url'),
                       int(row.get('hop') or 0), row.get('via'), bool(row.get('in_database'))))
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO entity_evidence (dossier_id, evidence_key, source, record_id, role, name_as_written,
                match_tier, bbl, address, party_address, record_date, details, source_url, hop, via, in_database)
            VALUES %s
            ON CONFLICT (dossier_id, evidence_key) DO UPDATE SET
                match_tier=EXCLUDED.match_tier, bbl=COALESCE(EXCLUDED.bbl, entity_evidence.bbl),
                address=COALESCE(EXCLUDED.address, entity_evidence.address),
                party_address=COALESCE(EXCLUDED.party_address, entity_evidence.party_address),
                record_date=COALESCE(EXCLUDED.record_date, entity_evidence.record_date),
                details=entity_evidence.details || EXCLUDED.details,
                source_url=COALESCE(EXCLUDED.source_url, entity_evidence.source_url),
                in_database=entity_evidence.in_database OR EXCLUDED.in_database,
                updated_at=NOW()""", values, page_size=200)
    conn.commit()
    return len(values)


def retier_dossier(conn, dossier):
    """Recompute tiers across all stored rows (new rows change what is corroborated)."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, name_as_written, bbl, party_address, hop, in_database FROM entity_evidence WHERE dossier_id=%s",
                    (dossier['id'],))
        rows = [dict(r) for r in cur.fetchall()]
    tier_rows(rows, dossier)
    if not rows:
        conn.rollback()
        return
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """UPDATE entity_evidence AS e SET match_tier=v.tier
            FROM (VALUES %s) AS v(id, tier) WHERE e.id=v.id AND e.match_tier<>v.tier""",
            [(r['id'], r['match_tier']) for r in rows], page_size=500)
    conn.commit()


def mark_in_database(conn, dossier_id):
    """Flag evidence on lots we already track so the page can link straight to the profile."""
    with conn.cursor() as cur:
        cur.execute("""UPDATE entity_evidence e SET in_database=TRUE
                       FROM buildings b WHERE e.dossier_id=%s AND e.bbl IS NOT NULL AND e.bbl=b.bbl
                         AND e.in_database=FALSE""", (dossier_id,))
    conn.commit()


def refresh_summary(conn, dossier_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT source, match_tier, hop, COUNT(*) AS n,
                              COUNT(DISTINCT bbl) FILTER (WHERE bbl IS NOT NULL) AS lots
                       FROM entity_evidence WHERE dossier_id=%s GROUP BY source, match_tier, hop""", (dossier_id,))
        groups = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT COUNT(DISTINCT bbl) AS lots FROM entity_evidence
                       WHERE dossier_id=%s AND bbl IS NOT NULL AND hop=0 AND match_tier<>'candidate'""", (dossier_id,))
        lots = cur.fetchone()['lots']
    summary = {'by_source': {}, 'by_tier': {t: 0 for t in TIERS}, 'lots': lots, 'hop_rows': 0}
    for g in groups:
        summary['by_source'][g['source']] = summary['by_source'].get(g['source'], 0) + g['n']
        if g['hop']:
            summary['hop_rows'] += g['n']
        else:
            summary['by_tier'][g['match_tier']] += g['n']
    with conn.cursor() as cur:
        cur.execute("UPDATE entity_dossiers SET summary=%s, updated_at=NOW() WHERE id=%s", (Json(summary), dossier_id))
    conn.commit()
    return summary


# ---------------------------------------------------------------------------
# Internal lookup: public tables only (CRM is joined live, per team)
# ---------------------------------------------------------------------------

def internal_lookup(conn, dossier, limit=300):
    """Evidence from our own buildings, ACRIS mirror, permits and permit contacts."""
    name, kind = dossier['display_name'], dossier['entity_kind']
    patterns = like_patterns(name, kind)
    if not patterns:
        return []
    rows = []
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SET LOCAL statement_timeout = '20s'")
        rows += _db_buildings(cur, patterns, limit)
        rows += _db_acris(cur, patterns, limit)
        rows += _db_permits(cur, patterns, limit)
        rows += _db_permit_contacts(cur, patterns, limit)
    conn.rollback()  # read-only; releases the SET LOCAL
    return rows


_BUILDING_OWNER_FIELDS = (
    ('current_owner_name', 'Tax-lot owner (PLUTO)'),
    ('owner_name_rpad', 'Assessment owner (RPAD)'),
    ('owner_name_hpd', 'HPD registered owner'),
    ('hpd_agent_name', 'HPD managing agent'),
    ('hpd_site_manager_name', 'HPD site manager'),
    ('sale_buyer_primary', 'Latest deed grantee'),
    ('sale_seller_primary', 'Latest deed grantor'),
    ('mortgage_lender_primary', 'Mortgage lender'),
    ('ecb_respondent_name', 'ECB respondent'),
    ('sos_principal_name', 'Registered entity principal'),
    ('sos_entity_name', 'Registered entity'),
)


def _db_buildings(cur, patterns, limit):
    cols = _columns(cur, 'buildings')
    fields = [(c, label) for c, label in _BUILDING_OWNER_FIELDS if c in cols]
    if not fields:
        return []
    where = ' OR '.join(_ilike_any(f'b.{c}', len(patterns)) for c, _ in fields)
    params = []
    for _ in fields:
        params += patterns
    select = ', '.join(f'b.{c}' for c, _ in fields)
    extra = [c for c in ('assessed_total_value', 'total_units', 'units', 'building_class', 'sale_date',
                         'sale_price', 'sos_dos_id', 'sos_principal_title') if c in cols]
    extra_sql = (', ' + ', '.join(f'b.{c}' for c in extra)) if extra else ''
    cur.execute(f"""SELECT b.id, b.bbl, b.address, b.borough, {select}{extra_sql}
                    FROM buildings b WHERE {where} ORDER BY b.id LIMIT %s""", params + [limit])
    out = []
    for b in cur.fetchall():
        if not b.get('bbl'):
            continue
        for column, label in fields:
            value = b.get(column)
            if not value:
                continue
            details = {'origin': 'database', 'field': column,
                       'assessed_total_value': float(b['assessed_total_value']) if b.get('assessed_total_value') else None,
                       'units': b.get('total_units') or b.get('units'), 'building_class': b.get('building_class'),
                       'sale_date': iso(b.get('sale_date')),
                       'sale_price': float(b['sale_price']) if b.get('sale_price') else None}
            if column == 'sos_principal_name':
                details['entity_name'] = b.get('sos_entity_name')
                details['title'] = b.get('sos_principal_title')
            out.append(evidence('db', f"{b['bbl']}:{column}", value, role=label, bbl=b['bbl'],
                                address=b['address'], record_date=b.get('sale_date') if column.startswith('sale_') else None,
                                details=details, source_url=f"/property/{b['bbl']}", in_database=True))
    return out


def _db_acris(cur, patterns, limit):
    cols = _columns(cur, 'acris_parties')
    if not {'party_name', 'transaction_id'} <= cols:
        return []
    cur.execute(f"""SELECT p.party_name, p.party_type, p.address_1, p.address_2, p.city, p.state, p.zip,
                           t.document_id, t.doc_type, t.doc_amount, t.doc_date, t.recorded_date, t.crfn,
                           b.bbl, b.address
                    FROM acris_parties p
                    JOIN acris_transactions t ON t.id = p.transaction_id
                    JOIN buildings b ON b.id = t.building_id
                    WHERE {_ilike_any('p.party_name', len(patterns))}
                    ORDER BY t.recorded_date DESC NULLS LAST LIMIT %s""", patterns + [limit])
    hits = [dict(r) for r in cur.fetchall()]
    if not hits:
        return []
    doc_ids = sorted({h['document_id'] for h in hits if h['document_id']})
    cur.execute("""SELECT t.document_id, p.party_name, p.party_type FROM acris_parties p
                   JOIN acris_transactions t ON t.id=p.transaction_id WHERE t.document_id = ANY(%s)""", (doc_ids,))
    parties = {}
    for r in cur.fetchall():
        parties.setdefault(r['document_id'], []).append({'name': r['party_name'], 'role': r['party_type']})
    from record_links import acris_document_url
    out = []
    for h in hits:
        others = [p for p in parties.get(h['document_id'], [])
                  if entity_key(p['name']) != entity_key(h['party_name'])]
        out.append(evidence('acris', h['document_id'], h['party_name'], role=h['party_type'] or 'party',
                            bbl=h['bbl'], address=h['address'],
                            party_address=party_address_dict(h['address_1'], h['address_2'], h['city'], h['state'], h['zip']),
                            record_date=h['recorded_date'] or h['doc_date'],
                            details={'origin': 'database', 'doc_type': h['doc_type'],
                                     'amount': float(h['doc_amount']) if h.get('doc_amount') else None,
                                     'crfn': h['crfn'], 'parties': others[:12]},
                            source_url=acris_document_url(h['document_id']), in_database=True))
    return out


_PERMIT_NAME_FIELDS = (
    ('owner_business_name', None, 'Owner (business)'),
    ('owner_first_name', 'owner_last_name', 'Owner'),
    ('applicant', None, 'Applicant'),
    ('applicant_business_name', None, 'Applicant (business)'),
    ('applicant_first_name', 'applicant_last_name', 'Applicant'),
    ('permittee_business_name', None, 'Permittee (business)'),
    ('permittee_first_name', 'permittee_last_name', 'Permittee'),
    ('filing_representative_business_name', None, 'Filing representative (business)'),
    ('filing_representative_first_name', 'filing_representative_last_name', 'Filing representative'),
    ('superintendent_name', None, 'Superintendent'),
    ('superintendent_business_name', None, 'Superintendent (business)'),
    ('site_safety_mgr_business_name', None, 'Site safety manager (business)'),
)


def _db_permits(cur, patterns, limit):
    cols = _columns(cur, 'permits')
    fields = [(a, b, label) for a, b, label in _PERMIT_NAME_FIELDS if a in cols and (b is None or b in cols)]
    if not fields:
        return []
    exprs = [(f"NULLIF(TRIM(CONCAT_WS(' ', p.{a}, p.{b})), '')" if b else f'p.{a}', label) for a, b, label in fields]
    where = ' OR '.join(_ilike_any(expr, len(patterns)) for expr, _ in exprs)
    params = []
    for _ in exprs:
        params += patterns
    select = ', '.join(f'{expr} AS name_{i}' for i, (expr, _) in enumerate(exprs))
    extra = [c for c in ('job_number', 'work_type', 'permit_status', 'filing_status', 'filing_date',
                         'work_description', 'api_source', 'link', 'owner_house_number', 'owner_street_name',
                         'owner_city', 'owner_state', 'owner_zip_code', 'initial_cost') if c in cols]
    extra_sql = (', ' + ', '.join(f'p.{c}' for c in extra)) if extra else ''
    cur.execute(f"""SELECT p.id, p.permit_no, p.job_type, p.issue_date, p.address, p.bbl, {select}{extra_sql}
                    FROM permits p WHERE {where}
                    ORDER BY COALESCE(p.issue_date, p.filing_date) DESC NULLS LAST LIMIT %s""", params + [limit])
    from record_links import permit_source_link
    out = []
    for p in cur.fetchall():
        p = dict(p)
        link = None
        try:
            link = (permit_source_link(p) or {}).get('url')
        except Exception:
            link = p.get('link')
        for i, (_, label) in enumerate(exprs):
            value = p.get(f'name_{i}')
            if not value:
                continue
            pa = None
            if label.startswith('Owner'):
                pa = party_address_dict(' '.join(str(p.get(k) or '') for k in ('owner_house_number', 'owner_street_name')).strip(),
                                        None, p.get('owner_city'), p.get('owner_state'), p.get('owner_zip_code'))
            others = [{'name': p.get(f'name_{j}'), 'role': other_label}
                      for j, (_, other_label) in enumerate(exprs)
                      if j != i and p.get(f'name_{j}') and entity_key(p.get(f'name_{j}')) != entity_key(value)]
            out.append(evidence('dob_db', f"{p['permit_no']}:{label}", value, role=label, bbl=p['bbl'],
                                address=p['address'], party_address=pa,
                                record_date=p.get('issue_date') or p.get('filing_date'),
                                details={'origin': 'database', 'permit_no': p['permit_no'], 'job_number': p.get('job_number'),
                                         'job_type': p.get('job_type'), 'work_type': p.get('work_type'),
                                         'status': p.get('permit_status') or p.get('filing_status'),
                                         'description': (p.get('work_description') or '')[:240] or None,
                                         'api_source': p.get('api_source'), 'parties': others[:8],
                                         'cost': float(p['initial_cost']) if p.get('initial_cost') else None},
                                source_url=link or f"/permit/{p['id']}", in_database=True))
    return out


def _db_permit_contacts(cur, patterns, limit):
    cols = _columns(cur, 'contacts')
    if 'name' not in cols:
        return []
    cur.execute(f"""SELECT c.id AS contact_id, c.name, c.phone, COALESCE(pc.contact_role, c.role, 'Contact') AS role,
                           p.id AS permit_id, p.permit_no, p.address, p.bbl, p.issue_date, p.job_type
                    FROM contacts c JOIN permit_contacts pc ON pc.contact_id=c.id JOIN permits p ON p.id=pc.permit_id
                    WHERE {_ilike_any('c.name', len(patterns))}
                    ORDER BY p.issue_date DESC NULLS LAST LIMIT %s""", patterns + [limit])
    out = []
    for r in cur.fetchall():
        out.append(evidence('permit_contact', f"{r['contact_id']}:{r['permit_id']}", r['name'], role=r['role'],
                            bbl=r['bbl'], address=r['address'], record_date=r['issue_date'],
                            details={'origin': 'database', 'phone': r['phone'], 'permit_no': r['permit_no'],
                                     'job_type': r['job_type'], 'contact_id': r['contact_id']},
                            source_url=f"/permit/{r['permit_id']}", in_database=True))
    if 'raw_name' in _columns(cur, 'contact_evidence'):
        cur.execute(f"""SELECT ce.id, ce.raw_name, ce.observed_role, ce.source, p.id AS permit_id, p.permit_no,
                               p.address, p.bbl, p.issue_date
                        FROM contact_evidence ce JOIN permits p ON p.id=ce.permit_id
                        WHERE ce.contact_id IS NULL AND {_ilike_any('ce.raw_name', len(patterns))}
                        ORDER BY p.issue_date DESC NULLS LAST LIMIT %s""", patterns + [limit])
        for r in cur.fetchall():
            out.append(evidence('permit_contact', f"evidence:{r['id']}", r['raw_name'],
                                role=r['observed_role'] or 'Legacy contact', bbl=r['bbl'], address=r['address'],
                                record_date=r['issue_date'],
                                details={'origin': 'database', 'permit_no': r['permit_no'], 'evidence_source': r['source']},
                                source_url=f"/permit/{r['permit_id']}", in_database=True))
    return out


# ---------------------------------------------------------------------------
# CRM join (live, team scoped, never stored)
# ---------------------------------------------------------------------------

def crm_matches(conn, dossier, ctx):
    """What this team already knows about the name: contacts, buildings, research flags."""
    from crm_service import record_scope_sql
    patterns = like_patterns(dossier['display_name'], dossier['entity_kind'])
    out = {'contacts': [], 'buildings': [], 'do_not_contact': [], 'research': []}
    if not patterns or not ctx:
        return out
    params = {'team_id': ctx['team_id'], 'user_id': ctx['user_id'], 'patterns': patterns, 'key': dossier['name_key']}
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        try:
            cur.execute(f"""SELECT c.id, c.name, c.company, c.title, c.last_contacted_at FROM crm_contacts c
                            WHERE {record_scope_sql(ctx, 'c')}
                              AND (c.name ILIKE ANY(%(patterns)s) OR c.company ILIKE ANY(%(patterns)s))
                            ORDER BY c.last_contacted_at DESC NULLS LAST LIMIT 25""", params)
            out['contacts'] = [dict(r, last_contacted_at=iso(r['last_contacted_at'])) for r in cur.fetchall()]
            cur.execute(f"""SELECT b.id, b.bbl, b.address, b.borough, b.stage, b.owner_name, b.last_contacted_at
                            FROM crm_buildings b WHERE {record_scope_sql(ctx, 'b')}
                              AND b.owner_name ILIKE ANY(%(patterns)s)
                            ORDER BY b.last_contacted_at DESC NULLS LAST LIMIT 25""", params)
            out['buildings'] = [dict(r, last_contacted_at=iso(r['last_contacted_at'])) for r in cur.fetchall()]
            cur.execute("""SELECT bbl, status, match_status, notes, reviewed_at FROM crm_owner_research
                           WHERE team_id=%(team_id)s AND name_key=%(key)s ORDER BY reviewed_at DESC LIMIT 50""", params)
            for r in cur.fetchall():
                item = dict(r, reviewed_at=iso(r['reviewed_at']))
                out['research'].append(item)
                if r['status'] == 'do_not_contact':
                    out['do_not_contact'].append(r['bbl'])
        except psycopg2.Error:
            conn.rollback()
            log.exception('CRM join for entity research failed')
    conn.rollback()
    return out


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def run_job(connect, job):
    """Execute one queued job: research (all sources) or expand (second hop)."""
    import entity_sources
    conn = connect()
    try:
        dossier = load_dossier(conn, job['dossier_id'])
        if not dossier:
            _finish(conn, job['id'], 'failed', 'Research no longer exists')
            return
        if job['kind'] == 'research':
            _run_research(conn, job, dossier, entity_sources)
        else:
            _run_expand(conn, job, dossier, entity_sources)
        mark_in_database(conn, dossier['id'])
        retier_dossier(conn, load_dossier(conn, dossier['id']))
        refresh_summary(conn, dossier['id'])
        touch_dossier(conn, dossier['id'])
        _finish(conn, job['id'], 'complete')
    except Exception as exc:
        log.exception('Entity research job %s failed', job['id'])
        try:
            conn.rollback()
            _finish(conn, job['id'], 'failed', f'{type(exc).__name__}: {exc}'[:500])
        except Exception:
            log.exception('Could not record job failure')
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _run_research(conn, job, dossier, sources):
    _step(conn, job['id'], 'db', status='running')
    try:
        rows = internal_lookup(conn, dossier)
        tier_rows(rows, dossier)
        upsert_evidence(conn, dossier['id'], rows)
        _step(conn, job['id'], 'db', status='done', count=len(rows))
    except Exception as exc:
        conn.rollback()
        log.exception('Internal lookup failed')
        _step(conn, job['id'], 'db', status='failed', error=str(exc)[:200])

    if external_is_fresh(dossier) and not job.get('force'):
        for key in RESEARCH_STEPS[1:]:
            _step(conn, job['id'], key, status='cached')
        return

    for key, fetch in sources.research_steps(dossier):
        _step(conn, job['id'], key, status='running')
        try:
            result = fetch()
            if result is None:
                _step(conn, job['id'], key, status='skipped', note=sources.skip_reason(key, dossier))
                continue
            rows, note = result
            tier_rows(rows, dossier)
            upsert_evidence(conn, dossier['id'], rows)
            _step(conn, job['id'], key, status='done', count=len(rows), note=note)
        except Exception as exc:
            conn.rollback()
            log.warning('Entity source %s failed for %r: %s', key, dossier['display_name'], exc)
            _step(conn, job['id'], key, status='failed', error=str(exc)[:200])
    with conn.cursor() as cur:
        cur.execute("UPDATE entity_dossiers SET external_checked_at=NOW(), updated_at=NOW() WHERE id=%s", (dossier['id'],))
    conn.commit()


def _run_expand(conn, job, dossier, sources):
    _step(conn, job['id'], 'targets', status='running')
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""SELECT source, role, name_as_written, match_tier, details, bbl FROM entity_evidence
                       WHERE dossier_id=%s AND hop=0 AND match_tier<>'candidate'""", (dossier['id'],))
        own_rows = [dict(r) for r in cur.fetchall()]
    conn.rollback()
    targets = sources.expansion_targets(dossier, own_rows)
    _step(conn, job['id'], 'targets', status='done', count=len(targets),
          note=', '.join(t['name'] for t in targets) or 'No connected names strong enough to follow')
    for key, fetch in sources.expand_steps(dossier, targets):
        _step(conn, job['id'], key, status='running')
        try:
            rows, note = fetch()
            for row in rows:
                row['match_tier'] = 'candidate'
            upsert_evidence(conn, dossier['id'], rows)
            _step(conn, job['id'], key, status='done', count=len(rows), note=note)
        except Exception as exc:
            conn.rollback()
            log.warning('Entity expansion %s failed for %r: %s', key, dossier['display_name'], exc)
            _step(conn, job['id'], key, status='failed', error=str(exc)[:200])
    with conn.cursor() as cur:
        cur.execute("UPDATE entity_dossiers SET expanded_at=NOW(), updated_at=NOW() WHERE id=%s", (dossier['id'],))
    conn.commit()


_worker_lock = threading.Lock()
_worker = None


def process_one(connect):
    conn = connect()
    try:
        job = _claim_job(conn)
    finally:
        conn.close()
    if not job:
        return False
    run_job(connect, job)
    return True


def start_worker(connect=connect_default):
    """One daemon thread per web worker; SKIP LOCKED keeps them from colliding."""
    global _worker
    with _worker_lock:
        if _worker and _worker.is_alive():
            return _worker

        def run():
            last_purge = 0
            while True:
                try:
                    if process_one(connect):
                        continue
                    import time
                    if time.time() - last_purge > 3600:
                        conn = connect()
                        try:
                            purged = purge_expired(conn)
                            if purged:
                                log.info('Purged %s expired entity dossiers', purged)
                        finally:
                            conn.close()
                        last_purge = time.time()
                except Exception:
                    log.exception('Entity research worker will retry')
                _wake.wait(5)
                _wake.clear()
        _worker = threading.Thread(target=run, daemon=True, name='entity-research')
        _worker.start()
        return _worker
