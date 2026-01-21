import psycopg2, os
from dotenv import load_dotenv
load_dotenv('dashboard_html/.env')
conn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'), database=os.getenv('DB_NAME'))
cur = conn.cursor()

# Get buildings that have been enriched by user 2
cur.execute("""
    SELECT ue.building_id, ue.owner_name_searched, b.address, b.bbl,
           b.current_owner_name, b.owner_name_rpad, b.owner_name_hpd, 
           b.sos_principal_name, b.ecb_respondent_name
    FROM user_enrichments ue
    JOIN buildings b ON ue.building_id = b.id
    WHERE ue.user_id = 2
""")

print("Buildings with enrichments - checking for multiple owners:\n")
for row in cur.fetchall():
    building_id, enriched_owner, address, bbl = row[0], row[1], row[2], row[3]
    owners = [row[4], row[5], row[6], row[7], row[8]]
    
    # Filter to person names (not LLCs)
    person_owners = []
    for owner in owners:
        if owner and 'LLC' not in owner.upper() and 'INC' not in owner.upper() and 'CORP' not in owner.upper() and ' ' in owner:
            person_owners.append(owner)
    
    # Dedupe
    unique_owners = list(set([o.upper() for o in person_owners]))
    
    if len(unique_owners) > 1:
        print(f"Building ID: {building_id}")
        print(f"BBL: {bbl}")
        print(f"Address: {address}")
        print(f"Already enriched: {enriched_owner}")
        print(f"All person owners: {unique_owners}")
        other_owners = [o for o in unique_owners if o != enriched_owner.upper()]
        print(f"Other owners available to enrich: {other_owners}")
        print("-" * 50)

# If none found, look for any building with multiple person owners
print("\n\nLooking for ANY building with multiple person owners (not yet enriched):\n")
cur.execute("""
    SELECT id, bbl, address, current_owner_name, owner_name_rpad, owner_name_hpd, 
           sos_principal_name, ecb_respondent_name
    FROM buildings
    WHERE sos_principal_name IS NOT NULL
    AND current_owner_name IS NOT NULL
    AND sos_principal_name != current_owner_name
    LIMIT 20
""")

count = 0
for row in cur.fetchall():
    building_id, bbl, address = row[0], row[1], row[2]
    owners = [row[3], row[4], row[5], row[6], row[7]]
    
    # Filter to person names (not LLCs)
    person_owners = []
    for owner in owners:
        if owner and 'LLC' not in owner.upper() and 'INC' not in owner.upper() and 'CORP' not in owner.upper() and 'TRUST' not in owner.upper() and 'ESTATE' not in owner.upper() and ' ' in owner:
            person_owners.append(owner)
    
    # Dedupe
    unique_owners = list(set(person_owners))
    
    if len(unique_owners) >= 2:
        count += 1
        print(f"Building ID: {building_id}")
        print(f"BBL: {bbl}")
        print(f"Address: {address}")
        print(f"Person owners to enrich: {unique_owners}")
        print("-" * 50)
        if count >= 5:
            break

conn.close()
