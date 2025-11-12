# Railway Deployment Guide - NYC DOB Permit Dashboard

## Overview
This guide will walk you through deploying the Streamlit dashboard to Railway with a PostgreSQL database, while keeping the scrapers running locally.

## Architecture
- **Local**: `permit_scraper.py` and `add_permit_contacts.py` (scrapers with Chrome)
- **Railway**: `dashboard.py` (Streamlit) + PostgreSQL database

## Prerequisites
1. GitHub account
2. Railway account (sign up at https://railway.app/)
3. Local repository pushed to GitHub

## Step 1: Prepare Your GitHub Repository

### Files to Commit
Make sure these files are in your repo:
```
dashboard.py                 # Updated with PostgreSQL support
requirements.txt             # Updated with psycopg2-binary
Procfile                     # Railway web process config
runtime.txt                  # Python 3.12 specification
railway.json                 # Railway build configuration
postgres_schema.sql          # Database table definitions
postgres_data.sql            # Exported data (optional - can import manually)
.env.example                 # Example environment variables
```

### Create .env.example
Create a file showing what environment variables are needed:
```bash
# Database Configuration
DB_TYPE=postgresql
DB_HOST=your-railway-postgres-host
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your-password
DB_NAME=railway
```

### Commit and Push
```bash
cd /Users/matthewmakh/PycharmProjects/Smart_Installers/DOB_Permit_Scraper_Streamlit
git add dashboard.py requirements.txt Procfile runtime.txt railway.json postgres_schema.sql
git commit -m "Add Railway deployment support with PostgreSQL"
git push origin main
```

## Step 2: Set Up Railway Project

### 2.1 Create New Project
1. Go to https://railway.app/
2. Click "New Project"
3. Select "Deploy from GitHub repo"
4. Choose your repository: `matthewmakh/NYC-DOB-permit-search-and-parse`
5. Railway will detect the Python app automatically

### 2.2 Add PostgreSQL Database
1. In your Railway project, click "+ New"
2. Select "Database" → "PostgreSQL"
3. Railway will create a PostgreSQL instance
4. Note: This will cost ~$5/month for database + ~$5-10/month for web service

## Step 3: Configure Database

### 3.1 Get Database Credentials
1. Click on your PostgreSQL service
2. Go to "Variables" tab
3. Note these values (Railway provides them automatically):
   - `PGHOST`
   - `PGPORT`
   - `PGUSER`
   - `PGPASSWORD`
   - `PGDATABASE`

### 3.2 Import Schema
1. Click on PostgreSQL service → "Data" tab
2. Click "Connect" to get connection command
3. On your local machine, run:
```bash
# Connect to Railway PostgreSQL
psql postgresql://postgres:PASSWORD@HOST:PORT/railway

# Import schema
\i /Users/matthewmakh/PycharmProjects/Smart_Installers/DOB_Permit_Scraper_Streamlit/postgres_schema.sql

# Import data
\i /Users/matthewmakh/PycharmProjects/Smart_Installers/DOB_Permit_Scraper_Streamlit/postgres_data.sql

# Verify
SELECT COUNT(*) FROM permits;
SELECT COUNT(*) FROM contact_scrape_jobs;
SELECT COUNT(*) FROM permit_search_config;
```

## Step 4: Configure Web Service

### 4.1 Set Environment Variables
In your Railway dashboard service (the web service):
1. Go to "Variables" tab
2. Add these variables:

```
DB_TYPE=postgresql
DB_HOST=${{Postgres.PGHOST}}
DB_PORT=${{Postgres.PGPORT}}
DB_USER=${{Postgres.PGUSER}}
DB_PASSWORD=${{Postgres.PGPASSWORD}}
DB_NAME=${{Postgres.PGDATABASE}}
```

Note: Railway uses `${{Postgres.VARIABLE}}` to reference the PostgreSQL service variables.

### 4.2 Deploy
1. Railway will automatically deploy after you set variables
2. Watch the deployment logs in the "Deployments" tab
3. Once deployed, Railway will provide a public URL (e.g., `your-app-name.up.railway.app`)

## Step 5: Configure Local Scrapers to Use Railway Database

### 5.1 Update Local .env
Edit your local `.env` file to point scrapers to Railway PostgreSQL:

```bash
# Railway PostgreSQL (from Railway dashboard)
DB_TYPE=postgresql
DB_HOST=your-railway-postgres-host.railway.app
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your-railway-password
DB_NAME=railway

# Proxy settings (existing)
PROXY_HOST=gate.smartproxy.com
PROXY_PORT=7000
PROXY_USER=your_username
PROXY_PASS=your_password
```

### 5.2 Update Scrapers for PostgreSQL
Both scrapers need to be updated to support PostgreSQL:

**permit_scraper.py** - Replace MySQL connector:
```python
import os
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()
DB_TYPE = os.getenv('DB_TYPE', 'mysql')

def get_db_connection():
    if DB_TYPE == 'postgresql':
        return psycopg2.connect(
            host=os.getenv('DB_HOST'),
            port=int(os.getenv('DB_PORT', 5432)),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_NAME')
        )
    else:
        import mysql.connector
        return mysql.connector.connect(
            host=os.getenv('DB_HOST'),
            port=int(os.getenv('DB_PORT', 3306)),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_NAME')
        )
```

**add_permit_contacts.py** - Same update needed.

## Step 6: Testing

### 6.1 Test Dashboard
1. Visit your Railway URL: `https://your-app-name.up.railway.app`
2. Verify:
   - Dashboard loads successfully
   - Data displays correctly (1,929+ permits)
   - Filters work
   - Map displays permit locations
   - Lead scoring functions

### 6.2 Test Local Scrapers
1. Run permit scraper locally:
```bash
cd /Users/matthewmakh/PycharmProjects/Smart_Installers/DOB_Permit_Scraper_Streamlit
source venv-permit/bin/activate
python permit_scraper.py
```

2. Check Railway dashboard updates with new permits

3. Run contact scraper locally:
```bash
python add_permit_contacts.py
```

4. Verify contacts appear in Railway dashboard

## Troubleshooting

### Dashboard Won't Start
- Check Railway deployment logs
- Verify all environment variables are set
- Check PostgreSQL connection: `DB_TYPE` must be `postgresql`

### Database Connection Errors
- Verify PostgreSQL service is running in Railway
- Check database credentials in environment variables
- Ensure PostgreSQL port 5432 is accessible

### Local Scrapers Can't Connect
- Update scrapers to support PostgreSQL (see Step 5.2)
- Verify `.env` has correct Railway database credentials
- Test connection: `psql postgresql://user:pass@host:port/database`

### Import Errors
- If `psycopg2` errors occur, Railway should handle this automatically
- For local development, install: `pip install psycopg2-binary`

## Cost Estimates

Railway pricing (as of 2024):
- **PostgreSQL Database**: ~$5/month (Starter plan)
- **Web Service**: $5-10/month (depends on usage)
- **Total**: ~$10-15/month

Free tier: $5 credit/month (may cover small usage)

## Maintenance

### Updating Dashboard
1. Make changes to `dashboard.py` locally
2. Commit and push to GitHub:
   ```bash
   git add dashboard.py
   git commit -m "Update dashboard features"
   git push origin main
   ```
3. Railway auto-deploys from GitHub

### Backing Up Database
```bash
# From Railway dashboard, get connection string
pg_dump "postgresql://user:pass@host:port/database" > backup_$(date +%Y%m%d).sql
```

### Monitoring
- Railway dashboard shows:
  - CPU/Memory usage
  - Request logs
  - Error logs
  - Database metrics

## Security Notes

1. **Never commit `.env` files** - Use `.gitignore`:
   ```
   .env
   __pycache__/
   venv-permit/
   *.pyc
   ```

2. **Use Railway's secret management** for sensitive variables

3. **PostgreSQL is private by default** - Only accessible to your Railway services

4. **HTTPS is automatic** - Railway provides SSL certificates

## Next Steps

After successful deployment:
1. Set up automated scraper scheduling (cron job on local machine)
2. Configure Railway custom domain (optional)
3. Set up monitoring alerts in Railway
4. Consider scaling options if traffic increases

## Support

- Railway Docs: https://docs.railway.app/
- PostgreSQL Docs: https://www.postgresql.org/docs/
- Streamlit Docs: https://docs.streamlit.io/

---

**Questions?** Check Railway dashboard logs first - they show real-time deployment status and errors.
