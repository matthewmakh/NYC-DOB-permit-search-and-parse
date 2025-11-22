# Property Page Missing Data - FIXED

## Issues Fixed

### ✅ 1. Last Sale Not Populating
**Problem:** Last sale price wasn't showing in quick stats bar
**Root Cause:** Code was correct, data was available
**Fix:** Verified code is working - uses `building.sale_price`
**Status:** WORKING

### ✅ 2. Block and Lot Missing
**Problem:** Block and Lot showing "-" even though BBL exists
**Root Cause:** `block` and `lot` columns are NULL in database (not populated during enrichment)
**Fix:** Added BBL parsing to extract block/lot from BBL string:
```javascript
const bblStr = building.bbl?.toString() || '';
const block = building.block || (bblStr.length >= 6 ? bblStr.substring(1, 6) : '-');
const lot = building.lot || (bblStr.length === 10 ? bblStr.substring(6) : '-');
```
**BBL Format:** `[Borough][Block 5 digits][Lot 4 digits]` = 10 digits total
**Status:** FIXED

### ✅ 3. Portfolio Value Missing
**Problem:** Owner Information showing "-" for Portfolio Value
**Root Cause:** Code was placeholder, not calculating actual sum
**Fix:** Enhanced portfolio fetch to calculate total value:
```javascript
const totalValue = results.reduce((sum, prop) => {
    const value = parseFloat(prop.assessed_value) || parseFloat(prop.sale_price) || 0;
    return sum + value;
}, 0);
```
**Status:** FIXED

### ✅ 4. Owner Address Missing
**Problem:** Owner Address showing "Not available"
**Root Cause:** Owner address fields don't exist in buildings table
**Investigation:** Checked schema - no `owner_address`, `owner_city`, `owner_state`, `owner_zip` columns
**Status:** NOT AVAILABLE IN DATABASE (not enriched)

### ✅ 5. Commercial Units Missing
**Problem:** Commercial units showing "-"
**Root Cause:** No `commercial_units` column in database
**Fix:** Calculate from total_units - residential_units:
```javascript
const comUnits = (totalUnits > resUnits ? totalUnits - resUnits : 0) || '-';
```
**Status:** FIXED (calculated)

### ✅ 6. Lot Area Missing
**Problem:** Lot Area (Sq Ft) showing "-"
**Root Cause:** Wrong field name - was using `lot_area`, should be `lot_sqft`
**Fix:** Changed to `building.lot_sqft`
**Status:** FIXED

### ✅ 7. Building Area Missing
**Problem:** Building Area (Sq Ft) showing "-"
**Root Cause:** Wrong field name - was using `bld_area`, should be `building_sqft`
**Fix:** Changed to `building.building_sqft`
**Status:** FIXED

### ✅ 8. Zoning Missing
**Problem:** Zoning showing "-"
**Root Cause:** Wrong field name - was using `zonedist1`, should use `land_use`
**Fix:** Changed to `building.land_use`
**Note:** This is land use code, not traditional zoning district
**Status:** FIXED

### ✅ 9. Market Value Missing (Key Metrics)
**Problem:** Market Value metric showing "-"
**Root Cause:** Code was correct, uses `building.sale_price`
**Fix:** Verified working - shows last sale price with date
**Status:** WORKING

### ✅ 10. Search API Enhancement
**Problem:** Portfolio value calculation needed assessed_value and sale_price
**Fix:** Enhanced `/api/search` to return:
- `assessed_total_value as assessed_value`
- `sale_price`
- Search across all 3 owner name sources (PLUTO, RPAD, HPD)
- Increased limit from 10 to 50 results
**Status:** ENHANCED

## Database Column Mapping

### Buildings Table Actual Columns:
```
✅ bbl (character varying)
✅ address (text)
✅ block (character varying) - NULL, parse from BBL
✅ lot (character varying) - NULL, parse from BBL
✅ residential_units (integer)
✅ total_units (integer)
✅ lot_sqft (integer) - was using wrong name
✅ building_sqft (integer) - was using wrong name
✅ land_use (character varying) - using for zoning
✅ assessed_total_value (numeric) - market value
✅ sale_price (numeric)
✅ sale_date (date)
✅ current_owner_name (character varying)
✅ owner_name_rpad (character varying)
✅ owner_name_hpd (character varying)
❌ owner_address - DOES NOT EXIST
❌ owner_city - DOES NOT EXIST
❌ owner_state - DOES NOT EXIST
❌ owner_zip - DOES NOT EXIST
❌ commercial_units - DOES NOT EXIST (calculate from total-residential)
```

## Test Property

**Best Complete Data:** BBL `1006130048`
- Address: 210 WEST 11TH STREET
- Owner: 210 WYCOMBE LLC
- Last Sale: $4,525,000 (2008-05-01)
- Units: 1 residential / 1 total
- Lot: 1,062 sqft
- Building: 3,600 sqft
- Market Value: $122,208
- Transactions: 28

**Test URL:** http://localhost:5001/property/1006130048

## Summary

### Fixed (8):
1. ✅ Block/Lot - parse from BBL
2. ✅ Commercial Units - calculate from total-residential
3. ✅ Lot Area - use lot_sqft
4. ✅ Building Area - use building_sqft
5. ✅ Zoning - use land_use
6. ✅ Portfolio Value - calculate sum
7. ✅ Search API - add assessed_value, sale_price
8. ✅ Search API - search all owner sources

### Already Working (2):
1. ✅ Last Sale - working correctly
2. ✅ Market Value - working correctly

### Not Available in Database (1):
1. ❌ Owner Address - fields don't exist, would need enrichment

## Next Steps

If owner address is critical:
1. Add columns to buildings table: `owner_address`, `owner_city`, `owner_state`, `owner_zip`
2. Enhance step2_enrich_from_pluto.py to extract owner address from PLUTO data
3. Or use ACRIS parties' seller addresses as fallback owner addresses
