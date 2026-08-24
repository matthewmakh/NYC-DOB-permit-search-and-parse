# 🚀 NYC DOB Permit Scraper - Complete Guide

**Last Updated:** November 15, 2025  
**Repository:** https://github.com/matthewmakh/NYC-DOB-permit-search-and-parse  
**Dashboard:** https://leads.installersny.com

---

## 📋 Table of Contents

1. [System Overview](#system-overview)
2. [Data Pipeline Flow](#data-pipeline-flow)
3. [Local Development](#local-development)
4. [Railway Deployment](#railway-deployment)
5. [Data Quality & Monitoring](#data-quality--monitoring)
6. [Troubleshooting](#troubleshooting)

---

## 🎯 System Overview

### What This System Does

Scrapes NYC DOB permit data, enriches it with property intelligence, and displays it in an interactive dashboard.

The current multi-source importer covers legacy BIS permits, DOB NOW Build job
filings, DOB NOW approved permits, DOB NOW Electrical applications and scope
details, DOB NOW Elevator applications, and City Record procurement notices.
Filing and permit stages are retained as raw records and consolidated into one
project for sales review. City Record notices remain standalone pre-permit
signals until an evidence-backed project graph can connect them.

### Key Features

- ✅ **Permit Scraping**: Gets permit data from NYC DOB BIS website
- ✅ **Contact Extraction**: Extracts contacts, phone numbers, emails, Block & Lot
- ✅ **BBL Generation**: Derives 10-digit BBL from Block + Lot + Borough
- ✅ **Dual Owner Data**: Corporate owner (PLUTO) + Current taxpayer (RPAD)
- ✅ **Property Intelligence**: Assessed values, building characteristics, renovations
- ✅ **Transaction History**: Sale dates, prices, mortgages (ACRIS)
- ✅ **Geocoding**: Latitude/longitude for mapping
- ✅ **Interactive Dashboard**: Two views (Permit & Building) with filters

### Technology Stack

- **Backend**: Python, Flask, Selenium (for scraping)
- **Database**: PostgreSQL (Railway)
- **Frontend**: HTML, JavaScript, CSS
- **Deployment**: Railway (dashboard + cron jobs)
- **Data Sources**: NYC DOB BIS, PLUTO, RPAD, ACRIS, Geoclient API

---

## 🔄 Data Pipeline Flow

### Complete Automation Pipeline

```
1. permit_scraper.py
   └─ Scrapes DOB BIS for basic permit info
   └─ Saves: permit_no, address, owner, filing_rep, work_type

2. add_permit_contacts.py
   └─ Clicks into EACH permit detail page
   └─ Extracts: contacts, phones, emails, block, lot, BIN

3. run_enrichment_pipeline.py (Master Pipeline)
   │
   ├─ Step 1: step1_link_permits_to_buildings.py
   │  └─ Derives full 10-digit BBL: Borough(1)+Block(5)+Lot(4)
   │  └─ Creates building records
   │  └─ Links permits to buildings
   │
   ├─ Step 2: step2_enrich_from_pluto.py
   │  └─ PLUTO: Corporate owner, building details, year altered
   │  └─ RPAD: Current taxpayer, assessed values
   │
   ├─ Step 3: step3_enrich_from_acris.py
   │  └─ ACRIS: Sale dates, prices, mortgages
   │
   └─ Step 4: geocode_permits.py
      └─ NYC Geoclient API: Latitude/longitude

4. Dashboard (leads.installersny.com)
   └─ Displays enriched data in two views
```

### Important: BBL Generation

**BBL is NOT from an external API** - we derive it locally:
- Block & Lot come from `add_permit_contacts.py` (scraped from permit detail pages)
- Borough comes from permit number (first digit: 1=Manhattan, 2=Bronx, 3=Brooklyn, 4=Queens, 5=Staten Island)
- Combined into 10-digit BBL: `3050080064` = Borough 3, Block 05008, Lot 0064

---

## 💻 Local Development

### Initial Setup

```bash
# Clone repository
git clone https://github.com/matthewmakh/NYC-DOB-permit-search-and-parse.git
cd NYC-DOB-permit-search-and-parse

# Create virtual environment
python3 -m venv venv-permit
source venv-permit/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env with your database credentials
```

### Environment Variables (.env)

```bash
# Database (Railway PostgreSQL)
DB_HOST=maglev.proxy.rlwy.net
DB_PORT=26571
DB_USER=postgres
DB_PASSWORD=your_password
DB_NAME=railway

# NYC Geoclient API (free - get at developer.cityofnewyork.us)
NYC_GEOCLIENT_APP_ID=your_app_id
NYC_GEOCLIENT_APP_KEY=your_app_key

# Optional Performance Tuning
BUILDING_BATCH_SIZE=500  # Buildings per enrichment run
API_DELAY=0.1            # Seconds between API calls
GEOCODE_BATCH_SIZE=10    # Permits per geocoding run

# Optional sales automations
CRM_WEBHOOK_URL=https://your-automation-endpoint.example/crm
CRM_WEBHOOK_BEARER_TOKEN=optional-secret
WATCHLIST_DIGEST_WEBHOOK_URL=https://your-automation-endpoint.example/digest
WATCHLIST_DIGEST_WEBHOOK_BEARER_TOKEN=optional-secret
```

### Running Scripts Locally

```bash
# Activate environment
source venv-permit/bin/activate

# Run the incremental permit scraper
python permit_scraper_api.py --days 14

# Run only DOB NOW Build + Electrical sources
python permit_scraper_api.py --dob-now-only --days 14

# Run the same source set used by the production permit-intelligence cron
python permit_scraper_api.py --sources dob_now_filings dob_now_approved dob_now_electrical dob_now_electrical_details dob_now_elevator city_record --days 14

# Production wrapper: sync all active feeds, then store watchlist digests.
# It deliberately does not send data to an external webhook.
python run_permit_intelligence_cron.py

# One-time repair after changing DOB NOW permit identity/owner mappings.
# This is idempotent and can be resumed by rerunning the same command.
python permit_scraper_api.py --dob-now-only --start 2016-01-01 --end YYYY-MM-DD

# Bounded, restartable project/signal backfill; Electrical and Elevator
# naturally begin at their 2017 launches. Electrical Details follows its
# dated parent applications because the child feed has no date column.
python backfill_project_intelligence.py --start 2016-01-01 --end YYYY-MM-DD --window-days 31

# Consolidate rows already in PostgreSQL without re-fetching APIs. The cursor
# form resumes after the last project key printed by a prior run.
python backfill_project_intelligence.py --refresh-existing-only --project-batch-size 1000
python backfill_project_intelligence.py --refresh-existing-only --project-batch-size 1000 --after-project-key DOBNOW:M01241021

# Smaller transactions are more resilient through Railway's public TCP proxy.
# A run inside Railway can use the faster private DATABASE_URL.
PERMIT_BATCH_SIZE=1000 python backfill_project_intelligence.py --start 2025-01-01 --end YYYY-MM-DD --window-days 31

# Extract contacts (after scraper)
python add_permit_contacts.py

# Run full enrichment pipeline
python run_enrichment_pipeline.py

# Generate/store the salesperson watchlist digest on demand
python generate_watchlist_digests.py

# Run dashboard locally
cd dashboard_html
python app.py
# Visit: http://localhost:5001
```

Before a full-history run, compare `pg_database_size(current_database())` with
the Postgres volume limit. The 2016-present feeds contain roughly 944k Build
filings, 988k approved permits, 585k electrical applications, 1.25m electrical
detail rows, 71k elevator applications, and 56k filtered City Record notices.
Expand the volume or backfill recent years first when headroom is tight.

### Testing Individual Steps

```bash
# Test just BBL generation
python step1_link_permits_to_buildings.py

# Test just PLUTO+RPAD enrichment
BUILDING_BATCH_SIZE=10 python step2_enrich_from_pluto.py

# Test just ACRIS
python step3_enrich_from_acris.py

# Test just geocoding
python geocode_permits.py
```

---

## 🚂 Railway Deployment

### Current Railway Services

| Service | Type | Config File | Schedule | Purpose |
|---------|------|-------------|----------|---------|
| **NYC-DOB-permit-search-and-parse** | Web App | `dashboard_html/railway.json` | Always On | Dashboard (leads.installersny.com) |
| **Postgres** | Database | - | Always On | PostgreSQL database |
| **Building-Enrichment-Pipeline** | Cron | `railway.enrichment.json` | Daily 3 AM | Master pipeline (all 4 steps) |

### Setup New Enrichment Pipeline Service

#### Option A: Replace Existing Geocode Service (Recommended)

1. Go to Railway → `Geocode-Properties-CRON` service
2. **Settings** → **Config File Path**
3. Change from `railway.geocode.json` to `railway.enrichment.json`
4. Done! Service now runs full pipeline instead of just geocoding

#### Option B: Create New Service

1. Railway → Your Project → **"+ Create"**
2. Select **"GitHub Repo"** → `NYC-DOB-permit-search-and-parse`
3. Click on service → **Settings**
4. **Service Name**: `Building-Enrichment-Pipeline`
5. **Config File Path**: `railway.enrichment.json`
6. **Variables**: Add reference to `Postgres.DATABASE_URL`
7. **Cron Schedule**: `0 3 * * *` (daily at 3 AM UTC)

### Environment Variables (Railway)

Railway automatically provides:
- `DATABASE_URL` (from PostgreSQL service)

Optional performance tuning:
- `BUILDING_BATCH_SIZE=500`
- `API_DELAY=0.1`
- `NYC_GEOCLIENT_APP_ID` (for geocoding)
- `NYC_GEOCLIENT_APP_KEY` (for geocoding)

### Monitoring Deployments

1. Go to service → **Deployments** tab
2. Click on deployment → **View Logs**
3. Look for:
```
✅ PIPELINE COMPLETED SUCCESSFULLY
   Step 1: ✅ SUCCESS
   Step 2: ✅ SUCCESS
   Step 3: ✅ SUCCESS
   Step 4: ✅ SUCCESS
```

### Manual Test Run

1. **Deployments** tab
2. Click **⋮** (three dots) → **"Restart"**
3. **View Logs** to watch execution

---

## 📊 Data Quality & Monitoring

### Expected Success Rates

| Data Source | Expected | Why Not 100%? |
|-------------|----------|---------------|
| **BBL Generation** | 100% | Derived locally from block+lot |
| **PLUTO** | 90-95% | New construction, data lag |
| **RPAD** | 60-75% | Not all properties taxed (govt, religious) |
| **ACRIS** | 30-50% | Many buildings never sold publicly |
| **Geocoding** | 95-100% | Address format variations |

### Automatic Retry Logic

**All steps automatically retry failed records on next run:**

```sql
-- Step 1: Retries permits without BBL
WHERE block IS NOT NULL AND lot IS NOT NULL AND bbl IS NULL

-- Step 2: Retries buildings missing owner data
WHERE (current_owner_name IS NULL OR owner_name_rpad IS NULL)

-- Step 3: Retries buildings without transaction data
WHERE purchase_date IS NULL

-- Step 4: Retries permits without coordinates
WHERE latitude IS NULL
```

**This means:** Every failed enrichment automatically retries on the next scheduled run. The system is self-healing!

### Check Data Status

```bash
source venv-permit/bin/activate
python -c "
import psycopg2, os
from dotenv import load_dotenv
load_dotenv()

conn = psycopg2.connect(f'postgresql://{os.getenv(\"DB_USER\")}:{os.getenv(\"DB_PASSWORD\")}@{os.getenv(\"DB_HOST\")}:{os.getenv(\"DB_PORT\")}/{os.getenv(\"DB_NAME\")}')
cur = conn.cursor()

print('📊 Enrichment Status:')
cur.execute('SELECT COUNT(*) FROM buildings')
print(f'Total buildings: {cur.fetchone()[0]}')

cur.execute('SELECT COUNT(*) FROM buildings WHERE current_owner_name IS NOT NULL')
print(f'With PLUTO data: {cur.fetchone()[0]}')

cur.execute('SELECT COUNT(*) FROM buildings WHERE owner_name_rpad IS NOT NULL')
print(f'With RPAD data: {cur.fetchone()[0]}')

cur.execute('SELECT COUNT(*) FROM buildings WHERE purchase_date IS NOT NULL')
print(f'With ACRIS data: {cur.fetchone()[0]}')

cur.execute('SELECT COUNT(*) FROM permits WHERE latitude IS NOT NULL')
geocoded = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM permits')
total = cur.fetchone()[0]
print(f'Geocoded: {geocoded}/{total} ({100*geocoded/total:.1f}%)')

conn.close()
"
```

### Pipeline Execution Time

- **Small dataset (100 buildings):** ~1 minute
- **Medium dataset (500 buildings):** ~5 minutes
- **Large dataset (1000+ buildings):** ~10-15 minutes

Each step processes in batches and respects API rate limits.

---

## 🐛 Troubleshooting

### Common Issues

#### "BBL is NULL for many permits"
**Cause:** Need to run `add_permit_contacts.py` first to get block/lot  
**Fix:** Ensure contact extraction runs before enrichment pipeline

#### "RPAD returns no data"
**Cause:** Not all buildings are in RPAD (normal)  
**Fix:** Expected - 60-75% success is normal. System will retry automatically.

#### "ACRIS returns 400 errors"
**Cause:** Was a bug (fixed in latest version)  
**Fix:** Pull latest code from GitHub, uses borough/block/lot fields now

#### "Pipeline takes too long"
**Cause:** Rate limiting delays  
**Fix:** Normal behavior. API_DELAY=0.1 prevents rate limit errors.

#### "Geocoding slow progress"
**Cause:** Batch size set to 10 permits per run  
**Fix:** Increase `GEOCODE_BATCH_SIZE` or let it run daily to gradually complete

#### "Dashboard not showing new data"
**Cause:** Dashboard caches data or hasn't redeployed  
**Fix:** Railway auto-deploys from GitHub. Check deployment logs.

### Railway Issues

#### Service won't start
- Verify `railway.enrichment.json` exists in repo root
- Check config file path spelling in Railway settings
- Ensure DATABASE_URL is connected

#### Module not found errors
- Check `requirements.txt` includes all dependencies
- Railway auto-installs from requirements.txt
- Try manual redeploy

#### Cron not running
- Verify schedule syntax: `0 3 * * *`
- Check service isn't paused
- Look for "Next run" countdown in Deployments

### Database Issues

#### Connection errors
- Verify DATABASE_URL in Railway variables
- Check PostgreSQL service is running
- Test connection locally with credentials

#### Missing columns
- Run migration scripts in order:
  - `migrate_add_buildings.py`
  - `migrate_add_dual_owner_fields.py`
  - `migrate_add_building_intelligence.py`
  - `migrate_add_freshness_and_jobs.py`

The master `run_enrichment_pipeline.py` now runs the last additive migration
itself before enrichment. Its versioned ACRIS refresh rebuilds existing
property mortgage/ownership summaries after logic changes.

---

## 📚 Database Schema Reference

### permits table (main fields)
```sql
id, permit_no, address, owner, filing_rep, work_type,
block, lot, bbl,  -- Added by add_permit_contacts + step1
contacts, phone_numbers, email,  -- Added by add_permit_contacts
latitude, longitude,  -- Added by geocode_permits
use, stories, total_units, filing_date, status
```

### buildings table (main fields)
```sql
id, bbl, address, block, lot, bin,
current_owner_name,        -- Corporate owner (PLUTO)
owner_name_rpad,           -- Current taxpayer (RPAD)
assessed_land_value,       -- RPAD or PLUTO
assessed_total_value,      -- RPAD or PLUTO
year_built, year_altered,  -- PLUTO
building_class, land_use,  -- PLUTO
residential_units, total_units, num_floors,  -- PLUTO
building_sqft, lot_sqft,   -- PLUTO
purchase_date, purchase_price, mortgage_amount  -- ACRIS
```

---

## 🎯 Quick Reference

### Cron Schedules
- `0 3 * * *` - Daily at 3 AM
- `0 */6 * * *` - Every 6 hours
- `0 3 * * 0` - Weekly (Sunday 3 AM)
- `*/30 * * * *` - Every 30 minutes

### API Endpoints
- PLUTO: `https://data.cityofnewyork.us/resource/64uk-42ks.json`
- RPAD: `https://data.cityofnewyork.us/resource/yjxr-fw8i.json`
- ACRIS: `https://data.cityofnewyork.us/resource/8h5j-fqxa.json`
- Geoclient: `https://api.nyc.gov/geo/geoclient/v2`

### Free API Keys Needed
- NYC Geoclient: https://developer.cityofnewyork.us/ (free)
- PLUTO/RPAD/ACRIS: No key needed (NYC Open Data)

### File Structure
```
/
├── run_enrichment_pipeline.py        # Master orchestrator
├── step1_link_permits_to_buildings.py
├── step2_enrich_from_pluto.py
├── step3_enrich_from_acris.py
├── geocode_permits.py
├── permit_scraper.py
├── add_permit_contacts.py
├── railway.enrichment.json           # Cron config
├── requirements.txt
├── .env                               # Local credentials
└── dashboard_html/
    ├── app.py                         # Flask backend
    ├── templates/
    │   ├── index.html
    │   └── permit_detail.html
    └── static/
        ├── js/app.js
        └── css/styles.css
```

---

## 🎉 Success Checklist

Your system is working correctly if:

- [ ] Permits have block, lot, and BBL
- [ ] Buildings show both owner names (PLUTO + RPAD)
- [ ] Assessed values display in dashboard
- [ ] Recent renovations show 🔥 badge (year_altered ≤ 5 years)
- [ ] Some buildings have ACRIS transaction data (~30-50%)
- [ ] Geocoding achieves ~100% success over time
- [ ] Pipeline completes in ~5 minutes
- [ ] Dashboard loads at leads.installersny.com
- [ ] Both views work (Permit View + Building View)
- [ ] Filters and search function properly

---

**Questions or Issues?**  
Check Railway logs first, then review troubleshooting section above.

**Repository:** https://github.com/matthewmakh/NYC-DOB-permit-search-and-parse  
**Latest Commit:** d09181e (Master enrichment pipeline + ACRIS fix)
