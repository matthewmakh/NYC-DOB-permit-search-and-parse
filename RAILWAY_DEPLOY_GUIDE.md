# Railway Deployment Guide

## Overview
This guide will help you deploy the HTML Dashboard to Railway using GitHub for continuous deployment.

## Prerequisites
- GitHub account
- Railway account (sign up at https://railway.app)
- This repository pushed to GitHub

## Step 1: Push Dashboard to GitHub (html-dashboard branch)

### Create and switch to a new branch:
```bash
cd /Users/matthewmakh/PycharmProjects/Smart_Installers/DOB_Permit_Scraper_Streamlit
git checkout -b html-dashboard
```

### Add dashboard files:
```bash
git add dashboard_html/
git add RAILWAY_DEPLOY_GUIDE.md
git commit -m "Add HTML dashboard for Railway deployment"
```

### Push to GitHub:
```bash
git push origin html-dashboard
```

## Step 2: Set Up Railway Project

### Option A: Deploy via Railway Dashboard (Recommended)
1. Go to https://railway.app
2. Click "New Project"
3. Select "Deploy from GitHub repo"
4. Authenticate with GitHub if needed
5. Select your repository: `NYC-DOB-permit-search-and-parse`
6. Select branch: `html-dashboard`
7. Set root directory: `dashboard_html`

### Option B: Deploy via Railway CLI
```bash
# Install Railway CLI
npm i -g @railway/cli

# Login to Railway
railway login

# Initialize project
cd dashboard_html
railway init

# Deploy
railway up
```

## Step 3: Configure Environment Variables

In your Railway project dashboard, go to **Variables** tab and add:

```
DB_HOST=your-railway-postgres-host
DB_PORT=26571
DB_NAME=railway
DB_USER=postgres
DB_PASSWORD=your-railway-postgres-password
FLASK_ENV=production
```

**To use your existing Railway database:**
1. Find your existing database service in Railway
2. Click on it and go to "Variables" tab
3. Copy the connection details
4. Paste them into your new dashboard service

**Or create a new database:**
1. In your Railway project, click "New Service"
2. Select "PostgreSQL"
3. Railway will automatically add the database connection variables

## Step 4: Configure Deployment Settings

### Set the Start Command
Railway should auto-detect the Procfile, but verify:
- Go to Settings → Deploy
- Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`

### Set Root Directory
- Go to Settings → Deploy
- Root Directory: `dashboard_html`
- Build Command: `pip install -r requirements.txt`

### Set Python Version
- Railway will use the `runtime.txt` file
- Python 3.12.0 is specified

## Step 5: Enable Continuous Deployment

1. Go to Settings → GitHub
2. Enable "Deploy on push"
3. Select branch: `html-dashboard`
4. Railway will now automatically deploy when you push to this branch

## Step 6: Get Your Deployment URL

1. Once deployed, Railway will provide a URL like:
   - `https://your-app.railway.app`
2. You can also set a custom domain:
   - Go to Settings → Domains
   - Click "Generate Domain" or "Add Custom Domain"

## Step 7: Verify Deployment

Visit your Railway URL and check:
- ✅ Dashboard loads
- ✅ Database connection works (check /api/health endpoint)
- ✅ Filters work
- ✅ Lead cards expand/collapse
- ✅ Stats display correctly

## Project Structure

```
dashboard_html/
├── app.py                  # Flask backend
├── requirements.txt        # Python dependencies
├── Procfile               # Railway process file
├── railway.json           # Railway configuration
├── runtime.txt            # Python version
├── .env.example           # Environment variables template
├── templates/
│   └── index.html         # Frontend HTML
├── static/
│   ├── css/
│   │   └── styles.css     # Styles
│   └── js/
│       └── app.js         # JavaScript
└── README.md              # Documentation
```

## Environment Variables Reference

| Variable | Description | Example |
|----------|-------------|---------|
| `DB_HOST` | PostgreSQL host | `maglev.proxy.rlwy.net` |
| `DB_PORT` | PostgreSQL port | `26571` |
| `DB_NAME` | Database name | `railway` |
| `DB_USER` | Database user | `postgres` |
| `DB_PASSWORD` | Database password | `your-password` |
| `FLASK_ENV` | Flask environment | `production` |
| `PORT` | Application port (auto-set by Railway) | `8000` |

## Updating Your Deployment

After making changes locally:

```bash
# Make sure you're on the html-dashboard branch
git checkout html-dashboard

# Stage your changes
git add dashboard_html/

# Commit changes
git commit -m "Update dashboard features"

# Push to GitHub
git push origin html-dashboard
```

Railway will automatically detect the push and redeploy! 🚀

## Monitoring

### View Logs
1. Go to your Railway project
2. Click on your service
3. Go to "Deployments" tab
4. Click on the active deployment
5. View real-time logs

### Check Health
Visit: `https://your-app.railway.app/api/health`

Should return:
```json
{
  "success": true,
  "status": "healthy",
  "database": "connected"
}
```

## Troubleshooting

### Database Connection Issues
- Verify environment variables are set correctly
- Check that database service is running
- Ensure database is in the same Railway project

### Build Failures
- Check logs in Railway dashboard
- Verify all files are committed to Git
- Ensure requirements.txt includes all dependencies

### Application Not Starting
- Check that Procfile is correct
- Verify gunicorn is in requirements.txt
- Check logs for Python errors

### Port Issues
- Railway automatically sets the PORT variable
- Ensure app.py reads from `os.getenv('PORT')`

## Benefits of This Setup

✅ **Automatic Deployments**: Push to GitHub → Railway deploys automatically
✅ **Branch Isolation**: HTML dashboard is separate from Streamlit version
✅ **Same Repository**: All project code in one place
✅ **Easy Rollback**: Railway keeps deployment history
✅ **Environment Management**: Separate dev/prod configurations
✅ **Shared Database**: Can use the same Railway PostgreSQL database

## Keeping Main Branch Separate

Your repository structure:
- **main branch**: Original Streamlit dashboard and scraper
- **html-dashboard branch**: New HTML/Flask dashboard

They share the database but are deployed independently!

## Next Steps

1. Test the deployment thoroughly
2. Set up a custom domain (optional)
3. Configure monitoring/alerts in Railway
4. Set up staging environment (optional)

## Support

- Railway Docs: https://docs.railway.app
- Railway Discord: https://discord.gg/railway
- GitHub Issues: Report in your repository
