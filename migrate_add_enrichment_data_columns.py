#!/usr/bin/env python3
"""
Migration: Add enrichment data to user_enrichments table
This allows storing contact data per-owner instead of per-building
"""
import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv
load_dotenv('dashboard_html/.env')

def run_migration():
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME')
    )
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    try:
        print("Starting migration: Add enrichment data columns to user_enrichments")
        
        # Check if columns already exist
        cur.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'user_enrichments' AND column_name = 'enriched_phones'
        """)
        if cur.fetchone():
            print("Columns already exist, skipping migration")
            return
        
        # Add new columns for storing per-owner enrichment data
        print("Adding enriched_phones column...")
        cur.execute("""
            ALTER TABLE user_enrichments 
            ADD COLUMN IF NOT EXISTS enriched_phones JSONB
        """)
        
        print("Adding enriched_emails column...")
        cur.execute("""
            ALTER TABLE user_enrichments 
            ADD COLUMN IF NOT EXISTS enriched_emails JSONB
        """)
        
        print("Adding enriched_person_id column...")
        cur.execute("""
            ALTER TABLE user_enrichments 
            ADD COLUMN IF NOT EXISTS enriched_person_id TEXT
        """)
        
        print("Adding enriched_at column...")
        cur.execute("""
            ALTER TABLE user_enrichments 
            ADD COLUMN IF NOT EXISTS enriched_at TIMESTAMP
        """)
        
        conn.commit()
        
        # Verify
        cur.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'user_enrichments'
            ORDER BY ordinal_position
        """)
        print("\nUpdated user_enrichments columns:")
        for row in cur.fetchall():
            print(f"  {row['column_name']}")
        
        print("\nMigration complete!")
        
    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        raise
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    run_migration()
