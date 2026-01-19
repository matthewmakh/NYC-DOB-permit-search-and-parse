"""
Stripe Service Module
Handles subscriptions, payments, and per-enrichment charges
"""

import os
import stripe
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

# Initialize Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

# Subscription price: $250/month
SUBSCRIPTION_PRICE = os.getenv('STRIPE_PRICE_ID')
ENRICHMENT_FEE_CENTS = 35  # $0.35


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


def create_customer(email, user_id):
    """
    Create a Stripe customer for the user
    Returns: customer_id
    """
    try:
        customer = stripe.Customer.create(
            email=email,
            metadata={'user_id': str(user_id)}
        )
        
        # Update user with customer ID
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET stripe_customer_id = %s WHERE id = %s",
            (customer.id, user_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        
        return customer.id
        
    except Exception as e:
        print(f"Error creating Stripe customer: {e}")
        raise e


def create_checkout_session(user_id, email, success_url, cancel_url):
    """
    Create a Stripe Checkout session for subscription signup
    Returns: checkout_session
    """
    try:
        # Get or create customer
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT stripe_customer_id FROM users WHERE id = %s", (user_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        customer_id = result['stripe_customer_id'] if result else None
        
        if not customer_id:
            customer_id = create_customer(email, user_id)
        
        # Create checkout session
        checkout_session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=['card'],
            line_items=[{
                'price': SUBSCRIPTION_PRICE,
                'quantity': 1
            }],
            mode='subscription',
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                'user_id': str(user_id)
            },
            # Save payment method for future charges
            payment_method_collection='always',
            subscription_data={
                'metadata': {'user_id': str(user_id)}
            }
        )
        
        return checkout_session
        
    except Exception as e:
        print(f"Error creating checkout session: {e}")
        raise e


def create_setup_intent(customer_id):
    """
    Create a SetupIntent for saving payment method without immediate charge
    """
    try:
        setup_intent = stripe.SetupIntent.create(
            customer=customer_id,
            payment_method_types=['card'],
            usage='off_session'
        )
        return setup_intent
    except Exception as e:
        print(f"Error creating setup intent: {e}")
        raise e


def charge_enrichment_fee(user_id, building_id, owner_name):
    """
    Charge $0.35 for enrichment lookup
    Returns: (success, message, charge_id)
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Get user's Stripe info
        cur.execute("""
            SELECT stripe_customer_id, subscription_status, is_admin
            FROM users WHERE id = %s
        """, (user_id,))
        
        user = cur.fetchone()
        if not user:
            return False, "User not found", None
        
        # Admin doesn't pay
        if user['is_admin']:
            return True, "Admin bypass", "admin_free"
        
        if user['subscription_status'] != 'active':
            return False, "Active subscription required", None
        
        if not user['stripe_customer_id']:
            return False, "No payment method on file", None
        
        # Get the customer's default payment method
        customer = stripe.Customer.retrieve(user['stripe_customer_id'])
        
        if not customer.invoice_settings.default_payment_method:
            # Try to get payment method from subscriptions
            subscriptions = stripe.Subscription.list(
                customer=user['stripe_customer_id'],
                status='active',
                limit=1
            )
            
            if subscriptions.data:
                payment_method = subscriptions.data[0].default_payment_method
            else:
                return False, "No payment method on file", None
        else:
            payment_method = customer.invoice_settings.default_payment_method
        
        # Create the charge
        payment_intent = stripe.PaymentIntent.create(
            amount=ENRICHMENT_FEE_CENTS,
            currency='usd',
            customer=user['stripe_customer_id'],
            payment_method=payment_method,
            off_session=True,
            confirm=True,
            description=f"Owner enrichment for building ID {building_id}",
            metadata={
                'user_id': str(user_id),
                'building_id': str(building_id),
                'owner_name': owner_name[:100] if owner_name else '',
                'type': 'enrichment_fee'
            }
        )
        
        # Record the transaction
        cur.execute("""
            INSERT INTO enrichment_transactions 
            (user_id, building_id, transaction_type, amount, stripe_payment_intent_id, status, description)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id, building_id, 'enrichment', 0.35, 
            payment_intent.id, payment_intent.status,
            f"Enrichment for: {owner_name}"
        ))
        
        conn.commit()
        
        return True, "Payment successful", payment_intent.id
        
    except stripe.error.CardError as e:
        conn.rollback()
        return False, f"Card declined: {e.user_message}", None
        
    except Exception as e:
        conn.rollback()
        print(f"Error charging enrichment fee: {e}")
        return False, str(e), None
        
    finally:
        cur.close()
        conn.close()


def handle_subscription_webhook(event):
    """
    Handle Stripe webhook events for subscriptions
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        event_type = event['type']
        data = event['data']['object']
        
        if event_type == 'checkout.session.completed':
            # User completed checkout
            user_id = data.get('metadata', {}).get('user_id')
            customer_id = data.get('customer')
            subscription_id = data.get('subscription')
            
            if user_id and subscription_id:
                cur.execute("""
                    UPDATE users SET 
                        stripe_customer_id = %s,
                        stripe_subscription_id = %s,
                        subscription_status = 'active',
                        subscription_started_at = %s,
                        is_verified = TRUE
                    WHERE id = %s
                """, (customer_id, subscription_id, datetime.now(), user_id))
                
        elif event_type == 'customer.subscription.updated':
            subscription_id = data.get('id')
            status = data.get('status')
            
            # Map Stripe status to our status
            status_map = {
                'active': 'active',
                'past_due': 'past_due',
                'canceled': 'canceled',
                'unpaid': 'inactive',
                'incomplete': 'inactive',
                'incomplete_expired': 'inactive',
                'trialing': 'active'
            }
            
            our_status = status_map.get(status, 'inactive')
            
            cur.execute("""
                UPDATE users SET subscription_status = %s
                WHERE stripe_subscription_id = %s
            """, (our_status, subscription_id))
            
        elif event_type == 'customer.subscription.deleted':
            subscription_id = data.get('id')
            
            cur.execute("""
                UPDATE users SET 
                    subscription_status = 'canceled',
                    subscription_ends_at = %s
                WHERE stripe_subscription_id = %s
            """, (datetime.now(), subscription_id))
            
        elif event_type == 'invoice.payment_failed':
            customer_id = data.get('customer')
            
            cur.execute("""
                UPDATE users SET subscription_status = 'past_due'
                WHERE stripe_customer_id = %s
            """, (customer_id,))
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        print(f"Webhook error: {e}")
        return False
        
    finally:
        cur.close()
        conn.close()


def get_customer_portal_url(customer_id, return_url):
    """
    Create a Stripe Customer Portal session for managing subscription
    """
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url
        )
        return session.url
    except Exception as e:
        print(f"Error creating portal session: {e}")
        return None


def get_subscription_status(user_id):
    """
    Get current subscription status for user
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT subscription_status, subscription_started_at, 
                   stripe_subscription_id, is_admin
            FROM users WHERE id = %s
        """, (user_id,))
        
        result = cur.fetchone()
        if result:
            return dict(result)
        return None
        
    finally:
        cur.close()
        conn.close()


def get_user_transactions(user_id, limit=50):
    """
    Get user's enrichment transactions history
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            SELECT et.*, b.address, b.bbl
            FROM enrichment_transactions et
            LEFT JOIN buildings b ON et.building_id = b.id
            WHERE et.user_id = %s
            ORDER BY et.created_at DESC
            LIMIT %s
        """, (user_id, limit))
        
        return [dict(r) for r in cur.fetchall()]
        
    finally:
        cur.close()
        conn.close()
