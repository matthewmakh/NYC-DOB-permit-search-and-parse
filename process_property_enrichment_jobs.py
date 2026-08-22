#!/usr/bin/env python3
"""Recover and process durable property auto-add enrichment jobs."""

import os

import psycopg2
from dotenv import load_dotenv

import _pipeline_path  # noqa: F401
from property_lookup import process_queued_enrichment_jobs

load_dotenv()
load_dotenv('dashboard_html/.env')


def connect():
    if os.getenv('DATABASE_URL'):
        return psycopg2.connect(os.environ['DATABASE_URL'])
    return psycopg2.connect(
        host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT', '5432'),
        user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'),
    )


if __name__ == '__main__':
    limit = int(os.getenv('PROPERTY_ENRICHMENT_JOB_LIMIT', '25'))
    processed = process_queued_enrichment_jobs(connect, limit=limit)
    print(f'✅ Processed {processed} queued property enrichment job(s)')
