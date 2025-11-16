# 🗄️ Database Schema Documentation

**NYC DOB Permit Scraper & Real Estate Intelligence Platform**

Last Updated: November 16, 2025

---

## 📋 Table of Contents

1. [Core Permit Tables](#core-permit-tables)
2. [Building Intelligence Tables](#building-intelligence-tables)
3. [ACRIS Intelligence Tables](#acris-intelligence-tables)
4. [Table Relationships](#table-relationships)
5. [Query Examples](#query-examples)

---

## 🏗️ Core Permit Tables

### `permits`
**Purpose**: Stores all NYC Department of Buildings permits scraped from DOB BIS

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL PRIMARY KEY | Unique permit identifier |
| `permit_no` | VARCHAR(50) | DOB permit number (e.g., "121234567") |
| `job_type` | VARCHAR(50) | Type of work (AL, NB, DM, etc.) |
| `address` | VARCHAR(500) | Property address |
| `block` | VARCHAR(10) | Tax block number |
| `lot` | VARCHAR(10) | Tax lot number |
| `bbl` | VARCHAR(10) | **Borough-Block-Lot (10 digits)** |
| `applicant` | VARCHAR(255) | Applicant name |
| `applicant_license` | VARCHAR(50) | License number |
| `stories` | INT | Number of stories |
| `total_units` | INT | Total dwelling units |
| `use_type` | VARCHAR(100) | Property use classification |
| `issue_date` | DATE | Date permit was issued |
| `link` | VARCHAR(500) | URL to DOB permit page |
| `contact_names` | TEXT | Pipe-separated contact names |
| `contact_phones` | TEXT | Pipe-separated phone numbers |
| `is_mobile` | TEXT | Pipe-separated mobile flags (Y/N) |
| `latitude` | DECIMAL(10,7) | Geocoded latitude |
| `longitude` | DECIMAL(10,7) | Geocoded longitude |
| `building_id` | INT | **FK → buildings.id** |
| `created_at` | TIMESTAMP | When record was created |

**Indexes:**
- `permit_no` (unique)
- `bbl`
- `building_id`
- `issue_date DESC`

---

### `contact_scrape_jobs`
**Purpose**: Tracks scraping jobs for audit and resume capability

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL PRIMARY KEY | Job identifier |
| `permit_type` | VARCHAR(10) | Job type scraped (AL, NB, etc.) |
| `start_month` | INT | Search start month |
| `start_day` | INT | Search start day |
| `start_year` | INT | Search start year |
| `created_at` | TIMESTAMP | When job started |

---

### `permit_search_config`
**Purpose**: Stores search parameters for permit scraping

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL PRIMARY KEY | Config identifier |
| `start_month` | INT | Month to start scraping from |
| `start_day` | INT | Day to start scraping from |
| `start_year` | INT | Year to start scraping from |
| `permit_type` | VARCHAR(10) | Type of permit to search |
| `created_at` | TIMESTAMP | When config was created |

---

## 🏢 Building Intelligence Tables

### `buildings`
**Purpose**: Deduplicated building records with enriched data from multiple sources

| Column | Type | Description | Source |
|--------|------|-------------|--------|
| **PRIMARY KEY** |
| `id` | SERIAL PRIMARY KEY | Unique building identifier | - |
| **BASIC INFO** |
| `bbl` | VARCHAR(10) UNIQUE | Borough-Block-Lot identifier | Derived |
| `address` | VARCHAR(500) | Primary address | DOB Permits |
| `borough` | INT | Borough code (1-5) | BBL |
| `block` | INT | Tax block | BBL |
| `lot` | INT | Tax lot | BBL |
| **PLUTO DATA** (MapPLUTO API) |
| `current_owner_name` | VARCHAR(255) | Corporate owner name | PLUTO |
| `units` | INT | Number of units | PLUTO |
| `sqft` | INT | Building square footage | PLUTO |
| `year_built` | INT | Year constructed | PLUTO |
| `year_altered` | INT | Year last altered | PLUTO |
| `building_class` | VARCHAR(10) | NYC building classification | PLUTO |
| **RPAD DATA** (Property Tax) |
| `owner_name_rpad` | VARCHAR(255) | Taxpayer name | RPAD |
| `assessed_land_value` | DECIMAL(12,2) | Land assessed value | RPAD |
| `assessed_total_value` | DECIMAL(12,2) | Total assessed value | RPAD |
| **HPD DATA** (Housing Preservation) |
| `owner_name_hpd` | VARCHAR(255) | Registered owner | HPD |
| `hpd_violations_count` | INT | Open violations | HPD |
| `hpd_complaints_count` | INT | Total complaints | HPD |
| **ACRIS DATA - PRIMARY DEED** |
| `sale_price` | DECIMAL(12,2) | Last purchase price | ACRIS |
| `sale_date` | DATE | Date of last sale | ACRIS |
| `sale_recorded_date` | DATE | Date deed recorded | ACRIS |
| `sale_buyer_primary` | VARCHAR(255) | Primary buyer name | ACRIS |
| `sale_seller_primary` | VARCHAR(255) | Primary seller name | ACRIS |
| `sale_percent_transferred` | INT | % of property sold (usually 100) | ACRIS |
| `sale_crfn` | VARCHAR(50) | City Register File Number | ACRIS |
| **ACRIS DATA - PRIMARY MORTGAGE** |
| `mortgage_amount` | DECIMAL(12,2) | Mortgage loan amount | ACRIS |
| `mortgage_date` | DATE | Mortgage document date | ACRIS |
| `mortgage_lender_primary` | VARCHAR(255) | Primary lender name | ACRIS |
| `mortgage_crfn` | VARCHAR(50) | Mortgage CRFN | ACRIS |
| **CALCULATED INTELLIGENCE** |
| `is_cash_purchase` | BOOLEAN | TRUE if no mortgage | Calculated |
| `financing_ratio` | DECIMAL(5,2) | mortgage_amount / sale_price | Calculated |
| `days_since_sale` | INT | Days since last sale | Calculated |
| **TRANSACTION COUNTS** |
| `acris_total_transactions` | INT | All ACRIS documents | ACRIS |
| `acris_deed_count` | INT | Number of deeds (sales) | ACRIS |
| `acris_mortgage_count` | INT | Number of mortgages | ACRIS |
| `acris_satisfaction_count` | INT | Paid-off loans | ACRIS |
| `acris_last_enriched` | TIMESTAMP | When ACRIS data last updated | - |
| **METADATA** |
| `last_updated` | TIMESTAMP | Last enrichment timestamp | - |
| `created_at` | TIMESTAMP | When record created | - |

**Indexes:**
- `bbl` (unique)
- `is_cash_purchase`
- `sale_date DESC`
- `financing_ratio`

---

## 📊 ACRIS Intelligence Tables

### `acris_transactions`
**Purpose**: Complete transaction history for each property (all deeds, mortgages, etc.)

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL PRIMARY KEY | Transaction identifier |
| `building_id` | INT FK | → buildings.id |
| `document_id` | VARCHAR(50) UNIQUE | ACRIS document ID |
| `doc_type` | VARCHAR(20) | DEED, MTGE, SAT, ASST, etc. |
| `doc_amount` | DECIMAL(12,2) | Transaction amount |
| `doc_date` | DATE | Document date |
| `recorded_date` | DATE | Date recorded with city |
| `percent_transferred` | INT | % of property involved |
| `crfn` | VARCHAR(50) | City Register File Number |
| `is_primary_deed` | BOOLEAN | Main deed shown in buildings |
| `is_primary_mortgage` | BOOLEAN | Main mortgage in buildings |
| `created_at` | TIMESTAMP | When record created |

**Indexes:**
- `building_id`
- `doc_type`
- `doc_date DESC`
- `is_primary_deed` (filtered)

**Unique Constraint**: `(building_id, document_id)`

---

### `acris_parties`
**Purpose**: Stores all buyers, sellers, lenders, borrowers with contact addresses - **LEAD GOLDMINE**

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL PRIMARY KEY | Party identifier |
| `transaction_id` | INT FK | → acris_transactions.id |
| **PARTY INFO** |
| `party_type` | VARCHAR(20) | 'buyer', 'seller', 'lender', 'borrower' |
| `party_name` | VARCHAR(255) | Full name |
| **CONTACT ADDRESS** |
| `address_1` | VARCHAR(255) | Street address |
| `address_2` | VARCHAR(255) | Apt/Suite |
| `city` | VARCHAR(100) | City |
| `state` | VARCHAR(2) | State code |
| `zip` | VARCHAR(10) | ZIP code |
| `country` | VARCHAR(2) | Country code |
| **LEAD TRACKING** |
| `is_lead` | BOOLEAN | Marked as sales lead |
| `lead_contacted_date` | DATE | When contacted |
| `lead_response_status` | VARCHAR(50) | Response status |
| `lead_notes` | TEXT | Sales notes |
| `created_at` | TIMESTAMP | When record created |

**Indexes:**
- `transaction_id`
- `party_type`
- `party_name`
- `is_lead` (filtered)

**Usage Examples:**
```sql
-- Find all sellers (previous owners)
SELECT DISTINCT party_name, address_1, city, state 
FROM acris_parties 
WHERE party_type = 'seller' AND address_1 IS NOT NULL;

-- Find all private lenders (individuals, not banks)
SELECT party_name, COUNT(*) as loan_count
FROM acris_parties
WHERE party_type = 'lender' 
AND party_name NOT LIKE '%BANK%'
AND party_name NOT LIKE '%MORTGAGE%'
GROUP BY party_name
ORDER BY loan_count DESC;
```

---

### `property_intelligence`
**Purpose**: Pre-calculated investment metrics and lead scoring for each building

| Column | Type | Description |
|--------|------|-------------|
| `building_id` | INT PRIMARY KEY FK | → buildings.id |
| **FLIP DETECTION** |
| `is_likely_flipper` | BOOLEAN | Multiple sales in 5 years |
| `flip_score` | INT | 0-100 flip activity score |
| `sale_velocity_months` | DECIMAL(6,2) | Avg months between sales |
| **INVESTMENT PROFILE** |
| `is_cash_investor` | BOOLEAN | Paid cash (no mortgage) |
| `is_heavy_leverage` | BOOLEAN | >80% financed |
| `equity_percentage` | DECIMAL(5,2) | 100 - financing_ratio |
| **PRICE TRENDS** |
| `appreciation_amount` | DECIMAL(12,2) | Current value - sale price |
| `appreciation_percent` | DECIMAL(6,2) | % appreciation |
| `price_per_sqft_at_sale` | DECIMAL(10,2) | Historical cost basis |
| **CONTACT VALUE** |
| `has_seller_address` | BOOLEAN | Can contact previous owner |
| `has_lender_info` | BOOLEAN | Know their financing source |
| `multi_property_owner` | BOOLEAN | Owns multiple buildings |
| **LEAD SCORING** |
| `lead_score` | INT | 0-100 overall lead quality |
| `lead_priority` | VARCHAR(20) | 'high', 'medium', 'low' |
| `calculated_at` | TIMESTAMP | When metrics calculated |

**Indexes:**
- `flip_score DESC`
- `lead_score DESC`
- `appreciation_percent DESC`

**Lead Score Algorithm:**
```
+ 40 pts: is_likely_flipper = TRUE
+ 30 pts: is_cash_investor = TRUE
+ 20 pts: days_since_sale < 730 (recent activity)
+ 15 pts: has_seller_address = TRUE
+ 10 pts: is_heavy_leverage = TRUE (needs financing)
+ 10 pts: acris_total_transactions > 10
+  5 pts: has_lender_info = TRUE
= MAX 100 pts
```

---

### `lender_intelligence`
**Purpose**: Aggregated financing patterns by lender - identify banks, hard money lenders, private lenders

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL PRIMARY KEY | Lender record ID |
| `lender_name` | VARCHAR(255) UNIQUE | Lender name |
| **ACTIVITY METRICS** |
| `total_loans_financed` | INT | Number of mortgages |
| `total_amount_financed` | DECIMAL(15,2) | Sum of all loans |
| `avg_loan_amount` | DECIMAL(12,2) | Average mortgage size |
| **GEOGRAPHIC FOCUS** |
| `borough_1_most_active` | INT | Top borough (1-5) |
| `borough_1_loan_count` | INT | Loans in top borough |
| `borough_2_most_active` | INT | 2nd borough |
| `borough_2_loan_count` | INT | Loans in 2nd borough |
| **LOAN CHARACTERISTICS** |
| `avg_ltv_ratio` | DECIMAL(5,2) | Avg loan-to-value |
| `prefers_renovation` | BOOLEAN | Finances rehabs |
| `prefers_new_construction` | BOOLEAN | Finances new builds |
| `repeat_borrower_count` | INT | Returning customers |
| **CONTACT INFO** (manual entry) |
| `contact_name` | VARCHAR(255) | Contact person |
| `contact_email` | VARCHAR(255) | Email |
| `contact_phone` | VARCHAR(20) | Phone |
| `notes` | TEXT | Internal notes |
| `last_updated` | TIMESTAMP | Last aggregation run |

**Indexes:**
- `total_loans_financed DESC`
- `total_amount_financed DESC`

**Usage Examples:**
```sql
-- Top 10 lenders by volume
SELECT lender_name, total_loans_financed, total_amount_financed
FROM lender_intelligence
ORDER BY total_amount_financed DESC
LIMIT 10;

-- Private lenders (individuals)
SELECT lender_name, total_loans_financed
FROM lender_intelligence
WHERE lender_name NOT LIKE '%BANK%'
AND total_loans_financed >= 3
ORDER BY total_loans_financed DESC;
```

---

### `investor_profiles`
**Purpose**: Track active investors across NYC - deduplicated buyer/seller profiles

| Column | Type | Description |
|--------|------|-------------|
| `id` | SERIAL PRIMARY KEY | Investor ID |
| `investor_name` | VARCHAR(255) UNIQUE | Deduplicated name |
| **ACTIVITY** |
| `properties_bought` | INT | Number purchased |
| `properties_sold` | INT | Number sold |
| `total_invested` | DECIMAL(15,2) | Sum of purchases |
| `total_liquidated` | DECIMAL(15,2) | Sum of sales |
| **INVESTMENT STYLE** |
| `investor_type` | VARCHAR(50) | 'flipper', 'buy-and-hold', 'developer' |
| `avg_hold_period_months` | INT | Avg time between buy/sell |
| `uses_financing` | BOOLEAN | Uses mortgages |
| `avg_financing_ratio` | DECIMAL(5,2) | Avg leverage |
| **GEOGRAPHY** |
| `active_boroughs` | VARCHAR(255) | Comma-separated boroughs |
| `target_neighborhoods` | VARCHAR(500) | Target areas |
| **STATUS** |
| `currently_active` | BOOLEAN | Active in last 2 years |
| `last_transaction_date` | DATE | Most recent buy/sell |
| **CONTACT** |
| `contact_address` | VARCHAR(500) | Most recent address |
| `is_lead` | BOOLEAN | Sales prospect |
| `lead_status` | VARCHAR(50) | Current lead status |
| `preferred_lenders` | VARCHAR(500) | Banks they use |
| **METADATA** |
| `created_at` | TIMESTAMP | Profile created |
| `last_updated` | TIMESTAMP | Profile updated |

**Indexes:**
- `investor_type`
- `currently_active`
- `last_transaction_date DESC`

**Classification Logic:**
- **Flipper**: properties_sold >= 2 AND avg_hold_period_months < 24
- **Buy-and-Hold**: avg_hold_period_months > 60
- **Developer**: properties_bought > 5 AND prefers_new_construction
- **Unknown**: Insufficient data

---

## 🔗 Table Relationships

```
permits
   ├─ building_id → buildings.id

buildings (central hub)
   ├─ acris_transactions (1:many)
   │     └─ acris_parties (1:many)
   │
   └─ property_intelligence (1:1)

lender_intelligence (standalone aggregation)
   └─ Aggregated from acris_parties WHERE party_type='lender'

investor_profiles (standalone aggregation)
   └─ Deduplicated from acris_parties WHERE party_type IN ('buyer','seller')
```

**ER Diagram:**
```
┌──────────────┐
│   permits    │
└──────┬───────┘
       │ building_id
       ↓
┌──────────────────────────┐
│       buildings          │ ←──── Central table
│  (enriched from 5 APIs)  │
└──────┬───────────────────┘
       │
       ├─→ acris_transactions ──→ acris_parties (buyers/sellers/lenders)
       │
       └─→ property_intelligence (calculated scores)

Aggregated Tables (no direct FK):
  • lender_intelligence
  • investor_profiles
```

---

## 📝 Query Examples

### Find High-Value Leads
```sql
SELECT 
    b.address,
    b.sale_seller_primary,
    b.sale_price,
    b.is_cash_purchase,
    pi.lead_score,
    pi.flip_score
FROM buildings b
JOIN property_intelligence pi ON b.id = pi.building_id
WHERE pi.lead_score >= 70
AND b.sale_seller_primary IS NOT NULL
ORDER BY pi.lead_score DESC;
```

### Transaction Timeline for a Property
```sql
SELECT 
    t.doc_type,
    t.doc_amount,
    t.doc_date,
    t.recorded_date,
    STRING_AGG(
        CASE 
            WHEN p.party_type = 'buyer' THEN '🔵 BUYER: ' || p.party_name
            WHEN p.party_type = 'seller' THEN '🔴 SELLER: ' || p.party_name
            WHEN p.party_type = 'lender' THEN '🏦 LENDER: ' || p.party_name
        END, 
        '\n'
    ) as parties
FROM acris_transactions t
LEFT JOIN acris_parties p ON t.id = p.transaction_id
WHERE t.building_id = 123
GROUP BY t.id, t.doc_type, t.doc_amount, t.doc_date, t.recorded_date
ORDER BY t.doc_date DESC;
```

### Find Active Flippers
```sql
SELECT 
    b.address,
    b.current_owner_name,
    pi.flip_score,
    b.acris_deed_count,
    pi.sale_velocity_months
FROM buildings b
JOIN property_intelligence pi ON b.id = pi.building_id
WHERE pi.is_likely_flipper = TRUE
AND pi.flip_score >= 40
ORDER BY pi.flip_score DESC;
```

### Seller Contact List (Previous Owners)
```sql
SELECT DISTINCT
    p.party_name as seller_name,
    p.address_1 || ', ' || p.city || ', ' || p.state || ' ' || p.zip as seller_address,
    b.address as property_they_sold,
    t.doc_amount as sale_price,
    t.doc_date as sale_date
FROM acris_parties p
JOIN acris_transactions t ON p.transaction_id = t.id
JOIN buildings b ON t.building_id = b.id
WHERE p.party_type = 'seller'
AND p.address_1 IS NOT NULL
AND t.doc_date >= '2020-01-01'
ORDER BY t.doc_date DESC;
```

### Lender Relationships
```sql
SELECT 
    li.lender_name,
    li.total_loans_financed,
    li.total_amount_financed,
    li.avg_loan_amount,
    li.borough_1_most_active,
    CASE li.borough_1_most_active
        WHEN 1 THEN 'Manhattan'
        WHEN 2 THEN 'Bronx'
        WHEN 3 THEN 'Brooklyn'
        WHEN 4 THEN 'Queens'
        WHEN 5 THEN 'Staten Island'
    END as top_borough
FROM lender_intelligence li
WHERE li.total_loans_financed >= 10
ORDER BY li.total_amount_financed DESC;
```

---

## 🔧 Maintenance Queries

### Re-calculate Property Intelligence
```sql
-- Run after ACRIS enrichment
SELECT calculate_property_intelligence();
```

### Aggregate Lender Data
```sql
-- Update lender_intelligence table
SELECT aggregate_lender_intelligence();
```

### Find Buildings Needing Enrichment
```sql
-- Buildings without ACRIS data
SELECT id, bbl, address
FROM buildings
WHERE bbl IS NOT NULL
AND (acris_last_enriched IS NULL 
     OR acris_last_enriched < NOW() - INTERVAL '30 days');
```

---

## 📈 Data Quality Checks

```sql
-- Buildings with BBL but no ACRIS data
SELECT COUNT(*) as missing_acris
FROM buildings
WHERE bbl IS NOT NULL
AND sale_date IS NULL;

-- Orphaned ACRIS transactions
SELECT COUNT(*)
FROM acris_transactions t
LEFT JOIN buildings b ON t.building_id = b.id
WHERE b.id IS NULL;

-- Parties without addresses (can't contact)
SELECT COUNT(*)
FROM acris_parties
WHERE party_type IN ('seller', 'buyer')
AND address_1 IS NULL;
```

---

## 🎯 Summary

**Total Tables**: 9
- **3 Core**: permits, contact_scrape_jobs, permit_search_config
- **1 Central**: buildings (enriched from 5 APIs)
- **5 Intelligence**: acris_transactions, acris_parties, property_intelligence, lender_intelligence, investor_profiles

**Key Features**:
- ✅ Complete transaction history per property
- ✅ Buyer/seller contact information for outreach
- ✅ Automated lead scoring (flip detection, cash buyers)
- ✅ Lender intelligence (who finances what)
- ✅ Investor profiling (active players in NYC market)
- ✅ 30-day refresh cycle for data freshness

**Data Flow**:
1. Scrape permits → `permits` table
2. Link permits to buildings → `buildings` table
3. Enrich from 5 APIs (PLUTO, RPAD, HPD, ACRIS x2) → `buildings` + `acris_*` tables
4. Calculate intelligence → `property_intelligence`
5. Aggregate patterns → `lender_intelligence`, `investor_profiles`
