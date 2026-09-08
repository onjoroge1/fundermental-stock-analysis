# Accounting and data-source recovery — September 7, 2026

## Verified baseline and bounded repair

Reconstructed all 54 companies from SEC CompanyFacts and reproduced the live
KPI exactly: 2,459 passes / 2,511 tested financial-period rows (97.9%). There
are 4,392 normalized period rows in total; 1,881 are untested because core
balance fields are absent or assets are not positive. Annual and quarterly
rows can refer to the same balance sheet. Neither this identity nor the
READY label certifies a complete, coherent historical model input set.

The `NonredeemableNoncontrollingInterest` alias repairs 32 UBER rows without
changing the 1% tolerance. Example: September 30, 2019 assets $32.292bn,
liabilities $16.241bn, parent equity $15.062bn, redeemable NCI $309m and
nonredeemable NCI $680m reconcile exactly. All five facts are in accession
`0001543151-19-000017`, filed November 5, 2019. Availability remains November 6.
Full-universe replay after the mapping: 2,491 / 2,511 (99.2%), 20 failures.

Remaining failures in that replay:

| Company | Rows | Evidence / unresolved work |
| --- | ---: | --- |
| BKNG | 9 | Older mezzanine/convertible classification; inspect statement and calculation relationships. |
| TSLA | 4 | Older residuals match a convertible-debt equity component. Do not add this concept globally: it can already be included in permanent equity. |
| NVDA | 3 | Older residuals match temporary-equity par-value facts. Par value is not generally the full carrying amount. |
| HIMS | 3 | 2019–2020 rows combine predecessor SPAC balance fields and later operating-company temporary equity from different accessions. A coherent entity/filing basis is still required. |
| PLTR | 1 | Pre-IPO 2019 temporary equity missing from standard CompanyFacts; inspect original custom-tagged filing. |

The new read-only `/api/accounting-quality` exposes tested/untested coverage,
failure components, source accessions and latest-quarter status. The existing
KPI now reports its excluded denominator. No gap is imputed to force an identity.

## Cover-page recovery

CompanyFacts omits many dimensional/custom filing facts. For companies with
no DEI entity-wide share facts, the pipeline fetches the latest 10-K/10-Q
primary document listed in SEC submissions and reads inline
`dei:EntityCommonStockSharesOutstanding` facts. It accepts a single entity,
instant, shares unit and share-class axis. It rejects conflicting duplicate
counts, inconsistent totals, other dimensions, unsupported transformations
and future instants. Class facts are summed only within that scope.

Verified directly against original filings:

| Ticker | Cover date | Common shares |
| --- | --- | ---: |
| ABNB | 2026-07-15 | 598,785,682 |
| DELL | 2026-06-02 | 648,107,991 |
| GOOGL | 2026-07-15 | 12,230,000,000 (reported in millions) |
| HIMS | 2026-08-07 | 233,313,413 |
| META | 2026-07-24 | 2,547,506,225 |
| PLTR | 2026-07-27 | 2,403,058,480 |
| RIVN | 2026-07-21 | 1,447,860,630 |

Class totals are disclosed common-share counts, not a claim that every class
has identical voting, conversion or economic rights. Raw documents and
component/source metadata are retained. Availability is the day after filing,
not the share-count date. Earlier share observations survive partial/failed
future refreshes. This initially recovers one source vintage per missing
company; it does not reconstruct complete historical cover-share archives.

ABNB still lacks numeric quarterly diluted EPS in the inspected standard
facts. CSCO, KLAC, LRCX, MSFT, NKE and ORCL have derived Q4 periods without
separately reported quarterly EPS. EPS and weighted shares cannot safely be
derived by subtracting the first three quarters from the annual number.
These seven fundamental warnings remain until independently sourced.

The release's `Recover SEC Fundamentals` workflow re-ingests UBER and the
seven cover-share companies, verifies persistence and rebuilds canonical
forecasts. It does not alter prices, vendor estimates or analyst narratives.
It retains research eligibility and promotion gates. Source evidence and
before/after diagnostics are attached to the workflow run.

PR #46 was released as `2be19c357af7263c18b570a693a68ad3863e606c`.
CI passed 331 tests on both Python 3.11 and 3.12, including PostgreSQL
integration contracts; the Vercel preview and production builds passed.
Recovery run `34170649141` verified all eight forecast rebuilds and the seven
cover-count writes. A scheduled daily run from the preceding commit had
started just before the release and subsequently overwrote some recovered
inputs. The follow-up makes recovery share `daily-data-forecast-refresh`
with the daily workflow, without cancelling the running job. Its release
queues another targeted recovery after that writer finishes. Final live
counts must be read after the serialized recovery completes.

## Data API decision

| Missing input | Appropriate source | Requirements before use |
| --- | --- | --- |
| Current SEC facts / cover counts | [SEC EDGAR](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), free | Original filings for dimensions/custom concepts; preserve filing availability and class/entity context. Current repair uses this. |
| More complete filing extraction | [sec-api.io XBRL converter](https://sec-api.io/docs/xbrl-to-json-converter-api) | Paid optional parser, not a substitute for accounting-context validation. No purchase needed for the seven counts above. |
| Current HIMS estimates | [FMP analyst estimates](https://site.financialmodelingprep.com/developer/docs/stable/financial-estimates) | Existing key is present, but the live HIMS response explicitly restricts the symbol under this subscription. Confirm entitlement before upgrading; current estimates do not recreate past vintages. |
| Historical analyst revisions | [LSEG I/B/E/S](https://developers.lseg.com/en/api-catalog/refinitiv-data-platform/estimates-API) | Request a genuine historical point-in-time archive with observation time, fiscal period, source, revisions and HIMS coverage. Confirm the licensed API/dataset includes that history. |
| Prospective quotes and option chains | IBKR | Connected tool returned 22 HIMS daily bars through September 4 and a September 18 $28 put bid/ask of $1.45/$1.56. Option midpoint IV was explicitly invalid; discard it. App's own expiration endpoint still returns 503 (`ibapi` absent). Unattended use needs a reachable authenticated bridge and market-data entitlements. |
| Historical option surfaces | [Cboe All Access API](https://datashop.cboe.com/cboe-all-access-api) | Verify expired contract history, timestamped bid/ask, IV, corporate-action identifiers and coverage. [IBKR historical limits](https://interactivebrokers.github.io/tws-api/historical_limitations.html) exclude expired options; today's chain is not an archive. |
| Delisted/unbiased historical universe | [Nasdaq Sharadar SF1](https://data.nasdaq.com/databases/SF1), [SEP](https://data.nasdaq.com/databases/SEP), [reference data](https://data.nasdaq.com/databases/PTSR) | Verify dated security membership, delisted price/action coverage, identifiers and delisting returns; include disappeared companies in the experiment. |

Current prices were complete for 54/54 names through September 4, the latest
completed session at the September 7 holiday check. This is sufficient for
current price-based diagnostics. Historical expectations, options, old credit
vintages and universe membership remain incomplete. No validated forecast
edge or profitable strategy is established by this repair.

## Liability-component recovery

The next normalization step reads SEC `LiabilitiesNoncurrent` alongside
`LiabilitiesCurrent`. It creates `total_liabilities` only when assets, equity,
both liability components and any equity components all use the same filing
accession, and the resulting balance sheet independently reconciles within the
unchanged 1% tolerance. Rejected candidates remain untested and produce a
diagnostic event; no residual is assigned to liabilities.

All-54 CompanyFacts replay accepted 554 additional normalized period rows and
rejected 36 older AT&T/United rows whose component sums did not reconcile.
Tested coverage rises from 2,511/4,392 (57.17%) to 3,065/4,392 (69.79%). The
accepted rows pass, so the 20 previously documented failures remain 20 and the
tested pass rate becomes 99.35%. Latest-quarter accounting status improves
from 35 to 43 passes. AAL, ADI, DAL, ORCL, TGT, TMUS, UAL and VZ are newly
testable at their latest quarter. Eleven latest quarters remain untested.
