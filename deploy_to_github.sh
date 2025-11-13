#!/bin/bash

# Railway Deployment - Commit and Push Script

echo "======================================"
echo "Railway Deployment - Git Commit"
echo "======================================"
echo ""

# Navigate to project directory
cd /Users/matthewmakh/PycharmProjects/Smart_Installers/DOB_Permit_Scraper_Streamlit

echo "Step 1: Adding Railway deployment files..."
git add Procfile runtime.txt railway.json
git add RAILWAY_DEPLOYMENT.md DEPLOYMENT_READY.md
git add .env.example .gitignore

echo "Step 2: Adding updated dashboard with PostgreSQL support..."
git add dashboard.py requirements.txt

echo "Step 3: Adding database migration files..."
git add postgres_schema.sql postgres_data.sql export_to_postgres.py

echo "Step 4: Adding updated scrapers (optional)..."
git add add_permit_contacts.py permit_scraper.py

echo "Step 5: Adding documentation..."
git add FILTERS_IMPLEMENTED.md

echo ""
echo "======================================"
echo "Files staged for commit:"
echo "======================================"
git status --short

echo ""
echo "======================================"
echo "Committing changes..."
echo "======================================"
git commit -m "Add Railway deployment support with PostgreSQL

- Update dashboard.py to support both MySQL and PostgreSQL
- Add Railway deployment files (Procfile, runtime.txt, railway.json)
- Add comprehensive deployment guide (RAILWAY_DEPLOYMENT.md)
- Export database to PostgreSQL format (postgres_schema.sql, postgres_data.sql)
- Update requirements.txt with psycopg2-binary
- Add .env.example template and .gitignore
- Update scrapers with dynamic Chrome detection and proxy fixes

Ready for Railway deployment from GitHub repo."

echo ""
echo "======================================"
echo "Pushing to GitHub..."
echo "======================================"
git push origin main

echo ""
echo "======================================"
echo "✅ DEPLOYMENT FILES PUSHED TO GITHUB!"
echo "======================================"
echo ""
echo "Next steps:"
echo "1. Go to https://railway.app/"
echo "2. Create new project from GitHub repo"
echo "3. Add PostgreSQL database service"
echo "4. Follow RAILWAY_DEPLOYMENT.md for detailed instructions"
echo ""
echo "Repository: https://github.com/matthewmakh/NYC-DOB-permit-search-and-parse"
echo "======================================"
