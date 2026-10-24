# Paper-ledger evaluation: pre-registered protocol, 2026-10-20

Tracker item 52. This fixes how the agent's paper ledger will be judged, before any paper position can exist.
Until its one final record exists, paper P&L is not evidence of anything.

## Identity

| | |
|---|---|
| Forward id | `agent-paper-ledger.v1:evaluation.v1` |
| Module | `stock_machine/agent_intelligence/paper_ledger_forward.py` |
| Protocol sha256 | `3e953d56143516baf3327a306c09ae4cb7ca6ce5df35972c7a84492d5c3f128c` |
| Behaviour pin (evaluation code on a fixed synthetic ledger) | `c8336b1c998eadb31017ad3c840caebccfb68f0a69c6c34df68d4e98141e232d` |
| Protocol record | `evidence_31e325659d608fb4cce9c6ce935982ae72125323de1c0bfb165f641601758bab` (kind `FV_FORWARD_PROTOCOL_V1`) |
| Registration deadline | before the 2026-11-10 open (14:30 UTC); later is `REGISTERED_LATE`, no verdict |
| Runs | `.github/workflows/paper-ledger-forward.yml`, 04:30 UTC Tue–Sat, with fallbacks at 06:55 and 08:45 UTC Mon–Fri that copy the same night (idempotent); main only. The first run registers. |

The first prospective learning labels mature on 2026-11-10. The confidence-gated bandit cannot act before
labels exist, so no in-scope position can exist before registration.

## What is judged

**What is in scope:**
- The Agent Trading v1 ledger only (`agent_trading.py`), which is the ledger the RESEARCH/PAPER switch drives.
- Not the legacy `/api/paper` book, and not `forward_paper_v2`.

**What is scored:** the stock-specific outcome of the positions the agent opened, never raw account P&L.
- Each position is scored by its own decision's append-only label (`AGENT_REWARD_V3`) for its side.
- The score is `(residual_return_pct − costs_pct) / risk_scale_pct`, which is reward v5.
- The residual is taken against frozen beta × SPY over the committed 20-session window, net of 20 bps,
  divided by ex-ante volatility.
- Abstaining scores 0. The question is whether the paper trades beat not trading.

**Intention to treat:** whatever PAPER executes in the window counts. No position is ever removed because of a
version change, a direction-policy switch, a mode toggle or a capture pause. A policy value that differs from
the registered one is recorded once as `POLICY_VERSION_CHANGED`.

## Scope

**In scope:** every ledger position that meets all of these:
- its intent records the execution contract and the `holding_commitment` field (the committed-hold code);
- it records a selector other than a legacy one (`source-report-classification`, `fundamental-score-paper-v1`);
- it comes from a decision recorded after the protocol;
- it was entered in the evaluation months.

**Listed but never scored:** `PRE_REGISTRATION`, `OTHER_SELECTOR`, `LEGACY_CONTRACT` and `OUTSIDE_WINDOW`
positions. A post-registration position excluded by selector (legacy or none) or by contract is also recorded
as `SCOPE_EXCLUDED_AFTER_REGISTRATION`.

## Integrity: the ledger tables can be edited, so they are copied

- Each in-scope position is copied append-only on the night it is first seen and on the night it is first seen
  closed. Once copied, it stays in scope.
- Records written when the ledger looks wrong:

| Record | Written when |
|---|---|
| `LEDGER_MUTATED` | A later difference in any copied field, or a deleted row. |
| `SNAPSHOT_LATE` | A position's first copy is made more than one session after its entry. |
| `NIGHT_MISSED` | A session from the window's first session to the end of the extended months, after registration, has no nightly copy. |

- All of these are listed for a week, counted for good (`recorded_checks`), and named in the final record.

## Scoring and reconciliation

**When a position is scored:** once its label exists and either its exit is copied or 10 sessions have passed
since its due session. The second case is recorded as `UNSETTLED`, so a paused ledger never drops a labelled
position.

**Reconciliation is recorded, never used to drop a position.** The ledger is compared with the label
(`reconcile.classify`, tolerance 0.05 pp), with one of these results: `MATCHED`, `LATE_EXIT`, `EARLY_EXIT`,
`RETURN_MISMATCH` or `INVALID_INPUTS`.

**Excluded and listed:**

| Exclusion | Meaning |
|---|---|
| `EXCLUDED_MISSING_LABEL` | No label 10 sessions after the due session. |
| `ENTRY_MISMATCH` | The label's entry session differs from the position's. |
| `EXCLUDED_LABEL_INCOMPLETE` | The label has no finite residual or cost, or no positive risk scale. |

- Each exclusion is listed for a week from the run that writes it (`sticky_attention`), so a fallback run for
  the same night still reports it.

## Statistic and verdict

**Unit:** calendar month of the entry session. The monthly value is the sum of that month's scores divided by
54 (the registered sizing, 0.50 / 54 of equity per position). Months without positions score 0.

**Statistic:** the mean of the monthly values over every month of the window, with a Newey-West (lag 1)
standard error and a t(n−1) two-sided 95% interval.

**Window:** positions entered from 2026-11 to 2028-10.
- If the window ends with fewer than 60 scored positions or fewer than 12 months containing one, it is
  extended once, to 2029-10. That decision uses the counts alone; no estimate is computed.
- If the extended window is still short, the result is `INSUFFICIENT_SAMPLE`.

**No verdict in these cases:**

| Status | Condition |
|---|---|
| `MODE_INTERRUPTED` | PAPER on and capture unpaused for the whole paper stage (17:00–23:00 ET) on under 0.9 of the window's sessions, by the `TRADING_MODE_CHANGED` and `CAPTURE_PAUSE_CHANGED` audit trails. |
| `DATA_COMPROMISED` | Excluded positions plus late-copied positions above 0.1 of in-scope positions. |
| `REGISTERED_LATE` | The protocol was recorded at or after the deadline. |

**Verdicts:**

| Verdict | Condition |
|---|---|
| `PASS_REQUIRES_INDEPENDENT_REVIEW` | The mean and its lower bound are both above 0, and the lower bound stays above 0 when every excluded position is scored at the lower of the lowest observed score and −3.0. |
| `NEGATIVE` | The upper bound is below 0: worse than not trading. |
| `NOT_SUPPORTED` | Otherwise. |

Every evaluated result reports its own 80%-power detectable difference.

## Power: read this before reading any verdict

**Detectable edge:** for independent positions with a reward standard deviation of 1, the smallest edge
detectable with 80% power is about 3.0 / √N per position:

| Scored positions | Detectable edge (reward units per position) |
|---|---|
| 60 (the minimum) | 0.39 |
| 225 | 0.20 |
| 850 (about the 20-session capacity of 35 stocks over 24 months) | 0.10 |

**Adjustments:**
- Same-month correlation (about 0.05 in the 2016–2026 direction replay) raises those figures about 1.2× at 10
  positions a month and 1.6× at 35.
- The false-pass rate is about 3–4%, against a nominal 2.5%.

**Likely outcome:** earlier tests put realistic edges near 0.00–0.04, so `NOT_SUPPORTED` (or
`INSUFFICIENT_SAMPLE`, if the gate rarely trades) is the likely verdict. **That is not evidence of no edge.**

**Size of the sample:** the trade count depends on how many stocks are paper-eligible. Only stocks with
verified financials can trade, so keeping the reviews current (item 53) matters.

## What it is not

- **Not evidence before the final record.** Ledger P&L, open marks and the weekly account-vs-SPY audit are not
  evidence. Before the final record only coverage is published (learning-status `forward_tests.paper_ledger`,
  the watchdog's `paper_ledger_forward` check, the admin forward-tracker card), with no mean, interval or
  "on track" signal.
- **Not independent of the direction test.** Its positions are a subset of the same labels.
- **Information only:**
  - nothing here writes an `agent_*` table, changes the mode, sizing, gate, bandit or eligibility, or touches a
    broker;
  - a pass authorizes review only (`promotion: NOT_AUTHORIZED`).

## Owner steps and how to check registration

1. **Merge.** The next 04:30 UTC run on main registers the protocol (or dispatch "Paper Ledger Evaluation" by
   hand). The last automatic chance is the morning of 2026-11-10.
2. **Check the registration.** `GET /api/v1/agent/learning-status` should show
   `forward_tests.paper_ledger.protocol_registered: true` and `registered_late: false`.
3. **Keep PAPER on and research capture unpaused.** Otherwise sessions count against the 0.9 mode rule.

## Reproduce

- `pytest tests/test_paper_ledger_forward.py tests/test_paper_ledger_forward_postgres.py` (the second needs
  `TEST_DATABASE_URL`).
- `behavior_digest()` runs on a settled past window (2023-11..2026-04) with synthetic tickers, so neither a
  universe change nor a future exchange-calendar release can move the pin.
