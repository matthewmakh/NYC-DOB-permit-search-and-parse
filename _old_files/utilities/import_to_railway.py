#!/usr/bin/env python3
"""
Import database schema and data to Railway PostgreSQL
"""
import psycopg2
import os

# Railway PostgreSQL credentials from environment variables
from dotenv import load_dotenv
load_dotenv()

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 5432)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME')
}

def import_sql_file(cursor, filepath):
    """Import a SQL file"""
    print(f"Importing {filepath}...")
    with open(filepath, 'r', encoding='utf-8') as f:
        sql = f.read()
    
    # Execute the SQL
    try:
        cursor.execute(sql)
        print(f"✅ Successfully imported {filepath}")
        return True
    except Exception as e:
        print(f"❌ Error importing {filepath}: {e}")
        return False

def main():
    print("=" * 60)
    print("Railway PostgreSQL Database Import")
    print("=" * 60)
    print()
    
    # Connect to Railway PostgreSQL
    print("Connecting to Railway PostgreSQL...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = False
        cursor = conn.cursor()
        print("✅ Connected successfully!")
        print()
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return
    
    # Get current directory
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Check if tables exist
    cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    existing_tables = [row[0] for row in cursor.fetchall()]
    
    if not existing_tables:
        # Import schema first
        schema_file = os.path.join(current_dir, 'postgres_schema.sql')
        print("Step 1: Importing schema...")
        if import_sql_file(cursor, schema_file):
            conn.commit()
            print("✅ Schema imported and committed")
        else:
            print("❌ Schema import failed, rolling back")
            conn.rollback()
            cursor.close()
            conn.close()
            return
        print()
    else:
        print(f"✅ Tables already exist: {', '.join(existing_tables)}")
        print("Skipping schema import...")
        print()
    
    # Import data
    data_file = os.path.join(current_dir, 'postgres_data.sql')
    print("Step 2: Importing data (2,025 rows)...")
    print("This may take a minute...")
    if import_sql_file(cursor, data_file):
        conn.commit()
        print("✅ Data imported and committed")
    else:
        print("❌ Data import failed, rolling back")
        conn.rollback()
        cursor.close()
        conn.close()
        return
    
    print()
    
    # Verify import
    print("Step 3: Verifying import...")
    cursor.execute("SELECT COUNT(*) FROM permits")
    permit_count = cursor.fetchone()[0]
    print(f"  Permits: {permit_count}")
    
    cursor.execute("SELECT COUNT(*) FROM permit_search_config")
    config_count = cursor.fetchone()[0]
    print(f"  Search Configs: {config_count}")
    
    cursor.execute("SELECT COUNT(*) FROM contact_scrape_jobs")
    jobs_count = cursor.fetchone()[0]
    print(f"  Scrape Jobs: {jobs_count}")
    
    total = permit_count + config_count + jobs_count
    print(f"  Total rows: {total}")
    
    print()
    
    if total == 2025:
        print("✅ Import successful! All 2,025 rows imported correctly.")
    else:
        print(f"⚠️  Warning: Expected 2,025 rows but got {total}")
    
    # Clean up
    cursor.close()
    conn.close()
    
    print()
    print("=" * 60)
    print("Import complete!")
    print("=" * 60)

if __name__ == '__main__':
    main()
