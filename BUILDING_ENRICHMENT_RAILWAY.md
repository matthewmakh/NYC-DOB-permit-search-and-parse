# Building Enrichment Cron Setup - Railway

## Overview
The `step2_enrich_from_pluto.py` script enriches building data from two NYC open data sources:
- **PLUTO (MapPLUTO)**: Corporate ownership, building characteristics, year altered
- **RPAD (Property Tax)**: Current taxpayer, assessed values

## Railway Setup

### 1. Create New Service
1. In Railway dashboard, create a new service in your project
2. Name it: `Building-Enrichment-CRON` or `Step2-PLUTO-RPAD-CRON`

### 2. Configure Service Settings
- **Config File Path**: `railway.step2.json`
- **Environment Variables**:
  - `DATABASE_URL`: (should already be available from Railway PostgreSQL)
  - `BUILDING_BATCH_SIZE`: `500` (processes 500 buildings per run)
  - `API_DELAY`: `0.1` (100ms delay between API calls for rate limiting)

### 3. Set Cron Schedule (Railway UI)
- Go to service settings → Cron Schedule
- Recommended: `0 4 * * *` (daily at 4 AM)
- Alternative: `0 4 * * 0` (weekly on Sunday at 4 AM)

## How It Works

1. **Query**: Finds buildings with missing owner data (current_owner_name IS NULL OR owner_name_rpad IS NULL)
2. **Batch**: Processes up to `BUILDING_BATCH_SIZE` buildings per run
3. **Dual Source**: 
   - Queries PLUTO for corporate owner + building details
   - Queries RPAD for current taxpayer + assessed values
4. **Smart Updates**: Only sets assessment values if available, prefers RPAD over PLUTO for values
5. **Rate Limited**: 100ms delay between API calls to respect rate limits

## Fields Populated

### From PLUTO:
- `current_owner_name` - Corporate entity
- `building_class`, `land_use`
- `residential_units`, `total_units`, `num_floors`
- `building_sqft`, `lot_sqft`
- `year_built`, `year_altered` (renovations)
- `zip_code`
- `assessed_land_value`, `assessed_total_value` (fallback)

### From RPAD:
- `owner_name_rpad` - Current taxpayer (more current than PLUTO)
- `assessed_land_value`, `assessed_total_value` (preferred, more current)

## Monitoring

Check logs in Railway to see:
- Number of buildings processed
- Success rates for PLUTO and RPAD
- Any API errors or failures

## Manual Run (for testing)

```bash
# Local test with small batch
BUILDING_BATCH_SIZE=10 python step2_enrich_from_pluto.py

# Full production batch
BUILDING_BATCH_SIZE=500 python step2_enrich_from_pluto.py
```

## Notes

- Script automatically stops when all buildings are enriched
- Safe to run multiple times (only processes NULL values)
- Both sources are optional - will use whichever is available
- RPAD assessed values preferred as they're more current
