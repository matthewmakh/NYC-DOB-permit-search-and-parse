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

# Configuration
BATCH_SIZE = int(os.getenv('GEOCODE_BATCH_SIZE', '100'))  # Process 100 permits per run
RATE_LIMIT_DELAY = float(os.getenv('GEOCODE_DELAY', '0.5'))  # 0.5 seconds between requests

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
    
    # Clean up address
    address = address.strip().upper()
    
    # Try to extract house number and street
    match = re.match(r'^(\d+[\w-]*)\s+(.+)$', address)
    if not match:
        return None, None, None
    
    house_number = match.group(1)
    street_name = match.group(2)
    
    # Extract borough if present (usually at the end)
    # Common patterns: "STREET, BOROUGH" or "STREET BOROUGH"
    borough = None
    for boro in ['MANHATTAN', 'BROOKLYN', 'QUEENS', 'BRONX', 'STATEN ISLAND']:
        if boro in street_name:
            borough = boro
            street_name = street_name.replace(f', {boro}', '').replace(boro, '').strip()
            break
    
    return house_number, street_name, borough


def geocode_with_nyc_geoclient(house_number, street_name, borough=None):
    """
    Geocode using NYC Geoclient API
    Returns: (latitude, longitude) or (None, None)
    """
    if not USE_GEOCLIENT:
        return None, None
    
    try:
        # NYC Geoclient Address endpoint
        url = "https://api.cityofnewyork.us/geoclient/v1/address.json"
        
        params = {
            'houseNumber': house_number,
            'street': street_name,
            'app_id': NYC_APP_ID,
            'app_key': NYC_APP_KEY
        }
        
        # Add borough if available
        if borough:
            # Convert borough name to code
            borough_codes = {
                'MANHATTAN': 'Manhattan',
                'BROOKLYN': 'Brooklyn', 
                'QUEENS': 'Queens',
                'BRONX': 'Bronx',
                'STATEN ISLAND': 'Staten Island'
            }
            params['borough'] = borough_codes.get(borough, borough)
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if 'address' in data:
                addr_data = data['address']
                lat = addr_data.get('latitude')
                lon = addr_data.get('longitude')
                
                if lat and lon:
                    return float(lat), float(lon)
        
        return None, None
        
    except Exception as e:
        print(f"  ⚠️  Geocoding error: {str(e)}")
        return None, None


def geocode_with_nominatim(address):
    """
    Fallback geocoding using OpenStreetMap Nominatim (free, no API key)
    Returns: (latitude, longitude) or (None, None)
    """
    try:
        # Add "New York, NY" to improve results
        full_address = f"{address}, New York, NY, USA"
        
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            'q': full_address,
            'format': 'json',
            'limit': 1
        }
        headers = {
            'User-Agent': 'DOB-Permit-Geocoder/1.0'  # Required by Nominatim
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code == 200:
            results = response.json()
            if results and len(results) > 0:
                result = results[0]
                lat = float(result['lat'])
                lon = float(result['lon'])
                return lat, lon
        
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
    
    # Connect to database
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        print(f"✅ Connected to database: {DB_HOST}:{DB_PORT}/{DB_NAME}\n")
    except Exception as e:
        print(f"❌ Database connection failed: {str(e)}")
        sys.exit(1)
    
    # Get statistics
    cur.execute("SELECT COUNT(*) as total FROM permits")
    total_permits = cur.fetchone()['total']
    
    cur.execute("SELECT COUNT(*) as with_coords FROM permits WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
    with_coords = cur.fetchone()['with_coords']
    
    without_coords = total_permits - with_coords
    
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
    print(f"🔍 Fetching {min(BATCH_SIZE, without_coords)} permits to geocode...\n")
    
    cur.execute("""
        SELECT id, address, bin
        FROM permits 
        WHERE (latitude IS NULL OR longitude IS NULL)
            AND address IS NOT NULL 
            AND address != ''
        ORDER BY id
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
    
    for i, permit in enumerate(permits_to_geocode, 1):
        permit_id = permit['id']
        address = permit['address']
        
        print(f"[{i}/{len(permits_to_geocode)}] Permit #{permit_id}: {address}")
        
        # Try NYC Geoclient first (if configured)
        lat, lon = None, None
        
        if USE_GEOCLIENT:
            house_number, street_name, borough = parse_nyc_address(address)
            
            if house_number and street_name:
                print(f"  📍 Using NYC Geoclient: {house_number} {street_name}")
                lat, lon = geocode_with_nyc_geoclient(house_number, street_name, borough)
        
        # Fallback to Nominatim if NYC Geoclient didn't work
        if lat is None or lon is None:
            print(f"  🌐 Trying OpenStreetMap Nominatim...")
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
