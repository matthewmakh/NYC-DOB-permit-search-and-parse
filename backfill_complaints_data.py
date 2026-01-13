#!/usr/bin/env python3
"""
Backfill HPD Complaints data for existing buildings
Uses the public Housing Maintenance Code Complaints API (ygpa-z7cr)
Multi-threaded for faster processing
"""

import psycopg2
import psycopg2.extras
import requests
import time
import os
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

load_dotenv('dashboard_html/.env')  # Load from dashboard_html folder

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME')
    
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        # Fallback to Railway connection
        DATABASE_URL = "postgresql://postgres:rYOeFwAQciYdTdUVPxuCqNparvRNbUov@maglev.proxy.rlwy.net:26571/railway"
    else:
        DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

HPD_COMPLAINTS_API = "https://data.cityofnewyork.us/resource/ygpa-z7cr.json"
API_DELAY = 0.05  # Reduced delay since we're threading
NUM_THREADS = 5

# Thread-safe counters
stats_lock = Lock()
stats = {
    'updated': 0,
    'failed': 0,
    'processed': 0
}

def get_complaints_for_bbl(bbl):
    """Fetch complaints data from NYC Open Data"""
    try:
        response = requests.get(
            HPD_COMPLAINTS_API,
            params={
                'bbl': bbl,
                '$select': 'complaint_status,problem_status',
                '$limit': 5000
            },
            timeout=10
        )
        response.raise_for_status()
        time.sleep(API_DELAY)
        
        complaints = response.json()
        total_complaints = len(complaints)
        open_complaints = sum(
            1 for c in complaints 
            if c.get('complaint_status') == 'OPEN' or c.get('problem_status') == 'OPEN'
        )
        
        return total_complaints, open_complaints
    except Exception as e:
        return None, None

def process_building(building_data):
    """Process a single building - designed to be run in a thread"""
    bbl = building_data['bbl']
    address = building_data['address'] or 'Unknown'
    
    # Get complaints data
    total_complaints, open_complaints = get_complaints_for_bbl(bbl)
    
    if total_complaints is not None:
        # Update database
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        try:
            cur.execute("""
                UPDATE buildings
                SET hpd_total_complaints = %s,
                    hpd_open_complaints = %s
                WHERE bbl = %s
            """, (total_complaints, open_complaints, bbl))
            
            conn.commit()
            
            # Update stats
            with stats_lock:
                stats['updated'] += 1
                stats['processed'] += 1
            
            if open_complaints > 0:
                result = f"✅ {open_complaints} open / {total_complaints} total"
            else:
                result = f"✓ {total_complaints} total (none open)"
            
            return (bbl, address, True, result)
            
        except Exception as e:
            conn.rollback()
            with stats_lock:
                stats['failed'] += 1
                stats['processed'] += 1
            return (bbl, address, False, f"DB Error: {str(e)}")
        finally:
            cur.close()
            conn.close()
    else:
        with stats_lock:
            stats['failed'] += 1
            stats['processed'] += 1
        return (bbl, address, False, "API fetch failed")

def backfill_complaints():
    """Update buildings table with complaints data using multithreading"""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    try:
        # Get buildings that need complaints data
        cur.execute("""
            SELECT bbl, address, hpd_registration_id
            FROM buildings
            WHERE hpd_registration_id IS NOT NULL
            AND (hpd_total_complaints IS NULL OR hpd_total_complaints = 0)
            ORDER BY hpd_total_violations DESC NULLS LAST
            LIMIT 1000
        """)
        
        buildings = cur.fetchall()
        total = len(buildings)
        
        print(f"\n{'='*70}")
        print(f"BACKFILLING HPD COMPLAINTS DATA (Multi-threaded)")
        print(f"{'='*70}")
        print(f"Found {total} buildings to update")
        print(f"Using {NUM_THREADS} threads")
        print(f"API: {HPD_COMPLAINTS_API}")
        print(f"{'='*70}\n")
        
        # Process buildings in parallel
        with ThreadPoolExecutor(max_workers=NUM_THREADS) as executor:
            # Submit all tasks
            futures = {executor.submit(process_building, building): building 
                      for building in buildings}
            
            # Process results as they complete
            for i, future in enumerate(as_completed(futures), 1):
                building = futures[future]
                try:
                    bbl, address, success, message = future.result()
                    
                    # Print result
                    status_icon = "✅" if success else "❌"
                    print(f"[{stats['processed']}/{total}] {status_icon} {bbl} - {address[:40]}")
                    print(f"    {message}")
                    
                    # Progress update every 50 buildings
                    if stats['processed'] % 50 == 0:
                        with stats_lock:
                            updated = stats['updated']
                            failed = stats['failed']
                            processed = stats['processed']
                        print(f"\n--- Progress: {processed}/{total} ({updated} updated, {failed} failed) ---\n")
                
                except Exception as e:
                    print(f"[{i}/{total}] ❌ Exception: {str(e)}")
                    with stats_lock:
                        stats['failed'] += 1
                        stats['processed'] += 1
        
        print(f"\n{'='*70}")
        print(f"BACKFILL COMPLETE")
        print(f"{'='*70}")
        print(f"Total processed: {stats['processed']}")
        print(f"Successfully updated: {stats['updated']}")
        print(f"Failed: {stats['failed']}")
        print(f"{'='*70}\n")
        
        # Show summary statistics
        cur.execute("""
            SELECT 
                COUNT(*) as total_buildings,
                COUNT(CASE WHEN hpd_open_complaints > 0 THEN 1 END) as with_open_complaints,
                COUNT(CASE WHEN hpd_total_complaints > 0 THEN 1 END) as with_total_complaints,
                SUM(hpd_open_complaints) as total_open_complaints,
                SUM(hpd_total_complaints) as total_all_complaints
            FROM buildings
            WHERE hpd_registration_id IS NOT NULL
        """)
        
        db_stats = cur.fetchone()
        print("Database Statistics:")
        print(f"  Total HPD-registered buildings: {db_stats['total_buildings']}")
        print(f"  With open complaints: {db_stats['with_open_complaints']}")
        print(f"  With any complaints: {db_stats['with_total_complaints']}")
        print(f"  Total open complaints citywide: {db_stats['total_open_complaints']}")
        print(f"  Total all complaints citywide: {db_stats['total_all_complaints']}")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    backfill_complaints()
