#!/usr/bin/env python3
"""
Migration: Add building intelligence tables
Creates the core tables needed for building ownership, valuation, and targeting system
"""

import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://postgres:rYOeFwAQciYdTdUVPxuCqNparvRNbUov@maglev.proxy.rlwy.net:26571/railway')

def run_migration():
    """Create building intelligence tables"""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    print("Starting migration: Building Intelligence System")
    print("=" * 60)
    
    # 1. Buildings table - core hub
    print("\n1. Creating buildings table...")
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS buildings (
                id SERIAL PRIMARY KEY,
                bbl VARCHAR(10) UNIQUE NOT NULL,
                address TEXT,
                borough VARCHAR(20),
                block VARCHAR(20),
                lot VARCHAR(20),
                bin VARCHAR(50),
                
                -- PLUTO basics
                building_class VARCHAR(10),
                land_use VARCHAR(50),
                residential_units INTEGER,
                total_units INTEGER,
                year_built INTEGER,
                num_floors INTEGER,
                building_sqft INTEGER,
                lot_sqft INTEGER,
                
                -- Current owner
                current_owner_name VARCHAR(500),
                owner_mailing_address TEXT,
                
                -- Latest ownership transaction
                purchase_date DATE,
                purchase_price DECIMAL(15,2),
                mortgage_amount DECIMAL(15,2),
                
                -- Current valuation
                estimated_value DECIMAL(15,2),
                value_source VARCHAR(50),
                estimated_rent_per_unit DECIMAL(10,2),
                estimated_annual_rent DECIMAL(15,2),
                estimated_equity DECIMAL(15,2),
                
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_buildings_bbl ON buildings(bbl)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_buildings_owner ON buildings(current_owner_name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_buildings_borough ON buildings(borough)")
        
        conn.commit()
        print("   ✓ Buildings table created")
    except Exception as e:
        print(f"   ✗ Error: {e}")
        conn.rollback()
    
    # 2. Owner contacts table
    print("\n2. Creating owner_contacts table...")
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS owner_contacts (
                id SERIAL PRIMARY KEY,
                owner_name VARCHAR(500),
                
                phone VARCHAR(50),
                phone_type VARCHAR(20),
                email VARCHAR(255),
                
                is_verified BOOLEAN DEFAULT FALSE,
                confidence VARCHAR(20),
                source VARCHAR(100),
                
                last_verified TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_owner_contacts_name ON owner_contacts(owner_name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_owner_contacts_phone ON owner_contacts(phone)")
        
        conn.commit()
        print("   ✓ Owner contacts table created")
    except Exception as e:
        print(f"   ✗ Error: {e}")
        conn.rollback()
    
    # 3. Building metrics table
    print("\n3. Creating building_metrics table...")
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS building_metrics (
                id SERIAL PRIMARY KEY,
                bbl VARCHAR(10) UNIQUE,
                
                -- Renovation spend
                total_permits_3yr INTEGER DEFAULT 0,
                total_spend_3yr DECIMAL(15,2) DEFAULT 0,
                last_permit_date DATE,
                major_work_types TEXT,
                
                -- Calculated scores (0-100)
                affordability_score INTEGER,
                renovation_need_score INTEGER,
                contact_quality_score INTEGER,
                overall_priority_score INTEGER,
                
                priority_tier VARCHAR(20),
                target_summary TEXT,
                
                last_calculated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (bbl) REFERENCES buildings(bbl) ON DELETE CASCADE
            )
        """)
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_building_metrics_bbl ON building_metrics(bbl)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_building_metrics_priority ON building_metrics(overall_priority_score DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_building_metrics_tier ON building_metrics(priority_tier)")
        
        conn.commit()
        print("   ✓ Building metrics table created")
    except Exception as e:
        print(f"   ✗ Error: {e}")
        conn.rollback()
    
    # 4. Add BBL to permits table
    print("\n4. Adding BBL column to permits table...")
    try:
        # Check if column exists
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='permits' AND column_name='bbl'
        """)
        
        if not cur.fetchone():
            cur.execute("ALTER TABLE permits ADD COLUMN bbl VARCHAR(10)")
            cur.execute("CREATE INDEX idx_permits_bbl ON permits(bbl)")
            print("   ✓ Added bbl column to permits")
        else:
            print("   ✓ BBL column already exists")
        
        conn.commit()
    except Exception as e:
        print(f"   ✗ Error: {e}")
        conn.rollback()
    
    # 5. Add BBL to contacts table
    print("\n5. Adding BBL column to contacts table...")
    try:
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='contacts' AND column_name='bbl'
        """)
        
        if not cur.fetchone():
            cur.execute("ALTER TABLE contacts ADD COLUMN bbl VARCHAR(10)")
            cur.execute("CREATE INDEX idx_contacts_bbl ON contacts(bbl)")
            print("   ✓ Added bbl column to contacts")
        else:
            print("   ✓ BBL column already exists")
        
        conn.commit()
    except Exception as e:
        print(f"   ✗ Error: {e}")
        conn.rollback()
    
    cur.close()
    conn.close()
    
    print("\n" + "=" * 60)
    print("Migration completed successfully!")
    print("\nNew tables created:")
    print("  • buildings - Core building data (PLUTO + ACRIS + valuations)")
    print("  • owner_contacts - Owner phone/email contact info")
    print("  • building_metrics - Scores and aggregated permit data")
    print("\nExisting tables updated:")
    print("  • permits - Added bbl column")
    print("  • contacts - Added bbl column")
    print("\nNext steps:")
    print("  1. Create script to populate buildings from PLUTO data")
    print("  2. Create script to enrich with ACRIS ownership data")
    print("  3. Create script to add valuations from Zillow/Redfin")
    print("  4. Create scoring calculator")
    print("  5. Create skip tracing integration for owner contacts")


if __name__ == "__main__":
    run_migration()
