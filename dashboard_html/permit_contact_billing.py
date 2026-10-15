"""Durable permit-contact purchase groups shared by single unlocks and exports."""
import hashlib

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS permit_contact_purchase_items (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    enrichment_id INTEGER NOT NULL,
    request_key TEXT NOT NULL,
    building_id INTEGER NOT NULL,
    contact_name TEXT NOT NULL,
    is_batch BOOLEAN NOT NULL,
    PRIMARY KEY(user_id,enrichment_id)
);
CREATE INDEX IF NOT EXISTS idx_permit_contact_purchase_key
    ON permit_contact_purchase_items(request_key);
"""


def settle_permit_contacts(user_id, items, should_charge=True, is_batch=False):
    """Freeze each result's purchase group, charge once, and atomically unlock it.

    A retry from either a single-contact page or an export recovers the same
    previously authorized group, even if the caller now requests a subset.
    """
    from enrichment_service import get_db_connection
    from stripe_service import charge_enrichment_fee, charge_batch_enrichment_total
    if not items:
        return True, 'No new contacts', 0.0, []
    items = {int(item['id']): item for item in items}
    conn = get_db_connection()
    total = 0.0
    receipts = []
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_lock(72110,%s)', (user_id,))
            cur.execute('SELECT enrichment_id FROM user_permit_contact_unlocks '
                        'WHERE user_id=%s AND enrichment_id=ANY(%s)', (user_id,list(items)))
            unlocked = {r['enrichment_id'] for r in cur.fetchall()}
            remaining = [key for key in items if key not in unlocked]
            if not remaining:
                return True, 'Already unlocked', 0.0, []
            cur.execute('SELECT enrichment_id,request_key FROM permit_contact_purchase_items '
                        'WHERE user_id=%s AND enrichment_id=ANY(%s)', (user_id,remaining))
            allocated = {r['enrichment_id']: r['request_key'] for r in cur.fetchall()}
            new_ids = sorted(set(remaining)-set(allocated))
            if new_ids:
                digest = hashlib.sha256(f'{user_id}:{is_batch}:{new_ids}'.encode()).hexdigest()
                request_key = f'permit-purchase-{digest}'
                for enrichment_id in new_ids:
                    item = items[enrichment_id]
                    cur.execute('''INSERT INTO permit_contact_purchase_items
                        (user_id,enrichment_id,request_key,building_id,contact_name,is_batch)
                        VALUES (%s,%s,%s,%s,%s,%s)''',
                        (user_id,enrichment_id,request_key,item['building_id'],item['name'],is_batch))
                    allocated[enrichment_id] = request_key
            conn.commit()

            for request_key in sorted(set(allocated.values())):
                cur.execute('SELECT * FROM permit_contact_purchase_items '
                            'WHERE user_id=%s AND request_key=%s ORDER BY enrichment_id',
                            (user_id,request_key))
                group = cur.fetchall()
                conn.commit()
                amount = 0.0
                receipt = 'free_access'
                if should_charge:
                    if group[0]['is_batch']:
                        amount = max(round(len(group)*0.35,2),0.50)
                        ok,message,receipt = charge_batch_enrichment_total(
                            user_id,sorted({r['building_id'] for r in group}),len(group),
                            [{'building_id':r['building_id'],'owner':r['contact_name']} for r in group],
                            idempotency_key=request_key)
                    else:
                        if len(group) != 1:
                            raise ValueError('Single-contact purchase contains multiple results')
                        amount = 0.50
                        ok,message,receipt = charge_enrichment_fee(
                            user_id,group[0]['building_id'],group[0]['contact_name'],
                            charge_scope='permit_contact',idempotency_key=request_key)
                    if not ok:
                        return False,message,total,receipts
                # One commit grants the whole paid group; a restart cannot
                # leave a partly unlocked group to be charged as a new subset.
                cur.execute('''INSERT INTO user_permit_contact_unlocks
                    (user_id,enrichment_id,charge_amount,stripe_charge_id)
                    SELECT user_id,enrichment_id,%s,%s FROM permit_contact_purchase_items
                    WHERE user_id=%s AND request_key=%s
                    ON CONFLICT(user_id,enrichment_id) DO NOTHING''',
                    (amount/len(group),receipt,user_id,request_key))
                conn.commit()
                total += amount
                receipts.append(receipt)
        return True,'Contacts unlocked',round(total,2),receipts
    finally:
        conn.close()
