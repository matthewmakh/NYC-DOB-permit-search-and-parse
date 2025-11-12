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
    
    # Get column names
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    columns = [col[0] for col in cursor.fetchall()]
    columns_str = ', '.join(columns)
    
    print(f"\nExporting {table_name}: {len(rows)} rows")
    
    with open(output_file, 'a') as f:
        f.write(f"\n-- Data for table: {table_name}\n")
        
        for row in rows:
            values = ', '.join(escape_value(val) for val in row)
            # Skip the auto-increment ID column
            cols_without_id = [col for col in columns if col != 'id']
            vals_without_id = [escape_value(val) for i, val in enumerate(row) if columns[i] != 'id']
            
            insert_stmt = f"INSERT INTO {table_name} ({', '.join(cols_without_id)}) VALUES ({', '.join(vals_without_id)});\n"
            f.write(insert_stmt)
    
    return len(rows)

# Create output file
output_file = 'postgres_data.sql'
with open(output_file, 'w') as f:
    f.write("-- PostgreSQL Data Export\n")
    f.write(f"-- Generated: {datetime.now()}\n\n")

# Export each table
tables = ['permit_search_config', 'contact_scrape_jobs', 'permits']
total_rows = 0

for table in tables:
    count = export_table(table, output_file)
    total_rows += count

print(f"\n✅ Export complete: {total_rows} total rows")
print(f"📁 File created: {output_file}")

conn.close()
