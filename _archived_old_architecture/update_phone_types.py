"""
Script to validate phone numbers in the permits table and update mobile flags.
Uses Twilio Lookup API to determine if phone numbers are mobile or landline.

NOTE: This script is DEPRECATED - we now use area code detection in calculate_lead_score()
for instant mobile detection without API costs. See app.py line 62-77.
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
# Get unique phone numbers from permits table (permittee_phone and owner_phone columns)
cur.execute("""
    SELECT DISTINCT phone FROM (
        SELECT permittee_phone as phone FROM permits WHERE permittee_phone IS NOT NULL AND permittee_phone != ''
        UNION
        SELECT owner_phone as phone FROM permits WHERE owner_phone IS NOT NULL AND owner_phone != ''
    ) phones
    ORDER BY phone
""")

phones = [row['phone'] for row in cur.fetchall()]
print(f"📞 Found {len(phones)} unique phone numbers in permits table.")
print(f"   (from permittee_phone and owner_phone columns)")


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

        # Update database - update permits table with mobile flag
        if is_mobile is not None:
            # Add is_mobile_permittee and is_mobile_owner columns if needed
            # For now, just log the results (database schema would need updating)
            print(f"   Would update: {phone} -> is_mobile = {is_mobile}")
            # TODO: Add columns to permits table:
            # ALTER TABLE permits ADD COLUMN IF NOT EXISTS is_mobile_permittee BOOLEAN;
            # ALTER TABLE permits ADD COLUMN IF NOT EXISTS is_mobile_owner BOOLEAN;
            # Then update:
            # UPDATE permits SET is_mobile_permittee = %s WHERE permittee_phone = %s
            # UPDATE permits SET is_mobile_owner = %s WHERE owner_phone = %s
            validated_count += 1

    except Exception as e:
        error_str = str(e)
        
        # Handle invalid/non-existent numbers gracefully
        if 'invalid' in error_str.lower() or '20404' in error_str:
            print(f"{index}/{len(phones)} - {phone} → ❌ INVALID")
            # Would mark as invalid (non-mobile) in permits table
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
print(f"\n📊 Results logged (database schema needs updating to store results)")
print(f"   Mobile numbers found: {mobile_count}")
print(f"   Landline numbers found: {landline_count}")
print(f"\n� RECOMMENDATION: Use area code detection instead (see app.py calculate_lead_score)")
print(f"   No API costs, instant results, works with existing schema")

# === CLOSE DB CONNECTION ===
cur.close()
conn.close()
print("\n✅ Done!")
