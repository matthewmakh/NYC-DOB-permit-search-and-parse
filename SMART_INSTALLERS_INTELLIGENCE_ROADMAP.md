# Smart Installers Intelligence Roadmap

## Phase 1 — sales operating layer (implemented in this branch)

- Preserve numeric DOB project cost, proposed/existing units and stories, and status dates.
- Consolidate filing and permit stages under a source-qualified project key.
- Label permit participants by source role and expose role/contractor confidence separately.
- Add DOB NOW Electrical Permit Applications and Electrical Details, including licensed electrician/firm, work flags, meters, scope items, devices, and floors.
- Add DOB NOW Elevator Applications with applicant, design professional, owner, device, work type, cost, milestone dates, and project alerts.
- Add City Record procurement notices as separately ranked pre-permit signals rather than pretending they are DOB projects.
- Add Smart Installers-specific property plays without replacing the existing investor/contractor plays.
- Rank repeat owner/buyer accounts by distinct consolidated projects, recency, stated project cost, and Smart Installers fit.
- Add account/project/property watchlists, live daily changes, stored digest generation, CRM CSV export, and an opt-in CRM webhook push.

The repeat-buyer grouping in Phase 1 deliberately performs only conservative normalized exact-name matching. It removes punctuation and case but does **not** claim that two different LLC names have the same parent.

## Phase 2 — project-participant graph

Create stable nodes and evidence-backed edges instead of flattening every identity onto a permit row.

### Core nodes

- `property` — BBL/BIN/address
- `project` — DOB job, private bid project, development announcement
- `organization` — developer, owner, GC, architect, engineer, permittee, subcontractor, manager
- `person` — principal, employee, applicant, filing representative
- `source_record` — immutable evidence from DOB, ACRIS, HPD, news, bid platform, or plan room

### Core edges

- organization `OWNS` property
- organization `DEVELOPS` project
- organization `GENERAL_CONTRACTOR_FOR` project
- organization `DESIGNS` project
- organization `ENGINEERS` project
- organization `PERMITTEE_FOR` project
- organization `SUBCONTRACTOR_FOR` project
- person `PRINCIPAL_OF` organization
- project `LOCATED_AT` property
- source record `SUPPORTS` edge

Every edge needs `role`, `valid_from`, `valid_to`, `confidence`, `source`, `source_record_id`, and `observed_at`. A salesperson must be able to see *why* the system thinks a company is the GC.

## Phase 3 — reliable entity resolution

Use a two-stage resolver:

1. Deterministic candidates: normalized name, licence/registration number, exact phone/domain/address, ACRIS party/address, SOS DOS ID, repeated principals.
2. Scored resolution: name similarity plus shared principals, addresses, domains, licences, and co-occurrence. Auto-merge only above a high threshold; send borderline pairs to a review queue.

Keep three identifiers separate:

- `entity_id` — one legal organization
- `corporate_family_id` — affiliated legal organizations controlled together
- `display_account_id` — the account a salesperson works in the CRM

Never use fuzzy name similarity alone to merge property LLCs. Affiliation should require corroborating evidence such as common principals, addresses, domains, registrations, or repeated project teams.

## Phase 4 — pre-permit external project intelligence

Add source records before trying to resolve them into the graph. Suggested order:

1. NYC DCP ZAP/ULURP and BSA land-use applications — entitlement activity before DOB filings.
2. NYC City Record and public-agency procurement notices — public bids and awards.
3. DOB NOW Elevator device details and safety compliance — device-level modernization and required work beyond the application header now ingested.
4. ACRIS construction mortgages and recent deeds — capital deployment and ownership changes.
5. Private plan rooms and bid platforms (BuildingConnected, ConstructConnect, Dodge, Blue Book) — invitations, plan holders, bidders, and due dates.
6. Development and real-estate news — announced projects and named teams, stored as evidence rather than accepted as fact without corroboration.

The first useful graph release should answer one question reliably: **“Who repeatedly buys buildings, which projects are moving now, and who can introduce Smart Installers before the technology scope is awarded?”**
