import psycopg2, os
from dotenv import load_dotenv
load_dotenv('dashboard_html/.env')
conn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'), database=os.getenv('DB_NAME'))
cur = conn.cursor()

# Check buildings columns for zip
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'buildings' AND column_name LIKE '%zip%'")
print('Zip columns in buildings:', cur.fetchall())

# Check permits columns for zip  
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'permits' AND column_name LIKE '%zip%'")
print('Zip columns in permits:', cur.fetchall())

# Check sample data
cur.execute("SELECT address, borough, zip_code FROM permits WHERE bbl IS NOT NULL LIMIT 1")
print('Sample permit:', cur.fetchone())

conn.close()
