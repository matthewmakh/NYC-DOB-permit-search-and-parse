import mysql.connector
import requests
import pandas as pd
import time
from dotenv import load_dotenv
import os

# === Load Environment Variables ===
load_dotenv()

# === CONFIGURATION ===
DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME')
}

NUMVERIFY_API_KEY = 'c9275958e9fe70cd8d4f76e25ed1e8c8'
NUMVERIFY_URL = 'http://apilayer.net/api/validate'

# === CONNECT TO DATABASE ===
try:
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    print("✅ Connected to database.")
except mysql.connector.Error as err:
    print(f"❌ Database connection failed: {err}")
    exit(1)

# === FETCH UNIQUE PHONE NUMBERS ===
cursor.execute("SELECT DISTINCT phone FROM contacts WHERE phone IS NOT NULL")
phones = [row[0] for row in cursor.fetchall()]
print(f"📞 Fetched {len(phones)} unique phone numbers.")

results = []

# === PROCESS EACH PHONE NUMBER ===
for index, phone in enumerate(phones, start=1):
    clean_phone = phone.strip().replace('-', '').replace(' ', '')
    if not clean_phone.startswith('+'):
        clean_phone = '+1' + clean_phone  # Adjust this based on your country

    params = {
        'access_key': NUMVERIFY_API_KEY,
        'number': clean_phone,
        'format': 1
    }

    try:
        response = requests.get(NUMVERIFY_URL, params=params)
        data = response.json()

        # Handle API errors (e.g., quota exceeded)
        if data.get('success') is False:
            print(f"❌ API error: {data.get('error', {}).get('info')}")
            break

        # Log progress
        print(f"{index}/{len(phones)} - {clean_phone} → {data.get('line_type')}")

        results.append({
            'original_phone': phone,
            'formatted_phone': clean_phone,
            'valid': data.get('valid'),
            'line_type': data.get('line_type'),
            'carrier': data.get('carrier'),
            'location': data.get('location'),
            'country_name': data.get('country_name')
        })

    except Exception as e:
        print(f"⚠️ Error checking {phone}: {e}")
        results.append({
            'original_phone': phone,
            'formatted_phone': clean_phone,
            'valid': False,
            'line_type': 'error',
            'carrier': None,
            'location': None,
            'country_name': None
        })

    # Respect API rate limits
    time.sleep(1)

# === EXPORT TO CSV ===
df = pd.DataFrame(results)
df.to_csv('phone_type_results.csv', index=False)
print("✅ Done. Results saved to phone_type_results.csv")

# === CLOSE DB CONNECTION ===
cursor.close()
conn.close()
