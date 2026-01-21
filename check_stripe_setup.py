#!/usr/bin/env python3
"""Check Stripe setup and test payment capability"""

from dotenv import load_dotenv
load_dotenv('dashboard_html/.env')

import os
import psycopg2
from psycopg2.extras import RealDictCursor
import stripe

stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    port=os.getenv('DB_PORT'),
    database=os.getenv('DB_NAME'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    cursor_factory=RealDictCursor
)
cur = conn.cursor()

print("=" * 70)
print("STRIPE PAYMENT CAPABILITY CHECK")
print("=" * 70)

# Check users with Stripe info
cur.execute('''
    SELECT id, email, is_admin, stripe_customer_id, stripe_subscription_id, subscription_status 
    FROM users 
    WHERE stripe_customer_id IS NOT NULL OR is_admin = TRUE
    ORDER BY id LIMIT 10
''')
users = cur.fetchall()

print("\n1. Users with Stripe info or admin status:")
print("-" * 50)

for u in users:
    print(f"  User ID={u['id']}, email={u['email']}")
    print(f"    is_admin: {u['is_admin']}")
    print(f"    stripe_customer_id: {u['stripe_customer_id']}")
    print(f"    subscription_id: {u['stripe_subscription_id']}")
    print(f"    subscription_status: {u['subscription_status']}")
    
    # If has Stripe customer, check their payment methods
    if u['stripe_customer_id']:
        try:
            customer = stripe.Customer.retrieve(u['stripe_customer_id'])
            print(f"    [Stripe] Customer exists: {customer.email}")
            print(f"    [Stripe] Default payment method: {customer.invoice_settings.default_payment_method}")
            
            # Check for payment methods
            payment_methods = stripe.PaymentMethod.list(
                customer=u['stripe_customer_id'],
                type='card'
            )
            print(f"    [Stripe] Cards on file: {len(payment_methods.data)}")
            for pm in payment_methods.data:
                print(f"      - {pm.card.brand} ending in {pm.card.last4}, exp {pm.card.exp_month}/{pm.card.exp_year}")
            
            # Check for active subscriptions
            subscriptions = stripe.Subscription.list(
                customer=u['stripe_customer_id'],
                status='active'
            )
            print(f"    [Stripe] Active subscriptions: {len(subscriptions.data)}")
            for sub in subscriptions.data:
                print(f"      - {sub.id}: {sub.status}, has payment method: {sub.default_payment_method is not None}")
                
        except stripe.error.InvalidRequestError as e:
            print(f"    [Stripe ERROR] Customer not found in Stripe: {e}")
        except Exception as e:
            print(f"    [Stripe ERROR] {e}")
    print()

print("\n2. What we store when user subscribes:")
print("-" * 50)
print("""
When a user completes Stripe Checkout, we save:
  - stripe_customer_id: Unique ID in Stripe (links to their account)
  - stripe_subscription_id: Their $250/month subscription ID
  - subscription_status: 'active', 'past_due', 'canceled', etc.

The payment method (card) is stored BY STRIPE, not in our database.
We just need the customer_id to charge them.
""")

print("\n3. How $0.35 enrichment charges work:")
print("-" * 50)
print("""
When charging for enrichment:
  1. Get user's stripe_customer_id from our database
  2. Ask Stripe for their default payment method
  3. If no default, get it from their active subscription
  4. Create PaymentIntent with off_session=True (merchant-initiated)
  5. Stripe charges their saved card automatically
""")

cur.close()
conn.close()
