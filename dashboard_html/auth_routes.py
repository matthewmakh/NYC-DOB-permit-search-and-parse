"""
Authentication Routes Blueprint
Handles login, signup, logout, email verification, and Stripe integration
"""

import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from auth_service import (
    create_user, authenticate_user, create_session, destroy_session,
    verify_email, get_user_by_email, validate_session
)
from stripe_service import create_checkout_session, handle_subscription_webhook
import stripe

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page and handler"""
    # Check if already logged in
    session_token = session.get('session_token')
    if session_token and validate_session(session_token):
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        
        success, message, user = authenticate_user(email, password)
        
        if success:
            # Create session
            session_token = create_session(
                user['id'],
                ip_address=request.remote_addr,
                user_agent=request.user_agent.string[:500]
            )
            session['session_token'] = session_token
            session.permanent = True
            
            # Redirect to intended page or home
            next_url = request.args.get('next', '/')
            return redirect(next_url)
        
        elif message == 'subscription_required':
            # User exists but no subscription - redirect to checkout
            flash('Please complete your subscription to access the platform.', 'warning')
            return redirect(url_for('auth.complete_subscription', email=email))
        
        else:
            flash(message, 'error')
    
    return render_template('login.html')


@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    """Signup page and handler"""
    if request.method == 'GET':
        return render_template('signup.html')
    
    # POST - JSON API for signup
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    
    if not email or not password:
        return jsonify({'success': False, 'error': 'Email and password required'})
    
    if len(password) < 8:
        return jsonify({'success': False, 'error': 'Password must be at least 8 characters'})
    
    # Check if this is the admin email
    admin_email = os.getenv('ADMIN_EMAIL', 'matt@tyeny.com')
    is_admin = (email == admin_email)
    
    # Create user account
    success, result, user_id = create_user(email, password)
    
    if not success:
        return jsonify({'success': False, 'error': result})
    
    if is_admin:
        # Admin gets direct access without Stripe
        session_token = create_session(user_id, request.remote_addr, request.user_agent.string[:500])
        session['session_token'] = session_token
        session.permanent = True
        return jsonify({'success': True, 'is_admin': True})
    
    # Create Stripe checkout session for regular users
    try:
        checkout = create_checkout_session(
            user_id=user_id,
            email=email,
            success_url=url_for('auth.subscription_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('auth.subscription_cancelled', _external=True)
        )
        
        return jsonify({
            'success': True,
            'checkout_url': checkout.url
        })
        
    except Exception as e:
        print(f"Stripe checkout error: {e}")
        return jsonify({'success': False, 'error': 'Failed to create checkout session'})


@auth_bp.route('/complete-subscription')
def complete_subscription():
    """Redirect existing unsubscribed users to Stripe"""
    email = request.args.get('email')
    if not email:
        return redirect(url_for('auth.login'))
    
    user = get_user_by_email(email)
    if not user:
        flash('Account not found', 'error')
        return redirect(url_for('auth.signup'))
    
    if user['subscription_status'] == 'active':
        flash('Your subscription is already active!', 'success')
        return redirect(url_for('auth.login'))
    
    try:
        checkout = create_checkout_session(
            user_id=user['id'],
            email=email,
            success_url=url_for('auth.subscription_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('auth.subscription_cancelled', _external=True)
        )
        return redirect(checkout.url)
    except Exception as e:
        flash('Failed to create checkout session', 'error')
        return redirect(url_for('auth.login'))


@auth_bp.route('/subscription-success')
def subscription_success():
    """Handle successful subscription"""
    session_id = request.args.get('session_id')
    
    if session_id:
        try:
            # Retrieve checkout session to get user info
            checkout = stripe.checkout.Session.retrieve(session_id)
            user_id = checkout.metadata.get('user_id')
            
            if user_id:
                # Create login session
                session_token = create_session(
                    int(user_id),
                    request.remote_addr,
                    request.user_agent.string[:500]
                )
                session['session_token'] = session_token
                session.permanent = True
                
                flash('Welcome! Your subscription is now active.', 'success')
                return redirect(url_for('home'))
        except Exception as e:
            print(f"Error retrieving checkout session: {e}")
    
    flash('Subscription successful! Please log in.', 'success')
    return redirect(url_for('auth.login'))


@auth_bp.route('/subscription-cancelled')
def subscription_cancelled():
    """Handle cancelled subscription attempt"""
    flash('Subscription was cancelled. You need an active subscription to access the platform.', 'warning')
    return redirect(url_for('auth.login'))


@auth_bp.route('/verify/<token>')
def verify(token):
    """Verify email with token"""
    success, message = verify_email(token)
    
    if success:
        flash(message, 'success')
    else:
        flash(message, 'error')
    
    return redirect(url_for('auth.login'))


@auth_bp.route('/resend-verification')
def resend_verification():
    """Resend verification email"""
    # TODO: Implement email sending
    flash('Verification email resent. Please check your inbox.', 'success')
    return redirect(url_for('auth.login'))


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Password reset request"""
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        # TODO: Implement password reset
        flash('If an account exists with that email, a reset link has been sent.', 'success')
        return redirect(url_for('auth.login'))
    
    return render_template('forgot_password.html')


@auth_bp.route('/logout')
def logout():
    """Logout user"""
    session_token = session.get('session_token')
    if session_token:
        destroy_session(session_token)
    
    session.pop('session_token', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('auth.login'))


@auth_bp.route('/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhooks"""
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError:
        return 'Invalid signature', 400
    
    # Handle the event
    handle_subscription_webhook(event)
    
    return 'Success', 200


@auth_bp.route('/check')
def check_auth():
    """API endpoint to check authentication status"""
    session_token = session.get('session_token')
    user = validate_session(session_token) if session_token else None
    
    if user:
        return jsonify({
            'authenticated': True,
            'user': {
                'email': user['email'],
                'is_admin': user['is_admin'],
                'subscription_status': user['subscription_status']
            }
        })
    
    return jsonify({'authenticated': False})
