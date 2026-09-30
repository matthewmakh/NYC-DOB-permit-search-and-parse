"""Persist contact results and match evidence, never vendor dossiers."""
from psycopg2 import sql


VERIFICATION_FIELDS = (
    'score', 'confidence', 'name_match', 'name_match_type', 'address_kind',
    'street_match', 'zip_match', 'city_match', 'state_match',
    'verified_candidate_count', 'returned_result_count',
)
RESULT_FIELDS = ('phones', 'emails', 'person_id', 'source', 'from_api')


def minimal_match(summary):
    if not isinstance(summary, dict):
        return None
    evidence = summary.get('verification')
    return {
        'matched_name': summary.get('matched_name'),
        'verification': {key: evidence[key] for key in VERIFICATION_FIELDS if key in evidence}
        if isinstance(evidence, dict) else {},
    }


def minimal_result(result):
    result = result if isinstance(result, dict) else {}
    return {**{key: result[key] for key in RESULT_FIELDS if key in result},
            'match': minimal_match(result.get('match'))}


def public_building_record(record):
    """Legacy SELECT * endpoints must never serve globally cached paid data."""
    return {key: value for key, value in record.items()
            if not key.startswith('enriched_')}


def migrate_privacy(cur):
    """One-time cleanup under the caller's transaction; preserves paid access.

    Vendor payloads have no consumers. Keep normalized phones/emails and the
    small match summary, removing relatives, birth data and address histories.
    """
    cur.execute('SELECT pg_advisory_xact_lock(72108,0)')
    cur.execute('''CREATE TABLE IF NOT EXISTS app_data_migrations (
        name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
    cur.execute("SELECT 1 FROM app_data_migrations WHERE name='enrichment_privacy_v1'")
    if cur.fetchone():
        return
    for table, column in (
            ('owner_enrichment_results', 'raw_response'),
            ('user_enrichments', 'raw_api_response'),
            ('permit_contact_enrichments', 'enriched_raw_response'),
            ('buildings', 'enriched_raw_response')):
        cur.execute('''SELECT 1 FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=%s AND column_name=%s''',
                    (table, column))
        if cur.fetchone():
            cur.execute(sql.SQL('UPDATE {} SET {}=NULL WHERE {} IS NOT NULL').format(
                sql.Identifier(table), sql.Identifier(column), sql.Identifier(column)))
    cur.execute("SELECT to_regclass('owner_enrichment_results')")
    # Fetch by position regardless of whether this connection uses RealDictCursor.
    row = cur.fetchone()
    exists = next(iter(row.values())) if isinstance(row, dict) else row[0]
    if exists:
        result_pairs = [sql.SQL('{} , result->{}').format(sql.Literal(k), sql.Literal(k))
                        for k in RESULT_FIELDS]
        evidence_pairs = [sql.SQL("{} , result->'match'->'verification'->{}").format(
            sql.Literal(k), sql.Literal(k)) for k in VERIFICATION_FIELDS]
        cur.execute(sql.SQL('''UPDATE owner_enrichment_results SET result =
            jsonb_build_object({result_pairs}) || jsonb_build_object('match',
                CASE WHEN jsonb_typeof(result->'match')='object' THEN
                    jsonb_build_object('matched_name', result->'match'->'matched_name',
                        'verification', jsonb_strip_nulls(jsonb_build_object({evidence_pairs})))
                ELSE 'null'::jsonb END)''').format(
                    result_pairs=sql.SQL(',').join(result_pairs),
                    evidence_pairs=sql.SQL(',').join(evidence_pairs)))
    # Old HPD rows contain no evidence linking the single address to one of
    # their co-owners. Re-fetch those pairs; do not guess during migration.
    hpd_columns = ('hpd_owner_contacts', 'hpd_owner_business_address',
                   'hpd_owner_business_city', 'hpd_owner_business_state', 'hpd_owner_business_zip')
    cur.execute('''SELECT column_name FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name='buildings'
          AND column_name=ANY(%s)''', (list(hpd_columns),))
    if len(cur.fetchall()) == len(hpd_columns):
        cur.execute('''UPDATE buildings SET hpd_owner_business_address=NULL,
            hpd_owner_business_city=NULL, hpd_owner_business_state=NULL, hpd_owner_business_zip=NULL
            WHERE CASE WHEN jsonb_typeof(hpd_owner_contacts)='array'
                       THEN jsonb_array_length(hpd_owner_contacts) != 1 ELSE TRUE END
              AND (hpd_owner_business_address IS NOT NULL OR hpd_owner_business_city IS NOT NULL
                   OR hpd_owner_business_state IS NOT NULL OR hpd_owner_business_zip IS NOT NULL)''')
    cur.execute("INSERT INTO app_data_migrations(name) VALUES('enrichment_privacy_v1')")
