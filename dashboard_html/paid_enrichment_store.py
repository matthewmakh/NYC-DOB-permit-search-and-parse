"""Verified lookup results stay private until their payment is confirmed."""
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS enrichment_payment_attempts (
    request_key TEXT PRIMARY KEY,
    request_json JSONB NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payment_id TEXT
);
CREATE TABLE IF NOT EXISTS owner_enrichment_results (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    building_id INTEGER NOT NULL REFERENCES buildings(id) ON DELETE CASCADE,
    owner_name TEXT NOT NULL,
    billing_scope TEXT NOT NULL,
    result JSONB NOT NULL,
    raw_response JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payment_id TEXT,
    granted_at TIMESTAMPTZ,
    PRIMARY KEY(user_id, building_id, owner_name)
);
"""


def grant_result(cur, user_id, building_id, owner_name, payment_id):
    """Caller commits access and the receipt together, after confirmed payment."""
    if not payment_id:
        raise ValueError('A confirmed payment or free-access receipt is required')
    cur.execute("""
        INSERT INTO user_enrichments
            (user_id, building_id, owner_name_searched, enriched_phones,
             enriched_emails, enriched_person_id, enriched_at, raw_api_response)
        SELECT user_id, building_id, owner_name, result->'phones', result->'emails',
               result->>'person_id', created_at, raw_response
        FROM owner_enrichment_results
        WHERE user_id=%s AND building_id=%s AND owner_name=UPPER(TRIM(%s))
        ON CONFLICT (user_id, building_id, owner_name_searched) DO UPDATE SET
            enriched_phones=EXCLUDED.enriched_phones,
            enriched_emails=EXCLUDED.enriched_emails,
            enriched_person_id=EXCLUDED.enriched_person_id,
            enriched_at=EXCLUDED.enriched_at,
            raw_api_response=EXCLUDED.raw_api_response
    """, (user_id, building_id, owner_name))
    if not cur.rowcount:
        raise ValueError('Verified lookup result is missing')
    cur.execute("""UPDATE owner_enrichment_results SET payment_id=%s, granted_at=NOW()
        WHERE user_id=%s AND building_id=%s AND owner_name=UPPER(TRIM(%s))""",
        (payment_id, user_id, building_id, owner_name))


def grant_owner_access(user_id, building_id, owner_name, payment_id):
    from enrichment_service import get_db_connection
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                grant_result(cur, user_id, building_id, owner_name, payment_id)
    finally:
        conn.close()

