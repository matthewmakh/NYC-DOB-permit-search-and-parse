# Owner Name Sources

The buildings table tracks owner information from **4 separate sources**. Each source provides a different perspective on property ownership:

## Source Columns

| Column | Source | Description | Use Case |
|--------|--------|-------------|----------|
| `current_owner_name` | PLUTO (MapPLUTO) | Corporate owner name from city GIS data | Most reliable for corporate entities |
| `owner_name_rpad` | Historical RPAD assessment | Historical assessed owner (through FY2018/19) | Historical ownership context |
| `owner_name_hpd` | HPD Registration | Registered owner with Housing Preservation | Required for rental properties |
| `ecb_respondent_name` | ECB Violations | Respondent on ECB violations | Property manager or responsible party |

## Why Multiple Sources?

Different city agencies maintain their own owner records:
- **PLUTO**: Geographic/planning perspective (corporate entities)
- **RPAD**: Historical assessment perspective (the published source ends in FY2018/19)
- **HPD**: Housing compliance perspective (registered managing agent)
- **ECB**: Enforcement perspective (who responds to violations)

## ECB Respondent Details

When ECB violations exist, we also capture:
- `ecb_respondent_address` - Full address
- `ecb_respondent_city` - City
- `ecb_respondent_zip` - ZIP code

This often identifies the **property manager** or **LLC manager** who handles city violations, which can be different from the deed owner.

## Frontend Usage

When displaying owner information, show all available sources:
```python
owner_sources = []
if building.current_owner_name:
    owner_sources.append(f"PLUTO: {building.current_owner_name}")
if building.owner_name_rpad:
    owner_sources.append(f"Tax Records: {building.owner_name_rpad}")
if building.owner_name_hpd:
    owner_sources.append(f"HPD Registration: {building.owner_name_hpd}")
if building.ecb_respondent_name:
    owner_sources.append(f"ECB Respondent: {building.ecb_respondent_name}")
```

This gives users the most complete picture of property ownership and management.

## Properties: person-owner filter

`has_person_owner=true` (the **Has a person listed as an owner** checkbox)
checks the latest deed buyer, PLUTO, HPD owner and RPAD fields, plus a Secretary
of State principal whose entity does not conflict with the recorded owners.
A registered/service-of-process agent alone does not qualify. An independently
recorded person in an owner field still qualifies, even when the same property
also has an agent. Managing agents, site managers, sellers and care-of mailing
recipients are not additional owner sources for this filter.

The filter uses the local `probablepeople==0.5.6` model with the existing NYC
organization exclusions. Semicolon/newline-separated parties and recognizable
household names qualify. Classification is probabilistic, not identity
verification; ambiguous or unrecognized names can be missed. Agent exclusion
uses recorded roles rather than trying to infer a profession from a name.

Filtering runs before counts, pagination and export/bulk limits. Name decisions
are cached in a bounded process-local cache; matching IDs are cached for five
minutes independently of pagination and sorting. The first uncached request
scans the candidate owners, so production latency depends on the candidate set.
No migration or classification backfill is needed. Install the updated dashboard
requirements when deploying. Existing `owner_kind` filters retain their meaning.

Regression checks: `python person_owner_filter_tests.py`,
`python filter_param_tests.py`, and `node properties_navigation_tests.js`.
Set `PERSON_OWNER_TEST_DATABASE_URL` to run the PostgreSQL integration checks;
they use temporary tables and roll back their fixtures.
## Public source links

The property profile links each displayed owner name to its source:

- ACRIS: the document matching `sale_crfn`, or the primary deed when no CRFN
  is stored; otherwise the lot's ACRIS search. CRFNs are never used as document IDs.
- PLUTO: the parcel's ZoLa page.
- RPAD: the original historical assessment table, with the BBLE to search.
- ECB: the BIS OATH/ECB list for the building BIN; if the BIN is missing,
  the BIS lot page prompts the user to choose the building.
- HPD: HPD Online, with the address to search.
- NY DOS: the public entity search, with the DOS ID or entity name to search.

DOB NOW and NY DOS record pages depend on a search session, so their links
open the working public portal and show the lookup value with a copy button.
HPD and RPAD search hints also offer a copy button. The collapsed Data sources
section links to each official public page and makes direct versus search-only
destinations clear. Permit links use
`api_source` and `job_number`; DOB NOW records override stale stored BIS links.
ACRIS transaction document IDs also link directly to the official document.
