# 🚀 Railway Deployment Checklist

## ✅ Pre-Deployment (COMPLETED)

- [x] Updated `dashboard.py` with PostgreSQL support
- [x] Created `Procfile` for Railway web process
- [x] Created `runtime.txt` specifying Python 3.12
- [x] Created `railway.json` with build configuration
- [x] Exported database to `postgres_schema.sql` (3 tables)
- [x] Exported all data to `postgres_data.sql` (2,025 rows)
- [x] Updated `requirements.txt` with psycopg2-binary
- [x] Created `.env.example` template
- [x] Created `.gitignore` to protect sensitive files
- [x] Wrote comprehensive `RAILWAY_DEPLOYMENT.md` guide
- [x] Created `deploy_to_github.sh` script for easy commit
- [x] Installed psycopg2-binary in local venv-permit

## 📋 Deployment Steps

### Step 1: Commit and Push to GitHub ⏳

**Option A: Use the script (easiest)**
```bash
cd /Users/matthewmakh/PycharmProjects/Smart_Installers/DOB_Permit_Scraper_Streamlit
./deploy_to_github.sh
```

**Option B: Manual commands**
```bash
cd /Users/matthewmakh/PycharmProjects/Smart_Installers/DOB_Permit_Scraper_Streamlit

# Add all deployment files
git add Procfile runtime.txt railway.json
git add RAILWAY_DEPLOYMENT.md DEPLOYMENT_READY.md
git add .env.example .gitignore
git add dashboard.py requirements.txt
git add postgres_schema.sql postgres_data.sql export_to_postgres.py
git add add_permit_contacts.py permit_scraper.py
git add FILTERS_IMPLEMENTED.md

# Commit with descriptive message
git commit -m "Add Railway deployment support with PostgreSQL"

# Push to GitHub
git push origin main
```

**Verify**: Check https://github.com/matthewmakh/NYC-DOB-permit-search-and-parse

---

### Step 2: Create Railway Project ⏳

1. Go to **https://railway.app/**
2. Sign in with your GitHub account
3. Click **"New Project"**
4. Select **"Deploy from GitHub repo"**
5. Choose **`matthewmakh/NYC-DOB-permit-search-and-parse`**
6. Railway will auto-detect Python and start building

**Expected**: Railway creates a service and starts deployment

---

### Step 3: Add PostgreSQL Database ⏳

1. In your Railway project dashboard, click **"+ New"**
2. Select **"Database"** → **"PostgreSQL"**
3. Railway provisions a PostgreSQL instance
4. Note the credentials in the "Variables" tab:
   - `PGHOST`
   - `PGPORT` (usually 5432)
   - `PGUSER` (usually postgres)
   - `PGPASSWORD`
   - `PGDATABASE` (usually railway)

**Cost**: ~$5/month for PostgreSQL

---

### Step 4: Configure Environment Variables ⏳

In your **web service** (not database), go to "Variables" tab and add:

```
DB_TYPE=postgresql
DB_HOST=${{Postgres.PGHOST}}
DB_PORT=${{Postgres.PGPORT}}
DB_USER=${{Postgres.PGUSER}}
DB_PASSWORD=${{Postgres.PGPASSWORD}}
DB_NAME=${{Postgres.PGDATABASE}}
```

**Note**: Railway uses `${{Postgres.VARIABLE}}` to reference the PostgreSQL service

**Expected**: Service automatically redeploys with new variables

---

### Step 5: Import Database Schema and Data ⏳

**Method 1: Using psql (recommended)**

1. Get connection string from Railway PostgreSQL service → "Connect"
2. On your local machine:
```bash
# Connect to Railway PostgreSQL
psql postgresql://postgres:PASSWORD@HOST:PORT/railway

# Import schema
\i /Users/matthewmakh/PycharmProjects/Smart_Installers/DOB_Permit_Scraper_Streamlit/postgres_schema.sql

# Import data
\i /Users/matthewmakh/PycharmProjects/Smart_Installers/DOB_Permit_Scraper_Streamlit/postgres_data.sql

# Verify import
SELECT COUNT(*) FROM permits;           -- Should show 1929
SELECT COUNT(*) FROM permit_search_config;  -- Should show 74
SELECT COUNT(*) FROM contact_scrape_jobs;   -- Should show 22

# Exit
\q
```

**Method 2: Using Railway Data Tab**
1. Click PostgreSQL service → "Data" tab
2. Use built-in SQL editor to paste and execute schema/data

**Expected**: 2,025 total rows imported successfully

---

### Step 6: Test Deployment ⏳

1. Go to your web service in Railway
2. Click the **public URL** (e.g., `your-app-name.up.railway.app`)
3. Dashboard should load with Streamlit interface

**Test checklist:**
- [ ] Dashboard loads without errors
- [ ] Permit data displays (should show 1,929+ permits)
- [ ] Filters work (permit type, date range, etc.)
- [ ] Map displays permit locations correctly
- [ ] Lead scoring shows calculated scores
- [ ] Export to CSV works
- [ ] Contact information displays properly

**Expected**: Fully functional dashboard with all features working

---

### Step 7: Update Local Scrapers (Optional) ⏳

If you want scrapers to write directly to Railway PostgreSQL:

1. **Update local `.env`:**
```bash
DB_TYPE=postgresql
DB_HOST=your-railway-postgres-host.railway.app
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your-railway-password
DB_NAME=railway
```

2. **Update scrapers** to support PostgreSQL:
   - See `RAILWAY_DEPLOYMENT.md` section 5.2
   - Both `permit_scraper.py` and `add_permit_contacts.py` need updates

3. **Test local scraper:**
```bash
cd /Users/matthewmakh/PycharmProjects/Smart_Installers/DOB_Permit_Scraper_Streamlit
source venv-permit/bin/activate
python permit_scraper.py
```

**Alternative**: Keep scrapers using MySQL locally, manually migrate data periodically

---

## 📊 Success Metrics

### Deployment Status
- [ ] GitHub repository updated with all files
- [ ] Railway project created and deployed
- [ ] PostgreSQL database provisioned
- [ ] Environment variables configured
- [ ] Database imported (2,025 rows)
- [ ] Dashboard accessible via public URL
- [ ] All features tested and working

### Performance
- [ ] Dashboard loads in < 3 seconds
- [ ] Filters respond quickly
- [ ] Map renders correctly
- [ ] No errors in Railway logs

---

## 💰 Cost Summary

| Service | Monthly Cost |
|---------|-------------|
| PostgreSQL Database | ~$5 |
| Web Service (Streamlit) | ~$5-10 |
| **Total** | **~$10-15** |

Railway free tier: $5/month credit (may cover basic usage)

---

## 🆘 Troubleshooting

### Dashboard won't load
- Check Railway deployment logs (Deployments tab)
- Verify all environment variables are set
- Ensure `DB_TYPE=postgresql` is set correctly
- Check PostgreSQL service is running

### Database connection errors
- Verify database credentials in environment variables
- Check PostgreSQL service status
- Test connection from local machine using psql

### Import fails
- Check SQL file syntax
- Verify PostgreSQL version compatibility
- Try importing schema first, then data separately

### Scrapers can't connect to Railway database
- Update scrapers to support PostgreSQL
- Verify Railway PostgreSQL allows external connections
- Check firewall/network settings

---

## 📚 Documentation

- **Deployment Guide**: `RAILWAY_DEPLOYMENT.md` (detailed step-by-step)
- **Quick Start**: `DEPLOYMENT_READY.md` (overview)
- **Environment Template**: `.env.example` (copy and customize)
- **Database Schema**: `postgres_schema.sql` (table definitions)
- **Database Data**: `postgres_data.sql` (exported records)

---

## 🎯 Next Steps After Deployment

1. **Custom Domain**: Configure custom domain in Railway (optional)
2. **Monitoring**: Set up Railway alerts for errors
3. **Backups**: Schedule regular database backups
4. **Automation**: Set up cron job for local scrapers
5. **Scaling**: Monitor usage and adjust Railway plan if needed

---

## ✅ Final Verification

Before considering deployment complete:

1. [ ] Dashboard accessible at public URL
2. [ ] All 1,929+ permits display correctly
3. [ ] Filters and search work properly
4. [ ] Map shows permit locations
5. [ ] Contact information displays
6. [ ] Lead scores calculated correctly
7. [ ] CSV export functions
8. [ ] No errors in Railway logs
9. [ ] Local scrapers updated (if using Railway DB)
10. [ ] Documentation reviewed and understood

---

**Current Status**: Ready for Step 1 (Commit and Push to GitHub)

**Next Action**: Run `./deploy_to_github.sh` or manually commit/push files
