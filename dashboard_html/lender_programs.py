"""Tag recorded mortgages with the investor loan program their lender makes.

ACRIS records no loan type, term or rate, so a mortgage is tagged by who the
lender is, using the curated, evidence-backed list in
``data/dscr_rtl_lenders.csv`` (built and tested in ``lender_research/``).

Programs written to ``acris_transactions.loan_program``:

* ``RTL``                fix-and-flip / bridge / rehab / ground-up construction lender
* ``DSCR``               long-term investor rental lender, or a lender that also
                         makes consumer home loans when the borrower is an entity
* ``DSCR_OR_RTL``        the lender makes both; the recording cannot tell which
* ``COMMERCIAL_BRIDGE``  larger commercial / multifamily bridge lender

A lender that also makes owner-occupied consumer loans (``CONSUMER+DSCR`` in
the list) only produces a DSCR tag when the borrower is an LLC, corporation
or partnership; its loans to individuals are mostly ordinary home loans and
stay untagged.

Everything here reads rows ACRIS enrichment (step 3) already stored, so tagging
needs no network access and can re-run over every building whenever the list
changes. Run as a script for the nightly batch::

    python lender_programs.py            # tag every building that needs it
    LENDER_PROGRAMS_MAX_BUILDINGS=500 python lender_programs.py
"""

import csv
import hashlib
import os
import re
import sys
import time
from collections import namedtuple
from datetime import date
from functools import lru_cache

LOGIC_VERSION = 1
LIST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'dscr_rtl_lenders.csv')

RTL = 'RTL'
DSCR = 'DSCR'
DSCR_OR_RTL = 'DSCR_OR_RTL'
COMMERCIAL_BRIDGE = 'COMMERCIAL_BRIDGE'
PROGRAMS = (RTL, DSCR, DSCR_OR_RTL, COMMERCIAL_BRIDGE)
PROGRAM_LABELS = {
    RTL: 'Fix & flip / bridge loan',
    DSCR: 'DSCR rental loan',
    DSCR_OR_RTL: 'Investor loan (DSCR or fix & flip)',
    COMMERCIAL_BRIDGE: 'Commercial bridge loan',
}
# The API filter accepts lower-case slugs.
PROGRAM_SLUGS = {p.lower(): p for p in PROGRAMS}

_GROUP_PROGRAM = {
    'RTL': RTL,
    'DSCR': DSCR,
    'DSCR+RTL': DSCR_OR_RTL,
    'COMMERCIAL_BRIDGE': COMMERCIAL_BRIDGE,
    'CONSUMER+DSCR': DSCR,          # only with an entity borrower, see classify_mortgage
}
# When one recording names several listed lenders, the most specific wins.
_PROGRAM_RANK = {RTL: 4, DSCR: 3, DSCR_OR_RTL: 2, COMMERCIAL_BRIDGE: 1}
_CONFIDENCE_RANK = {'high': 2, 'medium': 1}

# MERS (the industry nominee) appears as a lender party. Its row only
# identifies the real lender when it names one ('MERS as nominee for X').
_MERS_RE = re.compile(r'MORTGAGE ELECTRONIC REGISTRATION|\bMERS\b')
_ENTITY_RE = re.compile(
    r'\b(LLC|L L C|INC|CORP|CORPORATION|LP|L P|LTD|LLP|PLLC|COMPANY|HOLDINGS?|PARTNERS|'
    r'ASSOCIATES|REALTY|PROPERT(Y|IES)|EQUIT(Y|IES)|VENTURES?|GROUP|DEVELOPMENT|MANAGEMENT)\b')

Lender = namedtuple('Lender', 'name group program confidence name_rx address_rx zips')


def normalize(text):
    """Upper case, punctuation except & to spaces, single spaces."""
    text = re.sub(r'[^\w& ]', ' ', (text or '').upper())
    return re.sub(r'\s+', ' ', text).strip()


def is_mortgage_doc(doc_type):
    doc_type = (doc_type or '').upper()
    return doc_type == 'M&CON' or doc_type.startswith('MTGE')


def is_entity_name(name):
    return bool(_ENTITY_RE.search(normalize(name)))


@lru_cache(maxsize=1)
def _list_bytes():
    with open(LIST_PATH, 'rb') as handle:
        return handle.read()


@lru_cache(maxsize=1)
def list_version():
    """Changes whenever the lender list or the tagging logic changes."""
    digest = hashlib.sha1(_list_bytes()).hexdigest()[:10]
    return f'{LOGIC_VERSION}-{digest}'


@lru_cache(maxsize=1)
def load_lenders():
    rows = csv.DictReader(_list_bytes().decode('utf-8').splitlines())
    lenders = []
    for row in rows:
        group = (row.get('group') or '').strip()
        if group not in _GROUP_PROGRAM or row.get('confidence') not in _CONFIDENCE_RANK:
            continue
        address_rx = (row.get('acris_address_regex') or '').strip()
        lenders.append(Lender(
            name=row['lender'],
            group=group,
            program=_GROUP_PROGRAM[group],
            confidence=row['confidence'],
            name_rx=re.compile(row['acris_regex']),
            address_rx=re.compile(address_rx) if address_rx else None,
            zips=frozenset(z.strip() for z in (row.get('acris_zips') or '').split('|') if z.strip()),
        ))
    if not lenders:
        raise RuntimeError(f'No usable lenders in {LIST_PATH}')
    return tuple(lenders)


def match_lender(name, address_1='', address_2='', zip_code=''):
    """Return (Lender, basis) for a lender party, or (None, None).

    A recorded-name match is preferred. The address rule exists for platforms
    whose loans are recorded under many private-lender names at the platform's
    own address (Roc360 at 645 Madison Ave); it also requires the ZIP.
    """
    return _match(normalize(name), normalize(f'{address_1 or ""} {address_2 or ""}'),
                  (zip_code or '').strip()[:5])


_MERS_PHRASE = re.compile(r'\bMORTGAGE ELECTRONIC REGISTRATION SYSTEMS?(?: INC)?\b|\bMERS(?: INC)?\b')
_AFTER_NOMINEE = re.compile(r'\b(?:NOMIN\w*|FOR)\b(?!.*\b(?:NOMIN\w*|FOR)\b)(.*)$')
_MERS_FILLER = re.compile(r'\b(INC|AS|A|SOLELY|NOMIN\w*|FOR|OF|ITS|THE|AND|SUCCESSORS|ASSIGNS)\b')


def _lender_part(norm):
    """The lender named on a party row. MERS rows read 'MERS AS NOMINEE FOR
    <lender>' (often misspelled: NOMINEEE, NOMINNE, 'MERS FOR <lender>') or
    '<lender> (MERS)'; a bare MERS row names no lender at all."""
    if not _MERS_RE.search(norm):
        return norm
    after = _AFTER_NOMINEE.search(norm)
    rest = after.group(1) if after else _MERS_PHRASE.sub(' ', norm)
    rest = re.sub(r'\s+', ' ', rest).strip()
    return rest if _MERS_FILLER.sub(' ', rest).strip() else ''


@lru_cache(maxsize=100000)
def _match(norm, address, zip5):
    if not norm:
        return None, None
    name = _lender_part(norm)
    named = [lender for lender in load_lenders() if name and lender.name_rx.search(name)]
    if named:
        best = max(named, key=lambda l: (_CONFIDENCE_RANK[l.confidence], _PROGRAM_RANK[l.program]))
        return best, 'lender_name'
    for lender in load_lenders():
        if lender.address_rx and zip5 in lender.zips and lender.address_rx.search(address):
            return lender, 'lender_address'
    return None, None


def classify_mortgage(lenders, borrower_names):
    """Tag one mortgage.

    ``lenders`` is a list of dicts with name / address_1 / address_2 / zip_code;
    ``borrower_names`` the borrower party names. Returns a dict with program,
    lender, confidence and basis, or None when no listed lender applies.
    """
    matches = []
    for party in lenders:
        lender, basis = match_lender(party.get('name'), party.get('address_1'),
                                     party.get('address_2'), party.get('zip_code'))
        if lender:
            matches.append((lender, basis))
    if not matches:
        return None
    entity_borrower = any(is_entity_name(n) for n in borrower_names)
    usable = [(l, b) for l, b in matches if l.group != 'CONSUMER+DSCR' or entity_borrower]
    if not usable:
        return None
    lender, basis = max(usable, key=lambda m: (
        m[1] == 'lender_name', _CONFIDENCE_RANK[m[0].confidence], _PROGRAM_RANK[m[0].program]))
    if lender.group == 'CONSUMER+DSCR':
        basis += '+entity_borrower'
    return {'program': lender.program, 'lender': lender.name[:160],
            'confidence': lender.confidence, 'basis': basis}


def summarize(tagged):
    """Building summary from tagged mortgages: dicts with program, lender,
    confidence, amount, recorded_date (date or None)."""
    summary = {
        'investor_loan_program': None, 'investor_loan_lender': None,
        'investor_loan_confidence': None, 'investor_loan_amount': None,
        'investor_loan_date': None, 'investor_loan_count': len(tagged),
        'rtl_loan_last_date': None, 'dscr_loan_last_date': None,
    }
    if not tagged:
        return summary
    latest = max(tagged, key=lambda t: (t['recorded_date'] or date.min, _PROGRAM_RANK[t['program']]))
    summary.update({
        'investor_loan_program': latest['program'],
        'investor_loan_lender': latest['lender'],
        'investor_loan_confidence': latest['confidence'],
        'investor_loan_amount': latest['amount'] if latest['amount'] else None,
        'investor_loan_date': latest['recorded_date'],
    })
    for program, key in ((RTL, 'rtl_loan_last_date'), (DSCR, 'dscr_loan_last_date')):
        dates = [t['recorded_date'] for t in tagged if t['program'] == program and t['recorded_date']]
        summary[key] = max(dates) if dates else None
    return summary


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

TRANSACTION_COLUMNS = ('loan_program', 'loan_program_lender', 'loan_program_confidence', 'loan_program_basis')
BUILDING_COLUMNS = ('investor_loan_program', 'investor_loan_lender', 'investor_loan_confidence',
                    'investor_loan_amount', 'investor_loan_date', 'investor_loan_count',
                    'rtl_loan_last_date', 'dscr_loan_last_date',
                    'lender_programs_version', 'lender_programs_tagged_at')


def _value(row, key, index):
    return row[key] if isinstance(row, dict) else row[index]


def _columns(cur, table):
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_schema = current_schema() AND table_name = %s""", (table,))
    return {_value(r, 'column_name', 0) for r in cur.fetchall()}


_SCHEMA_READY = set()


def schema_ready(cur):
    """True once migrate_add_lender_programs.py has run. A positive answer is
    cached per database schema; columns are only ever added."""
    cur.execute('SELECT current_database(), current_schema()')
    row = cur.fetchone()
    key = tuple(row.values()) if isinstance(row, dict) else tuple(row)
    if key in _SCHEMA_READY:
        return True
    ready = (set(TRANSACTION_COLUMNS) <= _columns(cur, 'acris_transactions')
             and set(BUILDING_COLUMNS) <= _columns(cur, 'buildings'))
    if ready:
        _SCHEMA_READY.add(key)
    return ready


def _party_zip_column(cur):
    cols = _columns(cur, 'acris_parties')
    return 'zip_code' if 'zip_code' in cols else 'zip'


def tag_buildings(cur, building_ids, zip_column=None):
    """Tag every mortgage of the given buildings and refresh their summaries.

    Works inside the caller's transaction (no commit). Returns the number of
    mortgages tagged with a program.
    """
    building_ids = [int(b) for b in building_ids]
    if not building_ids:
        return 0
    zip_column = zip_column or _party_zip_column(cur)
    cur.execute("""
        SELECT id, building_id, doc_type, doc_amount, recorded_date, doc_date
        FROM acris_transactions WHERE building_id = ANY(%s)
    """, (building_ids,))
    mortgages = {}
    for row in cur.fetchall():
        if is_mortgage_doc(_value(row, 'doc_type', 2)):
            mortgages[_value(row, 'id', 0)] = {
                'building_id': _value(row, 'building_id', 1),
                'amount': _value(row, 'doc_amount', 3),
                'recorded_date': _value(row, 'recorded_date', 4) or _value(row, 'doc_date', 5),
                'lenders': [], 'borrowers': [],
            }
    if mortgages:
        cur.execute(f"""
            SELECT transaction_id, party_type, party_name, address_1, address_2, {zip_column}
            FROM acris_parties
            WHERE transaction_id = ANY(%s) AND party_type IN ('lender', 'borrower')
        """, (list(mortgages),))
        for row in cur.fetchall():
            m = mortgages.get(_value(row, 'transaction_id', 0))
            if m is None:
                continue
            if _value(row, 'party_type', 1) == 'lender':
                m['lenders'].append({'name': _value(row, 'party_name', 2),
                                     'address_1': _value(row, 'address_1', 3),
                                     'address_2': _value(row, 'address_2', 4),
                                     'zip_code': _value(row, zip_column, 5)})
            else:
                m['borrowers'].append(_value(row, 'party_name', 2))

    updates, tagged_by_building = [], {b: [] for b in building_ids}
    for txn_id, m in mortgages.items():
        tag = classify_mortgage(m['lenders'], m['borrowers'])
        updates.append((txn_id,) + ((tag['program'], tag['lender'], tag['confidence'], tag['basis'])
                                    if tag else (None, None, None, None)))
        if tag:
            tagged_by_building[m['building_id']].append({
                **tag, 'amount': m['amount'], 'recorded_date': m['recorded_date']})

    # Clear stale tags first (a non-mortgage row can never carry one), then set.
    cur.execute("""UPDATE acris_transactions SET loan_program = NULL, loan_program_lender = NULL,
                       loan_program_confidence = NULL, loan_program_basis = NULL
                   WHERE building_id = ANY(%s) AND loan_program IS NOT NULL""", (building_ids,))
    tagged = [u for u in updates if u[1]]
    if tagged:
        from psycopg2.extras import execute_values
        execute_values(cur, """
            UPDATE acris_transactions t
            SET loan_program = v.program, loan_program_lender = v.lender,
                loan_program_confidence = v.confidence, loan_program_basis = v.basis
            FROM (VALUES %s) AS v(id, program, lender, confidence, basis)
            WHERE t.id = v.id
        """, tagged)

    version = list_version()
    rows = []
    for building_id, items in tagged_by_building.items():
        s = summarize(items)
        rows.append((building_id, s['investor_loan_program'], s['investor_loan_lender'],
                     s['investor_loan_confidence'], s['investor_loan_amount'], s['investor_loan_date'],
                     s['investor_loan_count'], s['rtl_loan_last_date'], s['dscr_loan_last_date'], version))
    from psycopg2.extras import execute_values
    execute_values(cur, """
        UPDATE buildings b
        SET investor_loan_program = v.program, investor_loan_lender = v.lender,
            investor_loan_confidence = v.confidence, investor_loan_amount = v.amount::numeric,
            investor_loan_date = v.loan_date::date, investor_loan_count = v.loan_count::int,
            rtl_loan_last_date = v.rtl_date::date, dscr_loan_last_date = v.dscr_date::date,
            lender_programs_version = v.version, lender_programs_tagged_at = NOW()
        FROM (VALUES %s) AS v(id, program, lender, confidence, amount, loan_date, loan_count,
                              rtl_date, dscr_date, version)
        WHERE b.id = v.id
    """, rows)
    return len(tagged)


def tag_building(cur, building_id):
    """Per-building hook for ACRIS refreshes. Never raises: tagging problems
    must not undo an ACRIS refresh, and the nightly batch retries."""
    try:
        cur.execute('SAVEPOINT lender_programs')
        if not schema_ready(cur):
            cur.execute('RELEASE SAVEPOINT lender_programs')
            return None
        count = tag_buildings(cur, [building_id])
        cur.execute('RELEASE SAVEPOINT lender_programs')
        return count
    except Exception as exc:  # noqa: BLE001
        try:
            cur.execute('ROLLBACK TO SAVEPOINT lender_programs')
        except Exception:  # noqa: BLE001
            pass
        print(f'   ⚠️  lender program tagging skipped for building {building_id}: {exc}', flush=True)
        return None


def run_batch(conn, max_buildings=0, chunk_size=2000, log=print):
    """Tag every building whose ACRIS data or tagging rules changed since it
    was last tagged. Commits per chunk. Returns a stats dict."""
    version = list_version()
    stats = {'version': version, 'buildings': 0, 'mortgages_tagged': 0, 'skipped_locked': 0}
    with conn.cursor() as cur:
        if not schema_ready(cur):
            conn.rollback()
            log('   Lender program columns are missing; run migrate_add_lender_programs.py first.')
            stats['schema_ready'] = False
            return stats
        zip_column = _party_zip_column(cur)
        cur.execute("""
            SELECT b.id FROM buildings b
            WHERE b.acris_last_enriched IS NOT NULL
              AND (b.lender_programs_version IS DISTINCT FROM %s
                   OR b.lender_programs_tagged_at IS NULL
                   OR b.lender_programs_tagged_at < b.acris_last_enriched)
            ORDER BY b.id
        """, (version,))
        ids = [_value(r, 'id', 0) for r in cur.fetchall()]
    conn.commit()
    if max_buildings:
        ids = ids[:max_buildings]
    stats['schema_ready'] = True
    log(f'   {len(ids):,} buildings need lender-program tagging (list version {version})')
    started = time.time()
    for start in range(0, len(ids), chunk_size):
        chunk = ids[start:start + chunk_size]
        with conn.cursor() as cur:
            # Skip buildings an ACRIS refresh is writing right now; the next
            # run picks them up (their tagged_at will be older than the refresh).
            cur.execute('SELECT id FROM buildings WHERE id = ANY(%s) ORDER BY id FOR UPDATE SKIP LOCKED',
                        (chunk,))
            locked = [_value(r, 'id', 0) for r in cur.fetchall()]
            stats['skipped_locked'] += len(chunk) - len(locked)
            stats['mortgages_tagged'] += tag_buildings(cur, locked, zip_column=zip_column)
            stats['buildings'] += len(locked)
        conn.commit()
        log(f'   Tagged {stats["buildings"]:,}/{len(ids):,} buildings · '
            f'{stats["mortgages_tagged"]:,} investor mortgages · {time.time() - started:.1f}s')
    return stats


def coverage_report(conn, log=print):
    with conn.cursor() as cur:
        cur.execute("""SELECT loan_program, COUNT(*) AS n FROM acris_transactions
                       WHERE loan_program IS NOT NULL GROUP BY 1 ORDER BY 2 DESC""")
        for row in cur.fetchall():
            program, count = _value(row, 'loan_program', 0), _value(row, 'n', 1)
            log(f'   {count:>8,}  {PROGRAM_LABELS.get(program, program)}')
        cur.execute("""SELECT COUNT(*) FILTER (WHERE investor_loan_program IS NOT NULL) AS with_loan,
                              COUNT(*) FILTER (WHERE rtl_loan_last_date >= CURRENT_DATE - 365) AS recent_rtl
                       FROM buildings""")
        row = cur.fetchone()
        with_loan, recent_rtl = _value(row, 'with_loan', 0), _value(row, 'recent_rtl', 1)
        log(f'   {with_loan:,} buildings with an investor loan · {recent_rtl:,} with a '
            f'fix & flip / bridge loan in the last 12 months')
    conn.rollback()


def main():
    import psycopg2
    sys.stdout.reconfigure(line_buffering=True)
    dsn = os.getenv('DATABASE_URL')
    if not dsn:
        required = ('DB_HOST', 'DB_USER', 'DB_PASSWORD', 'DB_NAME')
        if not all(os.getenv(k) for k in required):
            raise SystemExit('DATABASE_URL or DB_HOST/DB_USER/DB_PASSWORD/DB_NAME must be set')
        dsn = (f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}@"
               f"{os.environ['DB_HOST']}:{os.getenv('DB_PORT', '5432')}/{os.environ['DB_NAME']}")
    print('Step 3b: Tag investor loan programs (DSCR / RTL) on recorded mortgages')
    print('=' * 70)
    print(f'   {len(load_lenders())} lenders in {os.path.relpath(LIST_PATH)}')
    conn = psycopg2.connect(dsn, connect_timeout=15)
    try:
        stats = run_batch(conn, max_buildings=max(0, int(os.getenv('LENDER_PROGRAMS_MAX_BUILDINGS', '0'))))
        if stats.get('schema_ready'):
            coverage_report(conn)
        print('✅ Lender program tagging complete')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
