# Research integrity recovery — 2026-09-16

All 54 covered names were checked against actual SEC company-facts and submissions responses. This is an evidence disposition, not a claim that all 54 balance sheets are fully reconciled. Only AVGO, MU and VZ meet the complete checks. The remaining 51 are explicitly withheld. Ford has a June 30 filing that is absent from the current company-facts payload.

All 167 non-forecast claims in the 54 latest saved reports were checked. None supplies the structured evidence needed to establish exact values and entailment; their old citation IDs alone are insufficient. New source briefs bind value, field, unit, tag, period, accession, source URL and a content hash. Historical unverified prose and all unqualified price targets/recommendations are withheld in normal API and UI reads.

The VZ source reconciliation uses its June 30 balance sheet: $21.783 billion current debt + $143.448 billion noncurrent debt = $165.231 billion total debt. Less $1.752 billion cash and equivalents gives $163.479 billion under the stated definition. This is not Verizon’s differently defined net unsecured debt. The old calculation omitted noncurrent debt. [Issuer statement, page 5](https://www.verizon.com/about/sites/default/files/2026-07/vz_2q2026_non-gaap_072426.pdf).

Net debt now means disclosed debt less cash and cash equivalents; investment securities are excluded and missing values are never zero. Interest coverage uses four quarters of interest expense with four quarters of operating income. Derived composite scores and relevant enterprise-value metrics are withheld when their dependencies fail.

## Per-name disposition

| Ticker | Latest normalized period | Latest filing period | Complete check | Blockers | Old claims verified |
|---|---|---|---|---|---|
| AAL | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED; CASH_AND_EQUIVALENTS_MISSING_OR_INVALID | 0/3 |
| AAPL | 2026-06-27 | 2026-06-27 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| ABNB | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| ADBE | 2026-05-29 | 2026-05-29 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| ADI | 2026-08-01 | 2026-08-01 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| AMAT | 2026-07-26 | 2026-07-26 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| AMD | 2026-06-27 | 2026-06-27 | WITHHELD | MISSING_BALANCE_FIELD:total_liabilities; TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| AMZN | 2026-06-30 | 2026-06-30 | WITHHELD | MISSING_BALANCE_FIELD:total_liabilities; TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| AVGO | 2026-08-02 | 2026-08-02 | VERIFIED | None in defined scope | 0/3 |
| BKNG | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| CMCSA | 2026-06-30 | 2026-06-30 | WITHHELD | MISSING_BALANCE_FIELD:total_liabilities | 0/3 |
| CMG | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| COST | 2026-05-10 | 2026-05-10 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| CRM | 2026-07-31 | 2026-07-31 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/4 |
| CSCO | 2026-07-25 | 2026-07-25 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| DAL | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| DELL | 2026-07-31 | 2026-07-31 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| DIS | 2026-06-27 | 2026-06-27 | WITHHELD | MISSING_BALANCE_FIELD:total_liabilities; TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| F | 2026-03-31 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED; NEWER_FINANCIAL_FILING_NOT_NORMALIZED | 0/3 |
| GM | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| GOOGL | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| HD | 2026-08-02 | 2026-08-02 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| HIMS | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/5 |
| HPQ | 2026-07-31 | 2026-07-31 | WITHHELD | MISSING_BALANCE_FIELD:total_liabilities | 0/3 |
| IBM | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| INTC | 2026-06-27 | 2026-06-27 | WITHHELD | MISSING_BALANCE_FIELD:total_liabilities; TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| INTU | 2026-07-31 | 2026-07-31 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| KLAC | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| LOW | 2026-07-31 | 2026-07-31 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| LRCX | 2026-06-28 | 2026-06-28 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| LUV | 2026-06-30 | 2026-06-30 | WITHHELD | MISSING_BALANCE_FIELD:total_liabilities; TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| MCD | 2026-06-30 | 2026-06-30 | WITHHELD | MISSING_BALANCE_FIELD:total_liabilities; TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| META | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| MSFT | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| MU | 2026-05-28 | 2026-05-28 | VERIFIED | None in defined scope | 0/3 |
| NFLX | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| NKE | 2026-05-31 | 2026-05-31 | WITHHELD | MISSING_BALANCE_FIELD:total_liabilities; TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/4 |
| NOW | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| NVDA | 2026-07-26 | 2026-07-26 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/4 |
| ORCL | 2026-08-31 | 2026-08-31 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| PLTR | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| QCOM | 2026-06-28 | 2026-06-28 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| RIVN | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| SBUX | 2026-06-28 | 2026-06-28 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED; CASH_AND_EQUIVALENTS_MISSING_OR_INVALID | 0/3 |
| SOFI | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| T | 2026-06-30 | 2026-06-30 | WITHHELD | MISSING_BALANCE_FIELD:total_liabilities | 0/3 |
| TGT | 2026-08-01 | 2026-08-01 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED; CASH_AND_EQUIVALENTS_MISSING_OR_INVALID | 0/3 |
| TMUS | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| TSLA | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| TXN | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| UAL | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| UBER | 2026-06-30 | 2026-06-30 | WITHHELD | TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |
| VZ | 2026-06-30 | 2026-06-30 | VERIFIED | None in defined scope | 0/3 |
| WMT | 2026-07-31 | 2026-07-31 | WITHHELD | MISSING_BALANCE_FIELD:total_liabilities; TOTAL_DEBT_NOT_SOURCE_RECONCILED | 0/3 |

## Bounded research exercise

Five fresh briefs were built from the captured public sources and passed exact claim validation. This offline exercise made no database journal writes, called no broker and does not prove the production integration.
- AAPL: 7 verified source facts; research status WITHHELD; guidance withheld.
- HIMS: 6 verified source facts; research status WITHHELD; guidance withheld.
- MSFT: 4 verified source facts; research status WITHHELD; guidance withheld.
- UBER: 7 verified source facts; research status WITHHELD; guidance withheld.
- VZ: 6 verified source facts; research status CURRENT_VERIFIED; guidance withheld.

## Prospective experiment

Protocol `momentum63-hold20-prospective-v1` freezes the 54-name universe and ranks 63-session adjusted-close momentum. It selects the top quintile, observes entry at a future exchange open, and exits at the twentieth session close. No same-close or backdated fills. Controls are SPY and equal weight of the frozen universe. Costs are 25 basis points each side; the stress case uses 50 each side. Costs are assumptions, not measured execution.

Fail on any cohort drawdown below −20%, missing observations block rather than dropping names, and after at least 12 non-overlapping cohorts require positive average excess versus both controls, a positive lower 95% paired bootstrap bound against each, and positive mean stress net return. A pass requires independent review and never enables orders or paper promotion. Database registration and future observations are required before this can be called a running prospective experiment.

## Release and operating contract

- Additive migration 0020 preserves the existing research journal and protects new evidence against UPDATE, DELETE and TRUNCATE.
- A current-main CI/build gate precedes migration. Release then refreshes SEC inputs, records each reconciliation, creates five bounded briefs, publishes all configured names to the research index, and registers/advances the experiment.
- Vercel retains the Massive key. The release exercises one authenticated VZ cycle in production; it cannot call the integration verified merely because the adapter is installed. Daily bars are explicitly split adjusted, not dividend adjusted or live quotes. News remains at most five unreviewed headline records.
- Hourly maintenance schedules no more than five pilot cycles per UTC weekday, at most two provider calls per cycle. Duplicate requests replay stored results. Daily GitHub work refreshes data and the full index, and advances the experiment. Failed source reads stay failures.
- Admin credentials and the existing capture feature flag still guard journal capture. The old 0019 runner is retained unchanged as historical release evidence; 0020 supersedes it and does not weaken its old guard.
- Legacy paper marking continues; automatic positions from report classifications are disabled. The prospective experiment is a separate ledger.

Local verification: 498 tests passed before final integration additions; PostgreSQL tests require the CI service. Deployment/capture/provider results are reported separately in `research-integrity-release-report.json`, never inferred from local tests.

## Remaining confidence limits

51 balance sheets still require issuer-specific completion of debt/cash/liability source evidence. Ford requires its missing latest statement. The new cycle is a bounded source-fact/change review, not an independent analyst or learned agent. No forecast or recommendation is financially qualified by this release. Provider entitlements, production capture and prospective registration require successful live release evidence. An intraday/live quote product is not implemented by this EOD integration.

Full claim-by-claim results, source hashes, exact balance values and the five exercise records are in [the machine-readable evidence](evidence/research-recovery-2026-09-16.json). Reproduce with `python -m scripts.audit_source_recovery --source-dir CAPTURED_SEC_DIR --live-dir CAPTURED_APP_DIR --as-of TIMESTAMP --output AUDIT.json`.
