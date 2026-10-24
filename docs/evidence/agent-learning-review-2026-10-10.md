# Agent learning review — tracker (opened 2026-10-10)

This tracks gaps found in a review of the agent-learning loop at `b448617` (PR #98).
Each item was reproduced by running the repo's own functions on synthetic inputs.
Production data was not inspected. When an item ships, update its status and link the PR.

Status: **DONE** (merged), **IN PR** (on a branch), **OPEN**.

## Critical — the loop cannot learn as designed

| # | Gap | Evidence | Fix direction | Status |
|---|---|---|---|---|
| 1 | Reward v3 makes every trade lose to abstaining. A 0.60×max-drawdown penalty dominates, and return (% of notional) is mixed with capital/turnover penalties (% of equity). | A +2.13pp mean gross edge scored a mean reward of −3.55; NO_TRADE always scores 0. | Reward v4: net return ÷ ex-ante holding-period volatility. Drawdown, capital and turnover become diagnostics. Arms trained under v3 cold-start. | DONE (#99 on main) |
| 2 | Bandit learns too slowly to use: diagonal LinUCB without an intercept, NO_TRADE learned as an arm despite a fixed 0 reward, a collinear `bias` feature, and per-ticker state. *Correction:* the original "60% lock-out" figure came from an immediate-reward probe; with realistic 20-session reward delays the current bandit keeps trading but learns slowly. | Realistic simulation (54 tickers × 252 sessions, 20-session holds, v4-scale rewards): old bandit captured +40 expected reward/yr; a pooled Thompson sampler captured +98. Per-ticker Thompson was worse than the old bandit, so pooling is what pays off. | Pooled linear Thompson sampling with full precision matrix, intercept, `bias` dropped, NO_TRADE as a fixed zero baseline, seeded by decision key. | DONE (#100, on main via #104) |
| 3 | Treated as a bandit even though every abstention has an observable what-if outcome; abstentions train only the constant-zero arm. *Correction:* the review claimed this gives 10–20× more labels. Daily labels on one stock share most of a 20-session window and contexts move slowly, so the effective gain is small. With the production router (only the heuristic's direction offered), realized-only and what-if learning captured the same simulated reward (+77–80/yr). | By design in `outcomes.py`. | Ships for its narrower but real value: it learns from capacity-blocked, held, abstaining and SHADOW-mode decisions, measures the direction the router didn't offer (needed for item 6), and keeps training data independent of exploration choices. Labels are down-weighted 1/20 for overlap. | DONE (#101, on main via #104) |
| 4 | 54 independent per-ticker learners, ≤ ~12 non-overlapping trades/yr each. | Bandit state is keyed by ticker. | One pooled model with ticker/sector shrinkage (pattern exists in `shadow.candidate_weights`). | DONE (#112 on main): sector intercepts. Per-ticker offsets were deliberately not adopted (simulated); revisit with real data. |
| 5 | The "20-session hold" is not real. Each daily re-decision can emit CLOSE, so a trade's reward depends on later unrelated choices; trade and abstention horizons differ. | Simulation (year 2): daily re-decision averaged a **5.5-session** hold and 1,757 trades/yr (net +46.5 after costs); committed 20-session holds made 623 trades/yr (net **+65.1**). | Positions opened under the prospective contract are commitments: an opposite or flat signal before the 20-session target becomes HOLD; the holding limit closes them. Legacy positions keep signal exits. | DONE (#101, on main via #104) |

## High

| # | Gap | Evidence | Fix direction | Status |
|---|---|---|---|---|
| 6 | Direction is never learned (hand-set weights 0.50/0.25/0.15/0.10, ±0.25 threshold); no promotion rule for shadow weights. | `state.assemble` | Each decision freezes the heuristic direction and the pooled model's learned direction (`direction_challenger`). Both are scored on the same matured counterfactual rewards, with FLAT = 0. A paired test is fixed in code (`learned-direction-vs-heuristic.v1`, hashed into every snapshot): resample 20-session blocks, ≥12 blocks, pass if the mean and the bootstrap lower 2.5% bound of (learned − heuristic) are > 0 and the learned mean reward is > 0. | DONE (#116 on main): owner-gated switch to the learned direction. Approval requires a v2 test pass under the current protocol; it reverts automatically if evidence stops passing or the protocol changes. |
| 7 | Shadow weight metric (squared error vs ±1) penalizes conviction. | A 56%-hit, magnitude-0.6 signal got 0.459 weight vs 0.541 for zero-skill ±0.1 noise. | Rank IC or calibrated log-loss, or a stacked ridge/logistic model. | DONE (#103, on main via #104) |
| 8 | Shadow targets use raw returns, which mostly measure market beta over 5 sessions. | `shadow.evaluate` | Score stock-specific components against SPY/sector-excess returns. | DONE (#103, on main via #104) |
| 9 | Technical-setup utility `mean − 0.5·std` needs per-trade Sharpe > 0.5, so it nearly always abstains. | Annual Sharpe 1.0: utility −1.97% (5d), −2.35% (20d). | Penalize standard error (`k·std/√n`) or rank by Sharpe with a cost floor. | DONE (#105 on main) |
| 10 | Headline classifier false positives trip `HIGH_MATERIALITY_NEGATIVE_HEADLINE_CONTEXT`. | "issued" → LITIGATION, "regulatory approval" → REGULATORY_ACTION, "no warning signs" → GUIDANCE_CUT. | Word-boundary regexes, a negation guard, approvals excluded from regulatory action. | DONE (#99 on main) |

## Medium

| # | Gap | Status |
|---|---|---|
| 11 | No pooled statistics or CIs: the weekly shadow view is ~162 ticker×horizon cells of ~5 observations; no Brier skill vs base rate. | DONE (#106 on main) |
| 12 | The shadow training window (5,000 latest outcomes at ~162/day) covers only ~31 sessions. | DONE (#106 on main) |
| 13 | Missing context features are coerced to 0 (looks neutral); clipped annual vol acts as a hidden intercept. | DONE (#106 on main) |
| 14 | `settle_holding_limits` exits at the latest close when late (holds can exceed 20 sessions) and caps at 6 exits per pass. | DONE (#109 on main) |
| 15 | Per-decision JSONB trial-count scan and one DB connection per scored record grow with history. | DONE (#110 on main) |
| 16 | Shadow scores from the origin close, while paper trades fill at the next close. | DONE (#108 on main) |
| 17 | With no edge anywhere, Thompson sampling keeps trading about half the time (the posterior mean sits near 0), so churn and costs continue while it learns. In simulation this is about 675 trades/yr vs 509 before, at zero expected reward. | DONE (#112 on main): 80% confidence gate; the paper book stays flat until the model is confident. |
| 18 | A matured decision whose price path stays incomplete is retried on every pass; enough of them could fill the bounded 100-record scan. The shadow scorer rotates these with per-session CHECK records; the paper learner does not. | DONE (#107 on main; migration 0027 applied) |
| 19 | Overlap weighting handles one stock's correlated daily labels; same-day labels across stocks share market moves and are still treated as independent. Related to item 8. | DONE (#112 on main): learning labels are stock-specific (beta-adjusted vs SPY). |
| 20 | No automatic reconciliation between each executed position's realized ledger return and its counterfactual label for the same window (they should match apart from late exits). | DONE (#108 on main) |
| 21 | No power analysis for the direction test: 12 blocks may be too few to detect a realistic improvement, and too many decisions per block are correlated to know in advance. | DONE (#111 on main); v2 rules from its findings in item 30. |
| 22 | The candidate shadow weights (item 7) still have no promotion protocol; item 6 covers direction only. | DONE (#107 on main). Evidence restarts under this branch's new entry-aligned target (item 16), since the protocol names its target. |
| 23 | The residual is relative to SPY only; there's no sector-relative target, and a 63-day beta is a noisy hedge ratio. | DONE (#113 on main): sector-relative diagnostic; beta noise measured and left alone. |
| 24 | Shadow snapshots captured before this change are scored under the v2 target with default beta/volatility when missing; their frozen candidate scores used v1 weights, so the first weeks mix weight versions in the weekly view. | CLOSED (documented): each change to the shadow target or a test protocol restarts its evidence, so early weeks under a new version are thin. Read early numbers with their reported intervals and detectable differences (items 11, 21), which make this visible. |
| 25 | Stacked PRs merge into each other, not main: #100–#103 merged into their base branches and only reached main via #104. | DONE: #104 landed #100–#103 on main. PRs now target main directly. |
| 26 | The technical evaluator's other per-trade metrics (net win rate, mean per opportunity) are reported without uncertainty, and walk-forward folds don't report how often a no-edge setup would act. | DONE (#113 on main) |
| 27 | Brier skill uses the evaluated set's own base rate (in-sample climatology), which slightly favours the reference; a prior-period base rate would be stricter. | DONE (#114 on main) |
| 28 | The cumulative pooled view reads every v2 shadow outcome on each admin load; at ~162 a session that grows ~41,000 rows a year. | DONE (#114 on main) |
| 29 | Release coordination for migration 0027: deployed code requires schema 0027 and ingestion fails closed until the migration workflow succeeds; the daily technical workflow's `alembic upgrade head` would also apply it. | DONE: the 0027 workflow succeeded on 2026-10-10 (run 38084929642); CI on main is green. |
| 30 | Both pre-registered tests pass too often with no real improvement: about 5–7% instead of 2.5%. Adjacent blocks are correlated (lag-1 ≈ +0.16) and the percentile bootstrap understates the spread by 22–26%. A Newey–West lag-1 + t(n−1) bound reaches about 3% from 24 blocks; nothing tried is calibrated at 12. | DONE (#115 on main) |
| 31 | The direction test's protocol now names reward v5 and the sector-aware challenger, so its hash changed and evidence restarts. No 20-session window had matured yet, so nothing is lost; item 30's protocol decision is still open. | INFO |
| 32 | `session_offset` built its calendar from the year before the origin, so look-backs of ~1 year from early January ran off its start. From early January 2027, shadow training history (−252) and the daily technical-setup job (−252) would have failed; the shadow capture failure would have been silent inside its savepoint. | DONE (#114 on main) |
| 33 | Learning saw only ~5 of 54 stocks: 49 decisions a day were blocked, 44 of them only because total debt isn't reconciled to a filing (`TOTAL_DEBT_NOT_SOURCE_RECONCILED`; no total-debt tag, complete subtotals or reviewed reconciliation). The 5 eligible stocks are the original pilot names with reviewed debt. | DONE for the current filings: learning side (#120), drafting tool (#122, #124), zero-debt rule (#129), and reviewed debt for all 54 issuers (#125, #128, #129, `data/segment-debt-f-gm-tmus`). F and GM use automotive-only debt (captive finance arms excluded; cash stays consolidated) and TMUS uses its face debt lines including the related-party split; these are owner decisions. Every entry is pinned to one filing: redo with the draft tool each quarter. F is still withheld for item 40. |
| 34 | Admin intelligence view returned "Admin storage or operation unavailable": #114 ran a query before `weekly_summary`'s `SET TRANSACTION ISOLATION LEVEL`, which Postgres allows only first. | DONE (#119): fixed, with an end-to-end Postgres test of the real admin summary. |
| 35 | Daily technical job reports ATTENTION (workflow failure) when a single stock's latest bar is incomplete (LOW Oct 15, DELL Oct 16) though everything else ran. | DONE (#123): root cause was a provider bar published before its fields were final (Yahoo now has complete DELL/LOW bars). The price fetch now rejects an incomplete latest bar as retryable (the existing retry passes re-fetch it), data health marks a stored incomplete latest bar `INCOMPLETE` so the price refresh re-fetches it, and the technical job reports isolated gaps (≤3 stocks) as a warning instead of a failed run. |
| 36 | Research queue reached only 29–30 of 54 stocks on Oct 14–15. | DONE (#123): the skipped stocks were exactly those whose nightly refresh failed (Oct 13 session: 25 stocks, Yahoo refused then FMP 402), so research had no current forecast for them; #118's retries already lifted Oct 15–16 to 53–54. Remaining gap: only one ingestion attempt per session (the two crons are a DST pair). Added a repair trigger (01:30 UTC, tolerant of GitHub's late starts) that replays when the session already succeeded and otherwise re-ingests only the stocks that are not research-ready. |
| 37 | No alerting: failed refreshes, stale prices or collapsing learning coverage were visible only by opening the app. | DONE (#121): weekday health watchdog (prices, refresh/technical/option/events workflows, learnable-decision coverage) keeps one `health-alert` issue, updates it only when the set of problems changes, and closes it when healthy. |
| 38 | 11 issuers have no normalized `total_liabilities` (AMD, AMZN, CMCSA, DIS, HPQ, INTC, LUV, MCD, NKE, T, WMT): they don't tag `us-gaap:Liabilities`, so financial integrity stays withheld even with debt verified. | DONE (#126 and `data/accept-amzn-tgt-balance-fields`): reviewed total liabilities from the face balance sheet, accepted only when the face identity closes within declared rounding, for all 11 (AMZN accepted by the owner with its issuer lease tag). Redo each quarter with the draft tool. |
| 39 | Cash and EPS mapping gaps: AAL (`Cash`), SBUX and TGT have no normalized cash and equivalents; INTU and two others lack latest-quarter diluted EPS (data quality WARN). | DONE (#126 and `data/accept-amzn-tgt-balance-fields`): cash from the face line labelled cash for AAL, SBUX and TGT (TGT accepted by the owner although its tag includes short-term investments); Q4 diluted EPS for COST, INTU and MU from their earnings releases. |
| 40 | Ford's Q2 2026 10-Q (filed 2026-07-29, accession 0000037996-26-000156) is missing from SEC companyfacts, so Ford's latest normalized quarter is March and its financials stay `NEWER_FINANCIAL_FILING_NOT_NORMALIZED`. | DONE (#132): SEC ingest now normalizes the newest 10-Q/10-K filings that companyfacts lacks from their own inline XBRL (consolidated us-gaap facts only, never replacing companyfacts, provenance citing the filing URL and hash). Ford's 2026-06-30 quarter normalizes (balance identity residual 0) and its automotive-only June debt ($23.619B) is promoted. |
| 41 | No historical baseline for the agent's heuristic direction: the forward direction test needs 24 blocks of 20 sessions (about two years) before it says anything. | DONE (#133; result in `direction-replay-2026-10-18.md`): over 6,555 decisions and 128 months the heuristic is indistinguishable from abstaining (−0.0017 vol units, 95% [−0.036, +0.033]); H1, H2 and H3 not supported. The forward challenger's baseline is about zero. |
| 42 | Candidate signals untested historically: earnings surprise, estimate revisions, and the owner's idea of using fundamentals for fair value with technicals for timing. | DONE (#135, #136; `signal-replay-2026-10-18.md`): surprise and revisions INSUFFICIENT_COVERAGE (no point-in-time history before 2026); value gap NOT_SUPPORTED and significantly negative (cheap lagged rich, 2016–2026); value gap timed by technicals ~0; timing beat the raw value gap only weakly (H8 lower bound +0.0003). OPEN: vendor history for surprises/revisions; pre-register the reversed value gap on post-2026-08 data. |
| 43 | The owner's design (fundamentals filter solid companies and set a fair value; trade toward it, timed by price action, over months) had no systematic historical fair value to test: report fair values are analyst-authored and the reverse DCF is withheld. FMP surprise history on this plan is 5 events (from 2025-10). | DONE (#138, #139; `fair-value-replay-2026-10-18.md`): nothing supported at family-wise 95%. Own-history P/E reversion negative; sector-relative fair value, timed, 120 sessions is positive but not significant (+0.0075/month, +0.109 vol units per trade, 54.9% hit rate) and is the candidate for the forward holdout. FMP plan returns 5 events per stock (history from 2025-10), so surprises stay untestable until a higher tier or accumulation. |
| 44 | The sector P/E rule (the replay's only positive policy) had no confirmatory test on unseen data. | DONE (#141, #142, #143): forward tracker `fair_value_forward.py` freezes each month-end's decisions before the next open from 2026-10, scores 120-session outcomes with reward v5, and evaluates once on the first 24 active months. Protocol registered 2026-10-19 (hash `6d09af6c…`). First freeze 2026-10-30; first outcome due 2027-04-27. |
| 45 | Owner-proposed strategies (momentum breakout, trend pullback, post-earnings momentum, value + technical confirmation) untested as rules with path-dependent exits. | DONE for development and the forward test (#144, #145, `feat/strategy-forward-shadow`): pinned rules (`strategy_signals.py`); SEC 8-K 2.02 announcements pinned by hash; protocol `event-strategy-replay.v1` registered (`3fa1d5cf…`). Development 2016–2023 (`event-strategy-replay-dev-2026-10-19.md`): no edge in any strategy (S1 −0.009, S2 +0.006, S3 −0.002, S4 −0.001 per month; every 95% interval spans 0); S2's path exits significantly worse than a fixed hold (disclosed, not tuned). Forward shadow `strategy_forward.py` (#146) registered 2026-10-19 (protocol `0a26e0b7…`, record `evidence_4ce5c403…`); it freezes each session's flags before the next open from 2026-10-26 and evaluates once on 2026-11..2028-10. Hold-out 2024-01..2026-08, run once after the registration (`event-strategy-replay-holdout-2026-10-19.md`): S1, S2, S3 NOT_SUPPORTED (−0.0008, −0.0064, +0.0024 per month, family-wise intervals span 0); S4 exploratory +0.0015 [−0.005, +0.008]. The forward shadow is the remaining test. |
| 46 | The agent and the MCP connector could not see strategy signals, strategy evidence or whether anything had been learned. | DONE (#146): read-only `/api/v1/strategies`, `/api/v1/stocks/{T}/strategy-signals`, `/api/v1/agent/learning-status` and MCP tools `get_strategy_evidence`, `get_strategy_signals`, `get_learning_status`. Information only: a test guards that no deciding, learning or trading module imports them. |
| 47 | The SEC User-Agent defaults to a generic string without a contact address (SEC asks for one); data.sec.gov accepts it today, www.sec.gov (filing archives used by the inline-XBRL fallback, company tickers) does not. | CODE DONE (`fix/sec-user-agent`): every SEC-reading job passes the `SEC_USER_AGENT` repository variable; an unset variable falls back to the default instead of an empty header; the strategy-forward receipt reports `sec_contact_configured`. OPEN (owner): create the Actions variable `SEC_USER_AGENT` (e.g. `StockMachine research <your contact email>`) and the same Vercel environment variable. |
| 48 | `features._atr_pct` mixes unadjusted high/low with the adjusted close (the event strategies compute ATR on normalized bars instead). | OPEN (low): `atr14_pct` is recorded in every decision's technical state but no rule or bandit feature reads it; fix under a new feature version so recorded states stay comparable. |
| 49 | The fair-value forward tracker would have stayed red for a week after every month-end following any edit to a tracked file (fingerprint compared with the first month, sticky), reported partial freezes and excluded months as green, did not fingerprint its own code, and a moved tracked file would have cost a month. | DONE in #148 (protocol `6d09af6c…` and both behaviour pins unchanged): a code change is recorded once against the previous month; FROZEN_WITH_MISSING_BARS / OUTCOME_EXCLUDED_MISSING_BARS / OUTCOME_WITHOUT_MEAN stay visible for a week; blocked months are reported by every run; the module and inference.py are fingerprinted; outcomes and the final evaluation carry the code version; a missing tracked file is reported nightly. |
| 50 | Neither forward tracker was visible to the health watchdog or the admin page beyond "the workflow ran"; a stopped schedule stayed green forever; the public agent-lab `policy.mode` reads RESEARCH regardless of the real switch. | DONE (`feat/forward-tracker-visibility`, after #147): watchdog content checks for both trackers from `/api/v1/agent/learning-status` (recorded losses for a week; a loss no run recorded = tracker not running), a 36h no-recent-run rule for every watched workflow, an admin card, the strategy shadow hardened like #148 (partial sessions and excluded trades visible for a week, tolerant fingerprint, provenance), and the real trading mode in learning-status. |
| 51 | Option-surface capture has never succeeded (29 runs since 2026-09-09): HTTP 401 since 2026-09-17 because the GitHub Production `STOCK_MACHINE_ADMIN_TOKEN` differs from Vercel's, and Vercel has no IBKR bridge configured (503 "No module named ibapi"). | OPEN (owner): make the two admin tokens identical (or rotate both); set `IBKR_PROVIDER=remote_bridge`, `IBKR_BRIDGE_BASE_URL`, `IBKR_BRIDGE_TOKEN` in Vercel and redeploy; keep the Mac bridge and tunnel running in the evening. Historical IV: IBKR offers a daily 30-day ATM implied vol per stock (no expired contracts, skew or quotes) — a one-off local probe can size it for an exploratory test; a confirmatory option-spread test needs vendor data (ORATS, Cboe DataShop). |
| 52 | Paper mode: the 2026-10-07 release set PAPER, the public label hides it, and no evaluation of the paper ledger is pre-registered. | PRE-REGISTERED (`feat/paper-ledger-evaluation`; `paper-ledger-evaluation-protocol-2026-10-20.md`): `agent-paper-ledger.v1:evaluation.v1` (`paper_ledger_forward.py`, protocol `3e953d56…`, behaviour pin `c8336b1c…`). Nightly on main (04:30 UTC) it copies each in-scope Agent Trading v1 position append-only at entry and exit, flags a ledger row that later changes (LEDGER_MUTATED), a late copy or a missed night, and scores each position once by its own decision's label (reward v5: residual vs beta x SPY net of 20 bps over ex-ante volatility), reconciling the ledger without dropping on it. One verdict, once, on positions entered 2026-11..2028-10 (extended once, on counts alone, to 2029-10): monthly book = sum / 54, empty months 0, Newey-West lag 1, t(n-1) 95%; needs >= 60 scored positions in >= 12 months, PAPER on and capture unpaused for >= 0.9 of sessions, <= 0.1 excluded or late; a pass must survive exclusions scored at the worst score or -3.0, whichever is lower. Underpowered (about 3/sqrt(N) per position): NOT_SUPPORTED is likely and is not evidence of no edge. Information only; until the final record paper P&L is not evidence (now labelled on the admin cards). Also fixed: learning-status `paper_outcomes` was always null (it read keys the stage receipt strips). REGISTERED ON TIME: the first scheduled run (2026-10-21 09:31 UTC, run 39577647430; GitHub starts the 04:30 UTC cron about five hours late) recorded the protocol, and learning-status reads `protocol_registered: true`, `registered_late: false`, every recorded check 0 (checked 2026-10-22). OPEN (owner): keep PAPER on and capture unpaused; keep reviewed financials current (53). The public agent-lab `policy.mode` still reads RESEARCH. |
| 53 | Reviewed debt and balance fields are pinned to one filing: 35 of the 52 reviewed issuers file a new 10-Q between about 2026-10-20 and 11-13 (GM, NFLX, then AAL, IBM, LUV, TMUS, TSLA, TXN, …) and each drops to withheld until re-reviewed; a stale review shows the same reason as a never-reviewed one. | NAMED AND LISTED (`feat/stale-review-scan`; diagnostic only): a review a newer filing superseded is named next to the generic reason it explains, in the bundle only — `REVIEW_PINNED_TO_SUPERSEDED_FILING:<kind>` (re-review), `REVIEW_NOT_BINDING_FOR_PERIOD:<kind>` (this period's review no longer validates: repair), `REVIEW_AWAITING_NORMALIZATION:<kind>` (promoted; the next nightly applies it). Status, numbers, blockers, the generic reasons and both forward trackers' fingerprints are unchanged. `GET /api/v1/financial-reviews` (read-only, no SEC call), an admin card and the watchdog check `financial_reviews` list every reviewed issuer against its latest stored quarter and filing, with its next step and the drafter commands; the watchdog alerts only for a re-review more than 7 days after the filing, a review that no longer binds, or a filing not normalized for 3 days. OPEN (owner): re-review each issuer as its 10-Q is normalized — `draft`, then `promote --kind balance` for liabilities and cash, then `promote` (`--accept-flags` only after reading the flags; zero-debt CMG and PLTR), and `custom` with the previous selection's tags and members for F, GM, TMUS; needs `SEC_USER_AGENT` with a contact address locally, and as the Actions variable for the nightly (item 47). |
| 54 | Open PRs left from September: #131 (flaky-test fix, conflict-free), #82 (I01, fully replaced by #90; merging would revert production code), #86 (I05 on a stale stacked branch, 6 conflicts with main, a schema bug). | OPEN (owner): merge #131; close #82; close #86 (re-land its QuantStats performance view fresh from main only if still wanted). |
| 55 | Options pay for the size of a move, so an options edge first needs a volatility forecast better than simple rules; no test existed, and IBKR's implied-vol history was unmeasured. | DONE (#150, #152, #153; `volatility-forecast-holdout-2026-10-20.md`): hold-out 2024-01..2026-08 (run once, 1,728 stock-months) — **H2 SUPPORTED** (+0.058 [+0.016, +0.100] family-wise) and **H4 SUPPORTED** (+0.086 [+0.025, +0.146]); HAR_PLUS QLIKE 0.236 vs HAR 0.294 vs RV21 0.473. First confirmatory result in the project. IBKR implied vol for all 55 symbols from 2006. NEXT: `implied-vol-comparison.v1` (does the forecast beat the options market's implied vol?), runs locally on IBKR data. |
| 56 | The nightly refresh replaced SPY's 15-year history with FMP's 5 years (Yahoo failed on 2026-10-20 00:27 UTC; `replace_prices` rewrites the whole series), silently breaking every replay and volatility feature that needs SPY before 2021-10. | FIX: `replace_prices` refuses a refresh that starts more than 31 days later and spans under 90% of the stored history (existing data retained); a Yahoo-only, main-only Price History Repair workflow; a watchdog check that SPY's history reaches back 14 years. DONE (#151): SPY repaired from Yahoo (run 39448003492, 3,770 sessions from 2011-10-20). Only SPY was truncated (all 69 stored tickers checked). |
| 57 | The volatility forecast beats simple rules (55), but an options edge needs it to beat the options market's own implied volatility; untested. | DONE (#154 and this PR; `implied-vol-comparison.v1`, `50f81997…`, registered with its two input files' hashes; local IBKR files, aggregates only): hold-out 2024-01..2026-08, run once, 1,726 stock-months — **V3 NOT_SUPPORTED** (−0.003 [−0.009, +0.002] family-wise: adding our forecast to implied volatility gains at most about 1% of the market's loss) and **V4 NOT_SUPPORTED** (relative-value variance book −0.071 [−0.256, +0.115] family-wise; the one-point-a-side cost, about −0.15 a month, decides it, and before cost the book was +0.07 (not significant) and about 0 in development). QLIKE calibrated IV 0.193 vs ours 0.238, the market better in 27 of 32 months (`implied-vol-comparison-holdout-2026-10-20.md`; dev in `…-dev-2026-10-20.md`, its book reading corrected). Closed: no edge from our monthly volatility forecast against 30-day at-the-money implied volatility; skew, term structure and per-expiry earnings implied moves remain untested and need the forward option-surface capture (51). |
| 58 | Every test so far is a 20-session directional call; intraday first-hour trading (Meir Barak's gap and go, gap fill, break of structure and the narrowing-range "h structure") and the published opening-range breakout were untested, and no intraday data existed. | DRAFT (`feat/intraday-replay`; `intraday-replay-protocol-v1.md`): `intraday-first-hour.v1` (`intraday_replay.py`) replays IBKR 1-minute bars for the 54 stocks and SPY, kept local (IBKR terms; aggregates only), fetched by stock-month (`scripts/fetch_ibkr_intraday.py`, read-only, local host, refuses hold-out months). Confirmatory G1, G2, B1, C1 (Barak, flat by 10:30) and X1 (Zarattini-Barbon-Aziz stocks-in-play ORB, to the close), family-wise z 2.576; exploratory E1 earnings continuation, M1 SPY intraday momentum, R1 end-of-day reversal, A1 attention gap fade. Reward v5 on intraday windows (residual vs beta x SPY over the same minutes, 10 bps a side, over the window's realized volatility), a random-direction twin for direction skill, a daily book over eligible stock-days; SUPPORTED needs the mean, its lower bound and direction skill above 0 with coverage. Barak's claims (68% G1 wins, 80% of 1-3% gaps fill, 80% of breakouts fail) are checked descriptively. 2026-10-22 (`feat/intraday-dev-report`): a preview over the finished months (`scripts/report_intraday_replay.py preview`) and two audits of it on the real 2016 bars (trades rebuilt bar by bar, each finding re-checked by a skeptic) fixed touch-fills at computed levels, silently dropped setups on missing minutes, X1's volume history and the exclusion naming. Each fix is listed under "Changes during development" in the protocol. A post-look A1 rule was withdrawn, and the behaviour pin now catches every applicable single-rule mutation tried. The fetcher asks day by day for a week IBKR will not answer whole. 2026-10-22 (`feat/intraday-lab`): the intraday lab (`intraday_lab.py`, `intraday-lab.md`) explores the owner's gap strategies on 5-minute signals: gap up 3%+ buy the pullback and gap down 3%+ buy the reversal, each entered on the way down at half the bounce, on a cross back above the open, or on a reversal bar; 2R target, turn-against exit. Explore 2016-2019 freely with every try logged (`intraday-lab-log.json`); check survivors once on 2020-2023; never 2024+. First logged run, 2016: all six negative at 10 bps a side; GD-LIM.v1 nearest (-11 bps, +5 at 2 bps, 98 trades). 2026-10-22 (`feat/intraday-confirm`; `intraday-discovery-2026-10-22.md`): analysts read only 2016-01..2017-06 and found that post-open price action barely predicts the rest of the day, while what is known at 09:30 does (an extension of yesterday's move, an earnings day, a market-wide gap). The owner froze `EXT-FADE.v2` (quiet-market extension fade, control `EXT-FADE.v1`) and `EARN-FH.v1` (earnings first-hour fade, sub-test `EARN-FH15.v1`). The lab's splits are now explore 2016-01..2017-06, confirm once 2017-07..2019-12, check once 2020-01..2023-12, never 2024+. Pass bar at 5 bps a side: mean net above 0, leave-one-session-out t above 2, positive in 2 of 2017H2/2018/2019 (the check: 3 of 2020-2023). A confirm needs an explore with the same code and inputs, and a check a passed confirm with the same code, definition and inputs. The replay's previews and development run wait for these looks (`LAB_LOOKS_PENDING`). The replay now excludes daily-bar errors (`DAILY_ROW_ERROR`, `DAILY_CLOSE_MISMATCH`, `SPY_DAILY_ERROR`) and keeps them out of the trailing series; across every daily file 2011-2026 only CMCSA 2017-02-17 is one. Logged explore (entries 7-16): EXT-FADE.v2 +40.9 bps net at 5 (263 trades, t 3.70), EXT-FADE.v1 +21.7 (547, t 2.61), EARN-FH.v1 +33.6 (109, t 2.52), EARN-FH15.v1 +49.3 (74, t 3.20); expect 30-50% shrinkage after about 2,500 tries. NEXT: the one confirm of the four frozen variants once the fetch finishes 2019-12; the check for any that pass once 2023-12 is fetched; then the replay's development run, `--repair`, registration, the hold-out fetch 2024-01..2026-08 and one run. |
| 59 | A pretrained candlestick foundation model (Kronos, AAAI 2026, 43k GitHub stars) was proposed as a way to predict the next candle and add value on top of the agent; it had not been reviewed. | NOT PURSUED (owner, 2026-10-23; `kronos-review-2026-10-23.md`; read-only review: nothing installed, downloaded or run). Kronos was pretrained on NYSE and Nasdaq bars from 2000 to about mid-2024, so every window of ours before 2024-07 is contaminated for it; its paper has no naive or HAR baseline, and its own Nasdaq table has GARCH beating every Kronos size at volatility. The best unverified US test (182 stocks, 2024-07..2026-07, after the stated cutoff) found an IC of +0.022 (t 1.39, not significant, weaker than 5-day reversal); its long-short Sharpe fell from 0.77 to 0.35 at 10 bps a side. Its one plausible use, volatility, is already dominated here by implied volatility (item 57). A cheap test on post-cutoff daily data, to be pre-registered before any Kronos output exists, is designed in the note, with prior odds of about 10% (volatility, against HAR_PLUS) and 3–5% (direction). |

## Change log

- **2026-10-10 — reward v4 + headline classifier** (branch `fix/reward-and-news-classifier`):
  - `reward.py` is now `risk-scaled-net-paper-reward.v4`: (gross − costs) ÷ ex-ante
    volatility over the realized holding period. It uses `realized_vol_20`, then
    `realized_vol_60`, then a recorded 40% default, with a 5% floor. Volatility comes
    from the decision-time state only.
  - Bandit state records `reward_version`. Arms trained under v3 are ignored, so learning
    cold-starts and v3/v4 scales never mix. Existing v3 reward records are kept unchanged.
  - The headline classifier uses word-boundary patterns and a 3-word negation window, and
    no longer treats approvals or clearances as regulatory action. "To buy" is limited to
    deal phrasing.
  - Tests: reward units, the positive-edge sanity check, volatility fallbacks, the
    version-scoped arms, and 15 headline regression cases.
  - No migration or change to trading mode.
  - Expected effect: the trade arm's average reward reflects net edge instead of being
    pushed below abstention.
  - Item 2 is still needed: one unlucky draw can still lock an arm out.

- **2026-10-10 — pooled Thompson sampling bandit** (branch `fix/bandit-lockin`, stacked on PR #99):
  - `bandit.py` is now `pooled-linear-thompson.v1`: one Bayesian linear model per trading
    action, shared by all covered stocks. It has a full precision matrix, prior precision 1,
    and unit noise variance (matching v4 reward units).
  - Context is `[intercept, fundamental, technical, news, regime, volatility]`; the
    collinear `bias` feature is dropped.
  - NO_TRADE is a fixed zero baseline: abstention reward records are kept for audit but do
    not update a model. Ties go to NO_TRADE.
  - Sampling is seeded by the decision key, so every choice can be reproduced.
  - Pooled state records `scope`, `bandit_version`, `reward_version` and a monotonically
    increasing `sequence`, written under a global advisory lock. It is read by `sequence`,
    not `recorded_at`, because `recorded_at` is transaction start time and could make a
    lock-waiting writer look older than the state it extended.
  - Per-ticker diagonal states from earlier contracts are ignored (cold start).
  - The per-decision trial-count scan is removed. The admin label changes from "UCB" to "score".
  - No migration or trading-mode change.

- **2026-10-10 — prospective counterfactual learning + fixed-horizon holds** (branch
  `fix/counterfactual-learning-fixed-horizon`, stacked on #100):
  - Decisions now freeze a learning window at decision time (`learning_contract`
    `prospective-counterfactual.v1`): the entry is the first close after the journal
    recording time, the same session a paper fill uses, and the exit is 20 sessions later.
  - Once that window matures, every paper-eligible decision is scored for both LONG and SHORT
    from the same-vintage adjusted path with fixed 10 bps/side costs. This applies whether
    the decision was traded, held, abstained, capacity-blocked or made in SHADOW mode.
  - Each label updates the pooled model with weight 1/20; `effective_observations` is
    recorded alongside the raw count.
  - One `AGENT_REWARD_V3` record per decision now holds both directions' outcomes. Realized
    P&L stays in the paper ledger and weekly audit.
  - The candidate scan skips immature windows in SQL and orders by target session.
  - Pooled state also records `learning_basis`, so earlier states cold-start.
  - Decisions under `executed-paper.v3` are excluded as `EXCLUDED_PRIOR_LEARNING_CONTRACT`.
  - Paper trading: positions opened under `prospective-next-close.v2` hold for 20 sessions;
    earlier signal changes become HOLD, with the commitment recorded in the intent's risk
    snapshot.
  - No migration or trading-mode change.

- **2026-10-10 — learned-direction challenger** (branch `feat/learned-direction-challenger`,
  stacked on #101):
  - Each decision records `direction_challenger`: the heuristic direction, the pooled
    model's posterior-mean direction (LONG/SHORT/FLAT, using only the state known at
    decision time), both posterior estimates, the pooled-state sequence, and
    `acts_on_paper: false`.
  - `direction.evaluate` pairs both directions on the matured counterfactual rewards and
    resamples 20-session blocks under a protocol fixed in code. The protocol hash is stored
    in every snapshot, so a changed protocol starts fresh evidence.
  - Results: `PENDING_EVIDENCE`, `NOT_SUPERIOR` or `PASS_REQUIRES_INDEPENDENT_REVIEW`; never
    an automatic promotion.
  - Shown in the learning-stage receipt and an admin card, read-only. No migration, no
    trading change.

- **2026-10-10 — shadow skill metric + stock-specific targets** (branch
  `fix/shadow-skill-metric-excess-returns`, stacked on #102):
  - `agent-shadow.v2` with target `spy-beta-residual.v1`. Snapshots freeze beta and ex-ante
    volatility; outcomes record raw, SPY, residual and volatility-scaled residual returns
    plus the frozen component values. The price-vintage hash covers SPY.
  - Candidate weights are now `family-budget-shrunk-information-coefficient.v2`: a
    direction-sensitive, scale-free IC, shrunk toward pooled history (prior 32), negative
    skill clipped to 0, a family budget for forecast models, and `NO_POSITIVE_SKILL` instead
    of forced weights.
  - Training history and the weekly view use only v2-target outcomes.
  - No migration or trading change.

- **2026-10-11 — #100–#103 landing + technical-setup utility** (PR #104, branch
  `fix/technical-setup-utility`):
  - #100–#103 had merged into their stacked base branches. PR #104 lands those four
    commits on main unchanged.
  - `technical-setups.v1` policy: utility = shrunk mean − 1.645 × standard error of the
    shrunk mean, with prior strength 32 counted in non-overlapping holding windows. Pooled
    evidence counts each date once across stocks.
  - Weight counts now report `stock_windows` and `pooled_windows`.
  - The policy hash changes, so new daily runs are versioned separately from the
    abstain-everywhere runs.
  - No migration or trading change.

- **2026-10-11 — pooled shadow statistics, explicit training window, missing-feature
  flags** (branch `feat/shadow-pooled-stats-window-missing-features`):
  - `shadow.pooled_statistics` gives per-horizon information coefficients (candidate,
    current, difference), hit rates and forecast Brier skill, with block-bootstrap 95%
    intervals (2,000 draws, fixed seed). It's included in `weekly_summary` as
    `pooled.week` and `pooled.cumulative` and shown in admin.
  - Outcomes now also record the frozen `candidate_score` and `baseline_score`.
  - Training history is time-based (252 sessions) and reads only training fields.
  - Bandit v2 context: intercept, four signals, standardized volatility, and five missing
    flags. Cold-starts the pooled model.
  - No migration or trading change.

- **2026-10-11 — outcome retry rotation + candidate-weight promotion test** (branch
  `feat/outcome-retry-rotation-shadow-weight-protocol`):
  - Migration `0027_agent_outcome_checks` adds evidence kind `AGENT_OUTCOME_CHECK_V1`,
    with release workflow `agent-outcome-checks-migrate-0027.yml`. It verifies the
    revision and the constraint, and doesn't touch paper settings. The 0025 workflow's
    revision allowlist now includes 0027.
  - `outcomes.record_check` writes one record per blocked decision per completed
    session, idempotently, inside a savepoint. `outcome_candidates` skips decisions
    already checked this session.
  - `shadow.WEIGHTS_PROTOCOL` and `weights_promotion_test` are added, with the protocol
    hash frozen into snapshots and outcomes. The result is reported in `weekly_summary`
    and the admin card.
  - Shadow `pooled_statistics` accepts the protocol's bootstrap draws and seed.

- **2026-10-11 — entry-aligned shadow windows + ledger reconciliation** (branch
  `feat/entry-aligned-shadow-ledger-reconciliation`):
  - Shadow target `spy-beta-residual-entry.v1`. Snapshots record `entry_session`,
    `entry_basis` and `forecast_due_session`; `due_session` is the later of the two
    windows. Outcomes record both `realized_return_pct` (entry window) and
    `forecast_return_pct` (origin window); pooled Brier skill uses the forecast window.
  - Immature pre-change snapshots return `PENDING_ENTRY_WINDOW` without a check record.
  - The weights protocol hash changes with the target, so its evidence restarts.
  - `agent_intelligence/reconcile.py` (read-only) reports the ledger against the
    counterfactual labels in the learning-stage receipt and admin.

- **2026-10-11 — precommitted exits at the target close** (branch
  `fix/precommitted-exit-at-target`):
  - `agent_trading._settle_commitment` is shared by `settle_holding_limits` and
    `process_decision`.
  - Exit market date and price are the target session's close. Fill rows record that
    session; created_at shows when settlement actually ran.
  - A matured, unpriced target blocks: the scheduled job reports ATTENTION and decisions
    hold.
  - The paper stage runs exits before pending entries, with no 6-per-pass cap.
  - No migration or trading-mode change.

- **2026-10-11 — one connection per scoring pass** (branch `perf/scorer-shared-connections`):
  - `outcomes.score_matured` uses `_score_one` per decision inside `conn.transaction()` on
    one autocommit connection. A failed decision rolls back fully, then writes its retry
    check on the same connection.
  - `shadow.score_matured` follows the same pattern and fetches SPY prices once per pass.
  - `learning.record_counterfactual(..., conn=)` and `outcomes.record_check(..., conn=)`
    reuse the caller's connection; standalone calls still open their own.

- **2026-10-11 — power and size of the promotion tests** (branch `feat/promotion-test-power`):
  - `agent_intelligence/power.py` simulates production-shaped panels and runs the live
    evaluators. `rule_comparison` checks the current rule against Newey–West + t;
    `calibrate` reads real records. `scripts/power_promotion_tests.py` is the CLI.
  - `direction.evaluate` and `shadow.weights_promotion_test` now report
    `detectable_difference_80pct` and `detectable_at_minimum_blocks`. The protocols and
    their hashes are unchanged.
  - The direction block index is cached (it was one calendar query per decision row).

- **2026-10-11 — stock-specific rewards, sector intercepts, confidence-gated trading**
  (branch `feat/residual-rewards-offsets-confident-trading`):
  - Reward `risk-scaled-residual-paper-reward.v5`. Outcomes record
    `market_return_pct`, `beta`, `beta_basis` and `residual_return_pct`; the learning
    step rejects labels without a residual.
  - Bandit `pooled-linear-confident-sector.v3`:
    - `predict` adds a shrunk sector intercept and its variance;
    - `update_sector_offset` learns from each label's residual against the pre-update
      pooled estimate;
    - `select` trades only when the mean minus 0.8416 × the uncertainty is > 0, so it
      is deterministic. Choice drivers: EXPLOIT_CONFIDENT, ABSTAIN_UNCERTAIN,
      ABSTAIN_NO_EDGE.
  - Decision state records the stock's sector, and the direction challenger uses
    sector-aware estimates.
  - The pooled model cold-starts (new reward and bandit versions).
  - No migration.

- **2026-10-11 — sector-relative diagnostics + technical uncertainty** (branch
  `feat/sector-relative-diagnostics-technical-uncertainty`):
  - Shadow snapshots record `sector_etf`. Outcomes record `sector_relative_return_pct`,
    `sector_relative_z` and `sector_relative_basis`. Pooled statistics add
    `candidate_ic_vs_sector`, `baseline_ic_vs_sector` and `ic_difference_vs_sector`.
  - Counterfactual outcomes record `sector_symbol` and `sector_relative_return_pct`
    (diagnostic only).
  - `technical_setups.metrics` adds `effective_trades`, `mean_net_return_ci95` and
    `net_win_rate_ci95`. Adds `technical_setups.placebo`, and `walk_forward` returns
    `placebo` (stored with each run and shown in admin).
  - The store passes the precomputed walk-forward context; the summary tolerates fields
    missing from older runs.

- **2026-10-11 — prospective Brier reference, precomputed cumulative view, calendar
  look-back fix** (branch `feat/prior-climatology-precomputed-cumulative`):
  - `shadow.reference_counts`, `prior_base_rates`, `cumulative_summary` and
    `latest_precomputed` are added.
  - `pooled_statistics(..., counts=)`; `weekly_summary(..., cumulative=)`.
  - `learning_operation` saves `shadow_cumulative` in its receipt.
  - `market_calendar.session_offset` sizes the calendar for negative offsets.

- **2026-10-11 — promotion tests v2** (branch `feat/promotion-tests-v2`):
  - `direction.PROTOCOL` and `shadow.WEIGHTS_PROTOCOL` are now v2: Newey–West lag-1
    + t(n−1), at least 24 blocks, with a `supersedes` note.
  - New `inference.py` (t quantile, Newey–West, jackknife, bounds, detectable
    difference). `power.py` uses it and the live protocols; the v1/v2 rule comparison
    stays labelled.
  - Item 24 is closed as documented.

- **2026-10-11 — owner-gated learned direction** (branch `feat/learned-direction-owner-gate`):
  - `agent_intelligence/direction_policy.py`: `setting`, `latest_evaluation`,
    `effective`, `set_policy` and `apply`.
  - The orchestrator freezes `direction_challenger` before routing, records
    `direction_policy`, and routes on the learned direction when it's effective
    (`state.direction_source`, `heuristic_direction`).
  - Owner endpoint `POST /api/operator/direction-policy` (409 when the test isn't
    passing or the version changed), plus admin controls.

- **2026-10-11 — close-out:** `technical-setups.v1`'s hashed `candidate_selection` label
  said "minus one standard error" while the rule (#105) subtracts 1.645. The label is
  corrected, so the policy hash changes and new daily technical runs are versioned
  separately; behavior is unchanged. Item 6 is marked done. **All 32 tracked items are
  now done or closed (24 as documented).** What remains is operational: watch the first
  matured windows from November.

- **2026-10-17 — learning from stocks with unverified financials** (branch
  `feat/learn-from-unverified-fundamentals`):
  - New `agent_intelligence/eligibility.py`. A decision is learning-eligible when it's
    paper-eligible, or when its only blockers say the financial data is unverified (state
    and journal checked separately) and price-based inputs are present. In the second case
    the fundamental signal is set to missing.
  - The outcome scanner and learning step use it, and reward records carry `eligibility`.
  - `direction.matured_rows` keeps the protocol's paper-eligible unit.
  - Paper eligibility is untouched.
- Debt reconciliation drafts (item 33, PR `feat/debt-reconciliation-drafts`):
  - `stock_machine/debt_reconciliation.py` parses inline XBRL with the standard library: consolidated,
    non-dimensional USD facts at the period-end instant. Face balance sheet debt lines are components;
    operating leases, segment (dimensional) facts and face subtotals are excluded; a missing debt line
    is never zero.
  - Drafts are `DRAFT_CLEAN` only when every line is a us-gaap tag, a debt-note total corroborates the sum
    and same-accession bindings exist. Anything else (issuer tags, finance leases, banks, no face debt
    such as Ford's segment-split debt) is `DRAFT_NEEDS_REVIEW` or `NO_PROPOSAL`.
  - `promote` re-fetches the filing (hash must match), re-normalizes, and appends only entries
    `financial_integrity.validate_reviewed_debt` accepts. Sample: NVDA promoted in a dry run verifies
    ($33.366B debt, $10.923B net); AAL, TSLA need review; F has no proposal.
- Price gaps and research coverage (items 35 and 36, PR `fix/technical-gaps-and-research-queue`):
  - `data_quality.complete_bar` is the single definition of a usable daily bar, shared by the technical
    features, `pipeline._completed_prices` (new retryable `PRICE_LATEST_BAR_INCOMPLETE`) and
    `market_health.health` (new `INCOMPLETE` state, counted as stale so the refresh re-fetches it).
  - `technical_setup_store` receipts carry `attention.gap_tickers`/`systemic`; the scheduled job fails only
    when more than 3 stocks have gaps (`MAX_ISOLATED_GAP_TICKERS`).
  - `scheduled_operations.INGESTION_REPAIR_CRON` and `automation.research_unready`; the workflow gate skips
    the repair when the session's receipt is already successful.
- Clean debt reviews promoted (item 33, `data/promote-clean-debt-reviews`): ABNB, AMD, DELL, INTU, KLAC, MCD, META,
  NVDA, SBUX, TXN redrafted with the #124 parser (all still clean), then promoted: each filing re-fetched with a
  matching hash, re-normalized, and accepted by `validate_reviewed_debt`. Seven pass financial integrity.
- Missing balance fields and Q4 EPS (items 38 and 39, PR `fix/balance-liabilities-cash-eps`):
  - `debt_reconciliation.propose_liabilities` / `propose_cash`, `financial_integrity.validate_reviewed_field`,
    `normalization/reviewed_balance_fields.json`; normalization fills only fields it did not produce, before
    reviewed debt. `draft_debt_reconciliations.py promote --kind balance`.
  - Effect on the latest quarter: AMD, CMCSA, HPQ, MCD, SBUX and T now pass financial integrity. AAL, DIS, INTC,
    LUV, NKE and WMT now lack only reviewed debt (item 33 review drafts).
- Owner-accepted balance fields (`data/accept-amzn-tgt-balance-fields`): AMZN total liabilities ($544.069B, identity
  residual 0) and TGT cash ($5.411B). Both now lack only reviewed debt, like AAL, DIS, INTC, LUV, NKE and WMT.
- Review groups 1 and 2 promoted (item 33, `data/promote-debt-groups-1-2`), owner-approved:
  - Redrafted with the current parser; totals were unchanged and none were dimensional.
  - Group 1, plain debt lines: ADBE, ADI, AMAT, BKNG, CSCO, DIS, IBM, INTC, NFLX, NKE, NOW, ORCL, QCOM, RIVN.
  - Group 2, finance leases included: AAL, DAL, HD, LOW, LRCX, LUV, TGT, UAL, WMT, TSLA.
  - All 24 pass financial integrity on the latest quarter.
- Zero-debt rule, plus group 3 and SOFI (item 33, `feat/zero-debt-rule-group3-sofi`):
  - Zero is a reviewed finding, never a default. It is proposed only when no obligation is disclosed anywhere in the
    filing, and accepted only with method `NO_DEBT_DISCLOSED`, same-accession bindings and no normalized debt fact.
  - Promoted, owner-approved: AMZN, COST, CRM, GOOGL, SOFI, CMG (zero) and PLTR (zero). 51 of 54 issuers now pass.
  - Seen in passing: `test_agent_shadow_postgres.py::test_training_cutoff_excludes_future_recording_and_target_sessions`
    fails intermittently, only in the full Postgres suite. Flagged as a separate task.
- Owner-selected segment debt (item 33, `data/segment-debt-f-gm-tmus`):
  - The parser records dimension members, and a new `custom` command promotes owner-chosen face debt facts by element
    id (they must be face debt facts for the period and pass `validate_reviewed_debt`).
  - F: $19.595B, Company excluding Ford Credit. GM: $15.979B, Automotive. TMUS: $86.920B (short-term, long-term
    non-related and related party, financing leases).
  - All three pass the balance check; F remains withheld for item 40.
- Filing XBRL fallback (item 40, `feat/filing-xbrl-fallback`):
  - The parser moved to `ingestion/inline_xbrl.py`; it now also handles durations, per-share units and dei fiscal
    focus.
  - `ingestion/filing_xbrl.py` has `lagging_filings`, `filing_facts`, `merge` and `supplement_lagging_filings`. It runs
    in `sec.ingest` before the cover-share supplement, and the draft tool's `latest_quarter` uses it too.
  - Ford reviewed debt for 2026-06-30 is promoted (automotive only).
- Heuristic direction replay (item 41, `feat/direction-replay-backtest`):
  - `backtest/direction_replay.py` (protocol hashed before any run) and `scripts/run_direction_replay.py`.
  - Manual workflow `direction-replay.yml` (read-only DB session, evidence artifact).
  - Results go to `docs/evidence` after the first run.
- Paper-ledger evaluation pre-registered (item 52, `feat/paper-ledger-evaluation`):
  - `agent_intelligence/paper_ledger_forward.py`, a third forward tracker on the FV_FORWARD_* kinds under its own id. It
    snapshots the mutable ledger append-only, scores by immutable labels, and writes one final verdict.
  - Surfaced in learning-status `forward_tests.paper_ledger`, the watchdog (`paper_ledger_forward` plus the workflow)
    and the admin forward-tracker card. Paper P&L cards are labelled not evidence.
  - Four adversarial reviews before registration changed the design. Positions are now scored from labels so a pause
    cannot drop losers; the capture pause counts against the mode rule; a pass must survive worst-case exclusions;
    the pins no longer depend on the universe or future calendars; a new selector version stays in scope.
- Stale reviews named and listed (item 53, `feat/stale-review-scan`):
  - `stock_machine/review_coverage.py`: `annotate` adds the three diagnostic reasons next to TOTAL_DEBT_NOT_SOURCE_RECONCILED,
    MISSING_BALANCE_FIELD:total_liabilities or CASH_AND_EQUIVALENTS_MISSING_OR_INVALID, through `bundle._financial_integrity`.
    `financial_integrity.py`, `data_quality.py` and every other file in the forward trackers' decision sources are untouched.
  - `review_coverage.scan` serves `GET /api/v1/financial-reviews`, the owner card and the watchdog check.
  - Runbook: `SEC_USER_AGENT='StockMachine research <contact email>' python scripts/draft_debt_reconciliations.py draft --tickers
    <due> --out docs/evidence/debt-reconciliation-drafts-<date>`; read the .md; `promote --kind balance --drafts <stem>.json
    --tickers <liabilities/cash>`, then `promote --drafts <stem>.json --tickers <debt>`; F, GM, TMUS via `custom`. A merged
    review applies at the next nightly (AWAITING_NORMALIZATION until then).
- Event strategies (item 45, `feat/event-strategies`):
  - Entries are judged on closes and executed at the next close; exits are path-dependent (trailing 2×ATR on
    normalized bars, trend failure below the 50-day average, gap fill for S3, maximum hold).
  - One open trade per stock and strategy; a missing bar excludes the trade and locks the stock until its maximum
    hold.
  - Monthly equal-weight book scored with reward v5; Newey-West lag 3.
