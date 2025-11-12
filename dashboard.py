import streamlit as st
import mysql.connector
import psycopg2
import psycopg2.extras
import pandas as pd
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import plotly.express as px

# Load environment variables
load_dotenv()

# Detect database type
DB_TYPE = os.getenv('DB_TYPE', 'mysql')  # 'mysql' or 'postgresql'

# Page config
st.set_page_config(
    page_title="DOB Permit Leads Dashboard",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for better styling
st.markdown("""
    <style>
    /* Main background */
    .main {
        background-color: #0e1117;
    }
    
    /* Header styling */
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #ffffff;
        margin-bottom: 0.5rem;
    }
    
    /* Metric cards */
    .stMetric {
        background-color: #262730;
        padding: 1rem;
        border-radius: 0.5rem;
        box-shadow: 0 2px 4px rgba(0,0,0,0.3);
    }
    
    /* Sidebar styling */
    section[data-testid="stSidebar"] {
        background-color: #262730;
    }
    
    /* Contact cards */
    .contact-card {
        background-color: #1e3a5f;
        padding: 0.75rem;
        border-radius: 0.5rem;
        margin-bottom: 0.5rem;
        border-left: 3px solid #4a9eff;
    }
    
    /* Expander styling */
    .streamlit-expanderHeader {
        background-color: #262730;
        border-radius: 0.5rem;
    }
    
    /* Input fields */
    .stTextInput > div > div > input {
        background-color: #262730;
        color: #ffffff;
    }
    
    /* Select boxes */
    .stSelectbox > div > div > select {
        background-color: #262730;
        color: #ffffff;
    }
    
    /* Date inputs */
    .stDateInput > div > div > input {
        background-color: #262730;
        color: #ffffff;
    }
    </style>
""", unsafe_allow_html=True)

# Database connection
@st.cache_resource
def get_db_connection():
    if DB_TYPE == 'postgresql':
        return psycopg2.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', '5432')),
            user=os.getenv('DB_USER', 'scraper_user'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_NAME', 'permit_scraper')
        )
    else:  # mysql
        return mysql.connector.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', '3306')),
            user=os.getenv('DB_USER', 'scraper_user'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_NAME', 'permit_scraper')
        )

conn = get_db_connection()

# Count total results for pagination
@st.cache_data(ttl=300)
def count_permits(permit_type=None, date_from=None, date_to=None, has_contacts=True, 
                  max_units=None, min_units=None, has_units_info=False, 
                  single_family=False, multi_family=False, min_contacts=None, 
                  only_mobile=False, min_stories=None, max_stories=None,
                  permit_status=None):
    """Count total permits matching filters for pagination"""
    if DB_TYPE == 'postgresql':
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cursor = conn.cursor(dictionary=True)
    
    # Build count query (same filters as main query)
    query = "SELECT COUNT(DISTINCT p.id) as total FROM permits p LEFT JOIN contacts c ON p.id = c.permit_id"
    
    if only_mobile:
        if DB_TYPE == 'postgresql':
            query = query.replace("AND c.phone != ''", "AND c.phone != '' AND c.is_mobile = true")
        else:
            query = query.replace("AND c.phone != ''", "AND c.phone != '' AND c.is_mobile = 1")
    
    query += " WHERE 1=1"
    params = []
    
    if has_contacts:
        query += " AND c.id IS NOT NULL"
    
    if permit_type and permit_type != "All":
        query += " AND p.job_type = %s"
        params.append(permit_type)
    
    if date_from:
        query += " AND p.issue_date >= %s"
        params.append(date_from)
    
    if date_to:
        query += " AND p.issue_date <= %s"
        params.append(date_to)
    
    if min_stories is not None:
        query += " AND p.stories >= %s"
        params.append(min_stories)
    
    if max_stories is not None:
        query += " AND p.stories <= %s"
        params.append(max_stories)
    
    if permit_status:
        if DB_TYPE == 'postgresql':
            if 'Active' in permit_status:
                query += " AND p.exp_date >= CURRENT_DATE"
            if 'Expired' in permit_status:
                if 'Active' not in permit_status:
                    query += " AND p.exp_date < CURRENT_DATE"
            if 'Expiring Soon' in permit_status and 'Active' not in permit_status:
                query += " AND p.exp_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '30 days'"
        else:
            if 'Active' in permit_status:
                query += " AND p.exp_date >= CURDATE()"
            if 'Expired' in permit_status:
                if 'Active' not in permit_status:
                    query += " AND p.exp_date < CURDATE()"
            if 'Expiring Soon' in permit_status and 'Active' not in permit_status:
                query += " AND p.exp_date BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 30 DAY)"
    
    cursor.execute(query, params)
    result = cursor.fetchone()
    cursor.close()
    
    if DB_TYPE == 'postgresql':
        return result['total'] if result else 0
    else:
        return result['total'] if result else 0

# Fetch data function with caching (increased TTL to 5 minutes)
@st.cache_data(ttl=300)
def fetch_permit_data(permit_type=None, date_from=None, date_to=None, has_contacts=True, 
                      max_units=None, min_units=None, has_units_info=False, 
                      single_family=False, multi_family=False, min_contacts=None, 
                      only_mobile=False, min_stories=None, max_stories=None,
                      permit_status=None, limit=None, offset=0):
    if DB_TYPE == 'postgresql':
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cursor = conn.cursor(dictionary=True)
    
    # PostgreSQL uses string_agg() instead of GROUP_CONCAT(), CURRENT_DATE instead of CURDATE()
    if DB_TYPE == 'postgresql':
        query = """
            SELECT 
                p.id,
                p.permit_no,
                p.applicant,
                p.job_type,
                p.issue_date,
                p.exp_date,
                p.address,
                p.bin,
                p.use_type,
                p.stories,
                p.total_units,
                p.occupied_units,
                p.link,
                p.latitude,
                p.longitude,
                (p.exp_date - CURRENT_DATE) as days_until_exp,
                string_agg(DISTINCT c.name, ' | ') as contact_names,
                string_agg(DISTINCT c.phone, ' | ') as contact_phones,
                COUNT(DISTINCT c.id) as contact_count,
                SUM(CASE WHEN c.is_mobile = true THEN 1 ELSE 0 END) as mobile_count
            FROM permits p
            LEFT JOIN contacts c ON p.id = c.permit_id AND c.phone IS NOT NULL AND c.phone != ''
        """
    else:
        query = """
            SELECT 
                p.id,
                p.permit_no,
                p.applicant,
                p.job_type,
                p.issue_date,
                p.exp_date,
                p.address,
                p.bin,
                p.use_type,
                p.stories,
                p.total_units,
                p.occupied_units,
                p.link,
                p.latitude,
                p.longitude,
                DATEDIFF(p.exp_date, CURDATE()) as days_until_exp,
                GROUP_CONCAT(DISTINCT c.name SEPARATOR ' | ') as contact_names,
                GROUP_CONCAT(DISTINCT c.phone SEPARATOR ' | ') as contact_phones,
                COUNT(DISTINCT c.id) as contact_count,
                SUM(CASE WHEN c.is_mobile = 1 THEN 1 ELSE 0 END) as mobile_count
            FROM permits p
            LEFT JOIN contacts c ON p.id = c.permit_id AND c.phone IS NOT NULL AND c.phone != ''
        """
    
    # Add mobile filter to JOIN if needed
    if only_mobile:
        if DB_TYPE == 'postgresql':
            query = query.replace("AND c.phone != ''", "AND c.phone != '' AND c.is_mobile = true")
        else:
            query = query.replace("AND c.phone != ''", "AND c.phone != '' AND c.is_mobile = 1")
    
    query += " WHERE 1=1"
    
    params = []
    
    if has_contacts:
        query += " AND c.id IS NOT NULL"
    
    if permit_type and permit_type != "All":
        query += " AND p.job_type = %s"
        params.append(permit_type)
    
    if date_from:
        query += " AND p.issue_date >= %s"
        params.append(date_from)
    
    if date_to:
        query += " AND p.issue_date <= %s"
        params.append(date_to)
    
    # Stories filter
    if min_stories is not None:
        query += " AND p.stories >= %s"
        params.append(min_stories)
    
    if max_stories is not None:
        query += " AND p.stories <= %s"
        params.append(max_stories)
    
    # Permit status filter
    if permit_status:
        if DB_TYPE == 'postgresql':
            if 'Active' in permit_status:
                query += " AND p.exp_date >= CURRENT_DATE"
            if 'Expired' in permit_status:
                if 'Active' not in permit_status:
                    query += " AND p.exp_date < CURRENT_DATE"
            if 'Expiring Soon' in permit_status and 'Active' not in permit_status:
                query += " AND p.exp_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '30 days'"
        else:
            if 'Active' in permit_status:
                query += " AND p.exp_date >= CURDATE()"
            if 'Expired' in permit_status:
                if 'Active' not in permit_status:
                    query += " AND p.exp_date < CURDATE()"
            if 'Expiring Soon' in permit_status and 'Active' not in permit_status:
                query += " AND p.exp_date BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 30 DAY)"
    
    query += " GROUP BY p.id"
    
    # Apply HAVING clauses
    having_clauses = []
    
    if single_family:
        having_clauses.append("(p.total_units = 1) OR (COALESCE(p.total_units, 0) <= 1 AND (p.use_type LIKE '%FAMILY%' OR p.use_type LIKE '%RESIDENTIAL%'))")
    elif multi_family:
        having_clauses.append("p.total_units IS NOT NULL AND p.total_units >= 2")
    elif has_units_info:
        having_clauses.append("p.total_units IS NOT NULL AND p.total_units > 0")
    elif max_units is not None:
        having_clauses.append(f"p.total_units IS NOT NULL AND p.total_units <= {max_units} AND p.total_units > 0")
    elif min_units is not None:
        having_clauses.append(f"p.total_units IS NOT NULL AND p.total_units >= {min_units}")
    
    # Contact count filter
    if min_contacts is not None:
        having_clauses.append(f"contact_count >= {min_contacts}")
    
    if having_clauses:
        query += " HAVING " + " AND ".join(having_clauses)
    
    query += " ORDER BY p.issue_date DESC, p.id DESC"
    
    # Add pagination
    if limit is not None:
        query += f" LIMIT {limit} OFFSET {offset}"
    
    cursor.execute(query, params)
    results = cursor.fetchall()
    cursor.close()
    
    return pd.DataFrame(results) if results else pd.DataFrame()

# Calculate lead score (0-100)
def calculate_lead_score(row):
    """
    Score leads based on:
    - Recency (0-40 points): More recent = higher score
    - Contact availability (0-40 points): More contacts + mobile bonus = higher score
    - Permit type (0-10 points): New buildings score higher
    - Project size (0-10 points): Larger projects get small bonus
    """
    score = 0
    
    # Recency score (0-40 points)
    if pd.notna(row.get('issue_date')):
        days_old = (datetime.now() - pd.to_datetime(row['issue_date'])).days
        if days_old <= 30:
            score += 40
        elif days_old <= 90:
            score += 30
        elif days_old <= 180:
            score += 20
        elif days_old <= 365:
            score += 10
    
    # Contact score (0-40 points) - Higher impact for mobile
    contact_count = int(row.get('contact_count', 0))
    mobile_count = int(row.get('mobile_count', 0))
    
    # Base contact points (0-20)
    if contact_count >= 3:
        score += 20
    elif contact_count == 2:
        score += 15
    elif contact_count == 1:
        score += 10
    
    # Mobile bonus/penalty (up to +20, or -5 if no mobile)
    if mobile_count >= 3:
        score += 20
    elif mobile_count == 2:
        score += 15
    elif mobile_count == 1:
        score += 10
    elif contact_count > 0 and mobile_count == 0:
        # Has contacts but no mobile - slight penalty
        score -= 5
    
    # Permit type score (0-10 points)
    job_type = row.get('job_type', '')
    if job_type == 'NB':  # New Building
        score += 10
    elif job_type == 'AL':  # Alteration
        score += 7
    elif job_type == 'DM':  # Demolition
        score += 5
    
    # Project size score (0-10 points) - Lower impact
    total_units = pd.to_numeric(row.get('total_units'), errors='coerce')
    if pd.notna(total_units):
        if total_units >= 50:
            score += 10
        elif total_units >= 20:
            score += 7
        elif total_units >= 10:
            score += 5
        elif total_units >= 5:
            score += 3
    
    return min(score, 100)  # Cap at 100

# Generate score breakdown explanation
def get_score_breakdown(row):
    """Generate detailed explanation of how the lead score was calculated"""
    breakdown = []
    total = 0
    
    # Recency
    if pd.notna(row.get('issue_date')):
        days_old = (datetime.now() - pd.to_datetime(row['issue_date'])).days
        if days_old <= 30:
            pts = 40
            breakdown.append(f"⏰ Recency: +{pts} pts (Last 30 days)")
        elif days_old <= 90:
            pts = 30
            breakdown.append(f"⏰ Recency: +{pts} pts ({days_old} days old)")
        elif days_old <= 180:
            pts = 20
            breakdown.append(f"⏰ Recency: +{pts} pts ({days_old} days old)")
        elif days_old <= 365:
            pts = 10
            breakdown.append(f"⏰ Recency: +{pts} pts ({days_old} days old)")
        else:
            pts = 0
            breakdown.append(f"⏰ Recency: +{pts} pts (Over 1 year old)")
        total += pts
    
    # Contacts
    contact_count = int(row.get('contact_count', 0))
    mobile_count = int(row.get('mobile_count', 0))
    
    if contact_count >= 3:
        pts = 20
        breakdown.append(f"📞 Contacts: +{pts} pts ({contact_count} contacts)")
        total += pts
    elif contact_count == 2:
        pts = 15
        breakdown.append(f"📞 Contacts: +{pts} pts (2 contacts)")
        total += pts
    elif contact_count == 1:
        pts = 10
        breakdown.append(f"📞 Contacts: +{pts} pts (1 contact)")
        total += pts
    else:
        breakdown.append(f"📞 Contacts: +0 pts (No contacts)")
    
    # Mobile bonus/penalty
    if mobile_count >= 3:
        pts = 20
        breakdown.append(f"📱 Mobile: +{pts} pts ({mobile_count} mobile numbers)")
        total += pts
    elif mobile_count == 2:
        pts = 15
        breakdown.append(f"📱 Mobile: +{pts} pts (2 mobile numbers)")
        total += pts
    elif mobile_count == 1:
        pts = 10
        breakdown.append(f"📱 Mobile: +{pts} pts (1 mobile number)")
        total += pts
    elif contact_count > 0 and mobile_count == 0:
        pts = -5
        breakdown.append(f"📱 Mobile: {pts} pts (No mobile numbers)")
        total += pts
    
    # Permit type
    job_type = row.get('job_type', '')
    if job_type == 'NB':
        pts = 10
        breakdown.append(f"🏗️ Type: +{pts} pts (New Building)")
        total += pts
    elif job_type == 'AL':
        pts = 7
        breakdown.append(f"🏗️ Type: +{pts} pts (Alteration)")
        total += pts
    elif job_type == 'DM':
        pts = 5
        breakdown.append(f"🏗️ Type: +{pts} pts (Demolition)")
        total += pts
    else:
        breakdown.append(f"🏗️ Type: +0 pts ({job_type})")
    
    # Project size
    total_units = pd.to_numeric(row.get('total_units'), errors='coerce')
    if pd.notna(total_units):
        if total_units >= 50:
            pts = 10
            breakdown.append(f"🏢 Size: +{pts} pts ({int(total_units)} units)")
            total += pts
        elif total_units >= 20:
            pts = 7
            breakdown.append(f"🏢 Size: +{pts} pts ({int(total_units)} units)")
            total += pts
        elif total_units >= 10:
            pts = 5
            breakdown.append(f"🏢 Size: +{pts} pts ({int(total_units)} units)")
            total += pts
        elif total_units >= 5:
            pts = 3
            breakdown.append(f"🏢 Size: +{pts} pts ({int(total_units)} units)")
            total += pts
        else:
            breakdown.append(f"🏢 Size: +0 pts ({int(total_units)} units)")
    else:
        breakdown.append(f"🏢 Size: +0 pts (No unit data)")
    
    breakdown.append(f"\n━━━━━━━━━━━━━━━━\n✨ Total Score: {total}/100")
    return "\n".join(breakdown)

# Get unique permit types
@st.cache_data(ttl=300)
def get_permit_types():
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT job_type FROM permits ORDER BY job_type")
    types = [row[0] for row in cursor.fetchall() if row[0]]
    cursor.close()
    return ["All"] + types

# Get stories range from database
@st.cache_data(ttl=300)
def get_stories_range():
    cursor = conn.cursor()
    # Convert varchar to numeric, filter out NULL and 0
    if DB_TYPE == 'postgresql':
        cursor.execute("""
            SELECT 
                CAST(MIN(CAST(stories AS DECIMAL(10,2))) AS INTEGER) as min_stories,
                CAST(MAX(CAST(stories AS DECIMAL(10,2))) AS INTEGER) as max_stories
            FROM permits 
            WHERE stories IS NOT NULL 
            AND stories != '' 
            AND CAST(stories AS DECIMAL(10,2)) > 0
        """)
    else:
        cursor.execute("""
            SELECT 
                CAST(MIN(CAST(stories AS DECIMAL(10,2))) AS UNSIGNED) as min_stories,
                CAST(MAX(CAST(stories AS DECIMAL(10,2))) AS UNSIGNED) as max_stories
            FROM permits 
            WHERE stories IS NOT NULL 
            AND stories != '' 
            AND CAST(stories AS DECIMAL(10,2)) > 0
        """)
    result = cursor.fetchone()
    cursor.close()
    if result and result[0] is not None:
        return int(result[0]), int(result[1])
    return 1, 100  # Default fallback

# Get units range from database
@st.cache_data(ttl=300)
def get_units_range():
    cursor = conn.cursor()
    # Convert varchar to numeric, filter out NULL and 0
    if DB_TYPE == 'postgresql':
        cursor.execute("""
            SELECT 
                CAST(MIN(CAST(total_units AS DECIMAL(10,2))) AS INTEGER) as min_units,
                CAST(MAX(CAST(total_units AS DECIMAL(10,2))) AS INTEGER) as max_units
            FROM permits 
            WHERE total_units IS NOT NULL 
            AND total_units != '' 
            AND CAST(total_units AS DECIMAL(10,2)) > 0
        """)
    else:
        cursor.execute("""
            SELECT 
                CAST(MIN(CAST(total_units AS DECIMAL(10,2))) AS UNSIGNED) as min_units,
                CAST(MAX(CAST(total_units AS DECIMAL(10,2))) AS UNSIGNED) as max_units
            FROM permits 
            WHERE total_units IS NOT NULL 
            AND total_units != '' 
            AND CAST(total_units AS DECIMAL(10,2)) > 0
        """)
    result = cursor.fetchone()
    cursor.close()
    if result and result[0] is not None:
        return int(result[0]), int(result[1])
    return 1, 500  # Default fallback

# Header
st.markdown('<p class="main-header">📋 DOB Permit Leads Dashboard</p>', unsafe_allow_html=True)
st.markdown("---")

# Create tabs
tab1, tab2, tab3 = st.tabs(["📊 Leads Dashboard", "📈 Visualizations", "🗺️ Map View"])

# Sidebar filters
with st.sidebar:
    st.header("🔍 Filters")
    
    # Initialize filter state
    if 'quick_filter_type' not in st.session_state:
        st.session_state.quick_filter_type = None
    
    # Quick Date Filters
    st.subheader("⚡ Quick Date Filters")
    quick_filter = st.selectbox(
        "Select time range",
        ["Custom", "Last 24 Hours", "Last Week", "Last Month", "Last Quarter", "Last 6 Months", "Last Year", "All Time"],
        index=4  # Default to "Last Quarter"
    )
    
    # Calculate dates based on quick filter
    today = datetime.now()
    if quick_filter == "Last 24 Hours":
        date_from = today - timedelta(days=1)
        date_to = today
    elif quick_filter == "Last Week":
        date_from = today - timedelta(weeks=1)
        date_to = today
    elif quick_filter == "Last Month":
        date_from = today - timedelta(days=30)
        date_to = today
    elif quick_filter == "Last Quarter":
        date_from = today - timedelta(days=90)
        date_to = today
    elif quick_filter == "Last 6 Months":
        date_from = today - timedelta(days=180)
        date_to = today
    elif quick_filter == "Last Year":
        date_from = today - timedelta(days=365)
        date_to = today
    elif quick_filter == "All Time":
        date_from = datetime(2000, 1, 1)
        date_to = today
    else:  # Custom
        st.subheader("Custom Date Range")
        col1, col2 = st.columns(2)
        with col1:
            date_from = st.date_input(
                "From",
                value=datetime(2000, 1, 1),
                max_value=datetime.now()
            )
        with col2:
            date_to = st.date_input(
                "To",
                value=datetime.now(),
                max_value=datetime.now()
            )
    
    st.markdown("---")
    
    # Smart Quick Filters
    st.subheader("🎯 Smart Filters")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🏢 Small New Buildings\n(<30 units)", use_container_width=True):
            st.session_state.quick_filter_type = "small_nb"
        if st.button("🏗️ Large Projects\n(50+ units)", use_container_width=True):
            st.session_state.quick_filter_type = "large"
        if st.button("💥 Active Demo\nSites", use_container_width=True):
            st.session_state.quick_filter_type = "demo"
        if st.button("⏰ Expiring Soon\n(30 days)", use_container_width=True):
            st.session_state.quick_filter_type = "expiring_soon"
    
    with col2:
        if st.button("🏘️ Multi-Family\nRenovations", use_container_width=True):
            st.session_state.quick_filter_type = "multifamily_alt"
        if st.button("🏠 Single-Family\nHomes", use_container_width=True):
            st.session_state.quick_filter_type = "single_family"
        if st.button("🆕 Recently Issued\n(30 days)", use_container_width=True):
            st.session_state.quick_filter_type = "recent"
        if st.button("📋 Show All", use_container_width=True):
            st.session_state.quick_filter_type = None
    
    st.markdown("---")
    
    st.subheader("Manual Filters")
    show_only_with_contacts = st.checkbox("Only show leads with phone numbers", value=True)
    
    # Mobile filter
    only_mobile = st.checkbox("📱 Mobile numbers only", value=False)
    
    # Contact count filter
    min_contacts = st.slider("Minimum contacts", 0, 5, 0)
    
    # Lead score filter
    min_lead_score = st.slider("Minimum lead score", 0, 100, 0, 5, 
                                help="Filter by lead quality (70+ = 🔥 Hot, 50+ = ⚡ Warm, 30+ = 💡 Cold). Score: Recency (40pts) + Contacts+Mobile (40pts) + Permit Type (10pts) + Project Size (10pts)")
    
    # Permit status filter
    permit_status = st.multiselect(
        "Permit Status",
        ["Active", "Expired", "Expiring Soon"],
        default=["Active"]
    )
    
    # Building units filter - intelligent slider
    units_min_db, units_max_db = get_units_range()
    st.markdown(f"**Total Units** (Range: {units_min_db}-{units_max_db})")
    units_range = st.slider(
        "Select units range",
        min_value=units_min_db,
        max_value=units_max_db,
        value=(units_min_db, units_max_db),
        help=f"Filter by total units ({units_min_db} to {units_max_db})"
    )
    filter_min_units = units_range[0] if units_range[0] > units_min_db else None
    filter_max_units = units_range[1] if units_range[1] < units_max_db else None
    
    # Building stories filter - intelligent slider
    stories_min_db, stories_max_db = get_stories_range()
    st.markdown(f"**Building Stories** (Range: {stories_min_db}-{stories_max_db})")
    stories_range = st.slider(
        "Select stories range",
        min_value=stories_min_db,
        max_value=stories_max_db,
        value=(stories_min_db, stories_max_db),
        help=f"Filter by number of stories ({stories_min_db} to {stories_max_db})"
    )
    min_stories = stories_range[0] if stories_range[0] > stories_min_db else None
    max_stories = stories_range[1] if stories_range[1] < stories_max_db else None
    
    st.subheader("Permit Type")
    permit_types = get_permit_types()
    selected_permit_type = st.selectbox("Select Permit Type", permit_types)
    
    st.markdown("---")
    
    st.subheader("Actions")
    col1, col2 = st.columns(2)
    with col1:
        refresh_button = st.button("🔄 Refresh", use_container_width=True)
    with col2:
        if st.button("🗑️ Clear", use_container_width=True):
            st.session_state.quick_filter_type = None
            st.cache_data.clear()
            st.rerun()

# Clear cache if refresh button is clicked
if refresh_button:
    st.cache_data.clear()
    st.rerun()

# Apply smart filters
filter_permit_type = selected_permit_type if selected_permit_type != "All" else None
# Use slider values as base, smart filters can override
smart_filter_max_units = None
smart_filter_min_units = None
filter_has_units_info = False
filter_single_family = False
filter_multi_family = False
filter_permit_status_override = None

if st.session_state.quick_filter_type == "small_nb":
    filter_permit_type = "NB"
    smart_filter_max_units = 30
elif st.session_state.quick_filter_type == "large":
    smart_filter_min_units = 50
elif st.session_state.quick_filter_type == "demo":
    filter_permit_type = "DM"
    # Recent demolitions are hot leads
    date_from = datetime.now() - timedelta(days=60)
elif st.session_state.quick_filter_type == "multifamily_alt":
    filter_permit_type = "AL"
    filter_multi_family = True  # 2+ units
elif st.session_state.quick_filter_type == "single_family":
    filter_single_family = True  # 1 unit or residential use
elif st.session_state.quick_filter_type == "expiring_soon":
    filter_permit_status_override = ["Expiring Soon"]
elif st.session_state.quick_filter_type == "recent":
    # Recently issued permits (last 30 days)
    date_from = datetime.now() - timedelta(days=30)
    date_to = datetime.now()

# Combine smart filter overrides with slider values
# Smart filters take precedence if set
final_min_units = smart_filter_min_units if smart_filter_min_units is not None else filter_min_units
final_max_units = smart_filter_max_units if smart_filter_max_units is not None else filter_max_units

# Pagination controls
st.sidebar.markdown("---")
st.sidebar.subheader("📄 Results Per Page")
results_per_page = st.sidebar.selectbox(
    "Show results",
    options=[20, 50, 100, 250, 500, 1000],
    index=2,  # Default to 100 (3rd option)
    help="Adjust how many permits to display per page"
)

# Initialize page number in session state
if 'page_number' not in st.session_state:
    st.session_state.page_number = 0

# Count total results first
total_permits = count_permits(
    permit_type=filter_permit_type,
    date_from=date_from,
    date_to=date_to,
    has_contacts=show_only_with_contacts,
    max_units=final_max_units,
    min_units=final_min_units,
    has_units_info=filter_has_units_info,
    single_family=filter_single_family,
    multi_family=filter_multi_family,
    min_contacts=min_contacts if min_contacts > 0 else None,
    only_mobile=only_mobile,
    min_stories=min_stories,
    max_stories=max_stories,
    permit_status=filter_permit_status_override if filter_permit_status_override else (permit_status if permit_status else None)
)

# Calculate pagination
total_pages = max(1, (total_permits + results_per_page - 1) // results_per_page)
if st.session_state.page_number >= total_pages:
    st.session_state.page_number = 0

# Fetch data with pagination
df = fetch_permit_data(
    permit_type=filter_permit_type,
    date_from=date_from,
    date_to=date_to,
    has_contacts=show_only_with_contacts,
    max_units=final_max_units,
    min_units=final_min_units,
    has_units_info=filter_has_units_info,
    single_family=filter_single_family,
    multi_family=filter_multi_family,
    min_contacts=min_contacts if min_contacts > 0 else None,
    only_mobile=only_mobile,
    min_stories=min_stories,
    max_stories=max_stories,
    permit_status=filter_permit_status_override if filter_permit_status_override else (permit_status if permit_status else None),
    limit=results_per_page,
    offset=st.session_state.page_number * results_per_page
)

# Calculate lead scores
if not df.empty:
    df['lead_score'] = df.apply(calculate_lead_score, axis=1)
    df['lead_score'] = df['lead_score'].astype(int)
    
    # Add score breakdown
    df['score_breakdown'] = df.apply(get_score_breakdown, axis=1)
    
    # Add lead quality indicator
    def get_lead_quality(score):
        if score >= 70:
            return "🔥 Hot"
        elif score >= 50:
            return "⚡ Warm"
        elif score >= 30:
            return "💡 Cold"
        else:
            return "❄️ Ice"
    
    df['quality'] = df['lead_score'].apply(get_lead_quality)
    
    # Apply lead score filter
    if min_lead_score > 0:
        df = df[df['lead_score'] >= min_lead_score]
    
    # Sort by lead score
    df = df.sort_values('lead_score', ascending=False)

# ----------------- TAB 1: LEADS DASHBOARD -----------------
with tab1:
    # Main content
    if df.empty:
        st.warning("No permits found matching your filters. Try adjusting the filters.")
    else:
        # Export button at the top - CRM ready format
        # Create CRM-ready contacts CSV
        crm_data = []
        for idx, row in df.iterrows():
            if row['contact_names'] and row['contact_phones']:
                names = str(row['contact_names']).split(' | ')
                phones = str(row['contact_phones']).split(' | ')
                for name, phone in zip(names, phones):
                    crm_data.append({
                        'Contact_Name': name,
                        'Phone': phone,
                        'Company': row['applicant'],
                        'Property_Address': row['address'],
                        'Job_Type': row['job_type'],
                        'Permit_Number': row['permit_no'],
                        'Issue_Date': str(row['issue_date']),
                        'Stories': row['stories'] if row['stories'] else '',
                        'Total_Units': row['total_units'] if row['total_units'] else '',
                        'Use_Type': row['use_type'] if row['use_type'] else '',
                        'Permit_Link': row['link'],
                        'Call_Status': '',  # Empty field for tracking calls
                        'Call_Date': '',    # Empty field for tracking when called
                        'Notes': ''         # Empty field for notes
                    })
        
        if crm_data:
            crm_df = pd.DataFrame(crm_data)
            crm_csv = crm_df.to_csv(index=False)
            st.download_button(
                label=f"📞 Download {len(crm_data)} Contacts for CRM ({len(df)} Properties)",
                data=crm_csv,
                file_name=f"crm_contacts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
                use_container_width=True,
                type="primary"
            )
        else:
            st.info("No contacts available to download")
        
        st.markdown("---")
        
        # Summary metrics
        col1, col2, col3, col4, col5 = st.columns(5)
        
        with col1:
            st.metric("Total Leads", len(df))
        
        with col2:
            total_contacts = df['contact_count'].sum()
            st.metric("Total Contacts", int(total_contacts))
        
        with col3:
            avg_score = df['lead_score'].mean()
            st.metric("Avg Lead Score", f"{avg_score:.0f}")
        
        with col4:
            hot_leads = len(df[df['lead_score'] >= 70])
            st.metric("🔥 Hot Leads", hot_leads)
        
        with col5:
            with_mobile = df['mobile_count'].sum()
            st.metric("📱 Mobile #s", int(with_mobile))
        
        st.markdown("---")
        
        # Search box
        search_term = st.text_input("🔎 Search by Address, Applicant, or Permit #", "")
        
        if search_term:
            mask = (
                df['address'].str.contains(search_term, case=False, na=False) |
                df['applicant'].str.contains(search_term, case=False, na=False) |
                df['permit_no'].str.contains(search_term, case=False, na=False)
            )
            df = df[mask]
        
        st.subheader(f"Showing {len(df)} Leads")
        
        # Display permits as expandable cards
        for idx, row in df.iterrows():
            lead_quality_badge = row['quality']
            score = row['lead_score']
            
            with st.expander(
                f"{lead_quality_badge} **{row['address']}** | Score: {score} | {row['contact_count']} Contact(s)",
                expanded=False
            ):
                col1, col2 = st.columns([2, 1])
                
                with col1:
                    st.markdown(f"### 📍 Property Information")
                    
                    # Lead score with breakdown in columns
                    score_col1, score_col2 = st.columns([3, 1])
                    with score_col1:
                        st.markdown(f"**Lead Score:** {row['lead_score']} - {row['quality']}")
                    with score_col2:
                        st.button("ℹ️", key=f"info_{row['id']}", help=row['score_breakdown'], 
                                 use_container_width=False)
                    
                    st.markdown(f"**Address:** {row['address']}")
                    st.markdown(f"**Applicant:** {row['applicant']}")
                    st.markdown(f"**Permit Number:** `{row['permit_no']}`")
                    st.markdown(f"**Job Type:** {row['job_type']}")
                    
                    if row['use_type']:
                        st.markdown(f"**Use Type:** {row['use_type']}")
                    
                    col_a, col_b, col_c = st.columns(3)
                    with col_a:
                        if row['stories']:
                            st.metric("Stories", row['stories'])
                    with col_b:
                        if row['total_units']:
                            st.metric("Total Units", row['total_units'])
                    with col_c:
                        if row['occupied_units']:
                            st.metric("Occupied Units", row['occupied_units'])
                    
                    st.markdown(f"**BIN:** {row['bin']}")
                    st.markdown(f"**Issue Date:** {row['issue_date']}")
                    st.markdown(f"**Expiration Date:** {row['exp_date']}")
                    
                with col2:
                    st.markdown(f"### 📞 Contact Information")
                    
                    if row['contact_names'] and row['contact_phones']:
                        names = str(row['contact_names']).split(' | ')
                        phones = str(row['contact_phones']).split(' | ')
                        
                        for name, phone in zip(names, phones):
                            st.markdown(f"""
                            <div class='contact-card'>
                                <strong>👤 {name}</strong><br>
                                📱 <a href='tel:{phone}' style='color: #4a9eff; text-decoration: none;'>{phone}</a>
                            </div>
                            """, unsafe_allow_html=True)
                    else:
                        st.info("No contacts available")
                    
                    st.markdown("---")
                    st.markdown(f"[🔗 View Permit Details]({row['link']})")
        
        # Pagination controls at the bottom
        st.markdown("---")
        col1, col2, col3, col4, col5 = st.columns([1, 1, 2, 1, 1])
        
        with col1:
            if st.button("⏮️ First", disabled=st.session_state.page_number == 0):
                st.session_state.page_number = 0
                st.rerun()
        
        with col2:
            if st.button("◀️ Previous", disabled=st.session_state.page_number == 0):
                st.session_state.page_number -= 1
                st.rerun()
        
        with col3:
            st.markdown(f"<div style='text-align: center; padding: 8px;'><strong>Page {st.session_state.page_number + 1} of {total_pages}</strong><br><small>Showing {len(df)} of {total_permits} permits</small></div>", unsafe_allow_html=True)
        
        with col4:
            if st.button("Next ▶️", disabled=st.session_state.page_number >= total_pages - 1):
                st.session_state.page_number += 1
                st.rerun()
        
        with col5:
            if st.button("Last ⏭️", disabled=st.session_state.page_number >= total_pages - 1):
                st.session_state.page_number = total_pages - 1
                st.rerun()

# ----------------- TAB 2: VISUALIZATIONS -----------------
with tab2:
    st.header("📈 Permit Visual Insights")
    
    if df.empty:
        st.warning("No data available to visualize. Adjust your filters in the Leads Dashboard tab.")
    else:
        # Permits by Job Type
        with st.expander("📊 Permits by Job Type", expanded=True):
            job_counts = df["job_type"].value_counts().reset_index()
            job_counts.columns = ["Job Type", "Count"]
            fig = px.bar(
                job_counts, 
                x="Job Type", 
                y="Count", 
                title="Permits by Job Type",
                color="Job Type",
                color_discrete_sequence=px.colors.qualitative.Set3
            )
            fig.update_layout(
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                font_color='white'
            )
            st.plotly_chart(fig, use_container_width=True)
        
        # Top Applicants
        with st.expander("🏢 Top 10 Applicants by Permit Count", expanded=True):
            top_applicants = df["applicant"].value_counts().head(10).reset_index()
            top_applicants.columns = ["Applicant", "Count"]
            fig = px.pie(
                top_applicants, 
                names="Applicant", 
                values="Count", 
                title="Top 10 Applicants",
                color_discrete_sequence=px.colors.sequential.Blues_r
            )
            fig.update_layout(
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                font_color='white'
            )
            st.plotly_chart(fig, use_container_width=True)
        
        # Permit Trends Over Time
        with st.expander("📅 Permit Trends Over Time", expanded=True):
            df_trends = df.copy()
            df_trends["issue_month"] = pd.to_datetime(df_trends["issue_date"]).dt.to_period("M").astype(str)
            monthly_counts = df_trends["issue_month"].value_counts().sort_index().reset_index()
            monthly_counts.columns = ["Month", "Permits"]
            fig = px.line(
                monthly_counts, 
                x="Month", 
                y="Permits", 
                title="Permit Trends Over Time",
                markers=True
            )
            fig.update_layout(
                plot_bgcolor='rgba(0,0,0,0)',
                paper_bgcolor='rgba(0,0,0,0)',
                font_color='white'
            )
            fig.update_traces(line_color='#4a9eff')
            st.plotly_chart(fig, use_container_width=True)
        
        # Distribution of Building Stories
        with st.expander("🏗️ Distribution of Building Stories", expanded=True):
            df_stories = df.copy()
            df_stories["stories"] = pd.to_numeric(df_stories["stories"], errors="coerce")
            df_stories = df_stories[df_stories["stories"].notna() & (df_stories["stories"] > 0)]
            
            if not df_stories.empty:
                fig = px.histogram(
                    df_stories, 
                    x="stories", 
                    nbins=20, 
                    title="Distribution of Building Stories",
                    color_discrete_sequence=['#4a9eff']
                )
                fig.update_layout(
                    plot_bgcolor='rgba(0,0,0,0)',
                    paper_bgcolor='rgba(0,0,0,0)',
                    font_color='white',
                    xaxis_title="Number of Stories",
                    yaxis_title="Count"
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No story data available for visualization")
        
        # Distribution of Total Units
        with st.expander("🏘️ Distribution of Total Units", expanded=True):
            df_units = df.copy()
            df_units["total_units"] = pd.to_numeric(df_units["total_units"], errors="coerce")
            df_units = df_units[df_units["total_units"].notna() & (df_units["total_units"] > 0)]
            
            if not df_units.empty:
                fig = px.histogram(
                    df_units, 
                    x="total_units", 
                    nbins=30, 
                    title="Distribution of Total Units",
                    color_discrete_sequence=['#1e3a5f']
                )
                fig.update_layout(
                    plot_bgcolor='rgba(0,0,0,0)',
                    paper_bgcolor='rgba(0,0,0,0)',
                    font_color='white',
                    xaxis_title="Total Units",
                    yaxis_title="Count"
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No unit data available for visualization")

# ----------------- TAB 3: MAP VIEW -----------------
with tab3:
    st.header("🗺️ Interactive Permit Map")
    
    # Lazy loading - only render map when tab is active
    # Using st.session_state to track if map has been loaded
    if 'map_loaded' not in st.session_state:
        st.session_state.map_loaded = False
    
    # Show a button to load the map (lazy loading)
    if not st.session_state.map_loaded:
        st.info("📍 Click below to load the interactive map")
        if st.button("🗺️ Load Map", key="load_map_button"):
            st.session_state.map_loaded = True
            st.rerun()
    
    # Only render map if user has clicked to load it
    if st.session_state.map_loaded:
        # Check if we have lat/lon data
        if df.empty:
            st.warning("No data to display. Adjust your filters in the Leads Dashboard tab.")
        else:
            df_map = df.dropna(subset=["latitude", "longitude"]).copy()
            
            if df_map.empty:
                st.info("No geocoded permits available in the current filter. Try adjusting your filters to see more permits on the map.")
            else:
                # Convert to numeric
                df_map["latitude"] = pd.to_numeric(df_map["latitude"], errors="coerce")
                df_map["longitude"] = pd.to_numeric(df_map["longitude"], errors="coerce")
                df_map = df_map.dropna(subset=["latitude", "longitude"])
                
                if not df_map.empty:
                    with st.spinner("🗺️ Loading map..."):
                        # Calculate map center
                        center_lat = df_map["latitude"].mean()
                        center_lon = df_map["longitude"].mean()
                        
                        fig = px.scatter_mapbox(
                            df_map,
                            lat="latitude",
                            lon="longitude",
                            color="job_type",
                            hover_name="applicant",
                            hover_data={
                                "address": True,
                                "job_type": True,
                                "total_units": True,
                                "stories": True,
                                "permit_no": True,
                                "latitude": False,
                                "longitude": False
                            },
                            zoom=10,
                            center={"lat": center_lat, "lon": center_lon},
                            height=800,
                            title="Permit Locations by Type"
                        )
                        
                        fig.update_layout(
                            mapbox_style="carto-darkmatter",
                            margin={"r": 0, "t": 40, "l": 0, "b": 0},
                            font_color='white'
                        )
                        st.plotly_chart(fig, use_container_width=True)
                        
                        # Show map statistics
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("Geocoded Permits", len(df_map))
                        with col2:
                            st.metric("Unique Locations", df_map[["latitude", "longitude"]].drop_duplicates().shape[0])
                        with col3:
                            st.metric("Job Types", df_map["job_type"].nunique())
                else:
                    st.warning("Geocoded data is invalid or out of range.")

# Footer
st.markdown("---")
st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
