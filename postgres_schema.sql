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
  assigned_at TIMESTAMP
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

-- Create indexes for better performance
CREATE INDEX idx_permits_permit_no ON permits(permit_no);
CREATE INDEX idx_permits_job_id ON permits(job_id);
CREATE INDEX idx_contact_scrape_jobs_dates ON contact_scrape_jobs(start_year, start_month, start_day, permit_type);
