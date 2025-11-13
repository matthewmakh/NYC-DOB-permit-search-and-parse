"""
Script to validate phone numbers in the database and update the is_mobile column.
Uses Twilio Lookup API to determine if phone numbers are mobile or landline.
"""
import psycopg2
from psycopg2.extras import RealDictCursor
from twilio.rest import Client
import time
from dotenv import load_dotenv
import os

# === Load Environment Variables ===
load_dotenv()

# === CONFIGURATION ===
DB_HOST = os.getenv('DB_HOST') or 'maglev.proxy.rlwy.net'
DB_PORT = os.getenv('DB_PORT') or '26571'
DB_USER = os.getenv('DB_USER') or 'postgres'
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME') or 'railway'

print(f"🔍 DB Connection Info:")
print(f"   Host: {DB_HOST}")
print(f"   Port: {DB_PORT}")
print(f"   User: {DB_USER}")
print(f"   Database: {DB_NAME}")
print(f"   Password: {'***' if DB_PASSWORD else 'NOT SET'}")

DB_CONFIG = {
    'host': DB_HOST,
    'port': int(DB_PORT),
    'user': DB_USER,
    'password': DB_PASSWORD,
    'database': DB_NAME
}

# Twilio credentials
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')

print(f"🔍 Twilio Account SID: {TWILIO_ACCOUNT_SID[:10]}..." if TWILIO_ACCOUNT_SID else "NOT SET")

# Initialize Twilio client
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# === CONNECT TO DATABASE ===
try:
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    print("✅ Connected to PostgreSQL database.")
except Exception as err:
    print(f"❌ Database connection failed: {err}")
    exit(1)

# === FETCH PHONE NUMBERS TO VALIDATE ===
# Option 1: Get all phones (update all records)
# cur.execute("SELECT DISTINCT phone FROM contacts WHERE phone IS NOT NULL AND phone != ''")

# Option 2: Only get phones that haven't been validated yet (is_mobile is NULL)
cur.execute("SELECT DISTINCT phone FROM contacts WHERE phone IS NOT NULL AND phone != '' AND is_mobile IS NULL")

phones = [row['phone'] for row in cur.fetchall()]
print(f"📞 Found {len(phones)} unique phone numbers to validate.")

if len(phones) == 0:
    print("✅ All phone numbers already validated!")
    cur.close()
    conn.close()
    exit(0)

# === STATISTICS ===
validated_count = 0
mobile_count = 0
landline_count = 0
invalid_count = 0
error_count = 0

# === PROCESS EACH PHONE NUMBER ===
for index, phone in enumerate(phones, start=1):
    clean_phone = phone.strip().replace('-', '').replace('(', '').replace(')', '').replace(' ', '').replace('.', '')
    
    # Add country code if missing (assuming US numbers)
    if not clean_phone.startswith('+'):
        if len(clean_phone) == 10:
            clean_phone = '+1' + clean_phone
        elif len(clean_phone) == 11 and clean_phone.startswith('1'):
            clean_phone = '+' + clean_phone

    try:
        # Use Twilio Lookup API with line type intelligence
        phone_number = twilio_client.lookups.v2.phone_numbers(clean_phone).fetch(
            fields='line_type_intelligence'
        )
        
        # Extract line type information
        line_type_info = phone_number.line_type_intelligence
        line_type = line_type_info.get('type', '').lower() if line_type_info else 'unknown'
        carrier_name = line_type_info.get('carrier_name', 'Unknown') if line_type_info else 'Unknown'
        mobile_country_code = line_type_info.get('mobile_country_code') if line_type_info else None
        mobile_network_code = line_type_info.get('mobile_network_code') if line_type_info else None

        # Determine if mobile
        is_mobile = None
        if line_type == 'mobile':
            is_mobile = True
            mobile_count += 1
            status = "📱 MOBILE"
        elif line_type in ['landline', 'fixedvoip']:
            is_mobile = False
            landline_count += 1
            status = "☎️  LANDLINE"
        elif line_type == 'voip':
            is_mobile = False
            landline_count += 1
            status = "🌐 VOIP"
        else:
            # Unknown type, default to non-mobile
            is_mobile = False
            landline_count += 1
            status = f"❓ {line_type.upper()}"

        # Log progress
        print(f"{index}/{len(phones)} - {phone} → {status} ({carrier_name})")

        # Update database
        if is_mobile is not None:
            update_query = """
                UPDATE contacts 
                SET is_mobile = %s 
                WHERE phone = %s
            """
            cur.execute(update_query, (is_mobile, phone))
            conn.commit()
            validated_count += 1

    except Exception as e:
        error_str = str(e)
        
        # Handle invalid/non-existent numbers gracefully
        if 'invalid' in error_str.lower() or '20404' in error_str:
            print(f"{index}/{len(phones)} - {phone} → ❌ INVALID")
            # Mark as invalid (non-mobile)
            cur.execute("UPDATE contacts SET is_mobile = %s WHERE phone = %s", (False, phone))
            conn.commit()
            invalid_count += 1
        else:
            print(f"⚠️ Error checking {phone}: {e}")
            error_count += 1
        continue

    # Small delay to avoid rate limiting (though Twilio is quite generous)
    time.sleep(0.5)

# === SUMMARY ===
print("\n" + "="*60)
print("📊 VALIDATION SUMMARY")
print("="*60)
print(f"✅ Total validated: {validated_count}")
print(f"📱 Mobile numbers: {mobile_count}")
print(f"☎️  Landline numbers: {landline_count}")
print(f"❌ Invalid numbers: {invalid_count}")
print(f"⚠️  Errors: {error_count}")
print("="*60)

# === VERIFY UPDATE ===
cur.execute("SELECT COUNT(*) as total FROM contacts WHERE is_mobile IS NOT NULL")
total_validated = cur.fetchone()['total']
cur.execute("SELECT COUNT(*) as total FROM contacts WHERE is_mobile = TRUE")
total_mobile = cur.fetchone()['total']
print(f"\n📊 Database status:")
print(f"   Total validated contacts: {total_validated}")
print(f"   Total mobile contacts: {total_mobile}")

# === CLOSE DB CONNECTION ===
cur.close()
conn.close()
print("\n✅ Done!")
