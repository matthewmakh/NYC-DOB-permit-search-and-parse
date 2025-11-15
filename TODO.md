# 📋 TODO - Future Enhancements

**Important features and improvements to implement when ready.**

---

## 🎯 Next Phase: Advanced Property Intelligence

### Phase 1: Property Valuations (Step 4)
**Goal:** Add current market value estimates to buildings

**Implementation:**
- Create `step4_estimate_property_values.py`
- Integrate Zillow/Redfin APIs or scraping
- Add fields to `buildings` table:
  - `estimated_value` - Current market value
  - `estimated_rent_per_unit` - Monthly rent estimate
  - `estimated_annual_rent` - Total rental income potential
  - `estimated_equity` - Value minus mortgage

**Dashboard Display:**
- Current market value with confidence range
- Price history chart
- Comparable properties
- ROI calculations
- Price per square foot
- Value appreciation since purchase

---

### Phase 2: Building Metrics (Step 5)
**Goal:** Aggregate permit spend and activity per building

**Implementation:**
- Create `step5_calculate_building_metrics.py`
- Add `building_metrics` table or fields:
  - `total_permits_3yr` - Count of permits last 3 years
  - `total_spend_3yr` - Sum of permit costs
  - `last_permit_date` - Most recent permit
  - `major_renovations` - JSON of significant work
  - `permit_count` - Total permits ever

**Dashboard Display:**
- Total renovation investment
- Permit activity timeline
- Spending by category (pie chart)
- Investment trend chart
- Major renovations completed
- Activity heatmap

---

### Phase 3: Lead Scoring System (Step 6)
**Goal:** Prioritize properties with multi-dimensional scoring

**Implementation:**
- Create `step6_calculate_scores.py`
- Calculate scores (0-100 each):
  - **Affordability Score**
    - Factors: equity, debt ratio, cash flow potential
    - High equity = better opportunity
  - **Renovation Need Score**
    - Factors: building age, recent permits, systems age
    - Old + low maintenance = higher need
  - **Contact Quality Score**
    - Factors: mobile vs landline, verified status
    - Better contacts = easier to reach
  - **Overall Priority Score**
    - Weighted average of above
    - Determines tier: A (80-100), B (60-79), C (40-59), D (0-39)

**Dashboard Display:**
- Score gauges for each dimension
- Radar chart showing all scores
- AI-generated insights (e.g., "High renovation need, strong contact quality")
- Recommendations (e.g., "Call within 48 hours")
- Percentile rankings
- Lead tier badges

---

### Phase 4: Skip Tracing (Step 7)
**Goal:** Find additional contact information for building owners

**Implementation:**
- Create `step7_skip_trace_owners.py`
- Research skip tracing services:
  - People Data Labs
  - BeenVerified API
  - RocketReach
  - TruePeopleSearch
- Add `owner_contacts` table:
  - Additional phone numbers
  - Email addresses
  - Social media profiles
  - Business affiliations
  - Other properties owned
  - Verification status

**Dashboard Display:**
- Enhanced owner profile
- Contact verification badges
- Phone number status (active/inactive)
- Email deliverability
- Property portfolio map
- Recommended contact strategy
- Best times to reach

---

## 🔧 Code Quality Improvements

### Medium Priority:
- [ ] Add database transaction rollback handling
- [ ] Add NULL checks before integer conversions
- [ ] Extract duplicate code to utils.py
- [ ] Add comprehensive docstrings
- [ ] Remove commented-out code
- [ ] Add type hints

### Testing:
- [ ] Test with malformed permit data
- [ ] Test database connection failures
- [ ] Test API timeouts and errors
- [ ] Load test with 1000+ permits
- [ ] Unit tests for score calculations
- [ ] Integration tests for pipeline

---

## 🎨 Dashboard Enhancements

### Additional Features:
- **Interactive Map View**
  - Property location markers
  - Nearby amenities (schools, transit, shopping)
  - Crime statistics overlay
  - Flood zone information
  - Cluster view for high-density areas

- **Advanced Filtering**
  - By building age
  - By neighborhood/zip code
  - By owner type (individual vs LLC)
  - By renovation budget spent
  - By lead tier (A/B/C/D)

- **Bulk Actions**
  - Export selected leads to CSV
  - Assign leads to sales team
  - Mark as contacted/not interested
  - Schedule follow-ups

- **Analytics Dashboard**
  - Lead conversion rates
  - Average deal size by property type
  - Best performing neighborhoods
  - Contact success rates
  - Sales pipeline visualization

---

## 📊 Automation Ideas

### Automated Workflows:
- **Daily Scraper Run**
  - Schedule `permit_scraper.py` nightly
  - Auto-run `add_permit_contacts.py` after
  - Trigger enrichment pipeline automatically

- **Weekly Deep Enrichment**
  - Re-run PLUTO/RPAD for updated values
  - Refresh ACRIS for new transactions
  - Update property valuations
  - Recalculate all scores

- **Smart Alerts**
  - Email when new A-tier leads appear
  - Notify when property values spike
  - Alert on major renovation permits
  - Flag when contact info changes

- **Auto-assignment**
  - Round-robin lead distribution
  - Geographic territory assignment
  - Workload balancing

---

## 🚀 Integration Ideas

### External Tools:
- **CRM Integration**
  - Salesforce/HubSpot connector
  - Auto-create contacts
  - Sync communication history
  - Track deal progress

- **Communication Tools**
  - Twilio for SMS campaigns
  - SendGrid for email automation
  - VoIP integration for call logging

- **Marketing Automation**
  - Automated drip campaigns
  - Personalized outreach based on scores
  - Follow-up sequences

---

## 📈 Success Metrics to Track

### Short Term (Next Month):
- 500+ permits in database
- 200+ unique buildings
- 80%+ PLUTO enrichment success rate
- Steps 4-6 prototyped
- Dashboard shows basic intelligence

### Medium Term (Next Quarter):
- 5,000+ permits scraped
- 2,000+ buildings with full intelligence
- Property valuations integrated
- Lead scoring operational
- Sales team actively using system

### Long Term (6 Months):
- 10,000+ permits
- Skip tracing operational
- CRM integration complete
- Measurable conversion rate improvement
- ROI tracking on leads

---

## 💡 Research Needed

### APIs to Evaluate:
- [ ] Zillow API alternatives (Estated, RealtyMole)
- [ ] Skip tracing services comparison
- [ ] Property valuation accuracy
- [ ] Cost per API call analysis
- [ ] Rate limits and scaling

### Data Sources to Explore:
- [ ] NYC Building Energy Benchmarking
- [ ] HPD Violations
- [ ] DOB Complaints
- [ ] Certificate of Occupancy changes
- [ ] Deed transfers (beyond ACRIS)

---

## 🎯 Prioritization Notes

**Start with Step 4 (Valuations)** because:
- High value to sales team
- Relatively straightforward to implement
- No new database tables needed
- Clear ROI (better lead qualification)

**Then Step 6 (Scoring)** because:
- Uses existing data
- Provides immediate prioritization
- Improves sales efficiency
- Foundation for automation

**Then Step 5 (Metrics)** because:
- Enhances scoring accuracy
- Provides renovation insights
- Good talking points for sales

**Finally Step 7 (Skip Tracing)** because:
- Most expensive (API costs)
- Depends on having good leads first
- Best used on high-priority properties

---

**Last Updated:** November 15, 2025  
**Current Focus:** Master enrichment pipeline operational, ACRIS bug fixed  
**Next Milestone:** Deploy pipeline to Railway, validate data quality
