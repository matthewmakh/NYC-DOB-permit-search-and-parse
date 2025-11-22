#!/usr/bin/env python3
"""
Migration script to add BBL and additional permit detail columns to the permits table.
This is a safe migration that won't break existing data.
"""

import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

# Database connection
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable must be set")

def run_migration():
    """Add new columns to permits table"""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    # List of columns to add with their definitions
    columns_to_add = [
        ('block', 'VARCHAR(20)'),                    # 1. BBL Block
        ('lot', 'VARCHAR(20)'),                      # 1. BBL Lot
        ('site_fill', 'VARCHAR(100)'),               # 3. Site Fill
        ('total_dwelling_units', 'INTEGER'),         # 5. Total Dwelling Units at Location
        ('dwelling_units_occupied', 'INTEGER'),      # 6. Dwelling Units Occupied During Construction
        ('fee_type', 'VARCHAR(50)'),                 # 7. Fee Type
        ('filing_date', 'DATE'),                     # 8. Filing Date
        ('status', 'VARCHAR(50)'),                   # 10. Status
        ('proposed_job_start', 'DATE'),              # 11. Proposed Job Start
        ('work_approved', 'DATE'),                   # 12. Work Approved
        ('work_description', 'TEXT'),                # 13. Work Description
        ('job_number', 'VARCHAR(50)')                # 14. Job Number
    ]
    
    print("Starting migration to add BBL and permit detail columns...")
    
    for column_name, column_type in columns_to_add:
        try:
            # Check if column already exists
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='permits' AND column_name=%s
            """, (column_name,))
            
            if cur.fetchone():
                print(f"  ✓ Column '{column_name}' already exists, skipping")
            else:
                # Add the column
                cur.execute(f"ALTER TABLE permits ADD COLUMN {column_name} {column_type}")
                conn.commit()
                print(f"  ✓ Added column '{column_name}' ({column_type})")
        except Exception as e:
            print(f"  ✗ Error adding column '{column_name}': {e}")
            conn.rollback()
    
    # Add indexes for BBL fields
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_permits_block ON permits(block)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_permits_lot ON permits(lot)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_permits_job_number ON permits(job_number)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_permits_status ON permits(status)")
        conn.commit()
        print("  ✓ Created indexes for new fields")
    except Exception as e:
        print(f"  ✗ Error creating indexes: {e}")
        conn.rollback()
    
    cur.close()
    conn.close()
    
    print("\nMigration completed successfully!")
    print("\nNew fields added:")
    print("  - block, lot (BBL info)")
    print("  - site_fill (site fill info)")
    print("  - total_dwelling_units, dwelling_units_occupied")
    print("  - fee_type, status")
    print("  - filing_date, proposed_job_start, work_approved")
    print("  - work_description (full work description)")
    print("  - job_number (job number separate from permit)")


if __name__ == "__main__":
    run_migration()
