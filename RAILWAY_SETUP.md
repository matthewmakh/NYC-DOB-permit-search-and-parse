# Railway Deployment Guide

## Files Created:
1. ✅ `postgres_schema.sql` - PostgreSQL table definitions
2. ✅ `postgres_data.sql` - All your data (2025 rows exported)
3. 📝 This guide

## Step-by-Step Deployment:

### 1. Create Railway Account & Project
- Go to https://railway.app
- Sign up with GitHub
- Click "New Project"

### 2. Add PostgreSQL Database
- In your project, click "+ New"
- Select "Database" → "PostgreSQL"
- Railway will create and provision the database

### 3. Get Database Connection Info
- Click on the PostgreSQL service
- Go to "Connect" tab
- Copy the connection details (you'll need these)

### 4. Import Schema & Data

**Option A: Using Railway's psql (easiest)**
- In PostgreSQL service, click "Data" tab
- Click "Connect via psql"
- Copy/paste the contents of `postgres_schema.sql`
- Then copy/paste the contents of `postgres_data.sql`

**Option B: Using local psql**
```bash
# Get connection string from Railway (looks like this)
# postgresql://postgres:password@host.railway.app:5432/railway

# Run schema first
psql "your-railway-connection-string" < postgres_schema.sql

# Then load data
psql "your-railway-connection-string" < postgres_data.sql
```

### 5. Deploy Streamlit Dashboard
- In Railway project, click "+ New" → "GitHub Repo"
- Connect your `NYC-DOB-permit-search-and-parse` repo
- Railway will auto-detect Python

### 6. Configure Environment Variables
In the Streamlit service, add these variables:
```
DB_HOST=<from railway postgres>
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=<from railway postgres>
DB_NAME=railway
PORT=8080
```

### 7. Add Deployment Files

Create these files in your repo:

**`Procfile`:**
```
web: streamlit run dashboard.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true
```

**`runtime.txt`:**
```
python-3.12
```

**`railway.json`:**
```json
{
  "build": {
    "builder": "NIXPACKS"
  },
  "deploy": {
    "startCommand": "streamlit run dashboard.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true"
  }
}
```

### 8. Update requirements.txt
Add psycopg2 for PostgreSQL:
```
psycopg2-binary==2.9.9
```

### 9. Update Code to Use PostgreSQL

I'll help you update the dashboard.py to use psycopg2 instead of mysql.connector.

## Costs:
- PostgreSQL: ~$5/month (Hobby plan)
- Streamlit Dashboard: ~$5/month (Hobby plan)
- **Total: ~$10/month**

## Next Steps:
Ready to proceed? I can:
1. Create the Procfile, runtime.txt, and railway.json files
2. Update your dashboard.py to use PostgreSQL
3. Update requirements.txt

Let me know when you're ready!