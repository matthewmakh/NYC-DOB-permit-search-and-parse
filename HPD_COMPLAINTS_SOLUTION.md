# HPD Complaints Data - Investigation & Solution

## Problem
The HPD complaints columns (`hpd_open_complaints`, `hpd_total_complaints`) were showing 0 for all buildings because the enrichment script was using a restricted API endpoint.

## Investigation Results

### Test Results (from `test_hpd_complaints_api.py`)

**FAILED Endpoints (403 Forbidden - Require Special Access):**
- `uwyv-629c` - HPD Complaints (original endpoint) ❌
- `a2nx-4u46` - HPD Complaints (alternative) ❌

**SUCCESS Endpoints (Public Access):**
- ✅ **`ygpa-z7cr` - Housing Maintenance Code Complaints and Problems**
  - Publicly accessible
  - Contains BBL field for direct querying
  - Has `complaint_status` and `problem_status` fields
  - Includes detailed complaint information
  - Updated in real-time

### Why The Original Endpoint Failed
NYC restricts access to certain datasets that contain sensitive information. The original complaints endpoint (`uwyv-629c`) requires authentication that goes beyond a standard API token - it may require special data sharing agreements with NYC.

## Solution Implemented

### 1. Updated Enrichment Script
**File:** `step2_enrich_from_pluto.py`

**Changes:**
```python
# OLD (Restricted):
HPD_COMPLAINTS_API = "https://data.cityofnewyork.us/resource/uwyv-629c.json"

# NEW (Public):
HPD_COMPLAINTS_API = "https://data.cityofnewyork.us/resource/ygpa-z7cr.json"
```

**Query Method:**
```python
# Now queries by BBL directly (more reliable)
params={'bbl': bbl, '$select': 'complaint_status,problem_status', '$limit': 5000}

# Counts open complaints where either status is OPEN
open_complaints = sum(1 for c in complaints 
                     if c.get('complaint_status') == 'OPEN' 
                     or c.get('problem_status') == 'OPEN')
```

### 2. Created Backfill Script
**File:** `backfill_complaints_data.py`

**Purpose:** Update existing buildings with complaints data

**Usage:**
```bash
python backfill_complaints_data.py
```

**What it does:**
- Fetches buildings with HPD registration but no complaints data
- Queries the public complaints API for each building
- Updates `hpd_total_complaints` and `hpd_open_complaints` columns
- Processes up to 1000 buildings per run
- Shows progress and statistics

## Next Steps

### Immediate Actions

1. **Run the backfill script:**
   ```bash
   cd /path/to/project
   python backfill_complaints_data.py
   ```
   This will populate complaints data for ~7,000+ buildings that have HPD registrations.

2. **Run enrichment for new permits:**
   Going forward, any new enrichment runs will automatically use the public API and populate complaints data.

3. **Verify the data:**
   ```sql
   SELECT 
       COUNT(*) as total,
       COUNT(CASE WHEN hpd_open_complaints > 0 THEN 1 END) as with_open,
       SUM(hpd_open_complaints) as total_open
   FROM buildings 
   WHERE hpd_registration_id IS NOT NULL;
   ```

### Data Quality Notes

**About the Public Complaints Dataset:**
- Contains Housing Maintenance Code complaints filed with HPD
- Includes both active and historical complaints
- Status fields: `complaint_status` and `problem_status`
  - Both can be "OPEN" or "CLOSE" (sic - not "CLOSED")
  - A complaint can have OPEN problems even if complaint_status is CLOSE
- Coverage: Buildings under HPD jurisdiction
- Update frequency: Real-time/Daily

**Comparison with Violations:**
- Violations: ~9,824 buildings with data
- Complaints: Should be similar coverage
- Both require HPD registration

## Testing the API

Run the test script to verify access:
```bash
python test_hpd_complaints_api.py
```

Test with a specific BBL:
```python
import requests
response = requests.get(
    'https://data.cityofnewyork.us/resource/ygpa-z7cr.json',
    params={'bbl': '2032920062', '$limit': 10}
)
complaints = response.json()
print(f"Found {len(complaints)} complaints")
```

## Why We Can't "Log In"

NYC Open Data doesn't use traditional login credentials. Access is controlled by:

1. **Public Datasets** - No authentication needed (what we're using now)
2. **App Tokens** - Free registration for higher rate limits
3. **Restricted Datasets** - Require special NYC data sharing agreements

The complaints data we needed was in a restricted dataset, but we found the same information in a public dataset with a slightly different structure.

## Summary

✅ **Problem Solved:** We can now access complaints data
✅ **No Authentication Needed:** Using public API endpoint
✅ **Better Data Quality:** Direct BBL queries are more reliable
✅ **Future-Proof:** Enrichment pipeline will work going forward

**Action Required:** Run `backfill_complaints_data.py` to populate existing buildings
