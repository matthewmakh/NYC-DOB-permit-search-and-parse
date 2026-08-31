# Navigation and Page Audit

## Product boundary

The customer-facing product is a searchable NYC lead-intelligence tool. It is not a CRM. The retained pages help a user find a permit, property, participant, repeat buyer, DOB project event, or early public signal and then follow the underlying evidence.

## Canonical top-level pages

| Page | Route | Purpose |
|---|---|---|
| Home | `/` | Search across known buildings and enter every lead surface |
| Permits | `/permits` | Search and filter permit leads; `/construction` redirects here |
| Properties | `/properties` | Filter buildings, owners, transaction history, and property signals |
| Participants | `/contractors` | Search permittees, applicants, owners, and filing representatives |
| Buyers | `/buyers` | Find owners with repeat DOB project activity |
| Project alerts | `/alerts` | Search consolidated new-filing, status-change, and permit-issued events |
| Pre-permit signals | `/signals` | Search ranked City Record procurement and contract notices |

All seven destinations are rendered by one shared navigation component in `_site_nav.html`. At narrower widths the links move into an accessible menu instead of disappearing or wrapping unpredictably.

## Drill-down pages

| Page | Route |
|---|---|
| Global search results | `/search-results?q=…` |
| Permit detail | `/permit/<id>` |
| Property profile | `/property/<bbl>` |
| Participant profile | `/contractor/<name>` |
| Admin activity | `/admin/activity` (admin only) |

Each drill-down keeps the global navigation visible and highlights its parent section.

## Removed pages and workflows

| Removed | Reason |
|---|---|
| Investments | The route pointed to a template that did not exist and duplicated property lead discovery |
| Analytics | The route pointed to a template that did not exist and did not provide a lead-search workflow |
| Old dashboard | Hidden duplicate of the permit/building data already served by current pages |
| Watchlists | Pipeline-management behavior outside the current search-only product boundary |
| CRM CSV and webhook push | Explicitly outside the product boundary |
| Alert/signal review mutations | Queue-management behavior replaced by search and direct evidence links |

Pre-existing database schema declarations for historical watchlist tables are left untouched so this UI cleanup does not perform a destructive database migration.

## Previous inconsistency

- Home, Properties, and Participants each hard-coded a four-link navigation.
- Shared-base pages used a different seven-link navigation that included Watchlists but omitted Permits.
- Permits and Admin used a legacy dark navigation with emoji labels and dead Investments/Analytics links.
- Investments and Analytics failed at render time because their templates were missing.
- Buyers and Project Alerts led primarily into watchlist/CRM actions instead of deeper lead research.

The regression test in `dashboard_html/test_navigation.py` now verifies that rendered templates exist, public pages use the shared navigation, primary destinations have routes, and removed workflow routes do not return.
