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
    """Serve the main dashboard page"""
    return render_template('index.html')


@app.route('/api/permits')
def get_permits():
    """Get all permits with calculated scores and contact info"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Query with contact information aggregated
        query = """
            SELECT 
                p.*,
                COALESCE(contact_info.contact_count, 0) as contact_count,
                COALESCE(contact_info.has_mobile, false) as has_mobile,
                contact_info.contact_names,
                contact_info.contact_phones
            FROM permits p
            LEFT JOIN (
                SELECT 
                    permit_id,
                    COUNT(*) as contact_count,
                    BOOL_OR(COALESCE(is_mobile, false)) as has_mobile,
                    STRING_AGG(name, '|' ORDER BY name) as contact_names,
                    STRING_AGG(phone, '|' ORDER BY name) as contact_phones
                FROM contacts
                WHERE name IS NOT NULL AND name != ''
                GROUP BY permit_id
            ) contact_info ON p.id = contact_info.permit_id
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
    """Get dashboard statistics"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Total permits
        cur.execute("SELECT COUNT(*) as total FROM permits;")
        total_permits = cur.fetchone()['total']
        
        # Total contacts
        cur.execute("SELECT COUNT(*) as total FROM contacts WHERE name IS NOT NULL AND name != '';")
        total_contacts = cur.fetchone()['total']
        
        # Mobile contacts
        cur.execute("SELECT COUNT(*) as total FROM contacts WHERE is_mobile = TRUE;")
        mobile_contacts = cur.fetchone()['total']
        
        cur.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'stats': {
                'total_permits': total_permits,
                'total_contacts': total_contacts,
                'mobile_contacts': mobile_contacts
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
    """Search for permits by contact name or phone"""
    query = request.args.get('q', '').strip()
    
    if len(query) < 2:
        return jsonify({
            'success': False,
            'error': 'Query must be at least 2 characters'
        }), 400
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        search_query = """
            SELECT DISTINCT
                p.id,
                p.permit_no,
                p.address,
                p.job_type,
                p.issue_date,
                c.name as contact_name,
                c.phone as contact_phone
            FROM permits p
            INNER JOIN contacts c ON p.id = c.permit_id
            WHERE 
                (LOWER(c.name) LIKE %s OR c.phone LIKE %s)
                AND c.name IS NOT NULL 
                AND c.name != ''
            ORDER BY p.issue_date DESC
            LIMIT 50;
        """
        
        search_pattern = f'%{query.lower()}%'
        cur.execute(search_query, (search_pattern, search_pattern))
        
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
        
        # Get contacts
        cur.execute("""
            SELECT name, phone
            FROM contacts 
            WHERE permit_id = %s 
                AND name IS NOT NULL 
                AND name != ''
            ORDER BY name;
        """, (permit_id,))
        contacts = cur.fetchall()
        
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
    """Serve detailed permit view page"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Get permit with all details
        query = """
            SELECT 
                p.*,
                COALESCE(contact_info.contact_count, 0) as contact_count,
                COALESCE(contact_info.has_mobile, false) as has_mobile
            FROM permits p
            LEFT JOIN (
                SELECT 
                    permit_id,
                    COUNT(*) as contact_count,
                    BOOL_OR(COALESCE(is_mobile, false)) as has_mobile
                FROM contacts
                WHERE name IS NOT NULL AND name != ''
                GROUP BY permit_id
            ) contact_info ON p.id = contact_info.permit_id
            WHERE p.id = %s;
        """
        
        cur.execute(query, (permit_id,))
        permit = cur.fetchone()
        
        if not permit:
            cur.close()
            conn.close()
            return "Permit not found", 404
        
        # Get all contacts for this permit
        cur.execute("""
            SELECT name, phone, is_mobile 
            FROM contacts 
            WHERE permit_id = %s AND name IS NOT NULL AND name != ''
            ORDER BY name;
        """, (permit_id,))
        contacts = cur.fetchall()
        
        # Calculate lead score
        permit['lead_score'] = calculate_lead_score(permit)
        
        cur.close()
        conn.close()
        
        return render_template('permit_detail.html', permit=permit, contacts=contacts)
        
    except Exception as e:
        print(f"Error fetching permit detail: {e}")
        return f"Error loading permit: {str(e)}", 500


@app.route('/api/permit/<int:permit_id>')
def get_permit_detail(permit_id):
    """API endpoint for getting single permit details (JSON)"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Get permit with all details
        query = """
            SELECT 
                p.*,
                COALESCE(contact_info.contact_count, 0) as contact_count,
                COALESCE(contact_info.has_mobile, false) as has_mobile,
                contact_info.contact_names,
                contact_info.contact_phones
            FROM permits p
            LEFT JOIN (
                SELECT 
                    permit_id,
                    COUNT(*) as contact_count,
                    BOOL_OR(COALESCE(is_mobile, false)) as has_mobile,
                    STRING_AGG(name, '|' ORDER BY name) as contact_names,
                    STRING_AGG(phone, '|' ORDER BY name) as contact_phones
                FROM contacts
                WHERE name IS NOT NULL AND name != ''
                GROUP BY permit_id
            ) contact_info ON p.id = contact_info.permit_id
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


if __name__ == '__main__':
    print("Starting DOB Permit Dashboard API...")
    print(f"Database: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
    port = int(os.getenv('PORT', 5001))
    debug = os.getenv('FLASK_ENV') != 'production'
    print(f"Visit: http://localhost:{port}")
    app.run(debug=debug, host='0.0.0.0', port=port)

