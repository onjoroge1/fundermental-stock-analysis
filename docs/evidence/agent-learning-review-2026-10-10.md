# Agent learning review — tracker (opened 2026-10-10)

This tracks gaps found in a review of the agent-learning loop at `b448617` (PR #98).
Each item was reproduced by running the repo's own functions on synthetic inputs.
Production data was not inspected. When an item ships, update its status and link the PR.

Status: **DONE** (merged), **IN PR** (on a branch), **OPEN**.

## Critical — the loop cannot learn as designed

| # | Gap | Evidence | Fix direction | Status |
|---|---|---|---|---|
| 1 | Reward v3 makes every trade lose to abstaining. A 0.60×max-drawdown penalty dominates, and return (% of notional) is mixed with capital/turnover penalties (% of equity). | A +2.13pp mean gross edge scored a mean reward of −3.55; NO_TRADE always scores 0. | Reward v4: net return ÷ ex-ante holding-period volatility. Drawdown, capital and turnover become diagnostics. Arms trained under v3 cold-start. | IN PR #99 (CI green) |
| 2 | Bandit learns too slowly to use: diagonal LinUCB without an intercept, NO_TRADE learned as an arm despite a fixed 0 reward, a collinear `bias` feature, and per-ticker state. *Correction:* the original "60% lock-out" figure came from an immediate-reward probe; with realistic 20-session reward delays the current bandit keeps trading but learns slowly. | Realistic simulation (54 tickers × 252 sessions, 20-session holds, v4-scale rewards): old bandit captured +40 expected reward/yr; a pooled Thompson sampler captured +98. Per-ticker Thompson was worse than the old bandit, so pooling is what pays off. | Pooled linear Thompson sampling with full precision matrix, intercept, `bias` dropped, NO_TRADE as a fixed zero baseline, seeded by decision key. | IN PR (`fix/bandit-lockin`, stacked on #99) |
| 3 | Treated as a bandit even though every abstention has an observable what-if outcome; abstentions train only the constant-zero arm. | By design in `outcomes.py`. | Learn from prospective counterfactual outcomes for every decision; keep paper fills for execution validation. | OPEN |
| 4 | 54 independent per-ticker learners, ≤ ~12 non-overlapping trades/yr each. | Bandit state is keyed by ticker. | One pooled model with ticker/sector shrinkage (pattern exists in `shadow.candidate_weights`). | PARTIAL: one pooled model per action ships with item 2; ticker/sector random effects still OPEN (the simulation shows pooling still wins with ticker offsets of sd 0.2, but per-ticker differences are ignored). |
| 5 | The "20-session hold" is not real. Each daily re-decision can emit CLOSE, so a trade's reward depends on later unrelated choices; trade and abstention horizons differ. | `agent_trading.deterministic_action` returns CLOSE when the desired side is FLAT. | Fixed-horizon commitments with risk-only early exits, or score each decision on its own fixed horizon. | OPEN |

## High

| # | Gap | Evidence | Fix direction | Status |
|---|---|---|---|---|
| 6 | Direction is never learned (hand-set weights 0.50/0.25/0.15/0.10, ±0.25 threshold); no promotion rule for shadow weights. | `state.assemble` | Pre-registered promotion test reusing the `prospective_experiment.py` bootstrap. | OPEN |
| 7 | Shadow weight metric (squared error vs ±1) penalizes conviction. | A 56%-hit, magnitude-0.6 signal got 0.459 weight vs 0.541 for zero-skill ±0.1 noise. | Rank IC or calibrated log-loss, or a stacked ridge/logistic model. | OPEN |
| 8 | Shadow targets use raw returns, which mostly measure market beta over 5 sessions. | `shadow.evaluate` | Score stock-specific components against SPY/sector-excess returns. | OPEN |
| 9 | Technical-setup utility `mean − 0.5·std` needs per-trade Sharpe > 0.5, so it nearly always abstains. | Annual Sharpe 1.0: utility −1.97% (5d), −2.35% (20d). | Penalize standard error (`k·std/√n`) or rank by Sharpe with a cost floor. | OPEN |
| 10 | Headline classifier false positives trip `HIGH_MATERIALITY_NEGATIVE_HEADLINE_CONTEXT`. | "issued" → LITIGATION, "regulatory approval" → REGULATORY_ACTION, "no warning signs" → GUIDANCE_CUT. | Word-boundary regexes, a negation guard, approvals excluded from regulatory action. | IN PR #99 (CI green) |

## Medium

| # | Gap | Status |
|---|---|---|
| 11 | No pooled statistics or CIs: the weekly shadow view is ~162 ticker×horizon cells of ~5 observations; no Brier skill vs base rate. | OPEN |
| 12 | The shadow training window (5,000 latest outcomes at ~162/day) covers only ~31 sessions. | OPEN |
| 13 | Missing context features are coerced to 0 (looks neutral); clipped annual vol acts as a hidden intercept. | OPEN |
| 14 | `settle_holding_limits` exits at the latest close when late (holds can exceed 20 sessions) and caps at 6 exits per pass. | OPEN |
| 15 | Per-decision JSONB trial-count scan and one DB connection per scored record grow with history. | PARTIAL: trial-count scan removed with item 2; per-record connections in `score_matured` still OPEN. |
| 16 | Shadow scores from the origin close, while paper trades fill at the next close. | OPEN |
| 17 | With no edge anywhere, Thompson sampling keeps trading about half the time (the posterior mean sits near 0), so churn and costs continue while it learns. In simulation this is about 675 trades/yr vs 509 before, at zero expected reward. | OPEN: consider a small required margin over 0, or a cost-aware baseline, once real outcomes exist. |

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
