# Archived Files - Old Architecture

## Date Archived
November 21, 2025

## Reason for Archival
These files are from the old architecture before switching to:
1. NYC OpenData API for permit scraping (instead of individual page scraping)
2. Flask HTML dashboard (instead of Streamlit)
3. Direct phone storage in permits table (instead of separate contacts table)
4. Area code detection for mobile phones (instead of Twilio API validation)

## Archived Files

### 1. **dashboard.py** - Old Streamlit Dashboard
- **Replaced by**: `dashboard_html/app.py` (Flask)
- **Why deprecated**: Switched from Streamlit to Flask/HTML for better performance and customization
- **Notable features**: Had contacts table JOINs, old scoring logic

### 2. **add_permit_contacts.py** - Local Permit Scraper
- **Replaced by**: NYC OpenData API (`permit_scraper_api.py`)
- **Why deprecated**: No longer scraping individual permit pages, using bulk API instead
- **Notable features**: Scraped contact info from individual DOB permit pages

### 3. **remote_add_permit_contacts.py** - Remote Permit Scraper
- **Replaced by**: NYC OpenData API (Railway deployment)
- **Why deprecated**: Same as above, Railway now runs API scraper instead
- **Notable features**: Remote version for Railway deployment

### 4. **update_phone_types.py** - Twilio Phone Validator
- **Replaced by**: Area code detection in `calculate_lead_score()` function
- **Why deprecated**: 
  - Twilio API costs money
  - Area code detection is instant and free
  - Works on 100% of permits (not just 2.4% with contacts table entries)
- **Notable features**: Used Twilio Lookup API to classify mobile vs landline

### 5. **check_contacts.py** - Debug/Inspection Script
- **Replaced by**: N/A (database schema changed)
- **Why deprecated**: Queries contacts table which is no longer populated
- **Notable features**: Displayed statistics about contacts table data

### 6. **fix_all_contacts_references.py** - Migration Script
- **Replaced by**: N/A (one-time migration completed)
- **Why deprecated**: One-time script to fix old code references
- **Notable features**: Automated search/replace for contacts table refactoring

### 7. **migrate_contacts_to_permits.py** - Migration Script
- **Replaced by**: N/A (one-time migration completed)
- **Why deprecated**: One-time script to move data from contacts → permits table
- **Notable features**: Migrated contact data into permits table columns

## Database Schema Changes

### Deprecated Table
- **contacts** table (1,547 rows) - No longer populated since switching to OpenData API

### Current Architecture
- **permits** table stores phone numbers directly:
  - `permittee_phone`
  - `owner_phone`
  - `permittee_business_name`
  - `owner_business_name`

## Active Files (Current Architecture)

### Main Application
- `dashboard_html/app.py` - Flask API backend
- `dashboard_html/templates/*.html` - Frontend templates
- `dashboard_html/static/` - CSS/JS assets

### Data Acquisition
- `permit_scraper_api.py` - NYC OpenData API scraper

### Enrichment Pipeline
- `step1_link_permits_to_buildings.py`
- `step2_enrich_from_pluto.py`
- `step3_enrich_from_acris.py`
- `run_enrichment_pipeline.py`

### Utilities
- `geocode_permits.py`
- Various migration scripts for building intelligence

## Recovery Instructions

If you need to reference or recover these files:

1. **View files**: They're in `_archived_old_architecture/`
2. **Git history**: All files remain in git history
3. **Restore**: Simply copy back to root directory if needed

## Important Notes

- The contacts table still exists in the database but is no longer populated
- To fully clean up, consider: `DROP TABLE contacts;`
- All lead scoring now uses area code detection (no external APIs)
- Mobile prefixes: 347, 646, 917, 929, 332

## Contact
If you have questions about these archived files, refer to:
- `CONTACTS_TABLE_DEPRECATION_AUDIT.md` for detailed migration info
- Git commit history for when changes were made
