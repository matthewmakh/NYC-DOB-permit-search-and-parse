#!/usr/bin/env python3
"""
Migration: Add ACRIS Intelligence Tables and Enhanced Building Fields

This migration creates a comprehensive ACRIS intelligence system:
- Tracks full transaction history per property
- Stores buyer/seller/lender contact information
- Calculates property investment intelligence
- Aggregates lender financing patterns
- Profiles active investors across NYC

Run once to set up the schema.
"""

import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv

load_dotenv()

# Database connection
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    DB_HOST = os.getenv('DB_HOST')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME')
    
    if not all([DB_HOST, DB_USER, DB_PASSWORD, DB_NAME]):
        raise ValueError("Either DATABASE_URL or DB_HOST/DB_USER/DB_PASSWORD/DB_NAME must be set")
    
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


def run_migration():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    print("=" * 70)
    print("ACRIS INTELLIGENCE MIGRATION")
    print("=" * 70)
    
    # ========== STEP 1: Enhance Buildings Table ==========
    print("\n[1/6] Enhancing buildings table with ACRIS fields...")
    
    cur.execute("""
        -- Primary deed data
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS sale_price DECIMAL(12,2);
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS sale_date DATE;
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS sale_recorded_date DATE;
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS sale_buyer_primary VARCHAR(255);
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS sale_seller_primary VARCHAR(255);
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS sale_percent_transferred INT;
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS sale_crfn VARCHAR(50);
        
        -- Mortgage data (enhanced)
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS mortgage_date DATE;
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS mortgage_lender_primary VARCHAR(255);
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS mortgage_crfn VARCHAR(50);
        
        -- Calculated intelligence fields
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS is_cash_purchase BOOLEAN;
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS financing_ratio DECIMAL(5,2);
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS days_since_sale INT;
        
        -- Transaction activity counts
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS acris_total_transactions INT DEFAULT 0;
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS acris_deed_count INT DEFAULT 0;
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS acris_mortgage_count INT DEFAULT 0;
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS acris_satisfaction_count INT DEFAULT 0;
        ALTER TABLE buildings ADD COLUMN IF NOT EXISTS acris_last_enriched TIMESTAMP;
        
        -- Copy existing data to new fields
        UPDATE buildings SET sale_price = purchase_price WHERE purchase_price IS NOT NULL;
        UPDATE buildings SET sale_date = purchase_date WHERE purchase_date IS NOT NULL;
        
        -- Create indexes for common queries
        CREATE INDEX IF NOT EXISTS idx_buildings_is_cash ON buildings(is_cash_purchase);
        CREATE INDEX IF NOT EXISTS idx_buildings_sale_date ON buildings(sale_date DESC);
        CREATE INDEX IF NOT EXISTS idx_buildings_financing_ratio ON buildings(financing_ratio);
    """)
    conn.commit()
    print("   ✅ Buildings table enhanced")
    
    # ========== STEP 2: Create acris_transactions Table ==========
    print("\n[2/6] Creating acris_transactions table...")
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS acris_transactions (
            id SERIAL PRIMARY KEY,
            building_id INT REFERENCES buildings(id) ON DELETE CASCADE,
            
            -- Document info
            document_id VARCHAR(50) UNIQUE NOT NULL,
            doc_type VARCHAR(20),
            doc_amount DECIMAL(12,2),
            doc_date DATE,
            recorded_date DATE,
            percent_transferred INT,
            crfn VARCHAR(50),
            
            -- Classification
            is_primary_deed BOOLEAN DEFAULT FALSE,
            is_primary_mortgage BOOLEAN DEFAULT FALSE,
            
            -- Metadata
            created_at TIMESTAMP DEFAULT NOW(),
            
            -- Indexes
            CONSTRAINT unique_doc_per_building UNIQUE(building_id, document_id)
        );
        
        CREATE INDEX IF NOT EXISTS idx_acris_trans_building ON acris_transactions(building_id);
        CREATE INDEX IF NOT EXISTS idx_acris_trans_type ON acris_transactions(doc_type);
        CREATE INDEX IF NOT EXISTS idx_acris_trans_date ON acris_transactions(doc_date DESC);
        CREATE INDEX IF NOT EXISTS idx_acris_trans_primary_deed ON acris_transactions(is_primary_deed) WHERE is_primary_deed = TRUE;
    """)
    conn.commit()
    print("   ✅ acris_transactions table created")
    
    # ========== STEP 3: Create acris_parties Table ==========
    print("\n[3/6] Creating acris_parties table...")
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS acris_parties (
            id SERIAL PRIMARY KEY,
            transaction_id INT REFERENCES acris_transactions(id) ON DELETE CASCADE,
            
            -- Party info
            party_type VARCHAR(20),  -- 'buyer', 'seller', 'lender', 'borrower'
            party_name VARCHAR(255),
            
            -- Address (contact gold!)
            address_1 VARCHAR(255),
            address_2 VARCHAR(255),
            city VARCHAR(100),
            state VARCHAR(2),
            zip VARCHAR(10),
            country VARCHAR(2),
            
            -- Lead tracking
            is_lead BOOLEAN DEFAULT FALSE,
            lead_contacted_date DATE,
            lead_response_status VARCHAR(50),
            lead_notes TEXT,
            
            created_at TIMESTAMP DEFAULT NOW()
        );
        
        CREATE INDEX IF NOT EXISTS idx_acris_parties_trans ON acris_parties(transaction_id);
        CREATE INDEX IF NOT EXISTS idx_acris_parties_type ON acris_parties(party_type);
        CREATE INDEX IF NOT EXISTS idx_acris_parties_name ON acris_parties(party_name);
        CREATE INDEX IF NOT EXISTS idx_acris_parties_is_lead ON acris_parties(is_lead) WHERE is_lead = TRUE;
    """)
    conn.commit()
    print("   ✅ acris_parties table created")
    
    # ========== STEP 4: Create property_intelligence Table ==========
    print("\n[4/6] Creating property_intelligence table...")
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS property_intelligence (
            building_id INT PRIMARY KEY REFERENCES buildings(id) ON DELETE CASCADE,
            
            -- Flip detection
            is_likely_flipper BOOLEAN DEFAULT FALSE,
            flip_score INT DEFAULT 0,
            sale_velocity_months DECIMAL(6,2),
            
            -- Investment profile
            is_cash_investor BOOLEAN DEFAULT FALSE,
            is_heavy_leverage BOOLEAN DEFAULT FALSE,
            equity_percentage DECIMAL(5,2),
            
            -- Price trends
            appreciation_amount DECIMAL(12,2),
            appreciation_percent DECIMAL(6,2),
            price_per_sqft_at_sale DECIMAL(10,2),
            
            -- Contact value
            has_seller_address BOOLEAN DEFAULT FALSE,
            has_lender_info BOOLEAN DEFAULT FALSE,
            multi_property_owner BOOLEAN DEFAULT FALSE,
            
            -- Lead scoring
            lead_score INT DEFAULT 0,
            lead_priority VARCHAR(20),  -- 'high', 'medium', 'low'
            
            -- Metadata
            calculated_at TIMESTAMP DEFAULT NOW()
        );
        
        CREATE INDEX IF NOT EXISTS idx_prop_intel_flip_score ON property_intelligence(flip_score DESC);
        CREATE INDEX IF NOT EXISTS idx_prop_intel_lead_score ON property_intelligence(lead_score DESC);
        CREATE INDEX IF NOT EXISTS idx_prop_intel_appreciation ON property_intelligence(appreciation_percent DESC);
    """)
    conn.commit()
    print("   ✅ property_intelligence table created")
    
    # ========== STEP 5: Create lender_intelligence Table ==========
    print("\n[5/6] Creating lender_intelligence table...")
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lender_intelligence (
            id SERIAL PRIMARY KEY,
            lender_name VARCHAR(255) UNIQUE NOT NULL,
            
            -- Activity metrics
            total_loans_financed INT DEFAULT 0,
            total_amount_financed DECIMAL(15,2) DEFAULT 0,
            avg_loan_amount DECIMAL(12,2),
            
            -- Geographic focus
            borough_1_most_active INT,
            borough_1_loan_count INT,
            borough_2_most_active INT,
            borough_2_loan_count INT,
            
            -- Loan characteristics
            avg_ltv_ratio DECIMAL(5,2),
            prefers_renovation BOOLEAN DEFAULT FALSE,
            prefers_new_construction BOOLEAN DEFAULT FALSE,
            
            -- Relationships
            repeat_borrower_count INT DEFAULT 0,
            
            -- Contact info (manual entry)
            contact_name VARCHAR(255),
            contact_email VARCHAR(255),
            contact_phone VARCHAR(20),
            notes TEXT,
            
            last_updated TIMESTAMP DEFAULT NOW()
        );
        
        CREATE INDEX IF NOT EXISTS idx_lender_intel_total_loans ON lender_intelligence(total_loans_financed DESC);
        CREATE INDEX IF NOT EXISTS idx_lender_intel_amount ON lender_intelligence(total_amount_financed DESC);
    """)
    conn.commit()
    print("   ✅ lender_intelligence table created")
    
    # ========== STEP 6: Create investor_profiles Table ==========
    print("\n[6/6] Creating investor_profiles table...")
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investor_profiles (
            id SERIAL PRIMARY KEY,
            investor_name VARCHAR(255) UNIQUE NOT NULL,
            
            -- Activity
            properties_bought INT DEFAULT 0,
            properties_sold INT DEFAULT 0,
            total_invested DECIMAL(15,2) DEFAULT 0,
            total_liquidated DECIMAL(15,2) DEFAULT 0,
            
            -- Investment style
            investor_type VARCHAR(50),  -- 'flipper', 'buy-and-hold', 'developer', 'unknown'
            avg_hold_period_months INT,
            uses_financing BOOLEAN,
            avg_financing_ratio DECIMAL(5,2),
            
            -- Geographic preferences
            active_boroughs VARCHAR(255),
            target_neighborhoods VARCHAR(500),
            
            -- Current status
            currently_active BOOLEAN DEFAULT TRUE,
            last_transaction_date DATE,
            
            -- Contact
            contact_address VARCHAR(500),
            is_lead BOOLEAN DEFAULT FALSE,
            lead_status VARCHAR(50),
            
            -- Relationships
            preferred_lenders VARCHAR(500),
            
            created_at TIMESTAMP DEFAULT NOW(),
            last_updated TIMESTAMP DEFAULT NOW()
        );
        
        CREATE INDEX IF NOT EXISTS idx_investor_type ON investor_profiles(investor_type);
        CREATE INDEX IF NOT EXISTS idx_investor_active ON investor_profiles(currently_active);
        CREATE INDEX IF NOT EXISTS idx_investor_last_trans ON investor_profiles(last_transaction_date DESC);
    """)
    conn.commit()
    print("   ✅ investor_profiles table created")
    
    # ========== VERIFICATION ==========
    print("\n" + "=" * 70)
    print("VERIFICATION")
    print("=" * 70)
    
    # Check all tables exist
    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        AND table_name IN (
            'buildings',
            'acris_transactions',
            'acris_parties',
            'property_intelligence',
            'lender_intelligence',
            'investor_profiles'
        )
        ORDER BY table_name;
    """)
    
    tables = cur.fetchall()
    print(f"\n✅ {len(tables)} tables verified:")
    for table in tables:
        print(f"   • {table[0]}")
    
    # Check new columns in buildings
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'buildings' 
        AND column_name LIKE 'sale_%' OR column_name LIKE 'acris_%' OR column_name LIKE 'is_cash%'
        ORDER BY column_name;
    """)
    
    columns = cur.fetchall()
    print(f"\n✅ {len(columns)} new columns in buildings table:")
    for col in columns:
        print(f"   • {col[0]}")
    
    cur.close()
    conn.close()
    
    print("\n" + "=" * 70)
    print("✅ MIGRATION COMPLETE!")
    print("=" * 70)
    print("\nNext steps:")
    print("1. Run step3_enrich_from_acris.py to populate ACRIS data")
    print("2. Run calculate_property_intelligence.py to compute scores")
    print("3. Run aggregate_lender_intelligence.py to analyze lenders")
    print("4. Run build_investor_profiles.py to identify investors")
    print("\nAll tables are ready to receive data!")


if __name__ == "__main__":
    try:
        run_migration()
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        raise
