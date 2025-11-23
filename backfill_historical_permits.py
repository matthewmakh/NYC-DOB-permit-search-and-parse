#!/usr/bin/env python3
"""
Backfill Historical Permits from NYC Open Data
Scrapes permits in monthly batches to avoid API limits
"""

from dotenv import load_dotenv
load_dotenv()

from permit_scraper_api import run_api_scraper
from datetime import datetime, timedelta
import time

def backfill_permits(start_year=2008, end_year=2025):
    """
    Backfill permits month by month from start_year to end_year
    
    Args:
        start_year: Starting year (default 2008 - when API data starts)
        end_year: Ending year (default current year)
    """
    print(f"🚀 Starting historical permit backfill from {start_year} to {end_year}")
    print("=" * 80)
    
    current_date = datetime(start_year, 1, 1)
    end_date = datetime(end_year, 12, 31)
    total_months = 0
    
    while current_date <= end_date:
        # Calculate month end
        if current_date.month == 12:
            month_end = datetime(current_date.year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = datetime(current_date.year, current_date.month + 1, 1) - timedelta(days=1)
        
        # Don't go beyond end_date
        if month_end > end_date:
            month_end = end_date
        
        start_str = current_date.strftime('%Y-%m-%d')
        end_str = month_end.strftime('%Y-%m-%d')
        
        print(f"\n📅 Scraping: {current_date.strftime('%B %Y')} ({start_str} to {end_str})")
        print("-" * 80)
        
        try:
            # Run scraper for this month
            run_api_scraper(
                start_date=start_str,
                end_date=end_str,
                permit_type=None,  # All permit types
                borough=None       # All boroughs
            )
            total_months += 1
            
            # Small delay to be nice to the API
            time.sleep(2)
            
        except Exception as e:
            print(f"❌ Error scraping {current_date.strftime('%B %Y')}: {e}")
            print("Continuing to next month...")
        
        # Move to next month
        if current_date.month == 12:
            current_date = datetime(current_date.year + 1, 1, 1)
        else:
            current_date = datetime(current_date.year, current_date.month + 1, 1)
    
    print("\n" + "=" * 80)
    print(f"✅ Backfill complete! Processed {total_months} months from {start_year} to {end_year}")
    print(f"💡 Check your database - you should now have permits from all years")


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1:
        start_year = int(sys.argv[1])
    else:
        # Default: start from 2008 (when NYC Open Data begins)
        start_year = 2008
    
    if len(sys.argv) > 2:
        end_year = int(sys.argv[2])
    else:
        # Default: current year
        end_year = datetime.now().year
    
    print("\n🏗️  NYC DOB Historical Permit Backfill")
    print(f"📊 Will scrape ALL permits from {start_year} to {end_year}")
    print(f"⏱️  This will take approximately {(end_year - start_year + 1) * 12 * 3 / 60:.1f} minutes")
    print("\nPress Ctrl+C to cancel, or Enter to continue...")
    input()
    
    backfill_permits(start_year, end_year)
