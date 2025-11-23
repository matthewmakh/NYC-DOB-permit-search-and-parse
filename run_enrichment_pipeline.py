#!/usr/bin/env python3
"""
Master Enrichment Pipeline
Runs all building enrichment steps in sequence with dependency checking

Execution order:
1. Step 1: Link permits to buildings (derive BBL)
2. Step 2: Enrich from PLUTO + RPAD (owners, assessed values)
3. Step 3: Enrich from ACRIS (transaction history)
4. Geocode permits (lat/lng)

Each step is self-contained and checks if work is needed before running.
"""

import subprocess
import sys
import time
from datetime import datetime

# Colors for output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'

def print_header(msg):
    print(f"\n{Colors.BOLD}{Colors.HEADER}{'='*70}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.HEADER}{msg}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.HEADER}{'='*70}{Colors.END}\n")

def print_step(step_num, name):
    print(f"\n{Colors.BOLD}{Colors.BLUE}▶ Step {step_num}: {name}{Colors.END}")
    print(f"{Colors.BLUE}{'─'*70}{Colors.END}")

def print_success(msg):
    print(f"{Colors.GREEN}✅ {msg}{Colors.END}")

def print_error(msg):
    print(f"{Colors.RED}❌ {msg}{Colors.END}")

def print_warning(msg):
    print(f"{Colors.YELLOW}⚠️  {msg}{Colors.END}")

def run_script(script_name, description):
    """Run a Python script and return success status with periodic progress updates"""
    print(f"\n🚀 Running: {script_name}")
    print(f"   Description: {description}")
    sys.stdout.flush()
    
    start_time = time.time()
    last_update = start_time
    
    try:
        # Run without capturing output so we see it in real-time
        process = subprocess.Popen(
            [sys.executable, script_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        # Read output line by line and print with periodic status updates
        for line in process.stdout:
            print(line, end='')
            sys.stdout.flush()
            
            # Print elapsed time every 5 minutes
            current_time = time.time()
            if current_time - last_update >= 300:  # 300 seconds = 5 minutes
                elapsed = current_time - start_time
                print(f"\n⏱️  Still running... Elapsed: {elapsed/60:.1f} minutes")
                sys.stdout.flush()
                last_update = current_time
        
        process.wait()
        duration = time.time() - start_time
        
        if process.returncode == 0:
            print_success(f"Completed in {duration:.1f}s")
            return True
        else:
            print_error(f"Failed with exit code {process.returncode}")
            return False
            
    except Exception as e:
        print_error(f"Exception running {script_name}: {e}")
        return False

def main():
    """Run the complete enrichment pipeline"""
    start_time = datetime.now()
    
    print_header("🏗️  BUILDING ENRICHMENT PIPELINE")
    print(f"Started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Track success/failure
    results = {}
    
    # ===== STEP 1: Link Permits to Buildings =====
    print_step(1, "Link Permits to Buildings (BBL Generation)")
    results['step1'] = run_script(
        'step1_link_permits_to_buildings.py',
        'Generate full BBL from block/lot and create building records'
    )
    
    if not results['step1']:
        print_error("Step 1 failed - cannot continue pipeline")
        sys.exit(1)
    
    # ===== STEP 2: Enrich from PLUTO + RPAD =====
    print_step(2, "Enrich from PLUTO + RPAD (Parallel)")
    results['step2'] = run_script(
        'step2_enrich_from_pluto_parallel.py',
        'Add owner names, building characteristics, assessed values (3 parallel workers)'
    )
    
    if not results['step2']:
        print_warning("Step 2 failed - continuing to next steps")
    
    # ===== STEP 3: Enrich from ACRIS =====
    print_step(3, "Enrich from ACRIS (Transaction History)")
    results['step3'] = run_script(
        'step3_enrich_from_acris_parallel.py',
        'Add purchase dates, sale prices, mortgage amounts'
    )
    
    if not results['step3']:
        print_warning("Step 3 failed - continuing to geocoding")
    
    # ===== STEP 4: Geocode Permits =====
    print_step(4, "Geocode Permits (Latitude/Longitude)")
    results['geocode'] = run_script(
        'geocode_permits.py',
        'Add geographic coordinates for mapping'
    )
    
    if not results['geocode']:
        print_warning("Geocoding failed - pipeline otherwise complete")
    
    # ===== SUMMARY =====
    end_time = datetime.now()
    duration = end_time - start_time
    
    print_header("📊 PIPELINE SUMMARY")
    
    print(f"Started:  {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Finished: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Duration: {duration.total_seconds():.1f} seconds\n")
    
    print("Step Results:")
    for step, success in results.items():
        status = "✅ SUCCESS" if success else "❌ FAILED"
        print(f"  {step:12} {status}")
    
    # Overall status
    critical_steps = ['step1', 'step2']  # Must succeed
    critical_failed = any(not results.get(step, False) for step in critical_steps)
    
    if critical_failed:
        print_error("\n❌ PIPELINE FAILED - Critical steps did not complete")
        sys.exit(1)
    else:
        print_success("\n✅ PIPELINE COMPLETED SUCCESSFULLY")
        sys.exit(0)

if __name__ == "__main__":
    main()
