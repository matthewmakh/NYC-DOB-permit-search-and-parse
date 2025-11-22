# Contacts Table Deprecation Audit

## ✅ MIGRATION COMPLETE - November 21, 2025

All deprecated files have been moved to `_archived_old_architecture/`

## Summary
Since switching to NYC OpenData API, phone numbers are stored directly in `permits` table columns (`permittee_phone`, `owner_phone`). The old `contacts` table is no longer populated and should not be referenced.

**Current Stack:**
- ✅ Flask HTML dashboard (`dashboard_html/app.py`) 
- ✅ NYC OpenData API scraper (`permit_scraper_api.py`)
- ✅ Direct phone storage in permits table
- ✅ Area code detection for mobile phones (no API costs)

## ✅ Already Fixed
- **dashboard_html/app.py** - Uses `permits` table columns directly, no contacts table references
- **calculate_lead_score()** - Now uses area code detection on permit phone columns (lines 62-77)

## ✅ Files Archived (Moved to `_archived_old_architecture/`)

All deprecated files have been moved and are no longer cluttering the main directory:

1. ✅ **dashboard.py** - Old Streamlit dashboard (replaced by Flask)
2. ✅ **add_permit_contacts.py** - Local permit scraper (replaced by OpenData API)
3. ✅ **remote_add_permit_contacts.py** - Remote permit scraper (replaced by OpenData API)
4. ✅ **update_phone_types.py** - Twilio phone validator (replaced by area code detection)
5. ✅ **check_contacts.py** - Debug script for contacts table
6. ✅ **fix_all_contacts_references.py** - One-time migration script
7. ✅ **migrate_contacts_to_permits.py** - One-time migration script

See `_archived_old_architecture/README.md` for full details on each file.

---

## 🎯 Active Files (Current Architecture)

### Production Application
- ✅ **dashboard_html/app.py** - Flask API backend with area code mobile detection
- ✅ Uses permits table columns directly
- ✅ `calculate_lead_score()` function (lines 53-97) with mobile area code detection

### Data Acquisition
- ✅ **permit_scraper_api.py** - NYC OpenData API scraper

### No Changes Required
The remaining files in the repository are part of the current active architecture and do not need updates.

---

## 📊 Database Schema Status

### Deprecated Table
- **contacts** table (1,547 rows) - No longer populated, can be dropped after archiving data

### Active Columns (permits table)
- `permittee_phone` - Primary contact phone
- `owner_phone` - Owner phone
- `permittee_business_name` - Contact name
- `owner_business_name` - Owner name

### Mobile Detection Strategy
**Current**: Area code pattern matching in `calculate_lead_score()` function
- Mobile prefixes: 347, 646, 917, 929, 332
- No database storage needed
- Calculated on-the-fly

**Alternative** (if needed): Add columns to permits table
```sql
ALTER TABLE permits ADD COLUMN IF NOT EXISTS is_mobile_permittee BOOLEAN;
ALTER TABLE permits ADD COLUMN IF NOT EXISTS is_mobile_owner BOOLEAN;
```
But this is **not recommended** since area code detection works well and is simpler.

---

## Summary Statistics

- **Files needing updates**: 3-6 (depending on what's in use)
- **Files to archive**: 4-7
- **Priority**: Determine which scripts are still running first
