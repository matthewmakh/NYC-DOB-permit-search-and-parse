#!/usr/bin/env python3
"""
Database Migration: Add Activity Logs Table
Creates a comprehensive activity logging system for admin monitoring
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
        # ACTIVITY LOGS TABLE
        # Flexible schema for comprehensive activity tracking
        # ==========================================
        print("Creating activity_logs table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS activity_logs (
                id SERIAL PRIMARY KEY,
                
                -- User context (nullable for anonymous actions)
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                user_email VARCHAR(255),
                
                -- Session tracking
                session_token VARCHAR(255),
                
                -- Activity details
                activity_type VARCHAR(100) NOT NULL,  -- login, logout, page_view, click, search, export, etc.
                activity_category VARCHAR(50) NOT NULL,  -- auth, navigation, interaction, data, system
                activity_description TEXT,
                
                -- Page/Route context
                page_url TEXT,
                page_name VARCHAR(100),
                referrer_url TEXT,
                
                -- Element interaction (for clicks)
                element_id VARCHAR(255),
                element_type VARCHAR(50),  -- button, link, form, etc.
                element_text VARCHAR(500),
                
                -- Search/filter context
                search_query TEXT,
                filter_params JSONB,
                
                -- Action result
                action_success BOOLEAN DEFAULT TRUE,
                action_result TEXT,
                error_message TEXT,
                
                -- Request details
                http_method VARCHAR(10),
                endpoint VARCHAR(255),
                request_params JSONB,
                response_status INTEGER,
                response_time_ms INTEGER,
                
                -- Client information
                ip_address VARCHAR(45),
                ip_city VARCHAR(100),
                ip_region VARCHAR(100),
                ip_country VARCHAR(100),
                ip_timezone VARCHAR(100),
                ip_isp VARCHAR(255),
                
                -- Device/Browser information
                user_agent TEXT,
                device_type VARCHAR(50),  -- desktop, mobile, tablet
                device_os VARCHAR(50),
                device_browser VARCHAR(100),
                browser_version VARCHAR(50),
                screen_width INTEGER,
                screen_height INTEGER,
                
                -- Extra flexible data
                metadata JSONB,
                
                -- Timestamps
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("  ✓ activity_logs table created")
        
        # ==========================================
        # INDEXES FOR FAST QUERYING
        # ==========================================
        print("Creating indexes...")
        
        # Index for user lookups
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_activity_logs_user_id 
            ON activity_logs(user_id)
        """)
        print("  ✓ idx_activity_logs_user_id created")
        
        # Index for activity type filtering
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_activity_logs_type 
            ON activity_logs(activity_type)
        """)
        print("  ✓ idx_activity_logs_type created")
        
        # Index for category filtering
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_activity_logs_category 
            ON activity_logs(activity_category)
        """)
        print("  ✓ idx_activity_logs_category created")
        
        # Index for time-based queries
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_activity_logs_created_at 
            ON activity_logs(created_at DESC)
        """)
        print("  ✓ idx_activity_logs_created_at created")
        
        # Composite index for common admin queries
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_activity_logs_user_time 
            ON activity_logs(user_id, created_at DESC)
        """)
        print("  ✓ idx_activity_logs_user_time created")
        
        # Index for IP-based queries
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_activity_logs_ip 
            ON activity_logs(ip_address)
        """)
        print("  ✓ idx_activity_logs_ip created")
        
        # Index for session tracking
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_activity_logs_session 
            ON activity_logs(session_token)
        """)
        print("  ✓ idx_activity_logs_session created")
        
        conn.commit()
        print("\n✅ Migration completed successfully!")
        
    except Exception as e:
        conn.rollback()
        print(f"\n❌ Migration failed: {e}")
        raise
    finally:
        cur.close()
        conn.close()


def rollback_migration():
    """Rollback the migration if needed"""
    conn = get_connection()
    cur = conn.cursor()
    
    try:
        print("Rolling back activity_logs migration...")
        cur.execute("DROP TABLE IF EXISTS activity_logs CASCADE")
        conn.commit()
        print("✅ Rollback completed")
    except Exception as e:
        conn.rollback()
        print(f"❌ Rollback failed: {e}")
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == 'rollback':
        rollback_migration()
    else:
        run_migration()
