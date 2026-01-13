"""
Script to validate phone numbers using Twilio Lookup API.
Stores validation results in contacts table for reuse across all linked permits.

Uses contacts table with many-to-many relationships via permit_contacts junction table.
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

print()
print("=" * 80)
print("PHONE VALIDATION USING CONTACTS TABLE")
print("=" * 80)

# === FETCH PHONE NUMBERS TO VALIDATE ===
# Get phone numbers from contacts table that haven't been validated yet
cur.execute("""
    SELECT phone, name, role
    FROM contacts
    WHERE phone_validated_at IS NULL
    AND phone IS NOT NULL
    AND phone ~ '^[0-9]{10}$'
    ORDER BY phone
""")

contacts_to_validate = cur.fetchall()
print(f"📞 Found {len(contacts_to_validate)} contacts needing validation")
print(f"   (excluding already validated phones)")
print()

if len(contacts_to_validate) == 0:
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
skipped_count = 0

# === PROCESS EACH CONTACT ===
for index, contact in enumerate(contacts_to_validate, start=1):
    phone = contact['phone']
    name = contact['name']
    role = contact['role']
    clean_phone = phone.strip()
    
    # VALIDATION: Skip obviously invalid numbers to save API costs
    if not clean_phone or len(clean_phone) < 10:
        print(f"{index}/{len(contacts_to_validate)} - {name[:30]} ({phone}) → ⏭️  SKIP (too short)")
        invalid_count += 1
        continue
    
    # Skip fake/test numbers
    if clean_phone in ['0', '0000', '00000000', '0000000000', '1111111111', '1234567890', '1234567891']:
        print(f"{index}/{len(contacts_to_validate)} - {name[:30]} ({phone}) → ⏭️  SKIP (fake number)")
        invalid_count += 1
        continue
    
    # Skip numbers that don't look like US phone numbers
    if clean_phone.startswith('00') or (len(clean_phone) == 10 and not clean_phone[0] in '23456789'):
        print(f"{index}/{len(contacts_to_validate)} - {name[:30]} ({phone}) → ⏭️  SKIP (invalid format)")
        invalid_count += 1
        continue
    
    # Add country code if missing (assuming US numbers)
    if not clean_phone.startswith('+'):
        if len(clean_phone) == 10:
            clean_phone = '+1' + clean_phone
        elif len(clean_phone) == 11 and clean_phone.startswith('1'):
            clean_phone = '+' + clean_phone
        else:
            print(f"{index}/{len(contacts_to_validate)} - {name[:30]} ({phone}) → ⏭️  SKIP (wrong length: {len(clean_phone)})")
            invalid_count += 1
            continue

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
        print(f"{index}/{len(contacts_to_validate)} - {name[:30]} ({phone}) → {status} ({carrier_name})")

        # UPDATE contact record with validation results
        if is_mobile is not None:
            cur.execute("""
                UPDATE contacts
                SET is_mobile = %s,
                    line_type = %s,
                    carrier_name = %s,
                    phone_validated_at = CURRENT_TIMESTAMP
                WHERE phone = %s
            """, (is_mobile, line_type, carrier_name, phone))
            conn.commit()
            validated_count += 1

    except Exception as e:
        error_str = str(e)
        
        # Handle invalid/non-existent numbers gracefully
        if 'invalid' in error_str.lower() or '20404' in error_str:
            print(f"{index}/{len(contacts_to_validate)} - {name[:30]} ({phone}) → ❌ INVALID")
            # Mark as invalid in contacts table
            cur.execute("""
                UPDATE contacts
                SET is_mobile = FALSE,
                    line_type = 'invalid',
                    carrier_name = 'Invalid Number',
                    phone_validated_at = CURRENT_TIMESTAMP
                WHERE phone = %s
            """, (phone,))
            conn.commit()
            invalid_count += 1
        else:
            print(f"⚠️ Error checking {name[:30]} ({phone}): {e}")
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
print(f"⏭️  Skipped (invalid): {invalid_count}")
print(f"❌ API errors: {error_count}")
print(f"💰 API calls made: {validated_count + error_count}")
print(f"💵 Estimated cost: ${(validated_count + error_count) * 0.005:.2f}")
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
