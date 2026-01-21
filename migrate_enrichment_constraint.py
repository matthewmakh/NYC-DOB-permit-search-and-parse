#!/usr/bin/env python3
"""
Migration: Fix user_enrichments unique constraint
Changes from (user_id, building_id) to (user_id, building_id, owner_name_searched)
This allows multiple owners to be enriched per building per user
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
        print("Starting migration: user_enrichments constraint fix")
        
        # Step 1: Check existing constraint
        cur.execute("""
            SELECT conname FROM pg_constraint 
            WHERE conrelid = 'user_enrichments'::regclass
            AND contype = 'u'
        """)
        existing = cur.fetchall()
        print(f"Existing unique constraints: {[r['conname'] for r in existing]}")
        
        # Step 2: Drop the old constraint
        print("Dropping old constraint: user_enrichments_user_id_building_id_key")
        cur.execute("""
            ALTER TABLE user_enrichments 
            DROP CONSTRAINT IF EXISTS user_enrichments_user_id_building_id_key
        """)
        
        # Step 3: Add new constraint that includes owner_name_searched
        print("Adding new constraint: user_enrichments_user_building_owner_key")
        cur.execute("""
            ALTER TABLE user_enrichments 
            ADD CONSTRAINT user_enrichments_user_building_owner_key 
            UNIQUE (user_id, building_id, owner_name_searched)
        """)
        
        # Step 4: Verify
        cur.execute("""
            SELECT conname, pg_get_constraintdef(oid) as def
            FROM pg_constraint 
            WHERE conrelid = 'user_enrichments'::regclass
            AND contype = 'u'
        """)
        new_constraints = cur.fetchall()
        print(f"New unique constraints: {[(r['conname'], r['def']) for r in new_constraints]}")
        
        conn.commit()
        print("Migration complete!")
        
    except Exception as e:
        conn.rollback()
        print(f"Migration failed: {e}")
        raise
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    run_migration()
