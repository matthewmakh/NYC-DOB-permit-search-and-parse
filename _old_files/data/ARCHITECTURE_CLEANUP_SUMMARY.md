# Architecture Cleanup - November 21, 2025

## What We Did

Successfully cleaned up the codebase by archiving deprecated files from the old architecture.

## Files Moved to Archive

Moved 7 deprecated files to `_archived_old_architecture/`:

1. ✅ `dashboard.py` - Old Streamlit dashboard
2. ✅ `add_permit_contacts.py` - Local permit scraper  
3. ✅ `remote_add_permit_contacts.py` - Remote permit scraper
4. ✅ `update_phone_types.py` - Twilio phone validator
5. ✅ `check_contacts.py` - Contacts table debug script
6. ✅ `fix_all_contacts_references.py` - Migration utility
7. ✅ `migrate_contacts_to_permits.py` - Migration script

## Current Active Architecture

### Frontend & Backend
- **Flask HTML Dashboard**: `dashboard_html/app.py`
- **Templates**: `dashboard_html/templates/*.html`
- **Static Assets**: `dashboard_html/static/`

### Data Acquisition
- **NYC OpenData API Scraper**: `permit_scraper_api.py`
- No longer scraping individual permit pages

### Data Storage
- **Direct phone storage** in `permits` table:
  - `permittee_phone`
  - `owner_phone`  
  - `permittee_business_name`
  - `owner_business_name`

### Mobile Detection
- **Area code pattern matching** in `calculate_lead_score()` function
- NYC mobile prefixes: 347, 646, 917, 929, 332
- No external API costs
- Works on 100% of permits (not just 2.4%)

### Enrichment Pipeline
- `step1_link_permits_to_buildings.py`
- `step2_enrich_from_pluto.py`
- `step3_enrich_from_acris.py`
- `run_enrichment_pipeline.py`

## Database Cleanup Recommendation

The `contacts` table (1,547 rows) is no longer populated and can be dropped:

```sql
-- Optional: Backup first
CREATE TABLE contacts_backup AS SELECT * FROM contacts;

-- Then drop
DROP TABLE contacts;
```

## Benefits of Cleanup

1. **Clearer codebase** - Only active files in root directory
2. **Reduced confusion** - No wondering which version to use
3. **Better onboarding** - New developers see only current architecture
4. **Git history preserved** - All files still accessible via git
5. **Easy recovery** - Archive directory has README with recovery instructions

## Next Steps

1. ✅ Files archived
2. ✅ Documentation updated
3. ⏳ Consider dropping `contacts` table from database
4. ⏳ Update README.md with new architecture overview
5. ⏳ Remove references to Streamlit from documentation

## No Code Changes Required

The active codebase (`dashboard_html/app.py` and related files) already uses the correct architecture:
- ✅ Queries permits table directly
- ✅ Uses area code detection for mobile phones
- ✅ No contacts table dependencies

## Reference Documents

- `_archived_old_architecture/README.md` - Details on each archived file
- `CONTACTS_TABLE_DEPRECATION_AUDIT.md` - Full migration audit and history
- Git history - All changes tracked with full context
