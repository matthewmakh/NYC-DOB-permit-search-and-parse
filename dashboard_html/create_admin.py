#!/usr/bin/env python3
"""Create admin user for the dashboard"""

import psycopg2
import os
import hashlib
import secrets
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

# Check if admin already exists
cur.execute("SELECT email FROM users WHERE email = 'matt@tyeny.com'")
if cur.fetchone():
    print('Admin user already exists: matt@tyeny.com')
else:
    # Create admin user with password 'admin123' (change this!)
    password = 'admin123'
    salt = secrets.token_hex(16)
    password_hash = hashlib.sha256((password + salt).encode()).hexdigest() + ':' + salt
    
    cur.execute('''
        INSERT INTO users (email, password_hash, is_admin, is_verified, subscription_status)
        VALUES (%s, %s, TRUE, TRUE, 'active')
    ''', ('matt@tyeny.com', password_hash))
    conn.commit()
    print('Created admin user:')
    print('  Email: matt@tyeny.com')
    print('  Password: admin123')
    print('')
    print('WARNING: Change this password after first login!')

conn.close()
