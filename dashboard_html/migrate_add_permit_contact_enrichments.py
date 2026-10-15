#!/usr/bin/env python3
"""
Migration: Add permit_contact_enrichments table

This table stores enriched contact data for permit applicants/permittees.
- Tracks which user enriched each contact to avoid duplicate charges
- Stores enriched phone/email data linked to the building's contacts tab
- Identifies contact type (applicant, permittee, contractor, etc.)

Run with: python migrate_add_permit_contact_enrichments.py
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        cursor_factory=RealDictCursor
    )

def run_migration():
    conn = get_db_connection()
    cur = conn.cursor()
    
    print("🚀 Starting migration: add permit_contact_enrichments table")
    
    try:
        # Create the permit_contact_enrichments table
        print("\n1️⃣ Creating permit_contact_enrichments table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS permit_contact_enrichments (
                id SERIAL PRIMARY KEY,
                
                -- Who we enriched
                bbl VARCHAR(10) NOT NULL,
                building_id INTEGER REFERENCES buildings(id),
                permit_id INTEGER REFERENCES permits(id),
                
                -- Original permit data used for lookup
                contact_name VARCHAR(255) NOT NULL,
                contact_type VARCHAR(50) NOT NULL,  -- 'applicant', 'permittee', 'owner', 'superintendent'
                license_number VARCHAR(50),
                license_type VARCHAR(100),
                original_phone VARCHAR(50),  -- Phone from permit if any
                
                -- Enriched data from Enformion
                enriched_phones JSONB,  -- [{number, type, is_valid}]
                enriched_emails JSONB,  -- [{email, is_valid}]
                enriched_person_id VARCHAR(100),
                enriched_raw_response JSONB,
                
                -- Who enriched it and when
                first_enriched_by INTEGER REFERENCES users(id),
                first_enriched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                
                -- Tracking
                enrichment_source VARCHAR(50) DEFAULT 'enformion',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                
                -- Unique constraint: one enrichment record per contact per building
                CONSTRAINT unique_building_contact UNIQUE (bbl, contact_name, contact_type)
            )
        """)
        print("   ✅ permit_contact_enrichments table created")
        
        # Create user access tracking table (who has paid to unlock which contacts)
        print("\n2️⃣ Creating user_permit_contact_unlocks table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_permit_contact_unlocks (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                enrichment_id INTEGER NOT NULL REFERENCES permit_contact_enrichments(id),
                unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                charge_amount DECIMAL(10,2),
                stripe_charge_id VARCHAR(100),
                
                -- User can only unlock once per enrichment
                CONSTRAINT unique_user_enrichment UNIQUE (user_id, enrichment_id)
            )
        """)
        print("   ✅ user_permit_contact_unlocks table created")
        
        # Create indexes for fast lookups
        print("\n3️⃣ Creating indexes...")
        indexes = [
            ("idx_pce_bbl", "CREATE INDEX IF NOT EXISTS idx_pce_bbl ON permit_contact_enrichments(bbl)"),
            ("idx_pce_building_id", "CREATE INDEX IF NOT EXISTS idx_pce_building_id ON permit_contact_enrichments(building_id)"),
            ("idx_pce_contact_name", "CREATE INDEX IF NOT EXISTS idx_pce_contact_name ON permit_contact_enrichments(contact_name)"),
            ("idx_pce_license", "CREATE INDEX IF NOT EXISTS idx_pce_license ON permit_contact_enrichments(license_number)"),
            ("idx_upcu_user", "CREATE INDEX IF NOT EXISTS idx_upcu_user ON user_permit_contact_unlocks(user_id)"),
            ("idx_upcu_enrichment", "CREATE INDEX IF NOT EXISTS idx_upcu_enrichment ON user_permit_contact_unlocks(enrichment_id)")
        ]
        
        for name, sql in indexes:
            cur.execute(sql)
            print(f"   ✅ Index {name} created")
        
        conn.commit()
        print("\n✅ Migration completed successfully!")
        
        # Show table info
        cur.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'permit_contact_enrichments'
            ORDER BY ordinal_position
        """)
        columns = cur.fetchall()
        print(f"\n📋 permit_contact_enrichments columns:")
        for col in columns:
            print(f"   - {col['column_name']}: {col['data_type']}")
        
    except Exception as e:
        conn.rollback()
        print(f"\n❌ Migration failed: {e}")
        raise
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    run_migration()
