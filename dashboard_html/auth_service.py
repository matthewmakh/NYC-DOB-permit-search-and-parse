"""
Authentication Service Module
Handles user registration, login, sessions, and email verification
"""

import os
import secrets
import hashlib
from datetime import datetime, timedelta
from functools import wraps
from flask import request, redirect, url_for, session, jsonify, g
import psycopg2
from psycopg2.extras import RealDictCursor

# Session duration: 48 hours
SESSION_DURATION_HOURS = 48
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', 'matt@tyeny.com')


def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        cursor_factory=RealDictCursor
    )


def hash_password(password):
    """Hash password using SHA-256 with salt"""
    salt = secrets.token_hex(16)
    password_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    return f"{salt}${password_hash}"


def verify_password(password, stored_hash):
    """Verify password against stored hash"""
    try:
        salt, hash_value = stored_hash.split('$')
        return hashlib.sha256((password + salt).encode()).hexdigest() == hash_value
    except:
        return False


def generate_token():
    """Generate a secure random token"""
    return secrets.token_urlsafe(32)


def create_user(email, password):
    """
    Create a new user account
    Returns: (success, message, user_id)
    """
    email = email.lower().strip()
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Check if email already exists
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            return False, "Email already registered", None
        
        # Create user
        password_hash = hash_password(password)
        verification_token = generate_token()
        verification_expires = datetime.now() + timedelta(hours=24)
        is_admin = (email == ADMIN_EMAIL)
        
        cur.execute("""
            INSERT INTO users (
                email, password_hash, is_admin, is_verified,
                verification_token, verification_token_expires,
                subscription_status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            email, password_hash, is_admin, True,  # Auto-verify all accounts
            verification_token, verification_expires,
            'active' if is_admin else 'inactive'
        ))
        
        user_id = cur.fetchone()['id']
        conn.commit()
        
        return True, verification_token, user_id
        
    except Exception as e:
        conn.rollback()
        return False, str(e), None
    finally:
        cur.close()
        conn.close()


def verify_email(token):
    """
    Verify user email with token
    Returns: (success, message)
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT id, verification_token_expires 
            FROM users 
            WHERE verification_token = %s AND is_verified = FALSE
        """, (token,))
        
        user = cur.fetchone()
        if not user:
            return False, "Invalid or expired verification link"
        
        if user['verification_token_expires'] < datetime.now():
            return False, "Verification link has expired"
        
        cur.execute("""
            UPDATE users 
            SET is_verified = TRUE, verification_token = NULL 
            WHERE id = %s
        """, (user['id'],))
        
        conn.commit()
        return True, "Email verified successfully"
        
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        cur.close()
        conn.close()


def authenticate_user(email, password):
    """
    Authenticate user with email and password
    Returns: (success, message, user_dict)
    """
    email = email.lower().strip()
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT id, email, password_hash, is_admin, is_verified,
                   stripe_customer_id, subscription_status
            FROM users 
            WHERE email = %s
        """, (email,))
        
        user = cur.fetchone()
        
        if not user:
            return False, "Invalid email or password", None
        
        if not verify_password(password, user['password_hash']):
            return False, "Invalid email or password", None
        
        # Email verification check removed - accounts are auto-verified
        
        # Check subscription status (admin bypasses)
        if not user['is_admin'] and user['subscription_status'] != 'active':
            return False, "subscription_required", user
        
        # Update last login
        cur.execute("UPDATE users SET last_login = %s WHERE id = %s", 
                   (datetime.now(), user['id']))
        conn.commit()
        
        return True, "Login successful", dict(user)
        
    except Exception as e:
        return False, str(e), None
    finally:
        cur.close()
        conn.close()


def create_session(user_id, ip_address=None, user_agent=None):
    """
    Create a new session for user
    Returns: session_token
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        session_token = generate_token()
        expires_at = datetime.now() + timedelta(hours=SESSION_DURATION_HOURS)
        
        cur.execute("""
            INSERT INTO user_sessions (user_id, session_token, expires_at, ip_address, user_agent)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, session_token, expires_at, ip_address, user_agent))
        
        conn.commit()
        return session_token
        
    except Exception as e:
        conn.rollback()
        return None
    finally:
        cur.close()
        conn.close()


def validate_session(session_token):
    """
    Validate session token and return user if valid
    Returns: user_dict or None
    """
    if not session_token:
        return None
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT u.id, u.email, u.is_admin, u.subscription_status,
                   u.stripe_customer_id, s.expires_at
            FROM user_sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.session_token = %s AND s.expires_at > %s
        """, (session_token, datetime.now()))
        
        result = cur.fetchone()
        if result:
            return dict(result)
        return None
        
    except:
        return None
    finally:
        cur.close()
        conn.close()


def destroy_session(session_token):
    """Delete a session (logout)"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("DELETE FROM user_sessions WHERE session_token = %s", (session_token,))
        conn.commit()
    except:
        conn.rollback()
    finally:
        cur.close()
        conn.close()


def cleanup_expired_sessions():
    """Remove all expired sessions"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("DELETE FROM user_sessions WHERE expires_at < %s", (datetime.now(),))
        conn.commit()
    except:
        conn.rollback()
    finally:
        cur.close()
        conn.close()


def get_user_by_id(user_id):
    """Get user by ID"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT id, email, is_admin, is_verified, subscription_status,
                   stripe_customer_id, stripe_subscription_id, created_at, last_login
            FROM users WHERE id = %s
        """, (user_id,))
        
        result = cur.fetchone()
        return dict(result) if result else None
    finally:
        cur.close()
        conn.close()


def get_user_by_email(email):
    """Get user by email"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT id, email, is_admin, is_verified, subscription_status,
                   stripe_customer_id, stripe_subscription_id
            FROM users WHERE email = %s
        """, (email.lower().strip(),))
        
        result = cur.fetchone()
        return dict(result) if result else None
    finally:
        cur.close()
        conn.close()


def update_user_stripe_info(user_id, customer_id=None, subscription_id=None, status=None):
    """Update user's Stripe information"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        updates = []
        values = []
        
        if customer_id is not None:
            updates.append("stripe_customer_id = %s")
            values.append(customer_id)
        
        if subscription_id is not None:
            updates.append("stripe_subscription_id = %s")
            values.append(subscription_id)
        
        if status is not None:
            updates.append("subscription_status = %s")
            values.append(status)
            if status == 'active':
                updates.append("subscription_started_at = %s")
                values.append(datetime.now())
        
        if updates:
            values.append(user_id)
            cur.execute(f"""
                UPDATE users SET {', '.join(updates)} WHERE id = %s
            """, values)
            conn.commit()
            
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()


# Flask decorator for requiring login
def login_required(f):
    """Decorator to require login for a route"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        session_token = session.get('session_token')
        user = validate_session(session_token)
        
        if not user:
            # Clear invalid session
            session.pop('session_token', None)
            
            # Check if API request
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            
            return redirect(url_for('auth.login', next=request.url))
        
        # Store user in g for access in route
        g.user = user
        return f(*args, **kwargs)
    
    return decorated_function


def admin_required(f):
    """Decorator to require admin access"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        session_token = session.get('session_token')
        user = validate_session(session_token)
        
        if not user or not user.get('is_admin'):
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Admin access required'}), 403
            return redirect(url_for('auth.login'))
        
        g.user = user
        return f(*args, **kwargs)
    
    return decorated_function
