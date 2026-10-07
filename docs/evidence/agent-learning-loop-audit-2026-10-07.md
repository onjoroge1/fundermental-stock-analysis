# Agent learning-loop audit — 2026-10-07

## Intended purpose
Monitor all 54 currently covered stocks, make gated prospective paper decisions,
measure account performance weekly, and learn from executed positions over time.
More symbols provide more observations, not independent evidence or guaranteed returns.
Live orders and additional strategies are outside this release.

## Findings and repairs

| Finding | Repair |
| --- | --- |
| Five-stock restrictions throughout storage, contracts, UI and scheduler | Versioned 54-stock policy; preserve old pilot history; migration 0024 removes ticker restrictions |
| Hour-modulo scheduler repeats pilots and cannot cover 54 daily | Fair completed-session queue, serialized scheduler, ten-minute bounded cron; approximately nine hours for 54 eligible names |
| Rewards attributed to hypothetical actions starting before decision; blocked and hold actions could learn | Prospective next-close fills using journal recording time; exclude blocked, legacy, shadow, hold and close decisions; reward only actual closed positions or separately labeled flat abstentions |
| Pending rewards can starve realized newer trades | Exclusion records before scan limits and priority for realized positions |
| Equal-score cold start repeatedly selects one arm | Least-attempted eligible arm tie breaking; attempts remain distinct from observed rewards |
| Concurrent reward writes lose state or double count | Per-ticker transaction locks and idempotent decision reward records |
| Option paths lack reliable valuation | Exclude option actions from stock learning until valuation coverage exists |
| Tail refresh blends adjusted-price histories and leaves manifests stale | Replace complete provider histories with matching manifests; reject partial bulk fallback; require every adjusted-price path observation |
| Aggregate portfolio can look healthy with missing prices | Incomplete aggregate is withheld; split-adjusted basis revalues open positions consistently |
| No actual account weekly audit | Five-session report from isolated agent paper ledger, position contributions, costs, SPY comparison and missing-data flags |

## Paper execution and learning limits

Paper account starts at $100,000; 50% maximum gross exposure; target per symbol is
50% / 54 (about 0.926%), subject to available capacity and a $500 minimum.
The existing 10% hard position ceiling remains. Trading remains equity-only simulated
fills with 10 bps per side. Blocked or stale evidence cannot force a trade.
Instructions must precede their simulated exchange close. Missed execution sessions
expire rather than backfill. New positions have a precommitted 20-session maximum
holding period; earlier signal exits are learned at actual ledger cash P&L.
Legacy positions are not forcibly closed and old hypothetical rewards do not warm
start the new bandit. Historical journal records remain readable.

Short borrow availability, borrow fees, liquidity and real slippage are not modeled.
Weekly account evaluation does not shorten the learning horizon. Correlated stocks
and overlapping horizons reduce effective sample size; do not add strategies or
increase exposure merely to collect observations.

## Release and verification

The main-branch release workflow migrates 0024, refreshes complete stock and benchmark
histories, then sets PAPER mode while preserving the owner capture pause. Research
and forecast freshness gates still apply; no guarantee that every stock trades.
Deployment may become ready before migration finishes; ingestion fails closed until
0024 is verified. Review release workflow success, deployed commit, market manifests,
54-stock queue coverage, pending-to-filled intents and operator weekly audit after merge.

Local suite passed before release preparation; PostgreSQL execution, migration,
54-position risk-cap and HIMS end-to-end reward tests run in GitHub CI. Production
activation and owner-only ledger inspection remain unverified until release.

PR94 production deployment matched merge commit 0e356ef. Public observations showed
prices through October 7 but manifests through October 6, and withheld research
cycles; public legacy paper positions are a different ledger from agent positions.
Private HIMS position attribution cannot be inferred from that public ledger.
The options-capture workflow's authentication failure remains a separate operational
issue; this release prevents that unvalued path from contaminating stock learning.
