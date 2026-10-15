#!/usr/bin/env python3
"""Migrate contacts_old_backup into the canonical contact evidence model."""

import argparse
import json

from contact_intelligence import (
    database_connection,
    ensure_contact_schema,
    sync_contacts_from_current_permits,
)


def table_exists(cur, table_name):
    cur.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
    return cur.fetchone()[0] is not None


def import_legacy_contacts(conn):
    with conn.cursor() as cur:
        if not table_exists(cur, "contacts_old_backup"):
            return {"backup_present": False, "message": "backup already retired"}
        cur.execute(
            """
            WITH legacy AS (
                SELECT b.*, CASE
                    WHEN length(regexp_replace(b.phone, '[^0-9]', '', 'g')) = 10
                        THEN regexp_replace(b.phone, '[^0-9]', '', 'g')
                    WHEN length(regexp_replace(b.phone, '[^0-9]', '', 'g')) = 11
                         AND left(regexp_replace(b.phone, '[^0-9]', '', 'g'), 1) = '1'
                        THEN right(regexp_replace(b.phone, '[^0-9]', '', 'g'), 10)
                END AS phone10 FROM contacts_old_backup b
            ), missing AS (
                SELECT DISTINCT ON (l.phone10)
                    COALESCE(NULLIF(trim(l.name), ''), 'Unknown') AS name,
                    l.phone10 AS phone
                FROM legacy l
                WHERE l.phone10 IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM contacts c
                    WHERE right(regexp_replace(c.phone, '[^0-9]', '', 'g'), 10) = l.phone10
                  )
                ORDER BY l.phone10,
                         CASE WHEN NULLIF(trim(l.name), '') IS NULL THEN 1 ELSE 0 END, l.id
            )
            INSERT INTO contacts (name, phone, role)
            SELECT name, phone, 'Legacy Contact' FROM missing
            ON CONFLICT (phone) DO NOTHING
            """
        )
        contacts_created = cur.rowcount
        cur.execute(
            """
            WITH legacy AS (
                SELECT b.*, CASE
                    WHEN length(regexp_replace(b.phone, '[^0-9]', '', 'g')) = 10
                        THEN regexp_replace(b.phone, '[^0-9]', '', 'g')
                    WHEN length(regexp_replace(b.phone, '[^0-9]', '', 'g')) = 11
                         AND left(regexp_replace(b.phone, '[^0-9]', '', 'g'), 1) = '1'
                        THEN right(regexp_replace(b.phone, '[^0-9]', '', 'g'), 10)
                END AS phone10 FROM contacts_old_backup b
            ), classified AS (
                SELECT l.*, p.permit_no,
                    CASE
                        WHEN l.phone10 IS NOT NULL AND l.phone10 =
                             right(regexp_replace(p.permittee_phone, '[^0-9]', '', 'g'), 10)
                            THEN 'Permittee'
                        WHEN l.phone10 IS NOT NULL AND l.phone10 =
                             right(regexp_replace(p.owner_phone, '[^0-9]', '', 'g'), 10)
                            THEN 'Owner'
                        WHEN NULLIF(trim(l.name), '') IS NOT NULL AND
                             upper(trim(l.name)) = upper(trim(COALESCE(
                                 NULLIF(p.permittee_business_name, ''), p.applicant, '')))
                            THEN 'Permittee'
                        WHEN NULLIF(trim(l.name), '') IS NOT NULL AND
                             upper(trim(l.name)) = upper(trim(COALESCE(p.owner_business_name, '')))
                            THEN 'Owner'
                        ELSE 'Legacy Contact'
                    END AS inferred_role,
                    CASE
                        WHEN l.phone10 IS NOT NULL AND (
                             l.phone10 = right(regexp_replace(p.permittee_phone, '[^0-9]', '', 'g'), 10)
                             OR l.phone10 = right(regexp_replace(p.owner_phone, '[^0-9]', '', 'g'), 10))
                            THEN 0.950
                        WHEN NULLIF(trim(l.name), '') IS NOT NULL AND
                             upper(trim(l.name)) IN (
                                 upper(trim(COALESCE(NULLIF(p.permittee_business_name, ''), p.applicant, ''))),
                                 upper(trim(COALESCE(p.owner_business_name, '')))
                             ) THEN 0.750
                        ELSE 0.250
                    END AS inferred_confidence
                FROM legacy l JOIN permits p ON p.id = l.permit_id
            ), resolved AS (
                SELECT cl.*, c.id AS contact_id FROM classified cl
                LEFT JOIN contacts c ON cl.phone10 IS NOT NULL
                 AND right(regexp_replace(c.phone, '[^0-9]', '', 'g'), 10) = cl.phone10
            )
            INSERT INTO contact_evidence (
                contact_id, permit_id, source, source_record_id,
                raw_name, raw_phone, normalized_phone, observed_role,
                role_confidence, is_mobile_observed, validation_status,
                observed_at, metadata, updated_at
            )
            SELECT contact_id, permit_id, 'legacy_contacts_backup', id::TEXT,
                NULLIF(trim(name), ''), NULLIF(trim(phone), ''), phone10,
                inferred_role, inferred_confidence, is_mobile,
                CASE WHEN is_checked THEN 'legacy_checked' ELSE 'legacy_unchecked' END,
                assigned_at,
                jsonb_build_object(
                    'legacy_is_checked', COALESCE(is_checked, false),
                    'legacy_assignment_present', assigned_at IS NOT NULL,
                    'permit_no', permit_no
                ), CURRENT_TIMESTAMP
            FROM resolved
            ON CONFLICT (source, source_record_id) DO UPDATE SET
                contact_id = EXCLUDED.contact_id, permit_id = EXCLUDED.permit_id,
                raw_name = EXCLUDED.raw_name, raw_phone = EXCLUDED.raw_phone,
                normalized_phone = EXCLUDED.normalized_phone,
                observed_role = EXCLUDED.observed_role,
                role_confidence = EXCLUDED.role_confidence,
                is_mobile_observed = EXCLUDED.is_mobile_observed,
                validation_status = EXCLUDED.validation_status,
                observed_at = EXCLUDED.observed_at, metadata = EXCLUDED.metadata,
                updated_at = CURRENT_TIMESTAMP
            WHERE (contact_evidence.contact_id, contact_evidence.permit_id,
                   contact_evidence.raw_name, contact_evidence.raw_phone,
                   contact_evidence.normalized_phone, contact_evidence.observed_role,
                   contact_evidence.role_confidence, contact_evidence.is_mobile_observed,
                   contact_evidence.validation_status, contact_evidence.observed_at,
                   contact_evidence.metadata)
                  IS DISTINCT FROM
                  (EXCLUDED.contact_id, EXCLUDED.permit_id,
                   EXCLUDED.raw_name, EXCLUDED.raw_phone,
                   EXCLUDED.normalized_phone, EXCLUDED.observed_role,
                   EXCLUDED.role_confidence, EXCLUDED.is_mobile_observed,
                   EXCLUDED.validation_status, EXCLUDED.observed_at,
                   EXCLUDED.metadata)
            """
        )
        evidence_upserts = cur.rowcount
        cur.execute(
            """
            INSERT INTO permit_contacts (permit_id, contact_id, contact_role)
            SELECT DISTINCT permit_id, contact_id, COALESCE(observed_role, 'Legacy Contact')
            FROM contact_evidence
            WHERE source = 'legacy_contacts_backup' AND contact_id IS NOT NULL
            ON CONFLICT (permit_id, contact_id, contact_role) DO NOTHING
            """
        )
        links_created = cur.rowcount
    conn.commit()
    ensure_contact_schema(conn)
    return {"backup_present": True, "contacts_created": contacts_created,
            "evidence_upserts": evidence_upserts, "links_created": links_created}


def reconcile(conn):
    with conn.cursor() as cur:
        if not table_exists(cur, "contacts_old_backup"):
            cur.execute("SELECT count(*) FROM contact_evidence WHERE source = 'legacy_contacts_backup'")
            return {"backup_present": False, "legacy_evidence_rows": cur.fetchone()[0], "ok": True}
        cur.execute(
            """
            WITH legacy AS (
                SELECT b.*, CASE
                    WHEN length(regexp_replace(b.phone, '[^0-9]', '', 'g')) = 10
                        THEN regexp_replace(b.phone, '[^0-9]', '', 'g')
                    WHEN length(regexp_replace(b.phone, '[^0-9]', '', 'g')) = 11
                         AND left(regexp_replace(b.phone, '[^0-9]', '', 'g'), 1) = '1'
                        THEN right(regexp_replace(b.phone, '[^0-9]', '', 'g'), 10)
                END AS phone10 FROM contacts_old_backup b
            )
            SELECT
                (SELECT count(*) FROM legacy),
                (SELECT count(*) FROM contact_evidence WHERE source = 'legacy_contacts_backup'),
                (SELECT count(*) FROM legacy l WHERE NOT EXISTS (
                    SELECT 1 FROM contact_evidence ce
                    WHERE ce.source = 'legacy_contacts_backup' AND ce.source_record_id = l.id::TEXT)),
                (SELECT count(*) FROM legacy l JOIN contact_evidence ce
                    ON ce.source = 'legacy_contacts_backup' AND ce.source_record_id = l.id::TEXT
                    WHERE l.phone10 IS NOT NULL AND ce.contact_id IS NULL),
                (SELECT count(*) FROM contact_evidence ce
                    WHERE ce.source = 'legacy_contacts_backup' AND ce.contact_id IS NOT NULL
                    AND NOT EXISTS (SELECT 1 FROM permit_contacts pc
                        WHERE pc.permit_id = ce.permit_id AND pc.contact_id = ce.contact_id
                          AND pc.contact_role = ce.observed_role)),
                (SELECT count(*) FROM legacy l JOIN permits p ON p.id = l.permit_id
                    WHERE l.assigned_at IS NOT NULL
                      AND (p.assigned_to IS DISTINCT FROM l.assigned_to
                           OR p.assigned_at IS DISTINCT FROM l.assigned_at))
            """
        )
        row = cur.fetchone()
    keys = ["backup_rows", "evidence_rows", "rows_without_evidence",
            "usable_phones_without_contact", "evidence_without_link",
            "assignment_mismatches"]
    result = dict(zip(keys, row))
    result["backup_present"] = True
    result["ok"] = result["backup_rows"] == result["evidence_rows"] and all(
        result[key] == 0 for key in keys[2:]
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-only", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not (args.schema_only or args.apply):
        parser.error("choose --schema-only or --apply")
    conn = database_connection()
    try:
        ensure_contact_schema(conn)
        current = sync_contacts_from_current_permits(conn)
        legacy = None if args.schema_only else import_legacy_contacts(conn)
        report = {"current_sync": current, "legacy_import": legacy}
        if args.apply:
            report["reconciliation"] = reconcile(conn)
            if not report["reconciliation"]["ok"]:
                raise RuntimeError(f"contact reconciliation failed: {report['reconciliation']}")
        print(json.dumps(report, indent=2, default=str))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
