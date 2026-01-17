#!/usr/bin/env python3
"""
Step 5: Enrich Buildings from NY Secretary of State

Finds real people (CEO, Registered Agent) behind LLC-owned properties.

This script:
1. Identifies buildings owned by LLCs (from PLUTO, RPAD, or HPD)
2. Looks up each LLC in NY Secretary of State database
3. Stores the CEO/Agent name and address in new columns

IMPORTANT: Run migration first to add the SOS columns to the buildings table.

Usage:
    python step5_enrich_from_sos.py              # Process all eligible buildings
    python step5_enrich_from_sos.py --limit 100  # Process 100 buildings
    python step5_enrich_from_sos.py --dry-run    # Preview without saving
    python step5_enrich_from_sos.py --reprocess  # Re-enrich previously enriched buildings

Author: Matthew Makh
"""

import os
import sys
import re
import time
import argparse
from datetime import datetime
from typing import List, Dict, Optional, Tuple

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

# Load environment
if os.path.exists('.env'):
    load_dotenv('.env')
elif os.path.exists('dashboard_html/.env'):
    load_dotenv('dashboard_html/.env')
else:
    load_dotenv()

# Import the SOS lookup module (local copy for Railway compatibility)
try:
    from ny_sos_lookup import lookup_businesses, SOSBusinessResult, is_likely_individual
except ImportError as e:
    print(f"❌ Cannot import ny_sos_lookup")
    print(f"   Error: {e}")
    print(f"   Make sure httpx is installed: pip install httpx")
    sys.exit(1)

# =============================================================================
# CONFIGURATION
# =============================================================================

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', '5432')),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME', 'railway')
}

BATCH_SIZE = 50  # SOS API is slow, process in smaller batches
CONCURRENCY = 5  # How many SOS lookups to run in parallel
REFRESH_DAYS = int(os.getenv('SOS_REFRESH_DAYS', '180'))  # Re-check after 6 months

# LLC indicators to find owner names that should be looked up
LLC_PATTERNS = [
    r'\bLLC\b', r'\bL\.L\.C\.', r'\bINC\b', r'\bINC\.', r'\bINCORPORATED\b',
    r'\bCORP\b', r'\bCORP\.', r'\bCORPORATION\b', r'\bLTD\b', r'\bLTD\.',
    r'\bLIMITED\b', r'\bLP\b', r'\bL\.P\.', r'\bLLP\b', r'\bL\.L\.P\.',
    r'\bCOMPANY\b', r'\bCO\b', r'\bCO\.',
]


def is_llc_name(name: str) -> bool:
    """Check if a name looks like an LLC/Corp that we should look up."""
    if not name:
        return False
    name_upper = name.upper()
    for pattern in LLC_PATTERNS:
        if re.search(pattern, name_upper):
            return True
    return False


def get_db_connection():
    """Get database connection."""
    return psycopg2.connect(**DB_CONFIG)


def check_sos_columns_exist(conn) -> bool:
    """Check if SOS columns have been added to buildings table."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'buildings' AND column_name = 'sos_principal_name'
        """)
        return cur.fetchone() is not None


def run_migration(conn):
    """Add SOS columns to buildings table."""
    print("📦 Running migration to add SOS columns...")
    
    with conn.cursor() as cur:
        # Add SOS enrichment columns
        columns = [
            ("sos_principal_name", "VARCHAR(255)", "Primary contact (CEO or Agent)"),
            ("sos_principal_title", "VARCHAR(50)", "CEO, Registered Agent, etc."),
            ("sos_principal_street", "VARCHAR(255)", "Contact street address"),
            ("sos_principal_city", "VARCHAR(100)", "Contact city"),
            ("sos_principal_state", "VARCHAR(20)", "Contact state"),
            ("sos_principal_zip", "VARCHAR(15)", "Contact ZIP"),
            ("sos_entity_name", "VARCHAR(255)", "Official registered name"),
            ("sos_entity_status", "VARCHAR(50)", "Active, Inactive, etc."),
            ("sos_dos_id", "VARCHAR(50)", "NY DOS ID"),
            ("sos_formation_date", "DATE", "When entity was formed"),
            ("sos_last_enriched", "TIMESTAMP", "When SOS data was last updated"),
            ("sos_lookup_attempted", "BOOLEAN DEFAULT FALSE", "Whether lookup was attempted"),
            ("sos_lookup_source", "VARCHAR(255)", "Which owner name was looked up"),
        ]
        
        for col_name, col_type, comment in columns:
            try:
                cur.execute(f"""
                    ALTER TABLE buildings 
                    ADD COLUMN IF NOT EXISTS {col_name} {col_type}
                """)
            except psycopg2.Error as e:
                if "already exists" not in str(e):
                    print(f"   Warning: {col_name}: {e}")
        
        conn.commit()
        print("   ✅ Migration complete")


def get_buildings_needing_sos(conn, limit: Optional[int] = None, reprocess: bool = False, refresh: bool = True) -> List[Dict]:
    """
    Get buildings that need SOS enrichment.
    
    Filters:
    - NOT already enriched (sos_principal_name is NULL) OR
    - Stale data (sos_last_enriched older than REFRESH_DAYS)
    - NOT already attempted (unless reprocess=True)
    - Has at least one owner name source
    
    Args:
        limit: Max buildings to return
        reprocess: If True, include ALL buildings (even recently enriched)
        refresh: If True, include stale records (default: True)
    """
    with conn.cursor() as cur:
        # Build WHERE clause
        conditions = []
        
        if not reprocess:
            # Include: never attempted OR stale (older than REFRESH_DAYS)
            stale_condition = f"""
                (
                    -- Never attempted
                    (sos_lookup_attempted IS NULL OR sos_lookup_attempted = FALSE)
                    OR
                    -- Never enriched (no principal found)
                    (sos_principal_name IS NULL OR sos_principal_name = '')
                    {f"OR (sos_last_enriched < NOW() - INTERVAL '{REFRESH_DAYS} days')" if refresh else ''}
                )
            """
            conditions.append(stale_condition)
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        # Include sale_buyer_primary from ACRIS as most recent owner source
        query = f"""
            SELECT 
                id, 
                bbl, 
                address,
                sale_buyer_primary,
                sale_date,
                current_owner_name,
                owner_name_rpad,
                owner_name_hpd
            FROM buildings
            WHERE {where_clause}
            AND (
                sale_buyer_primary IS NOT NULL
                OR current_owner_name IS NOT NULL 
                OR owner_name_rpad IS NOT NULL 
                OR owner_name_hpd IS NOT NULL
            )
            ORDER BY 
                -- Prioritize buildings with ACRIS data (most recent)
                CASE WHEN sale_buyer_primary IS NOT NULL THEN 10 ELSE 0 END +
                CASE WHEN current_owner_name IS NOT NULL THEN 1 ELSE 0 END +
                CASE WHEN owner_name_rpad IS NOT NULL THEN 1 ELSE 0 END +
                CASE WHEN owner_name_hpd IS NOT NULL THEN 1 ELSE 0 END DESC,
                -- Then by most recent sale date
                sale_date DESC NULLS LAST,
                id
        """
        
        if limit:
            query += f" LIMIT {limit}"
        
        cur.execute(query)
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_best_llc_name(building: Dict) -> Tuple[Optional[str], str]:
    """
    Get the best LLC name to look up from a building's owner fields.
    Returns (name, source_field) or (None, '') if no LLC found.
    
    Priority order (MOST RECENT FIRST):
    1. sale_buyer_primary (ACRIS) - from the most recent deed
    2. owner_name_rpad (Tax) - current taxpayer
    3. current_owner_name (PLUTO) - updated annually
    4. owner_name_hpd (HPD) - registered owner
    
    Skips:
    - Names that look like individuals (not LLCs/Corps)
    """
    # Priority order: ACRIS (most recent) > RPAD (taxpayer) > PLUTO > HPD
    sources = [
        ('sale_buyer_primary', building.get('sale_buyer_primary')),
        ('owner_name_rpad', building.get('owner_name_rpad')),
        ('current_owner_name', building.get('current_owner_name')),
        ('owner_name_hpd', building.get('owner_name_hpd')),
    ]
    
    for source_field, name in sources:
        if not name:
            continue
        
        # Skip if it looks like an individual person (not an LLC/Corp)
        if is_likely_individual(name):
            continue
        
        # Only look up if it's an LLC/Corp
        if is_llc_name(name):
            return (name, source_field)
    
    return (None, '')


def process_sos_result(result: SOSBusinessResult) -> Dict:
    """Convert SOS result to database fields."""
    if not result.found:
        return {
            'sos_principal_name': None,
            'sos_principal_title': None,
            'sos_principal_street': None,
            'sos_principal_city': None,
            'sos_principal_state': None,
            'sos_principal_zip': None,
            'sos_entity_name': result.entity_name or None,
            'sos_entity_status': None,
            'sos_dos_id': None,
            'sos_formation_date': None,
        }
    
    # Find the best person to use (prefer CEO, then registered agent, then SOP agent)
    principal = None
    
    # First try to get an individual (real person, not a company)
    individuals = result.get_individuals()
    if individuals:
        principal = individuals[0]
    elif result.people:
        # Fall back to first person even if it's a company
        # Prefer CEO over agents
        ceo = result.get_ceo()
        if ceo:
            principal = ceo
        else:
            principal = result.people[0]
    
    return {
        'sos_principal_name': principal.full_name if principal else None,
        'sos_principal_title': principal.title if principal else None,
        'sos_principal_street': principal.street if principal else None,
        'sos_principal_city': principal.city if principal else None,
        'sos_principal_state': principal.state if principal else None,
        'sos_principal_zip': principal.zipcode if principal else None,
        'sos_entity_name': result.entity_name,
        'sos_entity_status': result.status,
        'sos_dos_id': result.dos_id,
        'sos_formation_date': result.formation_date,
    }


def update_buildings_with_sos(conn, updates: List[Dict]):
    """Bulk update buildings with SOS data."""
    if not updates:
        return
    
    with conn.cursor() as cur:
        for update in updates:
            cur.execute("""
                UPDATE buildings SET
                    sos_principal_name = %(sos_principal_name)s,
                    sos_principal_title = %(sos_principal_title)s,
                    sos_principal_street = %(sos_principal_street)s,
                    sos_principal_city = %(sos_principal_city)s,
                    sos_principal_state = %(sos_principal_state)s,
                    sos_principal_zip = %(sos_principal_zip)s,
                    sos_entity_name = %(sos_entity_name)s,
                    sos_entity_status = %(sos_entity_status)s,
                    sos_dos_id = %(sos_dos_id)s,
                    sos_formation_date = %(sos_formation_date)s,
                    sos_last_enriched = NOW(),
                    sos_lookup_attempted = TRUE,
                    sos_lookup_source = %(lookup_source)s
                WHERE id = %(building_id)s
            """, update)
        
        conn.commit()


def main():
    parser = argparse.ArgumentParser(description='Enrich buildings from NY Secretary of State')
    parser.add_argument('--limit', type=int, help='Limit number of buildings to process')
    parser.add_argument('--dry-run', action='store_true', help='Preview without saving')
    parser.add_argument('--reprocess', action='store_true', help='Re-process ALL buildings (ignore previous enrichment)')
    parser.add_argument('--no-refresh', action='store_true', help='Skip stale record refresh (only process new buildings)')
    args = parser.parse_args()
    
    print("=" * 70)
    print("🏛️  Step 5: Enrich Buildings from NY Secretary of State")
    print("=" * 70)
    print(f"   Refresh cycle: {REFRESH_DAYS} days (set SOS_REFRESH_DAYS to change)")
    print()
    
    # Connect to database
    print("📊 Connecting to database...")
    conn = get_db_connection()
    
    # Check/run migration
    if not check_sos_columns_exist(conn):
        run_migration(conn)
    else:
        print("   ✅ SOS columns already exist")
    
    # Get buildings to process
    refresh = not args.no_refresh
    print(f"\n📥 Finding buildings with LLC owners{'...' if refresh else ' (no refresh)...'}")
    buildings = get_buildings_needing_sos(conn, limit=args.limit, reprocess=args.reprocess, refresh=refresh)
    
    # Filter to only those with LLC names, track skip reasons
    llc_buildings = []
    skipped_individual = 0
    skipped_no_owner = 0
    
    for b in buildings:
        llc_name, source = get_best_llc_name(b)
        if llc_name:
            b['llc_name'] = llc_name
            b['llc_source'] = source
            llc_buildings.append(b)
        else:
            # Figure out why skipped
            any_owner = (
                b.get('sale_buyer_primary') or 
                b.get('current_owner_name') or 
                b.get('owner_name_rpad') or 
                b.get('owner_name_hpd')
            )
            if any_owner:
                skipped_individual += 1  # Has owner but it's an individual
            else:
                skipped_no_owner += 1
    
    print(f"   Found {len(buildings)} buildings with owner data")
    print(f"   ✅ {len(llc_buildings)} have LLC/Corp names to look up")
    print(f"   ⏭️  {skipped_individual} skipped (owner is already an individual)")
    print(f"   ⏭️  {skipped_no_owner} skipped (no valid owner name)")
    
    # Count unique LLCs (avoid duplicate lookups)
    unique_llcs = set(b['llc_name'].upper().strip() for b in llc_buildings)
    if len(unique_llcs) < len(llc_buildings):
        print(f"   📊 {len(unique_llcs)} unique LLCs (saving {len(llc_buildings) - len(unique_llcs)} duplicate lookups)")
    
    if not llc_buildings:
        print("\n✅ No buildings need SOS enrichment!")
        conn.close()
        return
    
    if args.dry_run:
        print("\n🔍 DRY RUN - First 10 LLCs that would be looked up:")
        for b in llc_buildings[:10]:
            print(f"   • {b['llc_name']} ({b['llc_source']}) - BBL {b['bbl']}")
        conn.close()
        return
    
    # Process in batches with deduplication
    # Build a cache of LLC -> result to avoid duplicate API calls
    llc_cache = {}
    
    print(f"\n🔄 Processing {len(llc_buildings)} buildings ({len(unique_llcs)} unique LLCs) in batches of {BATCH_SIZE}...")
    
    total_found = 0
    total_individuals = 0
    total_processed = 0
    start_time = time.time()
    
    for i in range(0, len(llc_buildings), BATCH_SIZE):
        batch = llc_buildings[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(llc_buildings) + BATCH_SIZE - 1) // BATCH_SIZE
        
        print(f"\n📦 Batch {batch_num}/{total_batches} ({len(batch)} buildings)...")
        
        # Prepare lookup names - only lookup names not already in cache
        names_to_lookup = []
        for b in batch:
            llc_key = b['llc_name'].upper().strip()
            if llc_key not in llc_cache:
                names_to_lookup.append(b['llc_name'])
        
        # Do batch lookup (only for new names)
        batch_start = time.time()
        if names_to_lookup:
            # Deduplicate the lookup list itself
            unique_names = list(set(names_to_lookup))
            results = lookup_businesses(unique_names, concurrency=CONCURRENCY)
            # Add to cache
            for name, result in results.items():
                llc_cache[name.upper().strip()] = result
        batch_time = time.time() - batch_start
        
        # Process results (using cache)
        updates = []
        batch_found = 0
        batch_individuals = 0
        cache_hits = 0
        
        for building in batch:
            llc_name = building['llc_name']
            llc_key = llc_name.upper().strip()
            result = llc_cache.get(llc_key)
            
            if result is None:
                # Shouldn't happen, but handle gracefully
                result = SOSBusinessResult(query_name=llc_name, normalized_name=llc_name)
            else:
                if llc_name not in names_to_lookup:
                    cache_hits += 1
            
            if result and result.found:
                batch_found += 1
                individuals = result.get_individuals()
                if individuals:
                    batch_individuals += 1
            
            # Prepare update
            sos_data = process_sos_result(result)
            sos_data['building_id'] = building['id']
            sos_data['lookup_source'] = building['llc_source']
            updates.append(sos_data)
        
        # Save to database
        update_buildings_with_sos(conn, updates)
        
        total_found += batch_found
        total_individuals += batch_individuals
        total_processed += len(batch)
        
        cache_msg = f" | Cache hits: {cache_hits}" if cache_hits > 0 else ""
        print(f"   ✅ Found: {batch_found}/{len(batch)} | Individuals: {batch_individuals} | Time: {batch_time:.1f}s{cache_msg}")
        
        # Rate limit between batches
        if i + BATCH_SIZE < len(llc_buildings):
            time.sleep(1)
    
    elapsed = time.time() - start_time
    
    print("\n" + "=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)
    print(f"   Total processed: {total_processed:,}")
    print(f"   Found in SOS:    {total_found:,} ({total_found/total_processed*100:.1f}%)")
    print(f"   With individuals: {total_individuals:,} ({total_individuals/total_processed*100:.1f}%)")
    print(f"   Total time:       {elapsed:.1f}s ({total_processed/elapsed:.1f} buildings/sec)")
    print()
    
    conn.close()


if __name__ == "__main__":
    main()
