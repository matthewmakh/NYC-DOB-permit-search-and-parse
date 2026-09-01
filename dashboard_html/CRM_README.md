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
| `crm_routes.py` | Flask blueprint: pages under `/crm`, JSON APIs under `/crm/api` |
| `templates/crm/` | All screens; `_macros.html` and `_modals.html` are shared building blocks |
| `static/css/crm.css` | CRM styles on top of the `app.css` design tokens (all classes `crm-`prefixed) |
| `static/js/crm.js` | The Contacted dialog, stars, follow-ups, lists, people dialogs |

Integration points elsewhere: one `register_blueprint` + `init_crm_tables()`
call in `app.py`, a nav item in `_site_nav.html`, the **Add to CRM** button on
`building_profile.html`, and on the Properties page: the per-card CRM button,
**Save as lead list**, and bulk multi-select (card checkboxes → floating
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
* Phones are stored with a normalized 10-digit key (`crm_phones.digits`);
  duplicate numbers warn at entry (`409` with matches, `force: true`
  overrides). Every phone and contact carries provenance (`source`,
  `source_detail`, `added_by`).
* Contacts have a `do_not_contact` flag; phones have `good/bad/do_not_call`.
* Follow-up dues are **dates** in America/New_York; "today"/"overdue" in
  queues and counters are computed against the NY calendar, not UTC.
* Entity page views log to `crm_view_events`, debounced to one per
  user+entity per 30 minutes; view logging can never break a page.
* Saved lead lists are the Properties page's querystring saved verbatim
  (`crm_saved_filters`) — they re-run live as new permits arrive.
* `/crm/api/bbl-status` powers the "In CRM ✓" state on the permit-side
  buttons.

## Migration story

Purely additive: `init_crm_tables()` is a list of `CREATE TABLE IF NOT
EXISTS` / `CREATE INDEX IF NOT EXISTS` statements executed at startup from
`init_db_pool()` (same pattern as `team_service.init_team_tables()`). No
existing table is altered. Safe on a live database; a failed init never
blocks worker boot.
