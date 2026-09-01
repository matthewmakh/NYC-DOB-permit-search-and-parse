#!/usr/bin/env python3
"""
Flask API Backend for DOB Permit Dashboard
Serves data from PostgreSQL database to HTML frontend
"""

from flask import Flask, jsonify, request, render_template, redirect, url_for, session, g, flash
from flask_cors import CORS
from flask_caching import Cache
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
import os
import secrets
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
import requests
from urllib.parse import urlsplit
from socrata_client import SocrataClient, normalize_pluto_record

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for local development

# Session configuration for authentication
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SESSION_COOKIE_SECURE'] = os.getenv('FLASK_ENV') == 'production'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=48)

# Register auth blueprint
from auth_routes import auth_bp
from auth_service import login_required, validate_session
app.register_blueprint(auth_bp)

# Activity logging - try to import, use stubs if not available
try:
    from activity_service import (
        log_activity, log_page_view, log_search, log_export, log_error, log_api_call,
        ActivityType, ActivityCategory,
        get_activity_logs, get_activity_stats, get_recent_logins, get_recent_errors
    )
    ACTIVITY_LOGGING_ENABLED = True
except ImportError:
    # Create stub functions that do nothing
    ACTIVITY_LOGGING_ENABLED = False
    def log_activity(*args, **kwargs): pass
    def log_page_view(*args, **kwargs): pass
    def log_search(*args, **kwargs): pass
    def log_export(*args, **kwargs): pass
    def log_error(*args, **kwargs): pass
    def log_api_call(*args, **kwargs): pass
    def get_activity_logs(*args, **kwargs): return []
    def get_activity_stats(*args, **kwargs): return {}
    def get_recent_logins(*args, **kwargs): return []
    def get_recent_errors(*args, **kwargs): return []
    
    # Create stub classes
    class ActivityType:
        ADMIN_ACTIVITY_VIEW = 'admin_activity_view'
        ADMIN_USER_VIEW = 'admin_user_view'
        ADMIN_SETTINGS_CHANGE = 'admin_settings_change'
        BUTTON_CLICK = 'button_click'
        SECTION_CLICK = 'section_click'
        TAB_SWITCH = 'tab_switch'
        FILTER_CHANGE = 'filter_change'
        FORM_SUBMIT = 'form_submit'
        SEARCH = 'search'
        EXPORT = 'export'
        DATA_VIEW = 'data_view'
        ERROR = 'error'
    
    class ActivityCategory:
        ADMIN = 'admin'
        INTERACTION = 'interaction'

# Simple in-memory cache (can upgrade to Redis later)
cache = Cache(app, config={
    'CACHE_TYPE': 'SimpleCache',  # In-memory cache
    'CACHE_DEFAULT_TIMEOUT': 300  # 5 minutes
})

# Reuse one token-aware, retrying HTTP session for live NYC Open Data panels.
socrata = SocrataClient(timeout=20, max_retries=3)

# Force unbuffered output for Railway logging
import sys
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

print(f"[STARTUP] Flask app loading...", flush=True)

# Database configuration - support both DATABASE_URL and individual vars
DATABASE_URL = os.getenv('DATABASE_URL')

if DATABASE_URL:
    # Parse DATABASE_URL (format: postgresql://user:password@host:port/database)
    from urllib.parse import urlparse
    parsed = urlparse(DATABASE_URL)
    DB_CONFIG = {
        'host': parsed.hostname,
        'port': str(parsed.port or 5432),
        'database': parsed.path.lstrip('/'),
        'user': parsed.username,
        'password': parsed.password,
        'connect_timeout': 10,
        'sslmode': 'require',
        # No single statement may hold a worker past a minute. Without this,
        # a query stuck behind pipeline load ran until gunicorn's 120s kill,
        # and a killed worker is what the platform edge reports as a 502.
        'options': '-c statement_timeout=60000',
    }
    print(f"✅ Using DATABASE_URL (host: {parsed.hostname})", flush=True)
else:
    # Fall back to individual environment variables
    DB_CONFIG = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': os.getenv('DB_PORT', '5432'),
        'database': os.getenv('DB_NAME', 'permits_db'),
        'user': os.getenv('DB_USER', 'postgres'),
        'password': os.getenv('DB_PASSWORD', ''),
        'connect_timeout': 10,
        'sslmode': 'require',  # Railway requires SSL for public connections
        'options': '-c statement_timeout=60000',
    }
    print(f"✅ Using individual DB vars (host: {DB_CONFIG['host']}, port: {DB_CONFIG['port']}, db: {DB_CONFIG['database']}, user: {DB_CONFIG['user']}, pass: {'*' * len(DB_CONFIG['password']) if DB_CONFIG['password'] else 'EMPTY!'})", flush=True)

# Simple connection pool: 2-10 connections per worker
# Railway free tier supports up to 20 connections total
# With 2 workers, each gets max 5 connections (2-5 range)
db_pool = None

def init_db_pool():
    """Initialize the database connection pool for this worker process.

    MUST NOT raise. This runs from gunicorn's post_fork hook, and a raise
    there fails the worker boot — gunicorn then halts the whole master
    ('Worker failed to boot'), nothing listens, and the platform edge
    answers every request with an instant 502. That is exactly what took
    the site down while the enrichment pipeline had Postgres saturated:
    one worker restart during the bad window, and the app never came back
    (the platform stops restarting after 10 crash loops). Verified by
    booting this stack locally against an unreachable database.

    On failure the pool stays None and get_db_connection retries creating
    it per-request: routes answer with a fast JSON error while the
    database is down, and heal on their own the moment it is back.
    """
    global db_pool
    if db_pool is None:
        try:
            print(f"🔌 Creating connection pool to {DB_CONFIG['host']}:{DB_CONFIG['port']}...", flush=True)
            # Threaded: gunicorn runs gthread workers, so several request
            # threads hit this pool at once. 8 max × 2 workers = 16
            # connections, well inside Railway Postgres limits even with
            # the pipeline connected.
            db_pool = pool.ThreadedConnectionPool(1, 8, **DB_CONFIG)
            print(f"✅ Initialized connection pool for worker PID {os.getpid()}", flush=True)
        except Exception as e:
            print(f"❌ Failed to create connection pool (will retry per-request): {e}", flush=True)
            import traceback
            traceback.print_exc()
            db_pool = None
            return None
        # One-off, idempotent: ensure bulk_enrich_jobs table exists and any
        # 'running' jobs from a previous container are marked failed.
        try:
            import bulk_enrich_service
            bulk_enrich_service.init_bulk_enrich_jobs_table()
            bulk_enrich_service.resume_orphaned_jobs()
        except Exception as e:
            print(f"⚠️  bulk_enrich_service init skipped: {e}", flush=True)
        try:
            import team_service
            team_service.init_team_tables()
        except Exception as e:
            # Like the bulk-job migration, this must never prevent a worker
            # from booting while Postgres is briefly unavailable.
            print(f"⚠️  sponsored-account init skipped: {e}", flush=True)
    return db_pool


def get_db_connection():
    """Get a connection from the pool"""
    global db_pool
    if db_pool is None:
        init_db_pool()
    if db_pool is None:
        # Pool creation failed (database down/saturated). Fail this request
        # fast with a clear reason; the next request retries the pool.
        raise RuntimeError('database unavailable — connection pool could not be created')
    try:
        conn = db_pool.getconn()
        # Test if connection is valid
        if conn.closed:
            # Connection is closed, try to get a new one
            db_pool.putconn(conn, close=True)
            conn = db_pool.getconn()
        else:
            # Test connection with a simple query
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            except Exception:
                # Connection is stale, close it and get a new one
                try:
                    db_pool.putconn(conn, close=True)
                except:
                    pass
                conn = db_pool.getconn()
        return conn
    except Exception as e:
        print(f"❌ Error getting connection from pool: {e}", flush=True)
        import traceback
        traceback.print_exc()
        # If pool is having issues, try to recreate it
        try:
            db_pool = None
            init_db_pool()
            return db_pool.getconn()
        except Exception as e2:
            print(f"❌ Failed to recreate pool: {e2}", flush=True)
            traceback.print_exc()
            raise


def return_db_connection(conn):
    """Return a connection to the pool"""
    if conn and db_pool:
        try:
            # Only return if connection is still valid
            if not conn.closed:
                db_pool.putconn(conn)
            else:
                print("Connection was closed, not returning to pool")
        except Exception as e:
            print(f"Error returning connection to pool: {e}")


class DatabaseConnection:
    """Context manager for database connections"""
    def __init__(self):
        self.conn = None
        self.cursor = None
        self.conn_returned = False
    
    def __enter__(self):
        self.conn = get_db_connection()
        self.cursor = self.conn.cursor(cursor_factory=RealDictCursor)
        return self.cursor
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # Close cursor first
        if self.cursor:
            try:
                self.cursor.close()
            except Exception as e:
                print(f"Error closing cursor: {e}")
            finally:
                self.cursor = None
        
        # Handle transaction and return connection
        if self.conn and not self.conn_returned:
            try:
                if exc_type is not None:
                    # Rollback on error
                    try:
                        self.conn.rollback()
                    except Exception as e:
                        print(f"Error rolling back: {e}")
                else:
                    # Commit on success for write operations
                    # For read operations, this is a no-op
                    try:
                        self.conn.commit()
                    except Exception as e:
                        print(f"Error committing: {e}")
            finally:
                # Always try to return connection
                try:
                    return_db_connection(self.conn)
                    self.conn_returned = True
                except Exception as e:
                    print(f"Error in __exit__ returning connection: {e}")
                finally:
                    self.conn = None
        
        return False  # Don't suppress exceptions


def _fetch_contact_directory(cur, *, permit_id=None, bbl=None):
    """Return deduplicated canonical, historical, and name-only contacts."""
    if (permit_id is None) == (bbl is None):
        raise ValueError("provide exactly one of permit_id or bbl")
    if permit_id is not None:
        scope_sql, params = "d.permit_id = %s", (permit_id,)
    else:
        # Resolve the small permit set first. Joining the UNION view to every
        # permit prevented PostgreSQL from pushing the BBL predicate into each
        # indexed branch and made a one-property contact read scan the world.
        cur.execute("SELECT id FROM permits WHERE bbl = %s", (bbl,))
        permit_ids = [row['id'] for row in cur.fetchall()]
        if not permit_ids:
            return []
        scope_sql, params = "d.permit_id = ANY(%s)", (permit_ids,)
    cur.execute(
        f"""
        SELECT d.*, p.permit_no AS permit_number, p.bbl
        FROM permit_contact_directory d
        JOIN permits p ON p.id = d.permit_id
        WHERE {scope_sql}
        ORDER BY
            CASE d.verification_status WHEN 'validated' THEN 0
                 WHEN 'legacy_checked_needs_revalidation' THEN 1 ELSE 2 END,
            d.role, d.name
        """,
        params,
    )
    merged = {}
    for raw in cur.fetchall():
        item = dict(raw)
        digits = ''.join(ch for ch in str(item.get('phone') or '') if ch.isdigit())
        phone_key = digits[-10:] if len(digits) >= 10 else digits
        key = ('phone', phone_key) if phone_key else (
            'name', str(item.get('name') or '').strip().upper(), item.get('role')
        )
        current = merged.get(key)
        if current is None:
            current = item
            current['_roles'] = {item.get('role') or 'Contact'}
            current['_sources'] = set(filter(None, str(item.get('source') or '').split(', ')))
            current['_permits'] = {item.get('permit_number')} if item.get('permit_number') else set()
            current['evidence_count'] = int(item.get('evidence_count') or 0)
            merged[key] = current
        else:
            current['_roles'].add(item.get('role') or 'Contact')
            current['_sources'].update(filter(None, str(item.get('source') or '').split(', ')))
            if item.get('permit_number'):
                current['_permits'].add(item['permit_number'])
            current['evidence_count'] += int(item.get('evidence_count') or 0)
            current['is_mobile'] = bool(current.get('is_mobile') or item.get('is_mobile'))
            current['legacy_mobile_observed'] = bool(
                current.get('legacy_mobile_observed') or item.get('legacy_mobile_observed')
            )
            current['needs_revalidation'] = bool(
                current.get('needs_revalidation') and item.get('needs_revalidation')
            )
            if item.get('verification_status') == 'validated':
                current['verification_status'] = 'validated'
                current['needs_revalidation'] = False
                current['line_type'] = item.get('line_type') or current.get('line_type')
                current['carrier_name'] = item.get('carrier_name') or current.get('carrier_name')
    contacts = []
    for item in merged.values():
        item['role'] = ' / '.join(sorted(item.pop('_roles')))
        item['source'] = ', '.join(sorted(item.pop('_sources')))
        permits = item.pop('_permits')
        item['permit_count'] = len(permits)
        item['permit_numbers'] = sorted(permits)
        item['phone_type'] = item.get('line_type')
        item['carrier'] = item.get('carrier_name')
        item['email'] = None
        if item.get('role_confidence') is not None:
            item['role_confidence'] = float(item['role_confidence'])
        contacts.append(item)
    return contacts


# ============================================================================
# ACTIVITY LOGGING MIDDLEWARE
# ============================================================================

# Map of endpoints to friendly page names
PAGE_NAMES = {
    'index': 'Home',
    'old_dashboard': 'Old Dashboard',
    'construction': 'Construction Intelligence',
    'investments': 'Investments',
    'analytics': 'Analytics',
    'search_results': 'Search Results',
    'property_detail': 'Property Profile',
    'properties_page': 'Properties',
    'contractors_page': 'Permit Participants',
    'contractor_profile': 'Participant Profile',
    'sales_alerts_page': 'Sales Alerts',
}

# Endpoints to skip logging (health checks, static files, etc.)
SKIP_LOGGING_ENDPOINTS = {'static', 'api_health', 'get_stats'}


@app.before_request
def before_request_logging():
    """Store request start time for response time calculation"""
    g.request_start_time = time.time()


@app.after_request
def after_request_logging(response):
    """Log page views and API calls after each request"""
    try:
        # Skip if no endpoint or if it's a static file
        if not request.endpoint or request.endpoint in SKIP_LOGGING_ENDPOINTS:
            return response
        
        # Skip auth routes (they have their own logging)
        if request.endpoint.startswith('auth.'):
            return response
        
        # Calculate response time
        response_time_ms = None
        if hasattr(g, 'request_start_time'):
            response_time_ms = int((time.time() - g.request_start_time) * 1000)
        
        # Determine if this is a page view or API call
        is_api = request.path.startswith('/api/')
        
        if is_api:
            # Log API calls (but not all of them to avoid noise)
            # Only log search, export, and data-fetching APIs
            if any(x in request.path for x in ['/search', '/export', '/enrich']):
                log_api_call(
                    endpoint=request.path,
                    method=request.method,
                    response_status=response.status_code,
                    response_time_ms=response_time_ms,
                    success=response.status_code < 400
                )
        else:
            # Log page views
            page_name = PAGE_NAMES.get(request.endpoint, request.endpoint)
            log_page_view(
                page_name=page_name,
                page_url=request.url,
                metadata={
                    'response_status': response.status_code,
                    'response_time_ms': response_time_ms
                }
            )
    except Exception as e:
        # Don't let logging errors break the app
        print(f"Activity logging middleware error: {e}")
    
    return response


def calculate_lead_score(permit):
    """Calculate lead score based on permit attributes"""
    score = 0
    
    # Contact count
    contact_count = permit.get('contact_count', 0)
    if contact_count > 0:
        score += min(contact_count * 15, 40)
    
    # Has mobile phone (check for mobile-typical area codes or from has_mobile flag)
    has_mobile = permit.get('has_mobile', False)
    if not has_mobile:
        # Fallback: check phone numbers for mobile patterns
        permittee_phone = str(permit.get('permittee_phone', ''))
        owner_phone = str(permit.get('owner_phone', ''))
        # Common mobile area codes in NYC region: 347, 646, 917, 929, 332, 718 (mixed), 212 (mixed)
        # Conservative approach: 347, 646, 917, 929, 332 are primarily mobile
        mobile_prefixes = ('347', '646', '917', '929', '332')
        has_mobile = (
            any(permittee_phone.replace('-', '').replace(' ', '').replace('(', '').replace(')', '').startswith(prefix) for prefix in mobile_prefixes) or
            any(owner_phone.replace('-', '').replace(' ', '').replace('(', '').replace(')', '').startswith(prefix) for prefix in mobile_prefixes)
        )
    
    if has_mobile:
        score += 20
    
    # Recent permit
    issue_date = permit.get('issue_date')
    if issue_date:
        days_old = (datetime.now().date() - issue_date).days
        if days_old <= 30:
            score += 25
        elif days_old <= 90:
            score += 15
        elif days_old <= 180:
            score += 10
    
    # Job type value
    job_type = permit.get('job_type', '')
    high_value_types = ['NB', 'A1', 'AL']
    if job_type in high_value_types:
        score += 15
    
    return min(score, 100)


@app.route('/')
@login_required
def index():
    """Serve the new homepage"""
    return render_template('home.html', user=g.user, active_page='home')


@app.route('/api/permits')
def get_permits():
    """Get all permits with calculated scores, contact info, and building intelligence"""
    try:
        with DatabaseConnection() as cur:
            # Canonical contact directory combines current and recovered evidence.
            query = """
                SELECT 
                    p.*,
                    COALESCE(cd.contact_count, 0) AS contact_count,
                    COALESCE(cd.has_mobile, false) AS has_mobile,
                    COALESCE(cd.contact_names, CONCAT_WS(' | ',
                        NULLIF(COALESCE(p.permittee_business_name, p.applicant), ''),
                        NULLIF(p.owner_business_name, ''),
                        NULLIF(p.superintendent_business_name, ''),
                        NULLIF(p.site_safety_mgr_business_name, '')
                    )) AS contact_names,
                    COALESCE(cd.contact_phones, CONCAT_WS(' | ',
                        NULLIF(p.permittee_phone, ''),
                        NULLIF(p.owner_phone, '')
                    )) AS contact_phones,
                    b.id as building_id,
                    b.current_owner_name,
                    b.owner_name_rpad,
                    b.building_class,
                    b.land_use,
                    b.residential_units,
                    b.total_units,
                    b.num_floors,
                    b.building_sqft,
                    b.lot_sqft,
                    b.year_built,
                    b.year_altered,
                    b.assessed_land_value,
                    b.assessed_total_value,
                    b.purchase_date,
                    b.purchase_price,
                    b.mortgage_amount
                FROM permits p
                LEFT JOIN buildings b ON p.bbl = b.bbl
                LEFT JOIN (
                    SELECT pc.permit_id, count(DISTINCT pc.contact_id) AS contact_count,
                           bool_or(COALESCE(c.is_mobile, false)) AS has_mobile,
                           string_agg(DISTINCT c.name, ' | ') FILTER (WHERE c.name IS NOT NULL) AS contact_names,
                           string_agg(DISTINCT c.phone, ' | ') FILTER (WHERE c.phone IS NOT NULL) AS contact_phones
                    FROM permit_contacts pc JOIN contacts c ON c.id = pc.contact_id
                    GROUP BY pc.permit_id
                ) cd ON cd.permit_id = p.id
                ORDER BY p.issue_date DESC;
            """
            
            cur.execute(query)
            permits = cur.fetchall()
            
            # Add lead scores
            for permit in permits:
                permit['lead_score'] = calculate_lead_score(permit)
        
        return jsonify({
            'success': True,
            'permits': permits,
            'count': len(permits)
        })
        
    except Exception as e:
        print(f"Error fetching permits: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/stats')
@cache.cached(timeout=60, key_prefix='dashboard_stats')  # Cache for 1 minute
def get_stats():
    """Get dashboard statistics including building intelligence"""
    try:
        with DatabaseConnection() as cur:
            # Total permits
            cur.execute("SELECT COUNT(*) as total FROM permits;")
            total_permits = cur.fetchone()['total']
            
            # Canonical identities, rather than permits that happen to carry a phone.
            cur.execute("SELECT COUNT(*) as total FROM contacts;")
            total_contacts = cur.fetchone()['total']

            cur.execute("SELECT COUNT(*) as total FROM contacts WHERE is_mobile IS TRUE;")
            mobile_contacts = cur.fetchone()['total']
            
            # Building intelligence stats
            cur.execute("SELECT COUNT(*) as total FROM buildings;")
            total_buildings = cur.fetchone()['total']
            
            cur.execute("SELECT COUNT(*) as total FROM buildings WHERE current_owner_name IS NOT NULL;")
            buildings_with_owners = cur.fetchone()['total']
            
            cur.execute("SELECT COUNT(*) as total FROM buildings WHERE purchase_date IS NOT NULL;")
            buildings_with_acris = cur.fetchone()['total']
            
            cur.execute("SELECT COUNT(*) as total FROM permits WHERE bbl IS NOT NULL;")
            permits_with_bbl = cur.fetchone()['total']
        
        enrichment_rate = (buildings_with_owners / total_buildings * 100) if total_buildings > 0 else 0
        
        return jsonify({
            'success': True,
            'stats': {
                'total_permits': total_permits,
                'total_contacts': total_contacts,
                'mobile_contacts': mobile_contacts,
                'total_buildings': total_buildings,
                'buildings_with_owners': buildings_with_owners,
                'buildings_with_acris': buildings_with_acris,
                'permits_with_bbl': permits_with_bbl,
                'enrichment_rate': round(enrichment_rate, 1)
            }
        })
        
    except Exception as e:
        print(f"Error fetching stats: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/search-contact')
def search_contact():
    """Search current and recovered contact evidence by name or phone."""
    query = request.args.get('q', '').strip()
    
    if len(query) < 2:
        return jsonify({
            'success': False,
            'error': 'Query must be at least 2 characters'
        }), 400
    
    try:
        with DatabaseConnection() as cur:
            search_query = """
                WITH matches AS (
                    SELECT p.id, p.permit_no, p.address, p.job_type, p.issue_date,
                           c.name AS contact_name, c.phone AS contact_phone,
                           COALESCE(pc.contact_role, c.role, 'Contact') AS contact_role,
                           COALESCE(string_agg(DISTINCT ce.source, ', '), 'permit_contact') AS contact_source,
                           CASE WHEN c.phone_validated_at IS NOT NULL THEN 'validated'
                                ELSE 'unverified' END AS verification_status
                    FROM contacts c
                    JOIN permit_contacts pc ON pc.contact_id = c.id
                    JOIN permits p ON p.id = pc.permit_id
                    LEFT JOIN contact_evidence ce
                      ON ce.permit_id = pc.permit_id AND ce.contact_id = c.id
                    WHERE LOWER(COALESCE(c.name, '')) LIKE %s OR COALESCE(c.phone, '') LIKE %s
                    GROUP BY p.id, p.permit_no, p.address, p.job_type, p.issue_date,
                             c.id, pc.contact_role
                    UNION ALL
                    SELECT p.id, p.permit_no, p.address, p.job_type, p.issue_date,
                           ce.raw_name, NULL, COALESCE(ce.observed_role, 'Legacy Contact'),
                           ce.source, ce.validation_status
                    FROM contact_evidence ce JOIN permits p ON p.id = ce.permit_id
                    WHERE ce.contact_id IS NULL
                      AND LOWER(COALESCE(ce.raw_name, '')) LIKE %s
                    UNION ALL
                    SELECT p.id, p.permit_no, p.address, p.job_type, p.issue_date,
                           v.name, v.phone, v.role, 'permit_record', 'unverified'
                    FROM permits p
                    CROSS JOIN LATERAL (VALUES
                        (COALESCE(p.permittee_business_name, p.applicant), p.permittee_phone, 'Permittee'),
                        (p.owner_business_name, p.owner_phone, 'Owner'),
                        (COALESCE(p.superintendent_business_name, p.superintendent_name), NULL::VARCHAR, 'Superintendent'),
                        (p.site_safety_mgr_business_name, NULL::VARCHAR, 'Site Safety Manager')
                    ) v(name, phone, role)
                    WHERE LOWER(COALESCE(v.name, '')) LIKE %s OR COALESCE(v.phone, '') LIKE %s
                ), deduped AS (
                    SELECT DISTINCT ON (id, contact_name, COALESCE(contact_phone, '')) *
                    FROM matches
                    ORDER BY id, contact_name, COALESCE(contact_phone, ''), issue_date DESC
                )
                SELECT * FROM deduped ORDER BY issue_date DESC NULLS LAST LIMIT 50;
            """
            
            search_pattern = f'%{query.lower()}%'
            cur.execute(search_query, (
                search_pattern, search_pattern, search_pattern,
                search_pattern, search_pattern,
            ))
            
            results = cur.fetchall()
        
        return jsonify({
            'success': True,
            'results': results,
            'count': len(results)
        })
        
    except Exception as e:
        print(f"Error searching contacts: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# Which owner name step5 fed into the Secretary of State lookup. Stored by the
# pipeline but never surfaced until now, which left no way to tell whether an
# SOS entity legitimately supersedes the PLUTO name shown beside it (a newer
# deed) or is simply the wrong company.
SOS_SOURCE_LABELS = {
    'sale_buyer_primary': 'ACRIS deed buyer (most recent sale)',
    'owner_name_rpad': 'Tax records (RPAD)',
    'current_owner_name': 'NYC PLUTO',
    'owner_name_hpd': 'HPD registration',
}


# ============================================================================
# PERMIT CLASSIFICATION CODES
# ----------------------------------------------------------------------------
# DOB spells out what a permit is for across four columns. Labels here are for
# display only — the filter options themselves come from the database (see
# /api/permits/facets), so a code we have not named still appears, just bare.
# ============================================================================

# job_type: the scope of the job the permit belongs to.
JOB_TYPE_LABELS = {
    'A1': 'Alteration Type 1 (use, egress or occupancy change)',
    'A2': 'Alteration Type 2 (multiple work types, no use change)',
    'A3': 'Alteration Type 3 (minor, single work type)',
    'NB': 'New Building',
    'DM': 'Demolition',
    'SG': 'Sign',
    'PA': 'Place of Assembly',
}

# work_type: the trade actually being performed. This is the column that
# answers "what kind of work does this contractor do".
WORK_TYPE_LABELS = {
    'BL': 'Boiler',
    'CC': 'Curb Cut',
    'CH': 'Chute',
    'EQ': 'Construction Equipment',
    'EW': 'Equipment Work',
    'FA': 'Fire Alarm',
    'FB': 'Fuel Burning',
    'FN': 'Fence',
    'FP': 'Fire Suppression',
    'FS': 'Fuel Storage',
    'MH': 'Mechanical / HVAC',
    'OT': 'Other',
    'PL': 'Plumbing',
    'SD': 'Standpipe',
    'SF': 'Scaffold',
    'SH': 'Sidewalk Shed',
    'SP': 'Sprinkler',
}

# permit_type: the permit category DOB issues under.
PERMIT_TYPE_LABELS = {
    'AL': 'Alteration',
    'BL': 'Boiler',
    'DM': 'Demolition & Removal',
    'EQ': 'Construction Equipment',
    'EW': 'Equipment Work',
    'FO': 'Foundation',
    'FN': 'Fence',
    'FP': 'Fire Suppression',
    'NB': 'New Building',
    'OT': 'Other',
    'PL': 'Plumbing',
    'SD': 'Standpipe',
    'SG': 'Sign',
    'SH': 'Sidewalk Shed',
    'SP': 'Sprinkler',
}

# permittee_license_type: what licence the permit was pulled under, which is
# the cleanest read on a contractor's trade.
LICENSE_TYPE_LABELS = {
    'GC': 'General Contractor',
    'HI': 'Home Improvement Contractor',
    'MP': 'Master Plumber',
    'FS': 'Fire Suppression Contractor',
    'OB': 'Oil Burner Installer',
    'SI': 'Sign Hanger',
    'ME': 'Master Electrician',
    'EL': 'Electrician',
    'PE': 'Professional Engineer',
    'RA': 'Registered Architect',
    'TC': 'Tower Crane Rigger',
    'OW': 'Owner',
    'DM': 'Demolition Contractor',
    'GF': 'General Contractor (filing rep)',
}

_FACET_COLUMNS = {
    'job_type': ('job_type', JOB_TYPE_LABELS),
    'work_type': ('work_type', WORK_TYPE_LABELS),
    'permit_type': ('permit_type', PERMIT_TYPE_LABELS),
    'license_type': ('permittee_license_type', LICENSE_TYPE_LABELS),
    # The three DOB feeds represented in `permits` do not all use the same
    # status column.  One combined facet keeps the UI honest across them.
    'permit_status': (
        "COALESCE(NULLIF(permit_status, ''), NULLIF(status, ''), "
        "NULLIF(filing_status, ''))",
        {},
    ),
}


def label_for(kind, code):
    """Human label for a DOB code, falling back to the bare code."""
    if not code:
        return None
    labels = _FACET_COLUMNS.get(kind, (None, {}))[1]
    name = labels.get(str(code).upper())
    return f'{code} — {name}' if name else str(code)


@app.route('/api/permits/facets')
@cache.cached(timeout=1800)
def api_permit_facets():
    """Filter options for the permit classification columns.

    Values come from the permits table with a count each, so the filters only
    ever offer choices that match something, and a code DOB adds later shows
    up without a code change.
    """
    try:
        facets = {}
        with DatabaseConnection() as cur:
            for kind, (column, _labels) in _FACET_COLUMNS.items():
                # Column names are from the fixed map above, never user input.
                cur.execute(f"""
                    SELECT {column} AS code, COUNT(*) AS permit_count
                    FROM permits
                    WHERE {column} IS NOT NULL
                      AND btrim({column}) <> ''
                    GROUP BY {column}
                    HAVING COUNT(*) >= 25
                    ORDER BY COUNT(*) DESC
                """)
                facets[kind] = [
                    {
                        'value': row['code'].strip().upper(),
                        'label': label_for(kind, row['code'].strip().upper()),
                        'count': row['permit_count'],
                    }
                    for row in cur.fetchall()
                ]

        return jsonify({'success': True, 'facets': facets})

    except Exception as e:
        print(f"Permit facets API error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/permit-types')
def get_permit_types():
    """Get all unique permit/job types"""
    try:
        with DatabaseConnection() as cur:
            cur.execute("SELECT DISTINCT job_type FROM permits WHERE job_type IS NOT NULL ORDER BY job_type;")
            types = [row['job_type'] for row in cur.fetchall()]
        
        return jsonify({
            'success': True,
            'types': types
        })
        
    except Exception as e:
        print(f"Error fetching permit types: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/charts/job-types')
def get_job_types_chart():
    """Get job type distribution data"""
    try:
        with DatabaseConnection() as cur:
            cur.execute("""
                SELECT job_type, COUNT(*) as count 
                FROM permits 
                WHERE job_type IS NOT NULL
                GROUP BY job_type 
                ORDER BY count DESC 
                LIMIT 10;
            """)
            
            data = cur.fetchall()
        
        return jsonify({
            'success': True,
            'labels': [row['job_type'] for row in data],
            'data': [row['count'] for row in data]
        })
        
    except Exception as e:
        print(f"Error fetching job types chart: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/charts/trends')
def get_trends_chart():
    """Get permit trends over time"""
    try:
        with DatabaseConnection() as cur:
            cur.execute("""
                SELECT 
                    DATE_TRUNC('month', issue_date) as month,
                    COUNT(*) as count
                FROM permits
                WHERE issue_date >= NOW() - INTERVAL '12 months'
                GROUP BY month
                ORDER BY month;
            """)
            
            data = cur.fetchall()
        
        return jsonify({
            'success': True,
            'labels': [row['month'].strftime('%b %Y') for row in data],
            'data': [row['count'] for row in data]
        })
        
    except Exception as e:
        print(f"Error fetching trends chart: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/charts/applicants')
def get_top_applicants_chart():
    """Get top applicants by permit count"""
    try:
        with DatabaseConnection() as cur:
            cur.execute("""
                SELECT applicant, COUNT(*) as count
                FROM permits
                WHERE applicant IS NOT NULL AND applicant != ''
                GROUP BY applicant
                ORDER BY count DESC
                LIMIT 10;
            """)
            
            data = cur.fetchall()
        
        return jsonify({
            'success': True,
            'labels': [row['applicant'][:30] for row in data],
            'data': [row['count'] for row in data]
        })
        
    except Exception as e:
        print(f"Error fetching applicants chart: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/map-data')
def get_map_data():
    """Get permit data with geocoded locations for map"""
    try:
        with DatabaseConnection() as cur:
            cur.execute("""
                SELECT 
                    permit_no,
                    address,
                    job_type,
                    issue_date,
                    latitude,
                    longitude
                FROM permits
                WHERE latitude IS NOT NULL 
                    AND longitude IS NOT NULL
                    AND latitude BETWEEN -90 AND 90
                    AND longitude BETWEEN -180 AND 180
                LIMIT 1000;
            """)
            
            data = cur.fetchall()
        
        return jsonify({
            'success': True,
            'locations': data,
            'count': len(data)
        })
        
    except Exception as e:
        print(f"Error fetching map data: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/permit/<permit_id>')
def get_permit_details(permit_id):
    """Get detailed information for a single permit"""
    try:
        with DatabaseConnection() as cur:
            cur.execute("SELECT * FROM permits WHERE permit_no = %s;", (permit_id,))
            permit = cur.fetchone()
            if not permit:
                return jsonify({
                    'success': False,
                    'error': 'Permit not found'
                }), 404
            contacts = _fetch_contact_directory(cur, permit_id=permit['id'])

        permit['contacts'] = contacts
        permit['contact_count'] = len(contacts)
        permit['has_mobile'] = any(c.get('is_mobile') for c in contacts)
        permit['lead_score'] = calculate_lead_score(permit)
        
        return jsonify({
            'success': True,
            'permit': permit
        })
        
    except Exception as e:
        print(f"Error fetching permit details: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/health')
def health_check():
    """Health check endpoint"""
    # Basic health check without database
    health_status = {
        'success': True,
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    }
    
    # Try database connection
    try:
        with DatabaseConnection() as cur:
            cur.execute("SELECT 1;")
        health_status['database'] = 'connected'
    except Exception as e:
        health_status['database'] = 'disconnected'
        health_status['db_error'] = str(e)
    
    return jsonify(health_status)


@app.route('/permit/<int:permit_id>')
@login_required
def permit_detail(permit_id):
    """Serve detailed permit view page with comprehensive building information"""
    try:
        with DatabaseConnection() as cur:
            # Get permit with all details including building info
            query = """
            SELECT 
                p.*,
                -- Calculate contact count from permits table columns
                (
                    CASE WHEN p.permittee_phone IS NOT NULL AND p.permittee_phone != '' THEN 1 ELSE 0 END +
                    CASE WHEN p.owner_phone IS NOT NULL AND p.owner_phone != '' THEN 1 ELSE 0 END
                ) as contact_count,
                false as has_mobile,
                b.id as building_id,
                COALESCE(p.bbl, b.bbl) as bbl,
                COALESCE(p.bin, b.bin) as bin,
                b.address as building_address,
                b.current_owner_name,
                b.owner_name_rpad,
                b.owner_name_hpd,
                b.building_class,
                b.land_use,
                b.residential_units,
                b.total_units,
                b.num_floors,
                b.building_sqft,
                b.lot_sqft,
                b.year_built,
                b.year_altered,
                b.assessed_land_value,
                b.assessed_total_value,
                b.purchase_date,
                b.purchase_price,
                b.sale_price,
                b.sale_date,
                b.sale_recorded_date,
                b.sale_buyer_primary,
                b.sale_seller_primary,
                b.sale_percent_transferred,
                b.mortgage_amount,
                b.mortgage_date,
                b.mortgage_lender_primary,
                b.is_cash_purchase,
                b.financing_ratio,
                b.days_since_sale,
                b.estimated_value,
                b.estimated_equity,
                b.estimated_annual_rent,
                b.estimated_rent_per_unit,
                b.hpd_open_violations,
                b.hpd_total_violations,
                b.hpd_open_complaints,
                b.hpd_total_complaints,
                b.hpd_registration_id,
                b.acris_total_transactions,
                b.acris_deed_count,
                b.acris_mortgage_count,
                b.acris_satisfaction_count
            FROM permits p
            LEFT JOIN buildings b ON p.bbl = b.bbl
            WHERE p.id = %s;
            """
            
            cur.execute(query, (permit_id,))
            permit = cur.fetchone()
            
            if not permit:
                return "Permit not found", 404
            
            contacts = _fetch_contact_directory(cur, permit_id=permit_id)
            permit['contact_count'] = len(contacts)
            permit['has_mobile'] = any(c.get('is_mobile') for c in contacts)
            
            # Get all permits for the same building (if BBL exists)
            related_permits = []
            if permit['bbl']:
                cur.execute("""
                    SELECT 
                        p.id,
                        p.permit_no,
                        p.job_type,
                        p.work_type,
                        p.permit_status,
                        p.permit_type,
                        p.issue_date,
                        p.exp_date,
                        p.filing_date,
                        p.address,
                        p.applicant,
                        p.permittee_business_name,
                        p.owner_business_name,
                        (SELECT count(*) FROM permit_contact_directory d
                          WHERE d.permit_id = p.id) AS contact_count
                    FROM permits p
                    WHERE p.bbl = %s AND p.id != %s
                    ORDER BY p.issue_date DESC
                    LIMIT 50;
                """, (permit['bbl'], permit_id))
                related_permits = cur.fetchall()
            
            # Calculate lead score
            permit['lead_score'] = calculate_lead_score(permit)
        
        return render_template('permit_detail.html', 
                             permit=permit, 
                             contacts=contacts,
                             related_permits=related_permits,
                             active_page='permits')
        
    except Exception as e:
        print(f"Error fetching permit detail: {e}")
        return f"Error loading permit: {str(e)}", 500


@app.route('/api/buildings')
def get_buildings():
    """Get all buildings with owner and enrichment data"""
    try:
        with DatabaseConnection() as cur:
            query = """
                SELECT 
                    b.*,
                    COUNT(DISTINCT p.id) as linked_permits,
                    MAX(p.issue_date) as last_permit_date,
                    STRING_AGG(DISTINCT p.permit_no, ', ' ORDER BY p.permit_no) as permit_numbers
                FROM buildings b
                LEFT JOIN permits p ON b.bbl = p.bbl
                GROUP BY b.id
                ORDER BY b.assessed_total_value DESC NULLS LAST, b.id DESC;
            """
            
            cur.execute(query)
            buildings = cur.fetchall()
        
        return jsonify({
            'success': True,
            'buildings': buildings,
            'count': len(buildings)
        })
        
    except Exception as e:
        print(f"Error fetching buildings: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/buildings/<int:building_id>')
def get_building_detail(building_id):
    """Get detailed building information including all permits and contacts"""
    try:
        with DatabaseConnection() as cur:
            # Get building info
            cur.execute("SELECT * FROM buildings WHERE id = %s;", (building_id,))
            building = cur.fetchone()
            
            if not building:
                return jsonify({
                    'success': False,
                    'error': 'Building not found'
                }), 404
            
            # Get all permits for this building
            cur.execute("""
                SELECT p.*, 
                       (SELECT count(*) FROM permit_contact_directory d
                         WHERE d.permit_id = p.id) AS contact_count
                FROM permits p
                WHERE p.bbl = %s
                ORDER BY p.issue_date DESC;
            """, (building['bbl'],))
            permits = cur.fetchall()
            contacts = _fetch_contact_directory(cur, bbl=building['bbl'])
        
            return jsonify({
                'success': True,
                'building': building,
                'permits': permits,
                'contacts': contacts
            })
        
    except Exception as e:
        print(f"Error fetching building detail: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/buildings/<int:building_id>/contacts')
def get_building_contacts(building_id):
    """Get all contacts for a building"""
    try:
        with DatabaseConnection() as cur:
            # Get building BBL
            cur.execute("SELECT bbl FROM buildings WHERE id = %s;", (building_id,))
            building = cur.fetchone()
            
            if not building:
                return jsonify([])
            
            contacts = _fetch_contact_directory(cur, bbl=building['bbl'])
        
        return jsonify(contacts)
        
    except Exception as e:
        print(f"Error fetching building contacts: {e}")
        return jsonify([])


@app.route('/api/seller-leads')
def get_seller_leads():
    """Get previous property owners (sellers) with addresses for outreach campaign"""
    try:
        with DatabaseConnection() as cur:
            # Filter parameters
            min_sale_price = request.args.get('min_price', type=float)
            state_filter = request.args.get('state', type=str)
            limit = request.args.get('limit', 100, type=int)
            
            # Base query - exclude banks and financial institutions
            query = """
            SELECT 
                b.id as building_id,
                b.bbl,
                b.address as property_address,
                b.borough,
                ap.party_name as seller_name,
                ap.address_1 as seller_address_1,
                ap.address_2 as seller_address_2,
                ap.city as seller_city,
                ap.state as seller_state,
                ap.zip_code as seller_zip,
                ap.country as seller_country,
                t.doc_type,
                t.doc_date as sale_date,
                t.recorded_date,
                t.doc_amount as sale_price,
                t.crfn,
                -- Parse C/O if it exists
                CASE 
                    WHEN ap.address_2 ILIKE 'C/O%%' THEN TRIM(SUBSTRING(ap.address_2 FROM 5))
                    WHEN ap.address_1 ILIKE 'C/O%%' THEN TRIM(SUBSTRING(ap.address_1 FROM 5))
                    ELSE NULL
                END as care_of_contact,
                -- Check if multi-property owner
                (SELECT COUNT(DISTINCT building_id) 
                 FROM acris_parties ap2 
                 WHERE ap2.party_name = ap.party_name 
                 AND ap2.party_type = 'seller'
                ) as properties_sold_count
            FROM acris_parties ap
            JOIN buildings b ON ap.building_id = b.id
            JOIN acris_transactions t ON ap.transaction_id = t.id
            WHERE ap.party_type = 'seller'
            AND ap.is_lead = TRUE
            AND ap.address_1 IS NOT NULL
            AND ap.address_1 != ''
            AND t.doc_type LIKE '%%DEED%%'
            AND ap.party_name NOT ILIKE '%%bank%%'
            AND ap.party_name NOT ILIKE '%%federal%%'
            AND ap.party_name NOT ILIKE '%%credit union%%'
            AND ap.party_name NOT ILIKE '%%mortgage%%'
            AND ap.party_name NOT ILIKE '%%lending%%'
            AND ap.party_name NOT ILIKE '%%savings%%'
            AND ap.party_name NOT ILIKE '%%trust company%%'
            AND ap.party_name NOT ILIKE '%%capital%%'
            AND ap.party_name NOT ILIKE '%%funding%%'
        """
        
            params = []
        
            # Apply filters
            if min_sale_price:
                query += " AND t.doc_amount >= %s"
                params.append(min_sale_price)
        
            if state_filter:
                query += " AND ap.state = %s"
                params.append(state_filter.upper())
        
            # recorded_date is the reliable timeline field in ACRIS and is
            # covered by the partial deed-sales index. This avoids sorting
            # millions of candidate party rows for a 100-row response.
            query += " ORDER BY t.recorded_date DESC NULLS LAST, t.doc_amount DESC NULLS LAST"
            query += " LIMIT %s"
            params.append(limit)
        
            cur.execute(query, tuple(params))
            leads = cur.fetchall()
        
        # Format leads for frontend
        formatted_leads = []
        for lead in leads:
            try:
                # Build full address
                addr_parts = []
                if lead.get('seller_address_1'):
                    addr_parts.append(str(lead['seller_address_1']))
                if lead.get('seller_address_2'):
                    addr_parts.append(str(lead['seller_address_2']))
                if lead.get('seller_city'):
                    addr_parts.append(str(lead['seller_city']))
                if lead.get('seller_state'):
                    addr_parts.append(str(lead['seller_state']))
                if lead.get('seller_zip'):
                    addr_parts.append(str(lead['seller_zip']))
                
                formatted_leads.append({
                    'building_id': lead.get('building_id'),
                    'bbl': lead.get('bbl'),
                    'property_address': lead.get('property_address') or 'Unknown',
                    'borough': lead.get('borough'),
                    'seller_name': lead.get('seller_name') or 'Unknown',
                    'seller_address_full': ', '.join(addr_parts) if addr_parts else 'No address',
                    'seller_address_1': lead.get('seller_address_1'),
                    'seller_address_2': lead.get('seller_address_2'),
                    'seller_city': lead.get('seller_city'),
                    'seller_state': lead.get('seller_state'),
                    'seller_zip': lead.get('seller_zip'),
                    'care_of_contact': lead.get('care_of_contact'),
                    'sale_date': (lead['sale_date'] or lead['recorded_date']).isoformat() if (lead.get('sale_date') or lead.get('recorded_date')) else None,
                    'sale_price': float(lead['sale_price']) if lead.get('sale_price') else None,
                    'doc_type': lead.get('doc_type'),
                    'crfn': lead.get('crfn'),
                    'properties_sold_count': lead.get('properties_sold_count', 1),
                    'is_repeat_seller': lead.get('properties_sold_count', 1) > 1
                })
            except Exception as lead_error:
                print(f"Error formatting lead: {lead_error}")
                continue
        
        return jsonify({
            'leads': formatted_leads,
            'total': len(formatted_leads)
        })
        
    except Exception as e:
        import traceback
        print(f"Error fetching seller leads: {e}")
        print(traceback.format_exc())
        return jsonify({'error': str(e), 'leads': [], 'total': 0})


@app.route('/api/charts/owners')
def get_top_owners():
    """Get top property owners by permit activity"""
    try:
        with DatabaseConnection() as cur:
            cur.execute("""
                SELECT 
                    b.current_owner_name,
                    COUNT(DISTINCT p.id) as permit_count,
                    COUNT(DISTINCT b.id) as building_count,
                    SUM(COALESCE(b.total_permit_spend, 0)) as total_spend
                FROM buildings b
                INNER JOIN permits p ON b.bbl = p.bbl
                WHERE b.current_owner_name IS NOT NULL
                GROUP BY b.current_owner_name
                HAVING COUNT(DISTINCT p.id) > 0
                ORDER BY permit_count DESC
                LIMIT 15;
            """)
            
            data = cur.fetchall()
        
        return jsonify({
            'success': True,
            'labels': [row['current_owner_name'] for row in data],
            'permit_counts': [row['permit_count'] for row in data],
            'building_counts': [row['building_count'] for row in data],
            'total_spends': [float(row['total_spend']) if row['total_spend'] else 0 for row in data]
        })
        
    except Exception as e:
        print(f"Error fetching top owners: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/charts/building-ages')
def get_building_age_distribution():
    """Get distribution of building ages"""
    try:
        with DatabaseConnection() as cur:
            cur.execute("""
            SELECT 
                CASE 
                    WHEN year_built >= 2020 THEN '2020+'
                    WHEN year_built >= 2010 THEN '2010-2019'
                    WHEN year_built >= 2000 THEN '2000-2009'
                    WHEN year_built >= 1990 THEN '1990-1999'
                    WHEN year_built >= 1980 THEN '1980-1989'
                    WHEN year_built >= 1970 THEN '1970-1979'
                    WHEN year_built >= 1960 THEN '1960-1969'
                    WHEN year_built >= 1950 THEN '1950-1959'
                    WHEN year_built >= 1940 THEN '1940-1949'
                    ELSE 'Pre-1940'
                END as age_range,
                COUNT(*) as count
            FROM buildings
            WHERE year_built IS NOT NULL
            GROUP BY age_range
            ORDER BY age_range DESC;
            """)
            
            data = cur.fetchall()
        
        return jsonify({
            'success': True,
            'labels': [row['age_range'] for row in data],
            'data': [row['count'] for row in data]
        })
        
    except Exception as e:
        print(f"Error fetching building ages: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/charts/unit-distribution')
def get_unit_distribution():
    """Get distribution of building sizes by unit count"""
    try:
        with DatabaseConnection() as cur:
            cur.execute("""
                SELECT 
                    CASE 
                        WHEN residential_units = 1 THEN 'Single Family'
                        WHEN residential_units BETWEEN 2 AND 4 THEN '2-4 Units'
                        WHEN residential_units BETWEEN 5 AND 9 THEN '5-9 Units'
                        WHEN residential_units BETWEEN 10 AND 19 THEN '10-19 Units'
                        WHEN residential_units BETWEEN 20 AND 49 THEN '20-49 Units'
                        WHEN residential_units >= 50 THEN '50+ Units'
                        ELSE 'Unknown'
                    END as size_category,
                    COUNT(*) as count
                FROM buildings
                WHERE residential_units IS NOT NULL
                GROUP BY size_category
                ORDER BY 
                    CASE size_category
                        WHEN 'Single Family' THEN 1
                        WHEN '2-4 Units' THEN 2
                        WHEN '5-9 Units' THEN 3
                        WHEN '10-19 Units' THEN 4
                        WHEN '20-49 Units' THEN 5
                        WHEN '50+ Units' THEN 6
                        ELSE 7
                    END;
            """)
            
            data = cur.fetchall()
        
        return jsonify({
            'success': True,
            'labels': [row['size_category'] for row in data],
            'data': [row['count'] for row in data]
        })
        
    except Exception as e:
        print(f"Error fetching unit distribution: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/permit/<int:permit_id>')
def get_permit_detail(permit_id):
    """API endpoint for getting single permit details (JSON)"""
    try:
        with DatabaseConnection() as cur:
            # Get permit with all details including contact info from permits table
            query = """
            SELECT 
                p.*,
                -- Calculate contact count from permits table columns
                (
                    CASE WHEN p.permittee_phone IS NOT NULL AND p.permittee_phone != '' THEN 1 ELSE 0 END +
                    CASE WHEN p.owner_phone IS NOT NULL AND p.owner_phone != '' THEN 1 ELSE 0 END
                ) as contact_count,
                false as has_mobile,
                -- Aggregate contact names
                CONCAT_WS(' | ',
                    NULLIF(COALESCE(p.permittee_business_name, p.applicant), ''),
                    NULLIF(p.owner_business_name, ''),
                    NULLIF(p.superintendent_business_name, ''),
                    NULLIF(p.site_safety_mgr_business_name, '')
                ) as contact_names,
                -- Aggregate contact phones
                CONCAT_WS(' | ',
                    NULLIF(p.permittee_phone, ''),
                    NULLIF(p.owner_phone, '')
                ) as contact_phones
            FROM permits p
            WHERE p.id = %s;
            """
            
            cur.execute(query, (permit_id,))
            permit = cur.fetchone()
            
            if not permit:
                return jsonify({
                    'success': False,
                    'error': 'Permit not found'
                }), 404
            
            # Calculate lead score
            permit['lead_score'] = calculate_lead_score(permit)
        
        return jsonify({
            'success': True,
            'permit': permit
        })
        
    except Exception as e:
        print(f"Error fetching permit detail: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/permits')
@login_required
def permits_page():
    """Search and filter NYC permit leads."""
    return render_template('construction.html', active_page='permits')


@app.route('/construction')
@login_required
def construction_legacy_redirect():
    """Preserve old bookmarks while keeping one canonical permits page."""
    return redirect(url_for('permits_page'), code=301)


# ==================== CONSTRUCTION INTELLIGENCE APIs ====================

def _construction_time_filter(days_raw, mode_raw, alias='p'):
    """Validated issue-date predicate for permit-row views."""
    mode = 'inactive' if str(mode_raw or '').lower() == 'inactive' else 'within'
    if str(days_raw).lower() == 'all':
        return '', [], 'all', mode
    try:
        days = max(1, min(3650, int(days_raw)))
    except (TypeError, ValueError):
        days = 30
    operator = '<' if mode == 'inactive' else '>='
    return (
        f"{alias}.issue_date {operator} CURRENT_DATE - (%s || ' days')::interval",
        [str(days)],
        str(days),
        mode,
    )

@app.route('/api/construction/permits')
def get_construction_permits():
    """Get filtered permits for construction page with advanced filtering"""
    try:
        with DatabaseConnection() as cur:
            # Get filter parameters
            job_types = request.args.getlist('job_type')
            borough = request.args.get('borough')
            days = request.args.get('days', '30')
            activity_mode = request.args.get('permit_activity_mode', 'within')
            search = request.args.get('q', '').strip()
            min_lead_score = request.args.get('min_score', 0, type=int)
            has_contact = request.args.get('has_contact', type=str)  # 'true' or 'false'
            sort_by = request.args.get('sort', 'date')  # date, score, contacts, size
            limit = request.args.get('limit', 50, type=int)  # Reduced default from 200 to 50
            limit = min(200, limit)  # Cap at 200
            offset = request.args.get('offset', 0, type=int)  # For pagination
            
            # Build dynamic query - optimized for performance
            # Only join buildings table if sorting by size or if needed
            needs_buildings_join = sort_by == 'size'
            
            query = """
                SELECT 
                    p.id,
                    p.permit_no,
                    p.job_type,
                    p.address,
                    p.borough,
                    p.issue_date,
                    p.bbl,
                    p.bin,
                    p.applicant,
                    p.permittee_business_name,
                    p.owner_business_name,
                    p.permittee_phone,
                    p.owner_phone,
                    p.latitude,
                    p.longitude,
                    p.work_type,
                    -- Calculate contact count
                    (
                        CASE WHEN p.permittee_phone IS NOT NULL AND p.permittee_phone != '' THEN 1 ELSE 0 END +
                        CASE WHEN p.owner_phone IS NOT NULL AND p.owner_phone != '' THEN 1 ELSE 0 END
                    ) as contact_count"""
            
            if needs_buildings_join:
                query += """,
                    -- Building intelligence (only when needed)
                    b.residential_units,
                    b.total_units,
                    b.num_floors,
                    b.building_sqft,
                    b.assessed_total_value,
                    b.purchase_price,
                    b.current_owner_name
                FROM permits p
                LEFT JOIN buildings b ON p.bbl = b.bbl"""
            else:
                query += """
                FROM permits p"""
            
            query += """
                WHERE 1=1
            """
        
            params = []
        
            # Handle time period filter. On this permit-row page, inactive
            # means records older than the cutoff (property-level inactivity
            # lives on Properties and Search Explorer).
            time_sql, time_params, days, activity_mode = (
                _construction_time_filter(days, activity_mode, alias='p'))
            if time_sql:
                query += f" AND {time_sql}"
                params.extend(time_params)
        
            # Apply filters
            if job_types:
                placeholders = ','.join(['%s'] * len(job_types))
                query += f" AND p.job_type IN ({placeholders})"
                params.extend(job_types)
        
            if borough:
                query += " AND p.borough = %s"
                params.append(borough)

            if search:
                query += """ AND (
                    p.address ILIKE %s OR p.permit_no ILIKE %s OR
                    p.applicant ILIKE %s OR p.permittee_business_name ILIKE %s OR
                    p.owner_business_name ILIKE %s OR p.bbl ILIKE %s
                )"""
                term = f'%{search}%'
                params.extend([term] * 6)
        
            if has_contact == 'true':
                query += " AND (p.permittee_phone IS NOT NULL OR p.owner_phone IS NOT NULL)"
        
            # Sorting - default to newest first
            if sort_by == 'score':
                query += " ORDER BY contact_count DESC, p.issue_date DESC"
            elif sort_by == 'contacts':
                query += " ORDER BY contact_count DESC"
            elif sort_by == 'size':
                query += " ORDER BY b.total_units DESC NULLS LAST, b.building_sqft DESC NULLS LAST"
            else:
                # Default to date descending (newest first)
                query += " ORDER BY p.issue_date DESC"
        
            query += " LIMIT %s OFFSET %s"
            params.extend([limit, offset])
        
            cur.execute(query, tuple(params))
            permits = cur.fetchall()
        
            # Get total count for pagination - optimized without join
            count_query = """
                SELECT COUNT(*) 
                FROM permits p
                WHERE 1=1
            """
            count_params = []
        
            # Apply same filters to count query
            if time_sql:
                count_query += f" AND {time_sql}"
                count_params.extend(time_params)
        
            if job_types:
                placeholders = ','.join(['%s'] * len(job_types))
                count_query += f" AND p.job_type IN ({placeholders})"
                count_params.extend(job_types)
        
            if borough:
                count_query += " AND p.borough = %s"
                count_params.append(borough)

            if search:
                count_query += """ AND (
                    p.address ILIKE %s OR p.permit_no ILIKE %s OR
                    p.applicant ILIKE %s OR p.permittee_business_name ILIKE %s OR
                    p.owner_business_name ILIKE %s OR p.bbl ILIKE %s
                )"""
                term = f'%{search}%'
                count_params.extend([term] * 6)
        
            if has_contact == 'true':
                count_query += " AND (p.permittee_phone IS NOT NULL OR p.owner_phone IS NOT NULL)"
        
            cur.execute(count_query, tuple(count_params))
            total_count = cur.fetchone()['count']
        
            # Calculate lead scores
            results = []
            for permit in permits:
                lead_score = calculate_lead_score(permit)
            
                # Apply lead score filter
                if lead_score >= min_lead_score:
                    permit_dict = dict(permit)
                    permit_dict['lead_score'] = lead_score
                    results.append(permit_dict)
        
            # Sort by lead score if requested (after calculating scores)
            if sort_by == 'score':
                results.sort(key=lambda x: x.get('lead_score', 0), reverse=True)
        
            return jsonify({
                'success': True,
                'permits': results,
                'count': len(results),
                'total_count': total_count,
                'has_more': (offset + limit) < total_count,
                'pagination': {
                    'limit': limit,
                    'offset': offset,
                    'page': (offset // limit) + 1,
                    'total_pages': (total_count + limit - 1) // limit
                },
                'filters_applied': {
                    'job_types': job_types,
                    'borough': borough,
                    'days': days,
                    'permit_activity_mode': activity_mode,
                    'q': search,
                    'min_score': min_lead_score,
                    'has_contact': has_contact
                }
            })
        
    except Exception as e:
        print(f"Error fetching construction permits: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/construction/stats')
@cache.cached(timeout=60, query_string=True)
def get_construction_stats():
    """Get quick stats for construction dashboard"""
    try:
        with DatabaseConnection() as cur:
            days = request.args.get('days', '30')
            activity_mode = request.args.get('permit_activity_mode', 'within')
            time_sql, time_params, days, activity_mode = (
                _construction_time_filter(days, activity_mode, alias='p'))
            
            # Build WHERE clause based on time period
            if not time_sql:
                where_clause = "WHERE 1=1"
                params = ()
            else:
                where_clause = f"WHERE {time_sql.replace('p.issue_date', 'issue_date')}"
                params = tuple(time_params)
            
            # Total permits in time period
            cur.execute(f"""
                SELECT COUNT(*) as total
                FROM permits
                {where_clause}
            """, params)
            total_permits = cur.fetchone()['total']
            
            # Permits with contacts
            cur.execute(f"""
                SELECT COUNT(*) as total
                FROM permits
                {where_clause}
                AND (permittee_phone IS NOT NULL OR owner_phone IS NOT NULL)
            """, params)
            with_contacts = cur.fetchone()['total']
            
            # Hot leads (estimated with contact count > 0 and recent)
            if days == 'all':
                hot_params = ()
                hot_where = "WHERE (permittee_phone IS NOT NULL OR owner_phone IS NOT NULL) AND issue_date >= CURRENT_DATE - INTERVAL '7 days'"
            else:
                hot_params = params
                hot_where = f"{where_clause} AND (permittee_phone IS NOT NULL OR owner_phone IS NOT NULL) AND issue_date >= CURRENT_DATE - INTERVAL '7 days'"
            
            cur.execute(f"""
                SELECT COUNT(*) as total
                FROM permits
                {hot_where}
            """, hot_params)
            hot_leads = cur.fetchone()['total']
            
            # Total estimated value (from ACRIS purchase prices)
            value_where = where_clause.replace('issue_date', 'p.issue_date') + " AND b.purchase_price IS NOT NULL"
            cur.execute(f"""
                SELECT COALESCE(SUM(b.purchase_price), 0) as total_value
                FROM permits p
                LEFT JOIN buildings b ON p.bbl = b.bbl
                {value_where}
            """, params)
            total_value = cur.fetchone()['total_value']
            
            # Job type breakdown
            cur.execute(f"""
                SELECT job_type, COUNT(*) as count
                FROM permits
                {where_clause}
                GROUP BY job_type
                ORDER BY count DESC
                LIMIT 10
            """, params)
            job_types = cur.fetchall()
            
            # Borough breakdown
            cur.execute(f"""
                SELECT borough, COUNT(*) as count
                FROM permits
                {where_clause}
                AND borough IS NOT NULL
                GROUP BY borough
                ORDER BY count DESC
            """, params)
            boroughs = cur.fetchall()
        
        return jsonify({
            'success': True,
            'stats': {
                'total_permits': total_permits,
                'with_contacts': with_contacts,
                'hot_leads': hot_leads,
                'total_value': float(total_value) if total_value else 0,
                'job_types': [dict(row) for row in job_types],
                'boroughs': [dict(row) for row in boroughs]
            }
        })
        
    except Exception as e:
        print(f"Error fetching construction stats: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/construction/map-data')
def get_construction_map_data():
    """Get geocoded permits for map visualization"""
    try:
        with DatabaseConnection() as cur:
            days = request.args.get('days', '30')
            activity_mode = request.args.get('permit_activity_mode', 'within')
            job_types = request.args.getlist('job_type')
            borough = request.args.get('borough')
            
            query = """
            SELECT 
                p.id,
                p.permit_no,
                p.job_type,
                p.address,
                p.borough,
                p.issue_date,
                p.latitude,
                p.longitude,
                p.permittee_business_name,
                p.owner_business_name,
                (
                    CASE WHEN p.permittee_phone IS NOT NULL AND p.permittee_phone != '' THEN 1 ELSE 0 END +
                    CASE WHEN p.owner_phone IS NOT NULL AND p.owner_phone != '' THEN 1 ELSE 0 END
                ) as contact_count,
                false as has_mobile
            FROM permits p
            WHERE p.latitude IS NOT NULL 
                AND p.longitude IS NOT NULL
                AND p.latitude BETWEEN 40.4 AND 41.0
                AND p.longitude BETWEEN -74.3 AND -73.7
        """
        
            params = []
            
            # Handle time period
            time_sql, time_params, _days, _mode = (
                _construction_time_filter(days, activity_mode, alias='p'))
            if time_sql:
                query += f" AND {time_sql}"
                params.extend(time_params)
            
            if job_types:
                placeholders = ','.join(['%s'] * len(job_types))
                query += f" AND p.job_type IN ({placeholders})"
                params.extend(job_types)
            
            if borough:
                query += " AND p.borough = %s"
                params.append(borough)
            
            # Limit map markers for performance - prioritize recent/high-value permits
            query += " ORDER BY p.issue_date DESC LIMIT 500"
            
            cur.execute(query, tuple(params))
            locations = cur.fetchall()
            
            # Convert to list of dicts while cursor is still open
            locations_list = [dict(loc) for loc in locations]
        
        # Add lead scores to map locations (outside the context manager)
        locations_with_scores = []
        for loc in locations_list:
            loc['lead_score'] = calculate_lead_score(loc)
            locations_with_scores.append(loc)
        
        return jsonify({
            'success': True,
            'locations': locations_with_scores,
            'count': len(locations_with_scores)
        })
        
    except Exception as e:
        print(f"Error fetching map data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/construction/contractors')
def get_top_contractors():
    """Get top contractors by permit activity"""
    try:
        with DatabaseConnection() as cur:
            days = request.args.get('days', '90')
            activity_mode = request.args.get('permit_activity_mode', 'within')
            limit_count = request.args.get('limit', 20, type=int)
            time_sql, time_params, days, activity_mode = (
                _construction_time_filter(days, activity_mode, alias='p'))
            
            # Top contractors by permit count
            query = """
                SELECT 
                    COALESCE(permittee_business_name, applicant, 'Unknown') as contractor_name,
                    COUNT(*) as permit_count,
                    STRING_AGG(DISTINCT job_type, ', ') as job_types,
                    STRING_AGG(DISTINCT borough, ', ') as boroughs,
                    MAX(issue_date) as most_recent
                FROM permits p
                WHERE 1=1
                {time_clause}
                AND (permittee_business_name IS NOT NULL OR applicant IS NOT NULL)
                GROUP BY COALESCE(permittee_business_name, applicant, 'Unknown')
                HAVING COUNT(*) > 1
                ORDER BY permit_count DESC
                LIMIT %s
            """.format(time_clause=f'AND {time_sql}' if time_sql else '')
            
            cur.execute(query, tuple(time_params + [limit_count]))
            contractors = cur.fetchall()
        
        return jsonify({
            'success': True,
            'contractors': [dict(row) for row in contractors],
            'count': len(contractors),
            'period_days': days
        })
        
    except Exception as e:
        print(f"Error fetching contractors: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/construction/export')
@login_required
def export_construction_permits():
    """Export permits to CSV"""
    try:
        import csv
        from io import StringIO
        from flask import make_response
        
        with DatabaseConnection() as cur:
            # Get same filters as main query
            job_types = request.args.getlist('job_type')
            borough = request.args.get('borough')
            days = request.args.get('days', '30')
            activity_mode = request.args.get('permit_activity_mode', 'within')
            time_sql, time_params, days, activity_mode = (
                _construction_time_filter(days, activity_mode, alias='p'))
            
            query = """
            SELECT 
                p.permit_no,
                p.job_type,
                p.address,
                p.borough,
                p.issue_date,
                p.applicant,
                p.permittee_business_name,
                p.owner_business_name,
                p.permittee_phone,
                p.owner_phone,
                p.bbl,
                b.residential_units,
                b.total_units,
                b.building_sqft,
                b.current_owner_name
            FROM permits p
            LEFT JOIN buildings b ON p.bbl = b.bbl
            WHERE 1=1
        """
        
            params = list(time_params)
            if time_sql:
                query += f" AND {time_sql}"
        
            if job_types:
                placeholders = ','.join(['%s'] * len(job_types))
                query += f" AND p.job_type IN ({placeholders})"
                params.extend(job_types)
        
            if borough:
                query += " AND p.borough = %s"
                params.append(borough)
        
            query += " ORDER BY p.issue_date DESC LIMIT 500"
        
            cur.execute(query, tuple(params))
            permits = cur.fetchall()
        
        # Create CSV
        si = StringIO()
        writer = csv.writer(si)
        
        # Header
        writer.writerow([
            'Permit Number', 'Job Type', 'Address', 'Borough', 'Issue Date',
            'Applicant', 'Permittee', 'Owner', 'Permittee Phone', 'Owner Phone',
            'BBL', 'Residential Units', 'Total Units', 'Building Sqft', 'Current Owner'
        ])
        
        # Data rows
        for permit in permits:
            writer.writerow([
                permit['permit_no'],
                permit['job_type'],
                permit['address'],
                permit['borough'],
                permit['issue_date'],
                permit['applicant'],
                permit['permittee_business_name'],
                permit['owner_business_name'],
                permit['permittee_phone'],
                permit['owner_phone'],
                permit['bbl'],
                permit['residential_units'],
                permit['total_units'],
                permit['building_sqft'],
                permit['current_owner_name']
            ])
        
        # Create response
        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = "attachment; filename=construction_permits.csv"
        output.headers["Content-type"] = "text/csv"
        
        # Log the export
        log_export(
            export_type='csv',
            record_count=len(permits),
            filter_params={
                'job_types': job_types,
                'borough': borough,
                'days': days,
                'permit_activity_mode': activity_mode,
            }
        )
        
        return output
        
    except Exception as e:
        print(f"Error exporting permits: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/search-results')
@login_required
def search_results():
    """Grouped, filterable universal-search explorer."""
    query = request.args.get('q', '')
    return render_template(
        'search_results.html',
        query=query,
        building_class_groups=building_class_options(),
        active_page='home',
    )


def _safe_properties_return_to(value):
    """Allow only a local /properties path for profile breadcrumbs."""
    parsed = urlsplit(value or '/properties')
    if parsed.scheme or parsed.netloc or parsed.path != '/properties':
        return '/properties'
    target = parsed.path
    if parsed.query:
        target += f'?{parsed.query}'
    return target


@app.route('/property/<bbl>')
@login_required
def property_detail(bbl):
    """Comprehensive building intelligence profile page"""
    return_to = _safe_properties_return_to(request.args.get('return_to'))
    return render_template(
        'building_profile.html', bbl=bbl, return_to=return_to,
        active_page='properties')


@app.route('/api/property/<bbl>/violations')
def api_property_violations(bbl):
    """Fetch HPD violations for a property from NYC Open Data"""
    try:
        # NYC Open Data HPD Violations API
        # Dataset: HPD Violations
        api_url = "https://data.cityofnewyork.us/resource/wvxf-dwi5.json"
        
        # Query by BBL
        params = {
            '$where': f"boroid='{bbl[0]}' AND block='{int(bbl[1:6])}' AND lot='{int(bbl[6:])}'",
            '$limit': 500,
            '$order': 'inspectiondate DESC'
        }
        
        print(f"Fetching violations for BBL {bbl} with params: {params}")
        
        response = requests.get(api_url, params=params, timeout=15)
        
        print(f"NYC Open Data response status: {response.status_code}")
        
        if response.status_code != 200:
            return jsonify({
                'success': False,
                'error': f'NYC Open Data API returned status {response.status_code}'
            })
        
        violations_data = response.json()
        print(f"Received {len(violations_data)} violations from NYC Open Data")
        
        # Process and categorize violations
        violations = []
        for v in violations_data:
            violation = {
                'violation_id': v.get('violationid'),
                'class': v.get('class'),
                'inspection_date': v.get('inspectiondate'),
                'approved_date': v.get('approveddate'),
                'original_certify_date': v.get('originalcertifybydate'),
                'current_status': v.get('violationstatus'),
                'description': v.get('novdescription'),
                'order_number': v.get('ordernumber'),
                'nov_issued_date': v.get('novissueddate'),
                'severity': v.get('currentstatusid'),
                'apartment': v.get('apartment', 'N/A'),
                'story': v.get('story', 'N/A'),
                'is_open': v.get('violationstatus', '').upper() == 'OPEN'
            }
            violations.append(violation)
        
        # Categorize violations
        open_violations = [v for v in violations if v['is_open']]
        closed_violations = [v for v in violations if not v['is_open']]
        
        # Group by class
        by_class = {}
        for v in violations:
            vclass = v['class'] or 'Unknown'
            if vclass not in by_class:
                by_class[vclass] = {'count': 0, 'open': 0}
            by_class[vclass]['count'] += 1
            if v['is_open']:
                by_class[vclass]['open'] += 1
        
        return jsonify({
            'success': True,
            'total_count': len(violations),
            'total_violations': len(violations),
            'open_count': len(open_violations),
            'open_violations': len(open_violations),
            'closed_count': len(closed_violations),
            'closed_violations': len(closed_violations),
            'violations': violations[:100],  # Limit to first 100 for display
            'all_items': violations[:100],  # Legacy compatibility
            'by_class': by_class,
            'has_more': len(violations) > 100,
            # Include complaint data for compatibility
            'complaints': [],
            'open_complaints': 0,
            'closed_complaints': 0,
            'total_complaints': 0
        })
        
    except requests.exceptions.Timeout:
        return jsonify({
            'success': False,
            'error': 'Request timeout - NYC Open Data API is slow'
        })
    except Exception as e:
        print(f"Error fetching violations: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        })


def _safety_violation_is_open(status):
    """Lifecycle states in DOB NOW: Safety that still require attention."""
    normalized = (status or '').strip().upper()
    return normalized == 'ACTIVE' or 'PENDING' in normalized


@app.route('/api/property/<bbl>/safety-violations')
@cache.cached(timeout=300)
def api_property_safety_violations(bbl):
    """Live DOB NOW Safety violations, a separate daily feed from BIS.

    The legacy DOB Violations table does not contain the newer boiler,
    elevator, facade, benchmarking, gas-piping and Local Law civil-penalty
    records. This endpoint deliberately keeps the two sources distinct.
    """
    if len(bbl) != 10 or not bbl.isdigit():
        return jsonify({'success': False, 'error': 'BBL must contain 10 digits'}), 400

    try:
        rows = socrata.get_all(
            'dob_safety_violations', page_size=1000, max_rows=10000,
            **{
                '$where': f'bbl={int(bbl)}',
                '$select': (
                    'bin,violation_issue_date,violation_number,violation_type,'
                    'violation_remarks,violation_status,device_number,device_type,'
                    'cycle_end_date,borough,house_number,street,zip,bbl'
                ),
                '$order': 'violation_issue_date DESC, violation_number DESC',
            },
        )

        status_counts = {}
        device_counts = {}
        violations = []
        open_count = 0
        for row in rows:
            status = (row.get('violation_status') or 'Unknown').strip()
            device = (row.get('device_type') or 'Other safety').strip()
            is_open = _safety_violation_is_open(status)
            open_count += int(is_open)
            status_counts[status] = status_counts.get(status, 0) + 1
            device_counts[device] = device_counts.get(device, 0) + 1
            if len(violations) < 500:
                violations.append({
                    'violation_number': row.get('violation_number'),
                    'violation_type': row.get('violation_type'),
                    'remarks': row.get('violation_remarks'),
                    'status': status,
                    'is_open': is_open,
                    'issue_date': row.get('violation_issue_date'),
                    'cycle_end_date': row.get('cycle_end_date'),
                    'device_type': device,
                    'device_number': row.get('device_number'),
                    'bin': row.get('bin'),
                })

        return jsonify({
            'success': True,
            'bbl': bbl,
            'total_count': len(rows),
            'open_count': open_count,
            'closed_count': len(rows) - open_count,
            'by_status': [
                {'status': key, 'count': value}
                for key, value in sorted(
                    status_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
            'by_device_type': [
                {'device_type': key, 'count': value}
                for key, value in sorted(
                    device_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
            'violations': violations,
            'has_more': len(rows) > len(violations),
            'checked_at': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
            'source': {
                'name': 'DOB NOW: Safety Violations',
                'dataset_id': '855j-jady',
                'update_frequency': 'Daily',
                'url': 'https://data.cityofnewyork.us/d/855j-jady',
            },
        })
    except Exception as exc:
        print(f'Error fetching DOB Safety violations for {bbl}: {exc}')
        return jsonify({
            'success': False,
            'error': 'DOB Safety violations are temporarily unavailable',
        }), 502


@app.route('/api/property/<bbl>/building-facts')
@cache.cached(timeout=3600)
def api_property_building_facts(bbl):
    """Return the latest tax-lot and building facts from NYC PLUTO.

    Property profiles otherwise use the nightly warehouse row. This small live
    check prevents missing units or physical details from lingering on a lead
    when PLUTO has already published them.
    """
    if len(bbl) != 10 or not bbl.isdigit():
        return jsonify({'success': False, 'error': 'BBL must contain 10 digits'}), 400

    try:
        rows = socrata.get('pluto', **{
            '$where': f'bbl={int(bbl)}',
            '$limit': 1,
        })
        if not rows:
            return jsonify({
                'success': False,
                'error': 'No current PLUTO tax-lot record was found for this BBL',
            }), 404

        return jsonify({
            'success': True,
            'bbl': bbl,
            'facts': normalize_pluto_record(rows[0]),
            'checked_at': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
            'source': {
                'name': 'NYC Primary Land Use Tax Lot Output (PLUTO)',
                'dataset_id': '64uk-42ks',
                'update_frequency': 'Quarterly',
                'url': 'https://data.cityofnewyork.us/d/64uk-42ks',
            },
        })
    except Exception as exc:
        print(f'Error fetching live PLUTO facts for {bbl}: {exc}')
        return jsonify({
            'success': False,
            'error': 'Latest PLUTO building facts are temporarily unavailable',
        }), 502


@app.route('/api/property/<bbl>/hpd-info')
def api_property_hpd_info(bbl):
    """Fetch comprehensive HPD data: litigation, work orders, and fees"""
    try:
        # Dataset IDs
        datasets = {
            'litigation': '59kj-x8nc',  # Housing Litigations
            'omo': 'mdbu-nrqn',          # Open Market Order Charges
            'hwo': 'sbnd-xujn',          # Handyman Work Order Charges
            'fees': 'cp6j-7bjj'          # Fee Charges
        }
        
        results = {
            'success': True,
            'litigation': [],
            'omo_charges': [],
            'hwo_charges': [],
            'fees': [],
            'summary': {
                'total_litigation': 0,
                'active_litigation': 0,
                'total_work_orders': 0,
                'total_charges_amount': 0,
                'total_fees': 0,
                'total_fees_amount': 0
            }
        }
        
        # Fetch Housing Litigations
        try:
            litigation_url = f"https://data.cityofnewyork.us/resource/{datasets['litigation']}.json"
            litigation_params = {'bbl': bbl, '$limit': 1000, '$order': 'caseopendate DESC'}
            litigation_response = requests.get(litigation_url, params=litigation_params, timeout=10)
            
            if litigation_response.status_code == 200:
                litigation_data = litigation_response.json()
                results['litigation'] = litigation_data
                results['summary']['total_litigation'] = len(litigation_data)
                results['summary']['active_litigation'] = sum(1 for lit in litigation_data 
                                                             if lit.get('casestatus', '').upper() != 'CLOSED')
        except Exception as e:
            print(f"Error fetching litigation: {e}")
        
        # Fetch OMO Charges
        try:
            omo_url = f"https://data.cityofnewyork.us/resource/{datasets['omo']}.json"
            omo_params = {'bbl': bbl, '$limit': 1000, '$order': 'omocreatedate DESC'}
            omo_response = requests.get(omo_url, params=omo_params, timeout=10)
            
            if omo_response.status_code == 200:
                omo_data = omo_response.json()
                results['omo_charges'] = omo_data
                
                # Calculate total charges
                for omo in omo_data:
                    amount = float(omo.get('omoawardamount', 0) or 0)
                    results['summary']['total_charges_amount'] += amount
        except Exception as e:
            print(f"Error fetching OMO charges: {e}")
        
        # Fetch HWO Charges
        try:
            hwo_url = f"https://data.cityofnewyork.us/resource/{datasets['hwo']}.json"
            hwo_params = {'bbl': bbl, '$limit': 1000, '$order': 'hwocreatedate DESC'}
            hwo_response = requests.get(hwo_url, params=hwo_params, timeout=10)
            
            if hwo_response.status_code == 200:
                hwo_data = hwo_response.json()
                results['hwo_charges'] = hwo_data
                
                # Calculate total charges
                for hwo in hwo_data:
                    amount = float(hwo.get('chargeamount', 0) or 0)
                    results['summary']['total_charges_amount'] += amount
        except Exception as e:
            print(f"Error fetching HWO charges: {e}")
        
        # Fetch Fees
        try:
            fees_url = f"https://data.cityofnewyork.us/resource/{datasets['fees']}.json"
            fees_params = {'bbl': bbl, '$limit': 1000, '$order': 'feeissueddate DESC'}
            fees_response = requests.get(fees_url, params=fees_params, timeout=10)
            
            if fees_response.status_code == 200:
                fees_data = fees_response.json()
                results['fees'] = fees_data
                results['summary']['total_fees'] = len(fees_data)
                
                # Calculate total fees
                for fee in fees_data:
                    amount = float(fee.get('feeamount', 0) or 0)
                    results['summary']['total_fees_amount'] += amount
        except Exception as e:
            print(f"Error fetching fees: {e}")
        
        # Total work orders
        results['summary']['total_work_orders'] = len(results['omo_charges']) + len(results['hwo_charges'])
        
        return jsonify(results)
        
    except Exception as e:
        print(f"Error fetching HPD info: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        })


import re

# Pre-compiled regex patterns for address normalization (more efficient than compiling each time)
ADDRESS_ABBREVIATIONS = {
    'STREET': 'ST', 'AVENUE': 'AVE', 'BOULEVARD': 'BLVD', 'DRIVE': 'DR',
    'LANE': 'LN', 'ROAD': 'RD', 'PLACE': 'PL', 'COURT': 'CT',
    'TERRACE': 'TER', 'PARKWAY': 'PKWY', 'HIGHWAY': 'HWY', 'CIRCLE': 'CIR',
    'SQUARE': 'SQ', 'NORTH': 'N', 'SOUTH': 'S', 'EAST': 'E', 'WEST': 'W'
}

# Build reverse mapping
ADDRESS_EXPANSIONS = {v: k for k, v in ADDRESS_ABBREVIATIONS.items()}

# Pre-compile patterns for both directions
ABBREV_PATTERNS = {word: re.compile(r'\b' + word + r'\b', re.IGNORECASE) 
                   for word in list(ADDRESS_ABBREVIATIONS.keys()) + list(ADDRESS_EXPANSIONS.keys())}


def normalize_address_simple(address):
    """
    Simplified address normalization - converts to uppercase, removes punctuation,
    and standardizes common abbreviations.
    """
    if not address:
        return address
    
    # Uppercase and clean
    addr = ' '.join(address.upper().replace(',', ' ').replace('.', ' ').split())
    
    # Apply abbreviations
    for full, abbrev in ADDRESS_ABBREVIATIONS.items():
        addr = ABBREV_PATTERNS[full].sub(abbrev, addr)
    
    return addr


def get_search_variants(query):
    """
    Generate search variants for a query. Returns list of patterns.
    Simplified version that only generates the most useful variants.
    """
    if not query:
        return [query]
    
    variants = set()
    q = query.upper().strip()
    variants.add(q)
    variants.add(normalize_address_simple(q))
    
    # Add expansion variants (ST -> STREET, etc.)
    for abbrev, full in ADDRESS_EXPANSIONS.items():
        if ABBREV_PATTERNS[abbrev].search(q):
            variants.add(ABBREV_PATTERNS[abbrev].sub(full, q))
    
    return list(variants)


def escape_like_pattern(s):
    """Escape special characters for LIKE/ILIKE patterns"""
    return s.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def build_token_search_clause(field, tokens, params_dict, param_prefix):
    """
    Build a SQL clause that matches all tokens in any order.
    For "810 sterling" this creates: field ILIKE '%810%' AND field ILIKE '%sterling%'
    Returns (sql_clause, updated_params_dict)
    """
    clauses = []
    for i, token in enumerate(tokens):
        param_name = f"{param_prefix}_{i}"
        clauses.append(f"{field} ILIKE %({param_name})s")
        params_dict[param_name] = f'%{escape_like_pattern(token)}%'
    return ' AND '.join(clauses), params_dict


# Universal-search explorer -------------------------------------------------
#
# The homepage search used to collapse every match to one flat property card.
# That made a permittee search look like a list of unrelated addresses and
# discarded the dimensions that make the result useful.  The explorer keeps
# one candidate set and lets the user change only its grain: property, owner,
# job type, or individual permit.

_SEARCH_MATCH_FIELDS = {
    'permittee', 'applicant', 'property_owner', 'permit_owner', 'address',
    'permit', 'phone',
}


def _search_bind_values(params, prefix, values):
    """Named placeholders for a safe IN list."""
    placeholders = []
    for index, value in enumerate(values):
        key = f'{prefix}_{index}'
        params[key] = value
        placeholders.append(f'%({key})s')
    return ','.join(placeholders)


def _search_match_sql(query, match_fields, params, permit_alias='p', building_alias='b'):
    """Return separate building and permit text-match expressions.

    Separating them is important.  A permittee query should only count the
    permittee's permits; an address/owner query should include every related
    permit on the directly matched property.
    """
    roles = set(match_fields) & _SEARCH_MATCH_FIELDS
    if not roles:
        roles = set(_SEARCH_MATCH_FIELDS)

    pattern = f'%{escape_like_pattern(query)}%'
    params['search_pattern'] = pattern
    params['search_exact'] = query.strip()
    params['search_variants'] = [
        f'%{escape_like_pattern(value)}%' for value in get_search_variants(query)
    ] or [pattern]

    building_parts = []
    permit_parts = []

    if 'address' in roles:
        building_parts.extend([
            f"{building_alias}.bbl ILIKE %(search_pattern)s",
            f"{building_alias}.address ILIKE ANY(%(search_variants)s)",
        ])
        permit_parts.extend([
            f"{permit_alias}.bbl ILIKE %(search_pattern)s",
            f"{permit_alias}.address ILIKE ANY(%(search_variants)s)",
            f"{permit_alias}.zip_code = %(search_exact)s",
        ])
    if 'property_owner' in roles:
        building_parts.extend([
            f"{building_alias}.current_owner_name ILIKE %(search_pattern)s",
            f"{building_alias}.owner_name_rpad ILIKE %(search_pattern)s",
            f"{building_alias}.owner_name_hpd ILIKE %(search_pattern)s",
            f"{building_alias}.sale_buyer_primary ILIKE %(search_pattern)s",
        ])
    if 'permittee' in roles:
        permit_parts.extend([
            f"{permit_alias}.permittee_business_name ILIKE %(search_pattern)s",
            f"CONCAT_WS(' ', {permit_alias}.permittee_first_name, "
            f"{permit_alias}.permittee_last_name) ILIKE %(search_pattern)s",
        ])
    if 'applicant' in roles:
        permit_parts.append(
            f"{permit_alias}.applicant ILIKE %(search_pattern)s")
    if 'permit_owner' in roles:
        permit_parts.extend([
            f"{permit_alias}.owner_business_name ILIKE %(search_pattern)s",
            f"CONCAT_WS(' ', {permit_alias}.owner_first_name, "
            f"{permit_alias}.owner_last_name) ILIKE %(search_pattern)s",
        ])
    if 'permit' in roles:
        permit_parts.extend([
            f"{permit_alias}.permit_no ILIKE %(search_pattern)s",
            f"{permit_alias}.job_number ILIKE %(search_pattern)s",
        ])
    if 'phone' in roles:
        permit_parts.extend([
            f"{permit_alias}.permittee_phone ILIKE %(search_pattern)s",
            f"{permit_alias}.owner_phone ILIKE %(search_pattern)s",
        ])

    return (
        '(' + ' OR '.join(building_parts) + ')' if building_parts else 'FALSE',
        '(' + ' OR '.join(permit_parts) + ')' if permit_parts else 'FALSE',
        roles,
    )


def _current_permit_sql(alias='p'):
    """Best available definition of a current/open permit across DOB feeds."""
    status = (
        f"UPPER(BTRIM(COALESCE(NULLIF({alias}.permit_status, ''), "
        f"NULLIF({alias}.status, ''), NULLIF({alias}.filing_status, ''), '')))"
    )
    return f"""(
        (
            {alias}.exp_date >= CURRENT_DATE
            AND {status} NOT IN (
                'EXPIRED', 'CANCELLED', 'CANCELED', 'REVOKED',
                'SIGNED OFF', 'WITHDRAWN', 'DISAPPROVED'
            )
        )
        OR (
            {status} IN (
                'ACTIVE', 'ISSUED', 'PERMIT ISSUED', 'PERMIT ENTIRE',
                'APPROVED', 'IN PROCESS'
            )
            AND ({alias}.exp_date IS NULL OR {alias}.exp_date >= CURRENT_DATE)
            AND (
                {alias}.issue_date IS NULL
                OR {alias}.issue_date >= CURRENT_DATE - INTERVAL '2 years'
            )
        )
    )"""


def _search_match_role_case(roles, permit_alias='p', building_alias='b'):
    """Human explanation for why a permit belongs to the current result."""
    parts = []
    if 'permittee' in roles:
        parts.append(
            f"WHEN ({permit_alias}.permittee_business_name ILIKE %(search_pattern)s OR "
            f"CONCAT_WS(' ', {permit_alias}.permittee_first_name, "
            f"{permit_alias}.permittee_last_name) ILIKE %(search_pattern)s) "
            "THEN 'Permittee'")
    if 'applicant' in roles:
        parts.append(
            f"WHEN {permit_alias}.applicant ILIKE %(search_pattern)s THEN 'Applicant'")
    if 'permit_owner' in roles:
        parts.append(
            f"WHEN ({permit_alias}.owner_business_name ILIKE %(search_pattern)s OR "
            f"CONCAT_WS(' ', {permit_alias}.owner_first_name, "
            f"{permit_alias}.owner_last_name) ILIKE %(search_pattern)s) "
            "THEN 'Permit owner'")
    if 'permit' in roles:
        parts.append(
            f"WHEN ({permit_alias}.permit_no ILIKE %(search_pattern)s OR "
            f"{permit_alias}.job_number ILIKE %(search_pattern)s) THEN 'Permit number'")
    if 'phone' in roles:
        parts.append(
            f"WHEN ({permit_alias}.permittee_phone ILIKE %(search_pattern)s OR "
            f"{permit_alias}.owner_phone ILIKE %(search_pattern)s) THEN 'Phone'")
    if 'address' in roles:
        parts.append(
            f"WHEN ({permit_alias}.address ILIKE ANY(%(search_variants)s) OR "
            f"{building_alias}.address ILIKE ANY(%(search_variants)s)) THEN 'Address'")
    if 'property_owner' in roles:
        parts.append(
            f"WHEN ({building_alias}.sale_buyer_primary ILIKE %(search_pattern)s OR "
            f"{building_alias}.current_owner_name ILIKE %(search_pattern)s OR "
            f"{building_alias}.owner_name_hpd ILIKE %(search_pattern)s OR "
            f"{building_alias}.owner_name_rpad ILIKE %(search_pattern)s) "
            "THEN 'Property owner'")
    return 'CASE ' + ' '.join(parts) + " ELSE 'Related permit' END"


def _build_search_explorer_cte(args):
    """Build the shared, parameterized candidate set for every grouping."""
    query = (args.get('q') or '').strip()
    if len(query) < 2:
        raise ValueError('Search for at least two characters')

    params = {}
    fields = _multi_param(args, 'match_field', allowed=_SEARCH_MATCH_FIELDS)
    building_match, permit_match, roles = _search_match_sql(
        query, fields, params)

    building_filters = []
    property_types = _multi_param(
        args, 'property_type', allowed=set(_PROPERTY_TYPE_SQL))
    if property_types:
        building_filters.append(
            '(' + ' OR '.join(_PROPERTY_TYPE_SQL[value]
                              for value in property_types) + ')')

    building_classes = _multi_param(args, 'building_class', upper=True)
    if building_classes:
        values = [f'{value}%' for value in building_classes]
        placeholders = _search_bind_values(
            params, 'building_class', values)
        building_filters.append(
            f"UPPER(COALESCE(b.building_class, '')) LIKE ANY(ARRAY[{placeholders}])")

    boroughs = _parse_boroughs_param(
        args.get('borough', ''), multi_source=args)
    if boroughs:
        placeholders = _search_bind_values(params, 'borough', boroughs)
        building_filters.append(
            f"LEFT(b.bbl, 1) IN ({placeholders})")

    numeric_building_filters = (
        ('min_units', 'COALESCE(b.total_units, 0) >=', int),
        ('max_units', 'COALESCE(b.total_units, 0) <=', int),
        ('min_sqft', 'COALESCE(b.building_sqft, 0) >=', int),
        ('max_sqft', 'COALESCE(b.building_sqft, 0) <=', int),
        ('min_value', 'COALESCE(b.assessed_total_value, 0) >=', float),
        ('max_value', 'COALESCE(b.assessed_total_value, 0) <=', float),
    )
    for name, expression, caster in numeric_building_filters:
        try:
            value = caster(args.get(name)) if args.get(name) not in (None, '') else None
        except (TypeError, ValueError):
            value = None
        if value is not None:
            params[name] = value
            building_filters.append(f'{expression} %({name})s')

    violations = {value.lower() for value in _multi_param(args, 'has_violations')}
    if violations == {'true'}:
        building_filters.append('COALESCE(b.hpd_open_violations, 0) > 0')
    elif violations == {'false'}:
        building_filters.append('COALESCE(b.hpd_open_violations, 0) = 0')

    permit_filters = []
    permit_dimensions = (
        ('job_type', 'p.job_type'),
        ('work_type', 'p.work_type'),
        ('permit_type', 'p.permit_type'),
        ('license_type', 'p.permittee_license_type'),
        ('permit_status',
         "COALESCE(NULLIF(p.permit_status, ''), NULLIF(p.status, ''), "
         "NULLIF(p.filing_status, ''), '')"),
    )
    for name, expression in permit_dimensions:
        values = _multi_param(args, name, upper=True)
        if values:
            placeholders = _search_bind_values(params, name, values)
            permit_filters.append(
                f"UPPER(BTRIM({expression})) IN ({placeholders})")

    activity_mode, recent_days = _permit_activity_settings(args)
    if recent_days is not None:
        params['recent_permit_days'] = str(recent_days)
        recent_named_sql = """(
            {alias}.filing_date >= CURRENT_DATE - (%(recent_permit_days)s || ' days')::interval
            OR {alias}.issue_date >= CURRENT_DATE - (%(recent_permit_days)s || ' days')::interval
        )"""
        if activity_mode == 'inactive':
            # Property inactivity must check every permit on the building, not
            # just the permit rows that matched the search text or job filters.
            building_filters.append(
                "NOT EXISTS (SELECT 1 FROM permits recent_activity "
                "WHERE recent_activity.bbl = b.bbl AND "
                + recent_named_sql.format(alias='recent_activity') + ')'
            )
        else:
            permit_filters.append(recent_named_sql.format(alias='p'))

    current_only = str(args.get('current_only', '')).lower() == 'true'
    if current_only:
        permit_filters.append(_current_permit_sql('p'))

    try:
        min_matching = max(0, min(10000, int(args.get('min_matching_permits', 0))))
    except (TypeError, ValueError):
        min_matching = 0
    params['min_matching_permits'] = min_matching

    building_where = ' AND '.join(building_filters) if building_filters else 'TRUE'
    permit_where = ' AND '.join(permit_filters) if permit_filters else 'TRUE'
    require_matching_permit = bool(permit_filters)
    params['require_matching_permit'] = require_matching_permit

    owner_expression = """COALESCE(
        NULLIF(BTRIM(b.sale_buyer_primary), ''),
        NULLIF(BTRIM(b.current_owner_name), ''),
        NULLIF(BTRIM(b.owner_name_hpd), ''),
        NULLIF(BTRIM(b.owner_name_rpad), ''),
        'Owner unknown'
    )"""
    match_role = _search_match_role_case(roles)
    current_sql = _current_permit_sql('p')

    cte = f"""
        WITH search_bbls AS (
            SELECT b.bbl
            FROM buildings b
            WHERE {building_match}
            UNION
            SELECT p.bbl
            FROM permits p
            WHERE p.bbl IS NOT NULL
              AND {permit_match}
        ),
        filtered_buildings AS (
            SELECT b.*, {owner_expression} AS owner_display
            FROM buildings b
            JOIN search_bbls sb ON sb.bbl = b.bbl
            WHERE {building_where}
        ),
        matching_permits AS (
            SELECT
                p.*,
                b.owner_display,
                b.total_units AS property_units,
                b.building_sqft AS property_sqft,
                b.building_class AS property_building_class,
                b.assessed_total_value AS property_assessed_value,
                b.address AS property_address,
                GREATEST(p.filing_date, p.issue_date) AS activity_date,
                {current_sql} AS is_current,
                {match_role} AS match_role
            FROM filtered_buildings b
            JOIN permits p ON p.bbl = b.bbl
            WHERE ({building_match} OR {permit_match})
              AND {permit_where}
        ),
        property_rollup AS (
            SELECT
                b.bbl,
                b.address,
                LEFT(b.bbl, 1) AS borough,
                b.owner_display,
                b.total_units,
                b.residential_units,
                b.building_sqft,
                b.building_class,
                b.assessed_total_value,
                b.sale_price,
                b.hpd_open_violations,
                COUNT(mp.id) AS matching_permit_count,
                COUNT(mp.id) FILTER (WHERE mp.is_current) AS current_open_permit_count,
                MAX(mp.activity_date) AS latest_activity,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT NULLIF(UPPER(BTRIM(mp.job_type)), '')), NULL)
                    AS job_types,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT NULLIF(UPPER(BTRIM(mp.work_type)), '')), NULL)
                    AS work_types,
                ARRAY_REMOVE(ARRAY_AGG(DISTINCT mp.match_role), NULL) AS match_reasons,
                (SELECT COUNT(*) FROM permits ap WHERE ap.bbl = b.bbl)
                    AS total_permit_count
            FROM filtered_buildings b
            LEFT JOIN matching_permits mp ON mp.bbl = b.bbl
            GROUP BY
                b.bbl, b.address, b.owner_display, b.total_units,
                b.residential_units, b.building_sqft, b.building_class,
                b.assessed_total_value, b.sale_price, b.hpd_open_violations
            HAVING (NOT %(require_matching_permit)s OR COUNT(mp.id) > 0)
        ),
        qualified_properties AS (
            SELECT * FROM property_rollup
            WHERE matching_permit_count >= %(min_matching_permits)s
        ),
        qualified_permits AS (
            SELECT mp.*
            FROM matching_permits mp
            JOIN qualified_properties qp ON qp.bbl = mp.bbl
        )
    """
    return cte, params, {
        'query': query,
        'match_fields': sorted(roles),
        'current_only': current_only,
        'min_matching_permits': min_matching,
        'permit_activity_mode': activity_mode,
        'recent_permit_days': recent_days,
    }


@app.route('/api/search/explore')
def api_search_explore():
    """One search, switchable grain, shared filters, and URL-backed paging."""
    group_by = request.args.get('group_by', 'property').strip().lower()
    if group_by not in {'property', 'owner', 'job_type', 'permit'}:
        group_by = 'property'
    try:
        cte, params, context = _build_search_explorer_cte(request.args)
        page = max(1, request.args.get('page', 1, type=int))
        per_page = min(100, max(10, request.args.get('per_page', 25, type=int)))
        params.update(limit=per_page, offset=(page - 1) * per_page)

        summaries_sql = cte + """
            SELECT
                (SELECT COUNT(*) FROM qualified_properties) AS properties,
                (SELECT COUNT(*) FROM qualified_permits) AS permits,
                (SELECT COUNT(DISTINCT owner_display) FROM qualified_properties) AS owners,
                (SELECT COUNT(DISTINCT COALESCE(NULLIF(BTRIM(job_type), ''), 'Unknown'))
                   FROM qualified_permits) AS job_types,
                (SELECT COALESCE(SUM(current_open_permit_count), 0)
                   FROM qualified_properties) AS current_open_permits,
                (SELECT COALESCE(SUM(total_units), 0)
                   FROM qualified_properties) AS total_units
        """

        sort_order = 'ASC' if request.args.get('sort_order', 'desc').lower() == 'asc' else 'DESC'
        sort_key = request.args.get('sort_by', '').strip()
        selects = {
            'property': {
                'sorts': {
                    'latest': 'latest_activity', 'matching_permits': 'matching_permit_count',
                    'open_permits': 'current_open_permit_count', 'units': 'total_units',
                    'value': 'assessed_total_value', 'address': 'address',
                    'owner': 'owner_display',
                },
                'default': 'latest',
                'sql': "SELECT * FROM qualified_properties",
            },
            'permit': {
                'sorts': {
                    'latest': 'activity_date', 'expiry': 'exp_date',
                    'units': 'property_units', 'address': 'property_address',
                    'job_type': 'job_type',
                },
                'default': 'latest',
                'sql': """SELECT id, bbl, permit_no, job_number, job_type, work_type,
                            permit_type, permit_status, status, filing_status, issue_date,
                            filing_date, exp_date, activity_date, is_current, match_role,
                            applicant, permittee_business_name, permittee_license_type,
                            owner_business_name, property_address, property_units,
                            property_sqft, property_building_class, property_assessed_value,
                            owner_display, work_description
                         FROM qualified_permits""",
            },
            'owner': {
                'sorts': {
                    'properties': 'property_count', 'matching_permits': 'matching_permit_count',
                    'open_permits': 'current_open_permit_count', 'units': 'total_units',
                    'latest': 'latest_activity', 'owner': 'owner_display',
                },
                'default': 'properties',
                'sql': """SELECT owner_display,
                            COUNT(*) AS property_count,
                            SUM(matching_permit_count) AS matching_permit_count,
                            SUM(current_open_permit_count) AS current_open_permit_count,
                            SUM(COALESCE(total_units, 0)) AS total_units,
                            SUM(COALESCE(assessed_total_value, 0)) AS assessed_value,
                            MAX(latest_activity) AS latest_activity,
                            (ARRAY_AGG(address ORDER BY latest_activity DESC NULLS LAST))[1:4]
                                AS sample_addresses
                         FROM qualified_properties
                         GROUP BY owner_display""",
            },
            'job_type': {
                'sorts': {
                    'permits': 'permit_count', 'properties': 'property_count',
                    'open_permits': 'current_open_permit_count', 'latest': 'latest_activity',
                    'job_type': 'job_type',
                },
                'default': 'permits',
                'sql': """SELECT COALESCE(NULLIF(BTRIM(job_type), ''), 'Unknown') AS job_type,
                            COUNT(*) AS permit_count,
                            COUNT(DISTINCT bbl) AS property_count,
                            COUNT(*) FILTER (WHERE is_current) AS current_open_permit_count,
                            MAX(activity_date) AS latest_activity,
                            ARRAY_REMOVE(ARRAY_AGG(DISTINCT NULLIF(UPPER(BTRIM(work_type)), '')), NULL)
                                AS work_types
                         FROM qualified_permits
                         GROUP BY COALESCE(NULLIF(BTRIM(job_type), ''), 'Unknown')""",
            },
        }
        selected = selects[group_by]
        if sort_key not in selected['sorts']:
            sort_key = selected['default']
        order_expression = selected['sorts'][sort_key]
        tie_breaker = {
            'property': ', bbl ASC',
            'permit': ', bbl ASC, id ASC',
            'owner': ', owner_display ASC',
            'job_type': ', job_type ASC',
        }[group_by]
        result_sql = (
            cte + selected['sql'] +
            f" ORDER BY {order_expression} {sort_order} NULLS LAST" +
            tie_breaker +
            " LIMIT %(limit)s OFFSET %(offset)s"
        )

        with DatabaseConnection() as cur:
            cur.execute(summaries_sql, params)
            summary = dict(cur.fetchone())
            cur.execute(result_sql, params)
            results = [dict(row) for row in cur.fetchall()]

        total_count = int(summary[{
            'property': 'properties', 'permit': 'permits',
            'owner': 'owners', 'job_type': 'job_types',
        }[group_by]] or 0)
        total_pages = (total_count + per_page - 1) // per_page
        log_search(
            query=context['query'], result_count=total_count,
            filter_params={**context, 'group_by': group_by},
        )
        return jsonify({
            'success': True,
            'group_by': group_by,
            'results': results,
            'summary': summary,
            'context': context,
            'sort': {'by': sort_key, 'order': sort_order.lower()},
            'pagination': {
                'page': page, 'per_page': per_page, 'total_count': total_count,
                'total_pages': total_pages, 'has_prev': page > 1,
                'has_next': page < total_pages,
            },
        })
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        print(f'Search explorer error: {exc}')
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': 'Search could not be completed'}), 500


@app.route('/api/search')
def api_search():
    """
    Universal search using PostgreSQL's pattern matching.
    Searches buildings and permits, returns consolidated results.
    Handles multi-word queries by matching each word independently.
    """
    query = request.args.get('q', '').strip()
    
    if not query or len(query) < 2:
        return jsonify([])

    try:
        with DatabaseConnection() as cur:
            # Split query into tokens for multi-word matching
            tokens = [t.strip() for t in query.split() if t.strip()]
            
            if not tokens:
                return jsonify([])
            
            # For single token, use simple pattern; for multiple, use token matching
            base_pattern = f'%{escape_like_pattern(query)}%'
            
            # Get address variants for broader matching
            variants = get_search_variants(query)
            variant_patterns = [f'%{escape_like_pattern(v)}%' for v in variants]
            
            # Also create token-based variant patterns (for "810 sterling" -> "%810%" AND "%sterling%")
            token_variant_patterns = []
            for variant in variants:
                variant_tokens = variant.split()
                if len(variant_tokens) > 1:
                    token_variant_patterns.append([f'%{escape_like_pattern(t)}%' for t in variant_tokens])
            
            # Build dynamic WHERE clauses for token matching
            params = {
                'pattern': base_pattern,
                'pattern_stripped': query.strip(),  # For exact zip code match
                'variants': variant_patterns,
            }
            
            # For multi-word queries, build AND conditions for each token
            token_conditions_building = []
            token_conditions_permit = []
            
            if len(tokens) > 1:
                # Add token patterns to params
                for i, token in enumerate(tokens):
                    params[f'tok_{i}'] = f'%{escape_like_pattern(token)}%'
                
                # Build token match clause: address ILIKE '%tok1%' AND address ILIKE '%tok2%' ...
                token_clauses = ' AND '.join([f"b.address ILIKE %(tok_{i})s" for i in range(len(tokens))])
                token_conditions_building.append(f"({token_clauses})")
                
                token_clauses_p = ' AND '.join([f"p.address ILIKE %(tok_{i})s" for i in range(len(tokens))])
                token_conditions_permit.append(f"({token_clauses_p})")
            
            # Build the complete SQL with token matching
            building_token_clause = " OR ".join(token_conditions_building) if token_conditions_building else "FALSE"
            permit_token_clause = " OR ".join(token_conditions_permit) if token_conditions_permit else "FALSE"
            
            sql = f"""
                WITH all_matches AS (
                    -- Building matches
                    SELECT DISTINCT
                        b.bbl,
                        b.address,
                        b.current_owner_name as owner,
                        b.assessed_total_value as assessed_value,
                        b.sale_price,
                        CASE 
                            WHEN b.bbl::text ILIKE %(pattern)s THEN 'BBL'
                            WHEN b.address ILIKE ANY(%(variants)s) THEN 'Address'
                            WHEN {building_token_clause} THEN 'Address'
                            WHEN b.current_owner_name ILIKE %(pattern)s THEN 'Owner'
                            ELSE 'Building'
                        END as match_type,
                        CASE 
                            WHEN b.bbl::text ILIKE %(pattern)s THEN 1
                            WHEN b.address ILIKE ANY(%(variants)s) THEN 2
                            WHEN {building_token_clause} THEN 2
                            WHEN b.current_owner_name ILIKE %(pattern)s THEN 3
                            ELSE 4
                        END as priority
                    FROM buildings b
                    WHERE 
                        b.bbl::text ILIKE %(pattern)s
                        OR b.address ILIKE ANY(%(variants)s)
                        OR ({building_token_clause})
                        OR b.current_owner_name ILIKE %(pattern)s
                        OR b.owner_name_rpad ILIKE %(pattern)s
                        OR b.owner_name_hpd ILIKE %(pattern)s
                    
                    UNION ALL
                    
                    -- Permit matches
                    SELECT DISTINCT
                        p.bbl,
                        p.address,
                        COALESCE(p.owner_business_name, p.permittee_business_name) as owner,
                        NULL::numeric as assessed_value,
                        NULL::numeric as sale_price,
                        CASE 
                            WHEN p.zip_code = %(pattern_stripped)s THEN 'Zip Code'
                            WHEN p.permit_no ILIKE %(pattern)s THEN 'Permit #'
                            WHEN p.job_number ILIKE %(pattern)s THEN 'Job #'
                            WHEN p.address ILIKE ANY(%(variants)s) THEN 'Address'
                            WHEN {permit_token_clause} THEN 'Address'
                            WHEN p.permittee_business_name ILIKE %(pattern)s THEN 'Permittee'
                            WHEN p.owner_business_name ILIKE %(pattern)s THEN 'Owner'
                            WHEN p.applicant ILIKE %(pattern)s THEN 'Applicant'
                            ELSE 'Permit'
                        END as match_type,
                        CASE 
                            WHEN p.zip_code = %(pattern_stripped)s THEN 1
                            WHEN p.permit_no ILIKE %(pattern)s THEN 1
                            WHEN p.job_number ILIKE %(pattern)s THEN 1
                            WHEN p.address ILIKE ANY(%(variants)s) THEN 2
                            WHEN {permit_token_clause} THEN 2
                            ELSE 3
                        END as priority
                    FROM permits p
                    WHERE 
                        p.bbl IS NOT NULL
                        AND (
                            p.zip_code = %(pattern_stripped)s
                            OR p.permit_no ILIKE %(pattern)s
                            OR p.job_number ILIKE %(pattern)s
                            OR p.address ILIKE ANY(%(variants)s)
                            OR ({permit_token_clause})
                            OR p.permittee_business_name ILIKE %(pattern)s
                            OR p.owner_business_name ILIKE %(pattern)s
                            OR p.applicant ILIKE %(pattern)s
                            OR p.permittee_phone ILIKE %(pattern)s
                            OR p.owner_phone ILIKE %(pattern)s
                        )
                )
                SELECT 
                    bbl,
                    MAX(address) as address,
                    MAX(owner) as owner,
                    MAX(assessed_value) as assessed_value,
                    MAX(sale_price) as sale_price,
                    ARRAY_AGG(DISTINCT match_type) as match_reasons,
                    MIN(priority) as priority,
                    (SELECT COUNT(*) FROM permits WHERE permits.bbl = all_matches.bbl) as permits
                FROM all_matches
                WHERE bbl IS NOT NULL
                GROUP BY bbl
                ORDER BY MIN(priority), permits DESC
                LIMIT 100
            """
            
            cur.execute(sql, params)
            results = cur.fetchall()
            
            # Log the search
            log_search(
                query=query,
                result_count=len(results),
                filter_params={'tokens': tokens}
            )
            
            return jsonify([dict(r) for r in results])

    except Exception as e:
        print(f"Search error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify([])


@app.route('/api/property/auto-add', methods=['POST'])
def api_auto_add_property():
    """Look up a property that isn't in our DB yet and run every free
    enrichment step (PLUTO, RPAD, HPD, ACRIS, tax liens, SOS) so the user
    can see a populated building profile. Paid contact enrichment is NOT
    triggered — Apify/Enformion only fire when a user clicks Enrich.

    Body: {"query": "141 WYONA STREET, BROOKLYN, NY 11207"}  or a 10-digit BBL.
    Returns: {success, bbl, building_id, already_existed, report}
    """
    try:
        from property_lookup import auto_add_property
    except Exception as e:
        return jsonify({'success': False, 'error': f'lookup module unavailable: {e}'}), 500

    data = request.get_json(silent=True) or {}
    query = (data.get('query') or '').strip()
    if not query:
        return jsonify({'success': False, 'error': 'query is required'}), 400

    try:
        # Direct connections, never the shared pool: only resolve + insert
        # happen on this request (the enrichment steps continue on a
        # background thread), and the DB connection is opened only after
        # Geoclient answers, so this endpoint can't starve page requests.
        result = auto_add_property(
            lambda: psycopg2.connect(**DB_CONFIG), query,
        )
        status = 200 if result.get('success') else 422
        return jsonify(result), status
    except Exception as e:
        import traceback
        traceback.print_exc()
        # Some driver errors stringify to '' — never send an empty reason.
        message = str(e).strip() or type(e).__name__
        return jsonify({'success': False, 'error': message}), 500


@app.route('/api/suggest')
def api_suggest():
    """
    Fast autocomplete suggestions - simplified query for speed.
    """
    query = request.args.get('q', '').strip()
    limit = request.args.get('limit', 8, type=int)
    
    if not query or len(query) < 2:
        return jsonify([])
    
    try:
        with DatabaseConnection() as cur:
            safe_query = escape_like_pattern(query)
            base_pattern = f'%{safe_query}%'
            variants = [f'%{escape_like_pattern(v)}%' for v in get_search_variants(query)]
            
            # Split query into tokens for multi-word matching
            tokens = [t.strip() for t in query.split() if t.strip()]
            
            params = {
                'pattern': base_pattern,
                'variants': variants,
                'limit': limit
            }
            
            # Build token conditions for multi-word queries
            token_clause_b = "FALSE"
            token_clause_p = "FALSE"
            
            if len(tokens) > 1:
                for i, token in enumerate(tokens):
                    params[f'tok_{i}'] = f'%{escape_like_pattern(token)}%'
                token_clause_b = ' AND '.join([f"b.address ILIKE %(tok_{i})s" for i in range(len(tokens))])
                token_clause_p = ' AND '.join([f"p.address ILIKE %(tok_{i})s" for i in range(len(tokens))])
            
            # Simpler, faster suggestion query with token matching
            sql = f"""
                SELECT DISTINCT ON (bbl)
                    bbl,
                    address,
                    owner,
                    match_type,
                    permits
                FROM (
                    SELECT 
                        b.bbl,
                        b.address,
                        b.current_owner_name as owner,
                        CASE 
                            WHEN b.address ILIKE ANY(%(variants)s) THEN 'Address'
                            WHEN ({token_clause_b}) THEN 'Address'
                            WHEN b.current_owner_name ILIKE %(pattern)s THEN 'Owner'
                            ELSE 'Building'
                        END as match_type,
                        1 as priority,
                        (SELECT COUNT(*) FROM permits WHERE permits.bbl = b.bbl) as permits
                    FROM buildings b
                    WHERE 
                        b.address ILIKE ANY(%(variants)s)
                        OR ({token_clause_b})
                        OR b.current_owner_name ILIKE %(pattern)s
                        OR b.bbl::text ILIKE %(pattern)s
                    
                    UNION ALL
                    
                    SELECT 
                        p.bbl,
                        p.address,
                        COALESCE(p.owner_business_name, p.permittee_business_name) as owner,
                        CASE 
                            WHEN p.permit_no ILIKE %(pattern)s THEN 'Permit'
                            ELSE 'Address'
                        END as match_type,
                        2 as priority,
                        1 as permits
                    FROM permits p
                    WHERE 
                        p.bbl IS NOT NULL
                        AND NOT EXISTS (SELECT 1 FROM buildings WHERE buildings.bbl = p.bbl)
                        AND (
                            p.address ILIKE ANY(%(variants)s)
                            OR ({token_clause_p})
                            OR p.permit_no ILIKE %(pattern)s
                            OR p.permittee_business_name ILIKE %(pattern)s
                        )
                ) sub
                WHERE bbl IS NOT NULL AND address IS NOT NULL
                ORDER BY bbl, priority, permits DESC
                LIMIT %(limit)s
            """
            
            cur.execute(sql, params)
            results = cur.fetchall()
            
            return jsonify([dict(r) for r in results])

    except Exception as e:
        print(f"Suggest error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify([])


@app.route('/api/market-stats')
def api_market_stats():
    """Market statistics for homepage"""
    try:
        with DatabaseConnection() as cur:
            # Active permits (all permits with valid dates)
            cur.execute("""
                SELECT COUNT(*) as count
                FROM permits
                WHERE issue_date IS NOT NULL
                AND issue_date <= CURRENT_DATE
                AND issue_date >= '2000-01-01'
            """)
            active_permits = cur.fetchone()['count']
            
            # Recent sales (last 30 days)
            cur.execute("""
                SELECT COUNT(*) as count
                FROM acris_transactions
                WHERE doc_type LIKE '%%DEED%%'
                AND recorded_date >= CURRENT_DATE - INTERVAL '30 days'
            """)
            recent_sales = cur.fetchone()['count']
            
            # Total properties
            cur.execute("SELECT COUNT(*) as count FROM buildings")
            total_properties = cur.fetchone()['count']
            
            # Qualified leads - show total permits since we have 70k+
            cur.execute("SELECT COUNT(*) as count FROM permits")
            qualified_leads = cur.fetchone()['count']
        
        return jsonify({
            'activePermits': active_permits,
            'recentSales': recent_sales,
            'totalProperties': total_properties,
            'qualifiedLeads': qualified_leads
        })
        
    except Exception as e:
        print(f"Market stats error: {e}")
        return jsonify({
            'activePermits': 1968,
            'recentSales': 1141,
            'totalProperties': 1361,
            'qualifiedLeads': 937
        })


@app.route('/api/property/<bbl>')
def api_property_detail(bbl):
    """Get comprehensive property data"""
    try:
        with DatabaseConnection() as cur:
            # Get building data
            cur.execute("""
                SELECT *
                FROM buildings
                WHERE bbl = %s
            """, (bbl,))
            
            building = cur.fetchone()
            
            if not building:
                return jsonify({'success': False, 'error': 'Property not found'}), 404
            
            # Get permits
                cur.execute("""
                SELECT *
                FROM permits
                WHERE bbl = %s
                ORDER BY issue_date DESC
            """, (bbl,))
            permits = cur.fetchall()
        
            # Get ACRIS transactions
            cur.execute("""
                SELECT *
                FROM acris_transactions
                WHERE building_id = (SELECT id FROM buildings WHERE bbl = %s)
                ORDER BY recorded_date DESC
            """, (bbl,))
            transactions = cur.fetchall()
        
            # Get ACRIS parties (buyers, sellers, lenders)
            cur.execute("""
                SELECT p.*
                FROM acris_parties p
                WHERE p.building_id = (SELECT id FROM buildings WHERE bbl = %s)
                ORDER BY p.party_type, p.party_name
            """, (bbl,))
            parties = cur.fetchall()
        
            # Get contacts - aggregate from permits table columns
            cur.execute("""
                SELECT DISTINCT 
                    COALESCE(p.permittee_business_name, p.applicant) as name,
                    p.permittee_phone as phone,
                    'Permittee' as role,
                    NULL as email,
                    p.permit_no as permit_number
                FROM permits p
                WHERE p.bbl = %s AND (p.permittee_business_name IS NOT NULL OR p.applicant IS NOT NULL)
                UNION
                SELECT DISTINCT 
                    p.owner_business_name as name,
                    p.owner_phone as phone,
                    'Owner' as role,
                    NULL as email,
                    p.permit_no as permit_number
                FROM permits p
                WHERE p.bbl = %s AND p.owner_business_name IS NOT NULL
                UNION
                SELECT DISTINCT 
                    p.superintendent_business_name as name,
                    NULL as phone,
                    'Superintendent' as role,
                    NULL as email,
                    p.permit_no as permit_number
                FROM permits p
                WHERE p.bbl = %s AND p.superintendent_business_name IS NOT NULL
                UNION
                SELECT DISTINCT 
                    p.site_safety_mgr_business_name as name,
                    NULL as phone,
                    'Site Safety Manager' as role,
                    NULL as email,
                    p.permit_no as permit_number
                FROM permits p
                WHERE p.bbl = %s AND p.site_safety_mgr_business_name IS NOT NULL
                ORDER BY name;
            """, (bbl, bbl, bbl, bbl))
            contacts = cur.fetchall()
        
            return jsonify({
                'success': True,
                'building': dict(building),
                'permits': [dict(p) for p in permits],
                'transactions': [dict(t) for t in transactions],
                'parties': [dict(p) for p in parties],
                'contacts': [dict(c) for c in contacts]
            })
        
    except Exception as e:
        print(f"Property detail error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# PROPERTIES PAGE ROUTES
# ============================================================================

@app.route('/properties')
@login_required
def properties_page():
    """Render the properties search/browse page"""
    return render_template(
        'properties.html',
        building_class_groups=building_class_options(),
        active_page='properties',
    )


# Hard cap on the number of properties one bulk-enrich job can target.
# Caps cost/runtime exposure even with the type-to-confirm gate in the UI.
BULK_ENRICH_MAX_PROPERTIES = 20000


# Category filters the properties sidebar exposes as multi-selects. Each one
# accepts any number of values and ORs them together, so "Residential OR
# Mixed use" or "PL OR EW OR NB" is a single query rather than three passes.
_PROPERTY_TYPE_SQL = {
    # A=1-family, B=2-family, C=walk-up, D=elevator, R=condo
    'residential': "b.building_class ~ '^[ABCDR]'",
    # K=stores, O=office, E=warehouse, F=factory, G=garage
    'commercial': "b.building_class ~ '^[KOEFG]'",
    # S=mixed residential/commercial
    'mixed': "b.building_class ~ '^S'",
}


def _multi_param(args, name, allowed=None, upper=False):
    """Collect a repeatable filter param into a de-duplicated list of values.

    Handles every shape these params arrive in:
      - repeated query args   ?permit_type=PL&permit_type=EW
      - one comma-separated   ?permit_type=PL,EW
      - a JSON array          {"permit_type": ["PL", "EW"]}
      - a single scalar       ?permit_type=PL      (pre-multi-select clients)

    Empty values are dropped; `allowed`, when given, filters to a known set so
    unrecognised input can never reach the query.
    """
    raw_values = []

    def spread(value):
        if value is None:
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                spread(item)
            return
        raw_values.extend(str(value).split(','))

    if args is not None and hasattr(args, 'getlist'):
        spread(args.getlist(name))
    elif args is not None and hasattr(args, 'get'):
        spread(args.get(name))

    out = []
    seen = set()
    for value in raw_values:
        value = value.strip()
        if upper:
            value = value.upper()
        if not value or value in seen:
            continue
        if allowed is not None and value not in allowed:
            continue
        seen.add(value)
        out.append(value)
    return out


_PERMIT_ACTIVITY_MODES = {'within', 'inactive'}


def _permit_activity_settings(args):
    """Return the validated permit timing mode and window.

    ``within`` keeps the legacy meaning: at least one permit date falls inside
    the last X days. ``inactive`` is interpreted at the correct grain by the
    caller: property queries exclude any building with a recent permit, while
    permit-row queries keep records whose dates are older than the cutoff.
    """
    if args is None or not hasattr(args, 'get'):
        return 'within', None

    raw_mode = args.get('permit_activity_mode', 'within')
    if isinstance(raw_mode, (list, tuple)):
        raw_mode = raw_mode[0] if raw_mode else 'within'
    mode = str(raw_mode or 'within').strip().lower()
    if mode not in _PERMIT_ACTIVITY_MODES:
        mode = 'within'

    raw_days = args.get('recent_permit_days')
    if isinstance(raw_days, (list, tuple)):
        raw_days = raw_days[0] if raw_days else None
    try:
        days = int(raw_days) if raw_days not in (None, '') else None
    except (TypeError, ValueError):
        days = None
    if days is None or days <= 0:
        return mode, None
    return mode, min(days, 3650)


def _recent_permit_date_sql(alias='p'):
    """Predicate for a filing or issue date inside a bound day window."""
    return (
        f"({alias}.filing_date >= CURRENT_DATE - (%s || ' days')::interval"
        f" OR {alias}.issue_date >= CURRENT_DATE - (%s || ' days')::interval)"
    )


def _older_permit_date_sql(alias='p'):
    """Predicate for a dated permit row wholly before a bound day window."""
    return (
        f"(({alias}.filing_date IS NULL OR {alias}.filing_date < "
        f"CURRENT_DATE - (%s || ' days')::interval)"
        f" AND ({alias}.issue_date IS NULL OR {alias}.issue_date < "
        f"CURRENT_DATE - (%s || ' days')::interval)"
        f" AND ({alias}.filing_date IS NOT NULL OR {alias}.issue_date IS NOT NULL))"
    )


def _append_property_permit_activity_filter(
        args, where_clauses, params, building_alias='b'):
    """Apply permit timing to a building set without false inactivity matches.

    An ``EXISTS`` on an old permit is not enough to prove inactivity because
    the same property may also have a recent permit. The inactive mode must be
    a ``NOT EXISTS`` over *all* permits for the building. This also intentionally
    includes properties with no permit history at all.
    """
    mode, days = _permit_activity_settings(args)
    if days is None:
        return

    recent_sql = _recent_permit_date_sql('permit_activity')
    exists_sql = (
        'EXISTS (SELECT 1 FROM permits permit_activity '
        f'WHERE permit_activity.bbl = {building_alias}.bbl AND {recent_sql})'
    )
    where_clauses.append(f'NOT {exists_sql}' if mode == 'inactive' else exists_sql)
    params.extend([str(days), str(days)])


def _permit_predicates(args, alias='p', include_recency=True):
    """WHERE fragments describing the permits a filter set is asking about.

    Returned as (sql_parts, params) so the caller can drop them into an EXISTS
    against the buildings table or straight into a query already scanning
    permits. Property queries deliberately call this without recency and then
    apply timing across the whole building; permit and participant views use
    the row-level recent/older predicate returned here.
    """
    parts, params = [], []

    permit_types = _multi_param(args, 'permit_type', upper=True)
    if permit_types:
        placeholders = ','.join(['%s'] * len(permit_types))
        parts.append(f'UPPER(btrim({alias}.permit_type)) IN ({placeholders})')
        params.extend(permit_types)

    work_types = _multi_param(args, 'work_type', upper=True)
    if work_types:
        placeholders = ','.join(['%s'] * len(work_types))
        parts.append(f'UPPER(btrim({alias}.work_type)) IN ({placeholders})')
        params.extend(work_types)

    job_types = _multi_param(args, 'job_type', upper=True)
    if job_types:
        placeholders = ','.join(['%s'] * len(job_types))
        parts.append(f'UPPER(btrim({alias}.job_type)) IN ({placeholders})')
        params.extend(job_types)

    license_types = _multi_param(args, 'license_type', upper=True)
    if license_types:
        placeholders = ','.join(['%s'] * len(license_types))
        parts.append(
            f'UPPER(btrim({alias}.permittee_license_type)) IN ({placeholders})')
        params.extend(license_types)

    if not include_recency:
        return parts, params
    mode, recent_days = _permit_activity_settings(args)
    if recent_days is not None:
        parts.append(
            _older_permit_date_sql(alias)
            if mode == 'inactive'
            else _recent_permit_date_sql(alias)
        )
        params.extend([str(recent_days), str(recent_days)])

    return parts, params


def _append_building_only_filters(args, where_clauses, params, alias='b'):
    """Filters that describe the building itself.

    Split out from _append_category_filters because the contractors page
    filters permits directly and needs these without the permits EXISTS.
    """
    property_types = _multi_param(args, 'property_type', allowed=set(_PROPERTY_TYPE_SQL))
    if property_types:
        where_clauses.append(
            '(' + ' OR '.join(_PROPERTY_TYPE_SQL[t] for t in property_types) + ')'
        )

    building_classes = _multi_param(args, 'building_class', upper=True)
    if building_classes:
        where_clauses.append(
            '(' + ' OR '.join(['b.building_class LIKE %s'] * len(building_classes)) + ')'
        )
        params.extend(f'{code}%' for code in building_classes)

    # "Has violations" and "No violations" are complements: picking both is the
    # same as picking neither, so neither adds a clause.
    violations = {v.lower() for v in _multi_param(args, 'has_violations')}
    wants_open = 'true' in violations
    wants_clean = 'false' in violations
    if wants_open and not wants_clean:
        where_clauses.append('b.hpd_open_violations > 0')
    elif wants_clean and not wants_open:
        where_clauses.append('(b.hpd_open_violations = 0 OR b.hpd_open_violations IS NULL)')


def _append_category_filters(args, where_clauses, params):
    """Building filters plus the permit-attribute EXISTS.

    Shared by /api/properties, the CSV export and the bulk-enrich resolver so
    all three always resolve a given filter set to the same properties.
    """
    _append_building_only_filters(args, where_clauses, params)

    # Permit type, work type, job type and licence type all describe the
    # permits on a building, so they collapse into a single EXISTS. Recency is
    # left out because /api/properties applies it through its own join; the
    # predicate is the same either way.
    permit_parts, permit_params = _permit_predicates(
        args, alias='p', include_recency=False)
    if permit_parts:
        where_clauses.append(
            'EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl AND '
            + ' AND '.join(permit_parts) + ')'
        )
        params.extend(permit_params)


def _order_by_sql(args, whitelist, default_key, sort_order, tiebreaker=None):
    """Build an ORDER BY body from a repeatable sort_by param.

    The Sort control is a multi-select: the keys are applied in the order
    they were picked, so the second key breaks ties in the first. Unknown
    keys are dropped, and an empty selection falls back to `default_key`.

    `whitelist` maps a public key to a SQL expression — nothing else can
    reach the query, so the keys are safe to interpolate.
    """
    keys = [k for k in _multi_param(args, 'sort_by') if k in whitelist]
    if not keys:
        keys = [default_key]

    direction = 'ASC' if str(sort_order).lower() == 'asc' else 'DESC'
    parts = [f'{whitelist[key]} {direction} NULLS LAST' for key in keys]
    if tiebreaker:
        parts.append(tiebreaker)
    return ', '.join(parts)


def _parse_boroughs_param(raw, multi_source=None):
    """Parse the borough query param into a list of valid borough codes.

    Accepts repeated values (?borough=1&borough=3), a single comma-separated
    value (?borough=1,3), or a JSON array (["1", "3"]) from a POST body.
    Empty / unknown codes are dropped.

    `multi_source` (optional) is a MultiDict-like object with `getlist`.
    If provided, it's used instead of Flask's global request — needed when
    the filter logic is invoked outside a Flask request (e.g., from the
    bulk-enrich resolver which feeds in a synthesized MultiDict).
    """
    valid = {'1', '2', '3', '4', '5'}
    raw_values = []

    if isinstance(raw, (list, tuple, set)):
        # A JSON array, as the bulk-enrich POST body sends it. Checked first
        # because that request has its own empty query string, and falling
        # through to request.args would silently drop the filter.
        for v in raw:
            raw_values.extend(str(v).split(','))
    elif multi_source is not None and hasattr(multi_source, 'getlist'):
        for v in multi_source.getlist('borough'):
            raw_values.extend(str(v).split(','))
    else:
        # Prefer the live Flask request (handles both repeated and comma-separated)
        try:
            for v in request.args.getlist('borough'):
                raw_values.extend(str(v).split(','))
        except RuntimeError:
            if raw:
                raw_values = str(raw).split(',')
        if not raw_values and raw:
            raw_values = str(raw).split(',')

    out = []
    seen = set()
    for v in raw_values:
        v = v.strip()
        if v in valid and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _percent_filter_ratio(value):
    """Convert the sidebar's 0-100 financing percentage to DB ratio units."""
    if value is None:
        return None
    try:
        percentage = float(value)
    except (TypeError, ValueError):
        return None
    return percentage / 100 if 0 <= percentage <= 100 else None


# Coarse SQL prefilter only. The paid-enrichment path always runs the stricter
# Python classifier as its final authority. Keeping this centralized prevents
# the property list and bulk-estimate routes from drifting apart.
_NONPERSON_SQL_PATTERN = (
    r'(^|[^[:alpha:]])(LLC|INC|INCORPORATED|CORP|CORPORATION|LTD|LIMITED|'
    r'COMPANY|HOLDINGS|REALTY|PROPERTIES|BANK|BANC|BANCORP|MORTGAGE|LENDING|'
    r'FINANCE|FINANCIAL|FUNDING|SERVICING|TRUST|TRUSTEE|FUND|ASSOCIATION|'
    r'AUTHORITY|DEPARTMENT|AGENCY|CREDIT[[:space:]]+UNION|NATIONAL[[:space:]]+'
    r'ASSOCIATION|FANNIE[[:space:]]+MAE|FREDDIE[[:space:]]+MAC|MERS)'
    r'([^[:alpha:]]|$)'
)


def _enrichable_owner_sql():
    """SQL prefilter for rows likely to contain at least one human owner."""
    fields = [
        ('b.sos_principal_name',
         "AND UPPER(COALESCE(b.sos_principal_title, '')) NOT IN "
         "('SERVICE OF PROCESS AGENT', 'REGISTERED AGENT')"),
        ('b.sale_buyer_primary', ''),
        ('b.current_owner_name', ''),
        ('b.owner_name_hpd', ''),
        ('b.owner_name_rpad', ''),
    ]
    candidates = []
    for field, extra in fields:
        candidates.append(f"""(
            {field} IS NOT NULL
            AND {field} ~* '[[:alpha:]][[:alpha:]''’.-]+[[:space:],]+[[:alpha:]]'
            AND {field} !~* '{_NONPERSON_SQL_PATTERN}'
            AND {field} !~ '[0-9;&]'
            {extra}
        )""")
    return '(\n' + '\nOR '.join(candidates) + '\n)'


def _resolve_filter_building_ids(args, limit=None):
    """Return the list of building IDs matching the same filters as /api/properties,
    ignoring pagination. Used by the bulk-enrich endpoints so that 'enrich filtered'
    enriches exactly the set the user is browsing, not just the current page.

    `args` is a Flask MultiDict (request.args) or any object with .get().
    """
    def g(name, type=None, default=None):
        if hasattr(args, 'get'):
            try:
                return args.get(name, default=default, type=type) if type else args.get(name, default)
            except TypeError:
                # plain dicts don't take a `type` arg
                v = args.get(name, default)
                if type and v is not None:
                    try:
                        return type(v)
                    except (TypeError, ValueError):
                        return default
                return v
        return default

    search = (g('search', default='') or '').strip()
    owner = (g('owner', default='') or '').strip()
    min_value = g('min_value', type=float)
    max_value = g('max_value', type=float)
    min_sale_price = g('min_sale_price', type=float)
    max_sale_price = g('max_sale_price', type=float)
    sale_date_from = g('sale_date_from')
    sale_date_to = g('sale_date_to')
    cash_only = str(g('cash_only', default='')).lower() == 'true'
    with_permits = str(g('with_permits', default='')).lower() == 'true'
    min_permits = g('min_permits', type=int)
    boroughs = _parse_boroughs_param(g('borough', default=''), multi_source=args if hasattr(args, 'getlist') else None)
    min_units = g('min_units', type=int)
    max_units = g('max_units', type=int)
    recent_sale_days = g('recent_sale_days', type=int)
    financing_min = _percent_filter_ratio(g('financing_min', type=float))
    financing_max = _percent_filter_ratio(g('financing_max', type=float))
    has_enrichable_owner = str(g('has_enrichable_owner', default='')).lower() == 'true'

    where_clauses = []
    params = []

    # Prebuilt play — bulk enrichment over a play targets exactly the
    # play's building set. Unknown/unavailable plays raise so the caller
    # surfaces the error instead of silently enriching everything.
    play_id = str(g('play', default='') or '').strip()
    if play_id:
        play_where, play_error = _resolve_play_where(play_id)
        if play_error:
            raise ValueError(play_error)
        where_clauses.append(play_where)

    if search:
        is_zip_search = search.isdigit() and len(search) == 5 and search.startswith('1')
        if is_zip_search:
            where_clauses.append("""(
                b.address ILIKE %s OR b.bbl LIKE %s OR
                b.current_owner_name ILIKE %s OR b.owner_name_rpad ILIKE %s OR
                b.owner_name_hpd ILIKE %s OR
                EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl AND p.zip_code = %s)
            )""")
            term = f"%{search}%"
            params.extend([term, term, term, term, term, search])
        else:
            where_clauses.append("""(
                b.address ILIKE %s OR b.bbl LIKE %s OR
                b.current_owner_name ILIKE %s OR b.owner_name_rpad ILIKE %s OR
                b.owner_name_hpd ILIKE %s
            )""")
            term = f"%{search}%"
            params.extend([term, term, term, term, term])

    if owner:
        where_clauses.append("""(
            b.current_owner_name ILIKE %s OR b.owner_name_rpad ILIKE %s OR b.owner_name_hpd ILIKE %s
        )""")
        ot = f"%{owner}%"
        params.extend([ot, ot, ot])

    if min_value is not None:
        where_clauses.append("b.assessed_total_value >= %s"); params.append(min_value)
    if max_value is not None:
        where_clauses.append("b.assessed_total_value <= %s"); params.append(max_value)
    if min_sale_price is not None:
        where_clauses.append("b.sale_price >= %s"); params.append(min_sale_price)
    if max_sale_price is not None:
        where_clauses.append("b.sale_price <= %s"); params.append(max_sale_price)
    if sale_date_from:
        where_clauses.append("b.sale_date >= %s"); params.append(sale_date_from)
    if sale_date_to:
        where_clauses.append("b.sale_date <= %s"); params.append(sale_date_to)
    if cash_only:
        where_clauses.append("b.is_cash_purchase = true")
    if recent_sale_days:
        where_clauses.append("b.sale_date >= CURRENT_DATE - INTERVAL '%s days'")
        params.append(recent_sale_days)
    if financing_min is not None:
        where_clauses.append("b.financing_ratio >= %s"); params.append(financing_min)
    if financing_max is not None:
        where_clauses.append("b.financing_ratio <= %s"); params.append(financing_max)
    if boroughs:
        placeholders = ','.join(['%s'] * len(boroughs))
        where_clauses.append(f"LEFT(b.bbl, 1) IN ({placeholders})")
        params.extend(boroughs)
    _append_category_filters(args, where_clauses, params)
    _append_property_permit_activity_filter(args, where_clauses, params)
    if min_units is not None:
        where_clauses.append("COALESCE(b.total_units, 0) >= %s"); params.append(min_units)
    if max_units is not None:
        where_clauses.append("COALESCE(b.total_units, 0) <= %s"); params.append(max_units)
    if with_permits:
        where_clauses.append("EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl)")
    if min_permits is not None:
        where_clauses.append("(SELECT COUNT(*) FROM permits p WHERE p.bbl = b.bbl) >= %s")
        params.append(min_permits)
    if has_enrichable_owner:
        where_clauses.append(_enrichable_owner_sql())

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    query = f"""
        SELECT b.id
        FROM buildings b
        {where_sql}
        ORDER BY b.id
        {limit_sql}
    """
    with DatabaseConnection() as cur:
        cur.execute(query, params)
        return [r['id'] for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Prebuilt filter "plays" (see plays.py). A play is a server-defined WHERE
# fragment over the signal columns; plays are only offered when the columns
# from migrate_add_intel_signals.py exist, so pre-migration databases just
# show fewer plays.
# ---------------------------------------------------------------------------

_buildings_columns_cache = {'at': 0.0, 'cols': set()}
_permits_columns_cache = {'at': 0.0, 'cols': set()}


def _table_columns(table_name, cache_entry):
    """Return cached table columns without masking a failed first probe.

    A previously successful value may be served stale during a transient
    database problem. With no successful value, however, an outage must raise:
    returning an empty set made the plays API report a healthy empty list.
    """
    now = time.time()
    if cache_entry['cols'] and now - cache_entry['at'] <= 300:
        return cache_entry['cols']
    try:
        with DatabaseConnection() as cur:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = %s
            """, (table_name,))
            columns = {r['column_name'] for r in cur.fetchall()}
        if not columns:
            raise RuntimeError(f"required table {table_name!r} was not found")
        cache_entry['cols'] = columns
        cache_entry['at'] = now
    except Exception as e:
        if cache_entry['cols']:
            print(f"{table_name}-columns probe failed; using cached schema: {e}")
            return cache_entry['cols']
        raise RuntimeError(f"could not inspect {table_name} schema: {e}") from e
    return cache_entry['cols']


def _buildings_columns():
    """Column names on buildings, cached for 5 minutes."""
    return _table_columns('buildings', _buildings_columns_cache)


def _permits_columns():
    """Column names on permits, cached for 5 minutes."""
    return _table_columns('permits', _permits_columns_cache)


def _resolve_play_where(play_id):
    """(where_fragment, error) for a ?play= param. Empty play_id -> (None, None)."""
    if not play_id:
        return None, None
    from plays import get_play
    play = get_play(play_id, _buildings_columns(), _permits_columns())
    if not play:
        return None, f"Unknown or unavailable play: {play_id}"
    return play['where'], None


# Extra buildings columns returned to the list UI when the signals
# migration has run (property cards render badges from whichever arrive).
_SIGNAL_SELECT_COLUMNS = [
    'unused_far', 'zoning_district', 'is_free_and_clear', 'has_open_mortgage',
    'has_senior_exemption', 'has_disabled_exemption',
    'on_speculation_watch_list', 'speculation_watch_date',
    'litigation_open_count', 'eviction_count',
    'has_tax_delinquency', 'tax_delinquency_latest_date',
    'latest_co_date', 'latest_co_type', 'fisp_status',
    'll97_covered_estimated', 'energy_star_score',
]


def _signal_select_sql():
    cols = _buildings_columns()
    return ''.join(f", b.{c}" for c in _SIGNAL_SELECT_COLUMNS if c in cols)


@app.route('/api/properties/plays')
@cache.cached(timeout=900)
def api_property_plays():
    """Prebuilt plays with live counts and source-coverage diagnostics."""
    from plays import available_plays, public_play
    try:
        building_columns = _buildings_columns()
        permit_columns = _permits_columns()
        plays_list = available_plays(building_columns, permit_columns)
        counts = {}
        coverage_counts = {}
        total_buildings = 0
        count_error = None
        if plays_list:
            expressions = ['COUNT(*) AS total_buildings']
            permit_expressions = []
            coverage_aliases = {}
            for i, play in enumerate(plays_list):
                if play.get('permit_count_where'):
                    permit_expressions.append(
                        f"COUNT(DISTINCT p.bbl) FILTER "
                        f"(WHERE {play['permit_count_where']}) AS c{i}")
                else:
                    expressions.append(
                        f"COUNT(*) FILTER (WHERE {play['where']}) AS c{i}")
                required_building = set(
                    play.get('coverage_required_columns', []))
                required_permit = set(
                    play.get('coverage_required_permit_columns', []))
                if (play.get('coverage_where')
                        and required_building <= building_columns
                        and required_permit <= permit_columns):
                    if play.get('permit_coverage_where'):
                        permit_expressions.append(
                            f"COUNT(DISTINCT p.bbl) FILTER "
                            f"(WHERE {play['permit_coverage_where']}) AS v{i}")
                    else:
                        expressions.append(
                            f"COUNT(*) FILTER (WHERE {play['coverage_where']}) AS v{i}")
                    coverage_aliases[play['id']] = f'v{i}'
            row = {}
            try:
                with DatabaseConnection() as cur:
                    cur.execute(
                        f"SELECT {', '.join(expressions)} FROM buildings b", ())
                    row = dict(cur.fetchone() or {})
            except Exception as building_count_exc:
                count_error = 'Prebuilt-filter counts are temporarily unavailable.'
                print(f"Building play count query failed: {building_count_exc}")
            if row and permit_expressions:
                try:
                    # Use a separate transaction so a timeout here does not
                    # discard the faster building-backed card counts.
                    with DatabaseConnection() as cur:
                        cur.execute(
                            f"SELECT {', '.join(permit_expressions)} "
                            "FROM permits p "
                            "JOIN buildings b ON b.bbl = p.bbl "
                            "WHERE p.bbl IS NOT NULL", ())
                        row.update(dict(cur.fetchone() or {}))
                except Exception as permit_count_exc:
                    count_error = (
                        'Permit-based prebuilt-filter counts are temporarily unavailable.')
                    print(f"Permit play count query failed: {permit_count_exc}")
            if row:
                total_buildings = int(row.get('total_buildings') or 0)
                counts = {
                    p['id']: (int(row.get(f'c{i}') or 0)
                              if f'c{i}' in row else None)
                    for i, p in enumerate(plays_list)
                }
                coverage_counts = {
                    play_id: int(row.get(alias) or 0)
                    for play_id, alias in coverage_aliases.items()
                    if alias in row
                }

        payload = []
        for play in plays_list:
            item = dict(public_play(play))
            count = counts.get(play['id'])
            item['count'] = count
            item['count_status'] = 'error' if count is None else (
                'empty' if count == 0 else 'ready')

            if play['id'] in coverage_counts:
                covered = coverage_counts[play['id']]
                kind = play.get('coverage_kind', 'source')
                if covered == 0:
                    status = 'not_started'
                elif kind == 'pipeline' and total_buildings and covered < total_buildings:
                    status = 'partial'
                else:
                    status = 'ready'
                item['coverage'] = {
                    'status': status,
                    'kind': kind,
                    'count': covered,
                    'total': total_buildings,
                    'percent': (round(covered * 100 / total_buildings, 1)
                                if total_buildings else 0),
                    'label': play.get('coverage_label', 'Source data available'),
                }
            elif play.get('coverage_where'):
                item['coverage'] = {
                    'status': 'unavailable',
                    'kind': play.get('coverage_kind', 'source'),
                    'count': None,
                    'total': total_buildings,
                    'percent': None,
                    'label': play.get('coverage_label', 'Source data available'),
                }
            payload.append(item)

        count_health = 'ready'
        if count_error:
            count_health = ('partial' if any(
                count is not None for count in counts.values()) else 'error')
        return jsonify({
            'success': True,
            'plays': payload,
            'health': {
                'total_buildings': total_buildings,
                'counts': count_health,
                'message': count_error,
            },
        })
    except Exception as e:
        print(f"Plays API error: {e}")
        return jsonify({
            'success': False,
            'error': 'Prebuilt filters could not reach their data source.',
            'plays': [],
        }), 503


@app.route('/api/properties')
@cache.cached(timeout=300, query_string=True)
def api_properties():
    """
    Advanced property search API with comprehensive filtering
    
    Query Parameters:
    - search: Text search (address, BBL, owner name)
    - owner: Owner name search
    - min_value, max_value: Assessed value range
    - min_sale_price, max_sale_price: Sale price range
    - sale_date_from, sale_date_to: Sale date range
    - cash_only: Filter to cash purchases (true/false)
    - with_permits: Only properties with permits (true/false)
    - min_permits: Minimum permit count
    - recent_permit_days: Permit activity window in days (1-3650)
    - permit_activity_mode: within (default) or inactive; inactive means no
      permit on the property was filed/issued inside the selected window
    - borough: Borough filter (1-5), repeatable or comma-separated
    - building_class: Building class code prefix, repeatable or comma-separated
    - permit_type: DOB permit type, repeatable or comma-separated
    - property_type: residential/commercial/mixed, repeatable or comma-separated
    - min_units, max_units: Unit count range
    - has_violations: true and/or false; both (or neither) means no filter
    - recent_sale_days: Sold within X days
    - financing_min, financing_max: Financing ratio range
    - sort_by: Field(s) to sort by, repeatable; later keys break ties
    - sort_order: asc or desc, applied to every sort key
    - page: Page number (default 1)
    - per_page: Results per page (default 50, max 200)
    """
    try:
        with DatabaseConnection() as cur:
            # Parse query parameters
            search = request.args.get('search', '').strip()
            owner = request.args.get('owner', '').strip()
            min_value = request.args.get('min_value', type=float)
            max_value = request.args.get('max_value', type=float)
            min_sale_price = request.args.get('min_sale_price', type=float)
            max_sale_price = request.args.get('max_sale_price', type=float)
            sale_date_from = request.args.get('sale_date_from')
            sale_date_to = request.args.get('sale_date_to')
            cash_only = request.args.get('cash_only', '').lower() == 'true'
            with_permits = request.args.get('with_permits', '').lower() == 'true'
            min_permits = request.args.get('min_permits', type=int)
            boroughs = _parse_boroughs_param(request.args.get('borough', ''))
            min_units = request.args.get('min_units', type=int)
            max_units = request.args.get('max_units', type=int)
            recent_sale_days = request.args.get('recent_sale_days', type=int)
            financing_min = _percent_filter_ratio(
                request.args.get('financing_min', type=float))
            financing_max = _percent_filter_ratio(
                request.args.get('financing_max', type=float))
            sort_order = request.args.get('sort_order', 'desc').lower()
            page = max(1, request.args.get('page', 1, type=int))
            per_page = min(200, max(1, request.args.get('per_page', 50, type=int)))

            # Prebuilt play (server-defined WHERE fragment; see plays.py)
            play_where, play_error = _resolve_play_where(request.args.get('play', '').strip())
            if play_error:
                return jsonify({'success': False, 'error': play_error}), 400

            # Build WHERE clauses
            where_clauses = []
            params = []
            if play_where:
                where_clauses.append(play_where)
        
            # Text search across multiple fields
            if search:
                # Check if search looks like a zip code (5 digits starting with 1)
                is_zip_search = search.isdigit() and len(search) == 5 and search.startswith('1')
                
                if is_zip_search:
                    # Search by zip code - join with permits table
                    where_clauses.append("""(
                        b.address ILIKE %s OR 
                        b.bbl LIKE %s OR 
                        b.current_owner_name ILIKE %s OR
                        b.owner_name_rpad ILIKE %s OR
                        b.owner_name_hpd ILIKE %s OR
                        EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl AND p.zip_code = %s)
                    )""")
                    search_term = f"%{search}%"
                    params.extend([search_term, search_term, search_term, search_term, search_term, search])
                else:
                    where_clauses.append("""(
                        b.address ILIKE %s OR 
                        b.bbl LIKE %s OR 
                        b.current_owner_name ILIKE %s OR
                        b.owner_name_rpad ILIKE %s OR
                        b.owner_name_hpd ILIKE %s
                    )""")
                    search_term = f"%{search}%"
                    params.extend([search_term, search_term, search_term, search_term, search_term])
        
            # Owner search
            if owner:
                where_clauses.append("""(
                    b.current_owner_name ILIKE %s OR
                    b.owner_name_rpad ILIKE %s OR
                    b.owner_name_hpd ILIKE %s
                )""")
                owner_term = f"%{owner}%"
                params.extend([owner_term, owner_term, owner_term])
        
            # Value range
            if min_value is not None:
                where_clauses.append("b.assessed_total_value >= %s")
                params.append(min_value)
            if max_value is not None:
                where_clauses.append("b.assessed_total_value <= %s")
                params.append(max_value)
        
            # Sale price range
            if min_sale_price is not None:
                where_clauses.append("b.sale_price >= %s")
                params.append(min_sale_price)
            if max_sale_price is not None:
                where_clauses.append("b.sale_price <= %s")
                params.append(max_sale_price)
        
            # Sale date range
            if sale_date_from:
                where_clauses.append("b.sale_date >= %s")
                params.append(sale_date_from)
            if sale_date_to:
                where_clauses.append("b.sale_date <= %s")
                params.append(sale_date_to)
        
            # Cash purchases only
            if cash_only:
                where_clauses.append("b.is_cash_purchase = true")
        
            # Recent sales filter
            if recent_sale_days:
                where_clauses.append("b.sale_date >= CURRENT_DATE - INTERVAL '%s days'")
                params.append(recent_sale_days)
        
            # Financing ratio range
            if financing_min is not None:
                where_clauses.append("b.financing_ratio >= %s")
                params.append(financing_min)
            if financing_max is not None:
                where_clauses.append("b.financing_ratio <= %s")
                params.append(financing_max)
        
            # Borough filter - extract from BBL (first digit is borough code)
            if boroughs:
                placeholders = ','.join(['%s'] * len(boroughs))
                where_clauses.append(f"LEFT(b.bbl, 1) IN ({placeholders})")
                params.extend(boroughs)
        
            # Property type, building class, permit type and HPD violations are
            # all multi-select in the sidebar; one shared helper turns each set
            # of values into an OR'd clause.
            _append_category_filters(request.args, where_clauses, params)
            _append_property_permit_activity_filter(
                request.args, where_clauses, params)

            # Units range
            if min_units is not None:
                where_clauses.append("b.total_units >= %s")
                params.append(min_units)
            if max_units is not None:
                where_clauses.append("b.total_units <= %s")
                params.append(max_units)
        
            # Enrichable owner filter - has a person name (not LLC/INC/CORP) with first+last
            has_enrichable_owner = request.args.get('has_enrichable_owner', '').lower() == 'true'
            if has_enrichable_owner:
                where_clauses.append(_enrichable_owner_sql())

            # Build WHERE clause
            where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        
            # Get permit counts subquery using BBL
            permit_count_sql = """
                LEFT JOIN (
                    SELECT bbl, COUNT(*) as permit_count,
                           MAX(GREATEST(filing_date, issue_date)) AS last_permit_date
                    FROM permits
                    WHERE bbl IS NOT NULL
                    GROUP BY bbl
                ) pc ON b.bbl = pc.bbl
            """
            
            # Apply permit filters
            if with_permits:
                where_sql += (" AND " if where_clauses else "WHERE ") + "pc.permit_count > 0"
            if min_permits is not None:
                where_sql += (" AND " if where_clauses or with_permits else "WHERE ") + f"pc.permit_count >= {min_permits}"
            # Validate and sanitize sort column
            valid_sort_columns = {
                'address': 'b.address',
                'value': 'b.assessed_total_value',
                'sale_date': 'b.sale_date',
                'sale_price': 'b.sale_price',
                'owner': ('COALESCE(b.sale_buyer_primary, b.current_owner_name, '
                          'b.owner_name_hpd, b.owner_name_rpad)'),
                'permits': 'pc.permit_count',
                'recent_permits': 'pc.last_permit_date',
                'units': 'b.total_units'
            }
            # Signal-column sorts, offered only once the migration has run
            available_cols = _buildings_columns()
            if 'unused_far' in available_cols:
                valid_sort_columns['unused_far'] = 'b.unused_far'
            if 'latest_co_date' in available_cols:
                valid_sort_columns['co_date'] = 'b.latest_co_date'
            # Sort is multi-select: later keys break ties in earlier ones.
            order_by_sql = _order_by_sql(
                request.args, valid_sort_columns, 'sale_date', sort_order,
                tiebreaker='b.id',
            )
        
            # Get total count
            count_query = f"""
                SELECT COUNT(DISTINCT b.id) as count
                FROM buildings b
                {permit_count_sql}
                {where_sql}
            """
            cur.execute(count_query, params)
            result = cur.fetchone()
            total_count = result['count'] if result else 0
        
            # Calculate pagination
            offset = (page - 1) * per_page
            total_pages = (total_count + per_page - 1) // per_page
        
            # Get paginated results
            query = f"""
                SELECT 
                    b.id,
                    b.bbl,
                    b.address,
                    b.borough,
                    b.current_owner_name,
                    b.owner_name_rpad,
                    b.owner_name_hpd,
                    b.total_units,
                    b.residential_units,
                    b.building_sqft,
                    b.year_built,
                    b.year_altered,
                    b.building_class,
                    b.assessed_land_value,
                    b.assessed_total_value,
                    b.sale_price,
                    b.sale_date,
                    b.sale_buyer_primary,
                    b.sale_seller_primary,
                    b.mortgage_amount,
                    b.mortgage_lender_primary,
                    b.is_cash_purchase,
                    b.financing_ratio,
                    b.hpd_open_violations,
                    b.hpd_total_complaints,
                    b.acris_deed_count,
                    b.acris_mortgage_count,
                    b.acris_total_transactions,
                    b.lot_sqft{_signal_select_sql()},
                    COALESCE(pc.permit_count, 0) as permit_count,
                    pc.last_permit_date,
                    pcon.contractor_name,
                    pcon.contractor_phone,
                    b.last_updated
                FROM buildings b
                {permit_count_sql}
                LEFT JOIN LATERAL (
                    SELECT 
                        COALESCE(p.permittee_business_name, p.applicant) as contractor_name,
                        p.permittee_phone as contractor_phone
                    FROM permits p
                    WHERE p.bbl = b.bbl 
                      AND (p.permittee_phone IS NOT NULL OR p.permittee_business_name IS NOT NULL OR p.applicant IS NOT NULL)
                    ORDER BY p.issue_date DESC NULLS LAST
                    LIMIT 1
                ) pcon ON true
                {where_sql}
                ORDER BY {order_by_sql}
                LIMIT %s OFFSET %s
            """
        
            cur.execute(query, params + [per_page, offset])
            properties = cur.fetchall()
        
        return jsonify({
            'success': True,
            'properties': [dict(p) for p in properties],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total_count': total_count,
                'total_pages': total_pages,
                'has_next': page < total_pages,
                'has_prev': page > 1
            }
        })
        
    except Exception as e:
        print(f"Properties API error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/owner/<path:owner_name>/portfolio')
@cache.cached(timeout=300)
def api_owner_portfolio(owner_name):
    """Get all properties owned by a specific person/entity"""
    try:
        with DatabaseConnection() as cur:
            # Search across all owner name fields
            cur.execute("""
            SELECT 
                b.id,
                b.bbl,
                b.address,
                b.borough,
                b.current_owner_name,
                b.owner_name_rpad,
                b.assessed_total_value,
                b.sale_price,
                b.sale_date,
                COALESCE(b.total_units, 0) as total_units,
                b.building_class,
                b.is_cash_purchase,
                COALESCE(pc.permit_count, 0) as permit_count
            FROM buildings b
            LEFT JOIN (
                SELECT bbl, COUNT(*) as permit_count
                FROM permits
                WHERE bbl IS NOT NULL
                GROUP BY bbl
            ) pc ON b.bbl = pc.bbl
            WHERE 
                b.current_owner_name ILIKE %s OR
                b.owner_name_rpad ILIKE %s OR
                b.owner_name_hpd ILIKE %s OR
                b.sale_buyer_primary ILIKE %s
            ORDER BY b.assessed_total_value DESC NULLS LAST
        """, (f"%{owner_name}%", f"%{owner_name}%", f"%{owner_name}%", f"%{owner_name}%"))
        
            properties = cur.fetchall()
        
        # Calculate portfolio stats
        total_value = sum(p['assessed_total_value'] or 0 for p in properties)
        total_units = sum(p['total_units'] or 0 for p in properties)
        cash_purchases = sum(1 for p in properties if p['is_cash_purchase'])
        
        return jsonify({
            'success': True,
            'owner_name': owner_name,
            'properties': [dict(p) for p in properties],
            'stats': {
                'total_properties': len(properties),
                'total_assessed_value': total_value,
                'total_units': total_units,
                'cash_purchases': cash_purchases,
                'avg_property_value': total_value / len(properties) if properties else 0
            }
        })
        
    except Exception as e:
        print(f"Owner portfolio API error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/properties/stats')
@cache.cached(timeout=600)
def api_properties_stats():
    """Get aggregate statistics for properties"""
    try:
        with DatabaseConnection() as cur:
            cur.execute("""
                SELECT 
                    COUNT(*) as total_properties,
                    COUNT(CASE WHEN acris_last_enriched IS NOT NULL THEN 1 END) as with_acris,
                    COUNT(CASE WHEN is_cash_purchase = true THEN 1 END) as cash_purchases,
                    COALESCE(SUM(assessed_total_value), 0) as total_assessed_value,
                    COALESCE(AVG(assessed_total_value), 0) as avg_assessed_value,
                    COALESCE(AVG(sale_price), 0) as avg_sale_price,
                    COUNT(CASE WHEN sale_date >= CURRENT_DATE - INTERVAL '90 days' THEN 1 END) as recent_sales_90d,
                    COALESCE(SUM(COALESCE(total_units, 0)), 0) as total_units
                FROM buildings
            """)
            
            stats = cur.fetchone()
        
        return jsonify({
            'success': True,
            'stats': dict(stats)
        })
        
    except Exception as e:
        print(f"Properties stats API error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/properties/export')
@login_required
def api_properties_export():
    """
    Export properties to CSV based on current filters
    Includes unlocked contact data for THIS user only
    Max 10,000 properties per export
    """
    import csv
    from io import StringIO
    from flask import Response
    
    try:
        # Get current user ID from g.user (set by login_required decorator)
        user_id = g.user['id'] if g.user else None
        
        if not user_id:
            return jsonify({'success': False, 'error': 'User not authenticated'}), 401
        
        # Get requested fields
        fields_param = request.args.get('fields', '')
        requested_fields = [f.strip() for f in fields_param.split(',') if f.strip()]
        
        if not requested_fields:
            requested_fields = ['address', 'bbl', 'owner_name', 'assessed_value', 'sale_price', 'sale_date']
        
        with DatabaseConnection() as cur:
            # Parse filter parameters (same as api_properties)
            search = request.args.get('search', '').strip()
            owner = request.args.get('owner', '').strip()
            min_value = request.args.get('min_value', type=float)
            max_value = request.args.get('max_value', type=float)
            min_sale_price = request.args.get('min_sale_price', type=float)
            max_sale_price = request.args.get('max_sale_price', type=float)
            sale_date_from = request.args.get('sale_date_from')
            sale_date_to = request.args.get('sale_date_to')
            cash_only = request.args.get('cash_only', '').lower() == 'true'
            with_permits = request.args.get('with_permits', '').lower() == 'true'
            min_permits = request.args.get('min_permits', type=int)
            boroughs = _parse_boroughs_param(request.args.get('borough', ''))
            min_units = request.args.get('min_units', type=int)
            max_units = request.args.get('max_units', type=int)
            recent_sale_days = request.args.get('recent_sale_days', type=int)
            financing_min = _percent_filter_ratio(
                request.args.get('financing_min', type=float))
            financing_max = _percent_filter_ratio(
                request.args.get('financing_max', type=float))
            sort_order = request.args.get('sort_order', 'desc').lower()

            # Prebuilt play — exporting a play exports exactly the play's set
            play_where, play_error = _resolve_play_where(request.args.get('play', '').strip())
            if play_error:
                return jsonify({'success': False, 'error': play_error}), 400

            # Build WHERE clauses
            where_clauses = []
            params = []
            if play_where:
                where_clauses.append(play_where)

            if search:
                # Check if search looks like a zip code (5 digits starting with 1)
                is_zip_search = search.isdigit() and len(search) == 5 and search.startswith('1')

                if is_zip_search:
                    # Search by zip code - join with permits table
                    where_clauses.append("""(
                        b.address ILIKE %s OR
                        b.bbl LIKE %s OR
                        b.current_owner_name ILIKE %s OR
                        b.owner_name_rpad ILIKE %s OR
                        b.owner_name_hpd ILIKE %s OR
                        EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl AND p.zip_code = %s)
                    )""")
                    search_term = f"%{search}%"
                    params.extend([search_term, search_term, search_term, search_term, search_term, search])
                else:
                    where_clauses.append("""(
                        b.address ILIKE %s OR 
                        b.bbl LIKE %s OR 
                        b.current_owner_name ILIKE %s OR
                        b.owner_name_rpad ILIKE %s OR
                        b.owner_name_hpd ILIKE %s
                    )""")
                    search_term = f"%{search}%"
                    params.extend([search_term, search_term, search_term, search_term, search_term])
            
            if owner:
                where_clauses.append("""(
                    b.current_owner_name ILIKE %s OR
                    b.owner_name_rpad ILIKE %s OR
                    b.owner_name_hpd ILIKE %s
                )""")
                owner_term = f"%{owner}%"
                params.extend([owner_term, owner_term, owner_term])
            
            if min_value is not None:
                where_clauses.append("b.assessed_total_value >= %s")
                params.append(min_value)
            if max_value is not None:
                where_clauses.append("b.assessed_total_value <= %s")
                params.append(max_value)
            if min_sale_price is not None:
                where_clauses.append("b.sale_price >= %s")
                params.append(min_sale_price)
            if max_sale_price is not None:
                where_clauses.append("b.sale_price <= %s")
                params.append(max_sale_price)
            if sale_date_from:
                where_clauses.append("b.sale_date >= %s")
                params.append(sale_date_from)
            if sale_date_to:
                where_clauses.append("b.sale_date <= %s")
                params.append(sale_date_to)
            if cash_only:
                where_clauses.append("b.is_cash_purchase = true")
            if recent_sale_days:
                where_clauses.append("b.sale_date >= CURRENT_DATE - INTERVAL '%s days'")
                params.append(recent_sale_days)
            if financing_min is not None:
                where_clauses.append("b.financing_ratio >= %s")
                params.append(financing_min)
            if financing_max is not None:
                where_clauses.append("b.financing_ratio <= %s")
                params.append(financing_max)
            if boroughs:
                placeholders = ','.join(['%s'] * len(boroughs))
                where_clauses.append(f"LEFT(b.bbl, 1) IN ({placeholders})")
                params.extend(boroughs)
            _append_category_filters(request.args, where_clauses, params)
            _append_property_permit_activity_filter(
                request.args, where_clauses, params)
            if min_units is not None:
                where_clauses.append("COALESCE(b.total_units, 0) >= %s")
                params.append(min_units)
            if max_units is not None:
                where_clauses.append("COALESCE(b.total_units, 0) <= %s")
                params.append(max_units)
            if with_permits:
                where_clauses.append("EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl)")

            if min_permits:
                where_clauses.append("""(SELECT COUNT(*) FROM permits p WHERE p.bbl = b.bbl) >= %s""")
                params.append(min_permits)
            # Enrichable owner filter
            has_enrichable_owner = request.args.get('has_enrichable_owner', '').lower() == 'true'
            if has_enrichable_owner:
                where_clauses.append(_enrichable_owner_sql())
            
            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
            
            # Sort mapping
            sort_columns = {
                'sale_date': 'b.sale_date',
                'value': 'b.assessed_total_value',
                'sale_price': 'b.sale_price',
                'address': 'b.address',
                'owner': ('COALESCE(b.sale_buyer_primary, b.current_owner_name, '
                          'b.owner_name_hpd, b.owner_name_rpad)'),
            }
            order_by_sql = _order_by_sql(
                request.args, sort_columns, 'sale_date', sort_order)
            
            # Get user's unlocked building IDs
            cur.execute("""
                SELECT building_id FROM user_enrichments WHERE user_id = %s
            """, (user_id,))
            unlocked_building_ids = set(r['building_id'] for r in cur.fetchall())
            
            # Query properties (max 10,000)
            query = f"""
                SELECT 
                    b.id,
                    b.bbl,
                    b.address,
                    LEFT(b.bbl, 1) as borough_code,
                    b.building_class,
                    b.year_built,
                    COALESCE(b.total_units, 0) as units,
                    COALESCE(b.sale_buyer_primary, b.current_owner_name,
                             b.owner_name_hpd, b.owner_name_rpad) as owner_name,
                    COALESCE(b.sos_principal_street, b.ecb_respondent_address) as owner_address,
                    b.assessed_total_value,
                    b.sale_price,
                    b.sale_date,
                    b.is_cash_purchase,
                    b.enriched_phones,
                    b.enriched_emails,
                    COALESCE(b.hpd_total_violations, 0) as violation_count,
                    (SELECT COUNT(*) FROM permits p WHERE p.bbl = b.bbl) as permit_count
                FROM buildings b
                WHERE {where_sql}
                ORDER BY {order_by_sql}
                LIMIT 10000
            """
            
            cur.execute(query, params)
            properties = cur.fetchall()
            
            # Batch query contacts for all BBLs (for all_contacts field)
            bbls = [p['bbl'] for p in properties if p.get('bbl')]
            contacts_by_bbl = {}
            
            if bbls and ('all_contacts' in requested_fields or 'all_contact_phones' in requested_fields or 'contact_names' in requested_fields):
                # Get contacts from contacts table via permit_contacts
                cur.execute("""
                    SELECT DISTINCT p.bbl, c.name, c.phone,
                           COALESCE(pc.contact_role, c.role) AS role
                    FROM contacts c
                    JOIN permit_contacts pc ON c.id = pc.contact_id
                    JOIN permits p ON pc.permit_id = p.id
                    WHERE p.bbl = ANY(%s) AND c.phone IS NOT NULL
                """, (bbls,))
                
                for row in cur.fetchall():
                    bbl = row['bbl']
                    if bbl not in contacts_by_bbl:
                        contacts_by_bbl[bbl] = []
                    contacts_by_bbl[bbl].append({
                        'name': row['name'],
                        'phone': row['phone'],
                        'role': row['role'] or 'Contact'
                    })
                
                # Also get contacts directly from permits (permittee, owner)
                cur.execute("""
                    SELECT DISTINCT bbl, 
                        permittee_business_name, permittee_phone, permittee_license_type,
                        owner_business_name, owner_phone
                    FROM permits
                    WHERE bbl = ANY(%s) 
                    AND (permittee_phone IS NOT NULL OR owner_phone IS NOT NULL)
                """, (bbls,))
                
                for row in cur.fetchall():
                    bbl = row['bbl']
                    if bbl not in contacts_by_bbl:
                        contacts_by_bbl[bbl] = []
                    
                    # Add permittee contact
                    if row['permittee_business_name'] and row['permittee_phone']:
                        # Check if not already added
                        existing_phones = [c['phone'] for c in contacts_by_bbl[bbl]]
                        if row['permittee_phone'] not in existing_phones:
                            contacts_by_bbl[bbl].append({
                                'name': row['permittee_business_name'],
                                'phone': row['permittee_phone'],
                                'role': f"Contractor ({row['permittee_license_type'] or 'GC'})"
                            })
                    
                    # Add owner contact from permit
                    if row['owner_business_name'] and row['owner_phone']:
                        existing_phones = [c['phone'] for c in contacts_by_bbl[bbl]]
                        if row['owner_phone'] not in existing_phones:
                            contacts_by_bbl[bbl].append({
                                'name': row['owner_business_name'],
                                'phone': row['owner_phone'],
                                'role': 'Property Owner'
                            })
            
            # Build CSV
            output = StringIO()
            
            # Helper to extract ALL phones from JSON (semicolon separated)
            def get_all_phones(p):
                if p['id'] not in unlocked_building_ids:
                    return ''
                phones = p.get('enriched_phones')
                if phones and isinstance(phones, list) and len(phones) > 0:
                    return '; '.join([ph.get('number', '') for ph in phones if ph.get('number')])
                return ''
            
            # Helper to extract ALL emails from JSON (semicolon separated)
            def get_all_emails(p):
                if p['id'] not in unlocked_building_ids:
                    return ''
                emails = p.get('enriched_emails')
                if emails and isinstance(emails, list) and len(emails) > 0:
                    return '; '.join([em.get('email', '') for em in emails if em.get('email')])
                return ''
            
            # Helper to get all permit contacts for a property
            def get_all_contacts(p):
                bbl = p.get('bbl')
                if not bbl or bbl not in contacts_by_bbl:
                    return ''
                contacts = contacts_by_bbl[bbl]
                return '; '.join([f"{c['name']} ({c['role']}): {c['phone']}" for c in contacts])
            
            # Helper to get just phone numbers from permit contacts
            def get_all_contact_phones(p):
                bbl = p.get('bbl')
                if not bbl or bbl not in contacts_by_bbl:
                    return ''
                contacts = contacts_by_bbl[bbl]
                phones = list(set([c['phone'] for c in contacts if c.get('phone')]))
                return '; '.join(phones)
            
            # Helper to get just names from permit contacts
            def get_contact_names(p):
                bbl = p.get('bbl')
                if not bbl or bbl not in contacts_by_bbl:
                    return ''
                contacts = contacts_by_bbl[bbl]
                names = list(set([c['name'] for c in contacts if c.get('name')]))
                return '; '.join(names)
            
            # Map field names to column headers and data keys
            field_mapping = {
                'address': ('Address', lambda p: p['address'] or ''),
                'bbl': ('BBL', lambda p: p['bbl'] or ''),
                'borough': ('Borough', lambda p: {
                    '1': 'Manhattan', '2': 'Bronx', '3': 'Brooklyn', 
                    '4': 'Queens', '5': 'Staten Island'
                }.get(p['borough_code'], '')),
                'zip_code': ('Zip Code', lambda p: ''),  # Not in current query
                'building_class': ('Building Class', lambda p: p['building_class'] or ''),
                'year_built': ('Year Built', lambda p: p['year_built'] or ''),
                'units': ('Units', lambda p: p['units'] or ''),
                'owner_name': ('Owner Name', lambda p: p['owner_name'] or ''),
                'owner_phone': ('Enriched Owner Phone', get_all_phones),
                'owner_email': ('Enriched Owner Email', get_all_emails),
                'owner_address': ('Owner Address', lambda p: p['owner_address'] or ''),
                'assessed_value': ('Assessed Value', lambda p: p['assessed_total_value'] or ''),
                'sale_price': ('Sale Price', lambda p: p['sale_price'] or ''),
                'sale_date': ('Sale Date', lambda p: str(p['sale_date']) if p['sale_date'] else ''),
                'is_cash_purchase': ('Cash Purchase', lambda p: 'Yes' if p['is_cash_purchase'] else 'No'),
                'permit_count': ('Permit Count', lambda p: p['permit_count'] or 0),
                'violation_count': ('Violation Count', lambda p: p['violation_count'] or 0),
                # NEW: Permit-based contacts (from Contacts tab)
                'all_contacts': ('All Contacts', get_all_contacts),
                'all_contact_phones': ('All Contact Phones', get_all_contact_phones),
                'contact_names': ('Contact Names', get_contact_names),
            }
            
            # Filter to only requested fields
            headers = []
            extractors = []
            for field in requested_fields:
                if field in field_mapping:
                    header, extractor = field_mapping[field]
                    headers.append(header)
                    extractors.append(extractor)
            
            writer = csv.writer(output)
            writer.writerow(headers)
            
            for prop in properties:
                row = [extractor(prop) for extractor in extractors]
                writer.writerow(row)
            
            # Create response
            output.seek(0)
            from datetime import datetime
            filename = f"properties_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            
            return Response(
                output.getvalue(),
                mimetype='text/csv',
                headers={'Content-Disposition': f'attachment; filename={filename}'}
            )
            
    except Exception as e:
        print(f"Properties export error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/properties/export/enrichment-estimate')
@login_required
def api_properties_export_enrichment_estimate():
    """
    Calculate accurate enrichment cost estimate for bulk export.
    Returns count of enrichable contacts and cost.
    Only counts NEW contacts that will be charged (excludes already unlocked).
    """
    try:
        from enrichment_service import (
            get_enrichable_permit_contacts,
            check_permit_contact_enrichment
        )
        
        user_id = g.user['id']
        is_admin = g.user.get('is_admin', False)
        
        with DatabaseConnection() as cur:
            # Parse filter parameters (same as export)
            search = request.args.get('search', '').strip()
            owner = request.args.get('owner', '').strip()
            min_value = request.args.get('min_value', type=float)
            max_value = request.args.get('max_value', type=float)
            boroughs = _parse_boroughs_param(request.args.get('borough', ''))
            min_units = request.args.get('min_units', type=int)
            max_units = request.args.get('max_units', type=int)
            with_permits = request.args.get('with_permits', '').lower() == 'true'
            
            # Build WHERE clauses
            where_clauses = []
            params = []
            
            if search:
                where_clauses.append("(b.address ILIKE %s OR b.bbl LIKE %s OR b.current_owner_name ILIKE %s)")
                search_term = f"%{search}%"
                params.extend([search_term, search_term, search_term])
            
            if owner:
                where_clauses.append("(b.current_owner_name ILIKE %s OR b.owner_name_rpad ILIKE %s)")
                owner_term = f"%{owner}%"
                params.extend([owner_term, owner_term])
            
            if min_value is not None:
                where_clauses.append("b.assessed_total_value >= %s")
                params.append(min_value)
            
            if max_value is not None:
                where_clauses.append("b.assessed_total_value <= %s")
                params.append(max_value)
            
            if boroughs:
                placeholders = ','.join(['%s'] * len(boroughs))
                where_clauses.append(f"b.borough_code IN ({placeholders})")
                params.extend(boroughs)

            if min_units:
                where_clauses.append("b.residential_units >= %s")
                params.append(min_units)

            if max_units:
                where_clauses.append("b.residential_units <= %s")
                params.append(max_units)

            if with_permits:
                where_clauses.append("EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl)")

            # Keep the cost estimate on the same permit filters and timing
            # rule as the visible property list.
            _append_category_filters(request.args, where_clauses, params)
            _append_property_permit_activity_filter(
                request.args, where_clauses, params)
            
            where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
            
            # Get properties (limit 10000)
            query = f"""
                SELECT b.id, b.bbl
                FROM buildings b
                WHERE {where_clause}
                ORDER BY b.sale_date DESC NULLS LAST
                LIMIT 10000
            """
            
            cur.execute(query, params)
            properties = cur.fetchall()
            
            # Count enrichable contacts
            total_contacts = 0
            already_unlocked = 0
            need_enrichment = 0
            properties_with_contacts = 0
            
            for prop in properties:
                bbl = prop['bbl']
                
                # Get enrichable contacts for this property
                enrichable = get_enrichable_permit_contacts(bbl)
                
                prop_contacts = 0
                for contact in enrichable:
                    if not contact.get('is_enrichable'):
                        continue  # Skip business names
                    
                    total_contacts += 1
                    prop_contacts += 1
                    
                    contact_name = contact.get('name')
                    contact_type = contact.get('type', 'applicant')
                    
                    # Check if user already has access
                    already_enriched, existing_data, user_has_access = check_permit_contact_enrichment(
                        bbl, contact_name, contact_type, user_id
                    )
                    
                    if already_enriched and user_has_access:
                        already_unlocked += 1
                    else:
                        need_enrichment += 1
                
                if prop_contacts > 0:
                    properties_with_contacts += 1
            
            # Calculate cost ($0.35 per NEW contact, min $0.50)
            estimated_cost = max(need_enrichment * 0.35, 0.50) if need_enrichment > 0 else 0.0
            
            return jsonify({
                'success': True,
                'total_properties': len(properties),
                'properties_with_contacts': properties_with_contacts,
                'total_contacts': total_contacts,
                'already_unlocked': already_unlocked,
                'need_enrichment': need_enrichment,
                'estimated_cost': round(estimated_cost, 2),
                'is_admin': is_admin,
                'cost_per_contact': 0.35
            })
            
    except Exception as e:
        print(f"Enrichment estimate error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/properties/export-with-enrichment', methods=['POST'])
@login_required
def api_properties_export_with_enrichment():
    """
    Export properties to CSV with permit contact enrichment.
    This endpoint:
    1. Gets properties matching filters
    2. Finds enrichable permit contacts for each property
    3. Enriches contacts (charges $0.35/property for NEW enrichments only)
    4. Returns CSV with enriched contact data
    
    Charges are processed BEFORE data is returned.
    Previously unlocked contacts are free.
    """
    import csv
    from io import StringIO
    from flask import Response
    
    try:
        from enrichment_service import (
            enrich_permit_contact, 
            check_permit_contact_enrichment,
            grant_permit_contact_access,
            get_enrichable_permit_contacts
        )
        from stripe_service import charge_enrichment_fee, ensure_usage_billing_ready
        
        user_id = g.user['id']
        is_admin = g.user.get('is_admin', False)
        should_charge = g.user.get('should_charge_usage', not is_admin)

        if should_charge:
            billing_ready, billing_message, _billing = ensure_usage_billing_ready(user_id)
            if not billing_ready:
                return jsonify({'success': False, 'error': billing_message}), 402
        
        # Get requested fields
        fields_param = request.args.get('fields', '')
        requested_fields = [f.strip() for f in fields_param.split(',') if f.strip()]
        
        if not requested_fields:
            requested_fields = ['address', 'bbl', 'owner_name', 'enriched_permit_contacts']
        
        # Ensure enriched_permit_contacts is in the fields
        if 'enriched_permit_contacts' not in requested_fields:
            requested_fields.append('enriched_permit_contacts')
        
        with DatabaseConnection() as cur:
            # Parse filter parameters (same as regular export)
            search = request.args.get('search', '').strip()
            owner = request.args.get('owner', '').strip()
            min_value = request.args.get('min_value', type=float)
            max_value = request.args.get('max_value', type=float)
            min_sale_price = request.args.get('min_sale_price', type=float)
            max_sale_price = request.args.get('max_sale_price', type=float)
            sale_date_from = request.args.get('sale_date_from')
            sale_date_to = request.args.get('sale_date_to')
            cash_only = request.args.get('cash_only', '').lower() == 'true'
            with_permits = request.args.get('with_permits', '').lower() == 'true'
            min_permits = request.args.get('min_permits', type=int)
            boroughs = _parse_boroughs_param(request.args.get('borough', ''))
            min_units = request.args.get('min_units', type=int)
            max_units = request.args.get('max_units', type=int)
            recent_sale_days = request.args.get('recent_sale_days', type=int)
            financing_min = _percent_filter_ratio(
                request.args.get('financing_min', type=float))
            financing_max = _percent_filter_ratio(
                request.args.get('financing_max', type=float))

            # Build WHERE clauses (same as regular export - simplified for brevity)
            where_clauses = []
            params = []
            
            if search:
                where_clauses.append("(b.address ILIKE %s OR b.bbl LIKE %s OR b.current_owner_name ILIKE %s)")
                search_term = f"%{search}%"
                params.extend([search_term, search_term, search_term])
            
            if owner:
                where_clauses.append("(b.current_owner_name ILIKE %s OR b.owner_name_rpad ILIKE %s)")
                owner_term = f"%{owner}%"
                params.extend([owner_term, owner_term])
            
            if min_value is not None:
                where_clauses.append("b.assessed_total_value >= %s")
                params.append(min_value)
            
            if max_value is not None:
                where_clauses.append("b.assessed_total_value <= %s")
                params.append(max_value)
            
            if boroughs:
                placeholders = ','.join(['%s'] * len(boroughs))
                where_clauses.append(f"b.borough_code IN ({placeholders})")
                params.extend(boroughs)

            if min_units:
                where_clauses.append("b.residential_units >= %s")
                params.append(min_units)

            if max_units:
                where_clauses.append("b.residential_units <= %s")
                params.append(max_units)

            if with_permits or min_permits:
                where_clauses.append("EXISTS (SELECT 1 FROM permits p WHERE p.bbl = b.bbl)")

            # These were read from the query string but never applied, so an
            # enriched export could bill for rows the filtered screen excluded.
            _append_category_filters(request.args, where_clauses, params)
            _append_property_permit_activity_filter(
                request.args, where_clauses, params)

            if min_permits:
                where_clauses.append(
                    "(SELECT COUNT(*) FROM permits p WHERE p.bbl = b.bbl) >= %s")
                params.append(min_permits)

            # Build query (limit 10000)
            where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
            
            query = f"""
                SELECT b.id, b.bbl, b.address, b.borough_code, b.building_class,
                       b.year_built, b.residential_units as units,
                       b.current_owner_name as owner_name,
                       b.assessed_total_value, b.sale_price, b.sale_date
                FROM buildings b
                WHERE {where_clause}
                ORDER BY b.sale_date DESC NULLS LAST
                LIMIT 10000
            """
            
            cur.execute(query, params)
            properties = cur.fetchall()
            
            print(f"[Bulk Export] Found {len(properties)} properties to export with enrichment")
            
            # Track enrichment results and charges
            enriched_contacts_by_bbl = {}
            total_charged = 0.0
            new_enrichments_count = 0
            already_unlocked_count = 0
            failed_enrichments = []
            
            # Process each property's permit contacts
            for prop in properties:
                bbl = prop['bbl']
                building_id = prop['id']
                enriched_contacts_by_bbl[bbl] = []
                
                # Get enrichable contacts for this property
                enrichable = get_enrichable_permit_contacts(bbl)
                
                for contact in enrichable:
                    contact_name = contact.get('name')
                    contact_type = contact.get('type', 'applicant')
                    license_number = contact.get('license_number')
                    license_type = contact.get('license_type')
                    original_phone = contact.get('phone')
                    permit_id = contact.get('permit_id')
                    
                    if not contact_name:
                        continue
                    
                    # Check if already enriched and user has access
                    already_enriched, existing_data, user_has_access = check_permit_contact_enrichment(
                        bbl, contact_name, contact_type, user_id
                    )
                    
                    if already_enriched and user_has_access:
                        # Already have access - add to results, no charge
                        already_unlocked_count += 1
                        enriched_contacts_by_bbl[bbl].append({
                            'name': contact_name,
                            'type': contact_type,
                            'phones': existing_data.get('phones', []),
                            'emails': existing_data.get('emails', [])
                        })
                        continue
                    
                    # Need to enrich (or just grant access if already enriched)
                    # For bulk: charge FIRST, then grant access
                    need_to_charge = should_charge
                    
                    if need_to_charge:
                        # Charge for this enrichment ($0.35 for bulk)
                        charge_success, charge_msg, charge_id = charge_enrichment_fee(
                            user_id, building_id, contact_name, is_batch=True,
                            charge_scope='permit_contact'
                        )
                        
                        if not charge_success:
                            failed_enrichments.append({
                                'bbl': bbl,
                                'contact': contact_name,
                                'error': f'Payment failed: {charge_msg}'
                            })
                            continue
                        
                        total_charged += 0.35
                    else:
                        charge_id = 'admin_free'
                    
                    # Now do the enrichment (with grant_access=True since we already charged)
                    success, enrichment_data, message = enrich_permit_contact(
                        bbl, building_id, permit_id, contact_name, contact_type,
                        license_number, license_type, original_phone, user_id,
                        grant_access=True  # Grant access since charge succeeded
                    )
                    
                    if success:
                        new_enrichments_count += 1
                        enriched_contacts_by_bbl[bbl].append({
                            'name': contact_name,
                            'type': contact_type,
                            'phones': enrichment_data.get('phones', []),
                            'emails': enrichment_data.get('emails', [])
                        })
                    else:
                        failed_enrichments.append({
                            'bbl': bbl,
                            'contact': contact_name,
                            'error': message
                        })
            
            print(f"[Bulk Export] Enrichment complete: {new_enrichments_count} new, {already_unlocked_count} already unlocked, {len(failed_enrichments)} failed. Total charged: ${total_charged:.2f}")
            
            # Build CSV with enriched data
            output = StringIO()
            
            # Helper to format enriched contacts
            def get_enriched_contacts_str(p):
                bbl = p.get('bbl')
                contacts = enriched_contacts_by_bbl.get(bbl, [])
                if not contacts:
                    return ''
                
                parts = []
                for c in contacts:
                    phones = [ph.get('number', '') for ph in c.get('phones', []) if ph.get('number')]
                    emails = [em.get('email', '') for em in c.get('emails', []) if em.get('email')]
                    contact_str = f"{c['name']} ({c['type']})"
                    if phones:
                        contact_str += f" Phone: {', '.join(phones[:2])}"
                    if emails:
                        contact_str += f" Email: {', '.join(emails[:2])}"
                    parts.append(contact_str)
                
                return ' | '.join(parts)
            
            # Helper to get just phones
            def get_enriched_phones_str(p):
                bbl = p.get('bbl')
                contacts = enriched_contacts_by_bbl.get(bbl, [])
                all_phones = []
                for c in contacts:
                    phones = [ph.get('number', '') for ph in c.get('phones', []) if ph.get('number')]
                    all_phones.extend(phones)
                return '; '.join(list(set(all_phones)))
            
            # Helper to get just emails
            def get_enriched_emails_str(p):
                bbl = p.get('bbl')
                contacts = enriched_contacts_by_bbl.get(bbl, [])
                all_emails = []
                for c in contacts:
                    emails = [em.get('email', '') for em in c.get('emails', []) if em.get('email')]
                    all_emails.extend(emails)
                return '; '.join(list(set(all_emails)))
            
            # Field mapping
            field_mapping = {
                'address': ('Address', lambda p: p['address'] or ''),
                'bbl': ('BBL', lambda p: p['bbl'] or ''),
                'borough': ('Borough', lambda p: {
                    '1': 'Manhattan', '2': 'Bronx', '3': 'Brooklyn', 
                    '4': 'Queens', '5': 'Staten Island'
                }.get(p['borough_code'], '')),
                'building_class': ('Building Class', lambda p: p['building_class'] or ''),
                'year_built': ('Year Built', lambda p: p['year_built'] or ''),
                'units': ('Units', lambda p: p['units'] or ''),
                'owner_name': ('Owner Name', lambda p: p['owner_name'] or ''),
                'assessed_value': ('Assessed Value', lambda p: p['assessed_total_value'] or ''),
                'sale_price': ('Sale Price', lambda p: p['sale_price'] or ''),
                'sale_date': ('Sale Date', lambda p: str(p['sale_date']) if p['sale_date'] else ''),
                'enriched_permit_contacts': ('Enriched Permit Contacts', get_enriched_contacts_str),
                'enriched_permit_phones': ('Enriched Contact Phones', get_enriched_phones_str),
                'enriched_permit_emails': ('Enriched Contact Emails', get_enriched_emails_str),
            }
            
            # Build headers and extractors
            headers = []
            extractors = []
            for field in requested_fields:
                if field in field_mapping:
                    header, extractor = field_mapping[field]
                    headers.append(header)
                    extractors.append(extractor)
            
            writer = csv.writer(output)
            writer.writerow(headers)
            
            for prop in properties:
                row = [extractor(prop) for extractor in extractors]
                writer.writerow(row)
            
            # Return CSV
            output.seek(0)
            from datetime import datetime
            filename = f"properties_export_enriched_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            
            return Response(
                output.getvalue(),
                mimetype='text/csv',
                headers={'Content-Disposition': f'attachment; filename={filename}'}
            )
            
    except Exception as e:
        print(f"Bulk export with enrichment error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# CONTRACTOR PROFILE ROUTES
# ============================================================================

@app.route('/contractors')
@login_required
def contractors_page():
    """Render the contractors search/browse page"""
    return render_template(
        'contractors.html',
        building_class_groups=building_class_options(),
        active_page='contractors',
    )


@app.route('/alerts')
@login_required
def sales_alerts_page():
    """Render the salesperson-facing DOB project alert queue."""
    return render_template('sales_alerts.html', active_page='alerts')


@app.route('/api/sales-alerts')
@login_required
def api_sales_alerts():
    """Return searchable, deduplicated DOB project events."""
    try:
        status = request.args.get('status', 'all').strip().lower()
        alert_type = request.args.get('type', '').strip().lower()
        search = request.args.get('q', '').strip()
        limit = min(500, max(1, request.args.get('limit', 100, type=int)))
        where = []
        params = []
        if status and status != 'all':
            where.append('sa.status = %s')
            params.append(status)
        if alert_type:
            where.append('sa.alert_type = %s')
            params.append(alert_type)
        if search:
            where.append("""(
                sa.title ILIKE %s OR sa.summary ILIKE %s OR pr.address ILIKE %s OR
                pr.owner_business_name ILIKE %s OR pr.job_number ILIKE %s OR
                pr.applicant_business_name ILIKE %s OR
                pr.filing_representative_business_name ILIKE %s OR
                pr.design_professional_business_name ILIKE %s
            )""")
            term = f'%{search}%'
            params.extend([term] * 8)
        where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

        with DatabaseConnection() as cur:
            cur.execute(f"""
                SELECT sa.id, sa.alert_type, sa.title, sa.summary,
                       sa.old_value, sa.new_value, sa.event_at, sa.status,
                       sa.assigned_to, pr.id AS project_id, pr.project_key,
                       pr.job_number, pr.address, pr.borough, pr.bbl,
                       pr.job_type, pr.initial_cost,
                       pr.existing_stories_count, pr.proposed_stories_count,
                       pr.existing_dwelling_units, pr.proposed_dwelling_units,
                       pr.current_status, pr.current_status_date,
                       pr.owner_business_name, pr.applicant_business_name,
                       pr.applicant_professional_title,
                       pr.filing_representative_business_name,
                       pr.design_professional_business_name,
                       pr.design_professional_person_name,
                       pr.design_professional_license,
                       pr.has_electrical_filing, pr.electrical_service_work,
                       pr.electrical_general_wiring, pr.electrical_lighting_work,
                       pr.electrical_temp_power,
                       pr.electrical_hvac_or_boiler_wiring,
                       pr.electrical_new_meters,
                       pr.electrical_detail_count,
                       pr.electrical_scope_categories,
                       pr.electrical_floor_names,
                       pr.has_elevator_filing,
                       pr.elevator_device_types,
                       pr.elevator_work_types
                FROM sales_alerts sa
                JOIN projects pr ON pr.id = sa.project_id
                {where_sql}
                ORDER BY sa.event_at DESC, sa.id DESC
                LIMIT %s
            """, params + [limit])
            alerts = [dict(row) for row in cur.fetchall()]
            cur.execute("""
                SELECT status, COUNT(*) AS count
                FROM sales_alerts GROUP BY status
            """)
            counts = {row['status']: row['count'] for row in cur.fetchall()}

        return jsonify({'success': True, 'alerts': alerts, 'counts': counts})
    except Exception as e:
        print(f"Sales alerts API error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# LEAD DISCOVERY: EXTERNAL SIGNALS AND REPEAT BUYERS
# ============================================================================

@app.route('/signals')
@login_required
def external_signals_page():
    return render_template('external_signals.html', active_page='signals')


@app.route('/api/external-signals')
@login_required
def api_external_signals():
    """Ranked City Record opportunities kept separate from DOB projects."""
    days = min(3650, max(1, request.args.get('days', 90, type=int)))
    min_score = min(100, max(0, request.args.get('min_score', 10, type=int)))
    status = request.args.get('status', 'all').strip().lower()
    search = request.args.get('q', '').strip()
    limit = min(500, max(1, request.args.get('limit', 100, type=int)))
    clauses = [
        "source = 'city_record'",
        "notice_date >= CURRENT_DATE - (%s || ' days')::interval",
        "relevance_score >= %s",
    ]
    params = [days, min_score]
    if status in ('new', 'reviewed', 'dismissed'):
        clauses.append('review_status = %s')
        params.append(status)
    if search:
        clauses.append("""(
            title ILIKE %s OR description ILIKE %s OR agency_name ILIKE %s OR
            vendor_name ILIKE %s OR contact_name ILIKE %s OR
            street_address_1 ILIKE %s OR building_name ILIKE %s OR pin ILIKE %s
        )""")
        term = f'%{search}%'
        params.extend([term] * 8)
    try:
        with DatabaseConnection() as cur:
            cur.execute(f"""
                SELECT id, source_record_id, signal_type, title, description,
                       agency_name, category, selection_method, section_name, pin,
                       notice_date, end_date, due_date, event_date, contact_name,
                       contact_phone, contact_email, vendor_name, vendor_address,
                       contract_amount, building_name, street_address_1,
                       street_address_2, city, state, zip_code, source_url,
                       relevance_score, relevance_reasons, review_status
                FROM external_project_signals
                WHERE {' AND '.join(clauses)}
                ORDER BY relevance_score DESC,
                         due_date ASC NULLS LAST, notice_date DESC, id DESC
                LIMIT %s
            """, params + [limit])
            signals = [dict(row) for row in cur.fetchall()]
        return jsonify({
            'success': True,
            'signals': signals,
            'filters': {'days': days, 'min_score': min_score, 'status': status},
            'separation_note': (
                'City Record notices are pre-permit evidence. They are not linked to a DOB '
                'project until the participant-graph phase can support that relationship.'
            ),
        })
    except Exception as e:
        print(f"External signals API error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


_BUYER_KEY_SQL = "UPPER(REGEXP_REPLACE(COALESCE(pr.owner_business_name, ''), '[^A-Za-z0-9]+', '', 'g'))"


@app.route('/buyers')
@login_required
def repeat_buyers_page():
    """Rank repeat project buyers/accounts rather than individual permit contacts."""
    return render_template('repeat_buyers.html', active_page='buyers')


@app.route('/api/repeat-buyers')
@login_required
def api_repeat_buyers():
    """Return exact-entity repeat buyers ranked by distinct DOB projects."""
    search = request.args.get('search', '').strip()
    min_projects = min(100, max(2, request.args.get('min_projects', 2, type=int)))
    limit = min(500, max(1, request.args.get('limit', 100, type=int)))
    where = ["NULLIF(BTRIM(pr.owner_business_name), '') IS NOT NULL",
             f"{_BUYER_KEY_SQL} NOT IN ('', 'NA', 'NONE', 'NOTAPPLICABLE', 'PR')"]
    params = []
    if search:
        where.append('pr.owner_business_name ILIKE %s')
        params.append(f'%{search}%')
    try:
        with DatabaseConnection() as cur:
            cur.execute(f"""
                WITH buyer_projects AS (
                    SELECT {_BUYER_KEY_SQL} AS buyer_key, pr.*
                    FROM projects pr
                    WHERE {' AND '.join(where)}
                ), ranked AS (
                    SELECT buyer_key,
                           (ARRAY_AGG(owner_business_name ORDER BY
                               current_status_date DESC NULLS LAST, id DESC))[1] AS buyer_name,
                           COUNT(DISTINCT id) AS distinct_projects,
                           COUNT(DISTINCT bbl) FILTER (WHERE bbl IS NOT NULL) AS distinct_properties,
                           COUNT(*) FILTER (WHERE COALESCE(current_status_date::date,
                               latest_issue_date, first_filing_date) >= CURRENT_DATE - INTERVAL '365 days')
                               AS recent_projects_12m,
                           COUNT(*) FILTER (WHERE has_electrical_filing OR
                               has_elevator_filing OR
                               COALESCE(work_description, '') ~*
                               '(intercom|camera|cctv|access control|telecom|low[ -]?voltage|data cabl|wi[ -]?fi|security)')
                               AS smart_fit_projects,
                           COALESCE(SUM(initial_cost), 0) AS total_initial_cost,
                           MAX(COALESCE(current_status_date, latest_issue_date::timestamp,
                               first_filing_date::timestamp)) AS latest_activity,
                           (ARRAY_AGG(DISTINCT address) FILTER (WHERE address IS NOT NULL))[1:5]
                               AS sample_addresses,
                           STRING_AGG(DISTINCT job_type, ', ' ORDER BY job_type)
                               FILTER (WHERE job_type IS NOT NULL) AS job_types
                    FROM buyer_projects
                    GROUP BY buyer_key
                )
                SELECT *, ROUND((recent_projects_12m * 30
                                  + distinct_projects * 12
                                  + smart_fit_projects * 8
                                  + LEAST(total_initial_cost / 100000.0, 50))::numeric, 1)
                                  AS account_score
                FROM ranked
                WHERE distinct_projects >= %s
                ORDER BY account_score DESC, recent_projects_12m DESC,
                         distinct_projects DESC, latest_activity DESC NULLS LAST
                LIMIT %s
            """, params + [min_projects, limit])
            buyers = [dict(row) for row in cur.fetchall()]
        return jsonify({
            'success': True,
            'buyers': buyers,
            'resolution_note': ('Grouped by normalized exact owner name only. Affiliated LLC and '
                                'corporate-family resolution is intentionally deferred to the graph phase.'),
        })
    except Exception as e:
        print(f"Repeat buyers API error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# path converter: DOB applicant names contain slashes ("A/C", "D/B/A ...")
# and WSGI decodes %2F before routing, so the default converter 404s them.
@app.route('/contractor/<path:contractor_name>')
@login_required
def contractor_profile(contractor_name):
    """Render contractor profile page"""
    return render_template(
        'contractor_profile.html',
        contractor_name=contractor_name,
        active_page='contractors',
    )


def _attach_work_mix(cur, contractors, where_parts, where_params, top_n=4):
    """Fill in what kind of work each contractor on this page actually does.

    DOB spreads the answer over three columns and which one is populated
    varies by permit feed — scaffolding work, for instance, often carries a
    work_type with no job_type at all, which is why the old
    string_agg(job_type) rendered as N/A for whole categories of contractor.
    Falling back through work_type, permit_type then job_type gives every
    contractor a real answer.

    Runs as one extra query scoped to the names already on the page.
    """
    for contractor in contractors:
        contractor['work_mix'] = []
        contractor['job_types'] = None

    names = [c['contractor_name'] for c in contractors if c.get('contractor_name')]
    if not names:
        return

    scoped = list(where_parts) + [
        "COALESCE(pp.business_name, pp.person_name) = ANY(%s)"
    ]
    cur.execute(f"""
        SELECT
            COALESCE(pp.business_name, pp.person_name) AS contractor_name,
            COALESCE(
                NULLIF(UPPER(btrim(p.work_type)), ''),
                NULLIF(UPPER(btrim(p.permit_type)), ''),
                NULLIF(UPPER(btrim(p.job_type)), '')
            ) AS code,
            COUNT(DISTINCT p.id) AS permit_count
        FROM permit_participants pp
        JOIN permits p ON p.id = pp.permit_id
        LEFT JOIN buildings b ON p.bbl = b.bbl
        WHERE {' AND '.join(scoped)}
        GROUP BY 1, 2
        HAVING COALESCE(
            NULLIF(UPPER(btrim(p.work_type)), ''),
            NULLIF(UPPER(btrim(p.permit_type)), ''),
            NULLIF(UPPER(btrim(p.job_type)), '')
        ) IS NOT NULL
        ORDER BY 1, COUNT(DISTINCT p.id) DESC
    """, where_params + [names])

    by_name = {}
    for row in cur.fetchall():
        by_name.setdefault(row['contractor_name'], []).append({
            'code': row['code'],
            'label': (WORK_TYPE_LABELS.get(row['code'])
                      or PERMIT_TYPE_LABELS.get(row['code'])
                      or JOB_TYPE_LABELS.get(row['code'])
                      or row['code']),
            'count': row['permit_count'],
        })

    for contractor in contractors:
        mix = by_name.get(contractor['contractor_name'], [])
        contractor['work_mix'] = mix[:top_n]
        contractor['work_mix_other'] = max(0, len(mix) - top_n)
        # Kept for anything still reading the old flat field.
        contractor['job_types'] = ', '.join(m['code'] for m in mix[:top_n]) or None


@app.route('/api/contractors/search')
@cache.cached(timeout=300, query_string=True)
def api_contractors_search():
    """
    Search contractors with aggregated stats.

    Takes the same filter vocabulary as /api/properties. Building attributes
    still describe properties worked on; permit attributes describe the
    participant's own permit rows. In inactive mode this directory therefore
    counts older permit records, while /api/properties applies true
    no-recent-permit logic at the whole-building grain.

    Shared with /api/properties:
    - search, borough, property_type, building_class, min_units, max_units,
      min_value, max_value, permit_type, work_type, job_type, license_type,
      recent_permit_days, permit_activity_mode

    Contractor-specific:
    - min_jobs, max_jobs, min_active_jobs, min_properties, max_properties
    - sort_by: total_jobs, active_jobs, total_value, largest_project,
      unique_properties, most_recent_job — repeatable; later keys break ties
    - sort_order: asc or desc, applied to every sort key
    - page, per_page
    """
    try:
        with DatabaseConnection() as cur:
            search = request.args.get('search', '').strip()
            sort_order = request.args.get('sort_order', 'desc').lower()
            page = max(1, request.args.get('page', 1, type=int))
            per_page = min(200, max(1, request.args.get('per_page', 50, type=int)))
            offset = (page - 1) * per_page

            # The old directory grouped the overloaded permits.applicant field
            # and called every value a contractor.  The participant view keeps
            # permittees, applicants, owners and filing reps distinct.
            participant_name = "COALESCE(pp.business_name, pp.person_name)"
            where_parts = [f"""{participant_name} IS NOT NULL
                AND {participant_name} NOT IN ('', 'N/A', 'NA', 'NONE')
                AND {participant_name} NOT ILIKE 'unknown%%'"""]
            where_params = []

            if search:
                where_parts.append(
                    f'({participant_name} ILIKE %s OR pp.license_number ILIKE %s)')
                term = f"%{search}%"
                where_params.extend([term, term])

            roles = [role.strip() for role in request.args.get('role', '').split(',')
                     if role.strip()]
            if roles:
                where_parts.append('pp.role = ANY(%s)')
                where_params.append(roles)

            probable_gc = request.args.get('probable_gc', '').lower() in ('1', 'true', 'yes')
            if probable_gc:
                where_parts.append("""pp.role IN ('permittee', 'permit_applicant')
                    AND pp.contractor_confidence >= 0.85""")

            # Permit attributes — the same helper /api/properties uses.
            permit_parts, permit_params = _permit_predicates(request.args, alias='p')
            where_parts.extend(permit_parts)
            where_params.extend(permit_params)

            # Borough, from the permit itself so contractors working on
            # buildings we have not enriched yet are still filterable.
            boroughs = _parse_boroughs_param(request.args.get('borough', ''))
            if boroughs:
                placeholders = ','.join(['%s'] * len(boroughs))
                where_parts.append(f'LEFT(p.bbl, 1) IN ({placeholders})')
                where_params.extend(boroughs)

            # Building attributes of the properties worked on, straight from
            # the joined row. The permits EXISTS that /api/properties adds is
            # deliberately not used here: this query already filters permits
            # directly, and an EXISTS would match any permit on the building
            # rather than this contractor's own.
            building_parts, building_params = [], []
            _append_building_only_filters(request.args, building_parts, building_params)
            where_parts.extend(building_parts)
            where_params.extend(building_params)

            min_units = request.args.get('min_units', type=int)
            max_units = request.args.get('max_units', type=int)
            min_value = request.args.get('min_value', type=float)
            max_value = request.args.get('max_value', type=float)
            if min_units is not None:
                where_parts.append('COALESCE(b.total_units, 0) >= %s')
                where_params.append(min_units)
            if max_units is not None:
                where_parts.append('COALESCE(b.total_units, 0) <= %s')
                where_params.append(max_units)
            if min_value is not None:
                where_parts.append('b.assessed_total_value >= %s')
                where_params.append(min_value)
            if max_value is not None:
                where_parts.append('b.assessed_total_value <= %s')
                where_params.append(max_value)

            where_clause = 'WHERE ' + ' AND '.join(where_parts)

            # Contractor-scale filters apply to the aggregates, so they belong
            # in HAVING rather than WHERE.
            having_parts, having_params = [], []
            for param, expr in (
                ('min_jobs', 'COUNT(DISTINCT p.id) >= %s'),
                ('max_jobs', 'COUNT(DISTINCT p.id) <= %s'),
                ('min_active_jobs',
                 "COUNT(DISTINCT CASE WHEN p.issue_date >= CURRENT_DATE - INTERVAL '90 days'"
                 ' THEN p.id END) >= %s'),
                ('min_properties', 'COUNT(DISTINCT p.bbl) >= %s'),
                ('max_properties', 'COUNT(DISTINCT p.bbl) <= %s'),
            ):
                value = request.args.get(param, type=int)
                if value is not None:
                    having_parts.append(expr)
                    having_params.append(value)
            having_clause = ('HAVING ' + ' AND '.join(having_parts)) if having_parts else ''

            order_by_sql = _order_by_sql(
                request.args,
                {
                    'active_jobs': 'active_jobs',
                    'total_jobs': 'total_jobs',
                    'total_value': 'total_value',
                    'largest_project': 'largest_project',
                    'unique_properties': 'unique_properties',
                    'most_recent_job': 'most_recent_job',
                },
                'total_jobs',
                sort_order,
                tiebreaker='contractor_name ASC',
            )

            query = f"""
                WITH contractor_stats AS (
                    SELECT
                        {participant_name} as contractor_name,
                        pp.license_number as license,
                        COUNT(DISTINCT p.id) as total_jobs,
                        COUNT(DISTINCT CASE WHEN p.issue_date >= CURRENT_DATE - INTERVAL '90 days' THEN p.id END) as active_jobs,
                        COALESCE(SUM(b.assessed_total_value), 0) as total_value,
                        COALESCE(MAX(b.assessed_total_value), 0) as largest_project,
                        MAX(p.issue_date) as most_recent_job,
                        MIN(p.issue_date) as first_job,
                        COUNT(DISTINCT p.bbl) as unique_properties,
                        COUNT(DISTINCT NULLIF(btrim(pp.license_type), '')) as license_type_count,
                        (array_agg(DISTINCT UPPER(btrim(pp.license_type)))
                            FILTER (WHERE btrim(coalesce(pp.license_type, '')) <> ''))[1]
                            as license_type,
                        string_agg(DISTINCT pp.role, ', ' ORDER BY pp.role) AS participant_roles,
                        MAX(pp.role_confidence) AS role_confidence,
                        MAX(pp.contractor_confidence) AS contractor_confidence,
                        BOOL_OR(pp.role IN ('permittee', 'permit_applicant')
                                AND pp.contractor_confidence >= 0.85) AS probable_gc
                    FROM permit_participants pp
                    JOIN permits p ON p.id = pp.permit_id
                    LEFT JOIN buildings b ON p.bbl = b.bbl
                    {where_clause}
                    GROUP BY {participant_name}, pp.license_number
                    {having_clause}
                )
                SELECT *
                FROM contractor_stats
                ORDER BY {order_by_sql}
                LIMIT %s OFFSET %s
            """

            cur.execute(query, where_params + having_params + [per_page, offset])
            contractors = [dict(c) for c in cur.fetchall()]

            # What kind of work each of these contractors actually does.
            # Done as a second pass over just the page's contractors so the
            # aggregate above stays cheap.
            _attach_work_mix(cur, contractors, where_parts, where_params)

            if having_parts:
                count_query = f"""
                    SELECT COUNT(*) AS count FROM (
                        SELECT 1
                        FROM permit_participants pp
                        JOIN permits p ON p.id = pp.permit_id
                        LEFT JOIN buildings b ON p.bbl = b.bbl
                        {where_clause}
                        GROUP BY {participant_name}, pp.license_number
                        {having_clause}
                    ) matched
                """
                cur.execute(count_query, where_params + having_params)
            else:
                count_query = f"""
                    SELECT COUNT(*) AS count FROM (
                        SELECT 1
                        FROM permit_participants pp
                        JOIN permits p ON p.id = pp.permit_id
                        LEFT JOIN buildings b ON p.bbl = b.bbl
                        {where_clause}
                        GROUP BY {participant_name}, pp.license_number
                    ) matched
                """
                cur.execute(count_query, where_params)
            total = cur.fetchone()['count']

        return jsonify({
            'success': True,
            'contractors': contractors,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page
            }
        })

    except Exception as e:
        print(f"Contractors search API error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500



@app.route('/api/contractor/<path:contractor_name>')
@cache.cached(timeout=300)
def api_contractor_profile(contractor_name):
    """
    Get detailed contractor profile with permits and buildings
    """
    try:
        with DatabaseConnection() as cur:
            # Get contractor stats
            cur.execute("""
            SELECT 
                COALESCE(pp.business_name, pp.person_name) as contractor_name,
                (array_agg(DISTINCT pp.license_number)
                    FILTER (WHERE pp.license_number IS NOT NULL))[1] as license,
                (array_agg(DISTINCT pp.license_type)
                    FILTER (WHERE pp.license_type IS NOT NULL))[1] as license_type,
                string_agg(DISTINCT pp.role, ', ' ORDER BY pp.role) AS participant_roles,
                MAX(pp.role_confidence) AS role_confidence,
                MAX(pp.contractor_confidence) AS contractor_confidence,
                COUNT(DISTINCT p.id) as total_jobs,
                COUNT(DISTINCT CASE WHEN p.issue_date >= CURRENT_DATE - INTERVAL '90 days' THEN p.id END) as active_jobs,
                COUNT(DISTINCT CASE WHEN p.issue_date >= CURRENT_DATE - INTERVAL '365 days' THEN p.id END) as jobs_last_year,
                COALESCE(SUM(b.assessed_total_value), 0) as total_value,
                COALESCE(MAX(b.assessed_total_value), 0) as largest_project,
                COALESCE(AVG(b.assessed_total_value), 0) as avg_project_value,
                MAX(p.issue_date) as most_recent_job,
                MIN(p.issue_date) as first_job,
                COUNT(DISTINCT p.bbl) as unique_properties,
                COUNT(DISTINCT p.job_type) as job_type_variety,
                string_agg(DISTINCT p.job_type, ', ') as job_types
            FROM permit_participants pp
            JOIN permits p ON p.id = pp.permit_id
            LEFT JOIN buildings b ON p.bbl = b.bbl
            WHERE COALESCE(pp.business_name, pp.person_name) = %s
            GROUP BY COALESCE(pp.business_name, pp.person_name)
            """, (contractor_name,))
            
            stats = cur.fetchone()
            
            if not stats:
                return jsonify({'success': False, 'error': 'Permit participant not found'}), 404
            
            # Get permits (most recent first)
            cur.execute("""
                SELECT DISTINCT
                    p.id,
                    p.permit_no,
                    p.job_type,
                    p.address,
                    p.bbl,
                    p.issue_date,
                    p.stories,
                    p.total_units,
                    p.use_type,
                    p.link,
                    b.assessed_total_value,
                    b.current_owner_name
                FROM permit_participants pp
                JOIN permits p ON p.id = pp.permit_id
                LEFT JOIN buildings b ON p.bbl = b.bbl
                WHERE COALESCE(pp.business_name, pp.person_name) = %s
                ORDER BY p.issue_date DESC NULLS LAST
                LIMIT 500
            """, (contractor_name,))
            
            permits = cur.fetchall()
            
            # Get unique buildings (most recent work first)
            cur.execute("""
                SELECT 
                    b.id,
                    b.bbl,
                    b.address,
                    b.borough,
                    b.current_owner_name,
                    b.assessed_total_value,
                    b.total_units,
                    b.building_class,
                    COUNT(DISTINCT p.id) as permit_count,
                    MAX(p.issue_date) as most_recent_work,
                    MIN(p.issue_date) as first_work,
                    string_agg(DISTINCT p.job_type, ', ') as job_types
                FROM buildings b
                INNER JOIN permits p ON p.bbl = b.bbl
                INNER JOIN permit_participants pp ON pp.permit_id = p.id
                WHERE COALESCE(pp.business_name, pp.person_name) = %s
                GROUP BY b.id, b.bbl, b.address, b.borough, b.current_owner_name, 
                         b.assessed_total_value, b.total_units, b.building_class
                ORDER BY most_recent_work DESC NULLS LAST
                LIMIT 500
            """, (contractor_name,))
            
            buildings = cur.fetchall()
        
        return jsonify({
            'success': True,
            'contractor': dict(stats),
            'permits': [dict(p) for p in permits],
            'buildings': [dict(b) for b in buildings]
        })
        
    except Exception as e:
        print(f"Contractor profile API error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/license/<license_number>/permits')
@cache.cached(timeout=300)
def api_license_permits(license_number):
    """
    Get all permits associated with a license number with work type analysis
    """
    try:
        with DatabaseConnection() as cur:
            # Get total permit count
            cur.execute("""
                SELECT COUNT(*) as total_count
                FROM permits
                WHERE permittee_license_number = %s
            """, (license_number,))
            total_count = cur.fetchone()['total_count']
            
            # Get permits by this license number (recent 100)
            cur.execute("""
                SELECT 
                    permit_no, bbl, address, job_type, work_type,
                    issue_date, filing_date, permittee_business_name, permittee_license_type,
                    applicant
                FROM permits
                WHERE permittee_license_number = %s
                ORDER BY issue_date DESC NULLS LAST
                LIMIT 100
            """, (license_number,))
            
            permits = cur.fetchall()
            
            # Get unique buildings count
            cur.execute("""
                SELECT COUNT(DISTINCT bbl) as unique_buildings
                FROM permits
                WHERE permittee_license_number = %s
            """, (license_number,))
            unique_buildings = cur.fetchone()['unique_buildings']
            
            # Get the contractor name (most common one for this license)
            cur.execute("""
                SELECT permittee_business_name, COUNT(*) as cnt
                FROM permits
                WHERE permittee_license_number = %s AND permittee_business_name IS NOT NULL
                GROUP BY permittee_business_name
                ORDER BY cnt DESC
                LIMIT 1
            """, (license_number,))
            contractor_row = cur.fetchone()
            contractor_name = contractor_row['permittee_business_name'] if contractor_row else None
            
            # Get applicant name (the actual licensed professional)
            cur.execute("""
                SELECT applicant, COUNT(*) as cnt
                FROM permits
                WHERE permittee_license_number = %s AND applicant IS NOT NULL
                GROUP BY applicant
                ORDER BY cnt DESC
                LIMIT 1
            """, (license_number,))
            applicant_row = cur.fetchone()
            applicant_name = applicant_row['applicant'] if applicant_row else None
            
            # Get license type
            cur.execute("""
                SELECT permittee_license_type, COUNT(*) as cnt
                FROM permits
                WHERE permittee_license_number = %s AND permittee_license_type IS NOT NULL
                GROUP BY permittee_license_type
                ORDER BY cnt DESC
                LIMIT 1
            """, (license_number,))
            license_type_row = cur.fetchone()
            license_type = license_type_row['permittee_license_type'] if license_type_row else None
            
            # Get work type breakdown
            cur.execute("""
                SELECT work_type, COUNT(*) as cnt
                FROM permits
                WHERE permittee_license_number = %s AND work_type IS NOT NULL
                GROUP BY work_type
                ORDER BY cnt DESC
                LIMIT 10
            """, (license_number,))
            work_types = [{'work_type': r['work_type'], 'count': r['cnt']} for r in cur.fetchall()]
            
            # Get job type breakdown
            cur.execute("""
                SELECT job_type, COUNT(*) as cnt
                FROM permits
                WHERE permittee_license_number = %s AND job_type IS NOT NULL
                GROUP BY job_type
                ORDER BY cnt DESC
                LIMIT 5
            """, (license_number,))
            job_types = [{'job_type': r['job_type'], 'count': r['cnt']} for r in cur.fetchall()]
            
            # Determine primary specialty based on work types
            specialty = None
            if work_types:
                top_work = work_types[0]['work_type'].lower()
                if 'scaffold' in top_work or 'sidewalk shed' in top_work:
                    specialty = 'Scaffolding & Sidewalk Sheds'
                elif 'plumbing' in top_work:
                    specialty = 'Plumbing'
                elif 'electrical' in top_work:
                    specialty = 'Electrical'
                elif 'sprinkler' in top_work:
                    specialty = 'Fire Protection / Sprinklers'
                elif 'hvac' in top_work or 'mechanical' in top_work:
                    specialty = 'HVAC / Mechanical'
                elif 'demolition' in top_work:
                    specialty = 'Demolition'
                elif 'construction fence' in top_work:
                    specialty = 'Site Safety / Fencing'
                else:
                    specialty = work_types[0]['work_type']
        
        # Map license types to full names
        license_type_names = {
            'GC': 'General Contractor',
            'MP': 'Master Plumber',
            'PE': 'Professional Engineer',
            'RA': 'Registered Architect',
            'ME': 'Master Electrician',
            'FP': 'Fire Protection',
            'SS': 'Site Safety',
            'RG': 'Rigger'
        }
        license_type_full = license_type_names.get(license_type, license_type)
        
        # Try to get additional info from NYC Open Data DOB License Info
        nyc_license_info = None
        try:
            import requests
            # Try with and without leading zeros
            for lic_num in [license_number, license_number.lstrip('0')]:
                nyc_url = f"https://data.cityofnewyork.us/resource/t8hj-ruu2.json?license_number={lic_num}"
                resp = requests.get(nyc_url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    if data:
                        nyc_license_info = data[0]
                        break
        except Exception as e:
            print(f"NYC Open Data license lookup failed: {e}")
        
        # Try NY State ROSA API for PE/RA licenses (not in NYC DOB database)
        nys_license_info = None
        if license_type in ('PE', 'RA') or not nyc_license_info:
            try:
                import requests
                # NY State profession codes: PE=016, RA=003
                profession_codes = {
                    'PE': '016',
                    'RA': '003'
                }
                # Try PE first if we know it's PE, otherwise try both
                codes_to_try = []
                if license_type == 'PE':
                    codes_to_try = ['016']
                elif license_type == 'RA':
                    codes_to_try = ['003']
                else:
                    # Unknown type, try PE then RA
                    codes_to_try = ['016', '003']
                
                nys_api_key = os.getenv('NYS_ROSA_API_KEY', '')
                if nys_api_key:
                    for prof_code in codes_to_try:
                        nys_url = f"https://api.nysed.gov/rosa/V2/byProfessionAndLicenseNumber?licenseNumber={license_number}&professionCode={prof_code}"
                        headers = {'x-oapi-key': nys_api_key}
                        resp = requests.get(nys_url, headers=headers, timeout=5)
                        if resp.status_code == 200:
                            data = resp.json()
                            # Check if valid response (has a name)
                            if data and data.get('name', {}).get('value'):
                                nys_license_info = {
                                    'name': data.get('name', {}).get('value'),
                                    'profession': data.get('profession', {}).get('value'),
                                    'address': data.get('address', {}).get('value'),
                                    'status': data.get('status', {}).get('value'),
                                    'date_of_licensure': data.get('dateOfLicensure', {}).get('value'),
                                    'registered_through': data.get('registeredThroughDate', {}).get('value'),
                                    'license_number': data.get('licenseNumber', {}).get('value'),
                                    'additional_qualifications': data.get('additionalQualifications', {}).get('value'),
                                    'enforcement_actions': len(data.get('enforcementActions', [])) > 0
                                }
                                break
            except Exception as e:
                print(f"NY State ROSA license lookup failed: {e}")
        
        return jsonify({
            'success': True,
            'license_number': license_number,
            'license_type': license_type,
            'license_type_full': license_type_full,
            'applicant_name': applicant_name,
            'contractor_name': contractor_name,
            'total_permits': total_count,
            'unique_buildings': unique_buildings,
            'specialty': specialty,
            'work_types': work_types,
            'job_types': job_types,
            'permits': [dict(p) for p in permits],
            # NYC Open Data enrichment (GC, MP, Electricians, etc.)
            'nyc_license_info': nyc_license_info,
            # NY State ROSA enrichment (PE, RA)
            'nys_license_info': nys_license_info
        })
        
    except Exception as e:
        print(f"License lookup API error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# BUILDING PROFILE - COMPREHENSIVE DATA API
# ============================================================================

@app.route('/api/building-profile/<bbl>')
def api_building_profile(bbl):
    """
    Get complete building intelligence profile with ALL data sources
    Returns everything needed for the social media-style building profile
    """
    try:
        with DatabaseConnection() as cur:
            # Set statement timeout to 30 seconds
            cur.execute("SET statement_timeout = 30000")
            
            # ===== 1. BUILDING CORE DATA (70+ fields from all sources) =====
            cur.execute("""
            SELECT
                id, bbl, bin, address, CAST(borough AS TEXT) as borough, block, lot,
                -- PLUTO data
                current_owner_name, total_units, building_sqft, year_built, year_altered, building_class,
                residential_units, num_floors, lot_sqft, land_use, zip_code,
                zoning_district, built_far, max_resid_far, max_comm_far, unused_far,
                -- RPAD data
                owner_name_rpad, assessed_land_value, assessed_total_value,
                -- HPD data
                owner_name_hpd, hpd_total_violations, hpd_total_complaints,
                hpd_open_violations, hpd_open_complaints, hpd_registration_id,
                hpd_agent_name, hpd_site_manager_name,
                -- Intel signals (step6)
                has_open_mortgage, is_free_and_clear, open_mortgage_count, last_satisfaction_date,
                litigation_count, litigation_open_count, litigation_last_case_type,
                eviction_count, eviction_last_date,
                exemption_count, has_senior_exemption, has_disabled_exemption,
                on_speculation_watch_list, speculation_watch_date,
                dob_complaint_count, dob_active_complaint_count,
                co_count, latest_co_date, latest_co_type,
                fisp_status, fisp_cycle, energy_star_score, site_eui,
                rolling_sale_price, rolling_sale_date, signals_last_enriched,
                -- ACRIS primary deed
                sale_price, sale_date, sale_recorded_date, sale_buyer_primary, sale_seller_primary,
                sale_percent_transferred, sale_crfn,
                -- ACRIS primary mortgage
                mortgage_amount, mortgage_date, mortgage_lender_primary, mortgage_crfn,
                -- Calculated intelligence
                is_cash_purchase, financing_ratio, days_since_sale,
                -- Transaction counts
                acris_total_transactions, acris_deed_count, acris_mortgage_count, acris_satisfaction_count,
                acris_last_enriched,
                -- Tax/Liens data (NEW)
                has_tax_delinquency, tax_delinquency_count, tax_delinquency_water_only,
                ecb_violation_count, ecb_total_balance, ecb_open_violations,
                ecb_total_penalty, ecb_amount_paid, ecb_most_recent_hearing_date, ecb_most_recent_hearing_status,
                ecb_respondent_name, ecb_respondent_address, ecb_respondent_city, ecb_respondent_zip,
                dob_violation_count, dob_open_violations, tax_lien_last_checked,
                -- NY SOS LLC data (Real person behind LLC)
                sos_principal_name, sos_principal_title, sos_principal_street, sos_principal_city,
                sos_principal_state, sos_principal_zip, sos_entity_name, sos_entity_status,
                sos_dos_id, sos_formation_date, sos_last_enriched, sos_lookup_source,
                -- Metadata
                last_updated
            FROM buildings
            WHERE bbl = %s
            """, (bbl,))
            
            building = cur.fetchone()
            
            if not building:
                return jsonify({'success': False, 'error': 'Property not found'}), 404
            
            building_id = building['id']
            
            # ===== 1b. GET ZIP CODE FROM PERMITS =====
            cur.execute("""
                SELECT zip_code FROM permits 
                WHERE bbl = %s AND zip_code IS NOT NULL 
                LIMIT 1
            """, (bbl,))
            zip_result = cur.fetchone()
            property_zip = zip_result['zip_code'] if zip_result else None
            
            # ===== 2. PERMITS (All construction activity) =====
            cur.execute("""
            SELECT
                permit_no, job_type, address, applicant,
                stories, total_units, use_type, issue_date, link,
                    permittee_business_name, permittee_phone, permittee_license_type, permittee_license_number,
                    permittee_first_name, permittee_last_name,
                    owner_business_name, owner_phone, owner_first_name, owner_last_name,
                    superintendent_business_name, superintendent_name,
                    site_safety_mgr_business_name,
                    work_type, permit_status, filing_status,
                    work_description, exp_date, filing_date, proposed_job_start,
                    self_cert, fee_type
                FROM permits
                WHERE bbl = %s
                ORDER BY issue_date DESC
            """, (bbl,))
            permits = cur.fetchall()
        
            # ===== 3. ACRIS TRANSACTIONS (Complete transaction history) =====
            cur.execute("""
                SELECT 
                    document_id, doc_type, doc_amount, doc_date, recorded_date,
                    percent_transferred, crfn, is_primary_deed, is_primary_mortgage
                FROM acris_transactions
                WHERE building_id = %s
                ORDER BY recorded_date DESC
            """, (building_id,))
            transactions = cur.fetchall()
        
            # ===== 4. ACRIS PARTIES (Buyers, Sellers, Lenders with addresses) =====
            cur.execute("""
                SELECT 
                    ap.party_type, ap.party_name,
                    ap.address_1, ap.address_2, ap.city, ap.state, ap.zip_code, ap.country,
                    at.doc_type, at.doc_amount, at.recorded_date, at.document_id
                FROM acris_parties ap
                JOIN acris_transactions at ON ap.transaction_id = at.id
                WHERE at.building_id = %s
                ORDER BY at.recorded_date DESC, ap.party_type
            """, (building_id,))
            parties = cur.fetchall()
        
            # ===== 5. OWNER SOURCES (Deduplicate and organize) =====
            owners = {
                'acris': building['sale_buyer_primary'],
                'pluto': building['current_owner_name'],
                'rpad': building['owner_name_rpad'],
                'hpd': building['owner_name_hpd'],
                'ecb': building['ecb_respondent_name']
            }
            try:
                from enrichment_service import classify_party_name
                owner_classifications = {
                    source: classify_party_name(name)
                    for source, name in owners.items() if name
                }
            except Exception as e:
                print(f"Owner classification failed: {e}")
                owner_classifications = {}
            
            # ===== 5b. SOS DATA (Real person behind LLC) =====
            sos_data = None
            if building['sos_principal_name']:
                sos_data = {
                    'principal_name': building['sos_principal_name'],
                    'principal_title': building['sos_principal_title'],
                    'principal_address': {
                        'street': building['sos_principal_street'],
                        'city': building['sos_principal_city'],
                        'state': building['sos_principal_state'],
                        'zip': building['sos_principal_zip']
                    },
                    'entity_name': building['sos_entity_name'],
                    'entity_status': building['sos_entity_status'],
                    'dos_id': building['sos_dos_id'],
                    'formation_date': building['sos_formation_date'].isoformat() if building['sos_formation_date'] else None,
                    'last_enriched': building['sos_last_enriched'].isoformat() if building['sos_last_enriched'] else None
                }
                try:
                    sos_data.update(classify_party_name(building['sos_principal_name']))
                except Exception:
                    pass

                # Does the registered entity actually correspond to an owner
                # name we hold for this building? Checked here rather than at
                # write time so rows stored before the lookup verified its own
                # match are flagged without waiting for a re-run.
                try:
                    from enrichment_service import entity_match_quality
                    quality, matched_name = entity_match_quality(
                        building['sos_entity_name'],
                        [building['current_owner_name'], building['owner_name_rpad'],
                         building['owner_name_hpd'], building['sale_buyer_primary']],
                    )
                except Exception as e:
                    print(f"SOS entity match check failed: {e}")
                    quality, matched_name = 'unknown', None

                sos_data['entity_match'] = quality
                sos_data['entity_matched_owner'] = matched_name
                sos_data['lookup_source'] = SOS_SOURCE_LABELS.get(
                    building['sos_lookup_source'], building['sos_lookup_source'])
        
            # ===== 6. CALCULATE RISK SCORE =====
            risk_factors = []
            risk_score = 0
        
            # Tax delinquency (30 points)
            if building['has_tax_delinquency']:
                if building['tax_delinquency_water_only']:
                    risk_score += 10
                    risk_factors.append({'factor': 'Water Debt', 'severity': 'low', 'points': 10, 'details': f"{building['tax_delinquency_count']} water delinquency notices"})
                else:
                    risk_score += 30
                    risk_factors.append({'factor': 'Property Tax Delinquency', 'severity': 'high', 'points': 30, 'details': f"{building['tax_delinquency_count']} tax delinquency notices"})
        
            # ECB violations with outstanding balance (40 points max)
            if building['ecb_total_balance'] and building['ecb_total_balance'] > 0:
                if building['ecb_total_balance'] > 100000:
                    points = 40
                    severity = 'critical'
                elif building['ecb_total_balance'] > 50000:
                    points = 30
                    severity = 'high'
                elif building['ecb_total_balance'] > 10000:
                    points = 20
                    severity = 'moderate'
                else:
                    points = 10
                    severity = 'low'
                risk_score += points
                risk_factors.append({
                    'factor': 'ECB Outstanding Balance',
                    'severity': severity,
                    'points': points,
                    'details': f"${building['ecb_total_balance']:,.2f} due, {building['ecb_open_violations']} open violations"
                })
        
            # Open DOB violations (15 points)
            if building['dob_open_violations'] and building['dob_open_violations'] > 5:
                points = 15
                risk_score += points
                risk_factors.append({'factor': 'DOB Open Violations', 'severity': 'moderate', 'points': points, 'details': f"{building['dob_open_violations']} open building code violations"})
            elif building['dob_open_violations'] and building['dob_open_violations'] > 0:
                points = 5
                risk_score += points
                risk_factors.append({'factor': 'DOB Open Violations', 'severity': 'low', 'points': points, 'details': f"{building['dob_open_violations']} open building code violations"})
        
            # HPD violations (15 points)
            if building['hpd_total_violations'] and building['hpd_total_violations'] > 10:
                points = 15
                risk_score += points
                risk_factors.append({'factor': 'HPD Violations', 'severity': 'moderate', 'points': points, 'details': f"{building['hpd_total_violations']} housing violations"})
            elif building['hpd_total_violations'] and building['hpd_total_violations'] > 0:
                points = 5
                risk_score += points
                risk_factors.append({'factor': 'HPD Violations', 'severity': 'low', 'points': points, 'details': f"{building['hpd_total_violations']} housing violations"})
        
            # Determine risk level
            if risk_score >= 60:
                risk_level = 'critical'
                risk_label = 'CRITICAL RISK'
                risk_color = 'red'
            elif risk_score >= 40:
                risk_level = 'high'
                risk_label = 'HIGH RISK'
                risk_color = 'red'
            elif risk_score >= 20:
                risk_level = 'moderate'
                risk_label = 'MODERATE RISK'
                risk_color = 'yellow'
            elif risk_score > 0:
                risk_level = 'low'
                risk_label = 'LOW RISK'
                risk_color = 'yellow'
            else:
                risk_level = 'minimal'
                risk_label = 'MINIMAL RISK'
                risk_color = 'green'
        
            # ===== 7. BUILDING CLASS TRANSLATION =====
            building_class_desc = translate_building_class(building['building_class'])
        
            # ===== 8. ACTIVITY TIMELINE (Combine all events) =====
            activity_timeline = []
        
            # Add permits to timeline
            for permit in permits:
                if permit['issue_date']:
                    activity_timeline.append({
                        'date': permit['issue_date'],
                        'type': 'permit',
                        'icon': '🔨',
                        'title': f"{permit['job_type']} Permit Filed",
                        'description': f"{permit['work_type'] or 'Work'} - {permit['applicant']}",
                        'permit_no': permit['permit_no']
                    })
        
            # Add transactions to timeline
            for txn in transactions:
                if txn['recorded_date']:
                    icon = '🏠' if txn['doc_type'] in ['DEED', 'DEEDO'] else '🏦' if txn['doc_type'] in ['MTGE', 'AGMT'] else '✅' if txn['doc_type'] in ['SAT', 'SATF'] else '📄'
                    activity_timeline.append({
                        'date': txn['recorded_date'],
                        'type': 'transaction',
                        'document_type': txn['doc_type'],
                        'icon': icon,
                        'title': f"{txn['doc_type']} - ${txn['doc_amount']:,.0f}" if txn['doc_amount'] else txn['doc_type'],
                        'description': f"Document ID: {txn['document_id']}",
                        'crfn': txn['crfn']
                    })
        
            # Sort timeline by date descending
            activity_timeline.sort(key=lambda x: x['date'], reverse=True)
        
            # ===== 9. CONTACT AGGREGATION =====
            # The canonical directory contains current permit people, recovered
            # historical observations, source labels, and validation state.
            contacts = _fetch_contact_directory(cur, bbl=bbl)

            # Retain permit-specific license metadata that is not part of the
            # phone identity model. The merge below only adds a missing person.
            # properties-list card shows the latest permit's contact without
            # requiring a phone; this section must never show fewer people
            # than the card does. One entry per (name, role), permits tallied,
            # a phone kept from whichever permit carries one.
            permit_people = {}

            def _note_person(name, role, permit, phone=None, license_type=None,
                             license_number=None):
                clean = (name or '').strip()
                if not clean or clean.upper() in ('N/A', 'NA', 'NONE', '-'):
                    return
                key = (clean.upper(), role)
                entry = permit_people.get(key)
                if entry is None:
                    entry = permit_people[key] = {
                        'name': clean,
                        'phone': None,
                        'role': role,
                        'license': None,
                        'license_number': None,
                        'permit_count': 0,
                    }
                entry['permit_count'] += 1
                if phone and not entry['phone']:
                    entry['phone'] = phone
                if license_type and not entry['license']:
                    entry['license'] = license_type
                if license_number and not entry['license_number']:
                    entry['license_number'] = license_number

            for permit in permits:
                permittee = (permit['permittee_business_name'] or
                             f"{permit.get('permittee_first_name') or ''} "
                             f"{permit.get('permittee_last_name') or ''}".strip())
                _note_person(permittee, 'Contractor/Permittee', permit,
                             phone=permit['permittee_phone'],
                             license_type=permit['permittee_license_type'],
                             license_number=permit.get('permittee_license_number'))

                # The applicant is often the individual behind the permittee
                # business — list them when they aren't the same name.
                applicant = (permit.get('applicant') or '').strip()
                if applicant and applicant.upper() != (permittee or '').upper():
                    _note_person(applicant, 'Applicant', permit)

                owner = (permit['owner_business_name'] or
                         f"{permit.get('owner_first_name') or ''} "
                         f"{permit.get('owner_last_name') or ''}".strip())
                _note_person(owner, 'Property Owner (permit)', permit,
                             phone=permit['owner_phone'])

                _note_person(permit.get('superintendent_business_name') or
                             permit.get('superintendent_name'),
                             'Superintendent', permit)
                _note_person(permit.get('site_safety_mgr_business_name'),
                             'Site Safety Manager', permit)

            # Phones first, then by how much work the name shows up on.
            already_listed = {c['name'].upper() for c in contacts}
            for entry in sorted(permit_people.values(),
                                key=lambda e: (e['phone'] is None,
                                               -e['permit_count'])):
                if entry['name'].upper() not in already_listed:
                    contacts.append(entry)
            
            # Map borough number to name
            borough_names = {
                '1': 'Manhattan',
                '2': 'Bronx',
                '3': 'Brooklyn',
                '4': 'Queens',
                '5': 'Staten Island'
            }
            borough_name = borough_names.get(str(building['borough']), building['borough'])
            
            # Create enhanced building dict with full address info
            building_dict = dict(building)
            # The buildings row's own zip (from PLUTO) wins; the permits scan
            # is only a fallback for rows PLUTO hasn't filled yet.
            building_dict['zip_code'] = building_dict.get('zip_code') or property_zip
            building_dict['borough_name'] = borough_name
            # ACRIS's grantee on the latest deed is the strongest title
            # assertion we hold. PLUTO and HPD remain visible as corroborating
            # sources; RPAD is historical (the published dataset ends in
            # FY2018/19) and is only a last-resort fallback.
            owner_candidates = [
                ('ACRIS latest deed grantee', building['sale_buyer_primary']),
                ('NYC PLUTO', building['current_owner_name']),
                ('HPD registration', building['owner_name_hpd']),
                ('Historical RPAD assessment', building['owner_name_rpad']),
            ]
            owner_source, resolved_owner = next(
                ((source, name) for source, name in owner_candidates if name),
                (None, None),
            )
            building_dict['resolved_owner_name'] = resolved_owner
            building_dict['resolved_owner_source'] = owner_source
            
            # ===== ENRICHMENT DATA (include to speed up button load) =====
            enrichment_info = {'available_owners': [], 'enriched_owners': [], 'already_enriched': False, 'enrichment_data': None, 'cost': 0.50, 'batch_cost': 0.35, 'logged_in': False}
            try:
                from enrichment_service import (
                    parse_owner_name, classify_party_name, split_candidate_names,
                    check_user_enrichment_access, get_available_owners_for_enrichment,
                    is_sos_agent_title,
                )
                from auth_service import validate_session
                
                # Check if user is logged in (try to get user from session without requiring it)
                session_token = session.get('session_token')
                current_user = validate_session(session_token) if session_token else None
                
                # Use the enrichment service function which handles owner tracking properly
                if current_user:
                    enrichment_info['logged_in'] = True
                    is_billable = current_user.get(
                        'should_charge_usage', not current_user.get('is_admin'))
                    enrichment_info['cost'] = 0.50 if is_billable else 0
                    enrichment_info['batch_cost'] = 0.35 if is_billable else 0
                    enrichment_info['billing_email'] = current_user.get('billing_email')
                    enrichment_info['is_sponsored'] = current_user.get('is_sponsored', False)
                    
                    # Get available owners with enrichment status
                    available_owners = get_available_owners_for_enrichment(building_id, current_user['id'])
                    
                    # Separate enriched vs available
                    enriched_owners = [o for o in available_owners if o.get('already_enriched')]
                    not_enriched_owners = [o for o in available_owners if not o.get('already_enriched')]
                    
                    enrichment_info['available_owners'] = not_enriched_owners
                    enrichment_info['enriched_owners'] = enriched_owners
                    
                    # Check if user has any enrichment access
                    has_access, enrichment_data_list, enriched_names = check_user_enrichment_access(current_user['id'], building_id)
                    enrichment_info['already_enriched'] = has_access
                    # enrichment_data_list is now a list of {owner_name, phones, emails} per owner
                    enrichment_info['enrichment_data_per_owner'] = enrichment_data_list if enrichment_data_list else []
                    # Combined view, kept for older clients. Every contact
                    # carries the name it was looked up under: this list mixes
                    # people — an agent's number can sit next to an owner's —
                    # and an unlabelled block gives no way to tell which is
                    # which before you dial.
                    if enrichment_data_list:
                        all_phones = []
                        all_emails = []
                        for ed in enrichment_data_list:
                            owner = ed.get('owner_name')
                            for phone in ed.get('phones', []):
                                all_phones.append({**phone, 'owner_name': owner})
                            for email in ed.get('emails', []):
                                all_emails.append({**email, 'owner_name': owner})
                        enrichment_info['enrichment_data'] = {
                            'phones': all_phones,
                            'emails': all_emails
                        }
                    else:
                        enrichment_info['enrichment_data'] = None
                    enrichment_info['enriched_owner_names'] = enriched_names
                else:
                    # Not logged in - just build owner list without enrichment status
                    available_owners = []
                    
                    # SOS Principal is recommended (real person behind LLC)
                    if building['sos_principal_name']:
                        sos_classification = classify_party_name(
                            building['sos_principal_name'])
                        first, middle, last = parse_owner_name(building['sos_principal_name'])
                        sos_is_related = (not sos_data or
                                          sos_data.get('entity_match') != 'mismatch')
                        if (first and last and sos_is_related and
                                not is_sos_agent_title(building['sos_principal_title'])):
                            available_owners.append({
                                'name': building['sos_principal_name'],
                                'source': 'NY Secretary of State',
                                **sos_classification,
                                'recommended': True,
                                'reason': 'Real person behind LLC',
                                'already_enriched': False
                            })
                    
                    # Check other owner sources
                    owner_sources = [
                        ('sale_buyer_primary', 'ACRIS Latest Deed Grantee'),
                        ('current_owner_name', 'NYC PLUTO Database'),
                        ('owner_name_hpd', 'HPD Registration'),
                        ('owner_name_rpad', 'Historical Tax Records (RPAD)'),
                    ]
                    
                    for field, source in owner_sources:
                        for name in split_candidate_names(building_dict.get(field)):
                            classification = classify_party_name(name)
                            first, middle, last = parse_owner_name(name)
                            if first and last:
                                if not any(o['name'].upper() == name.upper() for o in available_owners):
                                    available_owners.append({
                                        'name': name,
                                        'source': source,
                                        **classification,
                                        'recommended': False,
                                        'already_enriched': False
                                    })
                    
                    enrichment_info['available_owners'] = available_owners
                    
            except Exception as e:
                print(f"Error getting enrichment info: {e}")
                import traceback
                traceback.print_exc()
        
            # Keep all ACRIS parties available to the Transactions section,
            # but annotate which rows actually establish ownership. Numeric
            # party types and labels are instrument-specific: a bank assigning
            # a mortgage is not a previous property owner.
            try:
                from enrichment_service import classify_party_name
                from socrata_client import is_ownership_party
                parties_payload = []
                for party in parties:
                    item = dict(party)
                    item.update(classify_party_name(item.get('party_name')))
                    item['is_ownership_party'] = is_ownership_party(
                        item.get('doc_type'), item.get('party_type'))
                    parties_payload.append(item)
            except Exception as e:
                print(f"ACRIS party annotation failed: {e}")
                parties_payload = [dict(p) for p in parties]

            return jsonify({
                'success': True,
                'building': building_dict,
                'building_class_description': building_class_desc,
                'owners': owners,
                'owner_classifications': owner_classifications,
                'sos_data': sos_data,
                'enrichment': enrichment_info,
                'risk_assessment': {
                    'score': risk_score,
                    'level': risk_level,
                    'label': risk_label,
                    'color': risk_color,
                    'factors': risk_factors
                },
                'permits': [dict(p) for p in permits],
                'transactions': [dict(t) for t in transactions],
                'parties': parties_payload,
                'activity_timeline': activity_timeline[:50],  # Last 50 events
                'contacts': contacts,
                'stats': {
                    'total_permits': len(permits),
                    'total_transactions': len(transactions),
                    'total_contacts': len(contacts),
                    'years_owned': round(building['days_since_sale'] / 365, 1) if building['days_since_sale'] else None
                }
            })
        
    except Exception as e:
        print(f"Building profile API error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# OWNER ENRICHMENT API ENDPOINTS
# ============================================================================

@app.route('/api/enrichment/available-owners/<int:building_id>')
@login_required
def api_available_owners(building_id):
    """
    Get list of owner names available for enrichment on a building
    Returns owners that are actual people (not LLCs) with recommendation
    Marks which owners have already been enriched by this user
    """
    try:
        from enrichment_service import get_available_owners_for_enrichment, check_user_enrichment_access
        
        # Get available owners with enrichment status for this user
        owners = get_available_owners_for_enrichment(building_id, g.user['id'])
        
        # Separate enriched vs available
        enriched_owners = [o for o in owners if o.get('already_enriched')]
        available_owners = [o for o in owners if not o.get('already_enriched')]
        
        # Check if user already has access to enriched data
        has_access, enrichment_data_list, enriched_names = check_user_enrichment_access(g.user['id'], building_id)
        
        # Build combined enrichment data for backward compatibility
        combined_data = None
        if enrichment_data_list:
            all_phones = []
            all_emails = []
            for ed in enrichment_data_list:
                all_phones.extend(ed.get('phones', []))
                all_emails.extend(ed.get('emails', []))
            combined_data = {'phones': all_phones, 'emails': all_emails}
        
        return jsonify({
            'success': True,
            'owners': available_owners,
            'enriched_owners': enriched_owners,
            'already_enriched': has_access,
            'enrichment_data': combined_data,
            'enrichment_data_per_owner': enrichment_data_list if enrichment_data_list else [],
            'cost': 0.50 if g.user.get('should_charge_usage', not g.user.get('is_admin')) else 0,
            'batch_cost': 0.35 if g.user.get('should_charge_usage', not g.user.get('is_admin')) else 0,
            'billing_email': g.user.get('billing_email'),
            'is_sponsored': g.user.get('is_sponsored', False)
        })
        
    except Exception as e:
        print(f"Available owners API error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/enrichment/enrich', methods=['POST'])
@login_required
def api_enrich_owner():
    """
    Enrich owner contact information
    Charges $0.35 and returns phone/email data
    
    POST body: {
        building_id: int,
        owner_name: string
    }
    """
    try:
        from enrichment_service import (
            enrich_owner, check_user_enrichment_access,
            classify_party_name, canonical_name_key, names_compatible,
            get_available_owners_for_enrichment,
        )
        from stripe_service import charge_enrichment_fee, ensure_usage_billing_ready
        
        data = request.get_json() or {}
        
        building_id = data.get('building_id')
        owner_name = data.get('owner_name')
        # Backward-compatible fallback only. enrich_owner resolves the
        # authoritative street/borough/ZIP from building_id.
        address = data.get('address', '')
        
        if not building_id or not owner_name:
            return jsonify({'success': False, 'error': 'Building ID and owner name required'}), 400

        classification = classify_party_name(owner_name)
        if not classification['is_person']:
            return jsonify({
                'success': False,
                'error': ('Contact enrichment is limited to confident human names; '
                          f"this entry is classified as {classification['entity_kind']}.")
            }), 400
        
        user_id = g.user['id']
        is_admin = g.user.get('is_admin', False)
        should_charge = g.user.get('should_charge_usage', not is_admin)
        
        # Check if user already has access for THIS SPECIFIC OWNER
        has_access, existing_data_list, enriched_names = check_user_enrichment_access(user_id, building_id, owner_name)
        if has_access and existing_data_list:
            # Find the data for this specific owner
            owner_data = next((d for d in existing_data_list if d.get('owner_name') == owner_name), existing_data_list[0] if existing_data_list else None)
            return jsonify({
                'success': True,
                'data': owner_data,
                'charged': False,
                'message': f'You already enriched {owner_name}'
            })

        if should_charge:
            billing_ready, billing_message, _billing = ensure_usage_billing_ready(user_id)
            if not billing_ready:
                return jsonify({'success': False, 'error': billing_message}), 402

        # Do not let a crafted request spend money looking up an unrelated
        # person. The name must still be one of this building's current,
        # human-only candidates after source/agent/entity checks.
        requested_key = canonical_name_key(owner_name)
        related_people = get_available_owners_for_enrichment(building_id, user_id)
        if not requested_key or not any(
                names_compatible(requested_key, canonical_name_key(person['name']))
                for person in related_people):
            return jsonify({
                'success': False,
                'error': 'This person is not a verified owner candidate for the property.'
            }), 400
        
        # Perform enrichment FIRST (before charging)
        success, data, message = enrich_owner(building_id, owner_name, address, user_id)
        print(f"Enrichment result: success={success}, message={message}")
        
        if success:
            # Only charge AFTER successful enrichment - single lookup = $0.50
            charge_id = 'admin_free'
            charged = False
            if should_charge:
                charge_success, charge_msg, charge_id = charge_enrichment_fee(
                    user_id, building_id, owner_name, is_batch=False,
                    charge_scope='owner_enrichment')
                charged = charge_success
                if not charge_success:
                    from enrichment_service import revoke_owner_enrichment_access
                    revoke_owner_enrichment_access(user_id, building_id, owner_name)
                    return jsonify({
                        'success': False,
                        'error': f'Payment failed: {charge_msg}'
                    }), 402
            
            return jsonify({
                'success': True,
                'data': data,
                'charged': charged,
                'charge_id': charge_id,
                'message': message
            })
        else:
            # No charge if enrichment failed
            return jsonify({'success': False, 'error': message}), 400
            
    except Exception as e:
        print(f"Enrichment API error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/enrichment/history')
@login_required
def api_enrichment_history():
    """Get user's enrichment transaction history"""
    try:
        from stripe_service import get_user_transactions
        
        transactions = get_user_transactions(g.user['id'])
        
        return jsonify({
            'success': True,
            'transactions': transactions
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _resolve_bulk_enrich_target(req_data):
    """Given the POST body for bulk-enrich endpoints, return (building_ids, mode, error).

    Accepts either:
      - {filters: {...}}  -> re-run the property filter server-side; enriches EXACTLY
        the set the user is browsing (not just the current page, not the whole DB).
      - {building_ids: [...]} -> use the explicit list (back-compat / power-user).

    The filters branch is the safety-critical path: we must never enrich anything
    that wouldn't appear in the filtered Properties view.
    """
    filters = req_data.get('filters')
    explicit_ids = req_data.get('building_ids')

    if filters is not None:
        # Synthesize a MultiDict-like wrapper around the filters JSON
        from werkzeug.datastructures import MultiDict
        md = MultiDict()
        for k, v in filters.items():
            if v is None or v == '' or v is False:
                continue
            if isinstance(v, list):
                # borough may arrive as an array
                md.setlist(k, [str(x) for x in v])
            else:
                md[k] = str(v)
        try:
            building_ids = _resolve_filter_building_ids(md, limit=BULK_ENRICH_MAX_PROPERTIES + 1)
        except Exception as e:
            return None, None, f"Failed to resolve filters: {e}"
        if len(building_ids) > BULK_ENRICH_MAX_PROPERTIES:
            return None, None, (
                f"Too many properties match these filters "
                f"({len(building_ids)} > {BULK_ENRICH_MAX_PROPERTIES}). "
                "Narrow your filters and try again."
            )
        return building_ids, 'filters', None

    if isinstance(explicit_ids, list) and explicit_ids:
        # Sanitize: must be ints, capped
        try:
            ids = [int(x) for x in explicit_ids][:BULK_ENRICH_MAX_PROPERTIES]
        except (TypeError, ValueError):
            return None, None, "building_ids must be a list of integers"
        return ids, 'explicit', None

    return None, None, "Must provide either 'filters' or 'building_ids'"


@app.route('/api/enrichment/bulk-estimate', methods=['POST'])
@login_required
def api_bulk_enrichment_estimate():
    """Estimate cost for bulk enrichment.

    POST body:
        {
          "filters": { ...properties.js filter dict... },   # OR
          "building_ids": [int, ...],
          "owner_strategy": "recommended" | "all",          # default: recommended
          "provider": "enformion" | "apify" | "enformion_fallback"  # admin-only override
        }
    """
    try:
        from enrichment_service import (
            estimate_owners_for_buildings,
            VALID_OWNER_STRATEGIES,
            VALID_PROVIDERS,
            DEFAULT_PROVIDER,
            CUSTOMER_COST_PER_LOOKUP,
            CUSTOMER_MIN_CHARGE,
            provider_real_cost_per_lookup,
        )

        data = request.get_json() or {}
        owner_strategy = data.get('owner_strategy', 'recommended')
        if owner_strategy not in VALID_OWNER_STRATEGIES:
            owner_strategy = 'recommended'

        user_id = g.user['id']
        is_admin = g.user.get('is_admin', False)
        should_charge = g.user.get('should_charge_usage', not is_admin)

        # Only admin can pick a non-default provider. Anyone else is locked into
        # the Apify-with-Enformion-fallback default.
        provider = data.get('provider', DEFAULT_PROVIDER)
        if not is_admin or provider not in VALID_PROVIDERS:
            provider = DEFAULT_PROVIDER

        building_ids, _mode, err = _resolve_bulk_enrich_target(data)
        if err:
            return jsonify({'success': False, 'error': err}), 400

        total_owners, properties_with_owners, breakdown, _per_building = (
            estimate_owners_for_buildings(building_ids, user_id, owner_strategy)
        )

        # Customer-facing math (what a regular paying user would owe). Admin
        # gets the same numbers shown for transparency, but is not actually
        # charged.
        customer_max_cost = total_owners * CUSTOMER_COST_PER_LOOKUP
        if total_owners > 0 and customer_max_cost < CUSTOMER_MIN_CHARGE:
            customer_max_cost = CUSTOMER_MIN_CHARGE

        cost_per_lookup = CUSTOMER_COST_PER_LOOKUP if should_charge else 0
        max_cost = customer_max_cost if should_charge else 0

        # Real upstream provider cost (what we pay the vendor). Only surfaced
        # to admins so they can monitor margin.
        provider_unit_cost = provider_real_cost_per_lookup(provider)
        provider_max_cost = total_owners * provider_unit_cost

        response = {
            'success': True,
            'total_owners': total_owners,
            'properties_with_owners': properties_with_owners,
            'total_properties': len(building_ids),
            'cost_per_lookup': cost_per_lookup,
            'max_cost': max_cost,
            'customer_cost_per_lookup': CUSTOMER_COST_PER_LOOKUP,
            'customer_max_cost': customer_max_cost,
            'breakdown': breakdown[:50],  # cap UI payload size
            'breakdown_truncated': len(breakdown) > 50,
            'owner_strategy': owner_strategy,
            'provider': provider,
            'is_admin': is_admin,
            'requires_typed_confirmation': should_charge and max_cost > 500,
            'max_properties_cap': BULK_ENRICH_MAX_PROPERTIES,
        }
        if is_admin:
            response['provider_cost_per_lookup'] = provider_unit_cost
            response['provider_max_cost'] = provider_max_cost
        return jsonify(response)

    except Exception as e:
        print(f"Bulk estimate error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/enrichment/bulk-job/start', methods=['POST'])
@login_required
def api_bulk_enrich_job_start():
    """Kick off a background bulk-enrich job.

    POST body:
        {
          "filters": {...} OR "building_ids": [...],
          "owner_strategy": "recommended" | "all",
          "provider": "enformion" | "apify" | "enformion_fallback",  # admin-only override
          "confirm_typed": "CONFIRM"   # required if estimated cost > $500
        }
    Returns: {success: true, job_id: int, total_properties: int, total_owners_planned: int, ...}
    """
    try:
        import bulk_enrich_service
        from enrichment_service import (
            estimate_owners_for_buildings,
            VALID_OWNER_STRATEGIES,
            VALID_PROVIDERS,
            DEFAULT_PROVIDER,
            CUSTOMER_COST_PER_LOOKUP,
            CUSTOMER_MIN_CHARGE,
            provider_real_cost_per_lookup,
        )

        data = request.get_json() or {}
        owner_strategy = data.get('owner_strategy', 'recommended')
        if owner_strategy not in VALID_OWNER_STRATEGIES:
            owner_strategy = 'recommended'

        user_id = g.user['id']
        is_admin = g.user.get('is_admin', False)
        should_charge = g.user.get('should_charge_usage', not is_admin)

        # Admin-only provider override (matches the estimate endpoint)
        provider = data.get('provider', DEFAULT_PROVIDER)
        if not is_admin or provider not in VALID_PROVIDERS:
            provider = DEFAULT_PROVIDER

        building_ids, _mode, err = _resolve_bulk_enrich_target(data)
        if err:
            return jsonify({'success': False, 'error': err}), 400

        cost_per_lookup = CUSTOMER_COST_PER_LOOKUP if should_charge else 0

        # Re-estimate so we know the planned owner count and gate by confirm-string
        total_owners, _props_with_owners, _breakdown, _per_b = (
            estimate_owners_for_buildings(building_ids, user_id, owner_strategy)
        )
        customer_max_cost = total_owners * CUSTOMER_COST_PER_LOOKUP
        if total_owners > 0 and customer_max_cost < CUSTOMER_MIN_CHARGE:
            customer_max_cost = CUSTOMER_MIN_CHARGE
        max_cost = customer_max_cost if should_charge else 0

        if should_charge and max_cost > 500:
            typed = (data.get('confirm_typed') or '').strip().upper()
            if typed != 'CONFIRM':
                return jsonify({
                    'success': False,
                    'requires_typed_confirmation': True,
                    'error': "Type CONFIRM to authorize charges above $500.",
                    'estimated_max_cost': max_cost,
                }), 400

        if total_owners == 0:
            return jsonify({
                'success': False,
                'error': "No enrichable owners found in the selected properties.",
            }), 400

        if should_charge:
            from stripe_service import ensure_usage_billing_ready
            billing_ready, billing_message, _billing = ensure_usage_billing_ready(user_id)
            if not billing_ready:
                return jsonify({'success': False, 'error': billing_message}), 402

        # Only persist filters if that's how the user requested it
        persisted_filters = data.get('filters') or {}
        if 'building_ids' in data:
            persisted_filters = {'_explicit_ids_count': len(building_ids)}

        job_id = bulk_enrich_service.create_job(
            user_id=user_id,
            filters=persisted_filters,
            building_ids=building_ids,
            total_owners_planned=total_owners,
            estimated_max_cost=max_cost,
            cost_per_lookup=cost_per_lookup,
            is_admin=is_admin,
            billing_user_id=g.user.get('billing_user_id'),
            owner_strategy=owner_strategy,
            provider=provider,
        )

        bulk_enrich_service.start_job_worker(job_id)

        response = {
            'success': True,
            'job_id': job_id,
            'total_properties': len(building_ids),
            'total_owners_planned': total_owners,
            'estimated_max_cost': max_cost,
            'customer_max_cost': customer_max_cost,
            'cost_per_lookup': cost_per_lookup,
            'owner_strategy': owner_strategy,
            'provider': provider,
        }
        if is_admin:
            response['provider_cost_per_lookup'] = provider_real_cost_per_lookup(provider)
            response['provider_max_cost'] = total_owners * provider_real_cost_per_lookup(provider)
        return jsonify(response)

    except Exception as e:
        print(f"Bulk-job start error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# PERMIT CONTACT ENRICHMENT APIs
# ============================================================================

@app.route('/api/enrichment/permit-contacts/<bbl>')
@login_required
def api_get_enrichable_permit_contacts(bbl):
    """
    Get list of permit contacts that can be enriched for a building.
    Shows which are already enriched and whether user has access.
    """
    try:
        from enrichment_service import get_enrichable_permit_contacts, get_enriched_contacts_for_building
        
        user_id = g.user['id']
        
        # Get enrichable contacts from permits
        enrichable = get_enrichable_permit_contacts(bbl, user_id)
        
        # Get already enriched contacts with user access info
        enriched = get_enriched_contacts_for_building(bbl, user_id)
        
        return jsonify({
            'success': True,
            'enrichable_contacts': enrichable,
            'enriched_contacts': enriched,
            'cost_per_enrichment': 0.50,  # Single lookup rate
            'is_admin': g.user.get('is_admin', False)
        })
        
    except Exception as e:
        print(f"Get permit contacts error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/enrichment/permit-contact', methods=['POST'])
@login_required
def api_enrich_permit_contact():
    """
    Enrich a single permit contact (applicant/permittee).
    
    POST body: {
        bbl: string,
        building_id: int (optional),
        permit_id: int (optional),
        contact_name: string,
        contact_type: string ('applicant', 'permittee', 'owner'),
        license_number: string (optional),
        license_type: string (optional),
        original_phone: string (optional)
    }
    
    Returns enriched phone/email data.
    Charges $0.50 per enrichment (or free if already enriched by another user).
    """
    try:
        from enrichment_service import (
            enrich_permit_contact, 
            check_permit_contact_enrichment,
            grant_permit_contact_access,
            classify_party_name,
        )
        from stripe_service import charge_enrichment_fee
        
        data = request.get_json()
        
        bbl = data.get('bbl')
        building_id = data.get('building_id')
        permit_id = data.get('permit_id')
        contact_name = data.get('contact_name')
        contact_type = data.get('contact_type', 'applicant')
        license_number = data.get('license_number')
        license_type = data.get('license_type')
        original_phone = data.get('original_phone')
        
        if not bbl or not contact_name:
            return jsonify({'success': False, 'error': 'BBL and contact_name required'}), 400

        classification = classify_party_name(contact_name)
        if not classification['is_person']:
            return jsonify({
                'success': False,
                'error': ('Contact enrichment is limited to confident human names; '
                          f"this entry is classified as {classification['entity_kind']}.")
            }), 400
        
        user_id = g.user['id']
        is_admin = g.user.get('is_admin', False)
        
        # Check if already enriched and user has access
        already_enriched, existing_data, user_has_access = check_permit_contact_enrichment(
            bbl, contact_name, contact_type, user_id
        )
        
        if already_enriched and user_has_access:
            return jsonify({
                'success': True,
                'data': existing_data,
                'charged': False,
                'message': 'Contact already unlocked'
            })
        
        # Sponsored employees unlock data under their own account while their
        # sponsor remains the payer.
        should_charge = g.user.get('should_charge_usage', not is_admin)
        need_to_charge = should_charge and (not already_enriched or not user_has_access)

        if need_to_charge:
            from stripe_service import ensure_usage_billing_ready
            billing_ready, billing_message, _billing = ensure_usage_billing_ready(user_id)
            if not billing_ready:
                return jsonify({'success': False, 'error': billing_message}), 402
        
        # Perform enrichment (grant_access=True only for admin, regular users get access after charge)
        success, enrichment_data, message = enrich_permit_contact(
            bbl, building_id, permit_id, contact_name, contact_type,
            license_number, license_type, original_phone, user_id,
            grant_access=is_admin  # Admin gets immediate access
        )
        
        if success:
            enrichment_id = enrichment_data.get('id')
            
            if need_to_charge:
                # Get building_id for charge record
                if not building_id:
                    with DatabaseConnection() as cur:
                        cur.execute("SELECT id FROM buildings WHERE bbl = %s", (bbl,))
                        result = cur.fetchone()
                        building_id = result['id'] if result else None
                
                # Charge FIRST
                charge_success, charge_msg, charge_id = charge_enrichment_fee(
                    user_id, building_id or 0, contact_name, is_batch=False,
                    charge_scope='permit_contact'
                )
                
                if charge_success:
                    # Only grant access AFTER successful charge
                    grant_permit_contact_access(
                        user_id, enrichment_id, 
                        charge_amount=0.50, 
                        stripe_charge_id=charge_id
                    )
                    return jsonify({
                        'success': True,
                        'data': enrichment_data,
                        'charged': True,
                        'charge_id': charge_id,
                        'message': message
                    })
                else:
                    # Charge failed - don't grant access, don't return data
                    print(f"Permit contact enrichment charge failed: {charge_msg}")
                    return jsonify({
                        'success': False, 
                        'error': f'Payment failed: {charge_msg}'
                    }), 402
            else:
                # Admin or no charge needed - already granted access
                return jsonify({
                    'success': True,
                    'data': enrichment_data,
                    'charged': False,
                    'charge_id': 'admin_free' if is_admin else None,
                    'message': message
                })
        else:
            return jsonify({'success': False, 'error': message}), 400
            
    except Exception as e:
        print(f"Permit contact enrichment error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/building/<bbl>/enriched-contacts')
def api_get_building_enriched_contacts(bbl):
    """
    Get all enriched contacts for a building (for Contacts tab).
    Returns enriched permit contacts + owner enrichments.
    Only shows full data for unlocked contacts.
    """
    try:
        from enrichment_service import get_enriched_contacts_for_building
        
        # Get user ID if logged in
        user_id = None
        if hasattr(g, 'user') and g.user:
            user_id = g.user['id']
        
        # Get enriched permit contacts
        enriched_permit_contacts = get_enriched_contacts_for_building(bbl, user_id)
        
        # Also get owner enrichments from buildings table
        owner_enrichments = []
        if user_id:
            with DatabaseConnection() as cur:
                # Get building ID
                cur.execute("SELECT id FROM buildings WHERE bbl = %s", (bbl,))
                building = cur.fetchone()
                
                if building:
                    # Get user's owner enrichments
                    cur.execute("""
                        SELECT owner_name_searched, enriched_phones, enriched_emails, enriched_at
                        FROM user_enrichments
                        WHERE user_id = %s AND building_id = %s
                    """, (user_id, building['id']))
                    
                    for row in cur.fetchall():
                        owner_enrichments.append({
                            'name': row['owner_name_searched'],
                            'type': 'owner',
                            'enriched': True,
                            'has_access': True,
                            'phones': row['enriched_phones'] or [],
                            'emails': row['enriched_emails'] or [],
                            'enriched_at': str(row['enriched_at']) if row['enriched_at'] else None
                        })
        
        return jsonify({
            'success': True,
            'permit_contacts': enriched_permit_contacts,
            'owner_contacts': owner_enrichments,
            'logged_in': user_id is not None
        })
        
    except Exception as e:
        print(f"Get building enriched contacts error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/enrichment/bulk-job/<int:job_id>', methods=['GET'])
@login_required
def api_bulk_enrich_job_status(job_id):
    """Poll a bulk-enrich job's status."""
    try:
        import bulk_enrich_service
        from enrichment_service import provider_real_cost_per_lookup
        user_id = g.user['id']
        is_admin = g.user.get('is_admin', False)
        job = bulk_enrich_service.get_job(job_id, user_id=user_id)
        if not job:
            return jsonify({'success': False, 'error': 'Job not found'}), 404

        # Drop the heavy building_ids array from polling responses
        job.pop('building_ids', None)
        # Format datetimes / Decimals for JSON
        for k, v in list(job.items()):
            if hasattr(v, 'isoformat'):
                job[k] = v.isoformat()
            elif hasattr(v, 'quantize'):
                job[k] = float(v)

        # Show admin the real upstream cost as enrichments progress.
        if is_admin:
            provider = job.get('provider') or 'enformion_fallback'
            unit = provider_real_cost_per_lookup(provider)
            successful = int(job.get('owners_successful') or 0)
            job['provider_cost_per_lookup'] = unit
            job['provider_actual_cost'] = round(successful * unit, 4)

        return jsonify({'success': True, 'job': job})

    except Exception as e:
        print(f"Bulk-job status error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/enrichment/bulk-job/<int:job_id>/cancel', methods=['POST'])
@login_required
def api_bulk_enrich_job_cancel(job_id):
    """Request cancellation of a running bulk-enrich job."""
    try:
        import bulk_enrich_service
        user_id = g.user['id']
        ok = bulk_enrich_service.request_cancel(job_id, user_id)
        if not ok:
            return jsonify({'success': False, 'error': 'Job not found or already finished'}), 404
        return jsonify({'success': True, 'message': 'Cancellation requested'})
    except Exception as e:
        print(f"Bulk-job cancel error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/enrichment/bulk-jobs', methods=['GET'])
@login_required
def api_bulk_enrich_jobs_list():
    """List the current user's recent bulk-enrich jobs (so the page can show
    'a job is still running' on reload)."""
    try:
        import bulk_enrich_service
        from enrichment_service import provider_real_cost_per_lookup
        user_id = g.user['id']
        is_admin = g.user.get('is_admin', False)
        jobs = bulk_enrich_service.list_recent_jobs_for_user(user_id, limit=5)
        for job in jobs:
            for k, v in list(job.items()):
                if hasattr(v, 'isoformat'):
                    job[k] = v.isoformat()
                elif hasattr(v, 'quantize'):
                    job[k] = float(v)
            # Admin: surface real per-lookup vendor cost so the resume modal can
            # render the live cost row without an extra round-trip.
            if is_admin:
                provider = job.get('provider') or 'enformion_fallback'
                job['provider_cost_per_lookup'] = provider_real_cost_per_lookup(provider)
        return jsonify({'success': True, 'jobs': jobs})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


BUILDING_CLASS_CODES = {
    # Residential
    'A0': 'Cape Cod style single-family home',
    'A1': 'Two-story detached single-family home',
    'A2': 'One-story ranch or bungalow',
    'A3': 'Large single-family mansion',
    'A4': 'Single-family home in city',
    'A5': 'Single-family attached or semi-detached',
    'A6': 'Summer cottage or bungalow',
    'A7': 'Mansion-type or town house',
    'A8': 'Bungalow colony (multiple cottages)',
    'A9': 'Miscellaneous single-family',
    'B1': 'Two-family brick or stone building',
    'B2': 'Two-family frame construction',
    'B3': 'Two-family converted from single-family',
    'B9': 'Miscellaneous two-family',
    'C0': 'Three-family brick or stone',
    'C1': 'Walk-up apartment (3-6 families) over stores',
    'C2': 'Walk-up apartment (3-6 families) no stores',
    'C3': 'Walk-up apartment converted from house',
    'C4': 'Renovated walk-up apartment',
    'C5': 'Converted dwelling to apartments',
    'C6': 'Walk-up cooperative or condo',
    'C7': 'Walk-up apartment with commercial',
    'C8': 'Walk-up cooperative or condo conversion',
    'C9': 'Garden-type apartment complex (1-2 stories)',
    'D0': 'Elevator apartment (7+ stories)',
    'D1': 'Semi-fireproof elevator apartment',
    'D2': 'Fireproof elevator apartment (artists in residence)',
    'D3': 'Fireproof elevator apartment',
    'D4': 'Elevator cooperative or condo',
    'D5': 'Elevator apartment converted',
    'D6': 'Elevator cooperative or condo conversion',
    'D7': 'Elevator apartment with stores',
    'D8': 'Elevator apartment (luxury)',
    'D9': 'Elevator apartment miscellaneous',
    # Commercial
    'E1': 'Warehouse (brick/concrete)',
    'E2': 'Warehouse (metal)',
    'E3': 'Warehouse (converted factory)',
    'E4': 'Warehouse (self-storage)',
    'E7': 'Warehouse (commercial storage)',
    'E9': 'Warehouse miscellaneous',
    'F1': 'Factory/industrial (heavy manufacturing)',
    'F2': 'Factory/industrial (artist loft)',
    'F4': 'Factory/industrial (light manufacturing)',
    'F5': 'Factory/industrial (metalworking)',
    'F8': 'Factory/industrial (commercial/printing)',
    'F9': 'Factory/industrial miscellaneous',
    'G0': 'Garage (residential, <4 cars)',
    'G1': 'Garage (all parking garages)',
    'G2': 'Garage (permitted parking lot)',
    'G3': 'Gas station with convenience store',
    'G4': 'Gas station only',
    'G5': 'Garage (commercial vehicles)',
    'G6': 'Licensed parking lot',
    'G7': 'Unlicensed parking lot',
    'G8': 'Marina/boat storage',
    'G9': 'Garage/parking miscellaneous',
    'H1': 'Hotel (luxury)',
    'H2': 'Hotel (full service)',
    'H3': 'Hotel (limited service)',
    'H4': 'Hotel (motel)',
    'H5': 'Hotel (apartment hotel)',
    'H6': 'Hotel (boutique/bed & breakfast)',
    'H7': 'Hotel (SRO - single room occupancy)',
    'H8': 'Hotel (dormitory)',
    'H9': 'Hotel miscellaneous',
    'I1': 'Hospital (general care)',
    'I2': 'Hospital (infirmary)',
    'I3': 'Hospital (mental health)',
    'I4': 'Hospital (special hospital)',
    'I5': 'Clinic/medical office',
    'I6': 'Nursing home',
    'I7': 'Adult care facility',
    'I9': 'Hospital/health facility miscellaneous',
    'J1': 'Theater (live performance)',
    'J2': 'Theater (movie)',
    'J3': 'Theater (photography/TV studio)',
    'J4': 'Theater (arts/dance studio)',
    'J5': 'Theater (bowling alley)',
    'J6': 'Theater (indoor sports arena)',
    'J7': 'Theater (athletic club)',
    'J8': 'Theater (swimming pool)',
    'J9': 'Theater/recreation miscellaneous',
    'K1': 'Store building (one story retail)',
    'K2': 'Store building (multi-story retail)',
    'K3': 'Store building (multi-story department store)',
    'K4': 'Store building (bank)',
    'K5': 'Store building (mixed retail/office)',
    'K6': 'Store building (shopping center)',
    'K7': 'Store building (retail building with parking)',
    'K8': 'Store building (convenience store)',
    'K9': 'Store building miscellaneous',
    'L1': 'Loft building (over 8 stories)',
    'L2': 'Loft building (brick/concrete)',
    'L3': 'Loft building (lightweight)',
    'L8': 'Loft building (luxury/artist)',
    'L9': 'Loft building miscellaneous',
    'M1': 'Church/religious facility',
    'M2': 'Mission/religious residence',
    'M3': 'Parsonage/clergy residence',
    'M4': 'Convent/monastery',
    'M9': 'Religious facility miscellaneous',
    'N1': 'Asylum/home for aged',
    'N2': 'Asylum/infirmary',
    'N3': 'Asylum/orphanage',
    'N4': 'Asylum/detention facility',
    'N9': 'Asylum/institution miscellaneous',
    'O1': 'Office building (1 story)',
    'O2': 'Office building (2-6 stories)',
    'O3': 'Office building (7-19 stories)',
    'O4': 'Office building (20+ stories - skyscraper)',
    'O5': 'Office building (mixed-use residential/office)',
    'O6': 'Office building (mixed-use with stores)',
    'O7': 'Professional building (doctors/dentists)',
    'O8': 'Office building (artist studio)',
    'O9': 'Office building miscellaneous',
    'P1': 'Indoor public assembly',
    'P2': 'Outdoor stadiums/arenas',
    'P3': 'Amusement park',
    'P4': 'Beach/pool club',
    'P5': 'Museum',
    'P6': 'Library',
    'P7': 'Funeral home',
    'P8': 'Observatory/landmark',
    'P9': 'Public assembly miscellaneous',
    'Q1': 'Parking lot',
    'Q2': 'Tennis court/pool',
    'Q3': 'Playground',
    'Q4': 'Beach',
    'Q5': 'Golf course',
    'Q6': 'Marina',
    'Q7': 'Race track',
    'Q8': 'Park/recreation area',
    'Q9': 'Recreation miscellaneous',
    'R0': 'Condo common area',
    'R1': 'Condo residential unit',
    'R2': 'Condo residential unit (horizontal)',
    'R3': 'Condo residential unit (conversion)',
    'R4': 'Condo commercial unit',
    'R5': 'Miscellaneous commercial condo',
    'R6': 'Condo garage',
    'R7': 'Condo warehouse',
    'R8': 'Condo office',
    'R9': 'Condo miscellaneous',
    'S0': 'Multiple dwellings (other)',
    'S1': 'Single-family (other)',
    'S2': 'Two-family (other)',
    'S3': 'Three-family (other)',
    'S4': 'Multiple dwelling',
    'S5': 'Mixed residential/commercial',
    'S9': 'Multiple residence miscellaneous',
    'T1': 'Airport',
    'T2': 'Pier/dock',
    'T9': 'Transportation facility miscellaneous',
    'U0': 'Utility company property',
    'U1': 'Gas/steam plant',
    'U2': 'Telephone exchange',
    'U3': 'Electric substation',
    'U4': 'Pumping station',
    'U5': 'Communication tower',
    'U6': 'Water/sewage plant',
    'U7': 'Heating plant',
    'U8': 'Garbage dump',
    'U9': 'Utility miscellaneous',
    'V0': 'Zoning permit/variance',
    'V1': 'Vacant land zoned residential',
    'V2': 'Vacant land zoned commercial',
    'V3': 'Vacant land zoned mixed use',
    'V4': 'Vacant land (police/fire department)',
    'V5': 'Vacant land (school)',
    'V6': 'Vacant land (library)',
    'V7': 'Vacant land (hospital)',
    'V8': 'Vacant land (public authority)',
    'V9': 'Vacant land miscellaneous',
    'W1': 'Educational structure (public school)',
    'W2': 'Educational structure (private school)',
    'W3': 'Educational structure (parochial school)',
    'W4': 'Educational structure (non-profit school)',
    'W5': 'Educational structure (private university)',
    'W6': 'Educational structure (public university)',
    'W7': 'Educational structure (religious seminary)',
    'W8': 'Educational structure (specialized education)',
    'W9': 'Educational structure miscellaneous',
    'Y1': 'Government building (fire/police)',
    'Y2': 'Government building (government office)',
    'Y3': 'Government building (school)',
    'Y4': 'Government building (library)',
    'Y5': 'Government building (park)',
    'Y6': 'Government building (courts)',
    'Y7': 'Government building (military)',
    'Y8': 'Government building (Department of Sanitation)',
    'Y9': 'Government building miscellaneous',
    'Z0': 'Mixed-use building (retail/residential)',
    'Z1': 'Primarily residential, some commercial',
    'Z2': 'Mixed retail/office',
    'Z3': 'Mixed residential/factory',
    'Z4': 'Industrial/warehouse complex',
    'Z5': 'Mixed-use commercial',
    'Z6': 'Mixed-use government/commercial',
    'Z7': 'Mixed-use cultural/commercial',
    'Z8': 'Mixed-use parking/residential',
    'Z9': 'Mixed-use miscellaneous'
}


# Letter-level building-class families, used to offer "all C walk-ups" style
# choices alongside the specific codes in the properties filter.
BUILDING_CLASS_FAMILIES = {
    'A': 'One-family homes',
    'B': 'Two-family homes',
    'C': 'Walk-up apartments',
    'D': 'Elevator apartments',
    'E': 'Warehouses',
    'F': 'Factories & industrial',
    'G': 'Garages & gas stations',
    'H': 'Hotels',
    'I': 'Hospitals & health',
    'J': 'Theatres',
    'K': 'Stores & retail',
    'L': 'Lofts',
    'M': 'Churches & religious',
    'N': 'Asylums & homes',
    'O': 'Office buildings',
    'P': 'Places of public assembly',
    'Q': 'Outdoor recreation & parking',
    'R': 'Condominiums',
    'S': 'Mixed residential/commercial',
    'T': 'Transportation',
    'U': 'Utility',
    'V': 'Vacant land',
    'W': 'Educational',
    'Y': 'Government & municipal',
    'Z': 'Mixed-use & misc',
}


def building_class_options():
    """Option groups for the properties page building-class multi-select.

    Each family is offered as a prefix (picking "C" matches every C code)
    followed by its specific codes, so the filter works at whichever
    granularity the user wants. Matching is a prefix LIKE either way.
    """
    by_letter = {}
    for code, label in BUILDING_CLASS_CODES.items():
        by_letter.setdefault(code[0], []).append({'value': code, 'label': f'{code} — {label}'})

    groups = []
    for letter in sorted(by_letter):
        family = BUILDING_CLASS_FAMILIES.get(letter, f'Class {letter}')
        groups.append({
            'letter': letter,
            'label': f'{letter} — {family}',
            'options': [{'value': letter, 'label': f'All {letter} — {family}'}]
                       + sorted(by_letter[letter], key=lambda o: o['value']),
        })
    return groups


def translate_building_class(code):
    """
    Translate NYC building classification codes to plain English
    Returns: (code, plain english description)
    """
    if not code:
        return "Unknown building type"
    
    # NYC building class codes - https://www1.nyc.gov/assets/finance/jump/hlpbldgcode.html
    return BUILDING_CLASS_CODES.get(code, f"Building code {code}")


# ============================================================================
# ADMIN ACTIVITY LOGS ROUTES (Admin Only)
# ============================================================================

def _admin_csrf_token():
    token = session.get('admin_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['admin_csrf_token'] = token
    return token


def _valid_admin_csrf():
    expected = session.get('admin_csrf_token') or ''
    supplied = request.headers.get('X-CSRF-Token', '')
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))

def admin_required(f):
    """Decorator that requires admin access"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if user is logged in and is admin
        if not hasattr(g, 'user') or not g.user:
            return redirect(url_for('auth.login'))
        if not g.user.get('is_admin'):
            return jsonify({'success': False, 'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated_function


@app.route('/admin/team')
@login_required
@admin_required
def admin_team_page():
    """Manage employee access and the payment method funding their usage."""
    log_activity(
        activity_type=ActivityType.ADMIN_USER_VIEW,
        activity_category=ActivityCategory.ADMIN,
        description='Admin viewed team accounts',
        page_name='Admin Team Accounts'
    )
    return render_template(
        'admin_team.html', user=g.user, active_page='admin_team',
        csrf_token=_admin_csrf_token(),
    )


@app.route('/api/admin/team')
@login_required
@admin_required
def api_admin_team():
    try:
        from team_service import list_sponsored_accounts
        from stripe_service import get_billing_method_summary

        members = list_sponsored_accounts(g.user['id'])
        billing = get_billing_method_summary(g.user['id'])
        active = sum(1 for member in members if member['status'] == 'active')
        pending = sum(1 for member in members if member['status'] == 'pending')
        spend_30d = sum(float(member.get('spend_30d') or 0) for member in members)
        for member in members:
            for field in ('invite_expires_at', 'accepted_at', 'revoked_at',
                          'created_at', 'last_login'):
                if member.get(field):
                    member[field] = member[field].isoformat()
            member['total_spend'] = float(member.get('total_spend') or 0)
            member['spend_30d'] = float(member.get('spend_30d') or 0)

        return jsonify({
            'success': True,
            'sponsor': {'email': g.user['email']},
            'billing': billing,
            'members': members,
            'summary': {
                'active': active,
                'pending': pending,
                'spend_30d': round(spend_30d, 2),
            },
        })
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/admin/team/invitations', methods=['POST'])
@login_required
@admin_required
def api_admin_create_team_invitation():
    if not _valid_admin_csrf():
        return jsonify({'success': False, 'error': 'Invalid request token. Refresh and try again.'}), 403
    try:
        from team_service import create_sponsorship

        data = request.get_json() or {}
        success, message, result = create_sponsorship(
            g.user['id'], data.get('email'), data.get('display_name'), g.user['id'])
        if not success:
            return jsonify({'success': False, 'error': message}), 400
        if result.get('invite_token'):
            result['invite_url'] = url_for(
                'accept_team_invitation', token=result.pop('invite_token'), _external=True)
        if result.get('invite_expires_at'):
            result['invite_expires_at'] = result['invite_expires_at'].isoformat()
        log_activity(
            activity_type=ActivityType.ADMIN_SETTINGS_CHANGE,
            activity_category=ActivityCategory.ADMIN,
            description=f"Admin assigned sponsored access to {result['email']}",
            action_result=result['status'],
            metadata={'sponsorship_id': result['id'], 'member_email': result['email']},
        )
        return jsonify({'success': True, 'message': message, 'account': result})
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/admin/team/<int:sponsorship_id>/regenerate', methods=['POST'])
@login_required
@admin_required
def api_admin_regenerate_team_invitation(sponsorship_id):
    if not _valid_admin_csrf():
        return jsonify({'success': False, 'error': 'Invalid request token. Refresh and try again.'}), 403
    from team_service import regenerate_invitation
    success, message, result = regenerate_invitation(g.user['id'], sponsorship_id)
    if not success:
        return jsonify({'success': False, 'error': message}), 400
    result['invite_url'] = url_for(
        'accept_team_invitation', token=result.pop('invite_token'), _external=True)
    result['invite_expires_at'] = result['invite_expires_at'].isoformat()
    return jsonify({'success': True, 'message': message, 'account': result})


@app.route('/api/admin/team/<int:sponsorship_id>/revoke', methods=['POST'])
@login_required
@admin_required
def api_admin_revoke_team_account(sponsorship_id):
    if not _valid_admin_csrf():
        return jsonify({'success': False, 'error': 'Invalid request token. Refresh and try again.'}), 403
    from team_service import revoke_sponsorship
    success, message, result = revoke_sponsorship(g.user['id'], sponsorship_id)
    if not success:
        return jsonify({'success': False, 'error': message}), 404
    log_activity(
        activity_type=ActivityType.ADMIN_SETTINGS_CHANGE,
        activity_category=ActivityCategory.ADMIN,
        description=f"Admin revoked sponsored access for {result['member_email']}",
        metadata={'sponsorship_id': sponsorship_id, 'member_email': result['member_email']},
    )
    return jsonify({'success': True, 'message': message})


@app.route('/api/admin/team/<int:sponsorship_id>/reactivate', methods=['POST'])
@login_required
@admin_required
def api_admin_reactivate_team_account(sponsorship_id):
    if not _valid_admin_csrf():
        return jsonify({'success': False, 'error': 'Invalid request token. Refresh and try again.'}), 403
    from team_service import reactivate_sponsorship
    success, message, result = reactivate_sponsorship(g.user['id'], sponsorship_id)
    if not success:
        return jsonify({'success': False, 'error': message}), 400
    return jsonify({'success': True, 'message': message, 'account': result})


@app.route('/api/admin/team/billing/setup', methods=['POST'])
@login_required
@admin_required
def api_admin_team_billing_setup():
    if not _valid_admin_csrf():
        return jsonify({'success': False, 'error': 'Invalid request token. Refresh and try again.'}), 403
    try:
        from stripe_service import create_payment_method_setup_session
        checkout = create_payment_method_setup_session(
            g.user['id'], g.user['email'],
            success_url=url_for(
                'admin_team_billing_complete', _external=True) +
                '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('admin_team_page', _external=True),
        )
        return jsonify({'success': True, 'checkout_url': checkout.url})
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/admin/team/billing/complete')
@login_required
@admin_required
def admin_team_billing_complete():
    session_id = request.args.get('session_id', '')
    try:
        from stripe_service import finalize_payment_method_setup
        finalize_payment_method_setup(session_id, g.user['id'])
        flash('Payment method saved. Employee lookup charges will use this card.', 'success')
    except Exception as exc:
        flash(f'Payment method could not be saved: {exc}', 'error')
    return redirect(url_for('admin_team_page'))


@app.route('/team/setup/<token>', methods=['GET', 'POST'])
def accept_team_invitation(token):
    from team_service import get_invitation, accept_invitation
    invitation = get_invitation(token)
    error = None
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirmation = request.form.get('confirm_password', '')
        if password != confirmation:
            error = 'Passwords do not match.'
        elif invitation:
            success, message, user_id = accept_invitation(token, password)
            if success:
                from auth_service import create_session
                session_token = create_session(
                    user_id, request.remote_addr, request.user_agent.string[:500])
                session['session_token'] = session_token
                session.permanent = True
                flash('Your employee account is ready. Welcome!', 'success')
                return redirect(url_for('index'))
            error = message
    return render_template(
        'team_setup.html', invitation=invitation, error=error,
        token=token,
    ), (200 if invitation else 410)


@app.route('/admin/activity')
@login_required
@admin_required
def admin_activity_page():
    """Admin activity logs page"""
    log_activity(
        activity_type=ActivityType.ADMIN_ACTIVITY_VIEW,
        activity_category=ActivityCategory.ADMIN,
        description='Admin viewed activity logs',
        page_name='Admin Activity Logs'
    )
    return render_template('admin_activity.html', user=g.user)


@app.route('/api/admin/activity-logs')
@login_required
@admin_required
def api_admin_activity_logs():
    """
    API to get activity logs with filtering
    
    Query Parameters:
    - user_id: Filter by specific user
    - user_email: Filter by email (partial match)
    - activity_type: Filter by type (comma-separated for multiple)
    - activity_category: Filter by category (comma-separated for multiple)
    - ip_address: Filter by IP
    - start_date: Start date (YYYY-MM-DD)
    - end_date: End date (YYYY-MM-DD)
    - page_name: Filter by page name
    - search: General search in descriptions
    - success_only: true/false to filter by action success
    - limit: Results per page (default 100, max 500)
    - offset: Pagination offset
    - order_by: Sort column (created_at, user_email, activity_type, etc.)
    - order_dir: ASC or DESC
    """
    try:
        # Parse query parameters
        user_id = request.args.get('user_id', type=int)
        user_email = request.args.get('user_email', '').strip() or None
        activity_type = request.args.get('activity_type', '').strip()
        activity_category = request.args.get('activity_category', '').strip()
        ip_address = request.args.get('ip_address', '').strip() or None
        start_date = request.args.get('start_date', '').strip() or None
        end_date = request.args.get('end_date', '').strip() or None
        page_name = request.args.get('page_name', '').strip() or None
        search = request.args.get('search', '').strip() or None
        success_only = request.args.get('success_only', '').strip()
        limit = min(500, request.args.get('limit', 100, type=int))
        offset = request.args.get('offset', 0, type=int)
        order_by = request.args.get('order_by', 'created_at')
        order_dir = request.args.get('order_dir', 'DESC')
        
        # Parse types and categories (comma-separated)
        activity_types = [t.strip() for t in activity_type.split(',') if t.strip()] if activity_type else None
        activity_categories = [c.strip() for c in activity_category.split(',') if c.strip()] if activity_category else None
        
        # Parse success_only
        if success_only.lower() == 'true':
            success_only_bool = True
        elif success_only.lower() == 'false':
            success_only_bool = False
        else:
            success_only_bool = None
        
        # Get logs
        logs = get_activity_logs(
            user_id=user_id,
            user_email=user_email,
            activity_type=activity_types,
            activity_category=activity_categories,
            ip_address=ip_address,
            start_date=start_date,
            end_date=end_date,
            page_name=page_name,
            search_query=search,
            success_only=success_only_bool,
            limit=limit,
            offset=offset,
            order_by=order_by,
            order_dir=order_dir
        )
        
        # Convert datetime objects to ISO strings for JSON
        for log in logs:
            if log.get('created_at'):
                log['created_at'] = log['created_at'].isoformat()
        
        return jsonify({
            'success': True,
            'logs': logs,
            'count': len(logs),
            'limit': limit,
            'offset': offset
        })
        
    except Exception as e:
        print(f"Admin activity logs error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/activity-stats')
@login_required
@admin_required
def api_admin_activity_stats():
    """
    API to get activity statistics
    
    Query Parameters:
    - start_date: Start date (YYYY-MM-DD)
    - end_date: End date (YYYY-MM-DD)
    - user_id: Filter stats by specific user
    """
    try:
        start_date = request.args.get('start_date', '').strip() or None
        end_date = request.args.get('end_date', '').strip() or None
        user_id = request.args.get('user_id', type=int)
        
        stats = get_activity_stats(
            start_date=start_date,
            end_date=end_date,
            user_id=user_id
        )
        
        return jsonify({
            'success': True,
            'stats': stats
        })
        
    except Exception as e:
        print(f"Admin activity stats error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/activity/track', methods=['POST'])
def api_activity_track():
    """
    Receive client-side tracking events.
    Used by the activity_tracker.js to log user interactions.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
        
        events = data.get('events', [])
        
        for event in events:
            event_type = event.get('type', 'unknown')
            event_data = event.get('data', {})
            
            # Map client event types to activity types
            type_mapping = {
                'click': ActivityType.BUTTON_CLICK,
                'section_view': ActivityType.SECTION_CLICK,
                'tab_switch': ActivityType.TAB_SWITCH,
                'filter_change': ActivityType.FILTER_CHANGE,
                'form_submit': ActivityType.FORM_SUBMIT,
                'search': ActivityType.SEARCH,
                'export': ActivityType.EXPORT,
                'data_view': ActivityType.DATA_VIEW,
                'error': ActivityType.ERROR
            }
            
            activity_type = type_mapping.get(event_type, 'client_event')
            
            log_activity(
                activity_type=activity_type,
                activity_category=ActivityCategory.INTERACTION,
                description=f"Client event: {event_type}",
                page_url=event.get('page_url'),
                element_id=event_data.get('element_id'),
                element_type=event_data.get('element_type'),
                element_text=event_data.get('element_text'),
                search_query=event_data.get('query'),
                metadata={
                    'client_event': True,
                    'event_data': event_data,
                    'screen_width': event.get('screen_width'),
                    'screen_height': event.get('screen_height')
                }
            )
        
        return jsonify({'success': True, 'tracked': len(events)})
        
    except Exception as e:
        print(f"Activity tracking error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/recent-logins')
@login_required
@admin_required
def api_admin_recent_logins():
    """Get recent login attempts"""
    try:
        limit = min(100, request.args.get('limit', 50, type=int))
        logins = get_recent_logins(limit=limit)
        
        # Convert datetime objects
        for login in logins:
            if login.get('created_at'):
                login['created_at'] = login['created_at'].isoformat()
        
        return jsonify({
            'success': True,
            'logins': logins,
            'count': len(logins)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/recent-errors')
@login_required
@admin_required
def api_admin_recent_errors():
    """Get recent errors"""
    try:
        limit = min(100, request.args.get('limit', 50, type=int))
        errors = get_recent_errors(limit=limit)
        
        # Convert datetime objects
        for error in errors:
            if error.get('created_at'):
                error['created_at'] = error['created_at'].isoformat()
        
        return jsonify({
            'success': True,
            'errors': errors,
            'count': len(errors)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/admin/users')
@login_required
@admin_required
def api_admin_users():
    """Get list of all users for filtering"""
    try:
        with DatabaseConnection() as cur:
            cur.execute("""
                SELECT id, email, is_admin, subscription_status, created_at, last_login
                FROM users
                ORDER BY created_at DESC
            """)
            users = cur.fetchall()
            
            # Convert datetime objects
            for user in users:
                if user.get('created_at'):
                    user['created_at'] = user['created_at'].isoformat()
                if user.get('last_login'):
                    user['last_login'] = user['last_login'].isoformat()
            
            return jsonify({
                'success': True,
                'users': [dict(u) for u in users],
                'count': len(users)
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    print("Starting DOB Permit Dashboard API...")
    print(f"Database: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
    port = int(os.getenv('PORT', 5001))
    debug = os.getenv('FLASK_ENV') != 'production'
    print(f"Visit: http://localhost:{port}")
    app.run(debug=debug, host='0.0.0.0', port=port)
