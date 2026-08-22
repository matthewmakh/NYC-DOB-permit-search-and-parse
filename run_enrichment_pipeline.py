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
import os
from datetime import datetime

# Force unbuffered output for Railway logging
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Startup diagnostics
print(f"[STARTUP] Pipeline starting at {datetime.now().isoformat()}", flush=True)
print(f"[STARTUP] Python: {sys.version}", flush=True)
print(f"[STARTUP] Working directory: {os.getcwd()}", flush=True)
print(f"[STARTUP] DATABASE_URL set: {bool(os.getenv('DATABASE_URL'))}", flush=True)

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
    print(f"\n{Colors.BOLD}{Colors.HEADER}{'='*70}{Colors.END}", flush=True)
    print(f"{Colors.BOLD}{Colors.HEADER}{msg}{Colors.END}", flush=True)
    print(f"{Colors.BOLD}{Colors.HEADER}{'='*70}{Colors.END}\n", flush=True)

def print_step(step_num, name):
    print(f"\n{Colors.BOLD}{Colors.BLUE}▶ Step {step_num}: {name}{Colors.END}", flush=True)
    print(f"{Colors.BLUE}{'─'*70}{Colors.END}", flush=True)

def print_success(msg):
    print(f"{Colors.GREEN}✅ {msg}{Colors.END}", flush=True)

def print_error(msg):
    print(f"{Colors.RED}❌ {msg}{Colors.END}", flush=True)

def print_warning(msg):
    print(f"{Colors.YELLOW}⚠️  {msg}{Colors.END}", flush=True)

def run_script(script_name, description):
    """Run a Python script and return success status - streams output in real-time"""
    print(f"\n🚀 Running: {script_name}", flush=True)
    print(f"   Description: {description}", flush=True)
    
    start_time = time.time()
    
    try:
        # Stream output directly instead of capturing (for Railway logs)
        result = subprocess.run(
            [sys.executable, '-u', script_name],  # -u for unbuffered Python output
            text=True,
            check=False
            # Note: No capture_output, so stdout/stderr go directly to console
        )
        
        duration = time.time() - start_time
        
        if result.returncode == 0:
            print_success(f"Completed in {duration:.1f}s")
            return True
        else:
            print_error(f"Failed with exit code {result.returncode}")
            # Note: stderr was streamed directly to console, not captured
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

    # ===== STEP 0: Safe additive schema migrations =====
    print_step(0, "Apply Additive Data-Freshness Migrations")
    results['migrations'] = run_script(
        'migrate_add_freshness_and_jobs.py',
        'Create source-specific freshness clocks and durable enrichment queue'
    )
    if not results['migrations']:
        print_error("Migration failed - cannot safely continue")
        sys.exit(1)
    
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
    
    # Flag every tracked property touched by a recently-recorded deed before
    # the normal ACRIS stale-row selection runs.
    print_step('2b', "Detect Recent ACRIS Activity")
    results['recent_sales'] = run_script(
        'sync_recent_sales.py',
        'Flag properties with recent deeds, mortgages, assignments, or satisfactions'
    )
    if not results['recent_sales']:
        print_warning("Recent-sale detection failed - continuing with stale-window refresh")

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
        print_warning("Step 4 failed - continuing to Step 5")
    
    # ===== STEP 5: Enrich from NY SOS =====
    print_step(5, "Enrich from NY Secretary of State (LLC Owners)")
    results['step5'] = run_script(
        'step5_enrich_from_sos.py',
        'Find real people (CEO, agents) behind LLC-owned properties'
    )
    
    if not results['step5']:
        print_warning("Step 5 failed - continuing to geocoding")
    
    # ===== STEP 6: Distress / compliance / freshness signals =====
    print_step(6, "Enrich Distress & Compliance Signals")
    results['step6'] = run_script(
        'step6_enrich_signals.py',
        'Litigation, evictions, exemptions, speculation list, DOB complaints, COs, FISP, LL84, rolling sales'
    )

    if not results['step6']:
        print_warning("Step 6 failed - continuing to geocoding")

    # ===== STEP 7: Geocode Permits =====
    print_step(7, "Geocode Permits (Latitude/Longitude)")
    results['geocode'] = run_script(
        'geocode_permits.py',
        'Add geographic coordinates for mapping'
    )

    if not results['geocode']:
        print_warning("Geocoding failed - pipeline otherwise complete")

    # ===== STEP 8: Recover auto-add work interrupted by web-worker restarts =====
    print_step(8, "Process Durable Property Enrichment Queue")
    results['property_jobs'] = run_script(
        'process_property_enrichment_jobs.py',
        'Recover and finish auto-add enrichment jobs left by web workers'
    )
    if not results['property_jobs']:
        print_warning("Property enrichment queue processing failed")
    
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
    critical_steps = ['migrations', 'step1', 'step2']  # Must succeed
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
    try:
        main()
    except Exception as e:
        print(f"[FATAL] Unhandled exception in pipeline: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
