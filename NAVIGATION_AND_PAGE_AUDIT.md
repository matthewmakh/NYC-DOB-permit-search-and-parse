# Branch and page design audit — October 15, 2026

## Canonical application and branch

`main` is the canonical branch for the complete Flask application in
`dashboard_html`. The reconciliation preserves the full `staging` history and
applies the shared design fixes on top. No history reset or force push is needed.

The GitHub branch audit started from these heads:

| Branch | Head | Finding |
|---|---|---|
| `main` | `85dc61a` | 87 commits behind `staging`; no unique commits |
| `staging` | `6940cc2` | Complete current application, including October property and contractor redesigns |
| `claude/frontend-redesign-core-pages-dhe4mo` | `e5e1723` | Remaining commit is patch-equivalent to the already merged contacts fix; PRs #1–#10 landed in `staging` |
| `claude/multi-borough-property-filter-dVzsq` | `8270a01` | Entirely included in `staging` |
| `html-dashboard` | `602aba5` | Historical ancestor, entirely included in `staging` |
| `nextjs-rebuild-v2` | `41db8b8` | Separate unfinished March rebuild of home and properties, not a replacement for the complete Flask app |

The checkout originally fetched only `staging`, concealing all other remote
branches. Fetch all branches when auditing: `git fetch origin '+refs/heads/*:refs/remotes/origin/*'`.

Railway's deployment audit found three web service instances:

| Environment / service | URL | Source at audit start |
|---|---|---|
| Production / `NYC-DOB-permit-search-and-parse` | `leads.installersny.com` | January `html-dashboard`, `602aba5` |
| Production / `DOB-Dashboard` | `permits.up.railway.app` | October `staging`, `6940cc2` |
| Staging / `NYC-DOB-permit-search-and-parse` | `nyc-dob-permit-search-and-parse-staging.up.railway.app` | October `staging`, `6940cc2` |

The live custom domain was still serving the old application while the newer
Railway URL served the redesign. All three web instances must follow the tested
`main` commit. Keep `staging` synchronized by fast-forward for existing pipeline
sources. Do not develop independent designs on the two branches or assume a
successful GitHub merge proves a deployment is healthy. The phone-check cron
also remained on `html-dashboard`; it is separate from the page reconciliation.
The enrichment cron reported `CRASHED` before these changes and requires its own
runtime diagnosis; this design reconciliation does not claim to repair it.

## Current page map

| Surface | Route | Shared presentation |
|---|---|---|
| Home | `/` | Base shell, current palette and navigation |
| Permits | `/permits` | Current workspace heading, summary strip, map, results, filter drawer |
| Properties | `/properties` | October discovery workspace and owner research |
| Contractors | `/contractors` | October contractor directory and participant role filters |
| Repeat buyers | `/buyers` | Current shell, matching cards and actions |
| Project alerts | `/alerts` | Current shell, matching cards and actions |
| Global search | `/search-results?q=…` | Current shell and search explorer |
| Property profile | `/property/<bbl>` | Navy dossier heading and ownership workspace |
| Contractor profile | `/contractor/<name>` | Matching navy dossier heading and summary strip |
| Permit detail | `/permit/<id>` | Matching navy profile heading and current cards |
| Entity research | `/entities`, `/entity/<id>`, `/entity/research?name=…` | Matching dossier presentation and public-record research |
| CRM | `/crm` and subpages | Existing workflow layouts with shared surfaces, accent and appearance preference |
| Administration | `/admin/team`, `/admin/activity` | Shared shell; Activity Logs no longer mixes legacy dark body and fixed white panels |
| Account entry | `/auth/*` templates | Current palette and shared theme initializer, preserving account forms |

`/construction` redirects to `/permits`. Historical Investments, Analytics,
old-dashboard and Watchlist routes remain removed. The earlier audit's claim
that the product excludes CRM is obsolete; the current namespaced CRM is retained.

## Design drift corrected

- Home, Properties and Contractors duplicated their own shell markup. They now
  inherit `base.html`, as do Permits and Activity Logs.
- Permits loaded the legacy `main.css` dark theme regardless of the appearance
  preference. Its map/results layout now uses `app.css` plus page-specific
  `permits.css`; generated fragments resolve shared color tokens.
- Permits linked to a second contractor profile implemented as a large legacy
  modal. Contractor results now open the canonical `/contractor/<name>` dossier
  through keyboard-accessible links, removing the duplicate profile renderer.
- Contractor/entity dossiers and permit detail now match the property profile's
  navy header, typography and card language through `workspace.css`.
- Navigation, CRM and account pages had independent blue/purple palettes. Shared
  tokens now follow the latest teal workspace and preserve light/dark selection.
- Appearance initialization ran in navigation markup or a duplicated CRM script.
  `_theme_init.html` now runs in the document head before styles paint.
- Activity Logs now highlights its admin navigation entry.
- The local fixture server cached templates, hiding edits during visual review.
  Template reloading is now explicit, with entity and email-verification fixtures.

## Verification

Run `python3 -m unittest dashboard_html.test_navigation`, the existing Node
navigation/profile/owner-research regressions, and both browser scripts in
`dashboard_html/tests`. The browser scripts use synthetic local fixtures and do
not connect to production databases or perform paid lookups.

The viewport suite checks 37 routes at 320, 375, 768 and 1440 pixels, plus filter
persistence, map/tabs, CRM sheets, focus return, landscape navigation, dark mode
and reduced motion. The design suite checks 20 page types in both themes for
shared canvas/accent, shell assets, duplicate scripts, navigation, overflow,
vertical permit fields, appearance persistence and uncaught browser errors.

Database-backed integration cases require an explicitly configured disposable
test database. Skipped integration cases are not production validation.
