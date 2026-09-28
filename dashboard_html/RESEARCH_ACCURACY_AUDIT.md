# Prospect research accuracy audit — September 28, 2026

Scope: prospecting matching, source adapters, approval, source presentation, cancellation, and how research relates to the list’s relationship graph. Reviewed implementation and synthetic regressions, not the private live records behind the screenshot. No live finding about Jason Phillips was independently verified by this audit.

## Findings and repairs

| Issue | Prior behavior | Repair |
|---|---|---|
| Person identity too permissive | Exact name plus company could produce contact-field proposals. | Require exact normalized name plus matching email or phone, a single corroborated candidate, and a non-truncated candidate set. Name/company-only hits stay unchecked research. |
| Cached address treated as confirmed | A unique address in the local cache could become an automatically accepted parcel. | Only an explicit valid BBL confirms the parcel for this workflow. Cached addresses and geocoder results require review. BBL confirmation does not establish a person’s ownership or role. |
| Registry “exact” misunderstood | Provider-normalized company names could preselect company and corporate-role findings. Normalization can remove legal suffixes. | All name-only registry findings remain unchecked. Show query, returned legal name, DOS ID, match tier, and the origin of the query (imported company or a recorded property owner). |
| Previously purchased lookup reused as identity | Name and property correspondence could produce a new automatic phone/email proposal. | Keep these values as unchecked research candidates with the searched name, parcel, lookup date and returned value. Never run a new paid lookup. |
| Lien-history label incorrect | `tax_delinquency_count` is latest-cycle rows within a configured recency window, but the research UI called it historical entries. | Opt-in adapter evidence now supplies all returned historical notice-row count and up to three latest-cycle row excerpts. Preserve the old pipeline calculation; describe it explicitly in old cached findings. Notice rows are not distinct debts, proof of current debt, a completed sale, or personal liability. |
| Aggregate status labels imprecise | ECB “open” includes any ACTIVE record or a positive balance. | Name it “ECB active or balance-due records” and show the calculation. Explain HPD complaint deduplication, violation status filters, DOB totals, and Safety ACTIVE/PENDING classification. |
| Records appeared to verify the person | A property/CRM/company match shared a “Match found” label and a flat evidence list. | Say “Records found”; group by subject and record type. Explicitly distinguish the imported person from source parties and property facts. |
| Missing audit trail | Only the current source label and final findings were retained. | Persist source starts, response summaries, cache reuse, errors, completion and cancellation. Keep raw errors and CRM values out of the activity feed. Findings hold permission-checked detailed evidence. |
| Old runs lacked evidence | Old runs contain explanations but no structured source snapshot. | Mark legacy evidence as unavailable, uncheck defaults, retain stable finding indices, and require new research before applying old field proposals. Do not fabricate excerpts. |
| Grouping could collapse different parcels | Common HPD portal URL plus identical owner values do not identify a single property. | Include subject identity in research deduplication and approval storage. Preserve the same source fact separately for different parcels. |

## What the relationships mean

`prospecting_network.build` derives a list-private `associated_with` edge from the company text in the imported sheet. Its note says the role is unverified. `owns`, `works_at`, `manages`, and other specific edges require explicit user selection. Research approval stores selected findings (or explicitly approved mapped-field values); it does not write relationship edges or promote the lead to CRM.

A chain such as **imported company → name in an HPD managing-agent field → parcel records** is a research path, not proof that the imported person owns that parcel. The group connection and individual evidence explain each available step. Registry corporate agents retain their source role and are never promoted to owners.

## Source meaning and limits

- [NYC HPD registration](https://www.nyc.gov/site/hpd/services-and-information/register-your-property.page) distinguishes owners, managing agents and changing registration information. The adapter uses explicit owner contact types and keeps agents separate. Registration data may be stale; a missing registration is not proof of no owner.
- [NYC DOF lien sales](https://home4.nyc.gov/site/finance/property/property-lien-sales.page) describes notice lists and the sale process; [NYC’s lien-sale guidance](https://www.nyc.gov/site/nfp/taxes/nfp-lien-sale-information.page) explains that payment or an agreement can prevent a lien sale. A notice is not a current debt determination.
- ACRIS findings identify grantees on the selected latest deed by instrument date (recorded date fallback), with document ID and date. They are not a title opinion; partial interests, later corrections, and entity changes require record review.
- Historical assessment names end at FY2018/19. They are history, not current ownership claims.
- Permit searches are bounded to 20 recent records per dataset. Permit contacts are not current owners by default. Portal links may require entering the shown job number or DOS ID.
- Adapter field snapshots are labeled as such. Most adapters return normalized/aggregated data, not complete underlying documents. Lien excerpts are literal selected API row fields; no generated quotations or hidden reasoning are presented as evidence.

## Remaining practical limits

An exact name plus email/phone can still refer to stale, shared, or incorrect contact data. These checks provide evidence for human approval, not certainty. The imported BBL itself can be wrong. Name-only property/company candidates may be numerous, but stay separate and unchecked. Source outages remain distinct from no matches. Completed findings survive cancellation; unfinished results are fenced out, although an already-issued HTTP request may finish on the server. Older runs need rerunning to gain the new evidence and event history. Already-approved historical sheet values are not silently rewritten.
