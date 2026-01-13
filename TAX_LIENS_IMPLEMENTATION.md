# Tax Delinquency & Liens Feature - Implementation Complete

## 📋 Overview

Added comprehensive tax delinquency and liens tracking to the NYC Real Estate Intelligence platform. This data identifies distressed properties and provides critical risk indicators for investment decisions.

## 🎯 Data Sources

### 1. NYC DOF Property Tax Delinquencies (9rz4-mjek)
- Identifies properties on the city's tax delinquency list
- Shows delinquency notices over time
- Flags water-only vs property tax delinquency

### 2. NYC ECB Violations (6bgk-3dad)
- Environmental Control Board violations
- **Includes financial data**: `penalty_imposed` and `balance_due`
- Outstanding balances can become liens
- Shows severity and status

### 3. NYC DOB Violations (3h2n-5cm9)
- Department of Buildings code violations
- Tracks open vs resolved violations
- Quality indicator for property condition

## 💾 Database Schema

### New Columns in `buildings` Table:

| Column | Type | Description |
|--------|------|-------------|
| `has_tax_delinquency` | BOOLEAN | Property is on delinquency list |
| `tax_delinquency_count` | INT | Number of delinquency notices |
| `tax_delinquency_water_only` | BOOLEAN | Only water debt (not property tax) |
| `ecb_violation_count` | INT | Total ECB violations |
| `ecb_total_balance` | DECIMAL(12,2) | Outstanding ECB balance ($) |
| `ecb_open_violations` | INT | Unresolved ECB violations |
| `dob_violation_count` | INT | Total DOB violations |
| `dob_open_violations` | INT | Open DOB violations |
| `tax_lien_last_checked` | TIMESTAMP | Last update timestamp |

### Indexes Created:
- `idx_buildings_has_tax_delinquency` (WHERE has_tax_delinquency = TRUE)
- `idx_buildings_ecb_balance` (WHERE ecb_total_balance > 0)
- `idx_buildings_ecb_open` (WHERE ecb_open_violations > 0)

## 📁 Files Created/Modified

### New Files:
1. **`migrate_add_tax_lien_data.py`** - Database migration script
2. **`step4_enrich_from_tax_liens.py`** - Enrichment script (part of pipeline)
3. **`test_tax_lien_data.py`** - Initial API testing
4. **`test_final_tax_lien.py`** - Comprehensive testing
5. **`find_properties_with_liens.py`** - Find properties with real outstanding balances

### Modified Files:
1. **`run_enrichment_pipeline.py`** - Added Step 4 to pipeline
2. **`DATABASE_SCHEMA.md`** - Documented new fields

## 🚀 Usage

### 1. Run Migration (One-time)
```bash
python3 migrate_add_tax_lien_data.py
```

### 2. Enrich Buildings (Standalone)
```bash
python3 step4_enrich_from_tax_liens.py
```

### 3. Run Full Pipeline (Includes Step 4)
```bash
python3 run_enrichment_pipeline.py
```

## 📊 Real Data Examples

From testing, we found properties with significant outstanding balances:

**BBL 4102290012** (Queens - 1 Keeler Street)
- **$580,090 in outstanding ECB penalties**
- 12 active violations
- Owner: ZAHID KHAN

**BBL 2028020064** (Bronx - 239 E 176th Street)
- **$363,140 outstanding**
- 10 active violations
- Owner: DOUGLAS LINFORD

**BBL 2026140072** (Bronx - 1164 Franklin Avenue)
- **$228,750 outstanding**
- 5 active violations
- **ALSO on tax delinquency list** (water debt)
- Multiple delinquency notices 2020-2025

## 💡 Use Cases

### For Investors:
- **Distressed Properties**: Find properties with tax delinquency + liens
- **Risk Assessment**: Avoid properties with major outstanding violations
- **Opportunity Identification**: Properties that may go to lien sale

### For Property Analysis:
- **Due Diligence**: Check for hidden liabilities before purchase
- **Portfolio Monitoring**: Track violation accumulation over time
- **Market Intelligence**: Identify troubled landlords/buildings

### For Lead Generation:
- **Motivated Sellers**: Properties with financial pressure
- **Remediation Services**: Buildings needing violation resolution
- **Legal Services**: Properties facing city action

## 🔄 Enrichment Pipeline Order

1. **Step 1**: Link permits to buildings (BBL generation)
2. **Step 2**: PLUTO + RPAD + HPD (owners, values, violations)
3. **Step 3**: ACRIS (transaction history, financing)
4. **Step 4**: Tax/Liens (delinquency, ECB liens, DOB violations) ← NEW
5. **Geocoding**: Add latitude/longitude

## 📈 Next Steps

### To Display in Frontend:
1. Update Flask API endpoints to include tax/lien fields
2. Add "Financial Risk" section to property detail page
3. Create filters for properties with:
   - Tax delinquency
   - Outstanding ECB balances > $X
   - High violation counts
4. Add warning badges on property cards
5. Create "Distressed Properties" report/page

### Advanced Features:
- Track changes over time (trending worse/better)
- Calculate "distress score" combining multiple factors
- Alert system when properties get new violations
- Integration with tax lien sale calendar
- Visualization of violation severity distribution

## 🎉 Impact

This feature adds critical financial and legal intelligence to each property record, enabling:
- Better investment decisions
- Risk identification
- Opportunity discovery
- Due diligence support

The data is now part of the automated enrichment pipeline and will be kept up-to-date with 30-day refresh cycles.
