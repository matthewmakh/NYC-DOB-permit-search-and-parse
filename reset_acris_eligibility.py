#!/usr/bin/env python3
"""
Reset ACRIS eligibility for buildings that were created recently 
but never got ACRIS enrichment
"""

import os
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras

load_dotenv()

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', '5432')),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)

cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

print("🔧 Resetting ACRIS eligibility for recently created buildings")
print("=" * 70)

# Find buildings that:
# 1. Have a BBL
# 2. Have never been ACRIS enriched (acris_last_enriched IS NULL)
# 3. But have a last_updated timestamp (from PLUTO/RPAD enrichment)
cur.execute("""
    SELECT id, bbl, address, last_updated, acris_last_enriched
    FROM buildings
    WHERE bbl IS NOT NULL
    AND acris_last_enriched IS NULL
    AND last_updated IS NOT NULL
""")

buildings = cur.fetchall()

print(f"\n📊 Found {len(buildings)} buildings that need ACRIS but are blocked by last_updated")

if not buildings:
    print("   ✅ No buildings need fixing!")
    cur.close()
    conn.close()
    exit(0)

print(f"\nThese buildings were enriched with PLUTO/RPAD but never got ACRIS data.")
print(f"Resetting their eligibility...\n")

for b in buildings[:5]:  # Show first 5
    print(f"   BBL {b['bbl']}: {b['address']}")
    print(f"      last_updated: {b['last_updated']}, acris_last_enriched: {b['acris_last_enriched']}")

# Reset last_updated to NULL for these buildings
# This will make them immediately eligible for enrichment again
cur.execute("""
    UPDATE buildings
    SET last_updated = NULL
    WHERE bbl IS NOT NULL
    AND acris_last_enriched IS NULL
    AND last_updated IS NOT NULL
""")

conn.commit()

print(f"\n✅ Reset {cur.rowcount} buildings")
print(f"   These buildings are now eligible for ACRIS enrichment!")

cur.close()
conn.close()
