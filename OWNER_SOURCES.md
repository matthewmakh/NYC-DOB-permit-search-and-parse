# Owner Name Sources

The buildings table tracks owner information from **4 separate sources**. Each source provides a different perspective on property ownership:

## Source Columns

| Column | Source | Description | Use Case |
|--------|--------|-------------|----------|
| `current_owner_name` | PLUTO (MapPLUTO) | Corporate owner name from city GIS data | Most reliable for corporate entities |
| `owner_name_rpad` | RPAD (Property Tax) | Taxpayer of record | Most up-to-date for tax purposes |
| `owner_name_hpd` | HPD Registration | Registered owner with Housing Preservation | Required for rental properties |
| `ecb_respondent_name` | ECB Violations | Respondent on ECB violations | Property manager or responsible party |

## Why Multiple Sources?

Different city agencies maintain their own owner records:
- **PLUTO**: Geographic/planning perspective (corporate entities)
- **RPAD**: Tax assessment perspective (who pays taxes)
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
