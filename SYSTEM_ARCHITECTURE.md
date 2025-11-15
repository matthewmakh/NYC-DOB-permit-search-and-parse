# 🏗️ NYC DOB Permit Scraper - Complete System Architecture

**Last Updated:** November 15, 2025

## 📋 System Overview

A complete pipeline for scraping NYC Department of Buildings permit data, enriching it with property intelligence, and displaying it in an interactive dashboard.

## 🔄 Complete Automation Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                      DATA COLLECTION                             │
├─────────────────────────────────────────────────────────────────┤
│ 1. permit_scraper.py                                            │
│    └─ Scrapes DOB BIS for basic permit info                     │
│    └─ Saves: permit_no, address, owner, filing_rep, etc.        │
│                                                                  │
│ 2. add_permit_contacts.py                                       │
│    └─ Clicks into EACH permit detail page                       │
│    └─ Extracts: contacts, phone, email, block, lot, BIN         │
│    └─ This is where we get Block & Lot! ⭐                      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                  BUILDING ENRICHMENT PIPELINE                    │
│              (run_enrichment_pipeline.py)                       │
├─────────────────────────────────────────────────────────────────┤
│ Step 1: step1_link_permits_to_buildings.py                      │
│    └─ Takes block + lot from permits                            │
│    └─ Derives borough from permit number (1st digit)            │
│    └─ Constructs full 10-digit BBL: Borough(1)+Block(5)+Lot(4)  │
│    └─ Creates building records                                  │
│    └─ Links permits to buildings via BBL                        │
│                                                                  │
│ Step 2: step2_enrich_from_pluto.py                              │
│    └─ Queries NYC PLUTO API (MapPLUTO)                          │
│       • Corporate owner (current_owner_name)                    │
│       • Building characteristics (floors, units, sqft)          │
│       • Year built, year altered                                │
│    └─ Queries NYC RPAD API (Property Tax)                       │
│       • Current taxpayer (owner_name_rpad) - often different!   │
│       • Assessed values (land + total) - more current           │
│    └─ Smart dual-source: prefers RPAD values over PLUTO         │
│                                                                  │
│ Step 3: step3_enrich_from_acris.py                              │
│    └─ Queries NYC ACRIS API (City Register)                     │
│    └─ Extracts transaction history:                             │
│       • Last sale date & price                                  │
│       • Mortgage amounts                                        │
│    └─ Optional: continues if fails                              │
│                                                                  │
│ Step 4: geocode_permits.py                                      │
│    └─ Adds lat/lng to permits                                   │
│    └─ Uses NYC Geoclient API (free, accurate)                   │
│    └─ Falls back to OpenStreetMap if needed                     │
│    └─ Optional: continues if fails                              │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                      DASHBOARD DISPLAY                           │
├─────────────────────────────────────────────────────────────────┤
│ dashboard_html/app.py                                           │
│    └─ Flask API serving enriched data                           │
│    └─ Deployed at: leads.installersny.com                       │
│    └─ Two views:                                                │
│       • Permit View: All permits with filters                   │
│       • Building View: Unique buildings sorted by value         │
│                                                                  │
│ Features:                                                        │
│    ✅ Dual owner display (Corporate + Taxpayer)                 │
│    ✅ Assessed value badges                                     │
│    ✅ Recent renovation indicators (🔥)                          │
│    ✅ Interactive filters & search                              │
│    ✅ Expandable Smart Insights section                         │
└─────────────────────────────────────────────────────────────────┘
```

## 🚀 Railway Services

### Active Services

| Service Name | Type | Config File | Schedule | Purpose |
|-------------|------|-------------|----------|---------|
| **NYC-DOB-permit-search-and-parse** | Web App | `dashboard_html/railway.json` | Always On | Main dashboard at leads.installersny.com |
| **Postgres** | Database | - | Always On | PostgreSQL database (Railway managed) |
| **Building-Enrichment-Pipeline** | Cron | `railway.enrichment.json` | `0 3 * * *` | Master pipeline (Step 1-4) |
| **Geocode-Properties-CRON** | Cron | `railway.geocode.json` | `0 3 * * 0` | Standalone geocoding (optional) |
| **Mobile-Number-Check-CRON** | Cron | `railway.cron.json` | - | Phone validation |

### Recommended Setup

**Option 1: Integrated (Recommended)**
- Use `Building-Enrichment-Pipeline` (runs all 4 steps daily)
- Disable or remove standalone `Geocode-Properties-CRON`
- Simpler, faster, more reliable

**Option 2: Separate Services**
- Keep all services as is
- `Geocode-Properties-CRON` runs independently
- No conflicts (both skip already-processed data)

## 📊 Database Schema

### permits table
```sql
id, permit_no, address, owner, filing_rep, work_type, 
block, lot, bbl,  -- Added by add_permit_contacts + step1
contacts, phone_numbers, email,  -- Added by add_permit_contacts
latitude, longitude  -- Added by geocode_permits
```

### buildings table
```sql
id, bbl, address, block, lot, bin,
current_owner_name,        -- Corporate owner (PLUTO)
owner_name_rpad,           -- Current taxpayer (RPAD)
assessed_land_value,       -- RPAD (preferred) or PLUTO
assessed_total_value,      -- RPAD (preferred) or PLUTO
year_built, year_altered,  -- PLUTO
building_class, land_use,  -- PLUTO
residential_units, total_units, num_floors,  -- PLUTO
building_sqft, lot_sqft,   -- PLUTO
purchase_date, purchase_price, mortgage_amount  -- ACRIS
```

## 🔧 Environment Variables

### Required for All Services
```bash
DATABASE_URL=postgresql://user:pass@host:port/db
```

### Enrichment Pipeline
```bash
BUILDING_BATCH_SIZE=500    # Buildings per run (default: 500)
API_DELAY=0.1              # Seconds between API calls (default: 0.1)
```

### Geocoding
```bash
NYC_GEOCLIENT_APP_ID=your_app_id
NYC_GEOCLIENT_APP_KEY=your_app_key
GEOCODE_BATCH_SIZE=10      # Permits per run (default: 10)
GEOCODE_DELAY=0.01         # Seconds between calls (default: 0.01)
```

## 📖 Documentation Files

| File | Purpose | Status |
|------|---------|--------|
| `ENRICHMENT_PIPELINE_GUIDE.md` | Master pipeline setup & usage | ✅ Current |
| `BUILDING_ENRICHMENT_RAILWAY.md` | Old step2 setup | ⚠️ Deprecated |
| `GEOCODING_README.md` | Standalone geocoding | ✅ Current (optional) |
| `DEPLOYMENT_READY.md` | Dashboard deployment | ✅ Current |
| `RAILWAY_DEPLOY_GUIDE.md` | General Railway setup | ✅ Current |

## ⚡ Performance Metrics

### Enrichment Pipeline (500 buildings)
- **Step 1** (BBL generation): ~2 seconds
- **Step 2** (PLUTO+RPAD): ~100 seconds (with rate limiting)
- **Step 3** (ACRIS): ~150 seconds (optional, may fail)
- **Step 4** (Geocoding): ~50 seconds
- **Total**: ~5 minutes

### Data Quality
- **BBL Success**: 100% (derived from block+lot)
- **Owner Data**: ~85% (PLUTO/RPAD availability)
- **Geocoding**: ~100% (NYC Geoclient API)
- **ACRIS**: ~30% (many properties don't have public records)

## 🛠️ Local Development

```bash
# Setup
cd /path/to/DOB_Permit_Scraper_Streamlit
source venv-permit/bin/activate

# Run individual scripts
python permit_scraper.py
python add_permit_contacts.py

# Run full enrichment pipeline
python run_enrichment_pipeline.py

# Run dashboard locally
cd dashboard_html
python app.py
# Visit: http://localhost:5001
```

## 🔍 Monitoring & Logs

### Railway Dashboard
1. Go to Railway → Your Project
2. Click on service (e.g., Building-Enrichment-Pipeline)
3. Go to **Deployments** tab
4. Click latest deployment → **View Logs**

### Look for Success Indicators
```
✅ PIPELINE COMPLETED SUCCESSFULLY
   Step 1: ✅ SUCCESS (X buildings created)
   Step 2: ✅ SUCCESS (X buildings enriched)
   Step 3: ✅ SUCCESS (X with transactions)
   Step 4: ✅ SUCCESS (X geocoded)
```

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Step 1 fails | No permits with block/lot | Run `add_permit_contacts.py` first |
| Step 2 slow | Rate limiting | Normal - respects API limits |
| Step 3 400 errors | BBL not in ACRIS | Expected - not all properties recorded |
| Step 4 no API key | Missing credentials | Add NYC_GEOCLIENT_APP_ID/KEY |

## 🎯 Data Flow Summary

```
NYC DOB Website
    ↓ (permit_scraper.py)
Permits Table (basic info)
    ↓ (add_permit_contacts.py)
Permits Table (+ contacts, block, lot)
    ↓ (run_enrichment_pipeline.py)
    ├─ Step 1: Permits + Buildings Tables (+ BBL)
    ├─ Step 2: Buildings Table (+ owners, values, characteristics)
    ├─ Step 3: Buildings Table (+ transaction history)
    └─ Step 4: Permits Table (+ lat/lng)
    ↓
Dashboard (leads.installersny.com)
    ├─ Permit View (all permits with filters)
    └─ Building View (unique buildings by value)
```

## 🔐 API Keys & Credentials

### Free APIs (No Key Required)
- ✅ NYC PLUTO (MapPLUTO) - Open Data
- ✅ NYC RPAD (Property Tax) - Open Data
- ✅ NYC ACRIS (City Register) - Open Data
- ✅ OpenStreetMap Nominatim - Fallback geocoding

### Free APIs (Key Required)
- 🔑 NYC Geoclient API - Register at https://developer.cityofnewyork.us/

### Rate Limits
- PLUTO/RPAD/ACRIS: ~1,000 requests/hour (we use 0.1s delay = 600/hour)
- NYC Geoclient: 2,500 requests/day
- Nominatim: 1 request/second

## 📚 Additional Resources

- [NYC Open Data Portal](https://opendata.cityofnewyork.us/)
- [NYC Geoclient API Docs](https://developer.cityofnewyork.us/api/geoclient-api)
- [DOB BIS Portal](https://a810-bisweb.nyc.gov/bisweb/bispi00.jsp)
- [Railway Documentation](https://docs.railway.app/)

## 🚨 Important Notes

1. **BBL is derived locally** - No external API needed for BBL
2. **Block + Lot come from permit detail pages** - Not the initial scrape
3. **Dual owner names are intentional** - Corporate vs. Current Taxpayer
4. **ACRIS failures are expected** - Not all properties have public records
5. **Master pipeline is idempotent** - Safe to run multiple times
6. **Rate limiting is crucial** - Don't remove API delays

## 🎉 Success Criteria

Your system is working correctly if:
- ✅ Permits have block, lot, and BBL
- ✅ Buildings show both owner names
- ✅ Assessed values display in dashboard
- ✅ Recent renovations show 🔥 badge
- ✅ Geocoding achieves ~100% success
- ✅ Pipeline completes in ~5 minutes
- ✅ Dashboard loads without errors

---

**Need Help?** Check the individual documentation files listed above or review Railway logs.
