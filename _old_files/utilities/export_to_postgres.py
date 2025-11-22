#!/usr/bin/env python3
"""
Export MySQL data to PostgreSQL-compatible SQL
"""
from dotenv import load_dotenv
load_dotenv()

import os
import mysql.connector
from datetime import datetime

# Connect to MySQL
conn = mysql.connector.connect(
    host=os.getenv('DB_HOST'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)
cursor = conn.cursor()

def escape_value(val):
    """Escape values for PostgreSQL"""
    if val is None:
        return 'NULL'
    elif isinstance(val, str):
        # Escape single quotes
        return "'" + val.replace("'", "''").replace("\\", "\\\\") + "'"
    elif isinstance(val, (int, float)):
        return str(val)
    elif isinstance(val, datetime):
        return f"'{val.strftime('%Y-%m-%d %H:%M:%S')}'"
    elif isinstance(val, bool):
        return 'TRUE' if val else 'FALSE'
    else:
        return f"'{str(val)}'"

def export_table(table_name, output_file):
    """Export a table to PostgreSQL INSERT statements"""
    cursor.execute(f"SELECT * FROM {table_name}")
    rows = cursor.fetchall()
    
    # Get column names and types
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    columns_info = cursor.fetchall()
    columns = [col[0] for col in columns_info]
    column_types = {col[0]: col[1] for col in columns_info}
    
    # Exclude 'id' column for auto-increment
    cols_without_id = [col for col in columns if col != 'id']
    cols_str = ', '.join(cols_without_id)
    
    print(f"\nExporting {table_name}: {len(rows)} rows")
    
    with open(output_file, 'a') as f:
        f.write(f"\n-- Data for table: {table_name}\n")
        
        for row in rows:
            # Convert boolean values and exclude id column
            converted_row = []
            for i, val in enumerate(row):
                col_name = columns[i]
                
                # Skip id column
                if col_name == 'id':
                    continue
                    
                col_type = column_types[col_name]
                
                # Check if column is boolean (tinyint(1))
                if 'tinyint(1)' in col_type.lower() and val in (0, 1):
                    converted_row.append('TRUE' if val == 1 else 'FALSE')
                else:
                    converted_row.append(escape_value(val))
            
            values_str = ', '.join(converted_row)
            f.write(f"INSERT INTO {table_name} ({cols_str}) VALUES ({values_str});\n")
    
    return len(rows)

# Create output file
output_file = 'postgres_data.sql'
with open(output_file, 'w') as f:
    f.write("-- PostgreSQL Data Export\n")
    f.write(f"-- Generated: {datetime.now()}\n\n")

# Export all tables (in order of dependencies)
tables_to_export = [
    'permit_search_config',
    'contact_scrape_jobs',
    'permits',
    'contacts',
    'assignment_log'
]

total_rows = 0
for table in tables_to_export:
    try:
        rows = export_table(table, output_file)
        total_rows += rows
    except Exception as e:
        print(f"⚠️  Warning: Could not export {table}: {e}")

print(f"\n✅ Export complete: {total_rows} total rows")
print(f"📁 File created: {output_file}")

cursor.close()
conn.close()
