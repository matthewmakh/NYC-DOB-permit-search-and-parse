#!/usr/bin/env python3
import os
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv()

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    port=os.getenv('DB_PORT', '5432'),
    database=os.getenv('DB_NAME'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD')
)
cur = conn.cursor(cursor_factory=RealDictCursor)

# Get permit types
print('=== PERMIT TYPES ===')
cur.execute("""
    SELECT permit_type, COUNT(*) as count 
    FROM permits 
    WHERE permit_type IS NOT NULL 
    GROUP BY permit_type 
    ORDER BY count DESC 
    LIMIT 20
""")
for row in cur.fetchall():
    print(f"  {row['permit_type']}: {row['count']}")

# Get job types
print('\n=== JOB TYPES ===')
cur.execute("""
    SELECT job_type, COUNT(*) as count 
    FROM permits 
    WHERE job_type IS NOT NULL 
    GROUP BY job_type 
    ORDER BY count DESC 
    LIMIT 20
""")
for row in cur.fetchall():
    print(f"  {row['job_type']}: {row['count']}")

# Get building classes from buildings
print('\n=== BUILDING CLASSES (top 20) ===')
cur.execute("""
    SELECT building_class, COUNT(*) as count 
    FROM buildings 
    WHERE building_class IS NOT NULL 
    GROUP BY building_class 
    ORDER BY count DESC 
    LIMIT 20
""")
for row in cur.fetchall():
    print(f"  {row['building_class']}: {row['count']}")

# Check for residential vs commercial indicator
print('\n=== BUILDING CLASS CATEGORIES ===')
cur.execute("""
    SELECT 
        CASE 
            WHEN building_class LIKE 'A%' THEN 'A - One Family'
            WHEN building_class LIKE 'B%' THEN 'B - Two Family'
            WHEN building_class LIKE 'C%' THEN 'C - Walk-up'
            WHEN building_class LIKE 'D%' THEN 'D - Elevator'
            WHEN building_class LIKE 'R%' THEN 'R - Condo'
            WHEN building_class LIKE 'S%' THEN 'S - Mixed Res/Comm'
            WHEN building_class LIKE 'K%' OR building_class LIKE 'O%' OR building_class LIKE 'E%' THEN 'Commercial/Office'
            ELSE 'Other'
        END as category,
        COUNT(*) as count
    FROM buildings 
    WHERE building_class IS NOT NULL 
    GROUP BY category 
    ORDER BY count DESC
""")
for row in cur.fetchall():
    print(f"  {row['category']}: {row['count']}")

# Check what contact info is available in permits
print('\n=== PERMIT CONTACT FIELDS ===')
cur.execute("""
    SELECT 
        COUNT(*) as total,
        COUNT(permittee_phone) as with_permittee_phone,
        COUNT(owner_phone) as with_owner_phone,
        COUNT(permittee_business_name) as with_permittee_name,
        COUNT(applicant) as with_applicant
    FROM permits
""")
row = cur.fetchone()
print(f"  Total permits: {row['total']}")
print(f"  With permittee phone: {row['with_permittee_phone']}")
print(f"  With owner phone: {row['with_owner_phone']}")
print(f"  With permittee name: {row['with_permittee_name']}")
print(f"  With applicant: {row['with_applicant']}")

cur.close()
conn.close()
