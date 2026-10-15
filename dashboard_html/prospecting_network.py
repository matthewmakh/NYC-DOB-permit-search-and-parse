"""List-private people, company and building profiles, separate from CRM.

Sheet values supply recorded associations; ownership and management are explicit
relationships entered by the user. Every node and edge belongs to one list.
"""
from psycopg2.extras import execute_values

import crm_service as crm
import prospecting_service as s

RELATIONS = {
    'works_at': ('Works at', {'person'}, {'company'}),
    'manages': ('Manages', {'person', 'company'}, {'building'}),
    'owns': ('Owns', {'person', 'company'}, {'building', 'company'}),
    'contact_for': ('Contact for', {'person'}, {'company', 'building'}),
    'knows': ('Knows / referred by', {'person'}, {'person'}),
    'part_of': ('Part of', {'company'}, {'company'}),
    'located_at': ('Located at', {'person', 'company'}, {'building'}),
    'associated_with': ('Associated with', {'person', 'company', 'building'}, {'person', 'company', 'building'}),
}


def sync_people(ctx, list_id):
    """Explicit, idempotent build from mapped sheet values. No CRM writes."""
    with s.transaction() as cur:
        listing = s.get_list(ctx, list_id, cur, for_update=True)
        cur.execute("""SELECT r.*,e.id AS node_id FROM prospect_rows r LEFT JOIN prospect_entities e ON e.row_id=r.id
            WHERE r.list_id=%s ORDER BY r.id""", (list_id,))
        rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            return {'added': 0}
        fields = {r['id']: s.mapped(r, listing) for r in rows}
        new_rows = [r for r in rows if not r['node_id']]
        if new_rows:
            execute_values(cur, 'INSERT INTO prospect_entities(list_id,kind,name,row_id) VALUES %s',
                           [(list_id, 'person', fields[r['id']]['name'] or fields[r['id']]['company'] or f'Lead {r["position"]}', r['id']) for r in new_rows], page_size=500)
        # Keep derived company associations current, without overwriting manual
        # relationships or restoring links the user explicitly removed.
        cur.execute("""DELETE FROM prospect_links l USING prospect_entities a,prospect_entities b,prospect_rows r
            WHERE l.source_id=a.id AND l.target_id=b.id AND a.row_id=r.id AND l.list_id=%s AND l.origin='sheet'
            AND LOWER(TRIM(COALESCE(r.cells->>%s,'')))<>l.source_value""", (list_id, listing['mapping'].get('company', '')))
        companies = {f['company'].strip().lower(): f['company'].strip() for f in fields.values() if f['company'].strip()}
        if companies:
            # Existing source aliases keep a renamed company attached to the
            # original sheet value; new people using that value join it too.
            cur.execute("SELECT source_value,target_id FROM prospect_links WHERE list_id=%s AND source_value<>'' ORDER BY id", (list_id,))
            aliases = {r['source_value']: r['target_id'] for r in cur.fetchall()}
            additions = [(list_id, 'company', name) for value, name in companies.items() if value not in aliases]
            if additions:
                execute_values(cur, "INSERT INTO prospect_entities(list_id,kind,name) VALUES %s ON CONFLICT DO NOTHING", additions, page_size=500)
            cur.execute("SELECT id,LOWER(TRIM(name)) AS name FROM prospect_entities WHERE list_id=%s AND kind='company'", (list_id,))
            company_ids = {r['name']: r['id'] for r in cur.fetchall()}
            company_ids.update(aliases)
            cur.execute('SELECT id,row_id FROM prospect_entities WHERE list_id=%s AND row_id=ANY(%s)', (list_id, list(fields)))
            links = [(list_id, node['id'], company_ids[fields[node['row_id']]['company'].strip().lower()], 'associated_with',
                      'Company recorded in the sheet; role not yet verified.', ctx['user_id'], 'sheet', fields[node['row_id']]['company'].strip().lower()) for node in cur.fetchall() if fields[node['row_id']]['company'].strip()]
            if links:
                execute_values(cur, 'INSERT INTO prospect_links(list_id,source_id,target_id,relation,note,added_by_id,origin,source_value) VALUES %s ON CONFLICT DO NOTHING', links, page_size=500)
        return {'added': len(new_rows)}


def get_network(ctx, list_id):
    with s.transaction() as cur:
        listing = s.get_list(ctx, list_id, cur)
        cur.execute("""SELECT e.*,r.position,r.status,r.archived_at,r.last_touch_at,r.next_follow_up,r.promoted_at,
            COALESCE(NULLIF(r.cells->>%s,''),NULLIF(r.cells->>%s,''),e.name) AS display_name,
            r.promoted_contact_id FROM prospect_entities e LEFT JOIN prospect_rows r ON r.id=e.row_id
            WHERE e.list_id=%s ORDER BY e.kind,LOWER(e.name),e.id""",
            (listing['mapping'].get('name', ''), listing['mapping'].get('company', ''), list_id))
        nodes = [dict(r) for r in cur.fetchall()]
        # References never become permission grants. Reassignment may make a
        # previously linked CRM record unavailable to the current sheet owner.
        contact_ids = list({cid for n in nodes for cid in (n['crm_contact_id'], n['promoted_contact_id']) if cid})
        building_ids = list({n['crm_building_id'] for n in nodes if n['crm_building_id']})
        cur.execute(f'SELECT c.id FROM crm_contacts c WHERE c.id=ANY(%(ids)s) AND {crm.record_scope_sql(ctx, "c")}', {**ctx, 'ids': contact_ids})
        contacts = {r['id'] for r in cur.fetchall()}
        cur.execute(f'SELECT b.id FROM crm_buildings b WHERE b.id=ANY(%(ids)s) AND {crm.record_scope_sql(ctx, "b")}', {**ctx, 'ids': building_ids})
        buildings = {r['id'] for r in cur.fetchall()}
        for node in nodes:
            node['crm_restricted'] = False
            for field, allowed in (('crm_contact_id', contacts), ('promoted_contact_id', contacts), ('crm_building_id', buildings)):
                if node[field] and node[field] not in allowed:
                    node[field] = None; node['crm_restricted'] = True
        cur.execute("SELECT * FROM prospect_links WHERE list_id=%s AND origin<>'dismissed' ORDER BY id", (list_id,))
        return {'nodes': nodes, 'links': [dict(r) for r in cur.fetchall()],
                'relations': {k: {'label': v[0], 'from': sorted(v[1]), 'to': sorted(v[2])} for k, v in RELATIONS.items()}}


def save_entity(ctx, list_id, data):
    kind = data.get('kind')
    if kind not in ('company', 'building'):
        raise ValueError('Add people as leads. Choose Company or Building for this profile.')
    name = s.clean_text(data.get('name', ''), 255, 'Name').strip()
    if not name:
        raise ValueError('Enter a name or building address.')
    values = {field: s.clean_text(data.get(field, ''), limit, field.title()).strip()
              for field, limit in (('address', 500), ('website', 1000), ('notes', 20000))}
    if values['website'] and not values['website'].lower().startswith(('https://', 'http://')):
        raise ValueError('Website must start with https:// or http://.')
    with s.transaction() as cur:
        listing = s.get_list(ctx, list_id, cur, for_update=True)
        entity_id = data.get('id')
        if entity_id is not None:
            cur.execute('SELECT * FROM prospect_entities WHERE id=%s AND list_id=%s FOR UPDATE', (entity_id, list_id))
            existing = cur.fetchone()
            if not existing:
                raise LookupError('Profile not found.')
            if existing['row_id'] or existing['kind'] != kind:
                raise ValueError('Edit a person through their lead record.')
            if data.get('version') != existing['version']:
                raise s.Conflict('This profile changed. Reopen it before saving.')
        cur.execute('SELECT id FROM prospect_entities WHERE list_id=%s AND kind=%s AND row_id IS NULL AND LOWER(TRIM(name))=LOWER(%s) AND id<>%s',
                    (list_id, kind, name, entity_id or 0))
        if cur.fetchone():
            raise s.Conflict('A profile with this name already exists in the sheet. Link to that profile or choose a more specific name.')
        crm_building_id = data.get('crm_building_id') or None
        if crm_building_id:
            cur.execute(f'SELECT b.id FROM crm_buildings b WHERE b.id=%(id)s AND {crm.record_scope_sql(ctx, "b")}', {**ctx, 'id': crm_building_id})
            if kind != 'building' or not cur.fetchone():
                raise LookupError('CRM building not found.')
        if entity_id:
            cur.execute('UPDATE prospect_entities SET name=%s,address=%s,website=%s,notes=%s,version=version+1 WHERE id=%s RETURNING id',
                        (name, values['address'], values['website'], values['notes'], entity_id))
        else:
            cur.execute('INSERT INTO prospect_entities(list_id,kind,name,address,website,notes,crm_building_id) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                        (list_id, kind, name, values['address'], values['website'], values['notes'], crm_building_id))
        node_id = cur.fetchone()['id']
        crm.audit_change(cur, ctx, 'prospect_list', list_id, listing['name'], 'updated', new_value={'profile_id': node_id, 'kind': kind})
        return {'entity_id': node_id}


def link(ctx, list_id, data):
    source_id, target_id, relation = data.get('source_id'), data.get('target_id'), data.get('relation')
    if type(source_id) is not int or type(target_id) is not int or source_id == target_id:
        raise ValueError('Choose two different profiles to link.')
    if not isinstance(relation, str) or relation not in RELATIONS:
        raise ValueError('Choose a relationship.')
    note = s.clean_text(data.get('note', ''), 2000, 'Relationship note')
    with s.transaction() as cur:
        listing = s.get_list(ctx, list_id, cur, for_update=True)
        cur.execute('SELECT id,kind FROM prospect_entities WHERE list_id=%s AND id=ANY(%s)', (list_id, [source_id, target_id]))
        nodes = {r['id']: r for r in cur.fetchall()}
        if len(nodes) != 2:
            raise LookupError('Choose profiles from this sheet.')
        _, sources, targets = RELATIONS[relation]
        if nodes[source_id]['kind'] not in sources or nodes[target_id]['kind'] not in targets:
            raise ValueError('This relationship does not apply to these profile types.')
        cur.execute("""INSERT INTO prospect_links(list_id,source_id,target_id,relation,note,added_by_id) VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT(source_id,target_id,relation) DO UPDATE SET note=EXCLUDED.note,origin='manual' RETURNING id""",
            (list_id, source_id, target_id, relation, note, ctx['user_id']))
        link_id = cur.fetchone()['id']
        crm.audit_change(cur, ctx, 'prospect_list', list_id, listing['name'], 'updated', new_value={'relationship_id': link_id, 'relation': relation})
        return {'link_id': link_id}


def unlink(ctx, list_id, link_id):
    with s.transaction() as cur:
        listing = s.get_list(ctx, list_id, cur, for_update=True)
        cur.execute("UPDATE prospect_links SET origin='dismissed' WHERE id=%s AND list_id=%s AND origin<>'dismissed' RETURNING id", (link_id, list_id))
        if not cur.fetchone():
            raise LookupError('Relationship not found.')
        crm.audit_change(cur, ctx, 'prospect_list', list_id, listing['name'], 'updated', new_value={'removed_relationship_id': link_id})
        return {}


def search_crm(ctx, list_id, kind, query):
    if kind not in ('person', 'building'):
        raise ValueError('Choose a person or building profile.')
    query = query.strip()[:100]
    with s.transaction() as cur:
        s.get_list(ctx, list_id, cur)
        if len(query) < 2:
            return {'records': []}
        match = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        if kind == 'person':
            cur.execute(f"""SELECT c.id,c.name,c.company AS detail FROM crm_contacts c
                WHERE {crm.record_scope_sql(ctx, 'c')} AND (c.name ILIKE %(q)s OR c.company ILIKE %(q)s)
                ORDER BY c.name,c.id LIMIT 25""", {**ctx, 'q': match})
        else:
            cur.execute(f"""SELECT b.id,b.address AS name,b.borough AS detail FROM crm_buildings b
                WHERE {crm.record_scope_sql(ctx, 'b')} AND b.address ILIKE %(q)s ORDER BY b.address,b.id LIMIT 25""", {**ctx, 'q': match})
        return {'records': [dict(r) for r in cur.fetchall()]}


def link_crm(ctx, list_id, node_id, data):
    record_id = data.get('record_id')
    if record_id is not None and type(record_id) is not int:
        raise ValueError('Choose a CRM record.')
    with s.transaction() as cur:
        listing = s.get_list(ctx, list_id, cur, for_update=True)
        cur.execute('SELECT * FROM prospect_entities WHERE list_id=%s AND id=%s FOR UPDATE', (list_id, node_id))
        node = cur.fetchone()
        if not node:
            raise LookupError('Profile not found.')
        if data.get('version') != node['version']:
            raise s.Conflict('This profile changed. Reopen it before linking a CRM record.')
        if node['kind'] not in ('person', 'building'):
            raise ValueError('CRM links are available for people and buildings.')
        table, field = ('crm_contacts', 'crm_contact_id') if node['kind'] == 'person' else ('crm_buildings', 'crm_building_id')
        if record_id is not None:
            cur.execute(f'SELECT c.id FROM {table} c WHERE c.id=%(id)s AND {crm.record_scope_sql(ctx, "c")} FOR SHARE', {**ctx, 'id': record_id})
            if not cur.fetchone():
                raise LookupError('CRM record not found.')
        if node['row_id']:
            cur.execute('SELECT promoted_at FROM prospect_rows WHERE id=%s', (node['row_id'],))
            if cur.fetchone()['promoted_at']:
                raise s.Conflict('This lead is already promoted. Manage its CRM record directly.')
        cur.execute(f'UPDATE prospect_entities SET {field}=%s,version=version+1 WHERE id=%s', (record_id, node_id))
        crm.audit_change(cur, ctx, 'prospect_list', list_id, listing['name'], 'updated', new_value={'profile_id': node_id, field: record_id})
        return {}
