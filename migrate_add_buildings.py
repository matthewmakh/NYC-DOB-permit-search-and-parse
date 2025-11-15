#!/usr/bin/env python3
"""
Database Migration: Add BBL column to permits table and create buildings tables
Run this on existing databases that were created before BBL support was added
"""

import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv

load_dotenv()

# Support both DATABASE_URL and individual DB_* variables
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    # Build from individual components
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME')
    
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        raise ValueError("Either DATABASE_URL or DB_HOST/DB_USER/DB_PASSWORD/DB_NAME must be set")
    
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


def run_migration():
    """Apply database migrations"""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    print("Running Database Migration")
    print("=" * 60)
    
    # Check if bbl column exists in permits table
    print("\n1. Checking permits table for bbl column...")
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='permits' AND column_name='bbl'
    """)
    
    if not cur.fetchone():
        print("   Adding bbl column to permits table...")
        cur.execute("ALTER TABLE permits ADD COLUMN bbl VARCHAR(10)")
        conn.commit()
        print("   ✅ Added bbl column")
    else:
        print("   ✅ bbl column already exists")
    
    # Check if buildings table exists
    print("\n2. Checking for buildings table...")
    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_name='buildings'
    """)
    
    if not cur.fetchone():
        print("   Creating buildings table...")
        cur.execute("""
            CREATE TABLE buildings (
              id SERIAL PRIMARY KEY,
              bbl VARCHAR(10) UNIQUE NOT NULL,
              address TEXT,
              block VARCHAR(20),
              lot VARCHAR(20),
              bin VARCHAR(50),
              
              -- PLUTO enrichment fields (Step 2)
              current_owner_name VARCHAR(500),
              owner_mailing_address TEXT,
              building_class VARCHAR(10),
              land_use VARCHAR(10),
              residential_units INTEGER,
              total_units INTEGER,
              num_floors INTEGER,
              building_sqft INTEGER,
              lot_sqft INTEGER,
              year_built INTEGER,
              
              -- ACRIS enrichment fields (Step 3)
              purchase_date DATE,
              purchase_price NUMERIC(15, 2),
              mortgage_amount NUMERIC(15, 2),
              
              -- Building intelligence metrics (Steps 4-6)
              estimated_value NUMERIC(15, 2),
              total_permit_spend NUMERIC(15, 2),
              permit_count INTEGER DEFAULT 0,
              affordability_score NUMERIC(5, 2),
              renovation_need_score NUMERIC(5, 2),
              contact_quality_score NUMERIC(5, 2),
              overall_priority_score NUMERIC(5, 2),
              
              -- Metadata
              last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        print("   ✅ Created buildings table")
    else:
        print("   ✅ buildings table already exists")
    
    # Check if owner_contacts table exists
    print("\n3. Checking for owner_contacts table...")
    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_name='owner_contacts'
    """)
    
    if not cur.fetchone():
        print("   Creating owner_contacts table...")
        cur.execute("""
            CREATE TABLE owner_contacts (
              id SERIAL PRIMARY KEY,
              building_id INTEGER REFERENCES buildings(id),
              name VARCHAR(500),
              phone VARCHAR(50),
              email VARCHAR(255),
              mailing_address TEXT,
              contact_source VARCHAR(100),
              is_verified BOOLEAN DEFAULT FALSE,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        print("   ✅ Created owner_contacts table")
    else:
        print("   ✅ owner_contacts table already exists")
    
    # Check if building_metrics table exists
    print("\n4. Checking for building_metrics table...")
    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_name='building_metrics'
    """)
    
    if not cur.fetchone():
        print("   Creating building_metrics table...")
        cur.execute("""
            CREATE TABLE building_metrics (
              id SERIAL PRIMARY KEY,
              building_id INTEGER REFERENCES buildings(id),
              metric_type VARCHAR(100),
              metric_value NUMERIC(15, 2),
              recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        print("   ✅ Created building_metrics table")
    else:
        print("   ✅ building_metrics table already exists")
    
    # Create missing indexes
    print("\n5. Creating indexes...")
    indexes = [
        ("idx_permits_bbl", "CREATE INDEX IF NOT EXISTS idx_permits_bbl ON permits(bbl)"),
        ("idx_permits_block_lot", "CREATE INDEX IF NOT EXISTS idx_permits_block_lot ON permits(block, lot)"),
        ("idx_buildings_bbl", "CREATE INDEX IF NOT EXISTS idx_buildings_bbl ON buildings(bbl)"),
        ("idx_owner_contacts_building_id", "CREATE INDEX IF NOT EXISTS idx_owner_contacts_building_id ON owner_contacts(building_id)"),
        ("idx_building_metrics_building_id", "CREATE INDEX IF NOT EXISTS idx_building_metrics_building_id ON building_metrics(building_id)")
    ]
    
    for idx_name, idx_sql in indexes:
        try:
            cur.execute(idx_sql)
            conn.commit()
            print(f"   ✅ {idx_name}")
        except Exception as e:
            print(f"   ⚠️ {idx_name}: {e}")
    
    print("\n✅ Migration Complete!")
    print("\nNext Steps:")
    print("1. Run permit scrapers to populate block/lot data")
    print("2. Run step1_link_permits_to_buildings.py to create building records")
    print("3. Run step2_enrich_from_pluto.py to add owner data")
    print("4. Run step3_enrich_from_acris.py to add transaction data")
    
    cur.close()
    conn.close()


if __name__ == "__main__":
    run_migration()
