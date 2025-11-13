# 🚀 Railway Deployment Configuration

This directory contains the production-ready HTML dashboard configured for Railway deployment.

## Deployment Files

- **`Procfile`** - Tells Railway how to start the app using gunicorn
- **`railway.json`** - Railway-specific configuration
- **`runtime.txt`** - Specifies Python 3.12.0
- **`requirements.txt`** - All Python dependencies including gunicorn
- **`.env.example`** - Template for environment variables

## Railway Setup

### Root Directory Setting
Set this in Railway Settings → Deploy:
```
dashboard_html
```

### Start Command (from Procfile)
```
gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

### Required Environment Variables
```
DB_HOST=your-railway-postgres-host
DB_PORT=26571
DB_NAME=railway
DB_USER=postgres
DB_PASSWORD=your-password
FLASK_ENV=production
```

## Auto-Deployment

Railway will automatically deploy when you push to the `html-dashboard` branch.

```bash
git checkout html-dashboard
# make changes...
git add .
git commit -m "Update dashboard"
git push origin html-dashboard
# Railway deploys automatically! 🎉
```

## Local Development

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure .env
cp .env.example .env
# Edit .env with your database credentials

# Run locally
python app.py

# Visit http://localhost:5001
```

## Production Notes

- **Debug mode**: Automatically disabled when `FLASK_ENV=production`
- **Port**: Automatically read from Railway's `$PORT` environment variable
- **Workers**: 2 gunicorn workers for better performance
- **Timeout**: 120 seconds for long-running queries
- **CORS**: Enabled for development, configure for production if needed

## Health Check

Railway can monitor your app health:
- Endpoint: `/api/health`
- Should return: `{"success": true, "status": "healthy", "database": "connected"}`

## Troubleshooting

**Check logs in Railway:**
1. Go to your service
2. Click "Deployments"
3. Click the active deployment
4. View logs in real-time

**Common issues:**
- Database connection: Verify environment variables
- Port binding: Railway sets `$PORT` automatically
- Static files: Ensure `static/` folder is committed to Git

## Documentation

See parent directory for:
- `RAILWAY_QUICK_START.md` - Quick setup steps
- `RAILWAY_DEPLOY_GUIDE.md` - Complete deployment guide

## Support

- Railway Docs: https://docs.railway.app
- GitHub Repo: https://github.com/matthewmakh/NYC-DOB-permit-search-and-parse
