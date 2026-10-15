#!/usr/bin/env python3
"""Canonical contact/evidence schema and permit-contact synchronization."""

import os
from urllib.parse import urlparse

import psycopg2


def database_connection():
    """Connect using Railway/Heroku-style URLs or the legacy DB_* variables."""
    database_url = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
    if database_url:
        parsed = urlparse(database_url)
        return psycopg2.connect(
            host=parsed.hostname,
            port=parsed.port or 5432,
            dbname=parsed.path.lstrip("/"),
            user=parsed.username,
            password=parsed.password,
            sslmode="require" if parsed.hostname not in {"localhost", "127.0.0.1"} else "prefer",
        )
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "permits_db"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )


def ensure_contact_schema(conn):
    """Create the additive provenance model and dashboard-facing view."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS contact_evidence (
                id BIGSERIAL PRIMARY KEY,
                contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
                permit_id INTEGER REFERENCES permits(id) ON DELETE CASCADE,
                source VARCHAR(80) NOT NULL,
                source_record_id VARCHAR(120) NOT NULL,
                raw_name VARCHAR(255),
                raw_phone VARCHAR(80),
                normalized_phone VARCHAR(20),
                observed_role VARCHAR(100),
                role_confidence NUMERIC(4,3),
                is_mobile_observed BOOLEAN,
                validation_status VARCHAR(40) NOT NULL DEFAULT 'unverified',
                observed_at TIMESTAMP,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (source, source_record_id)
            );

            CREATE INDEX IF NOT EXISTS idx_contact_evidence_permit
                ON contact_evidence (permit_id);
            CREATE INDEX IF NOT EXISTS idx_contact_evidence_contact
                ON contact_evidence (contact_id);
            CREATE INDEX IF NOT EXISTS idx_contact_evidence_phone
                ON contact_evidence (normalized_phone)
                WHERE normalized_phone IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_contact_evidence_source
                ON contact_evidence (source, imported_at DESC);

            CREATE OR REPLACE VIEW permit_contact_directory AS
            SELECT pc.permit_id, c.id AS contact_id, c.name, c.phone,
                COALESCE(NULLIF(pc.contact_role, ''), NULLIF(c.role, ''), 'Contact') AS role,
                c.is_mobile, c.line_type, c.carrier_name, c.phone_validated_at,
                COALESCE(er.sources, 'permit_contact') AS source,
                COALESCE(er.evidence_count, 0)::BIGINT AS evidence_count,
                (c.phone_validated_at IS NULL) AS needs_revalidation,
                er.legacy_mobile_observed,
                COALESCE(er.role_confidence, 0.500)::NUMERIC(4,3) AS role_confidence,
                CASE WHEN c.phone_validated_at IS NOT NULL THEN 'validated'
                     WHEN er.has_legacy_check THEN 'legacy_checked_needs_revalidation'
                     ELSE 'unverified' END::VARCHAR(40) AS verification_status
            FROM permit_contacts pc
            JOIN contacts c ON c.id = pc.contact_id
            LEFT JOIN LATERAL (
                SELECT string_agg(DISTINCT ce.source, ', ' ORDER BY ce.source) AS sources,
                    count(*)::BIGINT AS evidence_count,
                    bool_or(ce.is_mobile_observed) AS legacy_mobile_observed,
                    max(ce.role_confidence) AS role_confidence,
                    bool_or(ce.validation_status = 'legacy_checked') AS has_legacy_check
                FROM contact_evidence ce
                WHERE ce.permit_id = pc.permit_id AND ce.contact_id = pc.contact_id
            ) er ON true

            UNION ALL

            SELECT DISTINCT ON (ce.permit_id, upper(trim(ce.raw_name)),
                                COALESCE(ce.observed_role, 'Legacy Contact'))
                ce.permit_id, NULL::INTEGER, trim(ce.raw_name)::VARCHAR(255),
                NULL::VARCHAR(50),
                COALESCE(ce.observed_role, 'Legacy Contact')::VARCHAR(100),
                NULL::BOOLEAN, NULL::VARCHAR(50), NULL::VARCHAR(255), NULL::TIMESTAMP,
                ce.source::TEXT, 1::BIGINT, false, ce.is_mobile_observed,
                COALESCE(ce.role_confidence, 0.250)::NUMERIC(4,3),
                ce.validation_status::VARCHAR(40)
            FROM contact_evidence ce
            WHERE ce.contact_id IS NULL
              AND NULLIF(trim(ce.raw_name), '') IS NOT NULL
              AND upper(trim(ce.raw_name)) NOT IN ('N/A', 'NA', 'NONE', '-')
              AND NOT EXISTS (
                SELECT 1 FROM permit_contacts pc JOIN contacts c ON c.id = pc.contact_id
                WHERE pc.permit_id = ce.permit_id
                  AND upper(trim(COALESCE(c.name, ''))) = upper(trim(ce.raw_name))
                  AND COALESCE(pc.contact_role, c.role, 'Contact') =
                      COALESCE(ce.observed_role, 'Legacy Contact')
              )

            UNION ALL

            SELECT p.id, NULL::INTEGER, trim(v.name)::VARCHAR(255),
                NULLIF(trim(v.phone), '')::VARCHAR(50), v.role::VARCHAR(100),
                NULL::BOOLEAN, NULL::VARCHAR(50), NULL::VARCHAR(255), NULL::TIMESTAMP,
                'permit_record'::TEXT, 1::BIGINT,
                (NULLIF(trim(v.phone), '') IS NOT NULL), NULL::BOOLEAN,
                1.000::NUMERIC(4,3),
                CASE WHEN NULLIF(trim(v.phone), '') IS NULL
                     THEN 'current_name_only' ELSE 'unverified' END::VARCHAR(40)
            FROM permits p
            CROSS JOIN LATERAL (VALUES
                (COALESCE(NULLIF(p.permittee_business_name, ''), NULLIF(p.applicant, '')),
                 p.permittee_phone, 'Permittee'),
                (p.owner_business_name, p.owner_phone, 'Owner'),
                (COALESCE(NULLIF(p.superintendent_business_name, ''),
                          NULLIF(p.superintendent_name, '')), NULL::VARCHAR, 'Superintendent'),
                (p.site_safety_mgr_business_name, NULL::VARCHAR, 'Site Safety Manager')
            ) AS v(name, phone, role)
            WHERE NULLIF(trim(v.name), '') IS NOT NULL
              AND upper(trim(v.name)) NOT IN ('N/A', 'NA', 'NONE', '-')
              AND NOT EXISTS (
                SELECT 1 FROM permit_contacts pc JOIN contacts c ON c.id = pc.contact_id
                WHERE pc.permit_id = p.id AND (
                    (NULLIF(regexp_replace(v.phone, '[^0-9]', '', 'g'), '') IS NOT NULL
                     AND right(regexp_replace(c.phone, '[^0-9]', '', 'g'), 10) =
                         right(regexp_replace(v.phone, '[^0-9]', '', 'g'), 10))
                    OR (upper(trim(COALESCE(c.name, ''))) = upper(trim(v.name))
                        AND COALESCE(pc.contact_role, c.role, 'Contact') = v.role)
                )
              )
              AND NOT EXISTS (
                SELECT 1 FROM contact_evidence ce
                WHERE ce.permit_id = p.id AND ce.contact_id IS NULL
                  AND upper(trim(ce.raw_name)) = upper(trim(v.name))
                  AND COALESCE(ce.observed_role, 'Legacy Contact') = v.role
              );
            """
        )
    conn.commit()


def sync_contacts_from_current_permits(conn):
    """Idempotently canonicalize current permittee/owner phones and evidence."""
    observations = """
        SELECT p.id AS permit_id, p.permit_no,
               COALESCE(NULLIF(p.permittee_business_name, ''),
                        NULLIF(p.applicant, ''), 'Unknown') AS name,
               p.permittee_phone AS raw_phone, 'Permittee'::VARCHAR AS role
        FROM permits p WHERE NULLIF(p.permittee_phone, '') IS NOT NULL
        UNION ALL
        SELECT p.id, p.permit_no,
               COALESCE(NULLIF(p.owner_business_name, ''), 'Unknown'),
               p.owner_phone, 'Owner'::VARCHAR
        FROM permits p WHERE NULLIF(p.owner_phone, '') IS NOT NULL
    """
    normalize = """
        SELECT *, CASE
            WHEN length(regexp_replace(raw_phone, '[^0-9]', '', 'g')) = 10
                THEN regexp_replace(raw_phone, '[^0-9]', '', 'g')
            WHEN length(regexp_replace(raw_phone, '[^0-9]', '', 'g')) = 11
                 AND left(regexp_replace(raw_phone, '[^0-9]', '', 'g'), 1) = '1'
                THEN right(regexp_replace(raw_phone, '[^0-9]', '', 'g'), 10)
        END AS phone FROM observations
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH observations AS ({observations}), normalized AS ({normalize}),
            selected AS (
                SELECT DISTINCT ON (phone) name, phone, role
                FROM normalized WHERE phone IS NOT NULL
                ORDER BY phone, CASE WHEN name = 'Unknown' THEN 1 ELSE 0 END, permit_id DESC
            )
            INSERT INTO contacts (name, phone, role)
            SELECT name, phone, role FROM selected ON CONFLICT (phone) DO NOTHING
            """
        )
        new_contacts = cur.rowcount
        cur.execute(
            f"""
            WITH observations AS ({observations}), normalized AS ({normalize}),
            resolved AS (
                SELECT n.*, c.id AS contact_id FROM normalized n
                JOIN contacts c
                  ON right(regexp_replace(c.phone, '[^0-9]', '', 'g'), 10) = n.phone
                WHERE n.phone IS NOT NULL
            )
            INSERT INTO contact_evidence (
                contact_id, permit_id, source, source_record_id,
                raw_name, raw_phone, normalized_phone, observed_role,
                role_confidence, validation_status, metadata, updated_at
            )
            SELECT contact_id, permit_id, 'permit_record',
                   permit_id::TEXT || ':' || lower(role),
                   name, raw_phone, phone, role, 1.000, 'current_record',
                   jsonb_build_object('permit_no', permit_no), CURRENT_TIMESTAMP
            FROM resolved
            ON CONFLICT (source, source_record_id) DO UPDATE SET
                contact_id = EXCLUDED.contact_id, permit_id = EXCLUDED.permit_id,
                raw_name = EXCLUDED.raw_name, raw_phone = EXCLUDED.raw_phone,
                normalized_phone = EXCLUDED.normalized_phone,
                observed_role = EXCLUDED.observed_role,
                role_confidence = EXCLUDED.role_confidence,
                validation_status = EXCLUDED.validation_status,
                metadata = EXCLUDED.metadata, updated_at = CURRENT_TIMESTAMP
            WHERE (contact_evidence.contact_id, contact_evidence.permit_id,
                   contact_evidence.raw_name, contact_evidence.raw_phone,
                   contact_evidence.normalized_phone, contact_evidence.observed_role,
                   contact_evidence.role_confidence, contact_evidence.validation_status,
                   contact_evidence.metadata)
                  IS DISTINCT FROM
                  (EXCLUDED.contact_id, EXCLUDED.permit_id,
                   EXCLUDED.raw_name, EXCLUDED.raw_phone,
                   EXCLUDED.normalized_phone, EXCLUDED.observed_role,
                   EXCLUDED.role_confidence, EXCLUDED.validation_status,
                   EXCLUDED.metadata)
            """
        )
        evidence_upserts = cur.rowcount
        cur.execute(
            """
            INSERT INTO permit_contacts (permit_id, contact_id, contact_role)
            SELECT DISTINCT permit_id, contact_id, observed_role
            FROM contact_evidence
            WHERE source = 'permit_record' AND contact_id IS NOT NULL
            ON CONFLICT (permit_id, contact_id, contact_role) DO NOTHING
            """
        )
        new_links = cur.rowcount
    conn.commit()
    ensure_contact_schema(conn)
    return {"new_contacts": new_contacts, "evidence_upserts": evidence_upserts,
            "new_links": new_links}
