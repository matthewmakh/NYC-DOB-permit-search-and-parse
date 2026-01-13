#!/usr/bin/env python3
"""
Database Migration: Add NYC Open Data API Fields to Permits Table

This migration adds comprehensive fields from the DOB Permit Issuance dataset
from NYC Open Data API to enable rich permit details display.

Dataset: https://data.cityofnewyork.us/Housing-Development/DOB-Permit-Issuance/ipu4-2q9a
"""

from dotenv import load_dotenv
load_dotenv()

import os
import psycopg2
from datetime import datetime

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', '5432')),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME', 'railway')
}


def run_migration():
    """Run the database migration"""
    
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    print("=" * 100)
    print("DATABASE MIGRATION: Add NYC Open Data Fields to Permits Table")
    print("=" * 100)
    print(f"Database: {DB_CONFIG['database']}")
    print(f"Host: {DB_CONFIG['host']}")
    print(f"Time: {datetime.now()}")
    print("=" * 100)
    
    try:
        # Start transaction
        print("\n🔄 Starting migration...\n")
        
        # Create a backup note
        print("⚠️  RECOMMENDATION: Backup your database before proceeding")
        print("   You can backup with: pg_dump -h <host> -U <user> <database> > backup.sql\n")
        
        # Add new columns to permits table
        migrations = [
            # Property Information
            ("borough", "VARCHAR(2)", "Borough code (1=Manhattan, 2=Bronx, 3=Brooklyn, 4=Queens, 5=Staten Island)"),
            ("house_number", "VARCHAR(50)", "House number"),
            ("street_name", "VARCHAR(255)", "Street name"),
            ("zip_code", "VARCHAR(10)", "Zip code"),
            ("community_board", "VARCHAR(3)", "Community board (3-digit: borough + CB number)"),
            
            # Job Details
            ("job_doc_number", "VARCHAR(50)", "Job document number"),
            ("self_cert", "VARCHAR(10)", "Self certification status"),
            ("bldg_type", "VARCHAR(50)", "Building type (1-2-3 Family or Other)"),
            ("residential", "VARCHAR(10)", "Residential flag"),
            ("special_district_1", "VARCHAR(50)", "Special district 1"),
            ("special_district_2", "VARCHAR(50)", "Special district 2"),
            ("work_type", "VARCHAR(50)", "Work type (PL=Plumbing, BL=Boiler, MH=Mechanical, etc.)"),
            
            # Permit Details  
            ("permit_status", "VARCHAR(50)", "Permit status"),
            ("filing_status", "VARCHAR(50)", "Filing status"),
            ("permit_type", "VARCHAR(50)", "Permit type (DM=Demolition, EW=Equipment Work, etc.)"),
            ("permit_sequence", "VARCHAR(50)", "Permit sequence number"),
            ("permit_subtype", "VARCHAR(50)", "Permit subtype"),
            ("oil_gas", "VARCHAR(10)", "Oil/Gas flag"),
            
            # Permittee Information
            ("permittee_first_name", "VARCHAR(100)", "Permittee first name"),
            ("permittee_last_name", "VARCHAR(100)", "Permittee last name"),
            ("permittee_business_name", "VARCHAR(255)", "Permittee business name"),
            ("permittee_phone", "VARCHAR(50)", "Permittee phone number"),
            ("permittee_license_type", "VARCHAR(50)", "Permittee license type"),
            ("permittee_license_number", "VARCHAR(50)", "Permittee license number"),
            ("act_as_superintendent", "VARCHAR(10)", "Acts as superintendent flag"),
            ("permittee_other_title", "VARCHAR(100)", "Permittee other title"),
            ("hic_license", "VARCHAR(50)", "HIC license"),
            
            # Site Safety Manager
            ("site_safety_mgr_first_name", "VARCHAR(100)", "Site safety manager first name"),
            ("site_safety_mgr_last_name", "VARCHAR(100)", "Site safety manager last name"),
            ("site_safety_mgr_business_name", "VARCHAR(255)", "Site safety manager business name"),
            
            # Superintendent
            ("superintendent_name", "VARCHAR(255)", "Superintendent first & last name"),
            ("superintendent_business_name", "VARCHAR(255)", "Superintendent business name"),
            
            # Owner Information (Enhanced)
            ("owner_business_type", "VARCHAR(100)", "Owner business type"),
            ("non_profit", "VARCHAR(10)", "Non-profit flag"),
            ("owner_business_name", "VARCHAR(255)", "Owner business name"),
            ("owner_first_name", "VARCHAR(100)", "Owner first name"),
            ("owner_last_name", "VARCHAR(100)", "Owner last name"),
            ("owner_house_number", "VARCHAR(50)", "Owner house number"),
            ("owner_street_name", "VARCHAR(255)", "Owner street name"),
            ("owner_city", "VARCHAR(100)", "Owner city"),
            ("owner_state", "VARCHAR(2)", "Owner state"),
            ("owner_zip_code", "VARCHAR(10)", "Owner zip code"),
            ("owner_phone", "VARCHAR(50)", "Owner phone number"),
            
            # System/GIS Fields
            ("dob_run_date", "DATE", "DOB data run date"),
            ("permit_si_no", "VARCHAR(50)", "Permit SI number"),
            ("council_district", "VARCHAR(10)", "NYC council district"),
            ("census_tract", "VARCHAR(20)", "Census tract"),
            ("nta_name", "VARCHAR(255)", "Neighborhood Tabulation Area name"),
            
            # API Metadata
            ("api_source", "VARCHAR(50)", "Source of data (nyc_open_data, selenium, manual)"),
            ("api_last_updated", "TIMESTAMP", "Last updated from API"),
        ]
        
        print("📝 Adding new columns to permits table:\n")
        
        added_count = 0
        skipped_count = 0
        
        for column_name, data_type, description in migrations:
            # Check if column already exists
            cursor.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'permits' AND column_name = %s
            """, (column_name,))
            
            if cursor.fetchone():
                print(f"   ⏭️  SKIP: {column_name:<30} (already exists)")
                skipped_count += 1
                continue
            
            # Add the column
            sql = f"ALTER TABLE permits ADD COLUMN {column_name} {data_type}"
            cursor.execute(sql)
            print(f"   ✅ ADD:  {column_name:<30} {data_type:<20} -- {description}")
            added_count += 1
        
        # Create indexes on commonly queried fields
        print("\n📊 Creating indexes on new fields:\n")
        
        indexes = [
            ("idx_permits_borough", "borough"),
            ("idx_permits_zip_code", "zip_code"),
            ("idx_permits_work_type", "work_type"),
            ("idx_permits_permit_type_new", "permit_type"),
            ("idx_permits_permit_status_new", "permit_status"),
            ("idx_permits_permittee_business", "permittee_business_name"),
            ("idx_permits_owner_business_name", "owner_business_name"),
            ("idx_permits_api_source", "api_source"),
            ("idx_permits_dob_run_date", "dob_run_date"),
        ]
        
        for index_name, column_name in indexes:
            try:
                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS {index_name} 
                    ON permits({column_name})
                """)
                print(f"   ✅ INDEX: {index_name} on {column_name}")
            except Exception as e:
                print(f"   ⚠️  INDEX: {index_name} - {e}")
        
        # Commit transaction
        conn.commit()
        
        print("\n" + "=" * 100)
        print("✅ MIGRATION COMPLETED SUCCESSFULLY")
        print("=" * 100)
        print(f"   Columns added: {added_count}")
        print(f"   Columns skipped (already exist): {skipped_count}")
        print(f"   Total columns in migration: {len(migrations)}")
        print("=" * 100)
        
        # Print updated schema summary
        print("\n📋 Updated Permits Table Schema:\n")
        cursor.execute("""
            SELECT column_name, data_type, character_maximum_length
            FROM information_schema.columns
            WHERE table_name = 'permits'
            ORDER BY ordinal_position
        """)
        
        columns = cursor.fetchall()
        print(f"{'Column Name':<40} {'Type':<30}")
        print("-" * 70)
        for col in columns:
            col_name, data_type, max_length = col
            if max_length:
                type_str = f"{data_type}({max_length})"
            else:
                type_str = data_type
            print(f"{col_name:<40} {type_str:<30}")
        
        print(f"\nTotal columns: {len(columns)}")
        
    except Exception as e:
        conn.rollback()
        print(f"\n❌ MIGRATION FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        cursor.close()
        conn.close()
    
    return True


if __name__ == '__main__':
    import sys
    
    print("\n⚠️  WARNING: This migration will modify your database schema.")
    print("   Make sure you have a backup before proceeding.\n")
    
    response = input("Do you want to continue? (yes/no): ").strip().lower()
    
    if response == 'yes':
        success = run_migration()
        sys.exit(0 if success else 1)
    else:
        print("\n❌ Migration cancelled by user")
        sys.exit(1)
