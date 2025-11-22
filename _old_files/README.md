# 📦 Old Files Archive
**Created:** November 21, 2025  
**Purpose:** Store deprecated, test, and one-time scripts to keep root directory clean

---

## 📁 Directory Structure

### `/tests/` - Test & Verification Scripts (10 files)
Scripts used for manual testing and verification during development.

- `test_acris_enrichment.py` - Test ACRIS API integration
- `test_enhanced_acris.py` - Test enhanced ACRIS features
- `test_geoclient_api.py` - Test NYC Geoclient API
- `test_geocode_switching.py` - Test geocoding fallback logic
- `test_permit_upsert.py` - Test database upsert operations
- `test_proxy.py` - Test proxy configuration
- `test2.py` - Generic test file
- `verify_api_data.py` - Verify API data insertion
- `verify_contacts_migration.py` - Verify contact migration
- `verify_fix.py` - Verify bug fixes

**Note:** These are manual test scripts. For automated tests, see root `/tests/` directory (to be created).

---

### `/deprecated/` - Old Versions (4 files)
Scripts that have been replaced or are no longer used.

- `permit_scraper_old.py` - Old Selenium scraper (replaced by `permit_scraper_api.py`)
- `step2_enrich_from_pluto.py.old` - Backup of Step 2 before refactor
- `Scraper_frontend.py` - Old Streamlit UI (replaced by `dashboard_html/`)
- `step1_from_branch.py` - Branch-specific migration (no longer needed)

**Why deprecated:**
- `permit_scraper_old.py` → Replaced with NYC Open Data API (faster, more reliable)
- `Scraper_frontend.py` → Replaced with Flask + HTML dashboard (better UX)

---

### `/migrations/` - Database Migrations (13 files)
One-time scripts that modified database schema. Already executed on production.

**Schema Migrations:**
- `migrate_add_buildings.py` - Created buildings table
- `migrate_add_building_intelligence.py` - Added intelligence fields
- `migrate_add_acris_intelligence.py` - Added ACRIS transaction tables
- `migrate_add_dual_owner_fields.py` - Added PLUTO + RPAD owner fields
- `migrate_add_hpd_fields.py` - Added HPD violation fields
- `migrate_add_nyc_open_data_fields.py` - Added API source tracking
- `migrate_add_unique_permit_no.py` - Added unique constraint on permit_no

**Data Migrations:**
- `migrate_restructure_contacts.py` - Moved contacts from separate table to permits
- `migrate_restructure_contacts_v2.py` - V2 of contact restructure
- `add_bbl_and_details_columns.py` - Added BBL column to permits
- `add_unique_constraints.py` - Added database constraints
- `populate_contacts_from_permits.py` - Populated contact data
- `sync_contacts_from_permits.py` - Synced contact updates

**Status:** ✅ All migrations completed successfully on production database

**Important:** Do NOT delete these files. They serve as:
1. Documentation of schema evolution
2. Reference for rollback if needed
3. Blueprint for new environment setup

---

### `/audits/` - Audit & Check Scripts (5 files)
Scripts used to validate data quality and system state.

- `audit_migration.py` - Audit database migration results
- `check_enrichment_eligibility.py` - Check which buildings can be enriched
- `check_migration_safety.py` - Safety check before migrations
- `check_owner_data.py` - Validate owner data quality
- `calculate_property_intelligence.py` - Calculate property scores (not yet implemented)

**Use case:** Run these after major changes to verify data integrity.

---

### `/utilities/` - One-Time Utilities (3 files)
Helper scripts for specific one-time tasks.

- `export_to_postgres.py` - Export from MySQL to PostgreSQL (legacy)
- `import_to_railway.py` - Import data to Railway database
- `reset_acris_eligibility.py` - Reset ACRIS enrichment flags

**Note:** These were used during platform migration and setup.

---

### `/data/` - Data Files & Dumps (8+ files)
SQL dumps, CSV exports, debug files, and logs.

- `postgres_data.sql` - PostgreSQL data dump (legacy)
- `postgres_schema.sql` - Schema definition (legacy)
- `DB_SCHEMA.txt` - Text version of schema
- `phone_type_results.csv` - Phone validation results
- `dob_debug_dump.html` - Debug HTML dump from scraper
- `proxy_auth_plugin.zip` - Chrome proxy plugin
- `*.log` - Various log files

**Why archived:**
- Schema now defined in migration files
- Data dumps replaced by Railway automated backups
- Debug files from development

---

### `/configs/` - Old Configuration Files (2 files)
Deprecated Railway configuration files.

- `railway.step2.json` - Old Step 2 config
- `railway.geocode.json` - Old geocoding config

**Replaced by:**
- `railway.enrichment.json` (runs entire pipeline)
- `railway.cron.json` (all cron jobs)

---

## ⚠️ Important Notes

### Do NOT Delete This Directory
These files are archived for:
1. **Historical reference** - Understanding how the system evolved
2. **Troubleshooting** - Comparing current vs old implementations
3. **Documentation** - Seeing what approaches were tried
4. **Rollback capability** - Reverting migrations if needed

### When to Use These Files

**Migration Files:**
- Setting up new environment from scratch
- Rolling back database changes
- Understanding schema evolution

**Test Files:**
- Debugging specific components
- Validating after major changes
- Creating automated tests (as reference)

**Deprecated Files:**
- Understanding old approaches
- Extracting useful code snippets
- Learning from past decisions

### Maintenance

**Review quarterly** to decide if files can be permanently deleted:
- ✅ Keep: Migration files (permanent record)
- ✅ Keep: Test files with unique scenarios
- 🗑️ Delete: Temporary debug files older than 1 year
- 🗑️ Delete: Data dumps older than 6 months (if backed up)

---

## 📚 Related Documentation

- **Active code:** See root directory and `dashboard_html/`
- **System architecture:** See `COMPREHENSIVE_AUDIT_2025.md`
- **Strategic vision:** See `PROJECT_VISION_AND_ROADMAP.md`
- **Database schema:** See `DATABASE_SCHEMA.md`
- **Deployment:** See `DEPLOYMENT_CHECKLIST.md`

---

## 🔍 Finding Specific Files

### "I need to see how X used to work"
Check `/deprecated/` for old implementations

### "What database changes were made?"
Check `/migrations/` and read filenames in chronological order

### "How do I test feature Y?"
Check `/tests/` for relevant test scripts

### "Where is the old data?"
Check `/data/` for SQL dumps and CSVs

### "What configs did we use before?"
Check `/configs/` for old Railway configurations

---

**Last Updated:** November 21, 2025  
**Next Review:** February 2026

