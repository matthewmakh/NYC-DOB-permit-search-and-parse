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

# Force unbuffered output for Railway logging
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

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

# Import the SOS lookup module (local copy for Railway compatibility).
#
# NEVER sys.exit here: this module is imported by the web app's auto-add
# request path, and SystemExit is not an Exception — the old exit(1) sailed
# through every try/except, killed the gunicorn worker mid-request, and the
# platform edge reported the closed connection as an instant 502. That single
# line was the production outage. A missing httpx now just disables the
# lookup half; get_best_llc_name/process_sos_result keep working.
try:
    from ny_sos_lookup import lookup_businesses, SOSBusinessResult, is_likely_individual
    SOS_LOOKUP_IMPORT_ERROR = None
except ImportError as e:
    lookup_businesses = None
    SOSBusinessResult = None
    is_likely_individual = None
    SOS_LOOKUP_IMPORT_ERROR = str(e)
    print(f"⚠️  ny_sos_lookup unavailable ({e}) — SOS lookups disabled until httpx is installed")

# =============================================================================
# CONFIGURATION
# =============================================================================

# DATABASE_URL wins when set — it's how the deploy runbook passes the prod
# connection. The discrete DB_* variables remain for the Railway cron, whose
# environment defines them individually. Without this, a laptop run with only
# DATABASE_URL exported silently fell through to localhost.
DATABASE_URL = os.getenv('DATABASE_URL')

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
SOS_MAX_BUILDINGS = max(0, int(os.getenv('SOS_MAX_BUILDINGS', '5000')))

# LLC indicators to find owner names that should be looked up
LLC_PATTERNS = [
    r'\bLLC\b', r'\bL\.L\.C\.', r'\bINC\b', r'\bINC\.', r'\bINCORPORATED\b',
    r'\bCORP\b', r'\bCORP\.', r'\bCORPORATION\b', r'\bLTD\b', r'\bLTD\.',
    r'\bLIMITED\b', r'\bLP\b', r'\bL\.P\.', r'\bLLP\b', r'\bL\.L\.P\.',
    r'\bCOMPANY\b', r'\bCO\b', r'\bCO\.',
]

# Applied in SQL before LIMIT. Otherwise individual-owned rows can occupy the
# front of every bounded batch and starve actual LLCs later in the table.
CORPORATE_OWNER_SQL_REGEX = (
    r'\m(LLC|L[.]L[.]C|INC(ORPORATED)?|CORP(ORATION)?|LTD|LIMITED|'
    r'LP|L[.]P|LLP|L[.]L[.]P|COMPANY|CO)\M[.]?'
)


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
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL)
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
            ("sos_last_error", "TEXT", "Most recent transient SOS lookup error"),
            ("sos_last_error_at", "TIMESTAMP", "When the most recent SOS error occurred"),
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
            # A completed lookup with no matching entity is still complete.
            # Selecting every NULL principal made healthy misses run nightly.
            stale_condition = f"""
                (
                    -- Never attempted
                    (sos_lookup_attempted IS NULL OR sos_lookup_attempted = FALSE)
                    OR
                    -- Legacy/incomplete attempt with no success checkpoint
                    sos_last_enriched IS NULL
                    OR
                    -- A deed recorded after the last lookup: the building may
                    -- have changed hands, so the cached principal is suspect
                    -- regardless of the {REFRESH_DAYS}-day cycle.
                    (sale_recorded_date IS NOT NULL
                     AND sos_last_enriched IS NOT NULL
                     AND sale_recorded_date > sos_last_enriched)
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
                sale_recorded_date,
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
            AND concat_ws(' ', sale_buyer_primary, current_owner_name,
                           owner_name_hpd, owner_name_rpad) ~* %s
            ORDER BY 
                sos_last_enriched ASC NULLS FIRST,
                -- Prioritize buildings with ACRIS data (most recent)
                CASE WHEN sale_buyer_primary IS NOT NULL THEN 10 ELSE 0 END +
                CASE WHEN current_owner_name IS NOT NULL THEN 1 ELSE 0 END +
                CASE WHEN owner_name_rpad IS NOT NULL THEN 1 ELSE 0 END +
                CASE WHEN owner_name_hpd IS NOT NULL THEN 1 ELSE 0 END DESC,
                -- Then by most recent sale date
                sale_date DESC NULLS LAST,
                id
        """
        
        params = [CORPORATE_OWNER_SQL_REGEX]
        if limit:
            query += " LIMIT %s"
            params.append(limit)
        
        cur.execute(query, params)
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def get_best_llc_name(building: Dict) -> Tuple[Optional[str], str]:
    """
    Get the best LLC name to look up from a building's owner fields.
    Returns (name, source_field) or (None, '') if no LLC found.

    Priority order (MOST RECENT FIRST):
    1. sale_buyer_primary (ACRIS) - grantee on the most recent recorded deed,
       the legally authoritative current owner
    2. current_owner_name (PLUTO) - refreshed annually
    3. owner_name_hpd (HPD) - selected from owner-class registrations rather
       than agent/officer-only rows
    4. owner_name_rpad (Tax) - the RPAD open-data extract's newest vintage is
       FY2018/19 (verified live 2026-08), so these names can be years stale

    Skips:
    - Names that look like individuals (not LLCs/Corps)
    """
    sources = [
        ('sale_buyer_primary', building.get('sale_buyer_primary')),
        ('current_owner_name', building.get('current_owner_name')),
        ('owner_name_hpd', building.get('owner_name_hpd')),
        ('owner_name_rpad', building.get('owner_name_rpad')),
    ]
    
    for source_field, name in sources:
        if not name:
            continue
        
        # Skip if it looks like an individual person (not an LLC/Corp).
        # Without ny_sos_lookup the person-check is unavailable; is_llc_name
        # below still keeps obvious non-companies out.
        if is_likely_individual is not None and is_likely_individual(name):
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
    
    # Pick the most useful person for owner outreach.
    # Service-of-Process and Registered Agents are NOT the owner — they're
    # designated mail recipients (often a lawyer or registered-agent service).
    # The bug we used to have: get_individuals()[0] was returning whichever
    # individual happened to come back first, often the SoP agent, so the
    # building profile would show "C/O LARRY ENTE — Service of Process Agent"
    # as the recommended owner.
    AGENT_TITLES = {'Service of Process Agent', 'Registered Agent'}

    individuals = result.get_individuals()  # real-person names, any title
    ceo = result.get_ceo()                  # may be None

    principal = None
    # 1. CEO who is an actual individual — best signal.
    if ceo and ceo in individuals:
        principal = ceo
    # 2. Any non-agent individual (e.g. Director / Manager / Officer).
    if principal is None:
        for p in individuals:
            if p.title not in AGENT_TITLES:
                principal = p
                break
    # 3. Last-resort fallbacks: an agent individual, then any person at all
    #    (might be a company name). Better to surface SOMETHING than nothing,
    #    but the title will mark it so downstream code can skip enrichment.
    if principal is None and individuals:
        principal = individuals[0]
    if principal is None and result.people:
        principal = ceo or result.people[0]
    
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
                    sos_lookup_source = %(lookup_source)s,
                    sos_last_error = NULL,
                    sos_last_error_at = NULL
                WHERE id = %(building_id)s
            """, update)
        
        conn.commit()


def sos_result_needs_retry(result: SOSBusinessResult) -> bool:
    """Transport/service failures retry; an accurate name-miss does not."""
    error = (getattr(result, 'error', '') or '').strip()
    return bool(error and not error.startswith('No entity matching'))


def record_sos_failures(conn, failures: List[Dict]):
    """Persist diagnostics without advancing the successful-refresh clock."""
    if not failures:
        return
    with conn.cursor() as cur:
        for failure in failures:
            cur.execute("""
                UPDATE buildings
                SET sos_last_error = %s,
                    sos_last_error_at = NOW()
                WHERE id = %s
            """, (failure['error'][:4000], failure['building_id']))
    conn.commit()


def main():
    # The pipeline script genuinely cannot run without the lookup module —
    # exiting is right HERE, at the CLI entry, never at import time.
    if SOS_LOOKUP_IMPORT_ERROR:
        print(f"❌ Cannot run: ny_sos_lookup unavailable ({SOS_LOOKUP_IMPORT_ERROR})")
        print(f"   Install httpx first: pip install httpx")
        sys.exit(1)

    parser = argparse.ArgumentParser(description='Enrich buildings from NY Secretary of State')
    parser.add_argument(
        '--limit', type=int, default=SOS_MAX_BUILDINGS or None,
        help='Limit buildings this pass (default: SOS_MAX_BUILDINGS, 5000)')
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
    
    # Additive and idempotent, including error columns introduced after the
    # original migration.
    run_migration(conn)
    
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
    if args.limit and len(buildings) >= args.limit:
        print(f"   ↪ Bounded pass reached {args.limit:,}; remaining LLCs resume next run")
    
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
    total_failed = 0
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
        failures = []
        batch_found = 0
        batch_individuals = 0
        cache_hits = 0
        
        for building in batch:
            llc_name = building['llc_name']
            llc_key = llc_name.upper().strip()
            result = llc_cache.get(llc_key)
            
            if result is None:
                # A missing async result is not a healthy "not found".
                result = SOSBusinessResult(query_name=llc_name, normalized_name=llc_name)
                result.error = 'No lookup result returned'
            else:
                if llc_name not in names_to_lookup:
                    cache_hits += 1

            if sos_result_needs_retry(result):
                failures.append({
                    'building_id': building['id'],
                    'error': result.error,
                })
                continue
            
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
        record_sos_failures(conn, failures)
        
        total_found += batch_found
        total_individuals += batch_individuals
        total_processed += len(batch)
        total_failed += len(failures)
        
        cache_msg = f" | Cache hits: {cache_hits}" if cache_hits > 0 else ""
        print(f"   ✅ Found: {batch_found}/{len(batch)} | "
              f"Individuals: {batch_individuals} | Retryable failures: "
              f"{len(failures)} | Time: {batch_time:.1f}s{cache_msg}")
        
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
    print(f"   Retryable failures: {total_failed:,}")
    print(f"   Total time:       {elapsed:.1f}s ({total_processed/elapsed:.1f} buildings/sec)")
    print()
    
    conn.close()
    if total_failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
