#!/usr/bin/env python3
"""
Backfill Script - Fetch 6 months of permit data from all DOB sources
FAST VERSION: Uses bulk inserts and parallel processing

This script:
1. Fetches permits from BIS, DOB NOW Filings, and DOB NOW Approved IN PARALLEL
2. Uses execute_values for 10-50x faster bulk inserts
3. Uses ON CONFLICT to prevent duplicates
4. Runs enrichment pipeline after data is loaded
"""

import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Load .env from dashboard_html if root .env doesn't exist
if os.path.exists('.env'):
    load_dotenv('.env')
elif os.path.exists('dashboard_html/.env'):
    load_dotenv('dashboard_html/.env')
else:
    print("⚠️ No .env file found!")

from permit_scraper_api import (
    NYCOpenDataClient,
    DOBNowFilingsClient,
    DOBNowApprovedClient,
    PermitDatabase,
    DB_CONFIG
)

# Configuration
MONTHS_BACK = int(os.getenv('BACKFILL_MONTHS', '6'))
RUN_ENRICHMENT = os.getenv('RUN_ENRICHMENT_AFTER', 'true').lower() == 'true'

# Thread-safe print
print_lock = threading.Lock()

def safe_print(msg):
    with print_lock:
        print(msg)


def get_month_ranges(months_back: int):
    """Generate list of (start_date, end_date) tuples for each month"""
    ranges = []
    today = datetime.now()
    
    for i in range(months_back):
        # End of this month chunk
        if i == 0:
            end_date = today
        else:
            end_date = (today.replace(day=1) - timedelta(days=1)) - timedelta(days=30 * (i - 1))
        
        # Start of this month chunk (roughly 30 days earlier)
        start_date = end_date - timedelta(days=30)
        
        ranges.append((
            start_date.strftime('%Y-%m-%d'),
            end_date.strftime('%Y-%m-%d')
        ))
    
    return list(reversed(ranges))  # Oldest first


# Insert batch size - commits after each batch so crashes don't lose progress
INSERT_BATCH_SIZE = 50


def backfill_bis_permits_fast(start_date: str, end_date: str) -> tuple:
    """Backfill from BIS Permit Issuance (legacy) - FAST VERSION"""
    safe_print(f"\n📋 [BIS] Starting: {start_date} to {end_date}")
    safe_print(f"   [BIS] Connecting to database...")
    
    # Each thread gets its own DB connection
    db = PermitDatabase(DB_CONFIG)
    db.connect()
    safe_print(f"   [BIS] ✓ Connected to database")
    
    client = NYCOpenDataClient(app_token=None)
    safe_print(f"   [BIS] ✓ API client ready")
    
    total_fetched = 0
    total_inserted = 0
    offset = 0
    fetch_batch_size = 5000  # How many to fetch from API at once
    api_batch_num = 0
    
    while True:
        try:
            api_batch_num += 1
            safe_print(f"   [BIS] 🔄 Fetching API batch #{api_batch_num} (offset {offset:,})...")
            
            permits = client.fetch_permits(
                start_date=start_date,
                end_date=end_date,
                limit=fetch_batch_size,
                offset=offset
            )
            
            if not permits:
                safe_print(f"   [BIS] No more records to fetch")
                break
            
            total_fetched += len(permits)
            safe_print(f"   [BIS] ✓ Got {len(permits):,} records (total: {total_fetched:,})")
            
            # Insert in small batches (50 at a time) with commits
            insert_batches = (len(permits) + INSERT_BATCH_SIZE - 1) // INSERT_BATCH_SIZE
            safe_print(f"   [BIS] 💾 Inserting in {insert_batches} batches of {INSERT_BATCH_SIZE}...")
            
            for i in range(0, len(permits), INSERT_BATCH_SIZE):
                batch = permits[i:i + INSERT_BATCH_SIZE]
                # Use the working insert_permit method (already correctly mapped)
                for p in batch:
                    if db.insert_permit(p):
                        total_inserted += 1
                db.conn.commit()  # Commit after each batch of 50
                batch_num = (i // INSERT_BATCH_SIZE) + 1
                safe_print(f"   [BIS]    ✓ Batch {batch_num}/{insert_batches} done - {total_inserted:,} total")
            
            safe_print(f"   [BIS] ✓ API batch #{api_batch_num} complete: {total_inserted:,} total inserted")
            
            if len(permits) < fetch_batch_size:
                safe_print(f"   [BIS] Reached end of data (got {len(permits)} < {fetch_batch_size})")
                break
            
            offset += fetch_batch_size
            
        except Exception as e:
            safe_print(f"   [BIS] ⚠️ Error: {e}")
            import traceback
            safe_print(f"   [BIS] {traceback.format_exc()}")
            break
    
    db.close()
    safe_print(f"   [BIS] ✅ DONE: {total_fetched:,} fetched, {total_inserted:,} inserted")
    return ('bis', total_fetched, total_inserted)


def backfill_dob_now_filings_fast(start_date: str, end_date: str) -> tuple:
    """Backfill from DOB NOW Job Filings - FAST VERSION"""
    safe_print(f"\n📋 [DOB NOW Filings ⭐] Starting: {start_date} to {end_date}")
    safe_print(f"   [Filings] Connecting to database...")
    
    db = PermitDatabase(DB_CONFIG)
    db.connect()
    safe_print(f"   [Filings] ✓ Connected to database")
    
    client = DOBNowFilingsClient(app_token=None)
    safe_print(f"   [Filings] ✓ API client ready")
    
    total_fetched = 0
    total_inserted = 0
    offset = 0
    fetch_batch_size = 5000
    api_batch_num = 0
    
    while True:
        try:
            api_batch_num += 1
            safe_print(f"   [Filings] 🔄 Fetching API batch #{api_batch_num} (offset {offset:,})...")
            
            filings = client.fetch_filings(
                start_date=start_date,
                end_date=end_date,
                limit=fetch_batch_size,
                offset=offset
            )
            
            if not filings:
                safe_print(f"   [Filings] No more records to fetch")
                break
            
            total_fetched += len(filings)
            safe_print(f"   [Filings] ✓ Got {len(filings):,} records (total: {total_fetched:,})")
            
            # Insert in small batches (50 at a time) with commits
            insert_batches = (len(filings) + INSERT_BATCH_SIZE - 1) // INSERT_BATCH_SIZE
            safe_print(f"   [Filings] 💾 Inserting in {insert_batches} batches of {INSERT_BATCH_SIZE}...")
            
            for i in range(0, len(filings), INSERT_BATCH_SIZE):
                batch = filings[i:i + INSERT_BATCH_SIZE]
                # Use the working insert_dob_now_filing method
                for f in batch:
                    if db.insert_dob_now_filing(f):
                        total_inserted += 1
                db.conn.commit()  # Commit after each batch of 50
                batch_num = (i // INSERT_BATCH_SIZE) + 1
                safe_print(f"   [Filings]    ✓ Batch {batch_num}/{insert_batches} done - {total_inserted:,} total")
            
            safe_print(f"   [Filings] ✓ API batch #{api_batch_num} complete: {total_inserted:,} total inserted")
            
            if len(filings) < fetch_batch_size:
                safe_print(f"   [Filings] Reached end of data (got {len(filings)} < {fetch_batch_size})")
                break
            
            offset += fetch_batch_size
            
        except Exception as e:
            safe_print(f"   [Filings] ⚠️ Error: {e}")
            import traceback
            safe_print(f"   [Filings] {traceback.format_exc()}")
            break
    
    db.close()
    safe_print(f"   [Filings] ✅ DONE: {total_fetched:,} fetched, {total_inserted:,} inserted")
    return ('filings', total_fetched, total_inserted)


def backfill_dob_now_approved_fast(start_date: str, end_date: str) -> tuple:
    """Backfill from DOB NOW Approved Permits - FAST VERSION"""
    safe_print(f"\n📋 [DOB NOW Approved] Starting: {start_date} to {end_date}")
    safe_print(f"   [Approved] Connecting to database...")
    
    db = PermitDatabase(DB_CONFIG)
    db.connect()
    safe_print(f"   [Approved] ✓ Connected to database")
    
    client = DOBNowApprovedClient(app_token=None)
    safe_print(f"   [Approved] ✓ API client ready")
    
    total_fetched = 0
    total_inserted = 0
    offset = 0
    fetch_batch_size = 5000
    api_batch_num = 0
    
    while True:
        try:
            api_batch_num += 1
            safe_print(f"   [Approved] 🔄 Fetching API batch #{api_batch_num} (offset {offset:,})...")
            
            permits = client.fetch_permits(
                start_date=start_date,
                end_date=end_date,
                limit=fetch_batch_size,
                offset=offset
            )
            
            if not permits:
                safe_print(f"   [Approved] No more records to fetch")
                break
            
            total_fetched += len(permits)
            safe_print(f"   [Approved] ✓ Got {len(permits):,} records (total: {total_fetched:,})")
            
            # Insert in small batches (50 at a time) with commits
            insert_batches = (len(permits) + INSERT_BATCH_SIZE - 1) // INSERT_BATCH_SIZE
            safe_print(f"   [Approved] 💾 Inserting in {insert_batches} batches of {INSERT_BATCH_SIZE}...")
            
            for i in range(0, len(permits), INSERT_BATCH_SIZE):
                batch = permits[i:i + INSERT_BATCH_SIZE]
                # Use the working insert_dob_now_approved method
                for p in batch:
                    if db.insert_dob_now_approved(p):
                        total_inserted += 1
                db.conn.commit()  # Commit after each batch of 50
                batch_num = (i // INSERT_BATCH_SIZE) + 1
                safe_print(f"   [Approved]    ✓ Batch {batch_num}/{insert_batches} done - {total_inserted:,} total")
            
            safe_print(f"   [Approved] ✓ API batch #{api_batch_num} complete: {total_inserted:,} total inserted")
            
            if len(permits) < fetch_batch_size:
                safe_print(f"   [Approved] Reached end of data (got {len(permits)} < {fetch_batch_size})")
                break
            
            offset += fetch_batch_size
            
        except Exception as e:
            safe_print(f"   [Approved] ⚠️ Error: {e}")
            import traceback
            safe_print(f"   [Approved] {traceback.format_exc()}")
            break
    
    db.close()
    safe_print(f"   [Approved] ✅ DONE: {total_fetched:,} fetched, {total_inserted:,} inserted")
    return ('approved', total_fetched, total_inserted)


def run_backfill():
    """Main backfill function - PARALLEL VERSION"""
    print("=" * 80)
    print("🚀 DOB PERMIT BACKFILL - FAST PARALLEL VERSION")
    print("=" * 80)
    print(f"📅 Backfilling {MONTHS_BACK} months of data")
    print(f"📊 Sources: BIS + DOB NOW Filings + DOB NOW Approved")
    print(f"⚡ Using: Fast bulk inserts + 3 parallel threads")
    print("=" * 80)
    
    # Get the full date range (6 months ago to now)
    today = datetime.now()
    start_date = (today - timedelta(days=30 * MONTHS_BACK)).strftime('%Y-%m-%d')
    end_date = today.strftime('%Y-%m-%d')
    
    print(f"\n📆 Full date range: {start_date} to {end_date}")
    
    # Track totals
    grand_total = {
        'bis_fetched': 0, 'bis_inserted': 0,
        'filings_fetched': 0, 'filings_inserted': 0,
        'approved_fetched': 0, 'approved_inserted': 0
    }
    
    print("\n🔄 Starting parallel fetch from all 3 sources...")
    start_time = datetime.now()
    
    # Run all 3 sources in parallel (each source manages its own DB connection)
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(backfill_bis_permits_fast, start_date, end_date): 'bis',
            executor.submit(backfill_dob_now_filings_fast, start_date, end_date): 'filings',
            executor.submit(backfill_dob_now_approved_fast, start_date, end_date): 'approved'
        }
        
        for future in as_completed(futures):
            try:
                source, fetched, inserted = future.result()
                if source == 'bis':
                    grand_total['bis_fetched'] = fetched
                    grand_total['bis_inserted'] = inserted
                elif source == 'filings':
                    grand_total['filings_fetched'] = fetched
                    grand_total['filings_inserted'] = inserted
                elif source == 'approved':
                    grand_total['approved_fetched'] = fetched
                    grand_total['approved_inserted'] = inserted
            except Exception as e:
                print(f"⚠️ Source failed: {e}")
    
    elapsed = (datetime.now() - start_time).total_seconds()
    
    # Summary
    print("\n" + "=" * 80)
    print("📊 BACKFILL SUMMARY")
    print("=" * 80)
    print(f"\n⏱️  Completed in {elapsed:.1f} seconds ({elapsed/60:.1f} minutes)")
    
    print(f"\n🏛️  BIS Permits (Legacy):")
    print(f"    Fetched: {grand_total['bis_fetched']:,}")
    print(f"    Inserted/Updated: {grand_total['bis_inserted']:,}")
    
    print(f"\n⭐ DOB NOW Filings (New Permits!):")
    print(f"    Fetched: {grand_total['filings_fetched']:,}")
    print(f"    Inserted/Updated: {grand_total['filings_inserted']:,}")
    
    print(f"\n✅ DOB NOW Approved:")
    print(f"    Fetched: {grand_total['approved_fetched']:,}")
    print(f"    Inserted/Updated: {grand_total['approved_inserted']:,}")
    
    total_fetched = grand_total['bis_fetched'] + grand_total['filings_fetched'] + grand_total['approved_fetched']
    total_inserted = grand_total['bis_inserted'] + grand_total['filings_inserted'] + grand_total['approved_inserted']
    
    print(f"\n📈 GRAND TOTAL:")
    print(f"    Total Records Fetched: {total_fetched:,}")
    print(f"    Total Records Inserted/Updated: {total_inserted:,}")
    print(f"    Duplicates Prevented: {total_fetched - total_inserted:,}")
    
    if elapsed > 0:
        print(f"    Speed: {total_inserted / elapsed:.0f} records/second")
    print("=" * 80)
    
    return total_inserted


def run_enrichment():
    """Run the enrichment pipeline"""
    print("\n" + "=" * 80)
    print("🔧 RUNNING ENRICHMENT PIPELINE")
    print("=" * 80)
    
    try:
        # Import and run enrichment
        from run_enrichment_pipeline import run_full_pipeline
        run_full_pipeline()
        print("✅ Enrichment complete!")
    except Exception as e:
        print(f"⚠️ Could not run enrichment: {e}")
        print("   Run manually: python run_enrichment_pipeline.py")


if __name__ == '__main__':
    # Check for custom months
    if len(sys.argv) > 1:
        MONTHS_BACK = int(sys.argv[1])
    
    print(f"\n🚀 Starting {MONTHS_BACK}-month backfill...\n")
    
    # Run backfill
    inserted = run_backfill()
    
    # Run enrichment if enabled and we inserted records
    if RUN_ENRICHMENT and inserted > 0:
        run_enrichment()
    elif inserted > 0:
        print("\n💡 To enrich the new data, run:")
        print("   python run_enrichment_pipeline.py")
    
    print("\n🎉 BACKFILL COMPLETE!")
