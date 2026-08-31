"""
Stripe Service Module
Handles subscriptions, payments, and per-enrichment charges
"""

import os
import hashlib
import stripe
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from team_service import get_access_context

# Initialize Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
# Safe retries are paired with an idempotency key on every usage charge.
stripe.max_network_retries = 2

# Subscription price: $250/month
SUBSCRIPTION_PRICE = os.getenv('STRIPE_PRICE_ID')
ENRICHMENT_FEE_SINGLE_CENTS = 50  # $0.50 for single lookup
ENRICHMENT_FEE_BATCH_CENTS = 35   # $0.35 each for batch (2+)


def get_db_connection():
    """Get database connection"""
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        return psycopg2.connect(
            database_url,
            connect_timeout=5,
            options='-c statement_timeout=30000',
            cursor_factory=RealDictCursor,
        )
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        connect_timeout=5,
        options='-c statement_timeout=30000',
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


def create_payment_method_setup_session(user_id, email, success_url, cancel_url):
    """Open Stripe Checkout in setup mode so an admin can fund team usage."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT stripe_customer_id FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError('User not found')
        customer_id = row['stripe_customer_id']
    finally:
        cur.close()
        conn.close()

    if not customer_id:
        customer_id = create_customer(email, user_id)

    return stripe.checkout.Session.create(
        mode='setup',
        currency='usd',
        customer=customer_id,
        payment_method_types=['card'],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={'user_id': str(user_id), 'purpose': 'sponsored_team_usage'},
        setup_intent_data={
            'metadata': {'user_id': str(user_id), 'purpose': 'sponsored_team_usage'}
        },
    )


def finalize_payment_method_setup(session_id, user_id):
    """Make the card collected by a setup-mode Checkout the usage default."""
    checkout = stripe.checkout.Session.retrieve(session_id)
    if str(checkout.metadata.get('user_id')) != str(user_id):
        raise ValueError('That payment setup session belongs to another account.')
    if checkout.status != 'complete' or not checkout.setup_intent:
        raise ValueError('Payment setup is not complete.')

    setup_intent = stripe.SetupIntent.retrieve(checkout.setup_intent)
    payment_method_id = setup_intent.payment_method
    if not payment_method_id:
        raise ValueError('Stripe did not return a saved payment method.')
    stripe.Customer.modify(
        checkout.customer,
        invoice_settings={'default_payment_method': payment_method_id},
    )
    return get_billing_method_summary(user_id)


def get_billing_method_summary(user_id):
    """Return safe display-only card details; never expose full payment data."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT stripe_customer_id FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        customer_id = row['stripe_customer_id'] if row else None
    finally:
        cur.close()
        conn.close()

    if not customer_id:
        return {'ready': False, 'customer_connected': False}

    try:
        customer = stripe.Customer.retrieve(customer_id)
        payment_method = customer.invoice_settings.default_payment_method
        if not payment_method:
            return {'ready': False, 'customer_connected': True}
        if isinstance(payment_method, str):
            payment_method = stripe.PaymentMethod.retrieve(payment_method)
        card = getattr(payment_method, 'card', None)
        return {
            'ready': bool(card),
            'customer_connected': True,
            'brand': getattr(card, 'brand', None) if card else None,
            'last4': getattr(card, 'last4', None) if card else None,
            'exp_month': getattr(card, 'exp_month', None) if card else None,
            'exp_year': getattr(card, 'exp_year', None) if card else None,
        }
    except Exception as exc:
        print(f"Error loading billing method: {exc}")
        return {'ready': False, 'customer_connected': True, 'unavailable': True}


def _charge_context(cur, actor_user_id):
    access = get_access_context(user_id=actor_user_id, connection=cur.connection)
    if not access or not access['has_access']:
        return None, None, 'Active subscription or sponsorship required'
    if not access['should_charge_usage']:
        return access, None, None

    billing_user_id = access['billing_user_id']
    cur.execute(
        """SELECT id, email, stripe_customer_id
           FROM users WHERE id = %s""",
        (billing_user_id,),
    )
    payer = cur.fetchone()
    if not payer:
        return access, None, 'Billing account not found'
    if not payer['stripe_customer_id']:
        owner_label = 'Sponsor' if access.get('is_sponsored') else 'Account'
        return access, payer, f'{owner_label} has no payment method on file'
    return access, payer, None


def _default_payment_method(customer_id):
    customer = stripe.Customer.retrieve(customer_id)
    payment_method = customer.invoice_settings.default_payment_method
    if payment_method:
        return payment_method.id if hasattr(payment_method, 'id') else payment_method

    # Older subscription checkouts may have stored the default on the
    # subscription rather than on Customer.invoice_settings.
    subscriptions = stripe.Subscription.list(customer=customer_id, status='active', limit=1)
    if subscriptions.data and subscriptions.data[0].default_payment_method:
        default = subscriptions.data[0].default_payment_method
        return default.id if hasattr(default, 'id') else default
    return None


def ensure_usage_billing_ready(user_id):
    """Preflight paid work before calling an enrichment provider."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        access, payer, context_error = _charge_context(cur, user_id)
        if context_error:
            return False, context_error, None
        if not access['should_charge_usage']:
            return True, 'Admin bypass', access
        payment_method = _default_payment_method(payer['stripe_customer_id'])
        if not payment_method:
            owner_label = 'Sponsor' if access.get('is_sponsored') else 'Account'
            return False, f'{owner_label} has no payment method on file', access
        return True, 'Billing ready', access
    except Exception as exc:
        return False, str(exc), None
    finally:
        cur.close()
        conn.close()


def _payment_idempotency_key(scope, actor_user_id, building_id, subject):
    fingerprint = hashlib.sha256(
        f'{scope}|{actor_user_id}|{building_id}|{subject or ""}'.encode('utf-8')
    ).hexdigest()[:40]
    return f'nyc-permit-leads-{scope}-{fingerprint}'


def charge_enrichment_fee(user_id, building_id, owner_name, is_batch=False,
                          charge_scope='owner', idempotency_key=None):
    """
    Charge for enrichment lookup
    Single: $0.50, Batch (2+): $0.35 each
    Returns: (success, message, charge_id)
    """
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        access, payer, context_error = _charge_context(cur, user_id)
        if context_error:
            return False, context_error, None
        if not access['should_charge_usage']:
            return True, "Admin bypass", "admin_free"

        payment_method = _default_payment_method(payer['stripe_customer_id'])
        if not payment_method:
            owner_label = 'Sponsor' if access.get('is_sponsored') else 'Account'
            return False, f"{owner_label} has no payment method on file", None
        
        # Determine fee based on single vs batch
        fee_cents = ENRICHMENT_FEE_BATCH_CENTS if is_batch else ENRICHMENT_FEE_SINGLE_CENTS
        fee_dollars = fee_cents / 100
        
        # Create the charge
        request_key = idempotency_key or _payment_idempotency_key(
            charge_scope + ('_batch' if is_batch else '_single'),
            user_id, building_id, owner_name,
        )
        subject_label = 'Permit contact' if charge_scope == 'permit_contact' else 'Owner'
        payment_intent = stripe.PaymentIntent.create(
            amount=fee_cents,
            currency='usd',
            customer=payer['stripe_customer_id'],
            payment_method=payment_method,
            off_session=True,
            confirm=True,
            error_on_requires_action=True,
            description=f"{subject_label} enrichment for building ID {building_id}{' (batch)' if is_batch else ''}",
            metadata={
                'initiated_by_user_id': str(user_id),
                'billing_user_id': str(payer['id']),
                'building_id': str(building_id),
                'owner_name': owner_name[:100] if owner_name else '',
                'type': charge_scope,
                'is_batch': str(is_batch)
            },
            idempotency_key=request_key,
        )

        if payment_intent.status != 'succeeded':
            return False, f"Payment requires attention ({payment_intent.status})", payment_intent.id
        
        # A Stripe success must stay a success even if the local audit write
        # has a transient failure; otherwise we would charge the card and then
        # revoke the data because the ledger insert failed.
        try:
            cur.execute("""
                INSERT INTO enrichment_transactions
                (user_id, billing_user_id, building_id, transaction_type, amount,
                 stripe_payment_intent_id, status, description)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user_id, payer['id'], building_id, charge_scope, fee_dollars,
                payment_intent.id, payment_intent.status,
                f"Enrichment for: {owner_name}"
            ))
            conn.commit()
        except Exception as audit_error:
            conn.rollback()
            print(f"Payment {payment_intent.id} succeeded but audit recording failed: {audit_error}")
        
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


def charge_batch_enrichment_total(user_id, building_ids, num_enrichments, enrichment_details,
                                  idempotency_key=None):
    """
    Charge for batch enrichment - single aggregated charge at the end
    $0.35 per enrichment, minimum $0.50 charge (Stripe requirement)
    
    Args:
        user_id: User ID
        building_ids: List of building IDs that were enriched
        num_enrichments: Number of successful enrichments
        enrichment_details: List of dicts with owner names that were enriched
    
    Returns: (success, message, charge_id)
    """
    if num_enrichments <= 0:
        return True, "No enrichments to charge", None
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        access, payer, context_error = _charge_context(cur, user_id)
        if context_error:
            return False, context_error, None
        if not access['should_charge_usage']:
            return True, "Admin bypass", "admin_free"

        payment_method = _default_payment_method(payer['stripe_customer_id'])
        if not payment_method:
            owner_label = 'Sponsor' if access.get('is_sponsored') else 'Account'
            return False, f"{owner_label} has no payment method on file", None
        
        # Calculate total: $0.35 per enrichment, minimum $0.50
        total_cents = num_enrichments * ENRICHMENT_FEE_BATCH_CENTS
        if total_cents < 50:
            total_cents = 50  # Stripe minimum
        
        total_dollars = total_cents / 100
        
        # Build description
        owner_names = [d.get('owner', 'Unknown') for d in enrichment_details[:5]]
        if len(enrichment_details) > 5:
            owner_names.append(f"and {len(enrichment_details) - 5} more")
        owners_str = ", ".join(owner_names)
        
        # Create the charge
        request_key = idempotency_key or _payment_idempotency_key(
            'bulk_owner', user_id, building_ids[0] if building_ids else 0,
            f'{num_enrichments}|{owners_str}',
        )
        payment_intent = stripe.PaymentIntent.create(
            amount=total_cents,
            currency='usd',
            customer=payer['stripe_customer_id'],
            payment_method=payment_method,
            off_session=True,
            confirm=True,
            error_on_requires_action=True,
            description=f"Bulk owner enrichment: {num_enrichments} lookups",
            metadata={
                'initiated_by_user_id': str(user_id),
                'billing_user_id': str(payer['id']),
                'building_ids': ','.join(str(b) for b in building_ids[:10]),
                'num_enrichments': str(num_enrichments),
                'type': 'bulk_enrichment',
                'owners': owners_str[:200]
            },
            idempotency_key=request_key,
        )

        if payment_intent.status != 'succeeded':
            return False, f"Payment requires attention ({payment_intent.status})", payment_intent.id
        
        try:
            cur.execute("""
                INSERT INTO enrichment_transactions
                (user_id, billing_user_id, building_id, transaction_type, amount,
                 stripe_payment_intent_id, status, description)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user_id, payer['id'], building_ids[0] if building_ids else None,
                'bulk_enrichment', total_dollars,
                payment_intent.id, payment_intent.status,
                f"Bulk enrichment: {num_enrichments} owners - {owners_str[:100]}"
            ))
            conn.commit()
        except Exception as audit_error:
            conn.rollback()
            print(f"Payment {payment_intent.id} succeeded but audit recording failed: {audit_error}")
        
        return True, f"Payment successful: ${total_dollars:.2f} for {num_enrichments} enrichments", payment_intent.id
        
    except stripe.error.CardError as e:
        conn.rollback()
        return False, f"Card declined: {e.user_message}", None
        
    except Exception as e:
        conn.rollback()
        print(f"Error charging batch enrichment fee: {e}")
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
            SELECT et.*, b.address, b.bbl, payer.email AS billed_to_email
            FROM enrichment_transactions et
            LEFT JOIN buildings b ON et.building_id = b.id
            LEFT JOIN users payer ON payer.id = COALESCE(et.billing_user_id, et.user_id)
            WHERE et.user_id = %s
            ORDER BY et.created_at DESC
            LIMIT %s
        """, (user_id, limit))
        
        return [dict(r) for r in cur.fetchall()]
        
    finally:
        cur.close()
        conn.close()
