# Quick Start Guide - Updated System

## 🚀 Running the Complete Pipeline

### Step 0: Apply Database Migration (One Time Only)
```bash
# For existing databases - adds buildings tables and indexes
python migrate_add_buildings.py
```

### Step 1: Scrape Permits
```bash
# Run main scraper (will fallback to remote if rate limited)
python add_permit_contacts.py
```

**What it does:**
- Scrapes DOB permit list pages
- Extracts 16 fields including Block, Lot, BBL info
- Stores contacts (name, phone) for each permit
- Automatically switches to remote scraper if rate limited

**New improvements:**
- ✅ Better BBL extraction error logging
- ✅ Consistent rate limit handling (3 hits)
- ✅ Date conversion consistency

---

### Step 2: Link Permits to Buildings
```bash
python step1_link_permits_to_buildings.py
```

**What it does:**
- Derives BBL from Block + Lot + Borough (from permit number)
- Creates building records for unique BBLs
- Links permits back to buildings

**New improvements:**
- ✅ BBL validation (checks format, borough code)
- ✅ Detailed warnings for invalid data
- ✅ No hardcoded credentials

**Output example:**
```
Buildings created: 5
Permits updated: 5
Total unique buildings: 5
```

---

### Step 3: Enrich from PLUTO (Owner Data)
```bash
python step2_enrich_from_pluto.py
```

**What it does:**
- Queries NYC PLUTO API for building details by BBL
- Extracts: Owner name, address, units, sqft, year built
- Updates buildings table

**New improvements:**
- ✅ 0.5 second rate limit between API calls
- ✅ Progress counter ([1/5], [2/5], etc.)
- ✅ No hardcoded credentials

**Output example:**
```
[1/5] BBL 2033100072 (555 BROOK AVE)...
   ✅ Owner: SNL XXIII LLC
      Units: 26, Built: 2024
```

---

### Step 4: Enrich from ACRIS (Transaction Data)
```bash
python step3_enrich_from_acris.py
```

**What it does:**
- Queries NYC ACRIS API for property transactions by BBL
- Extracts: Purchase date, price, mortgage amount
- Updates buildings table

**New improvements:**
- ✅ 0.5 second rate limit between API calls
- ✅ Progress counter
- ✅ No hardcoded credentials

**Note:** ACRIS doesn't have data for new construction buildings (built 2021+)

**Output example:**
```
[1/5] BBL 3050080064 (123 MAIN ST)...
   ❌ No ACRIS transaction data found (new construction)
```

---

## 📊 Check Your Data

### See all buildings:
```sql
SELECT bbl, address, current_owner_name, year_built 
FROM buildings 
ORDER BY id;
```

### See permits linked to buildings:
```sql
SELECT p.permit_no, p.address, p.bbl, b.current_owner_name
FROM permits p
LEFT JOIN buildings b ON p.bbl = b.bbl
WHERE p.bbl IS NOT NULL
LIMIT 10;
```

### Check enrichment status:
```sql
-- PLUTO enrichment
SELECT COUNT(*) FROM buildings WHERE current_owner_name IS NOT NULL;

-- ACRIS enrichment  
SELECT COUNT(*) FROM buildings WHERE purchase_date IS NOT NULL;
```

---

## 🔧 Configuration

### Required Environment Variables (.env):
```bash
# Database
DATABASE_URL=postgresql://user:pass@host:port/dbname

# Or individual components:
DB_TYPE=postgresql
DB_HOST=maglev.proxy.rlwy.net
DB_PORT=26571
DB_USER=postgres
DB_PASSWORD=your_password
DB_NAME=railway

# Remote Webdriver (for rate limit fallback)
REMOTE_WEBDRIVER_URL=http://your-selenium-grid:4444/wd/hub

# Proxy (optional)
PROXY_HOST=proxy.example.com
PROXY_USER=username
PROXY_PASS=password
```

### Database Config (in database):
```sql
-- Control scraper behavior
SELECT * FROM permit_search_config ORDER BY created_at DESC LIMIT 1;

-- Update max permits per run
UPDATE permit_search_config 
SET max_successful_links = 10 
WHERE id = (SELECT id FROM permit_search_config ORDER BY created_at DESC LIMIT 1);
```

---

## 🐛 Troubleshooting

### Problem: Migration says tables already exist
**Solution:** This is normal. The migration is safe to run multiple times.

### Problem: step1 creates no buildings
**Check:** Do permits have block and lot data?
```sql
SELECT COUNT(*) FROM permits WHERE block IS NOT NULL AND lot IS NOT NULL;
```

### Problem: step2 finds no PLUTO data
**Check:** Are BBLs valid (10 digits)?
```sql
SELECT bbl, LENGTH(bbl) FROM buildings WHERE LENGTH(bbl) != 10;
```

### Problem: Rate limited too quickly
**Solution:** Increase MAX_RATE_LIMITS in scrapers (currently set to 3)

### Problem: ACRIS returns no data
**Common cause:** Building is new construction (no prior sales in ACRIS)
**Check:** `year_built` field - if 2021+, ACRIS likely has no data

---

## 📈 Next Features (Steps 4-7)

### Step 4: Zillow/Redfin Valuations
- Scrape estimated property values
- Update `buildings.estimated_value`

### Step 5: Calculate Building Metrics
- Aggregate permit spend per building
- Update `buildings.total_permit_spend`
- Count permits: `buildings.permit_count`

### Step 6: Calculate Scores
- Affordability score (value vs income)
- Renovation need score (age, permit activity)
- Contact quality score (phone availability)
- Overall priority score (weighted average)

### Step 7: Skip Tracing
- Find owner contact details (phone, email)
- Store in `owner_contacts` table
- Link to buildings

---

## 🎯 Common Workflows

### Fresh start:
```bash
python migrate_add_buildings.py       # Set up tables
python add_permit_contacts.py         # Scrape permits
python step1_link_permits_to_buildings.py  # Create buildings
python step2_enrich_from_pluto.py     # Add owner data
python step3_enrich_from_acris.py     # Add transaction data
```

### Daily update:
```bash
python add_permit_contacts.py         # Get new permits
python step1_link_permits_to_buildings.py  # Link new ones
python step2_enrich_from_pluto.py     # Enrich new buildings
```

### Re-enrich existing buildings:
```sql
-- Clear PLUTO data
UPDATE buildings SET current_owner_name = NULL;

-- Then run step2 again
python step2_enrich_from_pluto.py
```

---

## 📝 Files Reference

| File | Purpose |
|------|---------|
| `postgres_schema.sql` | Complete database schema |
| `migrate_add_buildings.py` | One-time migration for existing DBs |
| `add_permit_contacts.py` | Main permit scraper (local) |
| `remote_add_permit_contacts.py` | Fallback scraper (remote) |
| `permit_scraper.py` | Permit list scraper |
| `step1_link_permits_to_buildings.py` | Create buildings from permits |
| `step2_enrich_from_pluto.py` | Add owner data from PLUTO |
| `step3_enrich_from_acris.py` | Add transaction data from ACRIS |

---

## ✅ What Got Fixed Today

- ✅ Added buildings table to schema
- ✅ Added bbl column to permits
- ✅ Fixed date conversion in remote scraper
- ✅ Removed hardcoded credentials
- ✅ Added BBL validation
- ✅ Added API rate limiting
- ✅ Better error logging
- ✅ Consistent rate limit handling
- ✅ Removed junk data insertions
- ✅ Added performance indexes

See `CODE_REVIEW_SUMMARY.md` for full details.
