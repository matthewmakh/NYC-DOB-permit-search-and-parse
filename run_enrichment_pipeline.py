#!/usr/bin/env python3
"""
Master Enrichment Pipeline
Runs all building enrichment steps in sequence with dependency checking

Execution order:
1. Step 1: Link permits to buildings (derive BBL)
2. Step 2: Enrich from PLUTO + RPAD + HPD (owners, assessed values, violations)
3. Step 3: Enrich from ACRIS (transaction history)
4. Step 4: Enrich from Tax/Lien data (delinquency, ECB liens, DOB violations)
5. Geocode permits (lat/lng)

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
    """Run a Python script and return success status"""
    print(f"\n🚀 Running: {script_name}")
    print(f"   Description: {description}")
    
    start_time = time.time()
    
    try:
        result = subprocess.run(
            [sys.executable, script_name],
            capture_output=True,
            text=True,
            check=False
        )
        
        duration = time.time() - start_time
        
        # Print script output
        if result.stdout:
            print(result.stdout)
        
        if result.returncode == 0:
            print_success(f"Completed in {duration:.1f}s")
            return True
        else:
            print_error(f"Failed with exit code {result.returncode}")
            if result.stderr:
                print(f"\nError output:\n{result.stderr}")
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
    
    # ===== STEP 2: Enrich from PLUTO + RPAD + HPD =====
    print_step(2, "Enrich from PLUTO + RPAD + HPD (Tri-Source)")
    results['step2'] = run_script(
        'step2_enrich_from_pluto.py',
        'Add owner names, building characteristics, assessed values, HPD data'
    )
    
    if not results['step2']:
        print_warning("Step 2 failed - continuing to next steps")
    
    # ===== STEP 3: Enrich from ACRIS =====
    print_step(3, "Enrich from ACRIS (Transaction History)")
    results['step3'] = run_script(
        'step3_enrich_from_acris.py',
        'Add purchase dates, sale prices, mortgage amounts'
    )
    
    if not results['step3']:
        print_warning("Step 3 failed - continuing to next steps")
    
    # ===== STEP 4: Enrich from Tax/Lien Data =====
    print_step(4, "Enrich from Tax Delinquency & Liens")
    results['step4'] = run_script(
        'step4_enrich_from_tax_liens.py',
        'Add tax delinquency status, ECB liens, DOB violations'
    )
    
    if not results['step4']:
        print_warning("Step 4 failed - continuing to geocoding")
    
    # ===== STEP 5: Geocode Permits =====
    print_step(5, "Geocode Permits (Latitude/Longitude)")
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
        print_error("\n⚠️  Critical steps failed - enrichment incomplete")
        sys.exit(1)
    else:
        print_success("\n✅ Pipeline completed successfully!")
    
    if critical_failed:
        print_error("\n❌ PIPELINE FAILED - Critical steps did not complete")
        sys.exit(1)
    else:
        print_success("\n✅ PIPELINE COMPLETED SUCCESSFULLY")
        sys.exit(0)

if __name__ == "__main__":
    main()
