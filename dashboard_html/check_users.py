#!/usr/bin/env python3
"""Check user accounts in the database"""
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv('.env')

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    port=os.getenv('DB_PORT'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)
cur = conn.cursor()

# Check if users table exists
cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'users')")
if not cur.fetchone()[0]:
    print("No 'users' table found in database!")
    exit()

# Get columns
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'users' ORDER BY ordinal_position")
cols = [r[0] for r in cur.fetchall()]
print("Users table columns:", cols)
print()

# Get all users
cur.execute("SELECT * FROM users ORDER BY created_at DESC")
rows = cur.fetchall()
print(f"Total users: {len(rows)}")
print("=" * 80)

for row in rows:
    user = dict(zip(cols, row))
    print(f"Email: {user.get('email')}")
    print(f"  Created: {user.get('created_at')}")
    print(f"  Verified: {user.get('email_verified')}")
    print(f"  Subscription: {user.get('subscription_status')}")
    print(f"  Stripe Customer ID: {user.get('stripe_customer_id')}")
    print(f"  Stripe Subscription ID: {user.get('stripe_subscription_id')}")
    print("-" * 40)

conn.close()
