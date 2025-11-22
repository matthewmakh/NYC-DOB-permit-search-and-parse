#!/usr/bin/env python3
"""
Migration: Add HPD (Housing Preservation & Development) fields to buildings table

Adds owner data and quality indicators from HPD:
- owner_name_hpd: Corporate owner from HPD registration
- hpd_registration_id: HPD registration reference
- hpd_open_violations: Count of currently open violations
- hpd_total_violations: Total violation count
- hpd_open_complaints: Count of active complaints  
- hpd_total_complaints: Total complaint count
"""

import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def get_db_connection():
    """Create database connection from environment variables"""
    try:
        # Support both PGHOST and DB_HOST patterns
        host = os.getenv('PGHOST') or os.getenv('DB_HOST')
        database = os.getenv('PGDATABASE') or os.getenv('DB_NAME')
        user = os.getenv('PGUSER') or os.getenv('DB_USER')
        password = os.getenv('PGPASSWORD') or os.getenv('DB_PASSWORD')
        port = os.getenv('PGPORT') or os.getenv('DB_PORT', '5432')
        
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


def migrate():
    """Add HPD fields to buildings table"""
    
    conn = get_db_connection()
    if not conn:
        return
    
    cur = conn.cursor()
    
    print("=" * 70)
    print("🔄 Adding HPD fields to buildings table")
    print("=" * 70)
    print()
    
    # Check if columns already exist
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'buildings' 
        AND column_name IN (
            'owner_name_hpd',
            'hpd_registration_id',
            'hpd_open_violations',
            'hpd_total_violations',
            'hpd_open_complaints',
            'hpd_total_complaints'
        )
    """)
    
    existing_columns = [row[0] for row in cur.fetchall()]
    
    if existing_columns:
        print(f"⚠️  Some HPD columns already exist: {', '.join(existing_columns)}")
        print("   Skipping migration to avoid conflicts.")
        cur.close()
        conn.close()
        return
    
    print("Adding new columns...")
    
    # Add HPD owner field (complements current_owner_name and owner_name_rpad)
    cur.execute("""
        ALTER TABLE buildings
        ADD COLUMN IF NOT EXISTS owner_name_hpd VARCHAR(500),
        ADD COLUMN IF NOT EXISTS hpd_registration_id VARCHAR(50),
        ADD COLUMN IF NOT EXISTS hpd_open_violations INTEGER DEFAULT 0,
        ADD COLUMN IF NOT EXISTS hpd_total_violations INTEGER DEFAULT 0,
        ADD COLUMN IF NOT EXISTS hpd_open_complaints INTEGER DEFAULT 0,
        ADD COLUMN IF NOT EXISTS hpd_total_complaints INTEGER DEFAULT 0
    """)
    
    conn.commit()
    
    print("✅ Successfully added HPD columns:")
    print("   - owner_name_hpd (VARCHAR 500)")
    print("   - hpd_registration_id (VARCHAR 50)")
    print("   - hpd_open_violations (INTEGER)")
    print("   - hpd_total_violations (INTEGER)")
    print("   - hpd_open_complaints (INTEGER)")
    print("   - hpd_total_complaints (INTEGER)")
    print()
    
    # Verify the columns were added
    cur.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'buildings' 
        AND column_name LIKE 'hpd_%' OR column_name = 'owner_name_hpd'
        ORDER BY column_name
    """)
    
    new_columns = cur.fetchall()
    
    print("Verification - New HPD columns in buildings table:")
    for col_name, col_type in new_columns:
        print(f"   ✓ {col_name} ({col_type})")
    
    print()
    print("=" * 70)
    print("✅ Migration complete!")
    print("=" * 70)
    
    cur.close()
    conn.close()


if __name__ == "__main__":
    migrate()
