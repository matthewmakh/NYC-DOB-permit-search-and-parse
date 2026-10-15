"""Bulk edits, reversible row changes, column management and duplicate review.

All mutations lock the list before its rows. A batch either succeeds in full or
rolls back; stale versions, reassignment and promotion cannot silently lose work.
"""
import copy
import hashlib
import json
import re
from collections import defaultdict
from functools import lru_cache
from heapq import nsmallest
from datetime import date, datetime, timezone

from psycopg2.extras import Json

import crm_service as crm
import prospecting_service as s

EDIT_FIELDS = ('cells', 'status', 'notes', 'next_follow_up', 'archived_at', 'research')
MAX_BATCH = 500


def snapshot(row):
    def serial(value):
        return value.isoformat() if isinstance(value, (date, datetime)) else value
    return {k: serial(copy.deepcopy(row[k])) for k in ('id', 'version', *EDIT_FIELDS)}


def record_change(cur, ctx, list_id, label, before, after, request_key=None, request_hash=None):
    cur.execute("""INSERT INTO prospect_changes(list_id,user_id,label,before_rows,after_rows,request_key,request_hash)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (list_id, ctx['user_id'], label, Json([snapshot(r) for r in before]), Json([snapshot(r) for r in after]), request_key, request_hash))
    return cur.fetchone()['id']


def latest_change(ctx, list_id, cur):
    # One-level undo: never offer an older action after undoing the most recent.
    cur.execute('SELECT id,label,undone_at FROM prospect_changes WHERE list_id=%s AND user_id=%s ORDER BY id DESC LIMIT 1',
                (list_id, ctx['user_id']))
    row = cur.fetchone()
    return {'id': row['id'], 'label': row['label']} if row and not row['undone_at'] else None


def write_row(cur, row):
    cur.execute("""UPDATE prospect_rows SET cells=%s,status=%s,notes=%s,next_follow_up=%s,archived_at=%s,
        research=COALESCE(%s,research),version=version+1,updated_at=NOW() WHERE id=%s RETURNING *""",
        (Json(row['cells']), row['status'], row['notes'], row['next_follow_up'], row['archived_at'],
         Json(row['research']) if 'research' in row else None, row['id']))
    return dict(cur.fetchone())


def batch_edit(ctx, list_id, data):
    request_key = s.key(data.get('request_key'))
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    entries, action = data.get('rows'), data.get('action')
    if action not in ('update', 'archive', 'restore', 'paste'):
        raise ValueError('Choose a bulk action.')
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_BATCH:
        raise ValueError('Select between 1 and 500 leads per action.')
    if any(not isinstance(e, dict) or type(e.get('id')) is not int or type(e.get('version')) is not int for e in entries):
        raise ValueError('Invalid lead selection. Reload the list.')
    ids = [e['id'] for e in entries]
    if len(ids) != len(set(ids)):
        raise ValueError('Select each lead only once.')
    with s.transaction() as cur:
        listing = s.get_list(ctx, list_id, cur, for_update=True)
        cur.execute('SELECT id,list_id,request_hash FROM prospect_changes WHERE user_id=%s AND request_key=%s', (ctx['user_id'], request_key))
        prior = cur.fetchone()
        if prior:
            if prior['list_id'] != list_id or prior['request_hash'] != digest:
                raise s.Conflict('This action already saved with different settings. Reload before trying again.')
            return {'count': len(ids), 'change_id': prior['id'], 'replayed': True}
        if data.get('list_version') != listing['version']:
            raise s.Conflict('The list owner or columns changed. Reload before applying this action.')
        cur.execute('SELECT * FROM prospect_rows WHERE list_id=%s AND id=ANY(%s) ORDER BY id FOR UPDATE', (list_id, ids))
        before = [dict(r) for r in cur.fetchall()]
        if len(before) != len(ids):
            raise LookupError('One or more selected leads are not in this list.')
        entries_by_id = {e['id']: e for e in entries}
        after = []
        source_ids = {c['id'] for c in listing['columns']}
        changes = data.get('changes', {})
        if not isinstance(changes, dict) or set(changes) - {'status', 'next_follow_up'}:
            raise ValueError('Choose a status or follow-up change.')
        if action == 'update' and not changes:
            raise ValueError('Choose a status or follow-up change.')
        for original in before:
            row = copy.deepcopy(original)
            entry = entries_by_id[row['id']]
            if row['promoted_at'] or row['version'] != entry['version']:
                raise s.Conflict('A selected lead changed or was added to CRM. Reload and review the selection. No leads were changed.')
            if action == 'restore':
                if not row['archived_at']:
                    raise s.Conflict('Choose archived leads to restore. No leads were changed.')
                row['archived_at'] = None
            else:
                if row['archived_at']:
                    raise s.Conflict('Restore archived leads before editing them. No leads were changed.')
                if action == 'archive':
                    row['archived_at'] = datetime.now(timezone.utc).replace(tzinfo=None)
                elif action == 'paste':
                    cells = entry.get('cells')
                    if not isinstance(cells, dict) or not cells or set(cells) - source_ids:
                        raise ValueError('Paste into imported or custom columns only.')
                    for cid, value in cells.items():
                        row['cells'][cid] = s.clean_text(value, s.MAX_CELL, 'Cell')
                else:
                    if 'status' in changes:
                        if not isinstance(changes['status'], str) or changes['status'] not in s.STATUSES:
                            raise ValueError('Choose a valid status.')
                        row['status'] = changes['status']
                    if 'next_follow_up' in changes:
                        row['next_follow_up'] = s.parse_date(changes['next_follow_up'])
                    if row['status'] in ('do_not_contact', 'not_interested'):
                        if changes.get('next_follow_up'):
                            raise ValueError('Do not contact and Not interested leads cannot receive a follow-up date. Change their status first.')
                        row['next_follow_up'] = None
            after.append(write_row(cur, row))
        label = {'update': 'Bulk update', 'archive': 'Archive leads', 'restore': 'Restore leads', 'paste': 'Paste cells'}[action]
        change_id = record_change(cur, ctx, list_id, label, before, after, request_key, digest)
        crm.audit_change(cur, ctx, 'prospect_list', list_id, listing['name'], 'updated',
                         new_value={'action': action, 'row_ids': ids, 'change_id': change_id})
        return {'count': len(after), 'change_id': change_id, 'replayed': False}


def undo(ctx, list_id, change_id):
    with s.transaction() as cur:
        listing = s.get_list(ctx, list_id, cur, for_update=True)
        cur.execute('SELECT * FROM prospect_changes WHERE id=%s AND list_id=%s AND user_id=%s FOR UPDATE',
                    (change_id, list_id, ctx['user_id']))
        change = cur.fetchone()
        if not change:
            raise LookupError('Edit not found.')
        if change['undone_at']:
            return {'count': len(change['before_rows'])}
        if latest_change(ctx, list_id, cur)['id'] != change_id:
            raise s.Conflict('Only your latest row edit can be undone. Reload to review it.')
        expected = {r['id']: r for r in change['after_rows']}
        cur.execute('SELECT * FROM prospect_rows WHERE list_id=%s AND id=ANY(%s) ORDER BY id FOR UPDATE', (list_id, list(expected)))
        current = cur.fetchall()
        if len(current) != len(expected) or any(r['version'] != expected[r['id']]['version'] or r['promoted_at'] for r in current):
            raise s.Conflict('A lead changed since this action. Undo would overwrite newer work, so nothing was changed.')
        for row in change['before_rows']:
            write_row(cur, row)
        cur.execute('UPDATE prospect_changes SET undone_at=NOW() WHERE id=%s', (change_id,))
        crm.audit_change(cur, ctx, 'prospect_list', list_id, listing['name'], 'updated', new_value={'undo_change_id': change_id})
        return {'count': len(current)}


def manage_column(ctx, list_id, data):
    label = s.clean_text(data.get('label'), 200, 'Column name').strip()
    if not label:
        raise ValueError('Enter a column name.')
    with s.transaction() as cur:
        listing = s.get_list(ctx, list_id, cur, for_update=True)
        if data.get('version') != listing['version']:
            raise s.Conflict('The list changed. Reload before changing columns.')
        columns = copy.deepcopy(listing['columns'])
        cid = data.get('id')
        if cid is None:
            if len(columns) >= s.MAX_COLUMNS:
                raise ValueError('A sheet can have up to 100 columns.')
            cid = f'c{max(int(c["id"][1:]) for c in columns) + 1}'
            columns.append({'id': cid, 'label': label, 'custom': True})
            cur.execute('UPDATE prospect_rows SET cells=cells || %s,version=version+1 WHERE list_id=%s', (Json({cid: ''}), list_id))
        else:
            column = next((c for c in columns if c['id'] == cid), None)
            if not column:
                raise ValueError('Choose an imported or custom column to rename.')
            column.setdefault('original_label', column['label'])
            column['label'] = label
            cur.execute('UPDATE prospect_rows SET version=version+1 WHERE list_id=%s', (list_id,))
        cur.execute('UPDATE prospect_lists SET columns=%s,version=version+1 WHERE id=%s', (Json(columns), list_id))
        crm.audit_change(cur, ctx, 'prospect_list', list_id, listing['name'], 'updated', new_value={'column': cid, 'label': label})
        return {'column_id': cid, 'listing': s.get_list(ctx, list_id, cur)}


def identity_keys(fields):
    keys = set()
    email = fields.get('email', '').strip().lower()
    if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        keys.add(('email', email))
    for field in ('phone', 'secondary_phone'):
        raw = fields.get(field, '')
        base, _ = crm.split_phone_extension(raw)
        digits = re.sub(r'\D', '', base or '')
        # Do not turn several numbers or a long identifier into a false match.
        if len(digits) in (7, 10) or (len(digits) == 11 and digits.startswith('1')):
            keys.add(('phone', crm.normalize_phone_digits(raw)))
    return keys


def find_duplicates(ctx, listing, cur, target_ids=None):
    """Compare this sheet and accessible CRM records; no per-row SQL or merging.

    Results are bounded even for thousands of identical rows. A phone match is
    only a possible duplicate: a shared switchboard does not identify a person.
    """
    cur.execute('SELECT id,position,cells,archived_at,promoted_contact_id FROM prospect_rows WHERE list_id=%s ORDER BY position', (listing['id'],))
    rows = [dict(r) for r in cur.fetchall()]
    groups, row_keys, row_info = defaultdict(list), {}, {}
    source_ids = [c['id'] for c in listing['columns'] if not c.get('custom')]
    for row in rows:
        fields = s.mapped(row, listing)
        keys = identity_keys(fields)
        values = [row['cells'].get(cid, '').strip().casefold() for cid in source_ids]
        if any(values):
            keys.add(('identical row', hashlib.sha256(json.dumps(values).encode()).hexdigest()))
        row_keys[row['id']] = keys
        row_info[row['id']] = {'id': row['id'], 'position': row['position'],
                               'name': fields['name'] or fields['company'] or f'Row {row["position"]}',
                               'archived': bool(row['archived_at'])}
        for k in keys:
            groups[k].append(row['id'])
    targets = [r for r in rows if target_ids is None or r['id'] in target_ids]
    needed = set().union(*(row_keys[r['id']] for r in targets)) if targets else set()
    emails = [value for kind, value in needed if kind == 'email']
    phones = [value for kind, value in needed if kind == 'phone']
    contacts = []
    if emails or phones:
        cur.execute(f"""SELECT c.id,c.name,c.email,c.do_not_contact,c.last_contacted_at,p.digits
            FROM crm_contacts c LEFT JOIN crm_phones p ON p.contact_id=c.id
            WHERE {crm.record_scope_sql(ctx, 'c')} AND
                (LOWER(TRIM(c.email))=ANY(%(emails)s) OR c.id IN
                    (SELECT contact_id FROM crm_phones WHERE digits=ANY(%(phones)s))) ORDER BY c.id""",
            {**ctx, 'emails': emails, 'phones': phones})
        contacts = cur.fetchall()
    crm_groups = defaultdict(dict)
    for contact in contacts:
        for k in (('email', (contact['email'] or '').strip().lower()), ('phone', contact['digits'])):
            if k in needed:
                crm_groups[k][contact['id']] = dict(contact)
    list_sets = {k: frozenset(ids) for k, ids in groups.items() if len(ids) > 1}
    crm_sets = {k: frozenset(matches) for k, matches in crm_groups.items()}
    contact_info = {c['id']: c for c in contacts}

    @lru_cache(maxsize=256)
    def candidates(kind, keys):
        index = list_sets if kind == 'list' else crm_sets
        sets = sorted((index[k] for k in keys), key=len, reverse=True)
        ids = sets[0] if sets else frozenset()
        for other in sets[1:]:
            if not other.issubset(ids):
                ids = ids | other
        # Cache only a count and a small sample, never one full peer map per row.
        return len(ids), nsmallest(9, ids)

    results = {}
    for row in targets:
        keys = row_keys[row['id']]
        count, peer_ids = candidates('list', frozenset(k for k in keys if k in list_sets))
        count = max(0, count - 1)  # Duplicate groups include the row itself.
        crm_count, crm_ids = candidates('crm', frozenset(k for k in keys if k in crm_sets))
        if not count and not crm_count:
            continue
        list_matches = [{**row_info[rid], 'reasons': sorted({k[0] for k in keys & row_keys[rid]})}
                        for rid in peer_ids if rid != row['id']][:8]
        crm_matches = []
        for cid in crm_ids[:8]:
            contact = contact_info[cid]
            crm_matches.append({'id': cid, 'name': contact['name'], 'do_not_contact': contact['do_not_contact'],
                'last_contacted_at': contact['last_contacted_at'],
                'reasons': sorted({k[0] for k in keys if cid in crm_sets.get(k, ())})})
        results[str(row['id'])] = {'list_count': count, 'crm_count': crm_count,
                                  'list_matches': list_matches, 'crm_matches': crm_matches}
    return results


def duplicates(ctx, list_id):
    with s.transaction() as cur:
        listing = s.get_list(ctx, list_id, cur)
        return {'duplicates': find_duplicates(ctx, listing, cur)}
