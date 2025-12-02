#!/usr/bin/env python3
"""
Flask API Backend for DOB Permit Dashboard
Serves data from PostgreSQL database to HTML frontend
"""

from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from flask_caching import Cache
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import requests

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for local development

# Simple in-memory cache (can upgrade to Redis later)
cache = Cache(app, config={
    'CACHE_TYPE': 'SimpleCache',  # In-memory cache
    'CACHE_DEFAULT_TIMEOUT': 300  # 5 minutes
})

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': os.getenv('DB_PORT', '5432'),
    'database': os.getenv('DB_NAME', 'permits_db'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', ''),
    'connect_timeout': 10,
    'options': '-c statement_timeout=30000'  # 30 second query timeout
}

# Simple connection pool: 2-10 connections
# Railway free tier supports up to 20 connections, we use max 10 to be safe
db_pool = None

def init_db_pool():
    """Initialize the database connection pool"""
    global db_pool
    if db_pool is None:
        db_pool = pool.SimpleConnectionPool(2, 10, **DB_CONFIG)
    return db_pool


def get_db_connection():
    """Get a connection from the pool"""
    global db_pool
    if db_pool is None:
        init_db_pool()
    return db_pool.getconn()


def return_db_connection(conn):
    """Return a connection to the pool"""
    if conn and db_pool:
        db_pool.putconn(conn)


class DatabaseConnection:
    """Context manager for database connections"""
    def __init__(self):
        self.conn = None
        self.cursor = None
    
    def __enter__(self):
        self.conn = get_db_connection()
        self.cursor = self.conn.cursor(cursor_factory=RealDictCursor)
        return self.cursor
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.cursor:
            self.cursor.close()
        if self.conn:
            if exc_type is not None:
                # Rollback on error
                try:
                    self.conn.rollback()
                except:
                    pass
            return_db_connection(self.conn)
        return False  # Don't suppress exceptions


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
def index():
    """Serve the new homepage"""
    return render_template('home.html')

@app.route('/old-dashboard')
def old_dashboard():
    """Serve the old dashboard (for reference)"""
    return render_template('index.html')


@app.route('/api/permits')
def get_permits():
    """Get all permits with calculated scores, contact info, and building intelligence"""
    try:
        with DatabaseConnection() as cur:
            # Query with contact information from permits table and building intelligence
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
                    ) as contact_phones,
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
            
            # Total contacts (count permits with phone numbers)
            cur.execute("""
                SELECT COUNT(*) as total FROM permits 
                WHERE permittee_phone IS NOT NULL OR owner_phone IS NOT NULL;
            """)
            total_contacts = cur.fetchone()['total']
            
            # Mobile contacts (deprecated - always 0 since we don't track mobile vs landline)
            mobile_contacts = 0
            
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
    """Search for permits by contact name or phone (searches permits table columns)"""
    query = request.args.get('q', '').strip()
    
    if len(query) < 2:
        return jsonify({
            'success': False,
            'error': 'Query must be at least 2 characters'
        }), 400
    
    try:
        with DatabaseConnection() as cur:
            # Search permits table directly - all contacts now stored in permit columns
            search_query = """
                SELECT DISTINCT
                    p.id,
                    p.permit_no,
                    p.address,
                    p.job_type,
                    p.issue_date,
                    COALESCE(p.permittee_business_name, p.owner_business_name, p.superintendent_business_name, p.applicant) as contact_name,
                    COALESCE(p.permittee_phone, p.owner_phone) as contact_phone
                FROM permits p
                WHERE 
                    (
                        LOWER(p.permittee_business_name) LIKE %s OR
                        LOWER(p.owner_business_name) LIKE %s OR
                        LOWER(p.superintendent_business_name) LIKE %s OR
                        LOWER(p.site_safety_mgr_business_name) LIKE %s OR
                        LOWER(p.applicant) LIKE %s OR
                        p.permittee_phone LIKE %s OR
                        p.owner_phone LIKE %s
                    )
                ORDER BY p.issue_date DESC
                LIMIT 50;
            """
            
            search_pattern = f'%{query.lower()}%'
            # Need to pass search_pattern 7 times for the 7 fields being searched
            cur.execute(search_query, (search_pattern, search_pattern, search_pattern, search_pattern, search_pattern, search_pattern, search_pattern))
            
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
            # Get permit details
            cur.execute("SELECT * FROM permits WHERE permit_id = %s;", (permit_id,))
            permit = cur.fetchone()
            
            if not permit:
                return jsonify({
                    'success': False,
                    'error': 'Permit not found'
                }), 404
            
            # Build contacts array from permits table columns
            contacts = []
            if permit.get('permittee_business_name') or permit.get('applicant'):
                contacts.append({
                    'name': permit.get('permittee_business_name') or permit.get('applicant'),
                    'phone': permit.get('permittee_phone'),
                    'role': 'Permittee'
            })
        if permit.get('owner_business_name'):
            contacts.append({
                'name': permit.get('owner_business_name'),
                'phone': permit.get('owner_phone'),
                'role': 'Owner'
            })
        if permit.get('superintendent_business_name'):
            contacts.append({
                'name': permit.get('superintendent_business_name'),
                'phone': None,
                'role': 'Superintendent'
            })
        if permit.get('site_safety_mgr_business_name'):
            contacts.append({
                'name': permit.get('site_safety_mgr_business_name'),
                'phone': None,
                'role': 'Site Safety Manager'
            })
        
        permit['contacts'] = contacts
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
    try:
        with DatabaseConnection() as cur:
            cur.execute("SELECT 1;")
        
        return jsonify({
            'success': True,
            'status': 'healthy',
            'database': 'connected'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'status': 'unhealthy',
            'database': 'disconnected',
            'error': str(e)
        }), 500


@app.route('/permit/<int:permit_id>')
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
        
        # Build contacts array from permits table columns
        contacts = []
        if permit.get('permittee_business_name') or permit.get('applicant'):
            contacts.append({
                'name': permit.get('permittee_business_name') or permit.get('applicant'),
                'phone': permit.get('permittee_phone'),
                'is_mobile': False
            })
        if permit.get('owner_business_name'):
            contacts.append({
                'name': permit.get('owner_business_name'),
                'phone': permit.get('owner_phone'),
                'is_mobile': False
            })
        if permit.get('superintendent_business_name'):
            contacts.append({
                'name': permit.get('superintendent_business_name'),
                'phone': None,
                'is_mobile': False
            })
        if permit.get('site_safety_mgr_business_name'):
            contacts.append({
                'name': permit.get('site_safety_mgr_business_name'),
                'phone': None,
                'is_mobile': False
            })
        
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
                    (
                        CASE WHEN p.permittee_phone IS NOT NULL AND p.permittee_phone != '' THEN 1 ELSE 0 END +
                        CASE WHEN p.owner_phone IS NOT NULL AND p.owner_phone != '' THEN 1 ELSE 0 END
                    ) as contact_count
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
                             related_permits=related_permits)
        
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
                       (
                           CASE WHEN p.permittee_phone IS NOT NULL AND p.permittee_phone != '' THEN 1 ELSE 0 END +
                           CASE WHEN p.owner_phone IS NOT NULL AND p.owner_phone != '' THEN 1 ELSE 0 END
                       ) as contact_count
                FROM permits p
                WHERE p.bbl = %s
                ORDER BY p.issue_date DESC;
            """, (building['bbl'],))
            permits = cur.fetchall()
        
            # Get all contacts from all permits (aggregate from permits table columns)
            cur.execute("""
                SELECT DISTINCT 
                    COALESCE(p.permittee_business_name, p.applicant) as name,
                    p.permittee_phone as phone,
                    'Permittee' as role
                FROM permits p
                WHERE p.bbl = %s AND (p.permittee_business_name IS NOT NULL OR p.applicant IS NOT NULL)
                UNION
                SELECT DISTINCT 
                    p.owner_business_name as name,
                    p.owner_phone as phone,
                    'Owner' as role
                FROM permits p
                WHERE p.bbl = %s AND p.owner_business_name IS NOT NULL
                UNION
                SELECT DISTINCT 
                    p.superintendent_business_name as name,
                    NULL as phone,
                    'Superintendent' as role
                FROM permits p
                WHERE p.bbl = %s AND p.superintendent_business_name IS NOT NULL
                UNION
                SELECT DISTINCT 
                    p.site_safety_mgr_business_name as name,
                    NULL as phone,
                    'Site Safety Manager' as role
                FROM permits p
                WHERE p.bbl = %s AND p.site_safety_mgr_business_name IS NOT NULL
                ORDER BY name;
            """, (building['bbl'], building['bbl'], building['bbl'], building['bbl']))
            contacts = cur.fetchall()
        
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
            
            # Get all unique contacts from all permits for this BBL (from permits table columns)
            cur.execute("""
            SELECT DISTINCT 
                COALESCE(p.permittee_business_name, p.applicant) as name,
                'Permittee' as role,
                p.permittee_phone as phone,
                NULL as phone_type,
                NULL as email,
                p.permit_no as permit_number
            FROM permits p
            WHERE p.bbl = %s AND (p.permittee_business_name IS NOT NULL OR p.applicant IS NOT NULL)
            UNION
            SELECT DISTINCT 
                p.owner_business_name as name,
                'Owner' as role,
                p.owner_phone as phone,
                NULL as phone_type,
                NULL as email,
                p.permit_no as permit_number
            FROM permits p
            WHERE p.bbl = %s AND p.owner_business_name IS NOT NULL
            UNION
            SELECT DISTINCT 
                p.superintendent_business_name as name,
                'Superintendent' as role,
                NULL as phone,
                NULL as phone_type,
                NULL as email,
                p.permit_no as permit_number
            FROM permits p
            WHERE p.bbl = %s AND p.superintendent_business_name IS NOT NULL
            UNION
            SELECT DISTINCT 
                p.site_safety_mgr_business_name as name,
                'Site Safety Manager' as role,
                NULL as phone,
                NULL as phone_type,
                NULL as email,
                p.permit_no as permit_number
            FROM permits p
            WHERE p.bbl = %s AND p.site_safety_mgr_business_name IS NOT NULL
            ORDER BY name, role;
        """, (building['bbl'], building['bbl'], building['bbl'], building['bbl']))
        contacts = cur.fetchall()
        
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
        
        query += " ORDER BY t.doc_date DESC NULLS LAST, t.doc_amount DESC NULLS LAST"
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


@app.route('/construction')
def construction():
    """Construction intelligence page"""
    return render_template('construction.html')


# ==================== CONSTRUCTION INTELLIGENCE APIs ====================

@app.route('/api/construction/permits')
def get_construction_permits():
    """Get filtered permits for construction page with advanced filtering"""
    try:
        with DatabaseConnection() as cur:
            # Get filter parameters
            job_types = request.args.getlist('job_type')
            borough = request.args.get('borough')
            days = request.args.get('days', '30')
            min_lead_score = request.args.get('min_score', 0, type=int)
            has_contact = request.args.get('has_contact', type=str)  # 'true' or 'false'
            sort_by = request.args.get('sort', 'date')  # date, score, contacts, size
            limit = request.args.get('limit', 200, type=int)
            offset = request.args.get('offset', 0, type=int)  # For pagination
            
            # Build dynamic query
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
                    ) as contact_count,
                    -- Building intelligence
                    b.residential_units,
                    b.total_units,
                    b.num_floors,
                    b.building_sqft,
                    b.assessed_total_value,
                    b.purchase_price,
                    b.current_owner_name
                FROM permits p
                LEFT JOIN buildings b ON p.bbl = b.bbl
                WHERE 1=1
            """
        
            params = []
        
            # Handle time period filter
            if days != 'all':
                days_int = int(days)
                query += " AND p.issue_date >= CURRENT_DATE - INTERVAL '%s days'"
                params.append(days_int)
        
            # Apply filters
            if job_types:
                placeholders = ','.join(['%s'] * len(job_types))
                query += f" AND p.job_type IN ({placeholders})"
                params.extend(job_types)
        
            if borough:
                query += " AND p.borough = %s"
                params.append(borough)
        
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
        
            # Get total count for pagination
            count_query = """
                SELECT COUNT(*) 
                FROM permits p
                LEFT JOIN buildings b ON p.bbl = b.bbl
                WHERE 1=1
            """
            count_params = []
        
            # Apply same filters to count query
            if days != 'all':
                days_int = int(days)
                count_query += " AND p.issue_date >= CURRENT_DATE - INTERVAL '%s days'"
                count_params.append(days_int)
        
            if job_types:
                placeholders = ','.join(['%s'] * len(job_types))
                count_query += f" AND p.job_type IN ({placeholders})"
                count_params.extend(job_types)
        
            if borough:
                count_query += " AND p.borough = %s"
                count_params.append(borough)
        
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
@cache.cached(timeout=60, key_prefix='construction_stats')
def get_construction_stats():
    """Get quick stats for construction dashboard"""
    try:
        with DatabaseConnection() as cur:
            days = request.args.get('days', '30')
            
            # Build WHERE clause based on time period
            if days == 'all':
                where_clause = "WHERE 1=1"
                params = ()
            else:
                days_int = int(days)
                where_clause = "WHERE issue_date >= CURRENT_DATE - INTERVAL '%s days'"
                params = (days_int,)
            
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
        if days != 'all':
            days_int = int(days)
            query += " AND p.issue_date >= CURRENT_DATE - INTERVAL '%s days'"
            params.append(days_int)
        
        if job_types:
            placeholders = ','.join(['%s'] * len(job_types))
            query += f" AND p.job_type IN ({placeholders})"
            params.extend(job_types)
        
        if borough:
            query += " AND p.borough = %s"
            params.append(borough)
        
        query += " LIMIT 1000"
        
        cur.execute(query, tuple(params))
        locations = cur.fetchall()
        
        # Add lead scores to map locations
        locations_with_scores = []
        for loc in locations:
            loc_dict = dict(loc)
            loc_dict['lead_score'] = calculate_lead_score(loc)
            locations_with_scores.append(loc_dict)
        
        return jsonify({
            'success': True,
            'locations': locations_with_scores,
            'count': len(locations_with_scores)
        })
        
    except Exception as e:
        print(f"Error fetching map data: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/construction/contractors')
def get_top_contractors():
    """Get top contractors by permit activity"""
    try:
        with DatabaseConnection() as cur:
            days = request.args.get('days', 90, type=int)
            limit_count = request.args.get('limit', 20, type=int)
        
        # Top contractors by permit count
        query = """
            SELECT 
                COALESCE(permittee_business_name, applicant, 'Unknown') as contractor_name,
                COUNT(*) as permit_count,
                STRING_AGG(DISTINCT job_type, ', ') as job_types,
                STRING_AGG(DISTINCT borough, ', ') as boroughs,
                MAX(issue_date) as most_recent
            FROM permits
            WHERE issue_date >= CURRENT_DATE - INTERVAL '%s days'
            AND (permittee_business_name IS NOT NULL OR applicant IS NOT NULL)
            GROUP BY COALESCE(permittee_business_name, applicant, 'Unknown')
            HAVING COUNT(*) > 1
            ORDER BY permit_count DESC
            LIMIT %s
        """
        
        cur.execute(query, (days, limit_count))
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
            days = request.args.get('days', 30, type=int)
            
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
            WHERE p.issue_date >= CURRENT_DATE - INTERVAL '%s days'
        """
        
        params = [days]
        
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
        return output
        
    except Exception as e:
        print(f"Error exporting permits: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/investments')
def investments():
    """Investment opportunities page"""
    return render_template('investments.html')


@app.route('/analytics')
def analytics():
    """Market analytics page"""
    return render_template('analytics.html')


@app.route('/search-results')
def search_results():
    """Search results page"""
    query = request.args.get('q', '')
    return render_template('search_results.html', query=query)


@app.route('/property/<bbl>')
def property_detail(bbl):
    """Comprehensive building intelligence profile page"""
    return render_template('building_profile.html', bbl=bbl)


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


@app.route('/api/search')
def api_search():
    """Enhanced universal search endpoint with match reasons"""
    query = request.args.get('q', '').strip()
    
    if not query:
        return jsonify([])

    # Split into tokens so multi-word queries like "810 sterling" or "haim levy"
    # match records where tokens may appear in any order (not only as an exact phrase).
    tokens = [t.strip() for t in query.split() if t.strip()]
    if not tokens:
        return jsonify([])

    try:
        with DatabaseConnection() as cur:
            # helper to build AND-joined ILIKE clauses (field ILIKE %t1% AND field ILIKE %t2% ...)
            def and_like_clause(field, tok_count):
                return '(' + ' AND '.join([f"{field} ILIKE %s" for _ in range(tok_count)]) + ')'

            # Build clauses and params
            tok_count = len(tokens)
        addr_clause = and_like_clause('b.address', tok_count)
        owner_clause = and_like_clause('b.current_owner_name', tok_count)
        owner_rpad_clause = and_like_clause('b.owner_name_rpad', tok_count)
        owner_hpd_clause = and_like_clause('b.owner_name_hpd', tok_count)

        # bbl match will use full query as a contains
        bbl_clause = 'b.bbl::text LIKE %s'

        # contact / applicant matches will use LOWER(...) so use lowercased tokens
        # Updated to search permits table columns instead of contacts table
        permittee_clause = and_like_clause('LOWER(p.permittee_business_name)', tok_count)
        owner_business_clause = and_like_clause('LOWER(p.owner_business_name)', tok_count)
        applicant_clause = and_like_clause('LOWER(p.applicant)', tok_count)

        # params order must match placeholders in SQL below
        params = []
        # address params
        params += [f"%{t}%" for t in tokens]
        # owner params
        params += [f"%{t}%" for t in tokens]
        # owner_rpad params
        params += [f"%{t}%" for t in tokens]
        # owner_hpd params
        params += [f"%{t}%" for t in tokens]
        # bbl param (use full query)
        params.append(f"%{query}%")
        # permittee params (lowercase)
        params += [f"%{t.lower()}%" for t in tokens]
        # owner_business params (lowercase)
        params += [f"%{t.lower()}%" for t in tokens]
        # applicant params (lowercase)
        params += [f"%{t.lower()}%" for t in tokens]

        # prefix and contains helpers for priority scoring
        prefix_param = f"{query}%"
        contains_param = f"%{query}%"

        sql = f"""
            WITH contact_matches AS (
                SELECT DISTINCT p.bbl,
                    ARRAY_AGG(DISTINCT 'Contact: ' || COALESCE(p.permittee_business_name, p.owner_business_name, p.applicant)) as contact_reasons
                FROM permits p
                WHERE ({permittee_clause} OR {owner_business_clause})
                AND (p.permittee_business_name IS NOT NULL OR p.owner_business_name IS NOT NULL)
                GROUP BY p.bbl
            ),
            applicant_matches AS (
                SELECT DISTINCT p.bbl,
                    ARRAY_AGG(DISTINCT 'Applicant: ' || p.applicant) as applicant_reasons
                FROM permits p
                WHERE {applicant_clause}
                AND p.applicant IS NOT NULL AND p.applicant != ''
                GROUP BY p.bbl
            ),
            property_matches AS (
                SELECT b.bbl, b.address, b.current_owner_name as owner, b.assessed_total_value as assessed_value, b.sale_price,
                    COUNT(DISTINCT p.id) FILTER (WHERE p.id IS NOT NULL) as permits,
                    -- simple reasons
                    ARRAY_REMOVE(ARRAY[
                        CASE WHEN b.address ILIKE %s THEN 'Address prefix' END,
                        CASE WHEN b.address ILIKE %s THEN 'Address contains' END,
                        CASE WHEN b.current_owner_name ILIKE %s THEN 'Owner' END,
                        CASE WHEN b.bbl::text LIKE %s THEN 'BBL' END
                    ], NULL) as match_reasons,
                    CASE
                        WHEN b.address ILIKE %s THEN 1
                        WHEN b.address ILIKE %s THEN 2
                        WHEN b.current_owner_name ILIKE %s THEN 3
                        WHEN b.bbl::text LIKE %s THEN 4
                        ELSE 5
                    END as match_priority
                FROM buildings b
                LEFT JOIN permits p ON b.bbl = p.bbl
                WHERE ({addr_clause}) OR ({owner_clause}) OR ({owner_rpad_clause}) OR ({owner_hpd_clause}) OR ({bbl_clause})
                GROUP BY b.bbl, b.address, b.current_owner_name, b.assessed_total_value, b.sale_price
            )
            SELECT pm.bbl, pm.address, pm.owner, pm.assessed_value, pm.sale_price, pm.permits,
                pm.match_reasons || COALESCE(cm.contact_reasons, ARRAY[]::text[]) || COALESCE(am.applicant_reasons, ARRAY[]::text[]) as match_reasons,
                pm.match_priority
            FROM property_matches pm
            LEFT JOIN contact_matches cm ON pm.bbl = cm.bbl
            LEFT JOIN applicant_matches am ON pm.bbl = am.bbl
            ORDER BY pm.match_priority, pm.permits DESC
            LIMIT 50
        """

        # execute: need to provide the extra prefix/contains/owner/bbl params before the dynamic params
        exec_params = [prefix_param, contains_param, contains_param, contains_param, prefix_param, contains_param, contains_param, contains_param]
        # Then append the dynamic params constructed above: addr, owner, owner_rpad, owner_hpd, bbl, contact, applicant
        exec_params += params

        cur.execute(sql, tuple(exec_params))
        results = cur.fetchall()
        
        return jsonify([dict(r) for r in results])

    except Exception as e:
        print(f"Search error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify([])


@app.route('/api/suggest')
def api_suggest():
    """Enhanced autocomplete suggestions with match types"""
    query = request.args.get('q', '').strip()
    limit = request.args.get('limit', 5, type=int)
    
    if not query or len(query) < 2:
        return jsonify([])
    
    try:
        with DatabaseConnection() as cur:
            # Tokenize query so multi-word input works regardless of order
            tokens = [t.strip() for t in query.split() if t.strip()]
            tok_count = len(tokens)
            
            def and_like_clause(field, tok_count):
                return '(' + ' AND '.join([f"{field} ILIKE %s" for _ in range(tok_count)]) + ')'

            addr_clause = and_like_clause('b.address', tok_count)
            owner_clause = and_like_clause('b.current_owner_name', tok_count)
            owner_rpad_clause = and_like_clause('b.owner_name_rpad', tok_count)
            owner_hpd_clause = and_like_clause('b.owner_name_hpd', tok_count)
            contact_clause = and_like_clause('LOWER(c.name)', tok_count)

        params = []
        params += [f"%{t}%" for t in tokens]         # addr
        params += [f"%{t}%" for t in tokens]         # owner
        params += [f"%{t}%" for t in tokens]         # owner_rpad
        params += [f"%{t}%" for t in tokens]         # owner_hpd
        params += [f"%{t.lower()}%" for t in tokens] # contact

        sql = f"""
            WITH address_matches AS (
                SELECT b.bbl, b.address, b.current_owner_name as owner, COUNT(DISTINCT p.id) FILTER (WHERE p.id IS NOT NULL) as permits, 'Address' as match_type, 1 as priority
                FROM buildings b
                LEFT JOIN permits p ON b.bbl = p.bbl
                WHERE {addr_clause}
                GROUP BY b.bbl, b.address, b.current_owner_name
                ORDER BY permits DESC
                LIMIT %s
            ),
            owner_matches AS (
                SELECT b.bbl, b.address, b.current_owner_name as owner, COUNT(DISTINCT p.id) FILTER (WHERE p.id IS NOT NULL) as permits, 'Owner' as match_type, 2 as priority
                FROM buildings b
                LEFT JOIN permits p ON b.bbl = p.bbl
                WHERE ({owner_clause}) OR ({owner_rpad_clause}) OR ({owner_hpd_clause})
                AND b.bbl NOT IN (SELECT bbl FROM address_matches)
                GROUP BY b.bbl, b.address, b.current_owner_name
                ORDER BY permits DESC
                LIMIT %s
            ),
            contact_matches AS (
                SELECT DISTINCT b.bbl, b.address, b.current_owner_name as owner, COUNT(DISTINCT p.id) FILTER (WHERE p.id IS NOT NULL) as permits, 'Contact' as match_type, 3 as priority
                FROM buildings b
                LEFT JOIN permits p ON b.bbl = p.bbl
                WHERE (LOWER(p.permittee_business_name) LIKE %s OR LOWER(p.owner_business_name) LIKE %s OR LOWER(p.applicant) LIKE %s)
                AND (p.permittee_business_name IS NOT NULL OR p.owner_business_name IS NOT NULL OR p.applicant IS NOT NULL)
                AND b.bbl NOT IN (SELECT bbl FROM address_matches UNION SELECT bbl FROM owner_matches)
                GROUP BY b.bbl, b.address, b.current_owner_name
                ORDER BY permits DESC
                LIMIT %s
            )
            SELECT * FROM address_matches
            UNION ALL
            SELECT * FROM owner_matches
            UNION ALL
            SELECT * FROM contact_matches
            ORDER BY priority, permits DESC
            LIMIT %s
        """

        exec_params = params[:tok_count] + params[tok_count:tok_count*2] + params[tok_count*2:tok_count*3] + params[tok_count*3:tok_count*4] + params[tok_count*4:tok_count*5]
        # address limit, owner limit, contact limit, final limit
        exec_params = exec_params + [limit, limit, limit, limit]

        cur.execute(sql, tuple(exec_params))
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
def properties_page():
    """Render the properties search/browse page"""
    return render_template('properties.html')


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
    - borough: Borough filter (1-5)
    - building_class: Building class code
    - min_units, max_units: Unit count range
    - has_violations: Has HPD violations (true/false)
    - recent_sale_days: Sold within X days
    - financing_min, financing_max: Financing ratio range
    - sort_by: Field to sort (value, sale_date, address)
    - sort_order: asc or desc
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
            borough = request.args.get('borough', type=int)
            building_class = request.args.get('building_class', '').strip()
            min_units = request.args.get('min_units', type=int)
            max_units = request.args.get('max_units', type=int)
            has_violations = request.args.get('has_violations')
            recent_sale_days = request.args.get('recent_sale_days', type=int)
            financing_min = request.args.get('financing_min', type=float)
            financing_max = request.args.get('financing_max', type=float)
            sort_by = request.args.get('sort_by', 'sale_date')
            sort_order = request.args.get('sort_order', 'desc').lower()
            page = max(1, request.args.get('page', 1, type=int))
            per_page = min(200, max(1, request.args.get('per_page', 50, type=int)))
            
            # Build WHERE clauses
            where_clauses = []
            params = []
        
            # Text search across multiple fields
            if search:
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
        
            # Borough filter
            if borough:
                where_clauses.append("b.borough = %s")
                params.append(borough)
        
            # Building class
            if building_class:
                where_clauses.append("b.building_class LIKE %s")
                params.append(f"{building_class}%")
        
            # Units range
            if min_units is not None:
                where_clauses.append("b.total_units >= %s")
                params.append(min_units)
            if max_units is not None:
                where_clauses.append("b.total_units <= %s")
                params.append(max_units)
        
            # HPD violations
            if has_violations is not None:
                if has_violations.lower() == 'true':
                    where_clauses.append("b.hpd_open_violations > 0")
                else:
                    where_clauses.append("(b.hpd_open_violations = 0 OR b.hpd_open_violations IS NULL)")
        
            # Build WHERE clause
            where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        
            # Get permit counts subquery using BBL
            permit_count_sql = """
                LEFT JOIN (
                    SELECT bbl, COUNT(*) as permit_count
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
                'owner': 'COALESCE(b.current_owner_name, b.owner_name_rpad)',
                'permits': 'pc.permit_count'
            }
            sort_column = valid_sort_columns.get(sort_by, 'b.sale_date')
            sort_direction = 'ASC' if sort_order == 'asc' else 'DESC'
        
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
                    COALESCE(pc.permit_count, 0) as permit_count,
                    b.last_updated
                FROM buildings b
                {permit_count_sql}
                {where_sql}
                ORDER BY {sort_column} {sort_direction} NULLS LAST, b.id
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


# ============================================================================
# CONTRACTOR PROFILE ROUTES
# ============================================================================

@app.route('/contractors')
def contractors_page():
    """Render the contractors search/browse page"""
    return render_template('contractors.html')


@app.route('/contractor/<contractor_name>')
def contractor_profile(contractor_name):
    """Render contractor profile page"""
    return render_template('contractor_profile.html', contractor_name=contractor_name)


@app.route('/api/contractors/search')
@cache.cached(timeout=300, query_string=True)
def api_contractors_search():
    """
    Search contractors with aggregated stats
    
    Query Parameters:
    - search: Contractor name or license search
    - sort_by: active_jobs, total_jobs, total_value (default: total_jobs)
    - sort_order: asc or desc (default: desc)
    - page: Page number (default 1)
    - per_page: Results per page (default 50, max 200)
    """
    try:
        with DatabaseConnection() as cur:
            # Parse query parameters
            search = request.args.get('search', '').strip()
            sort_by = request.args.get('sort_by', 'total_jobs')
            sort_order = request.args.get('sort_order', 'desc').lower()
            page = max(1, request.args.get('page', 1, type=int))
            per_page = min(200, max(1, request.args.get('per_page', 50, type=int)))
            offset = (page - 1) * per_page
            
            # Build WHERE clause - exclude NULL, empty, and placeholder values
            where_clause = """WHERE p.applicant IS NOT NULL 
                AND p.applicant != '' 
                AND p.applicant != 'N/A'
                AND p.applicant != 'NA'
                AND p.applicant != 'NONE'
                AND p.applicant NOT ILIKE 'unknown%%'"""
            search_params = []
            
            if search:
                where_clause += " AND (p.applicant ILIKE %s OR p.permittee_license_number ILIKE %s OR p.permittee_business_name ILIKE %s)"
                search_term = f"%{search}%"
                search_params = [search_term, search_term, search_term]
            
            # Determine sort column
            sort_column = {
                'active_jobs': 'active_jobs',
                'total_jobs': 'total_jobs',
                'total_value': 'total_value',
                'largest_project': 'largest_project'
            }.get(sort_by, 'total_jobs')
            
            sort_direction = 'ASC' if sort_order == 'asc' else 'DESC'
            
            # Get contractors with aggregated stats
            query = f"""
                WITH contractor_stats AS (
                    SELECT 
                        p.applicant as contractor_name,
                        p.permittee_license_number as license,
                        COUNT(*) as total_jobs,
                        COUNT(CASE WHEN p.issue_date >= CURRENT_DATE - INTERVAL '90 days' THEN 1 END) as active_jobs,
                        COALESCE(SUM(b.assessed_total_value), 0) as total_value,
                        COALESCE(MAX(b.assessed_total_value), 0) as largest_project,
                        MAX(p.issue_date) as most_recent_job,
                        COUNT(DISTINCT p.bbl) as unique_properties,
                        string_agg(DISTINCT p.job_type, ', ') as job_types
                    FROM permits p
                    LEFT JOIN buildings b ON p.bbl = b.bbl
                    {where_clause}
                    GROUP BY p.applicant, p.permittee_license_number
                )
                SELECT 
                    contractor_name,
                    license,
                    total_jobs,
                    active_jobs,
                    total_value,
                    largest_project,
                    most_recent_job,
                    unique_properties,
                    job_types
                FROM contractor_stats
                ORDER BY {sort_column} {sort_direction}
                LIMIT %s OFFSET %s
            """
            
            query_params = search_params + [per_page, offset]
            cur.execute(query, query_params)
            contractors = cur.fetchall()
            
            # Get total count
            count_query = f"""
                SELECT COUNT(DISTINCT p.applicant)
                FROM permits p
                {where_clause}
            """
            cur.execute(count_query, search_params)
            total = cur.fetchone()['count']
        
        return jsonify({
            'success': True,
            'contractors': [dict(c) for c in contractors],
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


@app.route('/api/contractor/<contractor_name>')
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
                p.applicant as contractor_name,
                p.permittee_license_number as license,
                COUNT(*) as total_jobs,
                COUNT(CASE WHEN p.issue_date >= CURRENT_DATE - INTERVAL '90 days' THEN 1 END) as active_jobs,
                COUNT(CASE WHEN p.issue_date >= CURRENT_DATE - INTERVAL '365 days' THEN 1 END) as jobs_last_year,
                COALESCE(SUM(b.assessed_total_value), 0) as total_value,
                COALESCE(MAX(b.assessed_total_value), 0) as largest_project,
                COALESCE(AVG(b.assessed_total_value), 0) as avg_project_value,
                MAX(p.issue_date) as most_recent_job,
                MIN(p.issue_date) as first_job,
                COUNT(DISTINCT p.bbl) as unique_properties,
                COUNT(DISTINCT p.job_type) as job_type_variety,
                string_agg(DISTINCT p.job_type, ', ') as job_types
            FROM permits p
            LEFT JOIN buildings b ON p.bbl = b.bbl
            WHERE p.applicant = %s
            GROUP BY p.applicant, p.permittee_license_number
            """, (contractor_name,))
            
            stats = cur.fetchone()
            
            if not stats:
                return jsonify({'success': False, 'error': 'Contractor not found'}), 404
            
            # Get permits (most recent first)
            cur.execute("""
                SELECT 
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
                FROM permits p
                LEFT JOIN buildings b ON p.bbl = b.bbl
                WHERE p.applicant = %s
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
                    COUNT(p.id) as permit_count,
                    MAX(p.issue_date) as most_recent_work,
                    MIN(p.issue_date) as first_work,
                    string_agg(DISTINCT p.job_type, ', ') as job_types
                FROM buildings b
                INNER JOIN permits p ON p.bbl = b.bbl
                WHERE p.applicant = %s
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
                id, bbl, address, CAST(borough AS TEXT) as borough, block, lot,
                -- PLUTO data
                current_owner_name, total_units, building_sqft, year_built, year_altered, building_class,
                -- RPAD data
                owner_name_rpad, assessed_land_value, assessed_total_value,
                -- HPD data
                owner_name_hpd, hpd_total_violations, hpd_total_complaints,
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
                -- Metadata
                last_updated
            FROM buildings
            WHERE bbl = %s
        """, (bbl,))
        
        building = cur.fetchone()
        
        if not building:
            return jsonify({'success': False, 'error': 'Property not found'}), 404
        
        building_id = building['id']
        
        # ===== 2. PERMITS (All construction activity) =====
        cur.execute("""
            SELECT 
                permit_no, job_type, address, applicant,
                stories, total_units, use_type, issue_date, link,
                permittee_business_name, permittee_phone, permittee_license_type, permittee_license_number,
                owner_business_name, owner_phone,
                superintendent_business_name, site_safety_mgr_business_name,
                work_type, permit_status, filing_status
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
            'pluto': building['current_owner_name'],
            'rpad': building['owner_name_rpad'],
            'hpd': building['owner_name_hpd'],
            'ecb': building['ecb_respondent_name']
        }
        
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
                    'icon': icon,
                    'title': f"{txn['doc_type']} - ${txn['doc_amount']:,.0f}" if txn['doc_amount'] else txn['doc_type'],
                    'description': f"Document ID: {txn['document_id']}",
                    'crfn': txn['crfn']
                })
        
        # Sort timeline by date descending
        activity_timeline.sort(key=lambda x: x['date'], reverse=True)
        
        # ===== 9. CONTACT AGGREGATION =====
        contacts = []
        
        # Option 1: Get from contacts table (if linked to permits)
        cur.execute("""
            SELECT DISTINCT c.name, c.phone, c.role, c.is_mobile, c.line_type, c.carrier_name
            FROM contacts c
            JOIN permit_contacts pc ON c.id = pc.contact_id
            JOIN permits p ON pc.permit_id = p.id
            WHERE p.bbl = %s AND c.phone IS NOT NULL
        """, (bbl,))
        
        contacts_from_db = cur.fetchall()
        for contact in contacts_from_db:
            contacts.append({
                'name': contact['name'],
                'phone': contact['phone'],
                'role': contact['role'] or 'Contact',
                'is_mobile': contact['is_mobile'],
                'line_type': contact['line_type'],
                'carrier': contact['carrier_name']
            })
        
        # Option 2: Get unique contractors from permits (with phone numbers)
        contractor_contacts = {}
        for permit in permits:
            # Permittee with phone
            if permit['permittee_business_name'] and permit['permittee_phone']:
                key = permit['permittee_business_name']
                if key not in contractor_contacts:
                    contractor_contacts[key] = {
                        'name': permit['permittee_business_name'],
                        'phone': permit['permittee_phone'],
                        'role': 'Contractor/Permittee',
                        'license': permit['permittee_license_type'],
                        'permit_count': 0
                    }
                contractor_contacts[key]['permit_count'] += 1
            
            # Owner with phone
            if permit['owner_business_name'] and permit['owner_phone']:
                key = f"owner_{permit['owner_business_name']}"
                if key not in contractor_contacts:
                    contractor_contacts[key] = {
                        'name': permit['owner_business_name'],
                        'phone': permit['owner_phone'],
                        'role': 'Property Owner',
                        'permit_count': 0
                    }
                contractor_contacts[key]['permit_count'] += 1
        
        contacts.extend(contractor_contacts.values())
        
        # Option 3: Contractors without phone numbers (fallback)
        contractors_no_phone = {}
        for permit in permits:
            if permit['permittee_business_name'] and not permit['permittee_phone']:
                key = permit['permittee_business_name']
                if key not in contractor_contacts and key not in contractors_no_phone:
                    contractors_no_phone[key] = {
                        'name': permit['permittee_business_name'],
                        'phone': None,
                        'role': 'Contractor/Permittee',
                        'license': permit['permittee_license_type'],
                        'permit_count': 0
                    }
                if key in contractors_no_phone:
                    contractors_no_phone[key]['permit_count'] += 1
        
        # Only add contractors without phones if we have very few contacts
        if len(contacts) < 5:
            contacts.extend(list(contractors_no_phone.values())[:10])
        
        return jsonify({
            'success': True,
            'building': dict(building),
            'building_class_description': building_class_desc,
            'owners': owners,
            'risk_assessment': {
                'score': risk_score,
                'level': risk_level,
                'label': risk_label,
                'color': risk_color,
                'factors': risk_factors
            },
            'permits': [dict(p) for p in permits],
            'transactions': [dict(t) for t in transactions],
            'parties': [dict(p) for p in parties],
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


def translate_building_class(code):
    """
    Translate NYC building classification codes to plain English
    Returns: (code, plain english description)
    """
    if not code:
        return "Unknown building type"
    
    # NYC building class codes - https://www1.nyc.gov/assets/finance/jump/hlpbldgcode.html
    translations = {
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
    
    return translations.get(code, f"Building code {code}")


if __name__ == '__main__':
    print("Starting DOB Permit Dashboard API...")
    print(f"Database: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
    port = int(os.getenv('PORT', 5001))
    debug = os.getenv('FLASK_ENV') != 'production'
    print(f"Visit: http://localhost:{port}")
    app.run(debug=debug, host='0.0.0.0', port=port)

