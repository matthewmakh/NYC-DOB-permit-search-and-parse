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

## Ownership source dates

The building profile keeps each source's owner name paired with its evidence:
ACRIS uses the latest recorded deed, HPD uses the latest processed registration
(not its expiration date), RPAD selects the latest fiscal year and prefers the
final roll over the tentative roll, and ECB uses the latest dated violation
that names a respondent. An ECB respondent is not necessarily an owner.

PLUTO reports its release version; the SOS contact response does not provide a
contact-report date. Neither the app's refresh time nor the entity's formation
date is presented as an owner-report date. The UI shows “Last checked” separately.

`migrate_enrichment_reliability.py` and the dashboard's existing startup migration
add the source-date columns. Existing PLUTO/RPAD/HPD checkpoints become due once
so the next scheduled refresh fills their metadata; failed refreshes retain the
last successful names and dates and obey the normal retry delay. ECB metadata is
filled by the next successful tax/violation refresh. No production backfill runs
as part of the schema migration.

## Reported addresses and enrichment privacy

Address-bearing property/profile APIs require an active authenticated account
and send `Cache-Control: private, no-store`. Older building endpoints exclude
globally cached paid enrichment fields; paid contacts remain user-specific.

CSV owner addresses come only from the source record that supplied the exported
owner name: the primary ACRIS deed's matching grantee, or that owner's HPD
registration contact. Missing or conflicting pairs export a blank address.
Selecting the address also includes owner name, address source and reported date.
SOS agents and ECB respondents are never substituted for the exported owner.
Owner phone/email exports also require a matching owner name in that user's unlocks.

HPD keeps each owner's name, role, registration/contact IDs, reported date and
business address in `hpd_owner_contacts`. Its legacy single-address fields are
populated only for a single contact. Existing HPD checkpoints refresh once to
populate these pairs, with normal failure backoff.

New owner and permit lookups retain normalized phone/email data, the selected
person ID and limited match evidence. Raw vendor responses, relatives, birth
data and address histories are not saved. The existing startup migration (or
`migrate_enrichment_reliability.py`) runs a transactional, idempotent cleanup of
previous raw payloads and excess owner match-summary fields. It also clears
legacy HPD addresses that cannot be attributed to one contact. Saved contacts,
payment receipts and unlocks are preserved. This cleanup affects the application
database; existing database backups and vendor-side retention are separate.

## Owner research and source history

The Ownership section includes person cards with source roles, reported dates,
and attributed search localities. Cross-source grouping requires a complete name
and matching full reported address. A shared name or ZIP alone never establishes
identity. Older reviews remain visible as historical records when their original
source disappears. Source differences are review flags, not ownership conclusions.

People searches open an editable preview before sending a name and city/state or
ZIP to TruePeopleSearch. The newest available reported locality is selected;
the property location is explicitly a fallback. Users can correct the search name,
select another reported locality, enter a locality, or search by name only.
The application does not scrape or automatically import the manual search result.

Users can save a reviewed result link, selected phones/emails, match assessment,
notes and research status. Reviews are stored in `crm_owner_research`, strictly
scoped to the active user's team, with reviewer/time and optimistic save versions.
Conflicting edits return 409 and keep the browser draft. Do-not-contact applies
to that team/property/name, including accepted name variants: it blocks lookup
actions and suppresses paid contact output and contact addresses in exports.
It does not delete the underlying public source evidence or prior review.

Each of HPD, ACRIS, PLUTO, historical RPAD, ECB and NY SOS can be refreshed
individually through `/api/property/<bbl>/owner-sources/<source>/refresh`.
Requests are authenticated, same-origin JSON with `X-Owner-Research: 1`, and
queue only free public-source work. Jobs deduplicate per property/source, allow
12 pending jobs per user, and enforce a five-minute success cooldown. Failures
preserve successful facts, retry after six hours, and stop after three attempts;
abandoned jobs recover after their 20-minute lease when no worker still owns them.

`owner_source_snapshots` and `owner_source_history` save an initial baseline and
subsequent name/address/role changes in the source update's transaction. Reported
dates and observation times remain distinct; date-only refreshes do not create
duplicate change events. History starts when the feature observes a source,
with the most recent 100 events shown. Revision guards discard overlapping stale
responses. Automatic pipeline updates use the same history hooks.

Web startup and `migrate_enrichment_reliability.py` install these idempotent
schemas; migrate before running individual enrichment scripts on a fresh database.
The source worker starts per web worker and PostgreSQL coordinates job claims.
Railway's forwarded HTTPS scheme is trusted only when its environment marker is
present; local proxy deployments can explicitly set the Flask config
`OWNER_RESEARCH_TRUST_PROXY_PROTO`. Forwarded host values are never trusted.

Focused verification: `python -m unittest owner_research_tests
owner_source_history_tests owner_source_dates_tests owner_privacy_tests
enrichment_reliability_tests`, plus `node owner_research_ui_tests.js` and
`node building_profile_permit_tests.js`. Database tests use disposable schemas
under `OWNER_RESEARCH_TEST_DATABASE_URL` / `ENRICHMENT_TEST_DATABASE_URL`.
`dashboard_html/tests/mobile_preview.py` provides synthetic in-memory UI fixtures.
