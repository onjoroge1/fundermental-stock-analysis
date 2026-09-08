# Earnings-release EPS and historical estimate recovery

Reviewed September 8, 2026. Quarterly EPS recovery and historical analyst
expectations are separate datasets. Reported GAAP EPS must not be compared
with normalized analyst EPS without a comparable-actuals methodology.

## Quarterly release recovery

The seven reviewed **GAAP diluted** EPS values are in
`stock_machine/normalization/earnings_release_eps.json`, with original SEC
8-K exhibit URLs, accessions, publication dates, document SHA-256, exact fiscal
quarters, currency and share-basis notes.

| Ticker | Quarter ended | EPS, USD/share | Release filed | Full period available |
| --- | --- | ---: | --- | --- |
| ABNB | 2026-06-30 | 1.37 | 2026-08-06 | 2026-08-07 |
| CSCO | 2026-07-25 | 0.97 | 2026-08-12 | 2026-09-03 |
| KLAC | 2026-06-30 | 1.04 | 2026-07-28 | 2026-08-07 |
| LRCX | 2026-06-28 | 1.81 | 2026-07-29 | 2026-08-08 |
| MSFT | 2026-06-30 | 4.81 | 2026-07-29 | 2026-07-30 |
| NKE | 2026-05-31 | 0.72 | 2026-06-30 | 2026-07-16 |
| ORCL | 2026-05-31 | 1.45 | 2026-06-10 | 2026-06-23 |

ABNB was checked against shareholder-letter page 23: the 2026 three-month
column, Class A and Class B common stockholders. Six-month EPS is 1.62 and is
not used. The PDF table was checked because text extraction reorders columns:
[company-hosted original](https://s26.q4cdn.com/656283129/files/doc_financials/2026/q2/v2/Airbnb-Q2-2026-Shareholder-Letter.pdf).

KLA's original release explicitly applies its June 11 ten-for-one split. Its
1.04 is already adjusted; do not divide again. Recovering it exposes a mixed
pre/post-split TTM window. The code withholds that TTM EPS and stale-share
fallback until per-field historical split reconciliation is implemented; it
keeps the valid reported quarterly EPS. This is a bounded guard, not a claim
that all historical share-basis problems are resolved.

Normalization fills only missing facts for the exact CIK/start/end and never
overwrites an existing value; conflicting values generate an event. Q4 EPS
is never inferred by subtracting annual and quarterly EPS. Derived additive
Q4 fields retain their derivation flag. The Q4 start is the day after Q3 end.
Full-period availability remains the latest dependency date plus one day;
adding an earlier earnings release never advances other financial inputs.

The reviewed registry is reused by daily normalization and recovery. It is
an explicit seven-quarter repair, not an unreviewed generic HTML parser or a
backfill of all historical EPS. New releases require source review. Recovery
refetches all seven original exhibits and checks hashes before any period
replacement, retains raw documents in its evidence artifact, then verifies
EPS values and source accessions from PostgreSQL and rebuilds forecasts.
Bundles expose source metadata under each period's `field_provenance`.

## Historical analyst archives: access status

No licensed historical analyst archive is connected or supplied in this
workspace. No archive observations have been imported. FMP collection remains
useful for accumulating current snapshots, but a historical fiscal-period
parameter does not establish an old observation timestamp. The last verified
FMP HIMS response was an explicit symbol subscription restriction.

Two appropriate products, verified against primary documentation:

- [FactSet Estimates Point-in-Time Consensus](https://insight.factset.com/resources/at-a-glance-factset-estimates-point-in-time-consensus): daily point-in-time history from December 2009. Its ordinary Estimates API is not by itself proof of PIT feed entitlement.
- [LSEG I/B/E/S Point-in-Time](https://www.lseg.com/content/dam/data-analytics/en_us/documents/brochures/data-for-quant-research.pdf): archived daily snapshots with date/time fields, documented FTP delivery. Request this specific product and methodology, not merely current I/B/E/S estimates or actuals.

IBKR current prices and chains cannot substitute for a historical consensus
archive. No new subscription or external provider message has been initiated.

Request a sample for AAPL, HIMS, KLAC and ORCL before obtaining the full 54-name
export: genuine daily snapshots from 2014 onward (from listing/coverage start
where later), fiscal quarter and annual periods, original security identifiers
with dated mappings, EPS mean/high/low/count, revenue in explicit units,
GAAP/normalized basis, currency and original share basis. Include vendor
snapshot and dissemination timestamps, later corrections/deletions, and the
PIT product's timestamp/adjustment methodology. Current-universe selection
still does not solve historical survivorship bias.

## Implemented import contract

`python scripts/import_estimate_archive.py canonical.json` validates without
opening the database. `--apply --original-file vendor-export` additionally
checks the original export's hash and imports one atomic transaction. Migration
0018 stores the complete canonical batch and import timestamp for audit.
Identical observations are idempotent; conflicting observations roll back the
whole batch. Registered ticker/CIK identities must match. Imported streams
are separated by provider, dataset, security ID, currency and EPS basis; the
existing causal reader and same-period revision function can consume them.
No model promotion or forecast rebuild is implicit in an archive import.

This is our interchange format, **not** a claim that arbitrary proprietary
FactSet or I/B/E/S files can already be decoded. Map the entitled product's
sample to this contract after checking the provider's methodology.

Top-level required fields:

| Field | Requirement |
| --- | --- |
| format | `pit-consensus-v1` |
| point_in_time | boolean `true`; product methodology must support it |
| provider | `factset` or `lseg_ibes` |
| dataset | stable lowercase product identifier, digits/underscores/hyphens allowed |
| license_reference | entitlement reference, not credentials |
| source_reference | original vendor file/object identifier, not a signed URL containing secrets |
| timestamp_methodology | documented meaning of snapshot/dissemination fields |
| original_file_sha256 | lowercase 64-character hash of original licensed export |
| retrieved_at | actual export retrieval timestamp with timezone |
| records | nonempty list of canonical observations |

Each record requires `ticker`, `cik`, `security_id`, `snapshot_at`,
`available_at`, `period_type` (`annual`/`quarter`), `forecast_period_end`,
`currency` (`USD`), `eps_basis` (`gaap`/`normalized`), `eps_type` (`diluted`),
`share_basis` (`contemporaneous`), finite `eps_mean`, and positive integer
`analyst_count`. Optional EPS high/low must bracket the mean. Optional revenue
mean/high/low require `revenue_unit: USD` and explicit conversion from any
vendor scale. Date-only timestamps and values adjusted using future splits
are rejected. For an end-of-day-only feed, the reviewed adapter must use the
next eligible timestamp and describe that policy; it must not assume morning
availability. Snapshot <= dissemination <= retrieval <= now is enforced.

The existing `consensus_vintages.observed_at` column carries historical vendor
dissemination time for archive streams, and actual fetch time for current FMP
streams. Original vendor snapshot time remains in payload; our later import
and retrieval dates remain in the audit batch. No historical timestamp is
invented from the forecast fiscal period.

Before a full import: verify the licensed sample against the original export,
confirm identities and units, test revisions on the same fiscal period/basis,
and probe cutoffs immediately before and after dissemination. Then replay
historical coverage and Alpha/P1 evaluation. Until genuine archive data is
available, missing historical expectations and diagnostic model status remain.
