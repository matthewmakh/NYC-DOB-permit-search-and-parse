# Sales CRM module

A namespaced sales CRM living inside the permit dashboard at `/crm`. Reps work
their calls here all day: buildings pulled straight from the scraped permit
database (or added by hand), one-tap **Contacted** logging on everything,
follow-up dates so nothing falls through the cracks, lists and starred
shortlists per rep, and a complete admin view of who did what, when.

## The golden rule

The scraper-owned tables (`permits`, `contacts`, `buildings`, ACRIS, …) are
**read-only to humans**. Every piece of human-entered data lives in `crm_*` or `prospect_*`
tables, joined to permit data by BBL. Scrapers never overwrite a rep-found
phone number; the CRM never writes to permit tables.

## Prospecting before CRM

`/crm/prospecting` accepts CSV/TSV work lists without creating contacts, buildings,
activities, or CRM follow-ups. Preview suggests common contact fields and allows
manual mapping, delimiter selection, and files without headers. UTF-8 (with or
without BOM), UTF-16 with BOM, and Windows-1252 are supported. Limits: 10 MB,
10,000 rows, 100 columns, 10,000 characters per cell. Excel workbooks must be saved
as CSV first. Duplicate rows are reported and preserved, not silently merged.

Every original column is retained under a stable positional ID, even when headers
repeat or are blank. The spreadsheet supports inline cell/status/date edits,
column selection, full-row search, sorting, pagination, and CSV export with
formula-injection protection. Imported source URLs open from the lead's research
section. Last touch and touch count are derived from logged outreach, not status
changes. Touch input and display use New York time; timestamps are stored in UTC.

Drag any column header, including tracking columns, to change its position.
Touch dragging, Alt + Left/Right on a focused header, and the Columns menu's
arrow buttons are supported. Order and hidden columns save to the account for
each list and restore across browsers. Each user's layout is independent, even
when an admin views the same sheet. A failed save stays visible with a retry
button; changing layout never modifies lead data or the shared list version.

Column edges resize with pointer/touch or arrow keys. The Columns menu can add
custom fields, rename imported/custom headers, pin one column on wide screens,
set widths, or reset the personal layout. Header renames keep field mappings and
original header labels. Custom fields and names belong to the sheet; widths,
pinning, visibility and order belong to each user's view. Pasted TSV ranges are
reviewed before replacing displayed editable cells (up to 1 MB). Tracking fields
are excluded from pasted ranges; values are never evaluated as formulas.

Select rows across pages (up to 500) to update status/follow-up dates, archive or
restore, or open a sequential CRM review queue. Filters clear selection. Bulk
edits are atomic, version checked and retry safe; one stale or inaccessible lead
rolls back the batch. Promoted rows cannot be bulk edited. Archive preserves
research and history and removes the lead from current/due views. One-level undo
persists per user/list for cell, row, bulk and paste edits, and refuses to overwrite
newer work or promoted records. Touch events and CRM promotion are not undoable
through this control.

**Add person** inserts a lead using the sheet's current columns. **Add CSV to this
list** previews incoming headers with existing-column/new-column/skip choices.
Imports append rather than overwrite, retain the source filename per row and
preserve original cells, notes and touches. Total sheet capacity remains 10,000
rows and 100 columns. Concurrent additions allocate unique positions and import
request keys prevent retry duplicates.

**Check duplicates** compares normalized valid emails/phones and identical source
rows within the sheet, plus CRM contacts the viewer can access. Archived rows are
included as possible matches. Results show reasons, bounded samples, and CRM
Do not contact/last-contacted details. The check never merges records or treats a
shared switchboard as a verified identity. Checks invalidate after edits; opening
a lead also checks current matches.

People, Companies and Buildings are separate views of the same private sheet.
Company values generate labeled, unverified associations, not management or
ownership assertions. Users can add company/building profiles with addresses,
websites and notes, and explicit works-at, manages, owns, contact-for, knows,
part-of, located-at or associated-with links. Company profiles group related
people and buildings; buildings also show people at managing companies. Derived
associations follow company-field edits without recreating deliberately unlinked
relationships. Links to existing accessible CRM contacts/buildings are references
only; they do not create CRM records. Reassignment rechecks and redacts inaccessible
CRM references. All profiles and links are list-scoped, including admin views;
cross-list/global profile merging is intentionally not automatic.

Reps see only sheets currently assigned to them; team admins can review their team's sheets.
Uploads default to the uploader. Members cannot choose another owner or reassign
sheets. Admins can choose an active teammate during import, filter sheets by
owner, and reassign any team sheet to themselves or another active teammate.
The uploader and touch authors remain immutable attribution. Changing the owner
preserves all rows, notes, and history, removes the former owner's access, and
records an admin audit event. List locks serialize reassignment against in-flight
work; versions reject stale assignment forms and old row drafts.
Reads, writes, exports, and promotion all enforce this scope. Mutations require a
session CSRF token. Optimistic versions reject stale edits; request keys prevent
duplicate imports/touches. `original_cells` preserves the uploaded data separately
from working edits. No imported content is executed or rendered as HTML.

**Add to CRM** is an explicit review form. One transaction creates or links a
contact, carries research, working notes, outreach timestamps, phone numbers, and
the next follow-up, and freezes the prospect row. New contacts and follow-ups belong to
the sheet's current assignee, including when an admin promotes on their behalf.
Reassigning a sheet does not transfer contacts already in CRM; their independent
CRM permissions still apply. Automatic matching requires an exact name plus matching email or
normalized phone; a shared switchboard alone is not a match. A CRM contact explicitly
linked from the lead's profile takes precedence after fresh permission and Do not
contact checks. Direct relationships are copied into the research note. Contacts assigned to
another rep or marked Do not contact block promotion. Repeated/concurrent
promotion requests cannot create duplicate contacts or activities. CRM work then
continues on the contact; the prospect list remains a historical record.

Implementation: `prospecting_service.py`, `prospecting_workflows.py`,
`prospecting_imports.py`, `prospecting_network.py`, `prospecting_routes.py`,
`templates/crm/prospecting.html`, and `static/{css,js}/prospecting*`. The additive
`prospect_lists`, `prospect_rows`, `prospect_touches`, `prospect_list_views`,
`prospect_changes`, `prospect_imports`, `prospect_entities`, and `prospect_links` tables initialize under
the existing startup schema lock in `init_crm_tables()`. The ownership migration
assigns existing sheets to their uploaders once; subsequent restarts preserve
reassignments. An insert-only default also covers uploads from older workers
during a rolling deployment.

Verification (use a disposable PostgreSQL, never production):

```sh
PROSPECTING_TEST_DATABASE_URL=postgresql://127.0.0.1:55443/postgres \
  uv run --with flask --with psycopg2-binary --with requests python -m unittest discover -s dashboard_html -p prospecting_tests.py -v
PROSPECTING_TEST_DATABASE_URL=postgresql://127.0.0.1:55443/postgres \
  uv run --with flask --with psycopg2-binary --with requests python dashboard_html/tests/prospecting_preview.py
playwright-cli -s=prospecting open http://127.0.0.1:5101/crm/prospecting
playwright-cli -s=prospecting run-code --filename=dashboard_html/tests/prospecting_browser_checks.js
playwright-cli -s=prospecting run-code --filename=dashboard_html/tests/prospecting_assignment_checks.js
playwright-cli -s=prospecting run-code --filename=dashboard_html/tests/prospecting_columns_checks.js
playwright-cli -s=prospecting run-code --filename=dashboard_html/tests/prospecting_workflows_checks.js
```

## Files

| File | Role |
|---|---|
| `crm_service.py` | Schema (`init_crm_tables()`, idempotent, runs at worker startup) + the whole data layer |
| `crm_routes.py` | Flask blueprint: pages under `/crm`, JSON APIs under `/crm/api` (including the saved-search API the Properties page calls) |
| `templates/crm/` | All screens; `_layout.html` (sidebar / tab bar shell), `_macros.html`, `_sheets.html` (dialogs + ⌘K palette); `partials/` are the fragments refreshed in place |
| `static/css/crm.css` | The CRM design system — Apple-leaning tokens (`--c-*`), light + dark, all classes `crm-`/`cbtn` prefixed |
| `static/js/crm.js` | Sheets, in-place partial refresh, ⌘K palette, shortcuts, board drag-and-drop, bulk bar, Focus mode |

Integration points elsewhere: one `register_blueprint` + `init_crm_tables()`
call in `app.py`, a nav item in `_site_nav.html`, the **Add to CRM** button on
`building_profile.html`, and on the Properties page: the per-card CRM button,
the **Quick look** panel's CRM section (stage and last touch from
`POST /crm/api/bbl-status`, plus **Log a contact**, which adds the building on
first use and then posts to `/crm/api/contacted` and `/crm/api/followup`), the
**Saved searches** menu, and bulk multi-select (card checkboxes → floating
"N selected" bar → **Add N to CRM** dialog with contact import and list
placement, via `POST /crm/api/bulk-add`, chunked 25 per request client-side).
Bulk contact import only takes permit contacts that have phones, and a number
the team already knows links the existing contact instead of duplicating it.

## Teams, roles, and visibility

There is deliberately **no new roles table**. The existing sponsored-account
system is the team system:

* A *team* is a sponsor account plus its active sponsored members
  (`account_sponsorships`). `team_id` on every CRM row is the sponsor's user
  id. An unsponsored account is a team of one.
* Sponsored members are the **reps**; the sponsor (and any `is_admin`
  account) is the team's **CRM admin** — they see the Team screen,
  everyone’s records, attribution, reports, change history, and CSV exports.
* A rep sees only buildings, people, deals, and lists currently assigned to
  them. This rule is enforced by the data
  layer for detail pages, search, queues, lists, bulk actions, and writes—not
  only hidden in the interface. A duplicate BBL or phone already claimed by
  another rep produces a generic collision warning without leaking the other
  rep’s record.
* `added_by_id` is permanent attribution for admins; it does not grant the
  original rep access after a transfer. `assigned_to_id` is the current owner.
  Admin transfers and offboarding change ownership without rewriting who
  originally sourced the record.
* Reps are created exactly like before: **Admin → Team accounts** invites.
  Give the invite a display name — it's the name shown all over the CRM.

## The v2 experience

* **Shell**: macOS-style translucent sidebar on desktop, iOS-style tab bar on
  phones; large titles; grouped inset cards; tinted secondary buttons and
  filled blue primaries; **Appearance** toggle (auto / light / dark) in the
  sidebar footer, applied before first paint from `localStorage`. It is the
  same setting as the moon/sun switcher in the site nav: one key (`theme`),
  so the CRM and the permit pages are always in the same mode, and Auto
  follows the system live.
* **No reloads for common actions**: regions marked `data-partial` re-fetch
  their HTML fragment from `/crm/partials/...` after a write.
* **⌘K / `/`**: global search over buildings (address, owner, BBL), people
  (name, company, phone digits) and lists, plus quick actions. `?` shows all
  shortcuts (`C` touch, `N` note, `F` follow-up, `V` visit, `G` then `T/B/C`).
* **Focus mode** (`/crm/focus`): one lead at a time from today's queue, a
  list, cold buildings, or needs-attention. `C` logs a touch (auto-completing
  the follow-up that put it in the queue), `→` skips.
* **Buildings** come as Cards, a drag-and-drop **Board** by stage, or a dense
  **Table**; select many for bulk stage / assign / list / star.
* **Building detail**: pipeline stepper (click a stage to move), a **Next
  step** card (or a nudge to set one), day-grouped timeline, people with
  editable roles, unlink, Open in Maps, inline edit of every field.
* **Contacts**: alphabetical with letter index and filter-as-you-type,
  possible-duplicates banner (shared numbers) with one-click **Merge**;
  contact detail has Contacts.app-style quick actions (Call / Text / Email /
  Follow up / List), edit, merge, delete (admin).
* **Team & reports**: touches-per-day columns, leaderboard, outcome mix,
  deal pipeline and value, conversation/meeting/win rates, overdue and
  missing-next-step hygiene, nurture health, attribution, feed, and view log.

## Current sales workflow

* **Deals are first-class records** linked to a building and/or person, with
  an owner, service, value, close target, next action, Nurture stage, and a
  required reason when marked Lost. A building can produce multiple deals.
* **Call queueing** lives in Focus. Reps can work Today, a list, cold leads,
  needs-attention records, Nurture, or the missing-next-step queue. Starting a
  phone call records the pending call locally and opens the outcome sheet when
  the rep returns; an outcome is required so reporting stays useful.
* **Next-step hygiene** appears on Today, Deals, Focus, and admin reporting.
  Timed follow-ups and dated deal actions both enter Today, the call queue,
  and notifications with a configurable reminder lead time.
* **Nurture stats** show assigned count, value, and missing next actions by
  rep. Nurture is also a dedicated call queue.
* **Rep offboarding** is an admin-only atomic transfer of buildings, people,
  deals, follow-ups, and lists. Original attribution remains, access is
  revoked, and active sessions are invalidated.
* **Change history** is admin-only and append-only. It records creation,
  edits, stage changes, transfers, merges, deletes, follow-up changes, deal
  changes, bulk stage changes, and offboarding.

## Notifications and installable app

Every account has an in-app inbox for assignments, deal changes, and timed
follow-up reminders. The same notifications can be delivered through Web Push
on desktop and, after using **Add to Home Screen**, on iPhone. The CRM ships a
web app manifest, service worker, per-device subscriptions, per-user delivery
preferences, and an idempotent reminder dispatcher.

Push delivery needs three Railway variables on both the web service and the
notification worker:

```text
CRM_VAPID_PUBLIC_KEY=<public VAPID key>
CRM_VAPID_PRIVATE_KEY=<matching private VAPID key>
CRM_VAPID_SUBJECT=mailto:<responsible-email>
```

Create a second Railway service from the same repository using
`railway.crm-notifications.json`; it runs
`dashboard_html/dispatch_crm_notifications.py` every five minutes. Give it the
same database variables as the web service. In-app notifications work without
VAPID, but desktop/iPhone push intentionally stays disabled until the matching
keys and worker are configured. Never commit the private key.

## Behaviors worth knowing

* **Contacted** inserts one append-only `crm_activity` row and, in the same
  transaction, maintains rollups (`last_contacted_at`, `contact_count`), auto
  bumps a `prospect` building to `contacted` on its first touch, and a
  `wrong_number` outcome marks the dialed phone bad.
* Note in the dialog is optional but nudged once ("Save without note" on the
  second press) — never blocked.
* The dialog warns when someone on the team already touched the lead in the
  last 24h (double-call collision guard).
* Authors can delete their own activity for 15 minutes (fat-finger undo);
  admins always can. Deletes recompute the rollups.
* Everything is editable after the fact: buildings, people, numbers
  (label / primary / delete), follow-ups (title / date / assignee / delete),
  building-person roles and links. Merging two people moves numbers, links,
  history, follow-ups, stars, and list items onto the kept record.
* Phones are stored with a normalized 10-digit key (`crm_phones.digits`)
  plus an optional `extension`. An extension can be typed into the number
  itself (`(212) 555-0100 x204`, `ext. 204`, `,204`, `#204`) or into the
  dedicated **Ext.** field — either way the extension never pollutes the
  10-digit key, and `tel:` links dial it (`tel:+12125550100;ext=204`).
* Duplicate numbers warn at entry (`409` with matches, `force: true`
  overrides), but an office main line with a *different* extension is not
  treated as a duplicate — several people share one switchboard number.
  Every phone and contact carries provenance (`source`, `source_detail`,
  `added_by`).
* **Save & add another** on the Add-person sheet keeps the dialog open and
  clears it, so a building's owner, super, and manager go in one after
  another; the People list updates behind the sheet as each one lands.
* Contacts have a `do_not_contact` flag; phones have `good/bad/do_not_call`.
* Follow-up dues use an America/New_York **date and time**; "today"/"overdue"
  in queues and counters are computed against the NY calendar, not UTC.
* Building, contact, deal, and list page views log to `crm_view_events`,
  debounced to one per user+entity per 30 minutes; view logging can never
  break a page.
* Saved lead lists are the Properties page's querystring saved verbatim
  (`crm_saved_filters`) — they re-run live as new permits arrive. See
  **Saved searches** below.
* `/crm/api/bbl-status` powers the "In CRM ✓" state on the permit-side
  buttons.

## Saved searches

The Properties page's whole view — every sidebar filter, the play, the sort
and the page size — saves under a name from the **Saved searches** button in
the toolbar, and one click puts it back.

* The button names the search on screen. Change a filter and it reads
  *“Name” (edited)* with an amber dot; the menu then offers **Update “Name”**
  next to **Save current search**. Clear all filters and it goes neutral.
* Each row in the menu describes itself in plain English — *Queens · 5–20
  units · Assessed $1.5M+ · Cash purchases* — built from the querystring, so
  a teammate can tell the searches apart without running them. Rows can be
  pinned to the top, renamed, re-pointed at the filters on screen, or
  deleted; the pencil, pin and bin only appear on searches you may change.
* **My team** searches are shared and also show up in the CRM under Lists as
  live lead lists. **Only me** searches stay private to their owner — team
  admins cannot see them either. Everything is team-scoped as usual.
* Running a search applies it in place (no reload) and rewrites the address
  bar, so the view stays linkable and Back still works. A link that happens
  to match a saved search is recognised as that search.
* Ordering is pinned first, then most recently run, so the searches the team
  actually works rise to the top. `last_used_at`/`use_count` track that.
* The page number is stripped on save: where someone was scrolled to is not
  part of what they meant to save. Sort and page size are kept.
* Rows carry a `page` column (`properties` today), so the contractors page
  can adopt the same menu without a second table:
  `GET /crm/api/saved-filters?page=<page>`.

## Street View

Building pages (the permit-side `/property/<bbl>` dossier, the CRM building
detail, and the Focus card) show Google Street View of the lot.

* Set **`GOOGLE_MAPS_EMBED_KEY`** (Google Cloud → APIs & Services → enable
  *Maps Embed API* → create an API key restricted to *HTTP referrers*
  `permits.up.railway.app/*` and any custom domain) and the view is
  **embedded** in the page. Google prices Maps Embed API requests at $0.
* The primary **Open in Google Maps** link uses Google's supported address
  search URL (`maps/search/?api=1&query=...`). It does not force a panorama.
  Numeric borough codes are converted to names; records without an address
  use verified coordinates when available.
* **Where the pin goes** (`streetview.resolve`): NYC Planning's GeoSearch
  verifies the address against the BBL, then falls back to consistent DOB
  permit coordinates. Results are cached in `building_geocodes`.
* The property profile offers a separate **Street View nearby** link only
  when coordinates are available (`maps/@?api=1&map_action=pano&viewpoint=...`).
  This requests nearby imagery, not a guarantee of coverage. The embedded
  panorama searches within 100 metres for outdoor imagery. The normal map
  link remains available even when Google has no Street View coverage.

## Migration story

Purely additive: `init_crm_tables()` is a list of `CREATE TABLE IF NOT
EXISTS` / `CREATE INDEX IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`
statements executed at startup from
`init_db_pool()` (same pattern as `team_service.init_team_tables()`). No
scraper-owned table is ever touched, and the only ALTERs add nullable
columns to `crm_*` tables. Safe on a live database; a failed init never
blocks worker boot.
