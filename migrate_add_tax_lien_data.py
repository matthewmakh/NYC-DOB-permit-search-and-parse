#!/usr/bin/env python3
"""
Migration: Add Tax Delinquency and Liens Data to Buildings Table

Adds columns to track:
- Property tax delinquency status and details
- ECB violation liens with financial amounts
- DOB violations count
- Combined risk indicators

Data Sources:
- NYC DOF Property Tax Delinquencies (9rz4-mjek)
- NYC ECB Violations (6bgk-3dad) - includes balance_due
- NYC DOB Violations (3h2n-5cm9)
"""

import psycopg2
import os
from dotenv import load_dotenv

# Load .env from dashboard_html subdirectory
load_dotenv('dashboard_html/.env')

def get_db_connection():
    """Create database connection from environment variables"""
    try:
        # Support both PGHOST and DB_HOST patterns
        host = os.getenv('PGHOST') or os.getenv('DB_HOST')
        database = os.getenv('PGDATABASE') or os.getenv('DB_NAME')
        user = os.getenv('PGUSER') or os.getenv('DB_USER')
        password = os.getenv('PGPASSWORD') or os.getenv('DB_PASSWORD')
        port = os.getenv('PGPORT') or os.getenv('DB_PORT', '5432')
        
        print(f"Connecting to: {host}:{port}/{database}")
        
        conn = psycopg2.connect(
            host=host,
            database=database,
            user=user,
            password=password,
            port=port
        )
        return conn
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        return None


def run_migration():
    """Add tax delinquency and liens columns to buildings table"""
    
    conn = get_db_connection()
    if not conn:
        return
    
    cur = conn.cursor()
    
    try:
        print("=" * 70)
        print("🔧 Adding Tax Delinquency & Liens Fields to Buildings Table")
        print("=" * 70)
        print()
        
        # Check if columns already exist
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'buildings' 
            AND column_name IN (
                'has_tax_delinquency',
                'tax_delinquency_count',
                'tax_delinquency_water_only',
                'ecb_violation_count',
                'ecb_total_balance',
                'ecb_open_violations',
                'dob_violation_count',
                'dob_open_violations',
                'tax_lien_last_checked'
            )
        """)
        
        existing_columns = [row[0] for row in cur.fetchall()]
        
        if existing_columns:
            print(f"⚠️  Some columns already exist: {', '.join(existing_columns)}")
            print("   Skipping migration to avoid conflicts.")
            cur.close()
            conn.close()
            return
        
        # Add tax delinquency columns
        print("  � Adding tax delinquency columns...")
        cur.execute("""
            ALTER TABLE buildings 
            ADD COLUMN IF NOT EXISTS has_tax_delinquency BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS tax_delinquency_count INT DEFAULT 0,
            ADD COLUMN IF NOT EXISTS tax_delinquency_water_only BOOLEAN DEFAULT FALSE
        """)
        
        # Add ECB violations columns (these have financial data)
        print("  💰 Adding ECB violation/lien columns...")
        cur.execute("""
            ALTER TABLE buildings 
            ADD COLUMN IF NOT EXISTS ecb_violation_count INT DEFAULT 0,
            ADD COLUMN IF NOT EXISTS ecb_total_balance DECIMAL(12,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS ecb_open_violations INT DEFAULT 0,
            ADD COLUMN IF NOT EXISTS ecb_total_penalty DECIMAL(12,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS ecb_amount_paid DECIMAL(12,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS ecb_most_recent_hearing_date DATE,
            ADD COLUMN IF NOT EXISTS ecb_most_recent_hearing_status VARCHAR(100),
            ADD COLUMN IF NOT EXISTS ecb_respondent_name VARCHAR(255),
            ADD COLUMN IF NOT EXISTS ecb_respondent_address VARCHAR(500),
            ADD COLUMN IF NOT EXISTS ecb_respondent_city VARCHAR(100),
            ADD COLUMN IF NOT EXISTS ecb_respondent_zip VARCHAR(10)
        """)
        
        # Add DOB violations columns
        print("  � Adding DOB violation columns...")
        cur.execute("""
            ALTER TABLE buildings 
            ADD COLUMN IF NOT EXISTS dob_violation_count INT DEFAULT 0,
            ADD COLUMN IF NOT EXISTS dob_open_violations INT DEFAULT 0
        """)
        
        # Add timestamp for when tax/lien data was last updated
        print("  📅 Adding metadata columns...")
        cur.execute("""
            ALTER TABLE buildings 
            ADD COLUMN IF NOT EXISTS tax_lien_last_checked TIMESTAMP
        """)
        
        # Create indexes for efficient filtering
        print("  🔍 Creating indexes...")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_buildings_has_tax_delinquency 
            ON buildings(has_tax_delinquency) WHERE has_tax_delinquency = TRUE
        """)
        
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_buildings_ecb_balance 
            ON buildings(ecb_total_balance) WHERE ecb_total_balance > 0
        """)
        
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_buildings_ecb_open 
            ON buildings(ecb_open_violations) WHERE ecb_open_violations > 0
        """)
        
        conn.commit()
        print()
        print("=" * 70)
        print("✅ Migration completed successfully!")
        print("=" * 70)
        print("\n📋 New columns added to buildings table:")
        print("  • has_tax_delinquency - Boolean flag for properties on delinquency list")
        print("  • tax_delinquency_count - Number of delinquency notices")
        print("  • tax_delinquency_water_only - TRUE if only water debt")
        print("  • ecb_violation_count - Total ECB violations")
        print("  • ecb_total_balance - Total outstanding ECB balance ($)")
        print("  • ecb_open_violations - Number of unresolved ECB violations")
        print("  • ecb_total_penalty - Total penalties imposed ($)")
        print("  • ecb_amount_paid - Total amount paid ($)")
        print("  • ecb_most_recent_hearing_date - Latest hearing date")
        print("  • ecb_most_recent_hearing_status - Latest hearing status")
        print("  • ecb_respondent_name - Owner/manager name from ECB records")
        print("  • ecb_respondent_address - Full respondent address")
        print("  • ecb_respondent_city - Respondent city")
        print("  • ecb_respondent_zip - Respondent ZIP code")
        print("  • dob_violation_count - Total DOB violations")
        print("  • dob_open_violations - Number of open DOB violations")
        print("  • tax_lien_last_checked - Last update timestamp")
        print("\n💡 Next step: Run step4_enrich_from_tax_liens.py to populate data")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Migration failed: {str(e)}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    run_migration()
