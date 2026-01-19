#!/usr/bin/env python3
"""
Sequential permit scraper with verbose output for testing/debugging
Runs each source one at a time with detailed progress logging
"""

import os
import sys
import argparse
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load .env from dashboard_html if root .env doesn't exist
if os.path.exists('.env'):
    load_dotenv('.env')
elif os.path.exists('dashboard_html/.env'):
    load_dotenv('dashboard_html/.env')

from permit_scraper_api import (
    NYCOpenDataClient,
    DOBNowFilingsClient,
    DOBNowApprovedClient,
    DOBJobApplicationsClient,
    PermitDatabase,
    DB_CONFIG
)


def scrape_bis_permits(db, start_date: str, end_date: str):
    """Scrape BIS Permit Issuance (legacy system)"""
    print("\n" + "=" * 70)
    print("📋 SOURCE 1: BIS PERMIT ISSUANCE (Legacy System)")
    print("=" * 70)
    print(f"   Date range: {start_date} to {end_date}")
    print(f"   Note: BIS only has data up to mid-2020, so expect 0 for recent dates")
    print("-" * 70)
    
    client = NYCOpenDataClient(app_token=None)
    
    total_fetched = 0
    total_inserted = 0
    total_skipped = 0
    offset = 0
    batch_size = 5000
    batch_num = 0
    
    while True:
        batch_num += 1
        print(f"\n   [BIS] Batch {batch_num}: Fetching from offset {offset}...")
        
        try:
            permits = client.fetch_permits(
                start_date=start_date,
                end_date=end_date,
                limit=batch_size,
                offset=offset
            )
            
            if not permits:
                print(f"   [BIS] No more records to fetch")
                break
            
            total_fetched += len(permits)
            print(f"   [BIS] Fetched {len(permits)} records (total: {total_fetched})")
            print(f"   [BIS] Inserting {len(permits)} records into database...")
            
            batch_inserted = 0
            batch_skipped = 0
            for i, p in enumerate(permits):
                permit_no = p.get('job__', 'N/A')
                filing_date = p.get('filing_date', '')[:10] if p.get('filing_date') else 'N/A'
                borough = p.get('borough', 'N/A')
                
                if db.insert_permit(p):
                    total_inserted += 1
                    batch_inserted += 1
                    status = "✅ INSERTED"
                else:
                    total_skipped += 1
                    batch_skipped += 1
                    status = "⏭️  SKIPPED (duplicate)"
                
                # Show every record
                print(f"   [{i+1:4}/{len(permits)}] {permit_no} | {filing_date} | {borough:13} | {status}")
            
            db.conn.commit()
            print(f"\n   [BIS] Batch {batch_num} complete: {batch_inserted} inserted, {batch_skipped} skipped")
            
            if len(permits) < batch_size:
                break
            offset += batch_size
            
        except Exception as e:
            print(f"   [BIS] ⚠️ Error: {e}")
            break
    
    print("-" * 70)
    print(f"   [BIS] ✅ COMPLETE")
    print(f"   [BIS] Total fetched: {total_fetched}")
    print(f"   [BIS] Total inserted: {total_inserted}")
    print(f"   [BIS] Total skipped (duplicates): {total_skipped}")
    print("=" * 70)
    
    return total_fetched, total_inserted


def scrape_dob_now_filings(db, start_date: str, end_date: str):
    """Scrape DOB NOW Job Filings (new applications)"""
    print("\n" + "=" * 70)
    print("⭐ SOURCE 2: DOB NOW JOB FILINGS")
    print("=" * 70)
    print(f"   Date range: {start_date} to {end_date}")
    print(f"   This is where most NEW permit filings go (current system)")
    print("-" * 70)
    
    client = DOBNowFilingsClient(app_token=None)
    
    total_fetched = 0
    total_inserted = 0
    total_skipped = 0
    offset = 0
    batch_size = 5000
    batch_num = 0
    
    while True:
        batch_num += 1
        print(f"\n   [Filings] Batch {batch_num}: Fetching from offset {offset}...")
        
        try:
            filings = client.fetch_filings(
                start_date=start_date,
                end_date=end_date,
                limit=batch_size,
                offset=offset
            )
            
            if not filings:
                print(f"   [Filings] No more records to fetch")
                break
            
            total_fetched += len(filings)
            print(f"   [Filings] Fetched {len(filings)} records (total: {total_fetched})")
            
            # Show sample record
            if batch_num == 1 and filings:
                sample = filings[0]
                print(f"   [Filings] Sample record:")
                print(f"             - Filing #: {sample.get('job_filing_number')}")
                print(f"             - Date: {sample.get('filing_date', '')[:10] if sample.get('filing_date') else 'N/A'}")
                print(f"             - Borough: {sample.get('borough')}")
                print(f"             - Address: {sample.get('house_no')} {sample.get('street_name')}")
                print(f"             - BBL: {sample.get('bbl')}")
            
            # FAST: Bulk check which permits already exist
            print(f"   [Filings] Checking for existing permits (bulk query)...")
            permit_nos = [f.get('job_filing_number') for f in filings if f.get('job_filing_number')]
            existing = db.get_existing_permit_nos(permit_nos)
            print(f"   [Filings] Found {len(existing)} already in database, {len(filings) - len(existing)} are new")
            
            # Filter to only new records
            new_filings = [f for f in filings if f.get('job_filing_number') not in existing]
            batch_skipped = len(filings) - len(new_filings)
            total_skipped += batch_skipped
            
            if new_filings:
                print(f"   [Filings] Inserting {len(new_filings)} new records...")
                batch_inserted = 0
                for i, f in enumerate(new_filings):
                    permit_no = f.get('job_filing_number', 'N/A')
                    filing_date = f.get('filing_date', '')[:10] if f.get('filing_date') else 'N/A'
                    borough = f.get('borough', 'N/A')
                    
                    # Insert without checking (we already filtered)
                    db.cursor.execute("SAVEPOINT insert_filing")
                    try:
                        # Call insert with skip_exists_check=True since we already did bulk check
                        if db.insert_dob_now_filing(f, skip_exists_check=True):
                            batch_inserted += 1
                            total_inserted += 1
                            status = "✅ INSERTED"
                        else:
                            status = "⚠️  FAILED"
                    except Exception as e:
                        db.cursor.execute("ROLLBACK TO SAVEPOINT insert_filing")
                        status = f"❌ ERROR: {str(e)[:30]}"
                    
                    # Show progress every 50 records or for first 10
                    if i < 10 or (i + 1) % 50 == 0 or i == len(new_filings) - 1:
                        print(f"   [{i+1:4}/{len(new_filings)}] {permit_no} | {filing_date} | {borough:13} | {status}")
                
                db.conn.commit()
                print(f"\n   [Filings] Batch {batch_num} complete: {batch_inserted} inserted, {batch_skipped} skipped (duplicates)")
            else:
                print(f"   [Filings] Batch {batch_num}: All {batch_skipped} records already exist, skipping")
            
            if len(filings) < batch_size:
                break
            offset += batch_size
            
        except Exception as e:
            print(f"   [Filings] ⚠️ Error: {e}")
            break
    
    print("-" * 70)
    print(f"   [Filings] ✅ COMPLETE")
    print(f"   [Filings] Total fetched: {total_fetched}")
    print(f"   [Filings] Total inserted: {total_inserted}")
    print(f"   [Filings] Total skipped (duplicates): {total_skipped}")
    print("=" * 70)
    
    return total_fetched, total_inserted


def scrape_dob_now_approved(db, start_date: str, end_date: str):
    """Scrape DOB NOW Approved Permits (issued permits)"""
    print("\n" + "=" * 70)
    print("✅ SOURCE 3: DOB NOW APPROVED PERMITS")
    print("=" * 70)
    print(f"   Date range: {start_date} to {end_date}")
    print(f"   These are permits that have been ISSUED (approved)")
    print("-" * 70)
    
    client = DOBNowApprovedClient(app_token=None)
    
    total_fetched = 0
    total_inserted = 0
    total_skipped = 0
    offset = 0
    batch_size = 5000
    batch_num = 0
    
    while True:
        batch_num += 1
        print(f"\n   [Approved] Batch {batch_num}: Fetching from offset {offset}...")
        
        try:
            permits = client.fetch_permits(
                start_date=start_date,
                end_date=end_date,
                limit=batch_size,
                offset=offset
            )
            
            if not permits:
                print(f"   [Approved] No more records to fetch")
                break
            
            total_fetched += len(permits)
            print(f"   [Approved] Fetched {len(permits)} records (total: {total_fetched})")
            
            # Show sample record
            if batch_num == 1 and permits:
                sample = permits[0]
                print(f"   [Approved] Sample record:")
                print(f"              - Filing #: {sample.get('job_filing_number')}")
                print(f"              - Issued: {sample.get('issued_date', '')[:10] if sample.get('issued_date') else 'N/A'}")
                print(f"              - Borough: {sample.get('borough')}")
                print(f"              - Address: {sample.get('house_no')} {sample.get('street_name')}")
                print(f"              - Work Type: {sample.get('work_type')}")
            
            # FAST: Bulk check which permits already exist
            print(f"   [Approved] Checking for existing permits (bulk query)...")
            permit_nos = [p.get('job_filing_number') or p.get('work_permit') for p in permits]
            permit_nos = [pn for pn in permit_nos if pn]  # Filter out None values
            existing = db.get_existing_permit_nos(permit_nos)
            
            # Filter to only new records
            new_permits = [p for p in permits if (p.get('job_filing_number') or p.get('work_permit')) not in existing]
            batch_skipped = len(permits) - len(new_permits)
            total_skipped += batch_skipped
            print(f"   [Approved] Found {len(existing)} already in database, {len(new_permits)} are new")
            
            if new_permits:
                print(f"   [Approved] Inserting {len(new_permits)} new records...")
                batch_inserted = 0
                for i, p in enumerate(new_permits):
                    permit_no = p.get('job_filing_number') or p.get('work_permit', 'N/A')
                    issued_date = p.get('issued_date', '')[:10] if p.get('issued_date') else 'N/A'
                    borough = p.get('borough', 'N/A')
                    work_type = p.get('work_type', 'N/A')[:20] if p.get('work_type') else 'N/A'
                    
                    # Insert without checking (we already filtered)
                    db.cursor.execute("SAVEPOINT insert_approved")
                    try:
                        if db.insert_dob_now_approved(p, skip_exists_check=True):
                            batch_inserted += 1
                            total_inserted += 1
                            status = "✅ INSERTED"
                        else:
                            status = "⚠️  FAILED"
                    except Exception as e:
                        db.cursor.execute("ROLLBACK TO SAVEPOINT insert_approved")
                        status = f"❌ ERROR: {str(e)[:30]}"
                    
                    # Show progress every 50 records or for first 10
                    if i < 10 or (i + 1) % 50 == 0 or i == len(new_permits) - 1:
                        print(f"   [{i+1:4}/{len(new_permits)}] {permit_no} | {issued_date} | {borough:13} | {work_type:20} | {status}")
                
                db.conn.commit()
                print(f"\n   [Approved] Batch {batch_num} complete: {batch_inserted} inserted, {batch_skipped} skipped (duplicates)")
            else:
                print(f"   [Approved] Batch {batch_num}: All {batch_skipped} records already exist, skipping")
            
            if len(permits) < batch_size:
                break
            offset += batch_size
            
        except Exception as e:
            print(f"   [Approved] ⚠️ Error: {e}")
            break
    
    print("-" * 70)
    print(f"   [Approved] ✅ COMPLETE")
    print(f"   [Approved] Total fetched: {total_fetched}")
    print(f"   [Approved] Total inserted: {total_inserted}")
    print(f"   [Approved] Total skipped (duplicates): {total_skipped}")
    print("=" * 70)
    
    return total_fetched, total_inserted


def scrape_job_applications(db, start_date: str, end_date: str):
    """Scrape DOB Job Applications (HAS PHONE NUMBERS!)"""
    print("\n" + "=" * 70)
    print("📞 SOURCE 4: DOB JOB APPLICATIONS (WITH PHONE NUMBERS!)")
    print("=" * 70)
    print(f"   Date range: {start_date} to {end_date}")
    print(f"   This dataset includes owner_sphone__ - actual phone numbers!")
    print("-" * 70)
    
    client = DOBJobApplicationsClient(app_token=None)
    
    total_fetched = 0
    total_inserted = 0
    total_skipped = 0
    total_with_phone = 0
    offset = 0
    batch_size = 5000
    batch_num = 0
    
    while True:
        batch_num += 1
        print(f"\n   [JobApps] Batch {batch_num}: Fetching from offset {offset}...")
        
        try:
            apps = client.fetch_applications(
                start_date=start_date,
                end_date=end_date,
                limit=batch_size,
                offset=offset
            )
            
            if not apps:
                print(f"   [JobApps] No more records to fetch")
                break
            
            total_fetched += len(apps)
            
            # Count how many have phone numbers
            with_phone = sum(1 for a in apps if a.get('owner_sphone__'))
            total_with_phone += with_phone
            print(f"   [JobApps] Fetched {len(apps)} records ({with_phone} with phone numbers)")
            
            # Show sample record
            if batch_num == 1 and apps:
                sample = apps[0]
                print(f"   [JobApps] Sample record:")
                print(f"             - Job #: {sample.get('job__')}")
                print(f"             - Date: {sample.get('pre__filing_date', 'N/A')}")
                print(f"             - Borough: {sample.get('borough')}")
                print(f"             - Owner: {sample.get('owner_s_first_name')} {sample.get('owner_s_last_name')}")
                print(f"             - Owner Phone: {sample.get('owner_sphone__', 'N/A')} ⭐")
            
            # FAST: Bulk check which permits already exist
            print(f"   [JobApps] Checking for existing permits (bulk query)...")
            permit_nos = [a.get('job__') for a in apps if a.get('job__')]
            existing = db.get_existing_permit_nos(permit_nos)
            print(f"   [JobApps] Found {len(existing)} already in database, {len(apps) - len(existing)} are new")
            
            # Filter to only new records
            new_apps = [a for a in apps if a.get('job__') not in existing]
            batch_skipped = len(apps) - len(new_apps)
            total_skipped += batch_skipped
            
            if new_apps:
                print(f"   [JobApps] Inserting {len(new_apps)} new records...")
                batch_inserted = 0
                for i, a in enumerate(new_apps):
                    job_no = a.get('job__', 'N/A')
                    filing_date = a.get('pre__filing_date', 'N/A')[:10] if a.get('pre__filing_date') else 'N/A'
                    borough = a.get('borough', 'N/A')
                    has_phone = "📞" if a.get('owner_sphone__') else "  "
                    
                    # Insert without checking (we already filtered)
                    db.cursor.execute("SAVEPOINT insert_app")
                    try:
                        if db.insert_job_application(a, skip_exists_check=True):
                            batch_inserted += 1
                            total_inserted += 1
                            status = "✅ INSERTED"
                        else:
                            status = "⚠️  FAILED"
                    except Exception as e:
                        db.cursor.execute("ROLLBACK TO SAVEPOINT insert_app")
                        status = f"❌ ERROR: {str(e)[:30]}"
                    
                    # Show progress every 50 records or for first 10
                    if i < 10 or (i + 1) % 50 == 0 or i == len(new_apps) - 1:
                        print(f"   [{i+1:4}/{len(new_apps)}] {has_phone} {job_no} | {filing_date} | {borough:13} | {status}")
                
                db.conn.commit()
                print(f"\n   [JobApps] Batch {batch_num} complete: {batch_inserted} inserted, {batch_skipped} skipped (duplicates)")
            else:
                print(f"   [JobApps] Batch {batch_num}: All {batch_skipped} records already exist, skipping")
            
            if len(apps) < batch_size:
                break
            offset += batch_size
            
        except Exception as e:
            print(f"   [JobApps] ⚠️ Error: {e}")
            import traceback
            traceback.print_exc()
            break
    
    print("-" * 70)
    print(f"   [JobApps] ✅ COMPLETE")
    print(f"   [JobApps] Total fetched: {total_fetched}")
    print(f"   [JobApps] Total with phone numbers: {total_with_phone} 📞")
    print(f"   [JobApps] Total inserted: {total_inserted}")
    print(f"   [JobApps] Total skipped (duplicates): {total_skipped}")
    print("=" * 70)
    
    return total_fetched, total_inserted


def run_sequential_scraper(start_date: str, end_date: str, skip_bis: bool = True):
    """Run the scraper with sources running one at a time"""
    
    print("\n" + "#" * 70)
    print("#" + " " * 68 + "#")
    print("#   NYC DOB SEQUENTIAL PERMIT SCRAPER - VERBOSE MODE" + " " * 17 + "#")
    print("#" + " " * 68 + "#")
    print("#" * 70)
    print(f"\n📅 Date range: {start_date} to {end_date}")
    print(f"📊 Sources: {'Skipping BIS | ' if skip_bis else 'BIS | '}DOB NOW Filings | DOB NOW Approved | Job Applications (📞)")
    
    # Connect to database
    print("\n🔌 Connecting to database...")
    db = PermitDatabase(DB_CONFIG)
    db.connect()
    print("🔌 Connected!")
    
    results = {}
    
    try:
        # Source 1: BIS (skip by default for recent dates)
        if not skip_bis:
            fetched, inserted = scrape_bis_permits(db, start_date, end_date)
            results['bis'] = {'fetched': fetched, 'inserted': inserted}
        else:
            print("\n" + "=" * 70)
            print("📋 SOURCE 1: BIS PERMIT ISSUANCE - SKIPPED")
            print("   (BIS only has data up to mid-2020)")
            print("=" * 70)
            results['bis'] = {'fetched': 0, 'inserted': 0}
        
        # Source 2: DOB NOW Filings
        fetched, inserted = scrape_dob_now_filings(db, start_date, end_date)
        results['filings'] = {'fetched': fetched, 'inserted': inserted}
        
        # Source 3: DOB NOW Approved
        fetched, inserted = scrape_dob_now_approved(db, start_date, end_date)
        results['approved'] = {'fetched': fetched, 'inserted': inserted}
        
        # Source 4: Job Applications (HAS PHONE NUMBERS!)
        fetched, inserted = scrape_job_applications(db, start_date, end_date)
        results['job_apps'] = {'fetched': fetched, 'inserted': inserted}
        
    finally:
        db.close()
        print("\n🔌 Database connection closed")
    
    # Final summary
    print("\n" + "#" * 70)
    print("#" + " " * 68 + "#")
    print("#   FINAL SUMMARY" + " " * 51 + "#")
    print("#" + " " * 68 + "#")
    print("#" * 70)
    
    total_fetched = sum(r['fetched'] for r in results.values())
    total_inserted = sum(r['inserted'] for r in results.values())
    
    print(f"""
   Source                 Fetched    Inserted
   ─────────────────────  ─────────  ─────────
   BIS (Legacy)           {results['bis']['fetched']:>9,}  {results['bis']['inserted']:>9,}
   DOB NOW Filings        {results['filings']['fetched']:>9,}  {results['filings']['inserted']:>9,}
   DOB NOW Approved       {results['approved']['fetched']:>9,}  {results['approved']['inserted']:>9,}
   Job Applications 📞    {results['job_apps']['fetched']:>9,}  {results['job_apps']['inserted']:>9,}
   ─────────────────────  ─────────  ─────────
   TOTAL                  {total_fetched:>9,}  {total_inserted:>9,}
""")
    print("#" * 70)
    print("✅ SCRAPING COMPLETE!")
    print("#" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Sequential NYC DOB Permit Scraper')
    parser.add_argument('--days', '-d', type=int, default=7, help='Number of days back to scrape')
    parser.add_argument('--start', type=str, help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, help='End date (YYYY-MM-DD)')
    parser.add_argument('--include-bis', action='store_true', help='Include BIS source (legacy, usually empty for recent dates)')
    
    args = parser.parse_args()
    
    if args.start and args.end:
        start_date = args.start
        end_date = args.end
    else:
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=args.days)).strftime('%Y-%m-%d')
        print(f"📅 Scraping last {args.days} days: {start_date} to {end_date}")
    
    run_sequential_scraper(start_date, end_date, skip_bis=not args.include_bis)
