#!/usr/bin/env python3
"""
Step 3: ACRIS Enrichment — full transaction history and party intelligence.

Queries ACRIS Legals -> Master/Parties/References/Remarks (batched), then:
- populates acris_transactions (all doc types, with remarks)
- populates acris_parties with CORRECT roles per document type
  (for deeds, ACRIS party 1 is the GRANTOR/seller and party 2 the
  GRANTEE/buyer; for mortgages party 1 is the borrower and party 2 the
  lender — per the Document Control Codes dataset)
- populates acris_references (which satisfaction discharges which mortgage)
- updates buildings with sale/mortgage summary, a purchase-window cash flag,
  and equity signals (open mortgages, free-and-clear, last satisfaction)

Fixes vs. the previous version:
- B1: buyer/seller/lender roles were inverted (party_type hardcoded wrong)
- B3: is_cash_purchase compared the sale to the most recent mortgage ever;
  now it looks for a mortgage recorded around the purchase itself
- E1: master/parties were fetched one document at a time (~42 calls per
  building); now batched (~4-6 calls per building)
"""

import os
import sys
import time
from datetime import date, datetime, timedelta

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from socrata_client import (
    SocrataClient, SocrataError, bbl_parts, soql_quote,
    load_party_roles, party_role, is_deed, is_ownership_party,
    is_mortgage, is_satisfaction,
)

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME')
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        raise ValueError("Either DATABASE_URL or DB_HOST/DB_USER/DB_PASSWORD/DB_NAME must be set")
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# A mortgage recorded in this window around the deed is treated as the
# purchase mortgage. Outside it (e.g. a refi years later) it does not
# make the purchase "financed".
CASH_WINDOW_BEFORE_DAYS = 5
CASH_WINDOW_AFTER_DAYS = 90
ACRIS_LOGIC_VERSION = 4  # also separates loan assignments from deed ownership

_client = None


def get_client():
    global _client
    if _client is None:
        _client = SocrataClient()
    return _client


def parse_acris_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.strptime(str(date_str)[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Optional-column detection (lets new code run before the migration lands)
# ---------------------------------------------------------------------------

_column_cache = {}


def table_has_column(cur, table, column):
    key = (table, column)
    if key not in _column_cache:
        cur.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
        """, (table, column))
        _column_cache[key] = cur.fetchone() is not None
    return _column_cache[key]


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def get_document_ids_for_bbl(bbl):
    """All ACRIS document ids recorded against this lot."""
    boro, block, lot, _, _ = bbl_parts(bbl)
    client = get_client()
    rows = client.get_all(
        'acris_legals',
        page_size=1000,
        **{
            '$select': 'document_id',
            '$where': (f"borough={soql_quote(boro)} AND "
                       f"block={soql_quote(block)} AND lot={soql_quote(lot)}"),
        },
    )
    return sorted({r['document_id'] for r in rows if r.get('document_id')})


def _reference_counterpart_field(client):
    """Preferred counterpart column (compatibility helper for tests/tools)."""
    cols = client.get_columns('acris_references')
    # CRFN is the populated link for modern satisfactions in the live data.
    # A document-id column also exists now, but choosing it exclusively drops
    # valid CRFN-only satisfaction rows.
    for col in sorted(cols):
        if col.startswith('reference') and 'crfn' in col:
            return col
    for col in sorted(cols):
        if col.startswith('reference') and 'doc' in col and 'id' in col:
            return col
    return 'reference_by_crfn_'


def _reference_counterpart_fields(client):
    """Every modern reference link field present in the dataset.

    NYC uses CRFN for many satisfactions and document_id for some other
    relationships. Both may be present on the schema while only one is
    populated on a given row, so querying/processing only one is lossy.
    """
    cols = client.get_columns('acris_references')
    fields = [col for col in sorted(cols)
              if col.startswith('reference')
              and (('crfn' in col) or ('doc' in col and 'id' in col))]
    return fields or ['reference_by_crfn_']


def get_acris_full_history(bbl):
    """
    Complete ACRIS history for a BBL, batched.

    Returns {'transactions': [...], 'references': [...]}.
    Each transaction: document_id, doc_type, doc_amount, doc_date,
    recorded_date, crfn, percent_transferred, remarks, parties (all, with
    role), plus buyers/sellers/lenders lists for compatibility.
    Each reference: {'document_id': ..., 'referenced_document_id': ..., 'crfn': ...}
    """
    client = get_client()
    doc_ids = get_document_ids_for_bbl(bbl)
    if not doc_ids:
        return {'transactions': [], 'references': []}

    masters = client.get_batched(
        'acris_master', 'document_id', doc_ids,
        select='document_id,doc_type,document_amt,document_date,recorded_datetime,crfn,percent_trans',
    )
    parties_rows = client.get_batched(
        'acris_parties', 'document_id', doc_ids,
        select='document_id,party_type,name,address_1,address_2,city,state,zip,country',
    )

    # References link documents by CRFN, so resolve through the CRFNs the
    # master rows carry. Pre-2003 reel/page references have no CRFN and are
    # skipped — those mortgages simply stay "status unknown" rather than
    # wrongly "open".
    ref_fields = _reference_counterpart_fields(client)
    crfn_to_doc = {}
    for m in masters:
        crfn = (m.get('crfn') or '').strip()
        if crfn:
            crfn_to_doc.setdefault(crfn, m.get('document_id'))

    ref_rows = []
    try:
        select_fields = 'document_id,' + ','.join(ref_fields)
        ref_rows.extend(client.get_batched(
            'acris_references', 'document_id', doc_ids,
            select=select_fields))
        for ref_field in ref_fields:
            if 'crfn' in ref_field and crfn_to_doc:
                ref_rows.extend(client.get_batched(
                    'acris_references', ref_field, sorted(crfn_to_doc),
                    select=select_fields))
            elif 'crfn' not in ref_field:
                ref_rows.extend(client.get_batched(
                    'acris_references', ref_field, doc_ids,
                    select=select_fields))
    except SocrataError as e:
        print(f"      ⚠️ References fetch failed (non-fatal): {e}")

    remarks_by_doc = {}
    try:
        for row in client.get_batched('acris_remarks', 'document_id', doc_ids,
                                      select='document_id,remark_text'):
            text = (row.get('remark_text') or '').strip()
            if text:
                doc_id = row['document_id']
                remarks_by_doc[doc_id] = (remarks_by_doc.get(doc_id, '') + ' ' + text).strip()
    except SocrataError as e:
        print(f"      ⚠️ Remarks fetch failed (non-fatal): {e}")

    parties_by_doc = {}
    for p in parties_rows:
        parties_by_doc.setdefault(p.get('document_id'), []).append(p)

    roles = load_party_roles(client)
    transactions = []
    # Legacy FT_*/BK_* reel-era ids can come back as several master rows for
    # one document_id; keep the first. Without this the per-building insert
    # violates unique_doc_per_building and the whole building is skipped.
    seen_doc_ids = set()
    for doc in masters:
        doc_id = doc.get('document_id')
        if not doc_id or doc_id in seen_doc_ids:
            continue
        seen_doc_ids.add(doc_id)
        doc_type = (doc.get('doc_type') or '').strip().upper()
        try:
            doc_amount = float(doc.get('document_amt') or 0)
        except (ValueError, TypeError):
            doc_amount = 0.0
        try:
            percent_transferred = float(doc.get('percent_trans')) if doc.get('percent_trans') else None
        except (ValueError, TypeError):
            percent_transferred = None

        parties = []
        for p in parties_by_doc.get(doc_id, []):
            parties.append({
                'role': party_role(doc_type, p.get('party_type'), roles),
                'party_type_raw': str(p.get('party_type') or '').strip(),
                'name': (p.get('name') or '').strip(),
                'address1': (p.get('address_1') or '').strip(),
                'address2': (p.get('address_2') or '').strip(),
                'city': (p.get('city') or '').strip(),
                'state': (p.get('state') or '').strip(),
                'zip': (p.get('zip') or '').strip(),
                'country': (p.get('country') or '').strip(),
            })

        transactions.append({
            'document_id': doc_id,
            'doc_type': doc_type,
            'doc_amount': doc_amount,
            'doc_date': parse_acris_date(doc.get('document_date')),
            'recorded_date': parse_acris_date(doc.get('recorded_datetime')),
            'crfn': doc.get('crfn', ''),
            'percent_transferred': percent_transferred,
            'remarks': remarks_by_doc.get(doc_id) or None,
            'parties': parties,
            'buyers': [p for p in parties if p['role'] == 'buyer'],
            'sellers': [p for p in parties if p['role'] == 'seller'],
            'lenders': [p for p in parties if p['role'] == 'lender'],
            'borrowers': [p for p in parties if p['role'] == 'borrower'],
        })

    seen = set()
    references = []
    for row in ref_rows:
        a = row.get('document_id')
        for ref_field in ref_fields:
            b = (row.get(ref_field) or '').strip()
            crfn = ''
            if 'crfn' in ref_field:
                crfn = b
                b = crfn_to_doc.get(b)
            if not a or not b or a == b or (a, b) in seen:
                continue
            seen.add((a, b))
            references.append({
                'document_id': a,
                'referenced_document_id': b,
                'crfn': crfn or row.get('crfn', ''),
            })

    return {'transactions': transactions, 'references': references}


# ---------------------------------------------------------------------------
# Derivations
# ---------------------------------------------------------------------------

def _txn_date(t):
    """Recording order controls what is newest in the public record."""
    return t['recorded_date'] or t['doc_date']


def _instrument_date(t):
    """Document date is better for purchase-window comparisons."""
    return t['doc_date'] or t['recorded_date']


def find_primary_deed(transactions):
    deeds = [t for t in transactions if is_deed(t['doc_type']) and _txn_date(t)]
    return max(deeds, key=_txn_date) if deeds else None


def find_primary_mortgage(transactions, open_ids=None):
    """Newest apparently-open principal mortgage instrument.

    Assignments and generic agreements can move or modify a debt but do not
    establish a new principal balance, so they never become the summary.
    """
    mortgages = [t for t in transactions
                 if is_mortgage(t['doc_type']) and _txn_date(t)
                 and (open_ids is None or t['document_id'] in open_ids)]
    return max(mortgages, key=_txn_date) if mortgages else None


def find_purchase_mortgage(transactions, sale_date):
    """The mortgage recorded around the purchase, if any (B3)."""
    if not sale_date:
        return None
    lo = sale_date - timedelta(days=CASH_WINDOW_BEFORE_DAYS)
    hi = sale_date + timedelta(days=CASH_WINDOW_AFTER_DAYS)
    candidates = [t for t in transactions
                  if is_mortgage(t['doc_type']) and _instrument_date(t)
                  and lo <= _instrument_date(t) <= hi]
    return min(candidates, key=lambda t: abs((_instrument_date(t) - sale_date).days)) if candidates else None


def derive_mortgage_status(transactions, references):
    """
    Which mortgages are still open, using the References linkage between
    satisfactions and the mortgages they discharge. Direction-agnostic:
    a mortgage is satisfied if any reference row pairs it with a
    satisfaction-class document.
    """
    doc_types = {t['document_id']: t['doc_type'] for t in transactions}
    mortgage_ids = {t['document_id'] for t in transactions if is_mortgage(t['doc_type'])}
    satisfied = set()
    for ref in references:
        a, b = ref['document_id'], ref['referenced_document_id']
        if a in mortgage_ids and is_satisfaction(doc_types.get(b, '')):
            satisfied.add(a)
        if b in mortgage_ids and is_satisfaction(doc_types.get(a, '')):
            satisfied.add(b)

    transactions_by_id = {t['document_id']: t for t in transactions}

    # Resolve debt chains, not every historical instrument in a chain. ACRIS
    # assignments/agreements often reference several generations of the same
    # loan, and a Mortgage & Consolidation directly references its predecessor.
    # Treating each referenced MTGE as independently open produced "3 open
    # mortgages" for the screenshot property even though the later M&CON is
    # the single current chain.
    parent = {doc_id: doc_id for doc_id in mortgage_ids}

    def find(doc_id):
        while parent[doc_id] != doc_id:
            parent[doc_id] = parent[parent[doc_id]]
            doc_id = parent[doc_id]
        return doc_id

    def union(left, right):
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    mortgages_by_link_document = {}
    for ref in references:
        a, b = ref['document_id'], ref['referenced_document_id']
        if a in mortgage_ids and b in mortgage_ids:
            union(a, b)
        if a in mortgage_ids and b in doc_types and not is_satisfaction(doc_types[b]):
            mortgages_by_link_document.setdefault(b, set()).add(a)
        if b in mortgage_ids and a in doc_types and not is_satisfaction(doc_types[a]):
            mortgages_by_link_document.setdefault(a, set()).add(b)
    for linked_ids in mortgages_by_link_document.values():
        linked_ids = list(linked_ids)
        for other in linked_ids[1:]:
            union(linked_ids[0], other)

    components = {}
    for mortgage_id in mortgage_ids:
        components.setdefault(find(mortgage_id), []).append(mortgage_id)

    # The newest principal instrument is the terminal state of each connected
    # debt chain. A satisfaction of that terminal M&CON closes its predecessors
    # too; a satisfaction of an older predecessor must not close a later
    # consolidation. This also keeps primary-mortgage selection on the current
    # instrument instead of an unsatisfied historical link in the same chain.
    terminal_ids = {
        max(ids, key=lambda doc_id: (
            _txn_date(transactions_by_id[doc_id]) or date.min,
            doc_id,
        ))
        for ids in components.values()
    }
    trackable_ids = {
        doc_id for doc_id in terminal_ids
        if (transactions_by_id.get(doc_id, {}).get('crfn') or '').strip()
    }
    # Reel-era terminal loans have no CRFN counterpart in the current
    # References dataset. Their status is unknown, not "open".
    unknown_ids = terminal_ids - trackable_ids
    open_ids = trackable_ids - satisfied
    open_chain_count = len(open_ids)
    sat_dates = [_instrument_date(t) for t in transactions
                 if is_satisfaction(t['doc_type']) and _instrument_date(t)]
    return {
        'mortgage_count': len(mortgage_ids),
        'satisfied_ids': satisfied,
        'open_ids': open_ids,
        'unknown_ids': unknown_ids,
        'open_mortgage_count': open_chain_count,
        'has_open_mortgage': bool(open_chain_count),
        'is_free_and_clear': bool(mortgage_ids) and not open_ids and not unknown_ids,
        'last_satisfaction_date': max(sat_dates) if sat_dates else None,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_transactions_and_parties(cur, building_id, bbl, transactions,
                                  primary_mortgage_id=None):
    """Replace stored history for this building. Returns (primary_deed,
    primary_mortgage) for the buildings-table summary."""
    if not transactions:
        return None, None

    cur.execute("DELETE FROM acris_parties WHERE building_id = %s", (building_id,))
    cur.execute("DELETE FROM acris_transactions WHERE building_id = %s", (building_id,))

    primary_deed = find_primary_deed(transactions)
    primary_mortgage = next(
        (t for t in transactions if t['document_id'] == primary_mortgage_id), None)
    has_remarks_col = table_has_column(cur, 'acris_transactions', 'remarks')

    for transaction in transactions:
        if has_remarks_col:
            cur.execute("""
                INSERT INTO acris_transactions (
                    building_id, bbl, document_id, doc_type, doc_amount,
                    doc_date, recorded_date, crfn, percent_transferred,
                    is_primary_deed, is_primary_mortgage, remarks
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (building_id, document_id) DO UPDATE
                    SET doc_amount = EXCLUDED.doc_amount
                RETURNING id
            """, (
                building_id, bbl, transaction['document_id'], transaction['doc_type'],
                transaction['doc_amount'], transaction['doc_date'], transaction['recorded_date'],
                transaction['crfn'], transaction['percent_transferred'],
                transaction is primary_deed, transaction is primary_mortgage,
                transaction['remarks'],
            ))
        else:
            cur.execute("""
                INSERT INTO acris_transactions (
                    building_id, bbl, document_id, doc_type, doc_amount,
                    doc_date, recorded_date, crfn, percent_transferred,
                    is_primary_deed, is_primary_mortgage
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (building_id, document_id) DO UPDATE
                    SET doc_amount = EXCLUDED.doc_amount
                RETURNING id
            """, (
                building_id, bbl, transaction['document_id'], transaction['doc_type'],
                transaction['doc_amount'], transaction['doc_date'], transaction['recorded_date'],
                transaction['crfn'], transaction['percent_transferred'],
                transaction is primary_deed, transaction is primary_mortgage,
            ))
        transaction_id = cur.fetchone()['id']

        for p in transaction['parties']:
            if not p['name'] or p['role'] == 'other':
                continue
            # Only a grantor/seller on a DEED is a previous property owner.
            # An assignment's assignor can be a bank transferring the loan.
            is_lead = (is_ownership_party(transaction['doc_type'], p['role'])
                       and p['role'] == 'seller' and bool(p['address1']))
            cur.execute("""
                INSERT INTO acris_parties (
                    building_id, transaction_id, party_type, party_name,
                    address_1, address_2, city, state, zip_code, country, is_lead
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                building_id, transaction_id, p['role'], p['name'],
                p['address1'], p['address2'], p['city'],
                p['state'], p['zip'], p['country'], is_lead,
            ))

    return primary_deed, primary_mortgage


def save_references(cur, building_id, bbl, references):
    cur.execute("SAVEPOINT acris_refs")
    try:
        cur.execute("DELETE FROM acris_references WHERE building_id = %s", (building_id,))
        for ref in references:
            cur.execute("""
                INSERT INTO acris_references (building_id, bbl, document_id,
                    referenced_document_id, crfn)
                VALUES (%s, %s, %s, %s, %s)
            """, (building_id, bbl, ref['document_id'],
                  ref['referenced_document_id'], ref['crfn']))
        cur.execute("RELEASE SAVEPOINT acris_refs")
        return True
    except psycopg2.Error:
        # Table not migrated yet — references are re-fetchable, skip quietly.
        cur.execute("ROLLBACK TO SAVEPOINT acris_refs")
        return False


def update_buildings_table(cur, building_id, transactions, primary_deed,
                           primary_mortgage, references=None):
    deed_count = len([t for t in transactions if is_deed(t['doc_type'])])
    mortgage_count = len([t for t in transactions if is_mortgage(t['doc_type'])])
    satisfaction_count = len([t for t in transactions if is_satisfaction(t['doc_type'])])

    sale_price = sale_date = sale_recorded_date = sale_crfn = None
    sale_buyer_primary = sale_seller_primary = sale_percent_transferred = None
    is_cash_purchase = False
    financing_ratio = None
    days_since_sale = None

    if primary_deed:
        sale_price = primary_deed['doc_amount'] if primary_deed['doc_amount'] > 0 else None
        sale_date = primary_deed['doc_date'] or primary_deed['recorded_date']
        sale_recorded_date = primary_deed['recorded_date']
        sale_crfn = primary_deed['crfn']
        sale_percent_transferred = primary_deed['percent_transferred']
        if primary_deed['buyers']:
            sale_buyer_primary = '; '.join(dict.fromkeys(
                p['name'] for p in primary_deed['buyers'] if p.get('name')))[:255] or None
        if primary_deed['sellers']:
            sale_seller_primary = '; '.join(dict.fromkeys(
                p['name'] for p in primary_deed['sellers'] if p.get('name')))[:255] or None
        # B3: cash/LTV refer to financing at the purchase, not a later refi.
        purchase_mortgage = find_purchase_mortgage(transactions, sale_date)
        is_cash_purchase = purchase_mortgage is None
        if sale_price and sale_price > 0:
            financing_ratio = (0 if is_cash_purchase else
                               purchase_mortgage['doc_amount'] / sale_price)
        if sale_date:
            days_since_sale = max((date.today() - sale_date).days, 0)

    mortgage_amount = mortgage_date = mortgage_lender_primary = mortgage_crfn = None
    if primary_mortgage:
        mortgage_amount = primary_mortgage['doc_amount'] if primary_mortgage['doc_amount'] > 0 else None
        mortgage_date = primary_mortgage['doc_date'] or primary_mortgage['recorded_date']
        mortgage_crfn = primary_mortgage['crfn']
        if primary_mortgage['lenders']:
            mortgage_lender_primary = '; '.join(dict.fromkeys(
                p['name'] for p in primary_mortgage['lenders'] if p.get('name')))[:255] or None

    cur.execute("""
        UPDATE buildings
        SET sale_price = %s, sale_date = %s, sale_recorded_date = %s,
            sale_buyer_primary = %s, sale_seller_primary = %s,
            sale_percent_transferred = %s, sale_crfn = %s,
            mortgage_amount = %s, mortgage_date = %s,
            mortgage_lender_primary = %s, mortgage_crfn = %s,
            is_cash_purchase = %s, financing_ratio = %s,
            days_since_sale = %s,
            acris_total_transactions = %s, acris_deed_count = %s,
            acris_mortgage_count = %s, acris_satisfaction_count = %s,
            acris_last_enriched = CURRENT_TIMESTAMP,
            last_updated = CURRENT_TIMESTAMP
        WHERE id = %s
    """, (
        sale_price, sale_date, sale_recorded_date,
        sale_buyer_primary, sale_seller_primary, sale_percent_transferred, sale_crfn,
        mortgage_amount, mortgage_date, mortgage_lender_primary, mortgage_crfn,
        is_cash_purchase, financing_ratio, days_since_sale,
        len(transactions), deed_count, mortgage_count, satisfaction_count,
        building_id,
    ))

    status = derive_mortgage_status(transactions, references or [])
    cur.execute("SAVEPOINT acris_equity")
    try:
        cur.execute("""
            UPDATE buildings
            SET has_open_mortgage = %s, is_free_and_clear = %s,
                open_mortgage_count = %s, last_satisfaction_date = %s
            WHERE id = %s
        """, (status['has_open_mortgage'], status['is_free_and_clear'],
              status['open_mortgage_count'], status['last_satisfaction_date'],
              building_id))
        cur.execute("RELEASE SAVEPOINT acris_equity")
    except psycopg2.Error:
        cur.execute("ROLLBACK TO SAVEPOINT acris_equity")


# ---------------------------------------------------------------------------
# Single-building entry point (used by auto-add and targeted scripts)
# ---------------------------------------------------------------------------

def enrich_building_from_acris(conn, building_id, bbl):
    """Fetch + persist ACRIS data for one building. Returns the number of
    transactions stored (0 if none found)."""
    history = get_acris_full_history(bbl)
    transactions = history['transactions']
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        if transactions:
            status = derive_mortgage_status(transactions, history['references'])
            primary_mortgage = find_primary_mortgage(
                transactions, status['open_ids'])
            primary_deed, _stored_primary_mortgage = save_transactions_and_parties(
                cur, building_id, bbl, transactions,
                primary_mortgage['document_id'] if primary_mortgage else None)
            save_references(cur, building_id, bbl, history['references'])
            update_buildings_table(cur, building_id, transactions,
                                   primary_deed, primary_mortgage,
                                   history['references'])
        else:
            cur.execute("""
                UPDATE buildings
                SET acris_last_enriched = CURRENT_TIMESTAMP,
                    last_updated = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (building_id,))
        if table_has_column(cur, 'buildings', 'acris_last_attempted'):
            cur.execute("""
                UPDATE buildings
                SET acris_last_attempted = CURRENT_TIMESTAMP,
                    acris_last_error = NULL,
                    acris_logic_version = %s
                WHERE id = %s
            """, (ACRIS_LOGIC_VERSION, building_id))
        conn.commit()
    finally:
        cur.close()
    return len(transactions)


# ---------------------------------------------------------------------------
# Batch run
# ---------------------------------------------------------------------------

def enrich_buildings_from_acris():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()

    print("Step 3: ACRIS Enrichment (batched, role-correct)")
    print("=" * 70)

    logic_version_filter = (
        "OR acris_logic_version < %s"
        if table_has_column(cur, 'buildings', 'acris_logic_version') else ""
    )
    params = (ACRIS_LOGIC_VERSION,) if logic_version_filter else ()
    cur.execute(f"""
        SELECT id, bbl, address, current_owner_name
        FROM buildings
        WHERE bbl IS NOT NULL
        AND (acris_last_enriched IS NULL
             OR acris_last_enriched < NOW() - INTERVAL '30 days'
             {logic_version_filter})
        ORDER BY id
    """, params)
    buildings = cur.fetchall()
    print(f"\n📊 Found {len(buildings)} buildings to enrich")

    if not buildings:
        print("   ✅ No buildings need enrichment.")
        cur.close()
        conn.close()
        return

    enriched = no_data = failed = 0

    for i, building in enumerate(buildings, 1):
        bbl = building['bbl']
        building_id = building['id']
        print(f"\n🔍 [{i}/{len(buildings)}] BBL {bbl} — {building['address']}")

        try:
            count = enrich_building_from_acris(conn, building_id, bbl)
            if count:
                enriched += 1
                print(f"   ✅ {count} transactions stored")
            else:
                no_data += 1
                print("   ℹ️  No ACRIS data found")

        except Exception as e:
            conn.rollback()
            print(f"   ❌ Error: {e}")
            failed += 1
            # Never advance acris_last_enriched on failure. Otherwise one
            # outage suppresses retries for 30 days. Optional diagnostic
            # columns are populated when the freshness migration is present.
            try:
                if table_has_column(cur, 'buildings', 'acris_last_attempted'):
                    cur.execute("""
                        UPDATE buildings
                        SET acris_last_attempted = CURRENT_TIMESTAMP,
                            acris_last_error = %s
                        WHERE id = %s
                    """, (str(e)[:1000], building_id))
                conn.commit()
            except Exception:
                conn.rollback()

        time.sleep(0.05)

    print("\n" + "=" * 70)
    print("✅ ACRIS Enrichment Complete")
    print(f"   Enriched: {enriched} · No data: {no_data} · Failed: {failed}")

    cur.execute("""
        SELECT COUNT(*) AS total,
               COUNT(sale_date) AS with_sales,
               COUNT(CASE WHEN is_cash_purchase THEN 1 END) AS cash
        FROM buildings WHERE acris_last_enriched IS NOT NULL
    """)
    stats = cur.fetchone()
    if stats:
        print(f"\n📊 {stats['total']} enriched · {stats['with_sales']} with sales · "
              f"{stats['cash']} cash purchases")

    cur.close()
    conn.close()


if __name__ == "__main__":
    enrich_buildings_from_acris()
