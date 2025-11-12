# Railway Deployment - Ready for GitHub

## ✅ Completed Tasks

### 1. Dashboard Updated for PostgreSQL Support
**File: `dashboard.py`**
- Added `psycopg2` and `psycopg2.extras` imports
- Created `DB_TYPE` environment variable detection
- Updated `get_db_connection()` to support both MySQL and PostgreSQL
- Modified SQL queries to use PostgreSQL syntax:
  - `string_agg()` instead of `GROUP_CONCAT()`
  - `CURRENT_DATE` instead of `CURDATE()`
  - `INTERVAL '30 days'` instead of `INTERVAL 30 DAY`
  - Boolean `true/false` instead of `1/0`
- Updated cursor creation for PostgreSQL (`RealDictCursor`)

### 2. Railway Deployment Files Created
- **Procfile**: Defines web process to run Streamlit
- **runtime.txt**: Specifies Python 3.12
- **railway.json**: Configures NIXPACKS builder

### 3. Database Migration Files Created
- **postgres_schema.sql**: PostgreSQL table definitions (3 tables)
- **postgres_data.sql**: Exported data (2,025 rows: 1,929 permits + 74 configs + 22 jobs)

### 4. Documentation Created
- **RAILWAY_DEPLOYMENT.md**: Comprehensive step-by-step deployment guide
- **.env.example**: Template for environment variables
- **.gitignore**: Protects sensitive files from being committed

### 5. Dependencies Updated
- **requirements.txt**: Added `psycopg2-binary==2.9.9` for PostgreSQL support

### 6. Local Environment Updated
- Installed `psycopg2-binary==2.9.9` in `venv-permit`

## 📁 Files to Commit to GitHub

```bash
# New files
Procfile
runtime.txt
railway.json
RAILWAY_DEPLOYMENT.md
.env.example
.gitignore

# Modified files
dashboard.py
requirements.txt

# Database files (optional - can import manually)
postgres_schema.sql
postgres_data.sql
```

## 🚀 Next Steps

### Step 1: Commit and Push to GitHub
```bash
cd /Users/matthewmakh/PycharmProjects/Smart_Installers/DOB_Permit_Scraper_Streamlit

# Add all deployment files
git add dashboard.py requirements.txt Procfile runtime.txt railway.json
git add RAILWAY_DEPLOYMENT.md .env.example .gitignore
git add postgres_schema.sql postgres_data.sql

# Commit
git commit -m "Add Railway deployment support with PostgreSQL"

# Push to main branch
git push origin main
```

### Step 2: Set Up Railway
1. Go to https://railway.app/
2. Sign in with GitHub
3. Click "New Project" → "Deploy from GitHub repo"
4. Select `matthewmakh/NYC-DOB-permit-search-and-parse`
5. Add PostgreSQL database service
6. Configure environment variables (see RAILWAY_DEPLOYMENT.md)

### Step 3: Import Database
Follow the database import instructions in RAILWAY_DEPLOYMENT.md:
- Connect to Railway PostgreSQL
- Import `postgres_schema.sql`
- Import `postgres_data.sql`
- Verify data loaded correctly

### Step 4: Test Deployment
- Visit your Railway URL
- Verify dashboard loads and displays data
- Test all filters and features

## 🔧 How It Works

### Database Connection Logic
The dashboard now detects the `DB_TYPE` environment variable:
- **Local development**: Set `DB_TYPE=mysql` (or omit - defaults to MySQL)
- **Railway production**: Set `DB_TYPE=postgresql`

### SQL Compatibility
All database queries now check `DB_TYPE` and use the correct SQL syntax:
```python
if DB_TYPE == 'postgresql':
    # PostgreSQL syntax (string_agg, CURRENT_DATE, etc.)
else:
    # MySQL syntax (GROUP_CONCAT, CURDATE(), etc.)
```

### Environment Variables
**For Railway Dashboard:**
```
DB_TYPE=postgresql
DB_HOST=${{Postgres.PGHOST}}
DB_PORT=${{Postgres.PGPORT}}
DB_USER=${{Postgres.PGUSER}}
DB_PASSWORD=${{Postgres.PGPASSWORD}}
DB_NAME=${{Postgres.PGDATABASE}}
```

**For Local Scrapers:**
- Keep using MySQL locally, OR
- Update scrapers to support PostgreSQL and point to Railway

## 💰 Cost Estimate
- PostgreSQL Database: ~$5/month
- Web Service: ~$5-10/month
- **Total: ~$10-15/month**

Railway offers $5 free credit/month.

## 📊 Current Data
- **Permits**: 1,929 records
- **Search Configs**: 74 records
- **Scrape Jobs**: 22 records
- **Total**: 2,025 rows exported

## ⚠️ Important Notes

1. **Don't commit .env file** - Already in .gitignore
2. **Scrapers stay local** - Only dashboard goes to Railway
3. **PostgreSQL only on Railway** - Local can stay MySQL
4. **Auto-deploy enabled** - Push to GitHub triggers Railway deployment
5. **SSL automatic** - Railway provides HTTPS for free

## 🐛 Troubleshooting

If dashboard doesn't load on Railway:
1. Check deployment logs in Railway dashboard
2. Verify all environment variables are set correctly
3. Ensure `DB_TYPE=postgresql` is set
4. Check PostgreSQL service is running
5. Verify database import completed successfully

## 📚 Reference Files

- **Deployment Guide**: `RAILWAY_DEPLOYMENT.md` (detailed step-by-step)
- **Environment Template**: `.env.example` (copy and customize)
- **Schema**: `postgres_schema.sql` (table definitions)
- **Data**: `postgres_data.sql` (all exported records)

---

**Status**: ✅ Ready for GitHub deployment
**Next Action**: Commit and push files, then follow RAILWAY_DEPLOYMENT.md
