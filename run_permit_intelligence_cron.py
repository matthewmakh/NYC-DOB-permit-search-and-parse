#!/usr/bin/env python3
"""Run the daily permit sync and canonicalize newly imported contacts."""

import os
import subprocess
import sys


ACTIVE_SOURCES = (
    "dob_now_filings",
    "dob_now_approved",
    "dob_now_electrical",
    "dob_now_electrical_details",
    "dob_now_elevator",
    "city_record",
)


def main():
    days = str(max(1, int(os.getenv("PERMIT_SYNC_DAYS", "14"))))
    subprocess.run(
        [
            sys.executable,
            "-u",
            "permit_scraper_api.py",
            "--sources",
            *ACTIVE_SOURCES,
            "--days",
            days,
        ],
        check=True,
    )
    # Canonicalize every newly imported permit contact before scoring alerts.
    # New phones remain queued for the existing validation worker.
    subprocess.run(
        [sys.executable, "-u", "sync_contacts_from_permits.py"],
        check=True,
    )
if __name__ == "__main__":
    main()
