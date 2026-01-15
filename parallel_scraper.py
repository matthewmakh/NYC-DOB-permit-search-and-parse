#!/usr/bin/env python3
"""
Parallel permit scraper - fetches from ALL 3 DOB sources simultaneously
Sources: BIS (legacy), DOB NOW Filings, DOB NOW Approved
Significantly faster than sequential scraping
"""

import os
import sys
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple
from dotenv import load_dotenv
import threading

# Load .env from dashboard_html if root .env doesn't exist
if os.path.exists('.env'):
    load_dotenv('.env')
elif os.path.exists('dashboard_html/.env'):
    load_dotenv('dashboard_html/.env')

from permit_scraper_api import (
    NYCOpenDataClient,
    DOBNowFilingsClient,
    DOBNowApprovedClient,
    PermitDatabase,
    DB_CONFIG
)

# Thread-safe print
print_lock = threading.Lock()

def safe_print(msg):
    with print_lock:
        print(msg, flush=True)


def scrape_bis_permits(start_date: str, end_date: str) -> Tuple[str, int, int]:
    """Scrape BIS Permit Issuance (legacy system)"""
    safe_print(f"\n📋 [BIS] Starting: {start_date} to {end_date}")
    
    db = PermitDatabase(DB_CONFIG)
    db.connect()
    client = NYCOpenDataClient(app_token=None)
    
    total_fetched = 0
    total_inserted = 0
    offset = 0
    batch_size = 5000
    
    while True:
        try:
            permits = client.fetch_permits(
                start_date=start_date,
                end_date=end_date,
                limit=batch_size,
                offset=offset
            )
            
            if not permits:
                break
            
            total_fetched += len(permits)
            safe_print(f"   [BIS] Fetched {total_fetched:,} records, inserting...")
            
            batch_inserted = 0
            for i, p in enumerate(permits):
                if db.insert_permit(p):
                    total_inserted += 1
                    batch_inserted += 1
                if (i + 1) % 500 == 0:
                    safe_print(f"   [BIS] Inserted {i+1}/{len(permits)} ({batch_inserted} new)...")
            db.conn.commit()
            safe_print(f"   [BIS] Batch complete: {batch_inserted} new records")
            
            if len(permits) < batch_size:
                break
            offset += batch_size
            
        except Exception as e:
            safe_print(f"   [BIS] ⚠️ Error: {e}")
            break
    
    db.close()
    safe_print(f"   [BIS] ✅ Done: {total_fetched:,} fetched, {total_inserted:,} inserted")
    return ('bis', total_fetched, total_inserted)


def scrape_dob_now_filings(start_date: str, end_date: str) -> Tuple[str, int, int]:
    """Scrape DOB NOW Job Filings (new applications)"""
    safe_print(f"\n⭐ [DOB NOW Filings] Starting: {start_date} to {end_date}")
    
    db = PermitDatabase(DB_CONFIG)
    db.connect()
    client = DOBNowFilingsClient(app_token=None)
    
    total_fetched = 0
    total_inserted = 0
    offset = 0
    batch_size = 5000
    
    while True:
        try:
            filings = client.fetch_filings(
                start_date=start_date,
                end_date=end_date,
                limit=batch_size,
                offset=offset
            )
            
            if not filings:
                break
            
            total_fetched += len(filings)
            safe_print(f"   [Filings] Fetched {total_fetched:,} records, inserting...")
            
            batch_inserted = 0
            for i, f in enumerate(filings):
                if db.insert_dob_now_filing(f):
                    total_inserted += 1
                    batch_inserted += 1
                if (i + 1) % 500 == 0:
                    safe_print(f"   [Filings] Inserted {i+1}/{len(filings)} ({batch_inserted} new)...")
            db.conn.commit()
            safe_print(f"   [Filings] Batch complete: {batch_inserted} new records")
            
            if len(filings) < batch_size:
                break
            offset += batch_size
            
        except Exception as e:
            safe_print(f"   [Filings] ⚠️ Error: {e}")
            break
    
    db.close()
    safe_print(f"   [Filings] ✅ Done: {total_fetched:,} fetched, {total_inserted:,} inserted")
    return ('filings', total_fetched, total_inserted)


def scrape_dob_now_approved(start_date: str, end_date: str) -> Tuple[str, int, int]:
    """Scrape DOB NOW Approved Permits (issued permits)"""
    safe_print(f"\n✅ [DOB NOW Approved] Starting: {start_date} to {end_date}")
    
    db = PermitDatabase(DB_CONFIG)
    db.connect()
    client = DOBNowApprovedClient(app_token=None)
    
    total_fetched = 0
    total_inserted = 0
    offset = 0
    batch_size = 5000
    
    while True:
        try:
            permits = client.fetch_permits(
                start_date=start_date,
                end_date=end_date,
                limit=batch_size,
                offset=offset
            )
            
            if not permits:
                break
            
            total_fetched += len(permits)
            safe_print(f"   [Approved] Fetched {total_fetched:,} records, inserting...")
            
            batch_inserted = 0
            for i, p in enumerate(permits):
                if db.insert_dob_now_approved(p):
                    total_inserted += 1
                    batch_inserted += 1
                if (i + 1) % 500 == 0:
                    safe_print(f"   [Approved] Inserted {i+1}/{len(permits)} ({batch_inserted} new)...")
            db.conn.commit()
            safe_print(f"   [Approved] Batch complete: {batch_inserted} new records")
            
            if len(permits) < batch_size:
                break
            offset += batch_size
            
        except Exception as e:
            safe_print(f"   [Approved] ⚠️ Error: {e}")
            break
    
    db.close()
    safe_print(f"   [Approved] ✅ Done: {total_fetched:,} fetched, {total_inserted:,} inserted")
    return ('approved', total_fetched, total_inserted)


def run_parallel_scraper(start_date: str, end_date: str):
    """
    Run the scraper with all 3 sources in parallel
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
    """
    print("=" * 80)
    print("🚀 NYC DOB Parallel Permit Scraper - 3 SOURCE VERSION")
    print("=" * 80)
    print(f"📅 Date range: {start_date} to {end_date}")
    print(f"📊 Sources: BIS + DOB NOW Filings + DOB NOW Approved")
    print("=" * 80)
    
    start_time = datetime.now()
    
    # Track totals
    totals = {
        'bis_fetched': 0, 'bis_inserted': 0,
        'filings_fetched': 0, 'filings_inserted': 0,
        'approved_fetched': 0, 'approved_inserted': 0
    }
    
    # Run all 3 sources in parallel
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(scrape_bis_permits, start_date, end_date): 'bis',
            executor.submit(scrape_dob_now_filings, start_date, end_date): 'filings',
            executor.submit(scrape_dob_now_approved, start_date, end_date): 'approved'
        }
        
        for future in as_completed(futures):
            try:
                source, fetched, inserted = future.result()
                totals[f'{source}_fetched'] = fetched
                totals[f'{source}_inserted'] = inserted
            except Exception as e:
                print(f"⚠️ Source failed: {e}")
    
    elapsed = (datetime.now() - start_time).total_seconds()
    
    # Summary
    print("\n" + "=" * 80)
    print("📊 SCRAPER SUMMARY")
    print("=" * 80)
    print(f"\n⏱️  Completed in {elapsed:.1f} seconds")
    
    print(f"\n🏛️  BIS Permits (Legacy):")
    print(f"    Fetched: {totals['bis_fetched']:,}")
    print(f"    Inserted: {totals['bis_inserted']:,}")
    
    print(f"\n⭐ DOB NOW Filings:")
    print(f"    Fetched: {totals['filings_fetched']:,}")
    print(f"    Inserted: {totals['filings_inserted']:,}")
    
    print(f"\n✅ DOB NOW Approved:")
    print(f"    Fetched: {totals['approved_fetched']:,}")
    print(f"    Inserted: {totals['approved_inserted']:,}")
    
    total_fetched = totals['bis_fetched'] + totals['filings_fetched'] + totals['approved_fetched']
    total_inserted = totals['bis_inserted'] + totals['filings_inserted'] + totals['approved_inserted']
    
    print(f"\n📈 GRAND TOTAL:")
    print(f"    Total Fetched: {total_fetched:,}")
    print(f"    Total Inserted: {total_inserted:,}")
    print(f"    Duplicates Skipped: {total_fetched - total_inserted:,}")
    print("=" * 80)
    
    return total_inserted


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Parallel DOB Permit Scraper')
    parser.add_argument('--days', '-d', type=int, default=7,
                        help='Number of days to look back (default: 7)')
    parser.add_argument('--start', type=str, help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, help='End date (YYYY-MM-DD)')
    
    args = parser.parse_args()
    
    # If explicit dates provided, use them
    if args.start and args.end:
        start_date = args.start
        end_date = args.end
        print(f"📅 Using specified dates: {start_date} to {end_date}")
    else:
        # Calculate from --days flag
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=args.days)).strftime('%Y-%m-%d')
        print(f"📅 Scraping last {args.days} days: {start_date} to {end_date}")
    
    run_parallel_scraper(start_date, end_date)
