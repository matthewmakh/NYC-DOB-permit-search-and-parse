# 🎯 HTML Dashboard - Quick Start Guide

## 📂 Project Structure
```
dashboard_html/
├── app.py                    # Flask API backend
├── requirements.txt          # Python dependencies
├── start.sh                  # Quick start script
├── .env.example             # Environment template
├── README.md                # Full documentation
├── static/
│   ├── css/
│   │   └── styles.css       # ~650 lines - Dark theme styling
│   └── js/
│       └── app.js           # ~850 lines - All functionality
└── templates/
    └── index.html           # ~280 lines - Dashboard structure
```

## 🚀 Quick Start

### Option 1: Using the startup script (Recommended)
```bash
cd dashboard_html
./start.sh
```

### Option 2: Manual setup
```bash
cd dashboard_html

# 1. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure database (copy and edit)
cp .env.example .env
# Edit .env with your database credentials

# 4. Run the server
python app.py
```

## 🌐 Access Dashboard
Open browser to: **http://localhost:5000**

## ✨ Features Implemented

### Frontend (HTML/CSS/JS)
✅ Dark theme with modern card-based design
✅ Responsive layout (mobile, tablet, desktop)
✅ Fixed sidebar with all filters
✅ Smart filter buttons (8 types)
✅ Contact search with live results
✅ Global search bar
✅ Lead cards with expandable details
✅ Smart Insights in each lead
✅ Lead scoring system (Hot/Warm/Cold)
✅ Stats cards (Total Leads, Contacts, Avg Score, etc.)
✅ Pagination with controls
✅ Three tabs: Leads, Visualizations, Map
✅ Chart.js integration for visualizations
✅ Leaflet maps for geographic view
✅ Loading overlay
✅ Smooth animations and transitions

### Backend (Flask API)
✅ PostgreSQL database connection
✅ Lead scoring calculation
✅ Contact aggregation
✅ Search functionality
✅ Chart data endpoints
✅ Map data with geocoding
✅ Statistics calculation
✅ CORS enabled for development
✅ Error handling
✅ Health check endpoint

## 📊 Comparison with Streamlit Version

| Feature | Streamlit | HTML |
|---------|-----------|------|
| Smart Insights | ✅ | ✅ |
| Contact Search | ✅ | ✅ |
| Smart Filters | ✅ | ✅ |
| Lead Scoring | ✅ | ✅ |
| Visualizations | ✅ | ✅ |
| Map View | ✅ | ✅ |
| Responsive Design | ⚠️ Limited | ✅ Full |
| Performance | ⚠️ Slower | ✅ Faster |
| Customization | ⚠️ Limited | ✅ Full Control |
| Deployment | Easy | Moderate |

## 🎨 Design Features

**Color Scheme (Dark Theme):**
- Background: `#1a1a2e` (Dark navy)
- Cards: `#16213e` (Slightly lighter)
- Primary: `#4a9eff` (Blue)
- Success: `#28a745` (Green)
- Warning: `#ffc107` (Yellow)
- Danger: `#dc3545` (Red)

**Typography:**
- Font: Inter, system fonts fallback
- Size: 14px base with responsive scaling

**Layout:**
- Fixed 320px sidebar
- Fluid main content area
- CSS Grid for stats and charts
- Flexbox for lead cards

## 🔧 Customization

### Change Colors
Edit `static/css/styles.css` - CSS variables at top:
```css
:root {
    --primary-color: #4a9eff;  /* Change this */
    --dark-bg: #1a1a2e;        /* Or this */
    /* etc. */
}
```

### Modify Filters
Edit `static/js/app.js` - `applyFilters()` function

### Add New Charts
Edit `static/js/app.js` - `loadVisualizations()` function

### Adjust API Endpoints
Edit `app.py` - Add new Flask routes

## 📋 Database Requirements

Tables needed:
- `permits` - Main permit data
- `permit_contacts` - Contact information

See `README.md` for full schema.

## 🐛 Troubleshooting

**Import errors in app.py:**
- Normal - Flask not installed yet
- Will be installed when you run `pip install -r requirements.txt`

**Database connection failed:**
- Check PostgreSQL is running
- Verify credentials in `.env`
- Test with: `curl http://localhost:5000/api/health`

**No data showing:**
- Open browser console (F12)
- Check for JavaScript errors
- Verify API returns data: `curl http://localhost:5000/api/permits`

**Filters not working:**
- Clear browser cache
- Hard refresh (Cmd+Shift+R on Mac)

## 📝 Next Steps

1. Configure database connection
2. Run `./start.sh` or manual setup
3. Test at http://localhost:5000
4. Customize colors/layout as needed
5. Deploy to production server

## 🎯 API Testing

Test the API endpoints:
```bash
# Health check
curl http://localhost:5000/api/health

# Get permits
curl http://localhost:5000/api/permits

# Search contact
curl http://localhost:5000/api/search-contact?q=john

# Get stats
curl http://localhost:5000/api/stats
```

## 📚 Documentation

Full documentation: `README.md`
API details: See `app.py` docstrings

---

**Status:** ✅ Complete and ready to test
**Total Lines:** ~1,800 lines of code
**Created:** November 2025
