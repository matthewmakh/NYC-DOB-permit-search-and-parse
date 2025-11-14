import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os

load_dotenv()

# Connect to database
conn = psycopg2.connect(
    host=os.getenv('DB_HOST', 'maglev.proxy.rlwy.net'),
    port=int(os.getenv('DB_PORT', '26571')),
    user=os.getenv('DB_USER', 'postgres'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME', 'railway')
)

cur = conn.cursor(cursor_factory=RealDictCursor)

print("📊 CONTACTS TABLE SUMMARY\n" + "="*60)

# Total contacts
cur.execute("SELECT COUNT(*) as total FROM contacts WHERE phone IS NOT NULL AND phone != ''")
total = cur.fetchone()['total']
print(f"📞 Total contacts with phone numbers: {total}")

# is_mobile statistics
cur.execute("""
    SELECT 
        COUNT(*) FILTER (WHERE is_mobile = TRUE) as mobile_count,
        COUNT(*) FILTER (WHERE is_mobile = FALSE) as landline_count,
        COUNT(*) FILTER (WHERE is_mobile IS NULL) as unknown_count
    FROM contacts 
    WHERE phone IS NOT NULL AND phone != ''
""")
mobile_stats = cur.fetchone()
print(f"\n📱 Mobile phones: {mobile_stats['mobile_count']}")
print(f"☎️  Landlines/VOIP: {mobile_stats['landline_count']}")
print(f"❓ Not yet validated: {mobile_stats['unknown_count']}")

# is_checked statistics
cur.execute("""
    SELECT 
        COUNT(*) FILTER (WHERE is_checked = TRUE) as checked_count,
        COUNT(*) FILTER (WHERE is_checked = FALSE OR is_checked IS NULL) as unchecked_count
    FROM contacts 
    WHERE phone IS NOT NULL AND phone != ''
""")
checked_stats = cur.fetchone()
print(f"\n✅ Checked: {checked_stats['checked_count']}")
print(f"⬜ Unchecked: {checked_stats['unchecked_count']}")

# Sample records
print(f"\n📋 SAMPLE RECORDS (first 10):\n" + "="*60)
cur.execute("""
    SELECT name, phone, is_mobile, is_checked 
    FROM contacts 
    WHERE phone IS NOT NULL AND phone != ''
    LIMIT 10
""")
records = cur.fetchall()
for i, rec in enumerate(records, 1):
    mobile_status = "📱 Mobile" if rec['is_mobile'] == True else "☎️  Landline" if rec['is_mobile'] == False else "❓ Unknown"
    checked_status = "✅" if rec['is_checked'] else "⬜"
    print(f"{i}. {rec['name'][:30]:30} | {rec['phone']:15} | {mobile_status:15} | {checked_status}")

cur.close()
conn.close()
