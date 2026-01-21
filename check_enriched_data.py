import psycopg2, os, json
from dotenv import load_dotenv
load_dotenv('dashboard_html/.env')
conn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'), database=os.getenv('DB_NAME'))
cur = conn.cursor()

# Check the building for 2134 Ocean Parkway
cur.execute("SELECT id, bbl, address, COALESCE(current_owner_name, owner_name_rpad) as owner_name, enriched_phones, enriched_emails FROM buildings WHERE address ILIKE '%2134 OCEAN PARKWAY%'")
row = cur.fetchone()
if row:
    building_id = row[0]
    print(f'Building ID: {building_id}')
    print(f'BBL: {row[1]}')
    print(f'Address: {row[2]}')
    print(f'Owner: {row[3]}')
    print(f'enriched_phones: {json.dumps(row[4], indent=2) if row[4] else None}')
    print(f'enriched_emails: {json.dumps(row[5], indent=2) if row[5] else None}')
    
    # Check user and unlocked buildings
    cur.execute("SELECT id, email FROM users WHERE email = 'matt@tyeny.com'")
    user = cur.fetchone()
    if user:
        print(f"\nUser ID: {user[0]}, Email: {user[1]}")
        cur.execute("SELECT building_id FROM user_enrichments WHERE user_id = %s AND building_id = %s", (user[0], building_id))
        unlocked = cur.fetchone()
        print(f"Building {building_id} unlocked: {unlocked is not None}")
        cur.execute("SELECT COUNT(*) FROM user_enrichments WHERE user_id = %s", (user[0],))
        count = cur.fetchone()[0]
        print(f"Total unlocked buildings: {count}")
else:
    print('Building not found')
