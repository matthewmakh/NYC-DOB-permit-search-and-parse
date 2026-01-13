"""
Parallel permit scraper - fetches multiple date ranges simultaneously
Significantly faster than sequential scraping
"""

import os
import sys
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple
from dotenv import load_dotenv

load_dotenv('.env')

from permit_scraper_api import NYCOpenDataClient, PermitDatabase, DB_CONFIG


def scrape_date_range(start_date: str, end_date: str, worker_id: int) -> Tuple[int, int, str, str]:
    """
    Scrape a single date range (runs in parallel thread)
    
    Returns:
        (fetched_count, inserted_count, start_date, end_date)
    """
    try:
        # Each thread gets its own API client and database connection
        api_client = NYCOpenDataClient(app_token=None)
        db = PermitDatabase(DB_CONFIG)
        db.connect()
        
        print(f"[Worker {worker_id}] Fetching {start_date} to {end_date}...")
        
        # Fetch permits from API
        permits = api_client.fetch_all_permits(
            start_date=start_date,
            end_date=end_date
        )
        
        fetched = len(permits)
        print(f"[Worker {worker_id}] ✅ Fetched {fetched:,} permits")
        
        # Insert into database
        if permits:
            print(f"[Worker {worker_id}] 💾 Inserting...")
            inserted = db.bulk_insert_permits(permits)
            print(f"[Worker {worker_id}] ✅ Inserted {inserted:,} permits")
        else:
            inserted = 0
        
        db.close()
        return (fetched, inserted, start_date, end_date)
        
    except Exception as e:
        print(f"[Worker {worker_id}] ❌ Error: {e}")
        return (0, 0, start_date, end_date)


def split_date_range(start_date: str, end_date: str, num_chunks: int) -> List[Tuple[str, str]]:
    """
    Split a date range into smaller chunks for parallel processing
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        num_chunks: Number of chunks to split into
        
    Returns:
        List of (start_date, end_date) tuples
    """
    start = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')
    
    total_days = (end - start).days + 1
    days_per_chunk = max(1, total_days // num_chunks)
    
    chunks = []
    current = start
    
    while current <= end:
        chunk_end = min(current + timedelta(days=days_per_chunk - 1), end)
        chunks.append((
            current.strftime('%Y-%m-%d'),
            chunk_end.strftime('%Y-%m-%d')
        ))
        current = chunk_end + timedelta(days=1)
    
    return chunks


def run_parallel_scraper(start_date: str, end_date: str, num_workers: int = 4):
    """
    Run the scraper with parallel workers
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        num_workers: Number of parallel workers (default: 4)
    """
    print("=" * 80)
    print(f"NYC DOB Parallel Permit Scraper")
    print(f"Workers: {num_workers}")
    print("=" * 80)
    
    # Split date range into chunks
    chunks = split_date_range(start_date, end_date, num_workers)
    
    print(f"\n📅 Date range split into {len(chunks)} chunks:")
    for i, (chunk_start, chunk_end) in enumerate(chunks, 1):
        print(f"   Chunk {i}: {chunk_start} to {chunk_end}")
    
    print(f"\n🚀 Starting {num_workers} parallel workers...\n")
    
    # Run workers in parallel
    total_fetched = 0
    total_inserted = 0
    
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        # Submit all tasks
        futures = {
            executor.submit(scrape_date_range, chunk_start, chunk_end, i): i 
            for i, (chunk_start, chunk_end) in enumerate(chunks, 1)
        }
        
        # Process results as they complete
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                fetched, inserted, chunk_start, chunk_end = future.result()
                total_fetched += fetched
                total_inserted += inserted
            except Exception as e:
                print(f"[Worker {worker_id}] ❌ Failed: {e}")
    
    print("\n" + "=" * 80)
    print("✅ Parallel scraping complete!")
    print(f"   Total permits fetched: {total_fetched:,}")
    print(f"   Total permits inserted: {total_inserted:,}")
    print(f"   Duplicates skipped: {total_fetched - total_inserted:,}")
    print("=" * 80)


if __name__ == "__main__":
    # Default to last 7 days if no dates provided
    if len(sys.argv) < 2:
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        num_workers = 1
        print(f"📅 No dates specified, defaulting to last 7 days: {start_date} to {end_date}")
    elif len(sys.argv) < 3:
        print("Usage: python parallel_scraper.py [START_DATE END_DATE] [NUM_WORKERS]")
        print("Example: python parallel_scraper.py 2025-11-18 2025-11-21 8")
        print("Or run without arguments to scrape last 7 days:")
        print("  python parallel_scraper.py")
        sys.exit(1)
    else:
        start_date = sys.argv[1]
        end_date = sys.argv[2]
        num_workers = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    
    run_parallel_scraper(start_date, end_date, num_workers)
