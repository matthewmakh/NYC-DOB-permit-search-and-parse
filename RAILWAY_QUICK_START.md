# Quick Railway Setup Steps

## ✅ COMPLETED: GitHub Branch Setup
- ✅ Created `html-dashboard` branch
- ✅ Added all dashboard files
- ✅ Committed changes
- ✅ Pushed to GitHub

**Your new branch is live at:**
https://github.com/matthewmakh/NYC-DOB-permit-search-and-parse/tree/html-dashboard

---

## 🚀 Next Steps: Deploy to Railway

### Step 1: Go to Railway
Visit: https://railway.app

### Step 2: Create New Project
1. Click **"New Project"**
2. Select **"Deploy from GitHub repo"**
3. Authenticate with GitHub if prompted

### Step 3: Configure Deployment
1. **Select Repository**: `NYC-DOB-permit-search-and-parse`
2. **Select Branch**: `html-dashboard`
3. **Set Root Directory**: `dashboard_html`

### Step 4: Add Environment Variables
Click on your service → Variables tab → Add these:

```
DB_HOST=maglev.proxy.rlwy.net
DB_PORT=26571
DB_NAME=railway
DB_USER=postgres
DB_PASSWORD=your-existing-railway-db-password
FLASK_ENV=production
```

**Get your existing database credentials:**
- Go to your existing Railway database service
- Click on it
- Go to "Variables" tab
- Copy: `DATABASE_URL` or individual variables

### Step 5: Deploy!
Railway will automatically:
- ✅ Detect Python project
- ✅ Install dependencies from `requirements.txt`
- ✅ Use the `Procfile` to start gunicorn
- ✅ Assign a public URL

### Step 6: Enable Auto-Deployment
1. Go to Settings → GitHub
2. Enable **"Deploy on push"**
3. Branch: `html-dashboard` (already selected)

Now every time you push to `html-dashboard`, Railway will automatically redeploy! 🎯

---

## 📝 Important Notes

### Your Repository Structure
```
main branch               → Streamlit version (original)
html-dashboard branch     → Flask/HTML version (new)
```

Both can coexist! They're completely separate.

### Updating the Dashboard
```bash
# Switch to dashboard branch
git checkout html-dashboard

# Make your changes in dashboard_html/

# Commit and push
git add dashboard_html/
git commit -m "Update feature"
git push origin html-dashboard

# Railway will auto-deploy!
```

### Switching Back to Main
```bash
git checkout main
```

Your main branch is untouched and still has your Streamlit app!

---

## 🔍 Verification Checklist

After Railway deploys, check:

1. **Homepage loads**: Visit your Railway URL
2. **Health check**: `https://your-app.railway.app/api/health`
3. **API works**: `https://your-app.railway.app/api/permits`
4. **Dashboard functions**:
   - ✅ Stats cards show data
   - ✅ Filters work
   - ✅ Smart filters work
   - ✅ Lead cards expand/collapse
   - ✅ Search works
   - ✅ Pagination works

---

## 🎨 What's Deployed

Your beautiful HTML dashboard with:
- ✨ Modern gradient design
- 🎯 8 smart filters
- 📊 Live statistics
- 🔍 Global search
- 📱 Contact management
- 💡 Smart insights
- 🎭 Smooth animations
- 📈 Expandable lead cards

---

## 🆘 Troubleshooting

**If deployment fails:**
1. Check Railway logs (Deployments → Click deployment → View logs)
2. Verify environment variables are set
3. Ensure database is accessible
4. Check that all files are in the branch

**If database won't connect:**
1. Verify `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
2. Make sure database is in the same Railway project
3. Check database is running (not sleeping)

**If app starts but doesn't work:**
1. Check browser console for errors (F12)
2. Test API endpoints directly: `/api/health`, `/api/permits`
3. Verify static files are loading (CSS/JS)

---

## 📚 Full Documentation

See `RAILWAY_DEPLOY_GUIDE.md` for complete documentation including:
- Detailed Railway configuration
- Environment variable reference
- Monitoring and logging
- Custom domain setup
- Advanced troubleshooting

---

## 🎉 You're All Set!

Your HTML dashboard is now:
- ✅ In a separate Git branch (`html-dashboard`)
- ✅ Pushed to GitHub
- ✅ Ready to deploy to Railway
- ✅ Configured for auto-deployment

**Next:** Go to Railway and follow Step 1-6 above to deploy! 🚀
