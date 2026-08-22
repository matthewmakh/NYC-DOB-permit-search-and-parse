"""
Activity Logging Service
Comprehensive activity tracking for admin monitoring and analytics
"""

import os
import json
import threading
from datetime import datetime
from functools import wraps
from flask import request, session, g
import psycopg2
from psycopg2.extras import RealDictCursor, Json
import re


# Activity Categories
class ActivityCategory:
    AUTH = 'auth'           # Login, logout, signup, password changes
    NAVIGATION = 'navigation'  # Page views, section visits
    INTERACTION = 'interaction'  # Clicks, form submissions
    DATA = 'data'           # Searches, exports, API calls
    SYSTEM = 'system'       # Errors, background tasks
    ADMIN = 'admin'         # Admin-specific actions


# Activity Types
class ActivityType:
    # Auth
    LOGIN = 'login'
    LOGIN_FAILED = 'login_failed'
    LOGOUT = 'logout'
    SIGNUP = 'signup'
    SIGNUP_FAILED = 'signup_failed'
    PASSWORD_RESET_REQUEST = 'password_reset_request'
    PASSWORD_CHANGED = 'password_changed'
    EMAIL_VERIFIED = 'email_verified'
    SESSION_EXPIRED = 'session_expired'
    
    # Navigation
    PAGE_VIEW = 'page_view'
    SECTION_CLICK = 'section_click'
    TAB_SWITCH = 'tab_switch'
    MODAL_OPEN = 'modal_open'
    MODAL_CLOSE = 'modal_close'
    
    # Interaction
    BUTTON_CLICK = 'button_click'
    LINK_CLICK = 'link_click'
    FORM_SUBMIT = 'form_submit'
    FORM_ERROR = 'form_error'
    FILTER_CHANGE = 'filter_change'
    SORT_CHANGE = 'sort_change'
    
    # Data
    SEARCH = 'search'
    EXPORT = 'export'
    DOWNLOAD = 'download'
    API_CALL = 'api_call'
    DATA_VIEW = 'data_view'
    PERMIT_VIEW = 'permit_view'
    BUILDING_VIEW = 'building_view'
    CONTRACTOR_VIEW = 'contractor_view'
    ENRICHMENT_REQUEST = 'enrichment_request'
    
    # System
    ERROR = 'error'
    EXCEPTION = 'exception'
    API_ERROR = 'api_error'
    
    # Admin
    ADMIN_USER_VIEW = 'admin_user_view'
    ADMIN_ACTIVITY_VIEW = 'admin_activity_view'
    ADMIN_SETTINGS_CHANGE = 'admin_settings_change'


def get_db_connection():
    """Get database connection for logging"""
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        cursor_factory=RealDictCursor
    )


def parse_user_agent(user_agent_string):
    """Parse user agent string to extract device/browser info"""
    if not user_agent_string:
        return {
            'device_type': 'unknown',
            'device_os': 'unknown',
            'device_browser': 'unknown',
            'browser_version': 'unknown'
        }
    
    ua = user_agent_string.lower()
    
    # Detect device type
    if 'mobile' in ua or 'android' in ua and 'mobile' in ua:
        device_type = 'mobile'
    elif 'tablet' in ua or 'ipad' in ua:
        device_type = 'tablet'
    else:
        device_type = 'desktop'
    
    # Detect OS
    if 'windows' in ua:
        device_os = 'Windows'
    elif 'mac os' in ua or 'macintosh' in ua:
        device_os = 'macOS'
    elif 'iphone' in ua or 'ipad' in ua:
        device_os = 'iOS'
    elif 'android' in ua:
        device_os = 'Android'
    elif 'linux' in ua:
        device_os = 'Linux'
    else:
        device_os = 'Unknown'
    
    # Detect browser
    browser = 'Unknown'
    browser_version = 'Unknown'
    
    if 'edg/' in ua:
        browser = 'Edge'
        match = re.search(r'edg/(\d+[\.\d]*)', ua)
        if match:
            browser_version = match.group(1)
    elif 'chrome/' in ua and 'safari' in ua:
        browser = 'Chrome'
        match = re.search(r'chrome/(\d+[\.\d]*)', ua)
        if match:
            browser_version = match.group(1)
    elif 'firefox/' in ua:
        browser = 'Firefox'
        match = re.search(r'firefox/(\d+[\.\d]*)', ua)
        if match:
            browser_version = match.group(1)
    elif 'safari/' in ua and 'chrome' not in ua:
        browser = 'Safari'
        match = re.search(r'version/(\d+[\.\d]*)', ua)
        if match:
            browser_version = match.group(1)
    elif 'opera' in ua or 'opr/' in ua:
        browser = 'Opera'
    
    return {
        'device_type': device_type,
        'device_os': device_os,
        'device_browser': browser,
        'browser_version': browser_version
    }


def get_client_ip():
    """Get the real client IP address, handling proxies"""
    # Check X-Forwarded-For header (for proxies/load balancers)
    if request.headers.get('X-Forwarded-For'):
        # First IP in the list is the client
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    elif request.headers.get('X-Real-IP'):
        return request.headers.get('X-Real-IP')
    else:
        return request.remote_addr


def get_geo_info(ip_address):
    """
    Get geolocation info from IP address.
    Uses a free service - can be replaced with a paid one for better accuracy.
    Returns dict with city, region, country, timezone, isp
    """
    # Skip for localhost/private IPs
    if not ip_address or ip_address.startswith(('127.', '192.168.', '10.', '172.16.')):
        return {
            'ip_city': 'Local',
            'ip_region': 'Local',
            'ip_country': 'Local',
            'ip_timezone': None,
            'ip_isp': None
        }
    
    try:
        import requests as http_requests
        # Using ip-api.com (free, 45 requests/minute)
        response = http_requests.get(
            f'http://ip-api.com/json/{ip_address}?fields=city,regionName,country,timezone,isp',
            timeout=2
        )
        if response.status_code == 200:
            data = response.json()
            return {
                'ip_city': data.get('city'),
                'ip_region': data.get('regionName'),
                'ip_country': data.get('country'),
                'ip_timezone': data.get('timezone'),
                'ip_isp': data.get('isp')
            }
    except Exception:
        pass
    
    return {
        'ip_city': None,
        'ip_region': None,
        'ip_country': None,
        'ip_timezone': None,
        'ip_isp': None
    }


def log_activity(
    activity_type,
    activity_category,
    description=None,
    user_id=None,
    user_email=None,
    page_url=None,
    page_name=None,
    element_id=None,
    element_type=None,
    element_text=None,
    search_query=None,
    filter_params=None,
    action_success=True,
    action_result=None,
    error_message=None,
    response_status=None,
    response_time_ms=None,
    endpoint=None,
    http_method=None,
    metadata=None,
    async_log=True,
    include_geo=True
):
    """
    Log an activity to the database.
    
    Args:
        activity_type: Type of activity (use ActivityType constants)
        activity_category: Category of activity (use ActivityCategory constants)
        description: Human-readable description
        user_id: ID of the user (defaults to current user from g.user)
        user_email: Email of the user
        page_url: URL of the page where activity occurred
        page_name: Friendly name of the page
        element_id: ID of the element interacted with
        element_type: Type of element (button, link, etc.)
        element_text: Text content of the element
        search_query: Search query if applicable
        filter_params: Filter parameters as dict
        action_success: Whether the action succeeded
        action_result: Result of the action
        error_message: Error message if action failed
        endpoint: API path or endpoint name (defaults to request.endpoint)
        http_method: HTTP verb (defaults to request.method)
        response_status: HTTP response status code
        response_time_ms: Response time in milliseconds
        metadata: Additional data as dict
        async_log: Whether to log asynchronously (default True)
        include_geo: Whether to fetch geo info (can slow down if sync)
    """
    
    # IMPORTANT: Capture all Flask context data BEFORE starting the thread
    # This prevents "Working outside of application context" errors
    try:
        # Get user info from context if not provided
        _user_id = user_id
        _user_email = user_email
        
        try:
            if hasattr(g, 'user') and g.user:
                if _user_id is None:
                    _user_id = g.user.get('id')
                if _user_email is None:
                    _user_email = g.user.get('email')
        except RuntimeError:
            pass  # Outside request context
        
        # Get session token
        _session_token = None
        try:
            _session_token = session.get('session_token') if session else None
        except RuntimeError:
            pass  # Outside request context
        
        # Get request context
        _page_url = page_url
        _referrer_url = None
        _http_method = http_method
        _endpoint = endpoint
        _request_params = None
        _user_agent = None
        _ip_address = None
        
        try:
            if request:
                _page_url = page_url or request.url
                _referrer_url = request.referrer
                # Explicit values win: log_api_call passes the request path,
                # which is more useful than Flask's endpoint function name.
                _http_method = http_method or request.method
                _endpoint = endpoint or request.endpoint
                
                # Get request params (excluding sensitive data)
                params = {}
                for key, value in request.args.items():
                    if key.lower() not in ['password', 'token', 'secret', 'key']:
                        params[key] = value
                for key, value in request.form.items():
                    if key.lower() not in ['password', 'token', 'secret', 'key']:
                        params[key] = value
                if params:
                    _request_params = params
                
                # Get user agent
                if request.user_agent:
                    _user_agent = request.user_agent.string
                
                # Get IP address
                _ip_address = get_client_ip()
        except RuntimeError:
            pass  # Outside request context
        
        # Parse user agent
        _ua_info = parse_user_agent(_user_agent)
        
    except Exception as e:
        print(f"Activity logging context error: {e}")
        return
    
    def _do_log():
        try:
            # Get geo info (only if requested)
            geo_info = {}
            if include_geo and _ip_address:
                geo_info = get_geo_info(_ip_address)
            
            # Build the log entry
            conn = get_db_connection()
            cur = conn.cursor()
            
            try:
                cur.execute("""
                    INSERT INTO activity_logs (
                        user_id, user_email, session_token,
                        activity_type, activity_category, activity_description,
                        page_url, page_name, referrer_url,
                        element_id, element_type, element_text,
                        search_query, filter_params,
                        action_success, action_result, error_message,
                        http_method, endpoint, request_params,
                        response_status, response_time_ms,
                        ip_address, ip_city, ip_region, ip_country, ip_timezone, ip_isp,
                        user_agent, device_type, device_os, device_browser, browser_version,
                        metadata
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s
                    )
                """, (
                    _user_id, _user_email, _session_token,
                    activity_type, activity_category, description,
                    _page_url, page_name, _referrer_url,
                    element_id, element_type, element_text,
                    search_query, Json(filter_params) if filter_params else None,
                    action_success, action_result, error_message,
                    _http_method, _endpoint, Json(_request_params) if _request_params else None,
                    response_status, response_time_ms,
                    _ip_address, geo_info.get('ip_city'), geo_info.get('ip_region'),
                    geo_info.get('ip_country'), geo_info.get('ip_timezone'), geo_info.get('ip_isp'),
                    _user_agent, _ua_info['device_type'], _ua_info['device_os'],
                    _ua_info['device_browser'], _ua_info['browser_version'],
                    Json(metadata) if metadata else None
                ))
                conn.commit()
            finally:
                cur.close()
                conn.close()
                
        except Exception as e:
            # Don't let logging errors break the app
            print(f"Activity logging error: {e}")
    
    if async_log:
        # Log asynchronously to avoid slowing down requests
        thread = threading.Thread(target=_do_log)
        thread.daemon = True
        thread.start()
    else:
        _do_log()


def log_page_view(page_name, page_url=None, metadata=None):
    """Convenience function for logging page views"""
    log_activity(
        activity_type=ActivityType.PAGE_VIEW,
        activity_category=ActivityCategory.NAVIGATION,
        description=f"Viewed {page_name}",
        page_name=page_name,
        page_url=page_url,
        metadata=metadata
    )


def log_auth_event(activity_type, user_id=None, user_email=None, success=True, error_message=None):
    """Convenience function for logging auth events"""
    descriptions = {
        ActivityType.LOGIN: "User logged in successfully" if success else "Login attempt failed",
        ActivityType.LOGOUT: "User logged out",
        ActivityType.SIGNUP: "User signed up successfully" if success else "Signup attempt failed",
        ActivityType.LOGIN_FAILED: "Login attempt failed",
        ActivityType.SIGNUP_FAILED: "Signup attempt failed",
        ActivityType.PASSWORD_RESET_REQUEST: "Password reset requested",
        ActivityType.PASSWORD_CHANGED: "Password changed",
        ActivityType.EMAIL_VERIFIED: "Email verified",
    }
    
    log_activity(
        activity_type=activity_type,
        activity_category=ActivityCategory.AUTH,
        description=descriptions.get(activity_type, activity_type),
        user_id=user_id,
        user_email=user_email,
        action_success=success,
        error_message=error_message,
        include_geo=True  # Always include geo for auth events
    )


def log_search(query, result_count=None, filter_params=None):
    """Convenience function for logging searches"""
    log_activity(
        activity_type=ActivityType.SEARCH,
        activity_category=ActivityCategory.DATA,
        description=f"Search: {query[:100]}..." if len(query) > 100 else f"Search: {query}",
        search_query=query,
        filter_params=filter_params,
        metadata={'result_count': result_count} if result_count is not None else None
    )


def log_export(export_type, record_count=None, filter_params=None):
    """Convenience function for logging exports"""
    log_activity(
        activity_type=ActivityType.EXPORT,
        activity_category=ActivityCategory.DATA,
        description=f"Exported {record_count or 'unknown'} records as {export_type}",
        filter_params=filter_params,
        metadata={'export_type': export_type, 'record_count': record_count}
    )


def log_click(element_id, element_type='button', element_text=None, page_name=None):
    """Convenience function for logging UI clicks"""
    log_activity(
        activity_type=ActivityType.BUTTON_CLICK if element_type == 'button' else ActivityType.LINK_CLICK,
        activity_category=ActivityCategory.INTERACTION,
        description=f"Clicked {element_type}: {element_text or element_id}",
        element_id=element_id,
        element_type=element_type,
        element_text=element_text,
        page_name=page_name
    )


def log_error(error_message, error_type='error', metadata=None):
    """Convenience function for logging errors"""
    log_activity(
        activity_type=ActivityType.ERROR if error_type == 'error' else ActivityType.EXCEPTION,
        activity_category=ActivityCategory.SYSTEM,
        description=f"Error: {error_message[:200]}",
        action_success=False,
        error_message=error_message,
        metadata=metadata
    )


def log_api_call(endpoint, method='GET', response_status=200, response_time_ms=None, success=True, error=None):
    """Convenience function for logging API calls"""
    log_activity(
        activity_type=ActivityType.API_CALL,
        activity_category=ActivityCategory.DATA,
        description=f"{method} {endpoint}",
        endpoint=endpoint,
        http_method=method,
        response_status=response_status,
        response_time_ms=response_time_ms,
        action_success=success,
        error_message=error
    )


# Decorator for automatic page view logging
def track_page_view(page_name):
    """Decorator to automatically log page views"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            log_page_view(page_name)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# Query functions for admin panel

def get_activity_logs(
    user_id=None,
    user_email=None,
    activity_type=None,
    activity_category=None,
    ip_address=None,
    start_date=None,
    end_date=None,
    page_name=None,
    search_query=None,
    success_only=None,
    limit=100,
    offset=0,
    order_by='created_at',
    order_dir='DESC'
):
    """
    Query activity logs with flexible filtering.
    Returns list of activity log entries.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        conditions = []
        params = []
        
        if user_id:
            conditions.append("user_id = %s")
            params.append(user_id)
        
        if user_email:
            conditions.append("user_email ILIKE %s")
            params.append(f"%{user_email}%")
        
        if activity_type:
            if isinstance(activity_type, list):
                placeholders = ', '.join(['%s'] * len(activity_type))
                conditions.append(f"activity_type IN ({placeholders})")
                params.extend(activity_type)
            else:
                conditions.append("activity_type = %s")
                params.append(activity_type)
        
        if activity_category:
            if isinstance(activity_category, list):
                placeholders = ', '.join(['%s'] * len(activity_category))
                conditions.append(f"activity_category IN ({placeholders})")
                params.extend(activity_category)
            else:
                conditions.append("activity_category = %s")
                params.append(activity_category)
        
        if ip_address:
            conditions.append("ip_address = %s")
            params.append(ip_address)
        
        if start_date:
            conditions.append("created_at >= %s")
            params.append(start_date)
        
        if end_date:
            conditions.append("created_at <= %s")
            params.append(end_date)
        
        if page_name:
            conditions.append("page_name ILIKE %s")
            params.append(f"%{page_name}%")
        
        if search_query:
            conditions.append("(activity_description ILIKE %s OR search_query ILIKE %s)")
            params.extend([f"%{search_query}%", f"%{search_query}%"])
        
        if success_only is not None:
            conditions.append("action_success = %s")
            params.append(success_only)
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        # Validate order_by to prevent SQL injection
        valid_order_columns = ['created_at', 'user_email', 'activity_type', 'activity_category', 'ip_address']
        if order_by not in valid_order_columns:
            order_by = 'created_at'
        
        order_dir = 'DESC' if order_dir.upper() == 'DESC' else 'ASC'
        
        query = f"""
            SELECT * FROM activity_logs
            WHERE {where_clause}
            ORDER BY {order_by} {order_dir}
            LIMIT %s OFFSET %s
        """
        params.extend([limit, offset])
        
        cur.execute(query, params)
        logs = cur.fetchall()
        
        return [dict(row) for row in logs]
        
    finally:
        cur.close()
        conn.close()


def get_activity_stats(start_date=None, end_date=None, user_id=None):
    """Get summary statistics for activity logs"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        conditions = []
        params = []
        
        if start_date:
            conditions.append("created_at >= %s")
            params.append(start_date)
        if end_date:
            conditions.append("created_at <= %s")
            params.append(end_date)
        if user_id:
            conditions.append("user_id = %s")
            params.append(user_id)
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        # Total counts by category
        cur.execute(f"""
            SELECT activity_category, COUNT(*) as count
            FROM activity_logs
            WHERE {where_clause}
            GROUP BY activity_category
            ORDER BY count DESC
        """, params)
        category_counts = {row['activity_category']: row['count'] for row in cur.fetchall()}
        
        # Total counts by type
        cur.execute(f"""
            SELECT activity_type, COUNT(*) as count
            FROM activity_logs
            WHERE {where_clause}
            GROUP BY activity_type
            ORDER BY count DESC
            LIMIT 20
        """, params)
        type_counts = {row['activity_type']: row['count'] for row in cur.fetchall()}
        
        # Unique users
        cur.execute(f"""
            SELECT COUNT(DISTINCT user_id) as unique_users
            FROM activity_logs
            WHERE {where_clause} AND user_id IS NOT NULL
        """, params)
        unique_users = cur.fetchone()['unique_users']
        
        # Unique IPs
        cur.execute(f"""
            SELECT COUNT(DISTINCT ip_address) as unique_ips
            FROM activity_logs
            WHERE {where_clause}
        """, params)
        unique_ips = cur.fetchone()['unique_ips']
        
        # Device breakdown
        cur.execute(f"""
            SELECT device_type, COUNT(*) as count
            FROM activity_logs
            WHERE {where_clause}
            GROUP BY device_type
            ORDER BY count DESC
        """, params)
        device_counts = {row['device_type']: row['count'] for row in cur.fetchall()}
        
        # Browser breakdown
        cur.execute(f"""
            SELECT device_browser, COUNT(*) as count
            FROM activity_logs
            WHERE {where_clause}
            GROUP BY device_browser
            ORDER BY count DESC
            LIMIT 10
        """, params)
        browser_counts = {row['device_browser']: row['count'] for row in cur.fetchall()}
        
        # Failed actions
        cur.execute(f"""
            SELECT COUNT(*) as failed_count
            FROM activity_logs
            WHERE {where_clause} AND action_success = FALSE
        """, params)
        failed_count = cur.fetchone()['failed_count']
        
        # Total count
        cur.execute(f"""
            SELECT COUNT(*) as total
            FROM activity_logs
            WHERE {where_clause}
        """, params)
        total_count = cur.fetchone()['total']
        
        return {
            'total_count': total_count,
            'unique_users': unique_users,
            'unique_ips': unique_ips,
            'failed_count': failed_count,
            'category_counts': category_counts,
            'type_counts': type_counts,
            'device_counts': device_counts,
            'browser_counts': browser_counts
        }
        
    finally:
        cur.close()
        conn.close()


def get_user_activity_timeline(user_id, limit=50):
    """Get recent activity timeline for a specific user"""
    return get_activity_logs(user_id=user_id, limit=limit)


def get_recent_logins(limit=50):
    """Get recent login attempts"""
    return get_activity_logs(
        activity_type=[ActivityType.LOGIN, ActivityType.LOGIN_FAILED],
        limit=limit
    )


def get_recent_errors(limit=50):
    """Get recent errors"""
    return get_activity_logs(
        activity_type=[ActivityType.ERROR, ActivityType.EXCEPTION, ActivityType.API_ERROR],
        limit=limit
    )
