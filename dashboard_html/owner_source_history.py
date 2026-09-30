"""Source observations and changes, committed with the source-owned facts.

These are reported contacts, not assertions that somebody lives at an address.
Fetch timestamps are deliberately separate from dates published by a source.
"""
import json
from datetime import date, datetime, timezone

from psycopg2.extras import Json

from owner_source_dates import source_date


SOURCES = {
    'hpd': 'HPD registration', 'acris': 'ACRIS deed', 'pluto': 'NYC PLUTO',
    'rpad': 'Historical RPAD assessment', 'ecb': 'ECB respondent',
    'sos': 'NY Secretary of State',
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS owner_source_snapshots (
    building_id INTEGER NOT NULL REFERENCES buildings(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    snapshot JSONB NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revision BIGINT NOT NULL DEFAULT 1,
    PRIMARY KEY (building_id, source)
);
ALTER TABLE owner_source_snapshots ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 1;
CREATE TABLE IF NOT EXISTS owner_source_history (
    id BIGSERIAL PRIMARY KEY,
    building_id INTEGER NOT NULL REFERENCES buildings(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('baseline','change')),
    reported_date DATE,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    before JSONB,
    after JSONB NOT NULL,
    changes JSONB NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS owner_source_history_building
    ON owner_source_history(building_id, observed_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS owner_source_jobs (
    id BIGSERIAL PRIMARY KEY,
    building_id INTEGER NOT NULL REFERENCES buildings(id) ON DELETE CASCADE,
    bbl TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('hpd','acris','pluto','rpad','ecb','sos')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','completed','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    requested_by INTEGER NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_at TIMESTAMPTZ,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (building_id, source)
);
CREATE INDEX IF NOT EXISTS owner_source_jobs_due
    ON owner_source_jobs(status, available_at);
"""


def serial(value):
    if isinstance(value, datetime):
        value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return value.isoformat().replace('+00:00', 'Z')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: serial(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(item) for item in value]
    return value


def _value(row, key):
    return row.get(key) if isinstance(row, dict) else row[0]


def _record(name, role, **values):
    if not str(name or '').strip():
        return None
    return {'name': str(name).strip(), 'role': role,
            **{key: serial(value) for key, value in values.items()}}


def make_snapshot(building, source, deed_parties=()):
    """Whitelist ownership evidence; never archive vendor profiles or payloads."""
    b = building
    records = []
    reported = None
    period = None
    if source == 'acris':
        reported = b.get('sale_recorded_date')
        for party in deed_parties:
            records.append(_record(party.get('party_name'), party.get('party_type'),
                address=party.get('address_1'), address_2=party.get('address_2'),
                city=party.get('city'), state=party.get('state'), zip_code=party.get('zip_code'),
                record_id=party.get('document_id'), reported_date=party.get('recorded_date')))
        if not records:
            records.append(_record(b.get('sale_buyer_primary'), 'deed grantee',
                record_id=b.get('sale_crfn'), reported_date=reported))
    elif source == 'hpd':
        reported = b.get('hpd_last_registration_date')
        for contact in b.get('hpd_owner_contacts') or []:
            if isinstance(contact, dict):
                records.append(_record(contact.get('name'), contact.get('role'),
                    **{key: contact.get(key) for key in (
                        'address', 'city', 'state', 'zip_code', 'reported_date',
                        'registration_id', 'contact_id')}))
        if not records:
            # Old aggregated names do not establish which individual an address belongs to.
            records.append(_record(b.get('owner_name_hpd'), 'registered owner',
                registration_id=b.get('hpd_registration_id'), reported_date=reported))
        for field, role in (('hpd_agent_name', 'managing agent'), ('hpd_site_manager_name', 'site manager')):
            records.append(_record(b.get(field), role, reported_date=reported))
    elif source == 'pluto':
        period = b.get('pluto_version')
        records.append(_record(b.get('current_owner_name'), 'tax-lot owner'))
    elif source == 'rpad':
        period = ' '.join(str(b.get(key) or '').strip() for key in
            ('rpad_assessment_year', 'rpad_assessment_period')).strip() or None
        records.append(_record(b.get('owner_name_rpad'), 'historical assessment owner'))
    elif source == 'ecb':
        reported = b.get('ecb_respondent_issue_date')
        records.append(_record(b.get('ecb_respondent_name'), 'violation respondent',
            address=b.get('ecb_respondent_address'), city=b.get('ecb_respondent_city'),
            zip_code=b.get('ecb_respondent_zip'), reported_date=reported))
    elif source == 'sos':
        records.append(_record(b.get('sos_entity_name'), 'registered entity',
            record_id=b.get('sos_dos_id'), status=b.get('sos_entity_status')))
        records.append(_record(b.get('sos_principal_name'), b.get('sos_principal_title'),
            entity_name=b.get('sos_entity_name'), record_id=b.get('sos_dos_id'),
            address=b.get('sos_principal_street'), city=b.get('sos_principal_city'),
            state=b.get('sos_principal_state'), zip_code=b.get('sos_principal_zip')))
    else:
        raise ValueError('Unknown owner source')
    records = [record for record in records if record]
    # Dataset response order and duplicate rows must not manufacture changes.
    records = [json.loads(item) for item in sorted({json.dumps(record, sort_keys=True) for record in records})]
    parsed = source_date(reported)
    return {'records': records, 'reported_date': parsed.isoformat() if parsed else None,
            'period': period}


def capture_source_snapshot(cur, building_id, source):
    """Lock the property before replacing its facts and reading related parties."""
    cur.execute('SELECT to_jsonb(b) AS building FROM buildings b WHERE id=%s FOR UPDATE', (building_id,))
    row = cur.fetchone()
    if row is None:
        raise LookupError('Property not found')
    building = _value(row, 'building')
    parties = []
    if source == 'acris':
        cur.execute("""SELECT to_jsonb(p) || jsonb_build_object(
                'document_id',t.document_id,'recorded_date',t.recorded_date) AS party
            FROM acris_parties p JOIN acris_transactions t ON t.id=p.transaction_id
            WHERE p.building_id=%s AND t.is_primary_deed=TRUE AND p.party_type='buyer'""", (building_id,))
        parties = [_value(row, 'party') for row in cur.fetchall()]
    return make_snapshot(building, source, parties)


def source_revisions(conn, building_ids, source):
    """Capture optimistic revisions before network I/O, ending the read transaction."""
    with conn.cursor() as cur:
        cur.execute('SELECT building_id,revision FROM owner_source_snapshots '
                    'WHERE building_id=ANY(%s) AND source=%s', (list(building_ids), source))
        rows = cur.fetchall()
        revisions = {row['building_id']: row['revision'] for row in rows} if rows and isinstance(rows[0], dict) else dict(rows)
    conn.commit()
    return {building_id: revisions.get(building_id) for building_id in building_ids}


def source_revision_matches(cur, building_id, source, expected):
    """Caller holds the property row lock from capture_source_snapshot."""
    cur.execute('SELECT revision FROM owner_source_snapshots WHERE building_id=%s AND source=%s',
                (building_id, source))
    row = cur.fetchone()
    return (_value(row, 'revision') if row else None) == expected


def snapshot_changes(before, after):
    """Dates/IDs alone are a new observation, not a name/address change."""
    def facts(snapshot):
        ignored = {'reported_date', 'record_id', 'registration_id', 'contact_id'}
        return sorted({json.dumps({key: value for key, value in record.items()
                       if key not in ignored and value not in (None, '')}, sort_keys=True)
                       for record in (snapshot or {}).get('records', [])})
    if facts(before) == facts(after):
        return []
    return [{'field': 'Reported contacts', 'before': (before or {}).get('records', []),
             'after': after.get('records', [])}]


def record_source_snapshot(cur, building_id, source, before):
    """Call in the SAME transaction as a successful adapter update; never commit here."""
    after = capture_source_snapshot(cur, building_id, source)
    cur.execute('SELECT snapshot FROM owner_source_snapshots WHERE building_id=%s AND source=%s',
                (building_id, source))
    previous = cur.fetchone()
    if previous is None:
        # Existing pre-deployment facts are a baseline. A first-ever successful
        # fetch is also a baseline, never an inferred transfer of ownership.
        baseline = before if before.get('records') else after
        cur.execute('''INSERT INTO owner_source_history
            (building_id,source,kind,reported_date,after) VALUES (%s,%s,'baseline',%s,%s)''',
            (building_id, source, baseline.get('reported_date'), Json(baseline)))
        changes = snapshot_changes(baseline, after)
    else:
        changes = snapshot_changes(before, after)
    if changes:
        cur.execute('''INSERT INTO owner_source_history
            (building_id,source,kind,reported_date,before,after,changes)
            VALUES (%s,%s,'change',%s,%s,%s,%s)''',
            (building_id, source, after.get('reported_date'), Json(before), Json(after), Json(changes)))
    cur.execute('''INSERT INTO owner_source_snapshots (building_id,source,snapshot)
        VALUES (%s,%s,%s) ON CONFLICT (building_id,source)
        DO UPDATE SET snapshot=EXCLUDED.snapshot,checked_at=NOW(),revision=owner_source_snapshots.revision+1''',
        (building_id, source, Json(after)))
    if source not in ('pluto', 'rpad', 'hpd'):
        # Automated adapters must clear an earlier manual-refresh failure too.
        cur.execute("""INSERT INTO building_source_refresh
            (building_id,source,attempted_at,checked_at,next_attempt_at,error,owner_dates_version)
            VALUES (%s,%s,NOW(),NOW(),NOW()+INTERVAL '5 minutes',NULL,1)
            ON CONFLICT (building_id,source) DO UPDATE SET attempted_at=NOW(),checked_at=NOW(),
            next_attempt_at=EXCLUDED.next_attempt_at,error=NULL,owner_dates_version=1""", (building_id, source))
    return changes
