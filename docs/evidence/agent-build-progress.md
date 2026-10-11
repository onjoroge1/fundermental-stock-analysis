# Agent build progress

Updated 2026-10-07. The immediate priority is verified operation and prospective
measurement across the existing 54 stocks. Wait for mature observations before
judging candidate weights; continue repairing and verifying the pipeline now.

## Verified release baseline

- PR95: expanded covered-stock monitoring and prospective paper instructions.
- PR96: merged at `02c35cf12dba63d3ad62ec2c7b722881f5e8ca46`; production
  deployment `dpl_Fad75enQRBswUGmGxrJeeD4sfxb1` was READY on that commit.
- Schema `0025_agent_shadow`: migration workflow 37701553106 succeeded.
- PR96 CI 37701553094: both Python 3.11/3.12 suites passed (754 passed,
  2 skipped per matrix job); separate direct-LSTM tests passed.
- Public `/api/v1/data-health`: HEALTHY, 54 current stocks, zero stale at inspection.
  This verifies price freshness, not private account performance or shadow outcomes.
- Owner-only stock/forecast coverage, stage receipts and maturity counts require
  an authenticated admin session; those production totals were not inspected.

## Scheduled operations change awaiting merge

| Stage | Target in America/New_York | Runtime / evidence |
| --- | --- | --- |
| Inputs and canonical forecasts | 17:30 on completed market sessions | GitHub Actions; benchmark result, refresh counts, workflow artifacts, durable receipt |
| Research | Every 10 minutes | Existing queue drains one current-price/current-forecast stock per tick, plus bounded refresh work |
| Paper maintenance | Every 10 minutes, 17:00–22:59 | Holding-limit exits first (each at its own 20-session target close, even if the job runs late), then pending simulated fills, then marks; requires enabled capture and PAPER mode |
| Learning | 18:10, 20:10, 22:10 | Independent paper and shadow scoring, at most 100 candidates each per pass |
| Progress snapshot | Weekdays 08:15 | Current-session coverage, pending target sessions, queue and durable receipts |

UTC cron candidates are filtered against the Eastern clock and the exchange
calendar. GitHub and Vercel schedules can be delayed; configured time is not proof
of execution. Morning reports include the last completed session even after
holidays. Full ingestion stays outside the serverless runtime. Existing price
shards continue as recovery coverage.

A separate ChatGPT weekday morning progress report has been enabled, with a Friday
weekly summary. Public GitHub/deployment/price evidence is accessible; private
account or shadow totals must be explicitly marked unverified when inaccessible.

Each stage has a session/slot key, a PostgreSQL advisory lock and append-only
STARTED/FINISHED receipts. Successful, attention and intentionally skipped runs
replay within a slot; failed runs can retry. A crashed worker leaves an unfinished
receipt and releases its connection lock. Manual ingestion repair is explicit via
workflow_dispatch. No new migration, promotion, live order or trading-mode change.

## Next checks after merge

1. Verify the new production commit and the first real ingestion, paper, learning
   and progress receipts. Confirm forecasts and research reach all 54 names.
2. Investigate FAILED, ATTENTION, unfinished receipts or overdue unscored targets;
   compare refresh diagnostics with queue and manifest coverage.
3. Audit pending paper instructions, fills, risk blocks and cost-inclusive weekly
   account performance against SPY. Follow HIMS through the same portfolio audit.
4. Compare frozen current versus candidate errors, direction accuracy and forecast
   Brier scores only when 5-session targets mature. Ten and twenty sessions mature
   later. Five observations are an initial diagnostic, not sufficient promotion
   evidence. No retrospective reconstruction or automatic weight promotion.
5. Keep adding stock/session observations through the existing universe before
   introducing another strategy or increasing exposure for data collection.

The options-capture authorization issue observed previously remains separate and
unresolved. An unavailable owner endpoint must not be described as a healthy or
failed learning loop without evidence.


## Technical setup evaluator awaiting merge

Added a daily historical walk-forward technical setup evaluator with eight fixed
setups/combinations, per-stock/horizon candidate weights, immutable input vintages,
and separate prospective frozen predictions. The owner panel reports historical
mix/equal/trend/buy-and-hold comparisons and actual job receipts. Read
[the technical contract](technical-setup-shadow.md) before interpreting performance.
Migration 0026 and the first successful 54-stock daily receipt require verification
after merge. Existing five-session PR96 observations continue independently.

## Learning-loop review (2026-10-10)

Open gaps in reward, bandit, shadow metrics and technical utility are tracked with
status in [agent-learning-review-2026-10-10.md](agent-learning-review-2026-10-10.md).
