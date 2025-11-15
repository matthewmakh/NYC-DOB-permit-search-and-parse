# Building Intelligence System - Implementation Roadmap

## Current Status
✅ Database schema created
✅ 3 new tables: buildings, owner_contacts, building_metrics
✅ permits and contacts tables updated with bbl column
✅ Migration successfully run on Railway database

## Data Pipeline - Next Steps

### Phase 1: Foundation (BBL linking)
**Status:** Ready to implement once new permit data comes in

1. **Populate BBL from permit scraper** 
   - The `add_permit_contacts.py` already extracts block/lot
   - These will auto-populate as new permits are scraped
   - Run `step1_link_permits_to_buildings.py` to create building records

### Phase 2: Building Enrichment

2. **PLUTO Data Integration** (`step2_enrich_from_pluto.py`)
   - Download MapPLUTO dataset from NYC Open Data
   - For each building BBL, pull:
     * Owner name
     * Owner mailing address
     * Building class, land use
     * Units, floors, square footage
     * Year built
   - Updates: `buildings` table

3. **ACRIS Ownership Data** (`step3_enrich_from_acris.py`)
   - Query ACRIS API by BBL
   - Find latest DEED document
   - Extract:
     * Purchase date
     * Purchase price
     * Mortgage amount (from MTGE docs)
   - Updates: `buildings.purchase_date`, `purchase_price`, `mortgage_amount`

### Phase 3: Valuation

4. **Zillow Integration** (`step4_add_zillow_valuations.py`)
   - Use Zillow unofficial API or scraping
   - For each address/BBL:
     * Zestimate (property value)
     * Rent Zestimate
   - Calculate annual rent = rent_per_unit * residential_units
   - Calculate equity = estimated_value - mortgage_amount
   - Updates: `buildings.estimated_value`, `estimated_rent_per_unit`, `estimated_annual_rent`, `estimated_equity`

5. **Redfin Backup** (`step4b_add_redfin_valuations.py`)
   - Same as Zillow but for properties where Zillow has no data
   - Fills gaps in coverage

### Phase 4: Scoring

6. **Calculate Building Metrics** (`step5_calculate_metrics.py`)
   - Aggregate permit spend by BBL:
     * Sum all permit costs for last 3 years
     * Count permits
     * Identify major work types
   - Updates: `building_metrics.total_permits_3yr`, `total_spend_3yr`, `last_permit_date`

7. **Calculate Scores** (`step6_calculate_scores.py`)
   - **Affordability Score** (0-100):
     * High equity = higher score
     * Low mortgage = higher score
     * Recent purchase with appreciation = higher score
   
   - **Renovation Need Score** (0-100):
     * Old building + low recent spend = higher score
     * Many units but minimal upgrades = higher score
   
   - **Contact Quality Score** (0-100):
     * Owner contacts exist = higher score
     * Mobile phone = higher score
     * Verified contact = higher score
   
   - **Overall Priority** = weighted average
   - **Tier** = A (80-100), B (60-79), C (40-59), D (0-39)
   - **Summary** = human readable explanation
   
   - Updates: `building_metrics` all score fields

### Phase 5: Contact Enrichment

8. **Skip Trace Owners** (`step7_skip_trace_owners.py`)
   - For each unique owner_name in buildings:
     * Query People Data Labs or similar
     * Get phone, email
     * Store in `owner_contacts`
   - Updates: `owner_contacts` table

9. **Link Contacts to Buildings** (`step8_link_owner_contacts.py`)
   - Join buildings → owner_contacts on owner_name
   - Update contact_quality_score based on results

### Phase 6: Dashboard Integration

10. **Update Dashboard API** (`dashboard_html/app.py`)
    - Add new endpoints:
      * `/api/buildings` - list all buildings with scores
      * `/api/building/<bbl>` - detailed building profile
      * `/api/targets` - filtered list by priority tier
    - Modify existing `/api/permits` to include BBL and building data

11. **Update Frontend** 
    - Add building view with full intelligence
    - Show scores, owner info, valuation, contact info
    - Filter by priority tier
    - Sort by overall_priority_score

## Running the Pipeline

### Initial Setup (one-time)
```bash
# 1. Run migration
python migrate_add_building_intelligence.py

# 2. Wait for new permits to be scraped (with BBL data)
# OR re-run contact scraper on existing permits to populate block/lot
```

### Regular Updates (daily/weekly)
```bash
# 1. Link new permits to buildings
python step1_link_permits_to_buildings.py

# 2. Enrich buildings from NYC data
python step2_enrich_from_pluto.py
python step3_enrich_from_acris.py

# 3. Add valuations
python step4_add_zillow_valuations.py
python step4b_add_redfin_valuations.py  # for gaps

# 4. Calculate metrics and scores
python step5_calculate_metrics.py
python step6_calculate_scores.py

# 5. Enrich contacts
python step7_skip_trace_owners.py
python step8_link_owner_contacts.py
```

## Data Flow Summary
```
Permits (with BBL) 
    ↓
Buildings created (step1)
    ↓
PLUTO enrichment (step2) → owner, units, size
    ↓
ACRIS enrichment (step3) → purchase price, date
    ↓
Zillow/Redfin (step4) → current value, rent
    ↓
Calculate metrics (step5) → aggregate permit spend
    ↓
Calculate scores (step6) → affordability, need, priority
    ↓
Skip trace (step7) → owner contacts
    ↓
Dashboard (step10) → display everything
```

## Key Decisions Made

1. **No historical tracking** - Just current snapshot. Add valuation_history later if needed.
2. **One valuation per building** - Store best/latest, not all sources
3. **Owner contacts separate** - Same owner can have multiple buildings
4. **Scores are calculated, not stored intermediates** - Keep it lean
5. **BBL is the key** - Everything links through BBL

## Next Immediate Action

Re-run the contact scraper on a few permits to populate the new block/lot/BBL fields, then we can test the full pipeline.
