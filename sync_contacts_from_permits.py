#!/usr/bin/env python3
"""Synchronize current permit contacts into the canonical evidence model."""

import json

from contact_intelligence import (
    database_connection,
    ensure_contact_schema,
    sync_contacts_from_current_permits,
)


def main():
    conn = database_connection()
    try:
        ensure_contact_schema(conn)
        report = sync_contacts_from_current_permits(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM contacts")
            report["total_contacts"] = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM contacts WHERE phone_validated_at IS NULL")
            report["needs_phone_validation"] = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM permit_contacts")
            report["total_permit_contact_links"] = cur.fetchone()[0]
        print(json.dumps(report, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
