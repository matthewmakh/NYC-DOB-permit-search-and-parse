# 🚀 Automated Enrichment Pipeline Setup

## Overview

The **Master Enrichment Pipeline** (`run_enrichment_pipeline.py`) orchestrates all building data enrichment in a single, reliable execution flow.

## Architecture

### Single Pipeline vs. Multiple Cron Jobs

**✅ CHOSEN: Single Master Pipeline**
- Runs all 4 steps sequentially
- Each step waits for previous to complete
- Smart dependency checking
- Unified error handling and logging
- One cron job to manage

**❌ NOT USED: Separate Cron Jobs**
- Would require 4 Railway services
- Race conditions between steps
- Wasted time with 1-hour gaps
- Complex error handling
- Higher failure risk

## Pipeline Steps

```
run_enrichment_pipeline.py
  │
  ├─▶ Step 1: step1_link_permits_to_buildings.py
  │   └─ Generate full BBL from block/lot
  │      Create building records
  │      [CRITICAL - Must succeed]
  │
  ├─▶ Step 2: step2_enrich_from_pluto.py
  │   └─ Query PLUTO + RPAD APIs
  │      Add owner names, assessed values
  │      [CRITICAL - Must succeed]
  │
  ├─▶ Step 3: step3_enrich_from_acris.py
  │   └─ Query ACRIS API
  │      Add transaction history
  │      [OPTIONAL - Can fail]
  │
  └─▶ Step 4: geocode_permits.py
      └─ Add lat/lng coordinates
         [OPTIONAL - Can fail]
```

## Execution Flow

1. **Step 1 runs first** → If fails, pipeline stops ❌
2. **Step 2 runs next** → If fails, pipeline stops ❌
3. **Step 3 runs** → If fails, continues with warning ⚠️
4. **Step 4 runs** → If fails, continues with warning ⚠️
5. **Summary report** → Shows success/failure status ✅

## Railway Setup

### Create Enrichment Cron Service

1. **Create New Service:**
   - Name: `Building-Enrichment-Pipeline`
   - Type: Cron Job

2. **Connect Repository:**
   - Link to: `matthewmakh/NYC-DOB-permit-search-and-parse`
   - Branch: `main`

3. **Set Config File Path:**
   ```
   railway.enrichment.json
   ```

4. **Environment Variables:**
   The script will automatically use `DATABASE_URL` from Railway PostgreSQL.
   
   Optional performance tuning:
   ```
   BUILDING_BATCH_SIZE=500
   API_DELAY=0.1
   ```

5. **Set Cron Schedule:**
   ```
   0 3 * * *
   ```
   (Runs daily at 3:00 AM)
   
   Or for more frequent updates:
   ```
   0 */6 * * *
   ```
   (Runs every 6 hours)

6. **Deploy:**
   - Railway will auto-deploy from GitHub
   - First run will be manual (click "Run Now")
   - Subsequent runs follow cron schedule

## Local Testing

```bash
# Activate virtual environment
source venv-permit/bin/activate

# Run the full pipeline
python run_enrichment_pipeline.py
```

## Performance Characteristics

### Typical Execution Times

| Step | Time (100 buildings) | Time (500 buildings) |
|------|---------------------|---------------------|
| Step 1 (Link) | ~1 second | ~2 seconds |
| Step 2 (PLUTO+RPAD) | ~20 seconds | ~100 seconds |
| Step 3 (ACRIS) | ~30 seconds | ~150 seconds |
| Step 4 (Geocode) | ~10 seconds | ~50 seconds |
| **Total** | **~1 minute** | **~5 minutes** |

### Batch Processing

- `BUILDING_BATCH_SIZE=500` (default)
- Processes 500 buildings per run
- If you have 2,000 buildings, run 4 times
- Each script automatically skips already-enriched buildings

### API Rate Limiting

- `API_DELAY=0.1` (100ms between calls)
- ~600 API calls per minute
- Respectful to NYC Open Data APIs
- Prevents rate limit errors

## Error Handling

### Critical Failures (Pipeline Stops)
- **Step 1 fails**: No BBL = no enrichment possible
- **Step 2 fails**: Owner data is critical for dashboard

### Non-Critical Failures (Pipeline Continues)
- **Step 3 fails**: Transaction data is nice-to-have
- **Step 4 fails**: Geocoding is optional

### Automatic Recovery
- Each step checks for existing data
- Re-running pipeline only processes missing data
- Safe to run multiple times
- Idempotent operations

## Monitoring

### Railway Logs

Watch for these key messages:

```
✅ PIPELINE COMPLETED SUCCESSFULLY
   Step 1: ✅ SUCCESS (14 buildings created)
   Step 2: ✅ SUCCESS (14 buildings enriched)
   Step 3: ✅ SUCCESS (12 with transactions)
   Step 4: ✅ SUCCESS (14 geocoded)
```

### Error Indicators

```
❌ PIPELINE FAILED - Critical steps did not complete
   Step 1: ❌ FAILED
```

### Warnings (Non-Critical)

```
⚠️  Step 3 failed - continuing to geocoding
```

## Maintenance

### Adding New Steps

Edit `run_enrichment_pipeline.py`:

```python
# Add new step
print_step(5, "Your New Step")
results['step5'] = run_script(
    'your_new_script.py',
    'Description of what it does'
)
```

### Changing Criticality

```python
# Make steps critical or optional
critical_steps = ['step1', 'step2', 'step5']  # Add your step
```

### Adjusting Batch Size

```bash
# In Railway environment variables
BUILDING_BATCH_SIZE=1000  # Larger batches (faster but more memory)
BUILDING_BATCH_SIZE=100   # Smaller batches (slower but safer)
```

## Troubleshooting

### Pipeline Stuck?
- Check Railway logs for specific error
- Verify `DATABASE_URL` is set
- Ensure PostgreSQL service is running

### API Rate Limits?
- Increase `API_DELAY` to 0.2 or 0.5
- Decrease `BUILDING_BATCH_SIZE`

### Out of Memory?
- Decrease `BUILDING_BATCH_SIZE` to 250 or 100
- Railway free tier has memory limits

## Comparison: Before vs. After

### ❌ OLD WAY (Separate Cron Jobs)
```
Service 1: step1 (runs at 1:00 AM)
Service 2: step2 (runs at 2:00 AM) ← 1 hour gap
Service 3: step3 (runs at 3:00 AM) ← 2 hours total
Service 4: geocode (runs at 4:00 AM) ← 3 hours total

Total time: 3+ hours
Services: 4 Railway services
Reliability: Low (race conditions)
Complexity: High
```

### ✅ NEW WAY (Master Pipeline)
```
One Service: run_enrichment_pipeline.py (runs at 3:00 AM)
  → Step 1: ~2 seconds
  → Step 2: ~100 seconds
  → Step 3: ~150 seconds
  → Step 4: ~50 seconds

Total time: ~5 minutes
Services: 1 Railway service
Reliability: High (sequential)
Complexity: Low
```

## Cost Efficiency

- **Old way**: 4 Railway services = 4x cost
- **New way**: 1 Railway service = 1x cost
- **Execution time**: 5 minutes vs 3+ hours
- **API calls**: Same total, better reliability

## Conclusion

The master pipeline approach is:
- ✅ **Faster**: Minutes instead of hours
- ✅ **Cheaper**: 1 service instead of 4
- ✅ **More reliable**: Sequential execution
- ✅ **Easier to maintain**: Single cron job
- ✅ **Better logging**: Unified view
- ✅ **Safer**: Dependency checking

**Recommended cron schedule:** `0 3 * * *` (daily at 3 AM)
