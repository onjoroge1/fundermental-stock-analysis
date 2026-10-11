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
| 4 | 54 independent per-ticker learners, ≤ ~12 non-overlapping trades/yr each. | Bandit state is keyed by ticker. | One pooled model with ticker/sector shrinkage (pattern exists in `shadow.candidate_weights`). | PARTIAL: one pooled model per action ships with item 2; ticker/sector random effects still OPEN (the simulation shows pooling still wins with ticker offsets of sd 0.2, but per-ticker differences are ignored). |
| 5 | The "20-session hold" is not real. Each daily re-decision can emit CLOSE, so a trade's reward depends on later unrelated choices; trade and abstention horizons differ. | Simulation (year 2): daily re-decision averaged a **5.5-session** hold and 1,757 trades/yr (net +46.5 after costs); committed 20-session holds made 623 trades/yr (net **+65.1**). | Positions opened under the prospective contract are commitments: an opposite or flat signal before the 20-session target becomes HOLD; the holding limit closes them. Legacy positions keep signal exits. | DONE (#101, on main via #104) |

## High

| # | Gap | Evidence | Fix direction | Status |
|---|---|---|---|---|
| 6 | Direction is never learned (hand-set weights 0.50/0.25/0.15/0.10, ±0.25 threshold); no promotion rule for shadow weights. | `state.assemble` | Each decision freezes the heuristic direction and the pooled model's learned direction (`direction_challenger`). Both are scored on the same matured counterfactual rewards, with FLAT = 0. A paired test is fixed in code (`learned-direction-vs-heuristic.v1`, hashed into every snapshot): resample 20-session blocks, ≥12 blocks, pass if the mean and the bootstrap lower 2.5% bound of (learned − heuristic) are > 0 and the learned mean reward is > 0. | PARTIAL, on main (#102 via #104): the direction is learned and measured; acting on it needs a pass plus a separate reviewed change. |
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
| 17 | With no edge anywhere, Thompson sampling keeps trading about half the time (the posterior mean sits near 0), so churn and costs continue while it learns. In simulation this is about 675 trades/yr vs 509 before, at zero expected reward. | OPEN: consider a small required margin over 0, or a cost-aware baseline, once real outcomes exist. |
| 18 | A matured decision whose price path stays incomplete is retried on every pass; enough of them could fill the bounded 100-record scan. The shadow scorer rotates these with per-session CHECK records; the paper learner does not. | DONE (#107 on main; migration 0027 applied) |
| 19 | Overlap weighting handles one stock's correlated daily labels; same-day labels across stocks share market moves and are still treated as independent. Related to item 8. | PARTIAL: shadow targets now remove the market move (item 8); the bandit's counterfactual labels are still raw returns and same-day labels are still treated as independent. |
| 20 | No automatic reconciliation between each executed position's realized ledger return and its counterfactual label for the same window (they should match apart from late exits). | DONE (#108 on main) |
| 21 | No power analysis for the direction test: 12 blocks may be too few to detect a realistic improvement, and too many decisions per block are correlated to know in advance. | IN PR (`feat/promotion-test-power`): simulation with the live evaluators (`power.py`, `scripts/power_promotion_tests.py`, `promotion-test-power.md`). Power: at 12 blocks only large improvements are caught reliably (direction ≥ ~0.07 reward units, weights ≥ ~0.09 IC). Both tests now report `detectable_difference_80pct` and `detectable_at_minimum_blocks`. `calibrate` recomputes them from real records. Found the size problem logged as item 30. |
| 22 | The candidate shadow weights (item 7) still have no promotion protocol; item 6 covers direction only. | DONE (#107 on main). Evidence restarts under this branch's new entry-aligned target (item 16), since the protocol names its target. |
| 23 | The residual is relative to SPY only; there's no sector-relative target, and a 63-day beta is a noisy hedge ratio. | OPEN |
| 24 | Shadow snapshots captured before this change are scored under the v2 target with default beta/volatility when missing; their frozen candidate scores used v1 weights, so the first weeks mix weight versions in the weekly view. | OPEN: transitional, and repeats with item 16's target change. Old-target outcomes are excluded from training and views, but early weeks under each new target have thin evidence. |
| 25 | Stacked PRs merge into each other, not main: #100–#103 merged into their base branches and only reached main via #104. | DONE: #104 landed #100–#103 on main. PRs now target main directly. |
| 26 | The technical evaluator's other per-trade metrics (net win rate, mean per opportunity) are reported without uncertainty, and walk-forward folds don't report how often a no-edge setup would act. | OPEN |
| 27 | Brier skill uses the evaluated set's own base rate (in-sample climatology), which slightly favours the reference; a prior-period base rate would be stricter. | OPEN |
| 28 | The cumulative pooled view reads every v2 shadow outcome on each admin load; at ~162 a session that grows ~41,000 rows a year. | OPEN: materialize daily or cap by date once it is slow. |
| 29 | Release coordination for migration 0027: deployed code requires schema 0027 and ingestion fails closed until the migration workflow succeeds; the daily technical workflow's `alembic upgrade head` would also apply it. | DONE: the 0027 workflow succeeded on 2026-10-10 (run 38084929642); CI on main is green. |
| 30 | Both pre-registered tests pass too often with no real improvement: about 5–7% instead of 2.5%. Adjacent blocks are correlated (lag-1 ≈ +0.16) and the percentile bootstrap understates the spread by 22–26%. A Newey–West lag-1 + t(n−1) bound reaches about 3% from 24 blocks; nothing tried is calibrated at 12. | OPEN — owner decision: adopt v2 protocols (Newey–West + t, ≥ 24 blocks), which restarts both tests' evidence and costs about 8–10 points of power. |

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
