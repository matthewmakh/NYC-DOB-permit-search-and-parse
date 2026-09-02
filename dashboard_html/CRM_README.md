# Sales CRM module

A namespaced sales CRM living inside the permit dashboard at `/crm`. Reps work
their calls here all day: buildings pulled straight from the scraped permit
database (or added by hand), one-tap **Contacted** logging on everything,
follow-up dates so nothing falls through the cracks, lists and starred
shortlists per rep, and a complete admin view of who did what, when.

## The golden rule

The scraper-owned tables (`permits`, `contacts`, `buildings`, ACRIS, …) are
**read-only to humans**. Every piece of human-entered data lives in `crm_*`
tables, joined to permit data by BBL. Scrapers never overwrite a rep-found
phone number; the CRM never writes to permit tables.

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
the **Saved searches** menu, and bulk multi-select (card checkboxes → floating
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
  account) is the team's **CRM admin** — they see the Team screen
  (performance, activity feed, view log), everyone's stars, and CSV exports.
* Row visibility everywhere: `team_id = <my team> OR team_id IS NULL`.
* Reps are created exactly like before: **Admin → Team accounts** invites.
  Give the invite a display name — it's the name shown all over the CRM.

## The v2 experience

* **Shell**: macOS-style translucent sidebar on desktop, iOS-style tab bar on
  phones; large titles; grouped inset cards; tinted secondary buttons and
  filled blue primaries; **Appearance** toggle (auto / light / dark) in the
  sidebar footer, applied before first paint from `localStorage`.
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
  pipeline funnel, plus the performance table, feed, and view log.

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
* Follow-up dues are **dates** in America/New_York; "today"/"overdue" in
  queues and counters are computed against the NY calendar, not UTC.
* Entity page views log to `crm_view_events`, debounced to one per
  user+entity per 30 minutes; view logging can never break a page.
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
* Without the key, the same spots show an **Open Street View** button that
  deep-links into Google Maps (keyless Maps URLs API) — nothing breaks.
* **Where the pin goes** (`streetview.resolve`): first NYC Planning's free
  GeoSearch API — the official address point for the house number, accepted
  only when it comes back with the lot's own BBL (or an exact same-house,
  same-borough match). Answers, including misses, are cached in
  `building_geocodes` so each lot is looked up once. Second, the geocode on
  the lot's permits, taking the newest permit that agrees with the others on
  the lot and sits inside its borough; rounded placeholders, borough
  centroids, and lone outliers are ignored. If neither works the button
  becomes **Open in Google Maps** (an address search) rather than a Street
  View link that opens on a black screen.
* The keyless link uses the classic `layer=c&cbll=` form, which snaps to the
  nearest panorama. The Maps URLs API `map_action=pano&viewpoint=` form was
  dropped: it shows "No Street View imagery available here" whenever nothing
  was photographed within 50 m of the point.

## Migration story

Purely additive: `init_crm_tables()` is a list of `CREATE TABLE IF NOT
EXISTS` / `CREATE INDEX IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`
statements executed at startup from
`init_db_pool()` (same pattern as `team_service.init_team_tables()`). No
scraper-owned table is ever touched, and the only ALTERs add nullable
columns to `crm_*` tables. Safe on a live database; a failed init never
blocks worker boot.
