#!/usr/bin/env python3
"""Idempotent migration for project, participant, and sales-alert data."""

import os

import psycopg2
from dotenv import load_dotenv

from project_intelligence import ensure_project_intelligence_schema


load_dotenv()
load_dotenv("dashboard_html/.env")


def database_connection():
    database_url = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME", "railway"),
    )


def main():
    conn = database_connection()
    try:
        ensure_project_intelligence_schema(conn)
        print("✅ Project intelligence, participants, and sales alerts are ready")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
