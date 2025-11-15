-- PostgreSQL Schema for Railway Migration

-- Table: permits
CREATE TABLE permits (
  id SERIAL PRIMARY KEY,
  applicant VARCHAR(225),
  permit_no VARCHAR(100),
  job_type VARCHAR(500),
  issue_date DATE,
  exp_date DATE,
  bin VARCHAR(50),
  address TEXT,
  link TEXT,
  use_type VARCHAR(100),
  stories VARCHAR(10),
  total_units VARCHAR(10),
  occupied_units VARCHAR(10),
  latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION,
  job_id INTEGER,
  assigned_to VARCHAR(255),
  assigned_at TIMESTAMP,
  -- New fields from permit details page
  block VARCHAR(20),                    -- 1. BBL Block
  lot VARCHAR(20),                      -- 1. BBL Lot
  bbl VARCHAR(10),                      -- Derived BBL (Borough-Block-Lot)
  site_fill VARCHAR(100),               -- 3. Site Fill
  total_dwelling_units INTEGER,        -- 5. Total Dwelling Units at Location
  dwelling_units_occupied INTEGER,     -- 6. Dwelling Units Occupied During Construction
  fee_type VARCHAR(50),                 -- 7. Fee Type
  filing_date DATE,                     -- 8. Filing Date
  status VARCHAR(50),                   -- 10. Status
  proposed_job_start DATE,              -- 11. Proposed Job Start
  work_approved DATE,                   -- 12. Work Approved
  work_description TEXT,                -- 13. Work Description (full)
  job_number VARCHAR(50)                -- 14. Job Number
);

-- Table: contact_scrape_jobs
CREATE TABLE contact_scrape_jobs (
  id SERIAL PRIMARY KEY,
  permit_type VARCHAR(10) NOT NULL,
  start_month INTEGER NOT NULL,
  start_day INTEGER NOT NULL,
  start_year INTEGER NOT NULL,
  total_permits INTEGER DEFAULT 0,
  contacts_checked INTEGER DEFAULT 0,
  is_complete BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table: permit_search_config
CREATE TABLE permit_search_config (
  id SERIAL PRIMARY KEY,
  start_month INTEGER NOT NULL,
  start_day INTEGER NOT NULL,
  start_year INTEGER NOT NULL,
  permit_type VARCHAR(20) NOT NULL,
  end_month INTEGER,
  end_day INTEGER,
  end_year INTEGER,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  max_successful_links INTEGER DEFAULT 7
);

-- Table: contacts
CREATE TABLE contacts (
  id SERIAL PRIMARY KEY,
  permit_id INTEGER,
  name VARCHAR(225),
  phone VARCHAR(50),
  is_checked BOOLEAN DEFAULT FALSE,
  is_mobile BOOLEAN,
  assigned_to VARCHAR(255),
  assigned_at TIMESTAMP,
  FOREIGN KEY (permit_id) REFERENCES permits(id)
);

-- Table: assignment_log
CREATE TABLE assignment_log (
  id SERIAL PRIMARY KEY,
  permit_id INTEGER,
  contact_id INTEGER,
  assigned_to VARCHAR(255),
  assigned_at TIMESTAMP
);

-- Table: buildings (for building intelligence pipeline)
CREATE TABLE buildings (
  id SERIAL PRIMARY KEY,
  bbl VARCHAR(10) UNIQUE NOT NULL,      -- 10-digit BBL identifier (B-BBBBB-LLLL)
  address TEXT,
  block VARCHAR(20),
  lot VARCHAR(20),
  bin VARCHAR(50),
  
  -- PLUTO enrichment fields (Step 2)
  current_owner_name VARCHAR(500),
  owner_mailing_address TEXT,
  building_class VARCHAR(10),
  land_use VARCHAR(10),
  residential_units INTEGER,
  total_units INTEGER,
  num_floors INTEGER,
  building_sqft INTEGER,
  lot_sqft INTEGER,
  year_built INTEGER,
  
  -- ACRIS enrichment fields (Step 3)
  purchase_date DATE,
  purchase_price NUMERIC(15, 2),
  mortgage_amount NUMERIC(15, 2),
  
  -- Building intelligence metrics (Steps 4-6)
  estimated_value NUMERIC(15, 2),
  total_permit_spend NUMERIC(15, 2),
  permit_count INTEGER DEFAULT 0,
  affordability_score NUMERIC(5, 2),
  renovation_need_score NUMERIC(5, 2),
  contact_quality_score NUMERIC(5, 2),
  overall_priority_score NUMERIC(5, 2),
  
  -- Metadata
  last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table: owner_contacts (for skip tracing - Step 7)
CREATE TABLE owner_contacts (
  id SERIAL PRIMARY KEY,
  building_id INTEGER REFERENCES buildings(id),
  name VARCHAR(500),
  phone VARCHAR(50),
  email VARCHAR(255),
  mailing_address TEXT,
  contact_source VARCHAR(100),          -- PLUTO, skip_trace, manual, etc.
  is_verified BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table: building_metrics (historical tracking)
CREATE TABLE building_metrics (
  id SERIAL PRIMARY KEY,
  building_id INTEGER REFERENCES buildings(id),
  metric_type VARCHAR(100),             -- permit_spend, valuation, score_change
  metric_value NUMERIC(15, 2),
  recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for better performance
CREATE INDEX idx_permits_permit_no ON permits(permit_no);
CREATE INDEX idx_permits_bbl ON permits(bbl);
CREATE INDEX idx_permits_block_lot ON permits(block, lot);
CREATE INDEX idx_permits_job_id ON permits(job_id);
CREATE INDEX idx_contact_scrape_jobs_dates ON contact_scrape_jobs(start_year, start_month, start_day, permit_type);
CREATE INDEX idx_contacts_permit_id ON contacts(permit_id);
CREATE INDEX idx_contacts_is_mobile ON contacts(is_mobile);
CREATE INDEX idx_buildings_bbl ON buildings(bbl);
CREATE INDEX idx_owner_contacts_building_id ON owner_contacts(building_id);
CREATE INDEX idx_building_metrics_building_id ON building_metrics(building_id);
