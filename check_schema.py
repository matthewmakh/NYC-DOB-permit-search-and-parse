#!/usr/bin/env python3
"""
Quick script to check the actual database schema
"""
import psycopg2
from psycopg2.extras import RealDictCursor

# Database connection
DATABASE_URL = "postgresql://postgres:rYOeFwAQciYdTdUVPxuCqNparvRNbUov@maglev.proxy.rlwy.net:26571/railway"

try:
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    
    print("=" * 80)
    print("BUILDINGS TABLE SCHEMA")
    print("=" * 80)
    
    # Get all columns from buildings table
    cur.execute("""
        SELECT 
            column_name,
            data_type,
            character_maximum_length,
            is_nullable,
            column_default
        FROM information_schema.columns
        WHERE table_name = 'buildings'
        ORDER BY ordinal_position
    """)
    
    columns = cur.fetchall()
    
    print(f"\nTotal columns: {len(columns)}\n")
    
    for col in columns:
        col_name = col['column_name']
        data_type = col['data_type']
        max_length = f"({col['character_maximum_length']})" if col['character_maximum_length'] else ""
        nullable = "NULL" if col['is_nullable'] == 'YES' else "NOT NULL"
        default = f" DEFAULT {col['column_default']}" if col['column_default'] else ""
        
        print(f"{col_name:30} {data_type}{max_length:15} {nullable:10} {default}")
    
    print("\n" + "=" * 80)
    print("PERMITS TABLE SCHEMA")
    print("=" * 80)
    
    # Get all columns from permits table
    cur.execute("""
        SELECT 
            column_name,
            data_type,
            character_maximum_length,
            is_nullable,
            column_default
        FROM information_schema.columns
        WHERE table_name = 'permits'
        ORDER BY ordinal_position
    """)
    
    columns = cur.fetchall()
    
    print(f"\nTotal columns: {len(columns)}\n")
    
    for col in columns:
        col_name = col['column_name']
        data_type = col['data_type']
        max_length = f"({col['character_maximum_length']})" if col['character_maximum_length'] else ""
        nullable = "NULL" if col['is_nullable'] == 'YES' else "NOT NULL"
        default = f" DEFAULT {col['column_default']}" if col['column_default'] else ""
        
        print(f"{col_name:30} {data_type}{max_length:15} {nullable:10} {default}")
    
    # Check for building_id in permits
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'permits' AND column_name = 'building_id'
    """)
    has_building_id = cur.fetchone()
    
    print("\n" + "=" * 80)
    print(f"permits.building_id exists: {bool(has_building_id)}")
    print("=" * 80)
    
    cur.close()
    conn.close()
    
    print("\n✅ Schema check complete!")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
