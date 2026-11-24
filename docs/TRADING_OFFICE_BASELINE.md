# Trading Office: baseline inventory

Taken 2026-11-13 from `main` at `4f011b5` (the commit the build brief inspected; `main` has not moved).
This is step 1 of the brief's section 15. Nothing in this document changes production. Nothing here was
merged, deployed, enabled or configured.

How to read the "verified" column: **code** = read from the repository; **GitHub** = read from the GitHub
API or a workflow log (names and run times only); **not verified** = I could not check it without reading a
secret or logging in to Vercel, so the owner needs to confirm it.

## 1. Findings the owner should see first

1. **GitHub starts scheduled jobs 4 to 8 hours late on this repository.** On 2026-11-12/13 the health
   watchdog (planned 13:15 UTC) started 20:29 UTC; the daily refresh (planned 21:30 / 22:30 / 01:30 UTC)
   started 01:58, 02:45 and 08:34 UTC; the technical-setup job (planned 03:30 UTC) started 11:23 UTC.
   The brief asks for a GitHub controller "every 10 minutes" and for the paper stage (17:00–22:59 New York
   time) to move to GitHub. With these delays a GitHub trigger would arrive after the paper window has
   closed on most days. Section 6 therefore proposes keeping Vercel as the clock for the three time-windowed
   stages and using GitHub only for work that can be late.
2. **Three secret names also exist as plain Variables** in the `Production` environment: `DATABASE_URL`,
   `FMP_API_KEY`, `STOCK_MACHINE_ADMIN_TOKEN` (created 2026-09-05 and 2026-09-17). Variables are not
   encrypted: anyone who can view the repository settings can read them, and they are not masked in logs.
   I did not read their values. No workflow uses `vars.` for these names, so deleting the three Variables
   should change nothing. If they hold real values, rotate those credentials. Owner action.
3. **`option-surface-capture.yml` has failed its last 10 runs** with HTTP 401 from the app. It sends
   `STOCK_MACHINE_ADMIN_TOKEN`; the GitHub value and the Vercel value most likely differ. Open health issue
   [onjoroge1/fundermental-stock-analysis#156](https://github.com/onjoroge1/fundermental-stock-analysis/issues/156)
   (status WARNING since 2026-10-20) already reports it.
4. **`main` is not protected** and no check is required before a merge. CI does run on every pull request.
5. **No LLM provider key exists anywhere in GitHub** (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` both missing).
   The analyst and reviewer roles cannot make a real call until the owner chooses a provider and sets one.
6. **Production is already in PAPER mode** (set 2026-10-07) with research capture on. The existing paper
   ledger holds 0 positions (watchdog, 2026-11-13).

## 2. Every current schedule

Times are as written in the files. Vercel cron runs in UTC. "ET" is New York time.

### 2.1 Vercel (`vercel.json`)

| # | Path | Schedule (UTC) | What it does | Durable receipt | Verified |
|---|---|---|---|---|---|
| V1 | `/api/admin/cron` | every 10 min | `automation.cron_tick`: schedules due research work, runs up to 2 queued `orchestration_jobs` | `orchestration_jobs` rows | code |
| V2 | `/api/admin/prices/cron/0/18` | 22:00 Mon–Fri | price refresh, stocks 0–17, only if stale | `dataset_snapshots` | code |
| V3 | `/api/admin/prices/cron/18/18` | 22:20 Mon–Fri | price refresh, stocks 18–35 | same | code |
| V4 | `/api/admin/prices/cron/36/18` | 22:40 Mon–Fri | price refresh, stocks 36–53 | same | code |
| V5 | `/api/admin/prices/cron/0/25` | 23:00 Mon–Fri | recovery pass, first 25 | same | code |
| V6 | `/api/admin/agent/cron/paper` | minutes 2,12,…,52 every hour | `scheduled_operations.run("paper")`; acts only 17:00–22:59 ET on a completed session, in PAPER mode, capture on | `operator_audit` event `AGENT_STAGE_FINISHED`, key `agent-stage:paper:<session>:<HH:M0>` | code |
| V7 | `/api/admin/agent/cron/learning` | minute 10 of hours 22,23,0,1,2,3 | `run("learning")`; acts at 18:10, 20:10, 22:10 ET | key `agent-stage:learning:<session>:<HH>:10` | code |
| V8 | `/api/admin/agent/cron/progress` | 12:15 and 13:15 Mon–Fri | `run("progress")`; acts 08:15–08:59 ET | key `agent-stage:progress:<session>:08:15` | code |

All eight require `Authorization: Bearer` with `CRON_SECRET` or `STOCK_MACHINE_ADMIN_TOKEN` (24+ characters).
Every stage takes a Postgres advisory lock on its key and replays the stored receipt if the key already
finished, so a second caller with the same key cannot write twice. Last successful run of V1–V8: **not
verified** (needs Vercel logs or a database read).

### 2.2 GitHub Actions (all use environment `Production`)

| # | Workflow | Cron (UTC) | Command | Last scheduled success (start time) | Verified |
|---|---|---|---|---|---|
| G1 | `daily-data-refresh.yml` | 21:30, 22:30 Mon–Fri; 01:30 Tue–Sat (repair) | universe sync, prices, `run_agent_ingestion`, `run_research_operations --experiment` | 2026-11-13 08:34 (run 43394333621) | GitHub |
| G2 | `company-events-refresh.yml` | 10:30, 20:30 Mon–Fri | `refresh_company_events.py` | 2026-11-13 17:54 (43477486872) | GitHub |
| G3 | `health-watchdog.yml` | 13:15 Tue–Sat | `health_watchdog.py` | 2026-11-13 20:29 (43502083472) | GitHub |
| G4 | `fair-value-forward.yml` | 04:15 Tue–Sat; 06:45, 07:45, 08:30, 10:45, 11:45, 12:30 Mon–Fri | `run_fair_value_forward` | 2026-11-13 19:55 (43496748845) | GitHub |
| G5 | `strategy-forward.yml` | 04:20 Tue–Sat; 06:50, 07:50, 08:35, 10:50, 11:50, 12:35, 13:15 Mon–Fri | `run_strategy_forward` | 2026-11-13 20:30 (43502100846) | GitHub |
| G6 | `paper-ledger-forward.yml` | 04:30 Tue–Sat; 06:55, 08:45 Mon–Fri | `run_paper_ledger_forward` | 2026-11-13 16:44 (43465733711) | GitHub |
| G7 | `forward-paper-v2-mark.yml` | 23:30 Mon–Fri | price refresh, `mark_forward_paper_v2.py` | 2026-11-13 03:28 (43359219283) | GitHub |
| G8 | `macro-refresh.yml` | 23:30 Mon–Fri | `refresh_macro.py` | 2026-11-13 03:30 (43359389622) | GitHub |
| G9 | `option-surface-capture.yml` | 21:30 Mon–Fri | `capture_option_surfaces_api.py` (HTTP to the app) | **none; last 10 failed (401)** | GitHub |
| G10 | `technical-setup-shadow.yml` | 03:30 Tue–Sat | `run_technical_setups` | 2026-11-13 11:23 (43416693366) | GitHub |
| G11 | `alpha-shadow.yml` | 12:15 Sat | `run_shadow_alpha.py` | 2026-11-07 17:44 (42397787774) | GitHub |
| G12 | `p1-shadow.yml` | 12:45 Sat | alpha scoring, `run_p1_completion.py` | 2026-11-07 17:51 (42398329319) | GitHub |
| G13 | `strategy-lab-v2.yml` | 13:15 Sat | `run_strategy_lab_v2.py 15` | 2026-11-07 18:01 (42399143885) | GitHub |

The other 22 workflows have no schedule: `ci.yml` (push and pull request), migration and release workflows
that run on a push to `main` touching their own files, and manual repair jobs.

G1's ingestion gate compares the triggering cron string (`"30 21 * * 1-5"` in summer, `"30 22 * * 1-5"` in
winter, `"30 1 * * 2-6"` for repair) in `scheduled_operations.schedule`. Changing G1's YAML alone would
break it, as the brief says.

### 2.3 Local (the owner's Mac, research repository)

`~/code/fsa-research/research8/daily_runner/daily.py morning|evening`, optionally scheduled by launchd at
08:40 and 16:40 ET. It runs the frozen X2 shadow commands, the earnings feed, the pre-open sheet and the
OBS-QM-3 recorder against IBKR data. It is not part of the app and nothing in this plan changes it.

## 3. Owner and replacement plan for each schedule

"Owner" means the system that starts the job. The office adds work; it does not need to take these over.

| Schedule | Owner today | Plan | Reason |
|---|---|---|---|
| V1 research tick | Vercel | **Keep on Vercel.** The office controller pulse is added to this same 10-minute tick, behind `OFFICE_ENABLED` | Only timely clock available; already bounded to 2 jobs |
| V2–V5 price recovery | Vercel | **Keep unchanged** | Brief: do not remove provider-recovery jobs without a validated replacement |
| V6 paper stage | Vercel | **Keep on Vercel.** Add the experimental-book maintenance as a second step of the same stage, behind `OFFICE_PAPER_ENABLED` | A GitHub trigger 4–8 h late would miss the 17:00–22:59 ET window |
| V7 learning stage | Vercel | **Keep on Vercel.** Office outcome scoring runs after it under its own key | Same |
| V8 progress stage | Vercel | **Keep on Vercel.** The morning briefing is queued when this receipt is written | Same |
| G1 ingestion | GitHub | **Keep unchanged**, dual UTC triggers stay | Works; gate depends on the cron strings |
| G2 events | GitHub | **Keep.** The office scout reads the `company_event_snapshots` rows it writes; no second poller | Avoids duplicate provider calls |
| G3 watchdog | GitHub | **Keep, extend** with office checks (overdue tasks, budget, scheduler owner) | |
| G4–G8, G10–G13 | GitHub | **Keep unchanged** | Not office work; several are frozen forward tests |
| G9 option capture | GitHub | **Keep; fix separately** (token mismatch) | Unrelated to the office |
| New: office worker | none | **Add on GitHub** (`office-worker.yml`): drains queued LLM and QA tasks. Late starts are acceptable because tasks are durable | Runner has the time budget for model calls |
| New: weekly QA / admission review | none | **Add on GitHub**, Friday evening target; due-work record decides, not the cron | Not time-critical |

If the owner still wants a stage moved from Vercel to GitHub, the brief's six-step procedure applies to that
stage alone, and step 3 must first show from real runs that GitHub starts it inside its window. Today's
measurements say it would not.

## 4. Paper mode and books today

| Book | Where | Mode / state | Capital and limits | Fills | Verified |
|---|---|---|---|---|---|
| Agent Trading v1 ledger | `agent_trading.py`; tables `agent_trading_settings`, `agent_trade_intents`, `agent_paper_positions`, `agent_paper_fills`, `agent_paper_marks` | **PAPER** since 2026-10-07; 0 positions seen | $100,000; 10% max position; 50% max gross; up to 54 positions (so about 0.93% each); 20-session hold; 10 bps a side; $500 minimum | Simulated at the latest completed-session adjusted close | code + GitHub log |
| Its registered evaluation | `paper_ledger_forward.py`, id `agent-paper-ledger.v1:evaluation.v1` | Registered; PENDING; window 2026-11 to 2028-10 | 60 positions and 12 active months needed | none of its own | code + GitHub log |
| Legacy classification book | `paper.py`; `sm_paper_positions`, `sm_paper_nav` | No costs; long ATTRACTIVE / short UNATTRACTIVE | none | adjusted close | code only |
| Forward Paper v2 cohorts | `forward_paper_v2.py` | Strategy Lab v2 reports **no eligible policy** (run 42399143885), so no cohort can be frozen | n/a | marks only | code + GitHub log |
| Option paper | `agent_intelligence/option_paper.py` | evidence records only | n/a | modelled | code only |

The office's experimental book will be a new, separate book. It must not write to any table above.

Guards that already exist and must keep passing: `scheduled_operations` receipts carry
`broker_submission: False`; `instructions.paper_instruction` accepts only source `agent-intelligence.v2`;
`tests` include a check that the research app cannot place an order or reach the execution desk (#172).

## 5. Configured universe

`stock_machine/sectors.py` `UNIVERSE`: **54 stocks** (code, counted today):
AAL AAPL ABNB ADBE ADI AMAT AMD AMZN AVGO BKNG CMCSA CMG COST CRM CSCO DAL DELL DIS F GM GOOGL HD HIMS HPQ
IBM INTC INTU KLAC LOW LRCX LUV MCD META MSFT MU NFLX NKE NOW NVDA ORCL PLTR QCOM RIVN SBUX SOFI T TGT TMUS
TSLA TXN UAL UBER VZ WMT. `agents/contracts.py` freezes it as policy `research-universe-v2`; a change needs
a new policy version. Watchdog 2026-11-13: all 54 current through 2026-11-12; 54 decided, 34 learnable, 12
paper-eligible, 22 with fundamentals withheld.

The list is hand-picked with hindsight. Every strategy result on it carries that limitation.

## 6. Schema inventory

Migrations `0001`–`0028` (Alembic). Tables they create:

`agent_lab_decisions`, `agent_lab_events`, `agent_lab_evidence`, `agent_lab_policies`,
`agent_lab_report_outbox`, `alpha_probability_outcomes`, `alpha_shadow_runs`, `analysis_reports`,
`backtest_observations`, `companies`, `company_event_coverage`, `company_event_snapshots`,
`consensus_archive_imports`, `consensus_snapshots`, `consensus_vintages`, `corporate_actions`,
`data_quality_events`, `dataset_snapshots`, `earnings_surprise_vintages`, `earnings_surprises`, `filings`,
`financial_periods`, `forecast_outcomes`, `forward_paper_v2_cohorts`, `forward_paper_v2_marks`,
`insider_transactions`, `macro_series`, `macro_series_vintages`, `metric_snapshots`, `operator_audit`,
`operator_auth_limits`, `operator_controls`, `operator_pilot_items`, `operator_pilot_runs`,
`operator_sessions`, `operator_users`, `option_surface_snapshots`, `orchestration_jobs`,
`p1_research_runs`, `portfolio_proposals`, `prediction_forecasts`, `prices_daily`,
`research_evidence_records`, `shares_outstanding`, `sm_backtest_runs`, `sm_invalidation_events`,
`stock_research_index`, `strategy_lab_v2_runs`, `trade_expression_proposals` (49 tables).

Tables created at run time by code, outside Alembic: the five `agent_*` paper tables (`agent_trading.py`),
`sm_paper_positions`, `sm_paper_nav` (`paper.py`). Some migrated tables are also created defensively in code
(`orchestration_jobs`, `stock_research_index`, `sm_invalidation_events`, `alpha_shadow_runs`).

What the office can reuse, and what it cannot:

| Existing piece | Reuse | Limit |
|---|---|---|
| `operator_audit` | Admission, pause, resume, retire and scheduler-owner changes as audit events | Append-only log; not a queue |
| `research_evidence_records` (kind + request key, append-only) | Memos, reviews, QA scorecards and briefings as new kinds | No state machine; no leases |
| `orchestration_jobs` | Pattern only | No dependencies, due time, lease expiry or budget link; used by V1, so not safe to overload |
| `operator_controls` | Pattern for `OFFICE_*` pause switches | |
| `company_event_snapshots`, `filings` | Scout input | Already deduplicated by their own keys |
| `market_calendar.py` | Holidays, early closes, session arithmetic | |
| `agent_paper_*` tables | **Not reused.** One position per ticker, no strategy attribution, entry at next close only | The brief requires a separate book with per-strategy attribution |

## 7. Secret and Variable names (no values were read)

GitHub, environment `Production` (the repository level has none):

| Name | Kind | Status | Used by |
|---|---|---|---|
| `DATABASE_URL` | Secret | PRESENT (2026-09-05) | nearly every workflow |
| `FMP_API_KEY` | Secret | PRESENT (2026-09-05) | events, estimates |
| `STOCK_MACHINE_ADMIN_TOKEN` | Secret | PRESENT (2026-09-17); probably does not match Vercel (finding 3) | option capture, releases |
| `VERCEL_TOKEN` | Secret | **MISSING**, referenced by two release workflows | old releases 0019, 0020 |
| `SEC_USER_AGENT` | Variable | **MISSING**; code falls back to `StockMachine/1.0 public financial statement research` | G1, G5, universe sync |
| `STOCK_MACHINE_API_BASE_URL` | Variable | MISSING; workflows fall back to the public app URL | G3, G9 |
| `P1_OPTION_TICKERS`, `P1_OPTION_MIN_SUCCESSES` | Variable | MISSING; defaults in YAML | G9 |
| `DATABASE_URL`, `FMP_API_KEY`, `STOCK_MACHINE_ADMIN_TOKEN` | **Variable** | PRESENT and should not be (finding 2) | nothing |
| `ANTHROPIC_API_KEY` | Secret | MISSING | needed if Anthropic is chosen |
| `OPENAI_API_KEY` | Secret | MISSING | needed if OpenAI is chosen |
| `MASSIVE_API_KEY` / `MASSIVE_API` | Secret | MISSING in GitHub | only if a GitHub worker should fetch news |
| `OFFICE_DATABASE_URL`, `DATABASE_MIGRATION_URL`, `OFFICE_API_TOKEN` | Secret | MISSING; all optional | see plan |
| `OFFICE_*` settings | Variable | MISSING; checked-in defaults are disabled | see plan |

Environment `Production` has no protection rules and no branch restriction. A `Preview` environment exists
and is empty.

Vercel (names the code reads; status **not verified**, the Vercel CLI is not linked on this machine and I
did not log in): `DATABASE_URL`, `CRON_SECRET`, `STOCK_MACHINE_ADMIN_TOKEN`, `ADMIN_PASSWORD`,
`SEC_USER_AGENT`, `FMP_API_KEY`, `MASSIVE_API_KEY` or `MASSIVE_API`, `ALPHAVANTAGE_API_KEY`, `PRICE_SOURCE`,
`IBKR_BRIDGE_BASE_URL`, `IBKR_BRIDGE_TOKEN`. The crons work, so `DATABASE_URL` and one of the two cron
tokens must be present there.

Local development: the main checkout has an uncommitted change to `.env.example` and two untracked files
under `docs/evidence/`. They belong to other work and are untouched.

## 8. Other state

- Open pull requests: #131 (flaky shadow test, 2026-10-18), #86 (I05 QuantStats, 2026-09-22), #82 (I01 v2
  correctness, 2026-09-22). None is office work. #82 touches the fail-closed handoff the office must
  preserve, so office PRs will not edit `instructions.py`.
- CI: `ci.yml` runs pytest on Python 3.11 and 3.12 against a disposable Postgres 16 service
  (`TEST_DATABASE_URL`), plus an offline Alembic SQL render. Last run on `main`: success, 2026-10-30.
- HTTP MCP (`mcp_server/http.py`): 11 read-only tools, mounted at `/mcp`. It cannot start agents or write.
- The research repository `~/code/fsa-research` is **not a git repository**. Its versions are pinned by
  sha256 lists (`FREEZE.txt`, `FREEZE.json`). On 2026-11-13 the seven frozen X2 files I checked still match
  their recorded hashes.

## 9. Exact migration map

"Migration" here means who owns each responsibility after the office is built. No owner changes without
the owner's go-ahead for that single step.

| Responsibility | Today | After office build | Switch name | Shared key (prevents double writes) | Rollback |
|---|---|---|---|---|---|
| Research tick and job drain | V1 | V1 (unchanged) | none | `orchestration_jobs` | n/a |
| Price recovery | V2–V5 | V2–V5 (unchanged) | none | `dataset_snapshots` | n/a |
| Legacy paper stage | V6 | V6 (unchanged) | `scheduler_owner.paper = vercel` | `agent-stage:paper:…` | n/a |
| Learning stage | V7 | V7 (unchanged) | `scheduler_owner.learning = vercel` | `agent-stage:learning:…` | n/a |
| Progress stage | V8 | V8 (unchanged) | `scheduler_owner.progress = vercel` | `agent-stage:progress:…` | n/a |
| Ingestion and repair | G1 | G1 (unchanged) | `scheduler_owner.ingestion = github` | `agent-stage:ingestion:…` | n/a |
| Office controller pulse (plan due tasks, recover leases) | none | V1 calls it, bounded to 5 s, when `OFFICE_ENABLED` | `scheduler_owner.office_controller = vercel` | `office_tasks.logical_key` unique | set `OFFICE_ENABLED=false` |
| Office LLM and QA task drain | none | new `office-worker.yml` on GitHub | `scheduler_owner.office_worker = github` | task lease + `logical_key` | disable the workflow; tasks stay queued |
| Experimental book entries, exits, marks | none | second step of V6, when `OFFICE_PAPER_ENABLED` | `scheduler_owner.office_paper = vercel` | `office-stage:paper:<book>:<session>:<slot>` and unique order keys | set `OFFICE_PAPER_ENABLED=false`; exits and marks continue |
| Morning briefing | none | queued by V8's receipt, written by the GitHub worker | follows `office_worker` | `briefing:<date>` | disable; no data loss |
| Weekly QA and admission review | none | GitHub, Friday | follows `office_worker` | `weekly-qa:<iso-week>` | disable |
| Office health | none | added to G3 | none | read-only | revert the check |

The scheduler-owner switch is one table row per operation, read by both callers before they act. The value
cannot be `both`. It is added in PR 2 with every existing operation set to its current owner, so adding it
changes no behaviour.

What would count as "cutover complete" for the brief's acceptance item 13: real GitHub run IDs for the
office worker and a matching `office_runs` row for each. YAML alone is not evidence.

## 10. Not done in this stage

- No production database was queried. Paper state comes from the watchdog log, not from a query.
- Vercel environment names and Vercel cron history were not verified.
- No secret or Variable was created, changed or deleted.
- The research repository's results were read from its documents; none was re-run.
