# Entity research

Research a person or company by name across NYC public records, then link the
results to what the dashboard already holds. Two entry points share one
backend:

1. **Home page, "Public records" mode.** The search bar has a toggle: *Our data*
   (the existing universal search) and *Public records*. In public-records mode
   a name goes straight to a dossier. In our-data mode, a name that matches
   nothing offers the public-records search instead of a dead end, and the
   grouped search-results page shows the same offer in its empty state.
2. **Any name on a property profile.** Owner names (PLUTO, RPAD, HPD, ACRIS
   grantee, ECB respondent, NY DOS principal), prior deed owners, every party
   on a recorded transaction, permit applicants and permittees, permit
   contacts and owner-research people are links into a dossier. The link
   carries the property's BBL and address as context, which the research page
   uses to corroborate matches. The arrow next to an owner name still opens
   the public source record.

## What a dossier contains

| Section | Source | How it is found |
|---|---|---|
| In our database | buildings, permits, permit contacts, local ACRIS mirror | ILIKE across owner, applicant, permittee, filing-representative and party-name columns |
| Deeds & mortgages | ACRIS Real Property Parties + Master + Legals | party name, then documents resolved to lot, instrument, amount and counterparties |
| Permits & jobs | DOB BIS permits, DOB NOW job filings, DOB NOW approved permits | owner, applicant, permittee and filing-representative name fields |
| Registrations, violations & cases | HPD registration contacts (+ registrations), ECB violations, HPD housing litigation | contact first/last or corporation name; respondent name |
| Registered entity | NY Department of State public inquiry | entity name only (people cannot be searched directly) |
| Connections | derived | every other name on the same records, ranked by shared records and lots |
| Your team's CRM | crm_contacts, crm_buildings, crm_owner_research | joined live per team, never stored in the dossier; do-not-contact flags are shown |

Every stored row is **evidence**, not identity. Rows carry a match tier:

- **Corroborated (strong)**: the complete name agrees *and* the record's party
  address matches an address already linked to the name (two separate exact
  records sharing an address, or the click context), or the record is on a
  lot the click context or our database already ties to the name.
- **Name match (exact)**: the complete normalized name agrees (`SMITH, JOHN` =
  `John Smith`; `ABC REALTY, L.L.C.` = `ABC Realty LLC`).
- **Partial (candidate)**: the searched name is contained in a longer name.
  Hidden by default on the property list.

### Expand connections (second hop)

After the first pass, *Expand connections* follows up to five connected names
one hop out: for a company, its NY DOS principals and the people recorded as
owners or officers beside it; for a person, the companies they appear with on
deeds, registrations and permits. Hop rows are always shown as describing the
connected name, never as the subject's own records, and never count toward
the subject's totals.

## Lifecycle and limits

- **Nothing is added to the buildings table automatically.** Each lot in a
  dossier has *Add permanently*, which runs the existing free auto-add flow
  (`property_lookup.auto_add_property`) for that one BBL.
- **Retention.** A dossier expires 60 days after it was last opened or
  researched. *Keep permanently* turns that off. The background worker purges
  expired dossiers hourly; evidence and jobs cascade.
- **Cache.** Public-record results are cached per dossier for 24 hours.
  *Refresh sources* re-runs them. The internal lookup runs on every pass.
- **Bounds.** ACRIS party rows are capped at 2,000 per search and 600
  resolved documents (most recent first); HPD, DOB and ECB at 500 each;
  litigation at 300. The page says when a cap was hit.
- **Rate limit.** At most 12 queued or running jobs per user.
- **Visibility.** Dossiers hold public-record evidence and are shared by
  every signed-in user; the saved/recent index lists all of them. Only the
  CRM panel is team-scoped, and it is computed per request.
- **Paid lookups never run from a dossier.** Enformion and Apify stay behind
  the explicit Enrich click on the property profile.

## Implementation

| File | Purpose |
|---|---|
| `dashboard_html/entity_research.py` | schema (`entity_dossiers`, `entity_research_jobs`, `entity_evidence`), name normalization and tiers, internal lookup, CRM join, job queue and worker |
| `dashboard_html/entity_sources.py` | Socrata and NY DOS adapters, step plans, expansion targets |
| `dashboard_html/entity_routes.py` | pages and API, read models (property grouping, connections, expansions) |
| `dashboard_html/templates/entity_profile.html`, `entities.html` | dossier page and saved/recent index |
| `dashboard_html/static/js/entity_profile.js`, `static/css/entity.css` | page rendering, job polling, actions |
| `dashboard_html/static/js/home.js`, `templates/home.html` | search-mode toggle and public-records offer |
| `dashboard_html/static/js/building_profile.js` | `entityNameLink` on every rendered name |

Tables are created at app start by `entity_research.init_tables`; the worker
thread starts with `entity_research.start_worker` (one per web worker,
coordinated with `FOR UPDATE SKIP LOCKED`). Socrata queries combine the
indexed full-text `$q` parameter with an exact `$where` on the name column and
fall back to `$where` alone if a dataset rejects `$q`. Set
`SOCRATA_APP_TOKEN` for higher rate limits.

Writes require a same-origin JSON request with the `X-Entity-Research: 1`
header, mirroring the owner-research endpoints.

## Routes

```
GET  /entities                        saved and recent research
GET  /entity/research?name=&bbl=&role=&address=&source=
GET  /entity/<id>
POST /api/entity/research             {name, context, force}
GET  /api/entity/<id>
GET  /api/entity/jobs/<job_id>
POST /api/entity/<id>/refresh
POST /api/entity/<id>/expand
POST /api/entity/<id>/keep            {permanent}
POST /api/entity/<id>/add-property    {bbl}
GET  /api/entities
```

## Tests

```
python entity_research_tests.py
node building_profile_permit_tests.js
node owner_research_ui_tests.js
ENTITY_RESEARCH_TEST_DATABASE_URL=postgresql://localhost/entity_test \
    python entity_research_integration_check.py
```

The unit suite needs no network or database: Socrata and NY DOS are faked
and SQL builders run against a recording cursor that checks placeholder
counts. The integration check exercises the real schema, worker, tiering,
cache, expiry, team-scoped CRM join and routes against a disposable
PostgreSQL database. It drops that database's public schema, so it refuses to
run unless the database name contains "test".
