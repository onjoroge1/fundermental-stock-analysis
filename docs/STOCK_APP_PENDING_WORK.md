# Stock research app: pending work

Updated September 8, 2026. This is the execution checklist following the
predictive-integrity audit. Completion of infrastructure does not demonstrate
predictive edge.

| Package | Status and evidence | Next action |
| --- | --- | --- |
| Production release and data recovery | Released; 54/54 prices current, 41 ready / 13 caution / 0 blocked at the last check | Continue existing daily health and refresh jobs. |
| Historical macro coverage | PR #44 released; 20,344 genuine ALFRED rows; VIX and curve 50/50 evaluation dates, credit 11/50 | Longer credit vintages require an entitled source archive. |
| Analyst-estimate collection | PR #45 released; live run 34168414523 verified 5 precise AAPL observations; FMP explicitly restricts HIMS under this subscription | Existing daily pipeline accumulates precise vintages. Obtain a genuine historical estimate archive to fill earlier dates. |
| Historical options | Connected IBKR access returns valid current AAPL bid/ask, open interest and IV. A bounded weekday capture workflow now exists, but its unattended bridge configuration still needs verification. | Configure the read-only bridge secrets, run the first production capture, and continue prospective collection. Obtain an entitled archive for earlier dates; current chains cannot recreate them. |
| Historical universe | Current-universe survivorship limitation remains | Acquire dated membership, removed/delisted companies and delisting returns before claiming unbiased market-wide performance. |
| Accounting and missing fundamentals | PR #46 recovered UBER NCI and seven cover-share counts. This change adds same-filing, identity-corroborated current + noncurrent liability aggregation. Full-universe replay increases tested coverage from 57.17% to 69.79% while retaining 20 older failures | Verify production recovery; resolve the remaining liability coverage, 20 older failures and seven quarterly EPS gaps. See ACCOUNTING_DATA_RECOVERY.md. |
| Predictive validation | Alpha/P1 pending missing inputs; no P1 challenger passed; Strategy Lab candidates rejected | Freeze a 20- or 63-session benchmark-relative experiment and record prospective forecasts before outcomes; retain simple controls and realistic costs. |
| Paper portfolios | No eligible research policy at last validation | Preserve eligibility checks. Start cohorts only when the declared policy requirements pass. |

## Analyst history implementation

The daily display cache retains its original rows. A separate append-only
`consensus_vintages` table stores provider, exact UTC retrieval time, fiscal
period and values. A new source observation can record an intraday revision or
reversion; replaying the same observation is idempotent. Contradictory values
for the same source and timestamp fail rather than overwrite history.

Historical readers expose old day-only rows as `legacy_unattributed` and new
rows with their actual provider/time. The revision calculation uses the latest
eligible observation for the chosen fiscal period and compares only the same
source and period. Timestamped observations are not made available earlier in
the day; legacy dates become eligible only after the day ends. Old history is
not relabelled with invented provider identities or precise timestamps.

The release workflow applies migration 0017 and verifies FMP collection and
reader persistence for AAPL and HIMS. It reports provider limitations and empty
coverage separately from persistence failures. The existing daily pipeline
already invokes the same writer for the full universe, so no duplicate daily
schedule is needed. A current collection run does not fill the 2014–2026
historical expectation gap.

Related detail: [historical recovery](HISTORICAL_INPUT_RECOVERY.md) and
[original repair notes](PREDICTIVE_VALIDATION_REPAIR.md).

## September 8 EPS and estimate-archive package

Seven quarterly GAAP diluted EPS values have been verified against original SEC earnings exhibits and a repeatable recovery is implemented. Full-period timing remains conservative. KLA mixed-split TTM EPS is withheld pending basis reconciliation. A strict, atomic licensed PIT archive importer is implemented; no historical archive has been acquired or loaded. See [source evidence, access requirements and import contract](EARNINGS_AND_ESTIMATE_RECOVERY.md). Production results are recorded in the release PR and recovery workflow.

## September 9 prospective options package

The connected IBKR account returned valid current AAPL option contracts,
bid/ask quotes, open interest and implied volatility for two expirations. This
proves that IBKR is suitable for collecting new observations, but the ChatGPT
connection is not an unattended data feed and cannot recreate expired option
surfaces.

`option-surface-capture.yml` runs a bounded ten-name collection after the US
close through the existing read-only HTTPS bridge. It refuses unauthenticated
or competing sessions, persists only surfaces with positive observed ATM IV,
and fails unless at least eight names succeed. Partial collection can no longer
look like a successful full-universe run. Production activation requires
`IBKR_BRIDGE_BASE_URL` and `IBKR_BRIDGE_TOKEN` in the GitHub Production
environment and a running authenticated TWS/IB Gateway bridge.

The LSTM now predicts cumulative 5/10/20-session returns directly with three
Gaussian heads. It never feeds sampled predictions back into its features and
publishes no unsupported long-horizon distributions. It remains diagnostic.
`validate(returns, evaluate_lstm=True)` runs the three-seed model inside the
same purged folds and evaluates it against no-change and class-prior baselines.
The default forecast worker does not run this expensive research evaluation.
Use `scripts/validate_direct_lstm.py --ticker AAPL --as-of YYYY-MM-DD` with
DATABASE_URL and the prediction extra. The dated, hashed report is a research
artifact; it does not promote the model or overwrite production forecasts.
The current 54-name universe still has survivorship limitations.
