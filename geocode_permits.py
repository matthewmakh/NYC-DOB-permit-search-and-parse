#!/usr/bin/env python3
"""
Geocode Permits - Add coordinates to permits missing latitude/longitude

This script fetches permits without coordinates and geocodes them using NYC Geoclient API.
Designed to run as a Railway cron job.

Environment Variables Required:
- DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME (Database connection)
- NYC_GEOCLIENT_APP_ID (NYC Geoclient API App ID)
- NYC_GEOCLIENT_APP_KEY (NYC Geoclient API Key)

Get free NYC Geoclient API credentials at:
https://developer.cityofnewyork.us/user/register
"""

import os
import sys
import time
import re
import requests
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras

load_dotenv()

# Configuration - Optimized for NYC Geoclient V2 User
# V2 Limits: 100 calls/sec, 2,500 calls/min, 500,000 calls/day
BATCH_SIZE = int(os.getenv('GEOCODE_BATCH_SIZE', '500'))  # Process 500 permits per run
RATE_LIMIT_DELAY = float(os.getenv('GEOCODE_DELAY', '0.01'))  # 0.01s = 100 requests/sec

# Database configuration
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME')

# NYC Geoclient API credentials
NYC_APP_ID = os.getenv('NYC_GEOCLIENT_APP_ID')
NYC_APP_KEY = os.getenv('NYC_GEOCLIENT_APP_KEY')

# Validate configuration
if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
    print("❌ Error: Database environment variables not set")
    print("Required: DB_HOST, DB_USER, DB_PASSWORD, DB_NAME")
    sys.exit(1)

if not NYC_APP_ID or not NYC_APP_KEY:
    print("⚠️  Warning: NYC Geoclient API credentials not set")
    print("Set NYC_GEOCLIENT_APP_ID and NYC_GEOCLIENT_APP_KEY for official NYC geocoding")
    print("Get free credentials at: https://developer.cityofnewyork.us/user/register")
    print("\nFalling back to address parsing from existing data...")
    USE_GEOCLIENT = False
else:
    USE_GEOCLIENT = True
    print(f"✅ NYC Geoclient API configured")


def get_db_connection():
    """Create database connection"""
    return psycopg2.connect(
        host=DB_HOST,
        port=int(DB_PORT),
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursor_factory=psycopg2.extras.RealDictCursor
    )


def parse_nyc_address(address):
    """
    Parse NYC address into components
    Returns: (house_number, street_name, borough)
    """
    if not address:
        return None, None, None
    
    # Clean up address - remove extra whitespace
    address = ' '.join(address.split()).strip().upper()
    
    # Try to extract house number and street
    match = re.match(r'^(\d+[\w-]*)\s+(.+)$', address)
    if not match:
        return None, None, None
    
    house_number = match.group(1)
    street_name = match.group(2).strip()
    
    # Extract borough if present (usually at the end)
    # Common patterns: "STREET, BOROUGH" or "STREET BOROUGH"
    borough = None
    for boro in ['MANHATTAN', 'BROOKLYN', 'QUEENS', 'BRONX', 'STATEN ISLAND']:
        if boro in street_name:
            borough = boro
            street_name = street_name.replace(f', {boro}', '').replace(boro, '').strip()
            break
    
    return house_number, street_name, borough


def geocode_with_nyc_geoclient(address, borough=None):
    """
    Geocode an address using NYC Geoclient API V2.
    V2 API requires either borough or zip code.
    Returns (latitude, longitude) or (None, None) if failed.
    """
    if not NYC_APP_ID or not NYC_APP_KEY:
        return None, None
    
    # Parse the NYC address
    house_number, street_name, parsed_borough = parse_nyc_address(address)
    if not house_number or not street_name:
        return None, None
    
    # Use provided borough (from BBL) over parsed borough
    final_borough = borough or parsed_borough
    if not final_borough:
        # V2 API requires borough or zip - if we don't have either, skip NYC Geoclient
        return None, None
    
    # V2 API uses subscription key in header, not query params
    url = "https://api.nyc.gov/geoclient/v2/address"
    headers = {
        'Ocp-Apim-Subscription-Key': NYC_APP_ID  # NYC Geoclient V2 uses Ocp-Apim-Subscription-Key header
    }
    params = {
        'houseNumber': house_number,
        'street': street_name,
        'borough': final_borough
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            # V2 API returns data in 'address' object
            if 'address' in data:
                addr_data = data['address']
                # Get coordinates - V2 uses different field names
                lat = addr_data.get('latitude')
                lon = addr_data.get('longitude')
                
                if lat and lon:
                    return float(lat), float(lon)
        else:
            # Debug: show what went wrong
            try:
                error_data = response.json()
                print(f"⚠️ NYC Geoclient status {response.status_code}: {error_data.get('message', 'Unknown error')[:80]}")
            except:
                print(f"⚠️ NYC Geoclient returned status {response.status_code}")
            
    except Exception as e:
        print(f"⚠️ NYC Geoclient error: {str(e)}")
    
    return None, None


def geocode_with_nominatim(address):
    """
    Fallback geocoding using OpenStreetMap Nominatim (free, no API key)
    Returns: (latitude, longitude) or (None, None)
    """
    try:
        import re
        
        # Clean up address and convert to proper case for better results
        clean_address = ' '.join(address.split())  # Remove extra whitespace
        
        # Convert to title case for better OSM matching
        clean_address = clean_address.title()
        
        # Fix ordinal numbers (141TH → 141st, 22ND → 22nd, etc.)
        def fix_ordinals(match):
            num = match.group(1)
            suffix = match.group(2).lower() if match.group(2) else ''
            
            # Determine correct suffix based on number
            last_digit = num[-1]
            last_two = num[-2:] if len(num) >= 2 else num
            
            if last_two in ['11', '12', '13']:
                correct_suffix = 'th'
            elif last_digit == '1':
                correct_suffix = 'st'
            elif last_digit == '2':
                correct_suffix = 'nd'
            elif last_digit == '3':
                correct_suffix = 'rd'
            else:
                correct_suffix = 'th'
            
            return f"{num}{correct_suffix}"
        
        # Fix explicit ordinals (141TH, 22ND, etc.)
        clean_address = re.sub(r'(\d+)(?:[T][hH]|[N][dD]|[R][dD]|[S][tT])\b', fix_ordinals, clean_address)
        
        # Fix bare numbers before Street/Avenue/Place (e.g., "5 Street" → "5th Street")
        def fix_bare_number_street(match):
            num = match.group(1)
            street_type = match.group(2)
            # Determine correct suffix
            last_digit = num[-1]
            last_two = num[-2:] if len(num) >= 2 else num
            if last_two in ['11', '12', '13']:
                suffix = 'th'
            elif last_digit == '1':
                suffix = 'st'
            elif last_digit == '2':
                suffix = 'nd'
            elif last_digit == '3':
                suffix = 'rd'
            else:
                suffix = 'th'
            return f"{num}{suffix} {street_type}"
        
        clean_address = re.sub(r'(\d+)\s+(Street|Avenue|Place|Road)\b', fix_bare_number_street, clean_address)
        
        # Fix common street abbreviations
        replacements = {
            r'\bSt\b\.?': 'Street',
            r'\bAve\b\.?': 'Avenue',
            r'\bRd\b\.?': 'Road',
            r'\bBlvd\b\.?': 'Boulevard',
            r'\bPl\b\.?': 'Place',
            r'\bDr\b\.?': 'Drive',
            r'\bCt\b\.?': 'Court',
            r'\bLn\b\.?': 'Lane',
            r'\bPkwy\b\.?': 'Parkway'
        }
        
        for pattern, replacement in replacements.items():
            clean_address = re.sub(pattern, replacement, clean_address)
        
        # Try with just NYC first (most likely to work)
        url = "https://nominatim.openstreetmap.org/search"
        headers = {
            'User-Agent': 'DOB-Permit-Geocoder/1.0'
        }
        
        params = {
            'q': f"{clean_address}, New York, NY",
            'format': 'json',
            'limit': 1,
            'countrycodes': 'us'
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code == 200:
            results = response.json()
            if results and len(results) > 0:
                result = results[0]
                lat = float(result['lat'])
                lon = float(result['lon'])
                return lat, lon
        
        # Rate limit for Nominatim (1 request per second)
        time.sleep(1)
        
        return None, None
        
    except Exception as e:
        print(f"  ⚠️  Nominatim error: {str(e)}")
        return None, None


def update_permit_coordinates(conn, permit_id, latitude, longitude):
    """Update permit with geocoded coordinates"""
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE permits 
            SET latitude = %s, longitude = %s
            WHERE id = %s
        """, (latitude, longitude, permit_id))
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        print(f"  ❌ Database update error: {str(e)}")
        conn.rollback()
        return False


def geocode_permits():
    """Main geocoding function"""
    print("=" * 70)
    print("🗺️  PERMIT GEOCODING SERVICE")
    print("=" * 70)
    print()
    
    # Test API connection first
    if USE_GEOCLIENT:
        print("🔧 Testing NYC Geoclient API connection...")
        try:
            test_url = "https://api.nyc.gov/geoclient/v2/address"
            test_headers = {
                'Ocp-Apim-Subscription-Key': NYC_APP_ID
            }
            test_params = {
                'houseNumber': '1',
                'street': 'Centre Street',
                'borough': 'Manhattan'
            }
            test_response = requests.get(test_url, params=test_params, headers=test_headers, timeout=5)
            if test_response.status_code == 200:
                print("✅ NYC Geoclient API is working!\n")
            else:
                print(f"⚠️  NYC Geoclient returned status {test_response.status_code}\n")
        except Exception as e:
            print(f"❌ NYC Geoclient API test failed: {str(e)[:100]}")
            print("Falling back to OpenStreetMap only\n")
    
    # Connect to database
    print("🔌 Connecting to database...")
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        print(f"✅ Connected to database: {DB_HOST}:{DB_PORT}/{DB_NAME}\n")
    except Exception as e:
        print(f"❌ Database connection failed: {str(e)}")
        sys.exit(1)
    
    # Get statistics
    print("📊 Fetching statistics...")
    cur.execute("SELECT COUNT(*) as total FROM permits")
    total_permits = cur.fetchone()['total']
    
    cur.execute("SELECT COUNT(*) as with_coords FROM permits WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
    with_coords = cur.fetchone()['with_coords']
    
    without_coords = total_permits - with_coords
    print()
    print(f"📊 Database Statistics:")
    print(f"   Total permits: {total_permits:,}")
    print(f"   With coordinates: {with_coords:,} ({with_coords/total_permits*100:.1f}%)")
    print(f"   Without coordinates: {without_coords:,} ({without_coords/total_permits*100:.1f}%)")
    print()
    
    if without_coords == 0:
        print("✅ All permits already have coordinates!")
        cur.close()
        conn.close()
        return
    
    # Fetch permits without coordinates (limited by batch size)
    # Prioritize permits with BBL (can use NYC Geoclient V2)
    # Skip permits that have already failed geocoding
    print(f"🔍 Fetching {min(BATCH_SIZE, without_coords)} permits to geocode...\n")
    
    cur.execute("""
        SELECT id, address, bbl
        FROM permits 
        WHERE (latitude IS NULL OR longitude IS NULL)
            AND address IS NOT NULL 
            AND address != ''
            AND (geocode_failed IS NULL OR geocode_failed = FALSE)
        ORDER BY 
            CASE WHEN bbl IS NOT NULL AND bbl != '' THEN 0 ELSE 1 END,
            id
        LIMIT %s
    """, (BATCH_SIZE,))
    
    permits_to_geocode = cur.fetchall()
    
    if not permits_to_geocode:
        print("✅ No permits need geocoding!")
        cur.close()
        conn.close()
        return
    
    print(f"Processing {len(permits_to_geocode)} permits...\n")
    
    # Geocode each permit
    success_count = 0
    fail_count = 0
    start_time = time.time()
    
    for i, permit in enumerate(permits_to_geocode, 1):
        permit_id = permit['id']
        address = permit['address']
        bbl = permit.get('bbl')
        
        # Extract borough from BBL if available
        borough = None
        if bbl and len(str(bbl)) >= 1:
            borough_map = {
                '1': 'Manhattan',
                '2': 'Bronx', 
                '3': 'Brooklyn',
                '4': 'Queens',
                '5': 'Staten Island'
            }
            borough = borough_map.get(str(bbl)[0])
        
        # Progress indicator every 50 permits
        if i % 50 == 0:
            elapsed = time.time() - start_time
            rate = i / elapsed if elapsed > 0 else 0
            remaining = len(permits_to_geocode) - i
            eta = remaining / rate if rate > 0 else 0
            print(f"\n⏱️  Progress: {i}/{len(permits_to_geocode)} | {rate:.1f} permits/sec | ETA: {eta/60:.1f} min\n")
        
        print(f"[{i}/{len(permits_to_geocode)}] Permit #{permit_id}: {address}")
        if borough:
            print(f"  🏙️  Borough: {borough} (from BBL)")
        
        # Try NYC Geoclient first (if configured and we have BBL)
        lat, lon = None, None
        
        if USE_GEOCLIENT and borough:
            print(f"  📍 NYC Geoclient V2: {address}")
            lat, lon = geocode_with_nyc_geoclient(address, borough)
        
        # Fallback to Nominatim if NYC Geoclient didn't work or no BBL
        if lat is None or lon is None:
            if borough:
                print(f"  🌐 Trying OpenStreetMap Nominatim...")
            else:
                print(f"  ⚠️  No BBL/borough - using OpenStreetMap Nominatim...")
            lat, lon = geocode_with_nominatim(address)
        
        # Update database if we got coordinates
        if lat is not None and lon is not None:
            if update_permit_coordinates(conn, permit_id, lat, lon):
                print(f"  ✅ Success: {lat:.6f}, {lon:.6f}")
                success_count += 1
            else:
                print(f"  ❌ Failed to update database")
                fail_count += 1
        else:
            print(f"  ❌ Could not geocode address")
            # Mark as failed so we don't retry it
            try:
                cur_fail = conn.cursor()
                cur_fail.execute("UPDATE permits SET geocode_failed = TRUE WHERE id = %s", (permit_id,))
                conn.commit()
                cur_fail.close()
            except Exception as e:
                print(f"  ⚠️  Could not mark as failed: {e}")
            fail_count += 1
        
        # Rate limiting
        if i < len(permits_to_geocode):
            time.sleep(RATE_LIMIT_DELAY)
        
        print()
    
    # Summary
    cur.close()
    conn.close()
    
    print("=" * 70)
    print("📊 GEOCODING SUMMARY")
    print("=" * 70)
    print(f"✅ Successfully geocoded: {success_count}")
    print(f"❌ Failed to geocode: {fail_count}")
    print(f"📈 Success rate: {success_count/(success_count+fail_count)*100:.1f}%")
    print()
    
    # Calculate new statistics
    remaining = without_coords - success_count
    if remaining > 0:
        print(f"📝 Remaining permits without coordinates: {remaining:,}")
        print(f"   Run this script again to continue geocoding")
    else:
        print("🎉 All permits now have coordinates!")
    
    print()
    print("=" * 70)


if __name__ == '__main__':
    try:
        geocode_permits()
    except KeyboardInterrupt:
        print("\n\n⚠️  Geocoding interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
