# Enrichment reliability repairs — September 17, 2026

The audit found that failed or incomplete requests could be treated as successful refreshes, some historical records could keep obsolete ownership facts, and contact lookup storage was not safely separated from payment and access.

## Implemented

| Area | Correction |
| --- | --- |
| PLUTO / RPAD / HPD | Independent source checkpoints, schedules, last errors and retry times. Successful sources are retained when another source fails. Authoritative empty responses clear obsolete source facts while preserving parcel identity and coordinates. |
| Capacity | Property refresh supports six workers and 16,000 properties per pass, with bounded work submission and an error circuit breaker. PLUTO refreshes every 30 days, historical RPAD every 365 days, HPD every 14 days. |
| DOB Safety | One complete citywide aggregate sweep replaces a daily 15,000-property loop. Failed, truncated or empty snapshots cannot clear existing counts. The aggregate query was verified against the public API. |
| ACRIS | Reference and remark failures fail the refresh; pagination cannot silently truncate histories; empty histories clear stale transactions and summaries; failed writes roll back. Logic version 6 schedules old records for recalculation. |
| Purchase classification | Nominal and partial-interest transfers remain unknown. Absence of a purchase mortgage is labeled “likely cash,” rather than proof of cash payment. |
| Lien notices | UI and risk scoring distinguish published lien-sale notices from verified current unpaid debt. Unparseable dates produce an error. Historical notice data remains stored. |
| Owner identity | A deed grantee overrides older company names for SOS matching. HPD officers/agents are not substituted for explicitly registered owners. Current individual ownership stops fallback to a former LLC. |
| Paid provider identity | Both providers require a matching person and corroborated street address, including historical addresses. A name in the same city or ZIP alone is insufficient. Permit lookups use the actual property address and must name a contact present in the property’s permit records. |
| SOS response parsing | Whitespace-only names and null addresses no longer crash an otherwise valid entity response. Empty company-type placeholders are not retried as business identities. |
| Paid access | Verified owner results are saved separately from user access. Access is granted after a confirmed payment or complimentary authorization. Starting a permit lookup no longer implicitly unlocks its result. Bulk export charges only after successful lookup. Permit-contact exports combine successful results into the quoted total ($0.35 each, $0.50 minimum), with durable purchase groups and atomic access grants shared by single unlocks and exports. |
| Payment recovery | Exact payment requests and successful receipts are durable. Retries reuse receipts and idempotency keys. Unknown outcomes older than 23 hours stop for reconciliation instead of risking another charge. Audit entries are not duplicated by a payment retry. |
| Bulk jobs | Candidate sets and per-owner outcomes are persisted. A job freezes its successful set before billing, saves the payment receipt, then grants access transactionally. Cross-worker locks and periodic recovery allow interrupted jobs to resume. |
| Scheduling | Property jobs recover periodically; live workers retain their jobs even if a timestamp lease expires. Pipelines cannot overlap, individual steps have time limits, failures exit unsuccessfully, and retry ordering prevents repeatedly failing rows from monopolizing a batch. |
| Signals | Missing BINs no longer imply zero DOB complaints. LL84 and speculation rows paginate fully. Version 3 queues recalculation under the revised semantics. |

## Validation

The full offline and disposable-PostgreSQL regression pass covers pipeline mappings, person-owner filtering, source links, geocoding/SOS retries, billing, sponsored accounts, and CRM behavior. The new reliability suite exercises partial outages, authoritative empty data, caps, unknown identities, payment failure, receipt recovery, interrupted bulk jobs, live-worker locks, repeatable migrations, and idempotent derived-flag repairs. No paid vendor or real Stripe request is made by these tests.

The production column constraints were inspected read-only before the migration was finalized. The dashboard’s Railway deployment is connected to this repository’s `staging` branch.

## Rollout and record repair

- Dashboard startup creates the payment/result/job tables and property checkpoint schema. Pipeline startup runs `migrate_enrichment_reliability.py` before using the new columns.
- `repair_enrichment_flags.py` previews unsupported cash classifications and out-of-window lien flags. `--apply` changes only derived flags, preserving prices, transfer percentages, and notice dates. Repeated application makes no further changes.
- `sync_dob_safety.py` refreshes every tracked parcel after obtaining a complete public-source snapshot.
- `step5_enrich_from_sos.py --retry-failures --limit 100` retries saved SOS failures explicitly, bypassing the normal cooldown.
- The first property pass seeds independent checkpoints from real source fetches; legacy success dates are not invented. Versioned ACRIS and signal backfills continue in bounded batches. Shipping the code does not mean all historical records have already been fetched again.

## Operating limits

External sources can still be unavailable or incomplete. An old RPAD extract remains historical, and a lien notice does not establish a current balance. Recorded financing history remains an inference within the configured ACRIS retention period. Stronger contact matching intentionally rejects ambiguous people. An uncertain payment beyond the provider’s idempotency window requires receipt reconciliation; it is never blindly charged again. A hard crash after a vendor finishes a lookup but before its response is saved can still require another vendor lookup, although customer access and billing are protected by the durable payment path.

## Verified production recovery

- Both the dashboard and scheduled enrichment service deployed commit `68239a6`; the dashboard health endpoint reported a connected database and healthy service.
- Corrected 22,804 unsupported cash labels without changing the source transaction amounts or percentages.
- Retried all 42 saved property-source failures successfully.
- Live retries exposed a whitespace-name parser bug in SOS. After correcting it, all 17 saved SOS errors were resolved: 15 valid company lookups succeeded and two invalid/non-company candidates were cleared.
- No paid-provider or customer-charge requests were made during this recovery.

Stripe's [minimum-charge documentation](https://docs.stripe.com/currencies#minimum-and-maximum-charge-amounts) confirms why separate 35-cent charges must be combined into the batch total already displayed by the export estimate.
