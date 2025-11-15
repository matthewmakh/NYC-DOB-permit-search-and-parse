# 🚂 Railway Setup Guide: Building Enrichment Pipeline

**Complete step-by-step instructions to deploy the master enrichment pipeline on Railway.**

---

## 📋 Prerequisites

- ✅ Code pushed to GitHub (`matthewmakh/NYC-DOB-permit-search-and-parse`)
- ✅ Existing Railway project with PostgreSQL database
- ✅ Railway account logged in at https://railway.app

---

## 🎯 What You're Setting Up

**Service Name:** Building-Enrichment-Pipeline  
**Type:** Cron Job  
**Function:** Runs all 4 enrichment steps daily in one execution  
**Execution Time:** ~5 minutes per run  
**Schedule:** Daily at 3:00 AM UTC  

---

## 🛠️ Step-by-Step Setup

### **Step 1: Create New Service**

1. Go to Railway dashboard: https://railway.app
2. Click on your project: **"SINY DOB Leads (Dashboard + DB)"** 
3. Click **"+ Create"** button (top right)
4. Select **"GitHub Repo"**
5. Search for and select: **`NYC-DOB-permit-search-and-parse`**
6. Click **"Add Service"**

**Expected:** New service appears in your project canvas

---

### **Step 2: Rename the Service**

1. Click on the newly created service
2. Click on **"Settings"** tab
3. Under **"Service Name"**, change to: `Building-Enrichment-Pipeline`
4. Changes save automatically

**Expected:** Service now shows as "Building-Enrichment-Pipeline" in project view

---

### **Step 3: Configure Service Type (Cron)**

1. Still in **Settings** tab
2. Scroll to **"Service"** section
3. Look for **"Config File Path"** field
4. Enter: `railway.enrichment.json`
5. Press Enter or click away to save

**Expected:** Railway will automatically redeploy with cron configuration

**What this does:** Tells Railway to use the cron job configuration from `railway.enrichment.json`

---

### **Step 4: Connect Database**

1. Still in the service settings
2. Scroll to **"Variables"** section
3. Click **"+ Add Variable"**
4. Select **"Reference"** → **"Postgres"** → **"DATABASE_URL"**

**Expected:** `DATABASE_URL` variable now appears in the list

**Alternative:** Railway usually auto-connects PostgreSQL services, so you might already see database variables.

---

### **Step 5: Set Environment Variables**

Add these optional performance tuning variables:

1. Click **"+ New Variable"** button
2. Add the following one by one:

| Variable Name | Value | Purpose |
|---------------|-------|---------|
| `BUILDING_BATCH_SIZE` | `500` | Number of buildings per run |
| `API_DELAY` | `0.1` | Seconds between API calls |

**Expected:** Two new variables appear in the Variables section

**Note:** These are optional - the script has sensible defaults.

---

### **Step 6: Set Cron Schedule**

1. In **Settings** tab, scroll to **"Cron Schedule"**
2. In the schedule field, enter: `0 3 * * *`
3. Click **"Update"** or the field will auto-save

**Cron Schedule Explained:**
- `0 3 * * *` = Every day at 3:00 AM UTC
- `0 */6 * * *` = Every 6 hours (alternative)
- `0 3 * * 0` = Every Sunday at 3:00 AM (weekly)

**Expected:** Schedule appears under the service, showing "Next run: [time]"

---

### **Step 7: Verify Deployment**

1. Go to **"Deployments"** tab
2. Wait for deployment to complete (watch the logs)
3. Look for the green "Success" badge

**Expected Deployment Logs:**
```
✅ Build successful
✅ Config file path: railway.enrichment.json
✅ Start command: python run_enrichment_pipeline.py
✅ Restart policy: NEVER
```

---

### **Step 8: Manual Test Run**

Before waiting for the scheduled run, test it manually:

1. In **Deployments** tab
2. Click the **three dots (⋮)** next to latest deployment
3. Select **"Restart"**
4. Click **"View Logs"** to watch execution

**Expected Output in Logs:**
```
======================================================================
🏗️  BUILDING ENRICHMENT PIPELINE
======================================================================
Started at: 2025-11-15 03:00:00

▶ Step 1: Link Permits to Buildings (BBL Generation)
──────────────────────────────────────────────────────────────────────
✅ Completed in X.Xs

▶ Step 2: Enrich from PLUTO + RPAD (Dual-Source)
──────────────────────────────────────────────────────────────────────
[1/N] BBL XXXXXXXXXX
  ✅ PLUTO: [Owner Name]
  ✅ RPAD:  [Taxpayer Name]
✅ Completed in XX.Xs

▶ Step 3: Enrich from ACRIS (Transaction History)
──────────────────────────────────────────────────────────────────────
✅ Completed in XX.Xs

▶ Step 4: Geocode Permits (Latitude/Longitude)
──────────────────────────────────────────────────────────────────────
✅ Successfully geocoded: X
✅ Completed in XX.Xs

======================================================================
📊 PIPELINE SUMMARY
======================================================================
✅ PIPELINE COMPLETED SUCCESSFULLY
```

**Troubleshooting:** If you see errors, check the "Common Issues" section below.

---

### **Step 9: Monitor Scheduled Runs**

After setup, the cron will run automatically:

1. Check **"Deployments"** tab at scheduled time
2. New deployment appears with execution logs
3. Verify success in logs

**Pro Tip:** Set up Railway notifications:
- Settings → Notifications → Enable "Deployment notifications"

---

## 🔍 Verification Checklist

After setup, verify everything is working:

- [ ] Service appears in Railway project
- [ ] Service named "Building-Enrichment-Pipeline"
- [ ] Config file path set to `railway.enrichment.json`
- [ ] `DATABASE_URL` variable exists
- [ ] Cron schedule set (e.g., `0 3 * * *`)
- [ ] Manual test run completed successfully
- [ ] All 4 steps show ✅ in logs
- [ ] Next scheduled run shows correct time

---

## 🎛️ Railway Configuration Summary

Here's what your final configuration should look like:

### Service Settings:
```
Service Name: Building-Enrichment-Pipeline
Repository: matthewmakh/NYC-DOB-permit-search-and-parse
Branch: main
Config File Path: railway.enrichment.json
```

### Environment Variables:
```
DATABASE_URL: ${{Postgres.DATABASE_URL}}  (auto-connected)
BUILDING_BATCH_SIZE: 500  (optional)
API_DELAY: 0.1  (optional)
```

### Cron Settings:
```
Schedule: 0 3 * * *  (daily at 3 AM UTC)
Restart Policy: NEVER  (from railway.enrichment.json)
Start Command: python run_enrichment_pipeline.py  (from railway.enrichment.json)
```

---

## ⚙️ What Happens During Execution?

### Execution Flow:
1. **Railway triggers cron** at scheduled time (3 AM UTC)
2. **Clones latest code** from GitHub main branch
3. **Installs dependencies** from requirements.txt
4. **Runs:** `python run_enrichment_pipeline.py`
5. **Pipeline executes all 4 steps** sequentially
6. **Logs output** to Railway dashboard
7. **Exits** when complete (restart policy: NEVER)

### Resource Usage:
- **CPU:** Minimal (mostly waiting on API responses)
- **Memory:** ~200-500 MB
- **Duration:** ~5 minutes
- **Network:** ~1,000 API calls per run

---

## 🐛 Common Issues & Solutions

### Issue: "Config file not found"
**Solution:** 
- Verify config file path is exactly: `railway.enrichment.json`
- Check that file exists in GitHub repo root
- Try redeploying

### Issue: "DATABASE_URL not set"
**Solution:**
- Go to Settings → Variables
- Add reference to Postgres.DATABASE_URL
- Or manually set DATABASE_URL if not using Railway Postgres

### Issue: "Module not found" errors
**Solution:**
- Check that `requirements.txt` includes all dependencies
- Railway should auto-install from requirements.txt
- Try manual redeploy

### Issue: Pipeline never runs
**Solution:**
- Verify cron schedule syntax: `0 3 * * *`
- Check Railway service is not paused
- Look for "Next run" time in Deployments tab

### Issue: Steps timeout
**Solution:**
- Increase batch size to process more at once
- Or decrease batch size if hitting memory limits
- Check Railway service plan limits

### Issue: API rate limit errors
**Solution:**
- Increase `API_DELAY` to 0.2 or 0.5
- Decrease `BUILDING_BATCH_SIZE`
- Errors are usually temporary, retry works

---

## 🔄 Updating the Pipeline

When you push code changes to GitHub:

1. **Railway auto-deploys** from main branch
2. **Next cron run** uses updated code
3. **No manual intervention** needed

**To force immediate update:**
1. Go to Deployments tab
2. Click three dots → "Redeploy"
3. Updated code runs immediately

---

## 📊 Monitoring & Logs

### View Execution Logs:
1. Click on **Building-Enrichment-Pipeline** service
2. Go to **Deployments** tab
3. Click on any deployment
4. Click **"View Logs"**

### What to Look For:
- ✅ Green "SUCCESS" badges
- ✅ "PIPELINE COMPLETED SUCCESSFULLY" message
- ⚠️ Yellow warnings (non-critical failures)
- ❌ Red errors (critical failures)

### Success Indicators:
```
Step 1: ✅ SUCCESS (X buildings created/updated)
Step 2: ✅ SUCCESS (X buildings enriched)
Step 3: ✅ SUCCESS (X with transactions)
Step 4: ✅ SUCCESS (X geocoded)
```

---

## 🎯 Next Steps After Setup

1. **Wait for first scheduled run** (3 AM UTC)
2. **Check logs** to verify success
3. **Monitor dashboard** at leads.installersny.com
4. **Verify data appears** in Permit View and Building View
5. **Adjust cron schedule** if needed (more/less frequent)

---

## 🔗 Related Services

Your complete Railway setup should include:

| Service | Type | Purpose |
|---------|------|---------|
| **NYC-DOB-permit-search-and-parse** | Web App | Dashboard (leads.installersny.com) |
| **Postgres** | Database | Data storage |
| **Building-Enrichment-Pipeline** | Cron | **NEW** - Master enrichment (this guide) |
| **Geocode-Properties-CRON** | Cron | Optional - Standalone geocoding |
| **Mobile-Number-Check-CRON** | Cron | Phone validation |

**Note:** You can disable/remove the old separate enrichment services and keep just the master pipeline.

---

## 💡 Tips & Best Practices

### Performance Tuning:
- **Large dataset?** Increase `BUILDING_BATCH_SIZE` to 1000
- **Rate limit errors?** Increase `API_DELAY` to 0.2
- **Memory issues?** Decrease `BUILDING_BATCH_SIZE` to 250

### Scheduling:
- **Production:** Daily at 3 AM (`0 3 * * *`)
- **Testing:** Every 6 hours (`0 */6 * * *`)
- **Light usage:** Weekly (`0 3 * * 0`)

### Cost Optimization:
- Master pipeline = 1 service (cheaper than 4 separate)
- Cron jobs only use resources during execution
- ~5 minutes runtime = minimal cost

### Monitoring:
- Check logs after first few runs
- Set up deployment notifications
- Monitor success rates in DATA_QUALITY_GUIDE.md

---

## ✅ Setup Complete!

Your master enrichment pipeline is now:
- ✅ Deployed to Railway
- ✅ Scheduled to run automatically
- ✅ Connected to your database
- ✅ Monitoring data quality
- ✅ Ready for production

**Next scheduled run:** Check Railway Deployments tab for countdown

**Questions?** Check:
- `ENRICHMENT_PIPELINE_GUIDE.md` - Pipeline details
- `DATA_QUALITY_GUIDE.md` - Success rates & retry logic
- `SYSTEM_ARCHITECTURE.md` - Complete system overview

---

**Deployed by:** matthewmakh  
**Repository:** https://github.com/matthewmakh/NYC-DOB-permit-search-and-parse  
**Commit:** f295497 (Master enrichment pipeline + ACRIS fix)
