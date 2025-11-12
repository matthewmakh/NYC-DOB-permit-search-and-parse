# Advanced Filters Implemented

## Overview
All advanced filters have been implemented using only existing database fields - no new scraping required.

## New Filters Added

### 1. **Mobile Numbers Filter** 📱
- **UI Control**: Checkbox in Manual Filters section
- **Database Field**: `contacts.is_mobile`
- **Behavior**: When enabled, only shows permits with mobile phone contacts
- **SQL Implementation**: Filters JOIN condition to only include contacts where `is_mobile = 1`

### 2. **Contact Count Filter**
- **UI Control**: Slider (0-5 minimum contacts)
- **Database Field**: Calculated via `COUNT(DISTINCT c.id)`
- **Behavior**: Filters permits by minimum number of contacts
- **SQL Implementation**: HAVING clause for `contact_count >= min_contacts`

### 3. **Lead Score System** 🔥
- **Score Range**: 0-100 points
- **Calculation Components**:
  - **Recency (0-40 pts)**: Based on `issue_date`
    - Last 30 days: 40 pts
    - Last 90 days: 30 pts
    - Last 180 days: 20 pts
    - Last 365 days: 10 pts
  - **Contacts (0-30 pts)**: Based on `contact_count` + `mobile_count`
    - 3+ contacts: 30 pts
    - 2 contacts: 20 pts
    - 1 contact: 10 pts
    - Bonus: +5 pts per mobile (max +10)
  - **Project Size (0-20 pts)**: Based on `total_units`
    - 50+ units: 20 pts
    - 20+ units: 15 pts
    - 10+ units: 10 pts
    - 5+ units: 5 pts
  - **Permit Type (0-10 pts)**: Based on `job_type`
    - New Building (NB): 10 pts
    - Alteration (AL): 7 pts
    - Demolition (DM): 5 pts
- **UI Control**: Slider for minimum score (0-100)
- **Quality Indicators**:
  - 🔥 Hot: 70+ points
  - ⚡ Warm: 50-69 points
  - 💡 Cold: 30-49 points
  - ❄️ Ice: 0-29 points

### 4. **Permit Status Filter**
- **UI Control**: Multi-select dropdown
- **Options**: Active, Expired, Expiring Soon
- **Database Field**: `exp_date`
- **Logic**:
  - Active: `exp_date >= CURDATE()`
  - Expired: `exp_date < CURDATE()`
  - Expiring Soon: `exp_date BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 30 DAY)`
- **SQL Implementation**: Calculated field `days_until_exp = DATEDIFF(exp_date, CURDATE())`

### 5. **Building Height Filter**
- **UI Control**: Min/Max Stories number inputs
- **Database Field**: `stories`
- **Behavior**: Range filter for building height
- **SQL Implementation**: WHERE clauses for `stories >= min` and `stories <= max`

### 6. **Days Until Expiration**
- **Database Field**: Calculated as `DATEDIFF(exp_date, CURDATE())`
- **Returned in Query**: Available in dataframe as `days_until_exp`
- **Use Case**: Can be used for sorting or additional filtering

### 7. **Mobile Contact Count**
- **Database Field**: Calculated as `SUM(CASE WHEN is_mobile = 1 THEN 1 ELSE 0 END)`
- **Returned in Query**: Available in dataframe as `mobile_count`
- **Display**: Shown in metrics section "📱 Mobile #s"

## Smart Quick Filters (Enhanced)

### New Quick Filters Added:

1. **⏰ Expiring Soon (30 days)**
   - Sets permit status to "Expiring Soon"
   - Shows permits expiring in next 30 days
   - Great for time-sensitive outreach

2. **🆕 Recently Issued (30 days)**
   - Sets date range to last 30 days
   - Shows permits issued in last month
   - Helps target fresh leads

### Existing Quick Filters:
- 🏢 Small New Buildings (<30 units)
- 🏗️ Large Projects (50+ units)
- 💥 Active Demo Sites
- 🏘️ Multi-Family Renovations
- 🏠 Single-Family Homes

## Dashboard Enhancements

### Updated Metrics Display
Now shows 5 metrics:
1. Total Leads
2. Total Contacts
3. Avg Lead Score
4. 🔥 Hot Leads (score >= 70)
5. 📱 Mobile Numbers

### Lead Card Display
- Lead quality badge in expander title (🔥⚡💡❄️)
- Lead score prominently displayed
- Sorted by lead score (highest first)

### Property Information
- Lead score and quality shown at top
- All existing fields maintained

## Technical Implementation

### SQL Query Updates
- Added `days_until_exp` calculated field
- Added `mobile_count` aggregation
- Enhanced JOIN condition for mobile filtering
- Added HAVING clauses for contact count
- Added WHERE clauses for permit status and stories
- Implemented complex permit status logic

### Performance Considerations
- All calculations done in SQL for efficiency
- Lead score calculated client-side (pandas) for flexibility
- Caching maintained with @st.cache_data decorator
- Results sorted by lead score for better UX

## Usage Notes

### Filter Combinations
- Filters can be combined for precise targeting
- Smart filters override manual filters where applicable
- Lead score filter applied after data fetch for flexibility

### Best Practices
1. **Hot Leads**: Set min lead score to 70
2. **Mobile Only**: Enable mobile filter + min contacts 1
3. **Urgent**: Use "Expiring Soon" quick filter
4. **Fresh Leads**: Use "Recently Issued" quick filter
5. **Quality Projects**: Set min units + min lead score

### Data Fields Used
All filters use existing database fields:
- `permits.issue_date`
- `permits.exp_date`
- `permits.stories`
- `permits.total_units`
- `permits.job_type`
- `contacts.is_mobile`
- `contacts.id` (for counting)

No new scraping or data inference required!
