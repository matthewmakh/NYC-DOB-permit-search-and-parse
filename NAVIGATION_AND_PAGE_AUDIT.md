# Branch and page design audit — October 15, 2026

## Authenticated live-site follow-up

The follow-up audit signed in on `leads.installersny.com` and checked public,
CRM and administrator pages, including existing property, contractor, entity,
CRM building/contact and prospect-list details. Data requests succeeded for all
five primary discovery lists. Desktop checks found no missing shared stylesheet,
legacy page stylesheet, outer overflow, uncaught script error or failed page
request on the checked CRM and administration routes.

Repairs from this audit:

- Account recovery referenced a missing template and returned HTTP 500. It now
  has a matching account page with administrator contact. Production has no
  email delivery configuration; automatic password reset remains unavailable.
  Legacy reset/verification routes no longer falsely claim an email was sent.
- Login dropped the intended destination on form submission. It now preserves
  relative paths and filters, and rejects external redirect targets.
- The property contact directory advertised an unfinished unlock action. It now
  confirms the charge and uses the existing enrichment/billing endpoint, with
  cancellation, failure and retry handling. Enriched contacts also load when
  the original permit-contact list is empty.
- Buyers and alerts could remain on Loading forever after network/HTML errors,
  and older requests could overwrite newer searches. They now recover with a
  Retry action and ignore superseded responses. Permit loading also has Retry.
- Permit oldest/address/applicant/type options silently sorted newest-first.
  Every advertised sort now reaches an actual server ordering. Lead-score and
  contact filters apply before pagination; unsupported 500/1,000 page sizes
  were removed to match the API's 200-record cap.
- Imported permit issue dates included years 2103–2200 and were promoted as
  current hot leads. Permit time predicates now exclude future issue dates;
  scores and contractor recent-activity counts also reject them. Original
  database source records were preserved. Date-only permit display uses UTC
  to avoid appearing one day early in New York.
- Generated permit cards and map popups retained hard-coded white surfaces.
  They now use shared theme tokens; cards are real keyboard-accessible links.
- Contractor pagination repeated an expensive aggregation for its count. The
  normal page now obtains its count in the main query, with an explicit empty
  page fallback. A read-only production comparison retained the same 151,709
  participants and identical page identities, reducing elapsed time from
  35.1 to 28.3 seconds. Uncached directory searches remain slow.

Verification includes 124 account/CRM/permit/prospecting tests against disposable
local schemas, six navigation tests, the existing Node regression suites, all
14 offline Python suites, 152 page/viewport checks, 42 page/theme checks, and
synthetic browser checks for failure recovery and unlock billing. Prospecting
browser workflows cover import, mapping, editing, column manipulation,
assignment, bulk actions, relationships, research review/approval and undo.
Production record changes, real paid lookups and real card charges were not
used as test fixtures. Browser viewport checks do not certify a physical phone's
keyboard or safe-area behavior.

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
Railway URL served the redesign. The user confirmed that `permits.up.railway.app`
was the initial canonical URL. The user subsequently requested moving
`leads.installersny.com` to that same current application and retiring the old
production web service after the custom domain is verified.
`DOB-Dashboard` now follows `main`; `staging` is synchronized by fast-forward for
the current preview and existing pipeline sources. Do not develop independent
designs on the two branches or assume a successful GitHub merge proves a
deployment is healthy. The phone-check cron also remained on `html-dashboard`; it is separate from the page reconciliation.
The enrichment cron reported `CRASHED` at the initial audit; its latest run was
`SUCCESS` at the domain-transfer audit. The permit cron's latest run then reported
`FAILED`; neither observation is a diagnosis of its runtime behavior.

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

## Reconciliation result

PR #11 merged the complete application and shared design into `main`, preserving
all 87 previous staging commits. The checkout now tracks all remote branches and
uses `main`. Remote `staging` was fast-forwarded to the same merge commit.
Production `DOB-Dashboard` is configured to deploy `main` with its existing
configuration and database. The application initially used https://permits.up.railway.app; the subsequent
custom-domain cutover is documented below.

Local verification passed 148 viewport page checks, 40 page/theme checks, all
Node navigation/profile/owner-research regressions, six navigation/design-shell
checks and all 14 offline Python regression suites. The optional database-backed
integration cases remained skipped. The existing deployment health endpoint was
HTTP 200 with its database connected during the rollout; verify the new Railway
deployment's commit and status separately before declaring that rollout complete.

## Database dependency correction and domain transfer

The initial comparison of database host settings suggested separate production
databases. A full dependency check corrected that finding: the old application's
public endpoint `maglev.proxy.rlwy.net:26571` belongs to the same production
Postgres used by `DOB-Dashboard` at `postgres.railway.internal:5432`. Database,
user and password equality were verified without exposing credential values.
There is no separate obsolete production database to delete. Keep production
Postgres service `36c7f698-67d4-4d31-a866-1807a4570b2a` in environment
`b6e062af-d655-4f64-a83b-b094eafc7327`; the current app and pipeline services
actively depend on it.

The retired legacy production web instance was
`b091d649-80bc-4ba4-a2d3-969497da3bc1` in that production environment. The same
service ID has a separate, updated staging instance, which was retained. Only
the obsolete production instance was deleted.

Railway attached `leads.installersny.com` to current service `DOB-Dashboard` on
port 8080. GoDaddy's `leads` CNAME was changed from
`2ldtpjav.up.railway.app` to `d3hy1bc5.up.railway.app`, and a dedicated
`_railway-verify.leads` TXT ownership record was added. Both authoritative
nameservers and public resolvers return the new values. Railway verified ownership and issued valid HTTPS certificates after propagation. The existing app remains available at `permits.up.railway.app`.
Custom-domain HTTPS, the current sign-in design and the read-only health check
all passed; the health endpoint reports its database connected. The obsolete
production web instance was deleted after those checks. A fresh service inventory
confirmed it was gone from production, the current app and Postgres were retained,
and the updated staging instance was still present. A post-removal health check
on the custom domain returned HTTP 200 with the database connected.
The canonical public URL is https://leads.installersny.com; the Railway URL remains
available as a fallback.

Final operational verification also showed the production permit and enrichment
cron services reporting `SUCCESS`. No database was deleted or migrated during
this domain cutover. DNS records outside `leads` and `_railway-verify.leads` were
left unchanged.
