# 🎯 NEXT ACTIONS - Priority Order

## 🔴 IMMEDIATE (Do Now)

### 1. Run Database Migration
```bash
python migrate_add_buildings.py
```
**Purpose:** Adds buildings tables and BBL column to your Railway database  
**Time:** ~30 seconds  
**Safe:** Can run multiple times, checks existence first

---

### 2. Test Building Pipeline with Existing Data
```bash
# Creates buildings from the 5 permits you already have
python step1_link_permits_to_buildings.py

# Should output:
# Buildings created: 5
# Permits updated: 5
```

**Expected result:** 5 building records with BBLs

---

### 3. Re-run PLUTO Enrichment (with rate limiting now)
```bash
python step2_enrich_from_pluto.py

# Should output:
# [1/5] BBL 2033100072 (555 BROOK AVE)...
#    ✅ Owner: SNL XXIII LLC
# [2/5] ...
```

**Expected result:** 4-5 buildings enriched with owner names

---

### 4. Verify Everything Works
```bash
# Check database state
python -c "
import psycopg2
import os
from dotenv import load_dotenv
load_dotenv()

conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()

# Count buildings
cur.execute('SELECT COUNT(*) FROM buildings')
print(f'Buildings: {cur.fetchone()[0]}')

# Count with BBL
cur.execute('SELECT COUNT(*) FROM permits WHERE bbl IS NOT NULL')
print(f'Permits with BBL: {cur.fetchone()[0]}')

# Count with owners
cur.execute('SELECT COUNT(*) FROM buildings WHERE current_owner_name IS NOT NULL')
print(f'Buildings with owners: {cur.fetchone()[0]}')

conn.close()
"
```

**Expected output:**
```
Buildings: 5
Permits with BBL: 5
Buildings with owners: 4-5
```

---

## 🟡 NEXT (This Week)

### 5. Test New Scraper Improvements
```bash
# Run scraper to get 10 more permits (uses new BBL error handling)
python add_permit_contacts.py
```

**What to watch:**
- BBL extraction warnings (if any)
- Rate limit handling (switches to remote after 3 hits)
- Date conversion consistency

---

### 6. Run Full Pipeline on New Data
```bash
# After scraping, link new permits to buildings
python step1_link_permits_to_buildings.py

# Enrich new buildings (with 0.5s delay between API calls)
python step2_enrich_from_pluto.py

# Try ACRIS (may not have data for new construction)
python step3_enrich_from_acris.py
```

---

### 7. Scale Test
```bash
# Update config to get 50 permits
# Then run full pipeline
python add_permit_contacts.py
python step1_link_permits_to_buildings.py
python step2_enrich_from_pluto.py
```

**Monitor:**
- Scraper performance (time per permit)
- Database commit frequency
- API rate limiting effectiveness

---

## 🟢 FUTURE (Next Sprint)

### 8. Build Step 4: Property Valuations
- Research Zillow/Redfin APIs or scraping methods
- Add `estimated_value` to buildings
- Script: `step4_estimate_property_values.py`

### 9. Build Step 5: Building Metrics
- Calculate `total_permit_spend` per building
- Count permits: `permit_count`
- Script: `step5_calculate_building_metrics.py`

### 10. Build Step 6: Scoring System
- Define scoring algorithms:
  - Affordability: value / median_income ratio
  - Renovation need: age + permit frequency
  - Contact quality: phone availability score
  - Overall: weighted average
- Script: `step6_calculate_scores.py`

### 11. Build Step 7: Skip Tracing
- Research skip tracing services
- Implement owner contact lookup
- Store in `owner_contacts` table
- Script: `step7_skip_trace_owners.py`

### 12. Update Dashboard
- Add building intelligence view
- Show scores and prioritization
- Filter by affordability/renovation need
- Map view with color-coded priorities

---

## 📋 CODE QUALITY (Ongoing)

### Medium Priority Improvements:
- [ ] Add database transaction rollback handling
- [ ] Add NULL checks before integer conversions
- [ ] Extract duplicate helpers to utils.py
- [ ] Add comprehensive docstrings
- [ ] Remove commented-out code

### Testing Tasks:
- [ ] Test with malformed permit data
- [ ] Test database connection failures
- [ ] Test API timeouts and errors
- [ ] Load test with 500+ permits

---

## 📊 Success Metrics

### Short Term (This Week):
- ✅ Migration runs successfully
- ✅ 5 existing permits linked to buildings
- ✅ 4-5 buildings have owner names
- ✅ New scrapes extract BBL without errors
- ✅ 50+ permits scraped and enriched

### Medium Term (This Month):
- ✅ 500+ permits in database
- ✅ 200+ unique buildings
- ✅ 80%+ PLUTO enrichment success rate
- ✅ Steps 4-6 implemented
- ✅ Dashboard shows building intelligence

### Long Term (This Quarter):
- ✅ 5000+ permits scraped
- ✅ 2000+ buildings with full intelligence
- ✅ Skip tracing operational
- ✅ Sales team using dashboard for prioritization

---

## 🐛 Known Issues to Monitor

1. **ACRIS has no data for new construction** (2021+ buildings)
   - Expected behavior
   - Focus on older buildings for transaction data

2. **BBL extraction may fail on unusual permit formats**
   - Now logs detailed warnings
   - Manually review failed extractions

3. **API rate limits** (NYC Open Data: 1000 req/day)
   - 0.5s delay = max 7200 calls/hour
   - Current pace is safe

4. **Proxy rotation** (if using proxies)
   - May need to adjust rotation logic
   - Monitor for IP bans

---

## 💡 Pro Tips

1. **Run scrapers during off-peak hours** (late night/early morning)
   - Less DOB website traffic
   - Fewer rate limits

2. **Commit to git frequently**
   - Track what worked/didn't work
   - Easy rollback if needed

3. **Monitor database size**
   - Run VACUUM periodically
   - Archive old permits

4. **Check logs for patterns**
   - BBL extraction failures
   - API errors
   - Rate limit hits

5. **Keep .env file backed up securely**
   - Don't commit to git
   - Store in password manager

---

## 🎯 Summary

**Start here:**
1. `python migrate_add_buildings.py`
2. `python step1_link_permits_to_buildings.py`
3. `python step2_enrich_from_pluto.py`
4. Verify data looks good
5. Run new scrape

**Goal:** Get to 50+ enriched buildings this week, then scale to 500+

**All fixes applied:** See CODE_REVIEW_SUMMARY.md for details
