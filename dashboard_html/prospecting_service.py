"""CSV work lists kept outside CRM contacts until an explicit promotion.

Every lookup is scoped to the assignee (team admins can see their team's lists).
Original columns use stable positional IDs so duplicate/blank headers lose no data.
"""
import csv
import hashlib
import io
import json
import re
from contextlib import contextmanager
from datetime import date, datetime, timezone
from uuid import UUID

from psycopg2.extras import Json, execute_values

import crm_service as crm

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 10000
MAX_COLUMNS = 100
MAX_CELL = 10000
STATUSES = {
    'new': 'Not contacted', 'attempted': 'Attempted', 'connected': 'Connected',
    'interested': 'Interested', 'nurture': 'Follow up later',
    'not_interested': 'Not interested', 'do_not_contact': 'Do not contact',
}
FIELDS = {
    'name': 'Contact name', 'company': 'Company', 'title': 'Role / title',
    'phone': 'Phone', 'secondary_phone': 'Second phone', 'email': 'Email',
    'address': 'Address', 'notes': 'Notes',
}
ALIASES = {
    'name': ['best contact', 'contact name', 'full name', 'person', 'contact', 'name'],
    'company': ['manager or operator', 'company name', 'company', 'organization', 'business name', 'property manager', 'manager'],
    'title': ['best contact role', 'job title', 'contact role', 'role', 'title', 'position'],
    'phone': ['primary phone', 'phone number', 'phone', 'telephone', 'mobile', 'cell'],
    'secondary_phone': ['secondary phone', 'phone 2', 'alternate phone', 'other phone'],
    'email': ['email', 'email address', 'contact email', 'primary email'],
    'address': ['normalized address', 'street address', 'property address', 'address', 'input address'],
    'notes': ['verification notes', 'notes', 'comments', 'description'],
}

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS prospect_lists (
        id SERIAL PRIMARY KEY, name VARCHAR(200) NOT NULL,
        filename TEXT NOT NULL, columns JSONB NOT NULL, mapping JSONB NOT NULL,
        import_key UUID NOT NULL, import_hash TEXT NOT NULL,
        team_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        added_by_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        UNIQUE (added_by_id, import_key)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_prospect_lists_scope
       ON prospect_lists(team_id, added_by_id, created_at DESC)""",
    # Backfill only when the column is first introduced. A deleted assignee must
    # not silently restore the uploader's access on a subsequent worker restart.
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name='prospect_lists'
              AND column_name='assigned_to_id') THEN
            ALTER TABLE prospect_lists ADD COLUMN assigned_to_id INTEGER REFERENCES users(id) ON DELETE SET NULL;
            UPDATE prospect_lists SET assigned_to_id=added_by_id;
        END IF;
    END $$""",
    """ALTER TABLE prospect_lists ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1""",
    """CREATE INDEX IF NOT EXISTS idx_prospect_lists_assignee
       ON prospect_lists(team_id, assigned_to_id, created_at DESC)""",
    # Older workers can finish an upload during a rolling deployment. Supply the
    # uploader on insert only, without changing intentionally unassigned old rows.
    """CREATE OR REPLACE FUNCTION prospect_default_assignee() RETURNS trigger AS $$
       BEGIN
           IF NEW.assigned_to_id IS NULL THEN NEW.assigned_to_id := NEW.added_by_id; END IF;
           RETURN NEW;
       END; $$ LANGUAGE plpgsql""",
    """CREATE OR REPLACE TRIGGER prospect_default_assignee_trigger
       BEFORE INSERT ON prospect_lists FOR EACH ROW EXECUTE FUNCTION prospect_default_assignee()""",
    """CREATE TABLE IF NOT EXISTS prospect_rows (
        id SERIAL PRIMARY KEY, list_id INTEGER NOT NULL REFERENCES prospect_lists(id) ON DELETE CASCADE,
        position INTEGER NOT NULL, cells JSONB NOT NULL, original_cells JSONB NOT NULL,
        status TEXT NOT NULL DEFAULT 'new' CHECK (status IN
            ('new','attempted','connected','interested','nurture','not_interested','do_not_contact')),
        notes TEXT NOT NULL DEFAULT '', next_follow_up DATE,
        last_touch_at TIMESTAMP, touch_count INTEGER NOT NULL DEFAULT 0,
        promoted_contact_id INTEGER REFERENCES crm_contacts(id) ON DELETE SET NULL,
        promoted_at TIMESTAMP, version INTEGER NOT NULL DEFAULT 1,
        updated_at TIMESTAMP NOT NULL DEFAULT NOW(), UNIQUE(list_id, position)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_prospect_rows_status ON prospect_rows(list_id, status)""",
    """CREATE INDEX IF NOT EXISTS idx_prospect_rows_due ON prospect_rows(list_id, next_follow_up)""",
    """CREATE TABLE IF NOT EXISTS prospect_touches (
        id SERIAL PRIMARY KEY, row_id INTEGER NOT NULL REFERENCES prospect_rows(id) ON DELETE CASCADE,
        request_key UUID NOT NULL, method TEXT NOT NULL, outcome TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
        occurred_at TIMESTAMP NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, UNIQUE(row_id, request_key)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_prospect_touches_row ON prospect_touches(row_id, occurred_at DESC)""",
]


class Conflict(ValueError):
    pass


@contextmanager
def transaction():
    conn = crm.get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                yield cur
    finally:
        conn.close()


def scope(ctx):
    sql = 'l.team_id = %(team_id)s'
    return sql if ctx['is_admin'] else sql + ' AND l.assigned_to_id = %(user_id)s'


LIST_PEOPLE_SQL = f"""
    (SELECT {crm._user_name_sql('u')} FROM users u WHERE u.id=l.assigned_to_id) AS assigned_to_name,
    (SELECT {crm._user_name_sql('u')} FROM users u WHERE u.id=l.added_by_id) AS added_by_name
"""


def resolve_assignee(ctx, value, cur):
    """Use the existing active sponsorship roster, never a client-supplied team."""
    if value in (None, ''):
        value = ctx['user_id']
    if isinstance(value, bool) or not re.fullmatch(r'[1-9][0-9]*', str(value)):
        raise ValueError('Choose a valid list owner.')
    assignee = int(value)
    if not ctx['is_admin'] and assignee != ctx['user_id']:
        raise PermissionError('Only a team admin can assign a list to someone else.')
    if assignee != ctx['team_id']:
        cur.execute("""SELECT member_user_id FROM account_sponsorships
            WHERE sponsor_user_id=%s AND member_user_id=%s AND status='active' FOR SHARE""",
            (ctx['team_id'], assignee))
        if not cur.fetchone():
            raise ValueError('Choose yourself or an active member of your team.')
    return assignee


def clean_text(value, limit, label):
    if not isinstance(value, str) or '\x00' in value or len(value) > limit:
        raise ValueError(f'{label} must be text, up to {limit:,} characters.')
    return value


def key(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Invalid request key. Reload and try again.') from None


def parse_csv(content, *, delimiter='auto', has_header=True):
    if not content or len(content) > MAX_BYTES:
        raise ValueError('Choose a CSV or TSV file up to 10 MB.')
    if content.startswith(b'PK\x03\x04'):
        raise ValueError('Save this workbook as CSV or TSV, then upload that file.')
    try:
        if content.startswith((b'\xff\xfe', b'\xfe\xff')):
            text = content.decode('utf-16')
        else:
            try:
                text = content.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = content.decode('cp1252')
    except UnicodeError:
        raise ValueError('Could not read this text file. Save it as UTF-8 CSV.') from None
    if '\x00' in text:
        raise ValueError('This is not a supported text file. Save it as CSV or TSV.')
    # Excel's optional separator hint is metadata, never a lead or a header.
    if re.match(r'^sep=[,;\t|]\r?\n', text, flags=re.I):
        hint, text = text.split('\n', 1)
        if delimiter == 'auto':
            delimiter = hint[4]
    if delimiter == 'auto':
        try:
            delimiter = csv.Sniffer().sniff(text[:65536], delimiters=',;\t|').delimiter
        except csv.Error:
            delimiter = ','
    if delimiter not in (',', ';', '\t', '|'):
        raise ValueError('Choose a supported delimiter.')
    rows = []
    try:
        for row in csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True):
            if not any(value.strip() for value in row):
                continue
            if len(row) > MAX_COLUMNS or any(len(value) > MAX_CELL for value in row):
                raise ValueError('Use at most 100 columns and 10,000 characters per cell.')
            rows.append(row)
            if len(rows) > MAX_ROWS + int(has_header):
                raise ValueError('A list can contain up to 10,000 rows. Split larger files first.')
    except csv.Error as exc:
        raise ValueError(f'Could not parse the CSV: {exc}. Check the delimiter and quoted cells.') from None
    if not rows or (has_header and len(rows) < 2):
        raise ValueError('The file has no data rows.')
    headers = rows.pop(0) if has_header else []
    width = max([len(headers)] + [len(row) for row in rows])
    columns = [{'id': f'c{i}', 'label': (headers[i].strip() if i < len(headers) else '') or f'Column {i + 1}'} for i in range(width)]
    cells = [{f'c{i}': row[i] if i < len(row) else '' for i in range(width)} for row in rows]
    normalized = {c['id']: re.sub(r'[^a-z0-9]+', ' ', c['label'].lower()).strip() for c in columns}
    mapping = {}
    for field, aliases in ALIASES.items():
        mapping[field] = next((cid for alias in aliases for cid, label in normalized.items() if label == alias), '')
    return {'columns': columns, 'rows': cells, 'mapping': mapping, 'delimiter': delimiter,
            'row_count': len(cells), 'duplicate_count': len(cells) - len({json.dumps(r, sort_keys=True) for r in cells})}


def validate_mapping(mapping, columns):
    if not isinstance(mapping, dict) or set(mapping) - set(FIELDS):
        raise ValueError('Invalid column mapping.')
    ids = {c['id'] for c in columns}
    if any(not isinstance(v, str) or (v and v not in ids) for v in mapping.values()):
        raise ValueError('A mapped column is missing from the file.')
    return {field: mapping.get(field, '') for field in FIELDS}


def mapped(row, listing):
    return {field: row['cells'].get(listing['mapping'].get(field), '').strip() for field in FIELDS}


def create_list(ctx, parsed, *, name, filename, mapping, import_key, assigned_to_id=None):
    name = clean_text(name, 200, 'List name').strip()
    if not name:
        raise ValueError('Give the list a name.')
    mapping = validate_mapping(mapping, parsed['columns'])
    import_key = key(import_key)
    with transaction() as cur:
        assignee = resolve_assignee(ctx, assigned_to_id, cur)
        digest = hashlib.sha256(json.dumps([name, parsed['columns'], parsed['rows'], mapping, assignee], sort_keys=True).encode()).hexdigest()
        cur.execute("""INSERT INTO prospect_lists (name,filename,columns,mapping,import_key,import_hash,team_id,added_by_id,assigned_to_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (added_by_id,import_key) DO NOTHING RETURNING id""",
            (name, filename[:255], Json(parsed['columns']), Json(mapping), import_key, digest, ctx['team_id'], ctx['user_id'], assignee))
        row = cur.fetchone()
        if not row:
            cur.execute('SELECT id, import_hash, team_id FROM prospect_lists WHERE added_by_id=%s AND import_key=%s', (ctx['user_id'], import_key))
            existing = cur.fetchone()
            if existing['import_hash'] != digest or existing['team_id'] != ctx['team_id']:
                raise Conflict('This upload already completed with different settings. Start a new import.')
            get_list(ctx, existing['id'], cur)
            return existing['id']
        execute_values(cur, 'INSERT INTO prospect_rows(list_id,position,cells,original_cells) VALUES %s',
                       [(row['id'], i + 1, Json(cells), Json(cells)) for i, cells in enumerate(parsed['rows'])], page_size=500)
        return row['id']


def get_list(ctx, list_id, cur, *, for_update=False):
    # Hold the permission decision until the transaction ends. Reassignment waits
    # for in-flight reads/writes, and later requests recheck the new owner.
    lock = 'UPDATE' if for_update else 'SHARE'
    cur.execute(f'SELECT l.*, {LIST_PEOPLE_SQL} FROM prospect_lists l WHERE l.id=%(id)s AND {scope(ctx)} FOR {lock} OF l', {**ctx, 'id': list_id})
    result = cur.fetchone()
    if not result:
        raise LookupError('List not found.')
    return dict(result)


def assign_list(ctx, list_id, *, assigned_to_id, version):
    if not ctx['is_admin']:
        raise PermissionError('Only a team admin can reassign lists.')
    with transaction() as cur:
        listing = get_list(ctx, list_id, cur, for_update=True)
        if version != listing['version']:
            raise Conflict('This list was reassigned in another window. Reload before changing its owner.')
        assignee = resolve_assignee(ctx, assigned_to_id, cur)
        if assignee != listing['assigned_to_id']:
            cur.execute('UPDATE prospect_lists SET assigned_to_id=%s,version=version+1 WHERE id=%s', (assignee, list_id))
            # Invalidate row drafts even if an owner is later assigned the sheet again.
            cur.execute('UPDATE prospect_rows SET version=version+1 WHERE list_id=%s', (list_id,))
            crm.audit_change(cur, ctx, 'prospect_list', list_id, listing['name'], 'assigned',
                             'assigned_to_id', old_value=listing['assigned_to_id'], new_value=assignee)
        return get_list(ctx, list_id, cur)


def list_lists(ctx):
    with transaction() as cur:
        cur.execute(f"""SELECT l.id,l.name,l.filename,l.created_at,l.added_by_id,l.assigned_to_id,l.version,
            {LIST_PEOPLE_SQL},COUNT(r.id) AS row_count,
            COUNT(r.id) FILTER (WHERE r.promoted_at IS NOT NULL) AS promoted_count,
            COUNT(r.id) FILTER (WHERE r.next_follow_up<=%(today)s AND r.promoted_at IS NULL
                AND r.status NOT IN ('do_not_contact','not_interested')) AS due_count
            FROM prospect_lists l LEFT JOIN prospect_rows r ON r.list_id=l.id
            WHERE {scope(ctx)} GROUP BY l.id ORDER BY l.created_at DESC,l.id DESC""", {**ctx, 'today': crm.ny_today()})
        return [dict(r) for r in cur.fetchall()]


def list_rows(ctx, list_id, *, q='', status='', due=False, sort='position', page=1):
    with transaction() as cur:
        listing = get_list(ctx, list_id, cur)
        where = ['r.list_id=%(id)s']
        params = {'id': list_id, 'today': crm.ny_today()}
        if q:
            where.append("(r.cells::text ILIKE %(q)s ESCAPE '\\' OR r.notes ILIKE %(q)s ESCAPE '\\')")
            params['q'] = '%' + q[:200].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        if status == 'promoted':
            where.append('r.promoted_at IS NOT NULL')
        elif status == 'active':
            where.append('r.promoted_at IS NULL')
        elif status in STATUSES:
            where.append('r.status=%(status)s AND r.promoted_at IS NULL')
            params['status'] = status
        if due:
            where.append("r.next_follow_up<=%(today)s AND r.promoted_at IS NULL AND r.status NOT IN ('do_not_contact','not_interested')")
        orders = {'position': 'r.position', 'last_touch': 'r.last_touch_at DESC NULLS LAST',
                  'follow_up': 'r.next_follow_up ASC NULLS LAST', 'status': 'r.status', 'name': 'LOWER(r.cells->>%(name_column)s)'}
        params['name_column'] = listing['mapping'].get('name') or listing['columns'][0]['id']
        clause = ' AND '.join(where)
        cur.execute(f'SELECT COUNT(*) AS n FROM prospect_rows r WHERE {clause}', params)
        total = cur.fetchone()['n']
        page = max(1, min(page, max(1, (total + 49) // 50)))
        params['offset'] = (page - 1) * 50
        cur.execute(f'SELECT r.* FROM prospect_rows r WHERE {clause} ORDER BY {orders.get(sort, orders["position"])},r.id LIMIT 50 OFFSET %(offset)s', params)
        rows = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE promoted_at IS NOT NULL) AS promoted,
            COUNT(*) FILTER (WHERE touch_count>0) AS touched,
            COUNT(*) FILTER (WHERE next_follow_up<=%s AND promoted_at IS NULL
                AND status NOT IN ('do_not_contact','not_interested')) AS due
            FROM prospect_rows WHERE list_id=%s""", (crm.ny_today(), list_id))
        return {'listing': listing, 'rows': rows, 'total': total, 'page': page,
                'pages': max(1, (total + 49) // 50), 'summary': dict(cur.fetchone())}


def get_row(ctx, row_id, cur, *, lock=False):
    cur.execute(f"""SELECT r.list_id FROM prospect_rows r JOIN prospect_lists l ON l.id=r.list_id
        WHERE r.id=%(id)s AND {scope(ctx)}""", {**ctx, 'id': row_id})
    ref = cur.fetchone()
    if not ref:
        raise LookupError('Lead not found.')
    # Always lock list before row, matching reassignment's lock order.
    get_list(ctx, ref['list_id'], cur)
    cur.execute(f"SELECT * FROM prospect_rows WHERE id=%s {'FOR UPDATE' if lock else ''}", (row_id,))
    return dict(cur.fetchone())


def check_edit(row, version):
    if row['promoted_at']:
        raise Conflict('This lead has been added to CRM. Continue tracking it there.')
    if version != row['version']:
        raise Conflict('This lead changed in another window. Reload before saving.')


def parse_date(value):
    if value in (None, ''):
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError('Choose a valid follow-up date.') from None


def update_row(ctx, row_id, data):
    with transaction() as cur:
        row = get_row(ctx, row_id, cur, lock=True)
        check_edit(row, data.get('version'))
        listing = get_list(ctx, row['list_id'], cur)
        cells = data.get('cells', {})
        if not isinstance(cells, dict) or set(cells) - {c['id'] for c in listing['columns']}:
            raise ValueError('Invalid column.')
        for cid, value in cells.items():
            row['cells'][cid] = clean_text(value, MAX_CELL, 'Cell')
        status = data.get('status', row['status'])
        if status not in STATUSES:
            raise ValueError('Choose a valid status.')
        notes = clean_text(data.get('notes', row['notes']), 20000, 'Notes')
        follow_up = parse_date(data['next_follow_up']) if 'next_follow_up' in data else row['next_follow_up']
        if status in ('do_not_contact', 'not_interested'):
            follow_up = None
        cur.execute("""UPDATE prospect_rows SET cells=%s,status=%s,notes=%s,next_follow_up=%s,
            version=version+1,updated_at=NOW() WHERE id=%s RETURNING *""", (Json(row['cells']), status, notes, follow_up, row_id))
        return dict(cur.fetchone())


def row_detail(ctx, row_id):
    with transaction() as cur:
        row = get_row(ctx, row_id, cur)
        listing = get_list(ctx, row['list_id'], cur)
        row['crm_contact_restricted'] = False
        if row['promoted_contact_id']:
            cur.execute(f'SELECT id FROM crm_contacts c WHERE id=%(id)s AND {crm.record_scope_sql(ctx, "c")}', {**ctx, 'id': row['promoted_contact_id']})
            if not cur.fetchone():
                row['promoted_contact_id'] = None
                row['crm_contact_restricted'] = True
        cur.execute('SELECT * FROM prospect_touches WHERE row_id=%s ORDER BY occurred_at DESC,id DESC', (row_id,))
        return {'row': row, 'listing': listing, 'fields': mapped(row, listing), 'touches': [dict(t) for t in cur.fetchall()]}


def add_touch(ctx, row_id, data):
    request_key = key(data.get('request_key'))
    method, outcome = data.get('method'), data.get('outcome')
    if method not in crm.CONTACT_METHODS or outcome not in crm.CONTACT_OUTCOMES:
        raise ValueError('Choose a contact method and outcome.')
    note = clean_text(data.get('note', ''), 20000, 'Touch notes')
    try:
        occurred = datetime.fromisoformat(data['occurred_at'])
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=crm.NY_TZ)
        occurred = occurred.astimezone(timezone.utc).replace(tzinfo=None)
        if occurred > datetime.now(timezone.utc).replace(tzinfo=None) or occurred.year < 2000:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise ValueError('Choose a past touch date and time (New York time).') from None
    with transaction() as cur:
        row = get_row(ctx, row_id, cur, lock=True)
        cur.execute('SELECT id FROM prospect_touches WHERE row_id=%s AND request_key=%s', (row_id, request_key))
        if cur.fetchone():
            return row
        check_edit(row, data.get('version'))
        if row['status'] == 'do_not_contact':
            raise Conflict('This lead is marked Do not contact. Change that status before logging outreach.')
        status = data.get('status', row['status'])
        if status not in STATUSES:
            raise ValueError('Choose a valid status.')
        follow_up = parse_date(data.get('next_follow_up'))
        if status in ('do_not_contact', 'not_interested'):
            follow_up = None
        cur.execute("""INSERT INTO prospect_touches(row_id,request_key,method,outcome,note,occurred_at,user_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""", (row_id, request_key, method, outcome, note, occurred, ctx['user_id']))
        cur.execute("""UPDATE prospect_rows SET touch_count=touch_count+1,
            last_touch_at=GREATEST(last_touch_at,%s),status=%s,next_follow_up=%s,version=version+1,updated_at=NOW()
            WHERE id=%s RETURNING *""", (occurred, status, follow_up, row_id))
        return dict(cur.fetchone())


def promote(ctx, row_id, data):
    """Copy a vetted lead, provenance, history and follow-up atomically; retry-safe.

    A shared switchboard alone never identifies a person. Reuse requires the same
    name AND matching email/phone. Do not reveal records assigned to another rep.
    """
    with transaction() as cur:
        row = get_row(ctx, row_id, cur, lock=True)
        if row['promoted_at']:
            if not row['promoted_contact_id']:
                raise Conflict('The promoted CRM contact was deleted.')
            cur.execute(f'SELECT id FROM crm_contacts c WHERE id=%(id)s AND {crm.record_scope_sql(ctx, "c")}', {**ctx, 'id': row['promoted_contact_id']})
            if not cur.fetchone():
                raise Conflict('The CRM contact is now assigned to another rep.')
            return {'contact_id': row['promoted_contact_id'], 'existing': True}
        check_edit(row, data.get('version'))
        if row['status'] == 'do_not_contact':
            raise Conflict('This lead is marked Do not contact. Review that status before adding it to CRM.')
        listing = get_list(ctx, row['list_id'], cur)
        if not listing['assigned_to_id']:
            raise Conflict('Assign this list to an active team member before adding contacts to CRM.')
        resolve_assignee(ctx, listing['assigned_to_id'], cur)
        fields = mapped(row, listing)
        for field, limit in [('name', 255), ('company', 255), ('title', 150), ('email', 255), ('phone', 150), ('secondary_phone', 150)]:
            fields[field] = clean_text(data.get(field, fields[field]), limit, FIELDS[field]).strip()
        if not fields['name']:
            raise ValueError('Enter a contact or company name before adding to CRM.')
        if fields['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', fields['email']):
            raise ValueError('Enter one valid email address, or leave it blank. The original values stay in the research note.')
        phones = []
        for field in ('phone', 'secondary_phone'):
            raw = fields[field]
            if raw:
                digits = crm.normalize_phone_digits(raw)
                if not digits or len(digits) < 7:
                    raise ValueError('Enter a valid phone number, or leave it blank.')
                phones.append((raw, digits, crm.split_phone_extension(raw)[1]))
        # Serialize promotions for the team, including promotions from other lists.
        cur.execute('SELECT pg_advisory_xact_lock(875213,%s)', (ctx['team_id'],))
        cur.execute("""SELECT DISTINCT c.* FROM crm_contacts c LEFT JOIN crm_phones p ON p.contact_id=c.id
            WHERE c.team_id=%s AND LOWER(TRIM(c.name))=LOWER(%s)
            AND ((%s<>'' AND LOWER(c.email)=LOWER(%s)) OR p.digits=ANY(%s)) ORDER BY c.id""",
            (ctx['team_id'], fields['name'], fields['email'], fields['email'], [p[1] for p in phones]))
        candidates = cur.fetchall()
        if len(candidates) > 1:
            raise Conflict('Several CRM contacts match this lead. Resolve the duplicates in CRM before adding it.')
        existing = candidates[0] if candidates else None
        if existing and not crm.row_visible(ctx, existing):
            raise Conflict('A matching contact is assigned to another rep. Ask your team admin to review it.')
        if existing and existing['do_not_contact']:
            raise Conflict('The matching CRM contact is marked Do not contact.')
        source = f"Prospecting: {listing['name']} (row {row['position']})"[:255]
        if existing:
            contact_id = existing['id']
        else:
            cur.execute("""INSERT INTO crm_contacts(name,title,company,email,source,source_detail,assigned_to_id,added_by_id,team_id)
                VALUES (%s,%s,%s,%s,'import',%s,%s,%s,%s) RETURNING id""",
                (fields['name'], fields['title'] or None, fields['company'] or None, fields['email'] or None,
                 source, listing['assigned_to_id'], ctx['user_id'], ctx['team_id']))
            contact_id = cur.fetchone()['id']
        cur.execute('SELECT EXISTS(SELECT 1 FROM crm_phones WHERE contact_id=%s AND is_primary) AS present', (contact_id,))
        has_primary = cur.fetchone()['present']
        for i, (raw, digits, extension) in enumerate(phones):
            cur.execute("""INSERT INTO crm_phones(contact_id,number,digits,extension,is_primary,source,source_detail,added_by_id)
                VALUES (%s,%s,%s,%s,%s,'import',%s,%s) ON CONFLICT (contact_id,digits) DO NOTHING""",
                (contact_id, crm.format_phone(digits), digits, extension, i == 0 and not has_primary, source, ctx['user_id']))
        # Always preserve the complete research, including unmapped fields and sources.
        research = [f"Imported from prospecting list: {listing['name']}", f"File: {listing['filename']}, row {row['position']}",
                    f"Prospecting status: {STATUSES[row['status']]}"]
        research.extend(f"{c['label']}: {row['cells'].get(c['id'], '')}" for c in listing['columns'])
        if row['notes']:
            research.append('Working notes: ' + row['notes'])
        cur.execute("""INSERT INTO crm_activity(type,note,contact_id,user_id,team_id,meta)
            VALUES ('note',%s,%s,%s,%s,%s)""",
            ('\n'.join(research), contact_id, ctx['user_id'], ctx['team_id'], Json({'prospect_row_id': row_id, 'original_cells': row['original_cells']})))
        cur.execute("""INSERT INTO crm_activity(type,method,outcome,note,contact_id,user_id,team_id,created_at,meta)
            SELECT 'contacted',method,outcome,note,%s,COALESCE(user_id,%s),%s,occurred_at,
                   jsonb_build_object('prospect_touch_id',id,'prospect_row_id',row_id)
            FROM prospect_touches WHERE row_id=%s""", (contact_id, ctx['user_id'], ctx['team_id'], row_id))
        cur.execute('UPDATE crm_contacts SET last_contacted_at=GREATEST(last_contacted_at,%s),updated_at=NOW() WHERE id=%s', (row['last_touch_at'], contact_id))
        if row['next_follow_up']:
            cur.execute("""INSERT INTO crm_follow_ups(title,note,due_date,contact_id,assigned_to_id,created_by_id,team_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""", ('Follow up: ' + fields['name'][:240], source, row['next_follow_up'],
                contact_id, (existing['assigned_to_id'] or listing['assigned_to_id']) if existing else listing['assigned_to_id'], ctx['user_id'], ctx['team_id']))
        crm.audit_change(cur, ctx, 'contact', contact_id, fields['name'], 'updated' if existing else 'created',
                         new_value={'source': 'prospecting', 'prospect_row_id': row_id})
        cur.execute('UPDATE prospect_rows SET promoted_contact_id=%s,promoted_at=NOW(),version=version+1,updated_at=NOW() WHERE id=%s', (contact_id, row_id))
        return {'contact_id': contact_id, 'existing': bool(existing)}


def export_rows(ctx, list_id):
    with transaction() as cur:
        listing = get_list(ctx, list_id, cur)
        cur.execute('SELECT * FROM prospect_rows WHERE list_id=%s ORDER BY position', (list_id,))
        rows = cur.fetchall()
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    def safe(value):
        text = str(value) if value is not None else ''
        return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else text
    writer.writerow([safe(c['label']) for c in listing['columns']] + ['Contact status', 'Last touch (UTC)', 'Touch count', 'Next follow-up', 'Working notes', 'CRM contact ID'])
    for row in rows:
        writer.writerow([safe(row['cells'].get(c['id'], '')) for c in listing['columns']] +
                        [STATUSES[row['status']], str(row['last_touch_at'] or ''), row['touch_count'],
                         str(row['next_follow_up'] or ''), safe(row['notes']), row['promoted_contact_id'] or ''])
    return '\ufeff' + output.getvalue()
