"""Append people to an existing private work list without overwriting research."""
import hashlib
import json

from psycopg2.extras import Json, execute_values

import crm_service as crm
import prospecting_service as s


def suggest_matches(listing, parsed):
    result = {}
    for source in parsed['columns']:
        matches = [c['id'] for c in listing['columns'] if c['label'].strip().casefold() == source['label'].strip().casefold()]
        if len(matches) == 1:
            result[source['id']] = matches[0]
        else:
            fields = [f for f, cid in parsed['mapping'].items() if cid == source['id'] and listing['mapping'].get(f)]
            result[source['id']] = listing['mapping'][fields[0]] if fields else 'new'
    return result


def append(ctx, list_id, *, parsed=None, cells=None, mapping=None, filename='Manual entry', request_key=None, version=None):
    request_key = s.key(request_key)
    digest = hashlib.sha256(json.dumps([list_id, parsed, cells, mapping, filename], sort_keys=True).encode()).hexdigest()
    with s.transaction() as cur:
        listing = s.get_list(ctx, list_id, cur, for_update=True)
        cur.execute('SELECT * FROM prospect_imports WHERE user_id=%s AND request_key=%s', (ctx['user_id'], request_key))
        prior = cur.fetchone()
        if prior:
            if prior['list_id'] != list_id or prior['request_hash'] != digest:
                raise s.Conflict('This import already saved with different settings. Start a new import.')
            return {'count': prior['row_count'], 'replayed': True}
        if version != listing['version']:
            raise s.Conflict('The owner or columns changed. Reload and review your column matches.')
        columns = list(listing['columns'])
        existing = {c['id'] for c in columns}
        field_mapping = dict(listing['mapping'])
        if parsed is None:
            if not isinstance(cells, dict) or set(cells) - existing:
                raise ValueError('Enter values using this sheet’s columns.')
            for value in cells.values():
                s.clean_text(value, s.MAX_CELL, 'Cell')
            if not any(v.strip() for v in cells.values()):
                raise ValueError('Enter a name, company or other lead information.')
            new_rows = [{cid: cells.get(cid, '') for cid in existing}]
        else:
            incoming = {c['id'] for c in parsed['columns']}
            if not isinstance(mapping, dict) or set(mapping) != incoming or any(not isinstance(v, str) or v not in existing | {'new', 'skip'} for v in mapping.values()):
                raise ValueError('Match every incoming column, add it as new, or skip it.')
            targets = [v for v in mapping.values() if v not in ('new', 'skip')]
            if len(targets) != len(set(targets)):
                raise ValueError('Two incoming columns cannot overwrite the same destination column.')
            resolved = dict(mapping)
            next_id = max(int(c['id'][1:]) for c in columns) + 1
            for source in parsed['columns']:
                if mapping[source['id']] == 'new':
                    cid = f'c{next_id}'; next_id += 1
                    columns.append({'id': cid, 'label': source['label']})
                    resolved[source['id']] = cid
            if len(columns) > s.MAX_COLUMNS:
                raise ValueError('This import would exceed 100 columns. Match or skip some incoming columns.')
            if all(value == 'skip' for value in resolved.values()):
                raise ValueError('Keep at least one column.')
            for field, source_id in parsed['mapping'].items():
                if not field_mapping.get(field) and source_id and resolved[source_id] != 'skip':
                    field_mapping[field] = resolved[source_id]
            new_rows = []
            for source in parsed['rows']:
                row = {c['id']: '' for c in columns}
                row.update({resolved[cid]: value for cid, value in source.items() if resolved[cid] != 'skip'})
                if any(v.strip() for v in row.values()):
                    new_rows.append(row)
            if not new_rows:
                raise ValueError('The matched columns contain no lead data.')
        cur.execute('SELECT COUNT(*) AS n,COALESCE(MAX(position),0) AS last FROM prospect_rows WHERE list_id=%s', (list_id,))
        counts = cur.fetchone()
        if counts['n'] + len(new_rows) > s.MAX_ROWS:
            raise ValueError('A sheet can hold up to 10,000 leads, including archived rows.')
        if columns != listing['columns'] or field_mapping != listing['mapping']:
            cur.execute('UPDATE prospect_lists SET columns=%s,mapping=%s,version=version+1 WHERE id=%s', (Json(columns), Json(field_mapping), list_id))
            cur.execute('UPDATE prospect_rows SET version=version+1 WHERE list_id=%s', (list_id,))
        execute_values(cur, 'INSERT INTO prospect_rows(list_id,position,cells,original_cells,source_filename) VALUES %s',
                       [(list_id, counts['last']+i+1, Json(row), Json(row), filename[:255]) for i, row in enumerate(new_rows)], page_size=500)
        cur.execute('INSERT INTO prospect_imports(list_id,user_id,request_key,request_hash,filename,row_count) VALUES (%s,%s,%s,%s,%s,%s)',
                    (list_id, ctx['user_id'], request_key, digest, filename[:255], len(new_rows)))
        crm.audit_change(cur, ctx, 'prospect_list', list_id, listing['name'], 'updated', new_value={'added_rows': len(new_rows), 'source': filename[:255]})
        return {'count': len(new_rows), 'replayed': False}
