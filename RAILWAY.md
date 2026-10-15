# 🚂 Railway Deployment Guide

**Quick reference for deploying and managing services on Railway.**

---

## 🎯 Current Setup

### Active Services

| Service | Type | Config File | Purpose |
|---------|------|-------------|---------|
| **DOB-Dashboard** | Web | `dashboard_html/railway.json` | Dashboard at leads.installersny.com |
| **Postgres** | Database | - | PostgreSQL database |
| **Building-Enrichment-Pipeline** | Cron | `railway.enrichment.json` | Runs all enrichment steps daily |

---

## 🚀 Deploy Enrichment Pipeline

### Option 1: Replace Existing Geocode Service (Easiest)

1. Railway → Click `Geocode-Properties-CRON`
2. **Settings** tab
3. **Config File Path**: Change to `railway.enrichment.json`
4. Done! ✅

**What changed:** Service now runs full 4-step pipeline instead of just geocoding.

### Option 2: Create New Service

1. Railway → **"+ Create"** → **"GitHub Repo"**
2. Select: `NYC-DOB-permit-search-and-parse`
3. Click service → **Settings**:
   - **Service Name**: `Building-Enrichment-Pipeline`
   - **Config File Path**: `railway.enrichment.json`
   - **Cron Schedule**: `0 3 * * *`
4. **Variables**: Add reference to `Postgres.DATABASE_URL`
5. Done! ✅

---

## ⚙️ Configuration

### Environment Variables

**Required (auto-connected):**
- `DATABASE_URL` - Links to PostgreSQL service

**Optional (performance tuning):**
- `BUILDING_BATCH_SIZE=500` - Buildings per run
- `API_DELAY=0.1` - Seconds between API calls
- `NYC_GEOCLIENT_APP_ID` - For geocoding (get free at developer.cityofnewyork.us)
- `NYC_GEOCLIENT_APP_KEY` - For geocoding

### Cron Schedules

| Schedule | Meaning |
|----------|---------|
| `0 3 * * *` | Daily at 3:00 AM UTC |
| `0 */6 * * *` | Every 6 hours |
| `0 3 * * 0` | Weekly (Sunday 3 AM) |

---

## 📋 Testing & Monitoring

### Manual Test Run

1. Service → **Deployments** tab
2. Click **⋮** (three dots) → **"Restart"**
3. **View Logs**

### Expected Output

```
======================================================================
🏗️  BUILDING ENRICHMENT PIPELINE
======================================================================

▶ Step 1: Link Permits to Buildings
✅ Completed in X.Xs

▶ Step 2: Enrich from PLUTO + RPAD
✅ PLUTO data retrieved: X
✅ RPAD data retrieved: X
✅ Completed in XX.Xs

▶ Step 3: Enrich from ACRIS
✅ Buildings enriched: X
✅ Completed in XX.Xs

▶ Step 4: Geocode Permits
✅ Successfully geocoded: X
✅ Completed in XX.Xs

======================================================================
✅ PIPELINE COMPLETED SUCCESSFULLY
======================================================================
```

### Check Next Run

- **Deployments** tab shows countdown: "Next in X hours"
- Or check "Cron Schedule" in Settings

---

## 🐛 Common Issues

### "Config file not found"
- Verify path is exactly: `railway.enrichment.json`
- Check file exists in GitHub repo root
- Try redeploying

### "DATABASE_URL not set"
- Settings → Variables → Add reference to `Postgres.DATABASE_URL`

### "Pipeline never runs"
- Verify cron schedule: `0 3 * * *`
- Check service isn't paused
- Look for "Next run" in Deployments

### "Steps timeout"
- Decrease `BUILDING_BATCH_SIZE` to 250
- Or increase Railway plan limits

### "API rate limit errors"
- Increase `API_DELAY` to 0.2 or 0.5

---

## 🔄 Updates

### Automatic Deployment

When you push to GitHub:
1. Railway auto-detects changes
2. Builds and deploys automatically
3. Next cron run uses updated code

### Force Immediate Update

1. **Deployments** tab
2. Three dots → **"Redeploy"**
3. Changes apply immediately

---

## 📊 What Each Service Does

### Dashboard Service (NYC-DOB-permit-search-and-parse)
- Runs Flask web app
- Serves leads.installersny.com
- Always running
- Auto-deploys from GitHub

### Database Service (Postgres)
- Managed PostgreSQL
- Stores permits and buildings data
- Auto-backup enabled
- Always running

### Enrichment Pipeline (Building-Enrichment-Pipeline)
- Runs 4 enrichment steps sequentially
- Scheduled via cron (daily at 3 AM)
- Execution time: ~5 minutes
- Auto-retries failed records

---

## ✅ Verification Checklist

After deploying enrichment pipeline:

- [ ] Service appears in Railway project
- [ ] Config file path: `railway.enrichment.json`
- [ ] `DATABASE_URL` variable connected
- [ ] Cron schedule set: `0 3 * * *`
- [ ] Manual test run completed successfully
- [ ] All 4 steps show ✅ in logs
- [ ] "Next run" countdown visible
- [ ] Dashboard shows enriched data at leads.installersny.com

---

## 🎯 Quick Commands

### View Service Logs
Railway → Service → Deployments → Click deployment → View Logs

### Trigger Manual Run
Railway → Service → Deployments → ⋮ → Restart

### Update Environment Variable
Railway → Service → Settings → Variables → + New Variable

### Change Cron Schedule
Railway → Service → Settings → Cron Schedule → Enter new schedule

---

## 💡 Tips

- **First deployment:** Takes 2-3 minutes to build
- **Subsequent deploys:** ~1 minute (cached)
- **Cron jobs:** Only use resources during execution
- **Logs:** Kept for 7 days
- **Auto-deploy:** Push to GitHub = automatic deployment
- **Cost:** Master pipeline = 1 service (cheaper than 4 separate services)

---

**Need More Help?** See `README.md` for complete documentation.
