#!/usr/bin/env python3
"""
Database Migration: Add Authentication & Enrichment Tables
Creates users, user_enrichments, and adds enrichment columns to buildings
"""

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def get_connection():
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )

def run_migration():
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        # ==========================================
        # 1. USERS TABLE
        # ==========================================
        print("Creating users table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                is_admin BOOLEAN DEFAULT FALSE,
                is_verified BOOLEAN DEFAULT FALSE,
                verification_token VARCHAR(255),
                verification_token_expires TIMESTAMP,
                reset_token VARCHAR(255),
                reset_token_expires TIMESTAMP,
                stripe_customer_id VARCHAR(255),
                stripe_subscription_id VARCHAR(255),
                subscription_status VARCHAR(50) DEFAULT 'inactive',
                subscription_started_at TIMESTAMP,
                subscription_ends_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                enrichment_credits INTEGER DEFAULT 0
            )
        """)
        print("  ✓ users table created")
        
        # ==========================================
        # 2. USER SESSIONS TABLE (for 48-hour sessions)
        # ==========================================
        print("Creating user_sessions table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                session_token VARCHAR(255) UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                ip_address VARCHAR(45),
                user_agent TEXT
            )
        """)
        print("  ✓ user_sessions table created")
        
        # ==========================================
        # 3. USER ENRICHMENTS TABLE (tracks who unlocked what)
        # ==========================================
        print("Creating user_enrichments table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_enrichments (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                building_id INTEGER REFERENCES buildings(id) ON DELETE CASCADE,
                owner_name_searched VARCHAR(255),
                enrichment_cost DECIMAL(10, 2) DEFAULT 0.35,
                stripe_charge_id VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, building_id)
            )
        """)
        print("  ✓ user_enrichments table created")
        
        # ==========================================
        # 4. ENRICHMENT TRANSACTIONS TABLE (billing history)
        # ==========================================
        print("Creating enrichment_transactions table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS enrichment_transactions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                building_id INTEGER REFERENCES buildings(id),
                transaction_type VARCHAR(50) NOT NULL,
                amount DECIMAL(10, 2) NOT NULL,
                stripe_payment_intent_id VARCHAR(255),
                stripe_charge_id VARCHAR(255),
                status VARCHAR(50) DEFAULT 'pending',
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("  ✓ enrichment_transactions table created")
        
        # ==========================================
        # 5. ADD ENRICHMENT COLUMNS TO BUILDINGS TABLE
        # ==========================================
        print("Adding enrichment columns to buildings table...")
        
        columns_to_add = [
            ("enriched_phones", "JSONB"),
            ("enriched_emails", "JSONB"),
            ("enriched_at", "TIMESTAMP"),
            ("enriched_person_id", "VARCHAR(255)"),
            ("enriched_raw_response", "JSONB"),
        ]
        
        for col_name, col_type in columns_to_add:
            try:
                cur.execute(f"""
                    ALTER TABLE buildings 
                    ADD COLUMN IF NOT EXISTS {col_name} {col_type}
                """)
                print(f"  ✓ Added {col_name} column")
            except psycopg2.errors.DuplicateColumn:
                print(f"  - {col_name} already exists")
                conn.rollback()
        
        # ==========================================
        # 6. CREATE INDEXES
        # ==========================================
        print("Creating indexes...")
        
        indexes = [
            ("idx_users_email", "users", "email"),
            ("idx_users_stripe_customer", "users", "stripe_customer_id"),
            ("idx_user_sessions_token", "user_sessions", "session_token"),
            ("idx_user_sessions_expires", "user_sessions", "expires_at"),
            ("idx_user_enrichments_user", "user_enrichments", "user_id"),
            ("idx_user_enrichments_building", "user_enrichments", "building_id"),
            ("idx_enrichment_transactions_user", "enrichment_transactions", "user_id"),
            ("idx_buildings_enriched", "buildings", "enriched_at"),
        ]
        
        for idx_name, table, column in indexes:
            try:
                cur.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({column})")
                print(f"  ✓ Created index {idx_name}")
            except Exception as e:
                print(f"  - Index {idx_name} error: {e}")
                conn.rollback()
        
        conn.commit()
        print("\n✅ Migration completed successfully!")
        
        # Show table counts
        cur.execute("SELECT COUNT(*) FROM users")
        print(f"   Users: {cur.fetchone()[0]}")
        
    except Exception as e:
        conn.rollback()
        print(f"\n❌ Migration failed: {e}")
        raise
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    print("=" * 50)
    print("AUTH & ENRICHMENT DATABASE MIGRATION")
    print("=" * 50)
    run_migration()
