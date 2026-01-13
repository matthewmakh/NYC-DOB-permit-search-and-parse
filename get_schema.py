#!/usr/bin/env python3
"""
Script to print full database schema
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': os.getenv('DB_PORT', '5432'),
    'database': os.getenv('DB_NAME', 'permits_db'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', '')
}

def get_full_schema():
    """Get full database schema with all details"""
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    print("=" * 100)
    print("DATABASE SCHEMA - FULL DETAILS")
    print("=" * 100)
    
    # Get all tables
    cursor.execute("""
        SELECT tablename 
        FROM pg_tables 
        WHERE schemaname = 'public'
        ORDER BY tablename;
    """)
    
    tables = cursor.fetchall()
    
    for table in tables:
        table_name = table[0]
        print(f"\n{'=' * 100}")
        print(f"TABLE: {table_name}")
        print(f"{'=' * 100}")
        
        # Get column details
        cursor.execute("""
            SELECT 
                column_name,
                data_type,
                character_maximum_length,
                is_nullable,
                column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' 
            AND table_name = %s
            ORDER BY ordinal_position;
        """, (table_name,))
        
        columns = cursor.fetchall()
        
        print(f"\n{'Column Name':<40} {'Type':<20} {'Nullable':<10} {'Default':<30}")
        print("-" * 100)
        
        for col in columns:
            col_name, data_type, max_length, nullable, default = col
            if max_length:
                type_str = f"{data_type}({max_length})"
            else:
                type_str = data_type
            
            default_str = str(default) if default else ""
            if len(default_str) > 28:
                default_str = default_str[:25] + "..."
            
            print(f"{col_name:<40} {type_str:<20} {nullable:<10} {default_str:<30}")
        
        # Get row count
        cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
        count = cursor.fetchone()[0]
        print(f"\nRow Count: {count:,}")
        
        # Get indexes
        cursor.execute("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public' 
            AND tablename = %s
            ORDER BY indexname;
        """, (table_name,))
        
        indexes = cursor.fetchall()
        if indexes:
            print(f"\nIndexes:")
            for idx in indexes:
                print(f"  - {idx[0]}")
                print(f"    {idx[1]}")
    
    cursor.close()
    conn.close()

if __name__ == "__main__":
    try:
        get_full_schema()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
