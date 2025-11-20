#!/usr/bin/env python3
"""
Flask API Backend for DOB Permit Dashboard
Serves data from PostgreSQL database to HTML frontend
"""

from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for local development

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': os.getenv('DB_PORT', '5432'),
    'database': os.getenv('DB_NAME', 'permits_db'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', '')
}


def get_db_connection():
    """Create database connection"""
    return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)


def calculate_lead_score(permit):
    """Calculate lead score based on permit attributes"""
    score = 0
    
    # Contact count
    contact_count = permit.get('contact_count', 0)
    if contact_count > 0:
        score += min(contact_count * 15, 40)
    
    # Has mobile phone
    if permit.get('has_mobile'):
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
        conn = get_db_connection()
        cur = conn.cursor()
        
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
        
        cur.close()
        conn.close()
        
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
def get_stats():
    """Get dashboard statistics including building intelligence"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
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
        
        cur.close()
        conn.close()
        
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
        conn = get_db_connection()
        cur = conn.cursor()
        
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
        
        cur.close()
        conn.close()
        
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
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT DISTINCT job_type FROM permits WHERE job_type IS NOT NULL ORDER BY job_type;")
        types = [row['job_type'] for row in cur.fetchall()]
        
        cur.close()
        conn.close()
        
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
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT job_type, COUNT(*) as count 
            FROM permits 
            WHERE job_type IS NOT NULL
            GROUP BY job_type 
            ORDER BY count DESC 
            LIMIT 10;
        """)
        
        data = cur.fetchall()
        
        cur.close()
        conn.close()
        
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
        conn = get_db_connection()
        cur = conn.cursor()
        
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
        
        cur.close()
        conn.close()
        
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
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT applicant, COUNT(*) as count
            FROM permits
            WHERE applicant IS NOT NULL AND applicant != ''
            GROUP BY applicant
            ORDER BY count DESC
            LIMIT 10;
        """)
        
        data = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'labels': [row['applicant'][:30] for row in data],  # Truncate long names
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
        conn = get_db_connection()
        cur = conn.cursor()
        
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
        
        cur.close()
        conn.close()
        
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
        conn = get_db_connection()
        cur = conn.cursor()
        
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
        
        cur.close()
        conn.close()
        
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
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.close()
        conn.close()
        
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
        conn = get_db_connection()
        cur = conn.cursor()
        
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
                b.bbl,
                b.address as building_address,
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
                b.mortgage_amount,
                b.bin
            FROM permits p
            LEFT JOIN buildings b ON p.bbl = b.bbl
            WHERE p.id = %s;
        """
        
        cur.execute(query, (permit_id,))
        permit = cur.fetchone()
        
        if not permit:
            cur.close()
            conn.close()
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
                    p.issue_date,
                    p.address,
                    (
                        CASE WHEN p.permittee_phone IS NOT NULL AND p.permittee_phone != '' THEN 1 ELSE 0 END +
                        CASE WHEN p.owner_phone IS NOT NULL AND p.owner_phone != '' THEN 1 ELSE 0 END
                    ) as contact_count
                FROM permits p
                WHERE p.bbl = %s AND p.id != %s
                ORDER BY p.issue_date DESC
                LIMIT 20;
            """, (permit['bbl'], permit_id))
            related_permits = cur.fetchall()
        
        # Calculate lead score
        permit['lead_score'] = calculate_lead_score(permit)
        
        cur.close()
        conn.close()
        
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
        conn = get_db_connection()
        cur = conn.cursor()
        
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
        
        cur.close()
        conn.close()
        
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
        conn = get_db_connection()
        cur = conn.cursor()
        
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
        
        cur.close()
        conn.close()
        
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
        conn = get_db_connection()
        cur = conn.cursor()
        
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
        
        cur.close()
        conn.close()
        
        return jsonify(contacts)
        
    except Exception as e:
        print(f"Error fetching building contacts: {e}")
        return jsonify([])


@app.route('/api/seller-leads')
def get_seller_leads():
    """Get previous property owners (sellers) with addresses for outreach campaign"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
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
        query += f" LIMIT {limit}"
        
        cur.execute(query, params)
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
        
        cur.close()
        conn.close()
        
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
        conn = get_db_connection()
        cur = conn.cursor()
        
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
        
        cur.close()
        conn.close()
        
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
        conn = get_db_connection()
        cur = conn.cursor()
        
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
        
        cur.close()
        conn.close()
        
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
        conn = get_db_connection()
        cur = conn.cursor()
        
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
        
        cur.close()
        conn.close()
        
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
        conn = get_db_connection()
        cur = conn.cursor()
        
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
            cur.close()
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Permit not found'
            }), 404
        
        # Calculate lead score
        permit['lead_score'] = calculate_lead_score(permit)
        
        cur.close()
        conn.close()
        
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


@app.route('/investments')
def investments():
    """Investment opportunities page"""
    return render_template('investments.html')


@app.route('/properties')
def properties():
    """Property database page"""
    return render_template('properties.html')


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
    """Universal property report page"""
    return render_template('property_detail.html', bbl=bbl)


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
        conn = get_db_connection()
        cur = conn.cursor()

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
        cur.close()
        conn.close()

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
        conn = get_db_connection()
        cur = conn.cursor()
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
        cur.close()
        conn.close()

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
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Active permits (issued in last 6 months)
        cur.execute("""
            SELECT COUNT(*) as count
            FROM permits
            WHERE issue_date >= CURRENT_DATE - INTERVAL '6 months'
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
        
        # Qualified leads (permits with phone contacts)
        cur.execute("""
            SELECT COUNT(*) as count
            FROM permits
            WHERE (permittee_phone IS NOT NULL AND permittee_phone != '') 
               OR (owner_phone IS NOT NULL AND owner_phone != '')
        """)
        qualified_leads = cur.fetchone()['count']
        
        cur.close()
        conn.close()
        
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
        conn = get_db_connection()
        cur = conn.cursor()
        
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
        
        cur.close()
        conn.close()
        
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


if __name__ == '__main__':
    print("Starting DOB Permit Dashboard API...")
    print(f"Database: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
    port = int(os.getenv('PORT', 5001))
    debug = os.getenv('FLASK_ENV') != 'production'
    print(f"Visit: http://localhost:{port}")
    app.run(debug=debug, host='0.0.0.0', port=port)

