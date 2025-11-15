# Code Review Findings
**Date:** 2025-01-XX  
**Scope:** Permit Scraper + Building Intelligence Pipeline

---

## 🚨 CRITICAL ISSUES (Must Fix)

### 1. **Missing `buildings` table in schema**
- **File:** `postgres_schema.sql`
- **Issue:** The buildings table is not defined in the schema but is used extensively in step1/step2/step3
- **Impact:** Production deployment will fail, fresh database setups will crash
- **Fix Required:** Add complete buildings table definition with all columns

### 2. **Missing `bbl` column in permits table**
- **File:** `postgres_schema.sql` 
- **Issue:** step1 updates `permits.bbl` but column not in schema
- **Impact:** Database migration will fail on first run
- **Fix Required:** Add `bbl VARCHAR(10)` to permits table

### 3. **Date conversion not applied to dates in remote scraper**
- **File:** `remote_add_permit_contacts.py` lines 340-355
- **Issue:** Local scraper calls `convert_date()` helper but remote scraper inserts raw date strings
- **Impact:** Date format inconsistency, potential PostgreSQL type errors
- **Fix Required:** Add convert_date() helper and apply to filing_date, proposed_job_start, work_approved

### 4. **No database transaction rollback on errors**
- **Files:** All scrapers (add_permit_contacts.py, remote_add_permit_contacts.py, permit_scraper.py)
- **Issue:** conn.commit() called frequently but no try/except/rollback for database errors
- **Impact:** Partial data commits on crashes, corrupted state
- **Fix Required:** Wrap database operations in try/except with conn.rollback()

### 5. **BBL extraction returns None silently**
- **File:** `add_permit_contacts.py` line 376, `remote_add_permit_contacts.py` line 83
- **Issue:** extract_bbl_info() returns {'block': None, 'lot': None} on error with generic except
- **Impact:** Permits saved without BBL, buildings can't be created, pipeline breaks silently
- **Fix Required:** Log the actual exception, add fallback extraction methods

---

## ⚠️ HIGH PRIORITY ISSUES

### 6. **Hardcoded database credentials in files**
- **Files:** step1/step2/step3 all have `DATABASE_URL` hardcoded
- **Issue:** Credentials in code, won't work for other environments
- **Impact:** Security risk, Railway migration requires code changes
- **Fix Required:** Use os.getenv('DATABASE_URL') with no default

### 7. **No rate limit on PLUTO/ACRIS API calls**
- **Files:** step2_enrich_from_pluto.py, step3_enrich_from_acris.py
- **Issue:** Loops through buildings making sequential API requests with no delays
- **Impact:** Could hit NYC Open Data rate limits (1000 req/day), ban IP
- **Fix Required:** Add time.sleep(0.5) between API calls, add retry logic

### 8. **Duplicate contact records being inserted**
- **File:** `remote_add_permit_contacts.py` lines 283-285
- **Issue:** Inserts "SKIPPED_UNTRACKED" and "SKIPPED_ALREADY_CHECKED" as name values
- **Impact:** Junk data in contacts table, meaningless records
- **Fix Required:** Remove these INSERT statements or use a separate tracking table

### 9. **No validation of BBL format**
- **Files:** step1_link_permits_to_buildings.py, step2, step3
- **Issue:** BBL should be exactly 10 digits but no validation
- **Impact:** Invalid BBLs sent to APIs return no data, silent failures
- **Fix Required:** Add validation: len(bbl) == 10 and bbl.isdigit()

### 10. **Max rate limits logic is inconsistent**
- **Files:** Local scraper has MAX_RATE_LIMITS=1, remote has MAX_RATE_LIMITS=3
- **Issue:** Local scraper switches to remote after 1 denial, but remote tolerates 3
- **Impact:** Confusing behavior, could cause infinite loops if both hit limits
- **Fix Required:** Use same value (3) in both, or make configurable

---

## 📋 MEDIUM PRIORITY IMPROVEMENTS

### 11. **Missing indexes on critical columns**
- **File:** postgres_schema.sql
- **Issue:** No indexes on permits.bbl, permits.block, permits.lot, buildings.bbl
- **Impact:** Slow queries as data scales, step1 JOIN will be slow
- **Fix Required:** Add indexes on these columns

### 12. **No NULL checks before integer conversion**
- **File:** `add_permit_contacts.py` lines 454-456
- **Issue:** `int(total_units_text)` called without checking if value is None
- **Impact:** Crashes on permits without dwelling unit data
- **Fix Required:** Check `if total_units_text and total_units_text.isdigit()`

### 13. **permit_scraper.py cursor issues not fully fixed**
- **File:** permit_scraper.py
- **Issue:** Code has mixed cursor handling, some fetchone() calls may still assume tuples
- **Impact:** May crash on specific database queries
- **Fix Required:** Audit all cursor.fetchone() calls, ensure RealDictCursor consistency

### 14. **ACRIS date parsing will fail on malformed dates**
- **File:** step3_enrich_from_acris.py line 125
- **Issue:** Generic except: return None hides errors
- **Impact:** Failed date conversions are silent, lose transaction dates
- **Fix Required:** Log the exception with date_str value

### 15. **Work description extraction fragile**
- **Files:** Both scrapers lines ~430
- **Issue:** Relies on specific HTML structure (.content class), stops at first .label
- **Impact:** May truncate work descriptions or capture wrong data
- **Fix Required:** Add better boundary detection, handle missing elements gracefully

---

## 🧹 CODE CLEANUP & HOUSEKEEPING

### 16. **Commented-out code should be removed**
- **File:** add_permit_contacts.py lines 6-7, 243-244
- **Issue:** Old version pinning and headless mode comments
- **Fix:** Remove or move to documentation

### 17. **Duplicate helper functions across files**
- **Issue:** `human_delay()`, `fix_date_format()`, `get_text_by_label()` duplicated
- **Impact:** Maintenance burden, inconsistencies
- **Fix:** Create shared utils.py module

### 18. **Magic numbers throughout code**
- **Examples:** 
  - 5 rows limit in work description (line ~443)
  - 20 documents limit in ACRIS (step3 line 78)
  - 100 permits commit batch (step1 line 98)
- **Fix:** Extract to named constants at top of file

### 19. **No docstrings on most functions**
- **Issue:** Only some functions have docstrings, most are undocumented
- **Impact:** Hard to understand code purpose, maintenance difficulty
- **Fix:** Add docstrings to all public functions

### 20. **Inconsistent error message format**
- **Examples:** "⚠️", "❌", "✅" emoji usage is inconsistent
- **Fix:** Standardize: ✅ success, ⚠️ warning, ❌ error, 🔍 info

---

## 🔒 SECURITY CONCERNS

### 21. **No input sanitization on permit numbers**
- **Files:** All scrapers use permit_no directly in SQL queries
- **Issue:** Using parameterized queries (good) but no validation of input format
- **Impact:** Low risk but worth noting for security audit
- **Fix:** Validate permit_no matches expected pattern (digit + letter + digits)

### 22. **Proxy credentials in environment variables**
- **File:** add_permit_contacts.py lines 130-132
- **Issue:** PROXY_USER and PROXY_PASS in .env (acceptable) but logged in plain text
- **Impact:** Credentials may appear in log files
- **Fix:** Redact credentials from print statements

---

## 📊 EDGE CASES TO HANDLE

### 23. **What if permit has block but no lot (or vice versa)?**
- **Current:** derive_bbl_from_permit() returns None
- **Issue:** Silently skips the permit
- **Fix:** Log warning with permit_no and address for manual review

### 24. **What if PLUTO returns multiple records for same BBL?**
- **Current:** Takes first record ([0])
- **Issue:** No guarantee which is correct
- **Fix:** Log warning if len(data) > 1, consider deduplication logic

### 25. **What if permit has no contacts at all?**
- **Current:** Inserts empty contact record with is_checked=TRUE
- **Issue:** Clutters contacts table
- **Fix:** Consider separate tracking (permit_processing_status table)

### 26. **What if date fields are empty strings vs None?**
- **Current:** convert_date() returns None on ValueError
- **Issue:** Empty strings may not trigger ValueError
- **Fix:** Strip and check `if not date_str or not date_str.strip():`

### 27. **What if borough code is invalid (0, 6, 7, etc.)?**
- **Current:** No validation, BBL will be malformed
- **Issue:** API queries will fail
- **Fix:** Validate borough_code in [1,2,3,4,5], log error if invalid

---

## 🎯 RECOMMENDED FIXES PRIORITY

### Immediate (Before Next Scrape Run):
1. Add buildings table to schema ✅ CRITICAL
2. Add bbl column to permits table ✅ CRITICAL  
3. Fix date conversion in remote scraper ✅ CRITICAL
4. Add transaction rollback error handling ✅ CRITICAL
5. Fix BBL extraction error handling ✅ CRITICAL

### Before Production Scale-Up:
6. Remove hardcoded DATABASE_URL
7. Add API rate limiting to step2/step3
8. Add BBL validation
9. Add missing database indexes
10. Standardize MAX_RATE_LIMITS

### Nice to Have (Tech Debt):
11. Extract duplicate code to utils.py
12. Add comprehensive docstrings
13. Remove commented code
14. Extract magic numbers to constants
15. Improve error messages

---

## 📝 TESTING RECOMMENDATIONS

1. **Test with malformed data:** Run scraper on older permits with missing fields
2. **Test database failure:** Kill database connection mid-scrape, verify rollback
3. **Test API failures:** Mock PLUTO/ACRIS 500 errors, verify retry/skip logic
4. **Test BBL edge cases:** Manually create permits with block=0, lot=0, block=None
5. **Load test:** Run scraper for 100+ permits, check memory usage and commit frequency

---

**Next Steps:**
1. Fix critical issues (1-5) immediately
2. Run test scrape to validate fixes
3. Implement high-priority improvements (6-10)
4. Add monitoring/logging for production
