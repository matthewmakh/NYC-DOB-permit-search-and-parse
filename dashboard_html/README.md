# DOB Permit Dashboard - HTML Version

Modern HTML/CSS/JavaScript dashboard for DOB permit data with Smart Insights features.

## Features

- **Smart Filters**: Quick filter buttons for common scenarios
- **Advanced Filtering**: Date ranges, permit types, status, units, stories
- **Contact Search**: Search permits by contact name or phone number
- **Smart Insights**: 
  - Project value estimation
  - Permit history analysis
  - Contact portfolio tracking
  - Block hotspot detection
- **Lead Scoring**: Automated scoring based on contact quality, recency, and value
- **Interactive Visualizations**: Charts and graphs with Chart.js
- **Map View**: Geographic visualization with Leaflet
- **Responsive Design**: Works on desktop, tablet, and mobile

## Tech Stack

**Frontend:**
- HTML5
- CSS3 (with CSS Grid and Flexbox)
- Vanilla JavaScript (ES6+)
- Chart.js 4.4.0
- Leaflet 1.9.4
- Font Awesome 6.5.1

**Backend:**
- Python 3.12
- Flask 3.0.3
- PostgreSQL with psycopg2
- Flask-CORS for API access

## Installation

### 1. Clone/Navigate to the project
```bash
cd dashboard_html
```

### 2. Create virtual environment
```bash
python3 -m venv venv
source venv/bin/activate  # On macOS/Linux
```

### 3. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Database
Create a `.env` file with your database credentials:
```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=permits_db
DB_USER=postgres
DB_PASSWORD=your_password
```

Or export environment variables:
```bash
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=permits_db
export DB_USER=postgres
export DB_PASSWORD=your_password
```

### 5. Run the Flask API server
```bash
python app.py
```

The dashboard will be available at: **http://localhost:5000**

## Project Structure

```
dashboard_html/
├── app.py                 # Flask API backend
├── requirements.txt       # Python dependencies
├── README.md             # This file
├── static/
│   ├── css/
│   │   └── styles.css    # All styles (dark theme)
│   └── js/
│       └── app.js        # Frontend JavaScript
└── templates/
    └── index.html        # Main HTML template
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main dashboard page |
| `/api/permits` | GET | Get all permits with scores |
| `/api/stats` | GET | Dashboard statistics |
| `/api/search-contact?q=<query>` | GET | Search by contact |
| `/api/permit-types` | GET | Get all permit types |
| `/api/charts/job-types` | GET | Job type distribution |
| `/api/charts/trends` | GET | Permit trends over time |
| `/api/charts/applicants` | GET | Top applicants |
| `/api/map-data` | GET | Geocoded locations |
| `/api/permit/<id>` | GET | Single permit details |
| `/api/health` | GET | Health check |

## Database Schema

The application expects these PostgreSQL tables:

**permits:**
- permit_id (primary key)
- permit_no
- address
- job_type
- issue_date
- exp_date
- applicant
- total_units
- stories
- use_type
- link
- latitude
- longitude

**permit_contacts:**
- id (primary key)
- permit_id (foreign key)
- name
- phone
- phone_type

## Usage

1. **Filter Leads**: Use the sidebar filters to narrow down permits
2. **Smart Filters**: Click quick filter buttons for common scenarios
3. **Search Contacts**: Type a name or phone to find related permits
4. **View Insights**: Expand lead cards to see Smart Insights
5. **Visualize**: Switch to Visualizations tab for charts
6. **Map View**: See geographic distribution on the map

## Development

To modify the dashboard:

- **Styling**: Edit `static/css/styles.css`
- **Functionality**: Edit `static/js/app.js`
- **Layout**: Edit `templates/index.html`
- **API/Database**: Edit `app.py`

## Deployment

For production deployment:

1. Set `debug=False` in `app.py`
2. Use a production WSGI server (gunicorn, uWSGI)
3. Set up proper environment variables
4. Enable HTTPS
5. Configure CORS properly

Example with gunicorn:
```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

## Troubleshooting

**Database connection errors:**
- Verify PostgreSQL is running
- Check database credentials in .env
- Ensure database tables exist

**No data showing:**
- Check browser console for JavaScript errors
- Verify API endpoints return data: `curl http://localhost:5000/api/health`
- Check Flask terminal for Python errors

**Filters not working:**
- Clear browser cache
- Check browser console for errors
- Verify filtered data exists in database

## License

Proprietary - Smart Installers Project

## Contact

For issues or questions, contact the development team.
