# Trading Office: schema plan, pull-request plan and owner decisions

Companion to `TRADING_OFFICE_BASELINE.md` and `TRADING_OFFICE_STRATEGY_INVENTORY.md` (2026-11-13). This is
a plan for review. No code in it exists yet.

## 1. Rules every pull request follows

- One pull request at a time, each from current `main`, never stacked. The owner approves each merge.
- Checked-in defaults are off: `OFFICE_ENABLED=false`, `OFFICE_PAPER_ENABLED=false`, provider `none`.
  With the defaults, merging a pull request changes no production behaviour.
- A pull request that adds tables ships its Alembic migration, but **running it against production is a
  separate step the owner starts**. Migrations 0022–0028 each had a workflow that also ran on the merge to
  `main`; the office migration workflows will have a manual trigger only, so a merge alone cannot change
  the production schema. Recurring jobs never run migrations.
- Tests run against the disposable Postgres that CI already provides (`TEST_DATABASE_URL`). No test touches
  production. No fixture signal is ever written to a production table.
- Paper and simulation only. No office module imports `ibkr_bridge`, a broker client or the execution
  desk; a test asserts this, extending the existing boundary test (#172).
- Frozen files are read, never edited: `strategy_forward.py`, `fair_value_forward.py`,
  `paper_ledger_forward.py`, `strategy_signals.py`, the event replay, `instructions.py`, and everything
  under `~/code/fsa-research`. A test pins the existing protocol hashes and fails if one changes.
- The owner's email is never placed in a request. SEC calls use the repository's configured
  `SEC_USER_AGENT` value as it is.

## 2. Schema plan

New tables, all prefixed `office_`, added by three migrations. Existing tables are not altered.

### Migration 0029 (PR 2): work, runs, budget, scheduler owner

| Table | Purpose | Key constraints |
|---|---|---|
| `office_agent_definitions` | Role, prompt/config version, provider, model, allowed tools | unique (role, version); rows are never updated |
| `office_tasks` | One row per logical piece of work: type, payload, state, priority, due time, retry time, dependencies, lease owner and expiry, attempt count | unique `logical_key`; state is one of QUEUED, RUNNING, WAITING_DATA, WAITING_REVIEW, RETRY_WAIT, COMPLETED, BLOCKED, FAILED, CANCELLED |
| `office_task_dependencies` | task → task it waits for | primary key (task, depends_on) |
| `office_runs` | One row per attempt: actor, start, heartbeat, end, code/config/model versions, safe error code, usage and cost, GitHub run id if any | foreign key to task; unique (task, attempt number) |
| `office_budget_reservations` | Reserved maximum cost before a paid call, actual cost after, state RESERVED / RECONCILED / RELEASED / UNRESOLVED | unique (run, call number); a check keeps amounts non-negative |
| `office_scheduler_owner` | One row per operation: owner `vercel` or `github`, version, changed by, changed at | primary key operation; a check forbids any other value |

Claiming a task is one statement (`UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED) RETURNING`), so
two workers cannot take the same task. An expired lease returns the task to RETRY_WAIT with its attempt
count kept; after the configured maximum it becomes FAILED. A run that called a provider and never
recorded the result leaves its reservation UNRESOLVED and the task BLOCKED for a person to look at; it is
not sent again automatically. The daily and monthly caps are enforced inside the transaction that inserts
the reservation, under an advisory lock, so two workers cannot both pass the cap. An unknown cost counts as
the reserved maximum, never as zero.

### Migration 0030 (PR 3): research

| Table | Purpose | Key constraints |
|---|---|---|
| `office_events` | A deduplicated "something changed" record from existing collectors: source identity, event time, first observed, content hash, tickers | unique (source, source key, content hash) |
| `office_memos` | Immutable memo versions: ticker, trigger event, version, claims (each FACT / INTERPRETATION / HYPOTHESIS / UNKNOWN with a source pointer), input hash, author model and prompt version, expiry | unique (memo id, version); no update or delete (trigger) |
| `office_reviews` | Review of one exact memo version: PASS / REVISION_REQUESTED / BLOCKED, findings, reviewer run | unique (memo id, memo version, review round); round ≤ 2 |
| `office_briefings` | Morning briefing and weekly summary | unique (kind, date) |

Memos live apart from `analysis_reports`. Nothing the existing trading logic reads is overwritten.

### Migration 0031 (PR 4 and PR 5): strategies, paper book, evaluation

| Table | Purpose | Key constraints |
|---|---|---|
| `office_strategy_versions` | Inventory row per exact version: source repository, hashes, rule summary, evidence tier, samples, known defects, original status. Copied from the source record; the source is never edited | unique (strategy id, version) |
| `office_admission_decisions` | Each of the six checks with its evidence, policy version, actor, time, outcome | append-only |
| `office_deployments` | A strategy version in a book: state BLOCKED / EXPERIMENTAL_PAPER / PAUSED / RETIRED / QUALIFIED_PAPER, execution kind NATIVE or EXTERNAL_LOCAL, forward protocol | unique (strategy id, version, book); state changes are also written to `operator_audit` |
| `office_books` | The experimental book: starting equity, frozen limits and cost model, policy version | limits cannot be updated once a deployment is active (trigger) |
| `office_opportunities` | Every signal the scanner saw, taken or not: deployment, ticker, signal time, intended entry time, expiry, sizing inputs, risk checks, reason codes; includes NO_OPPORTUNITY rows per scan | unique (deployment, ticker, signal session) |
| `office_capital_reservations` | Capital held for an accepted opportunity before its fill | unique (opportunity) |
| `office_orders` | Simulated order decision | unique (opportunity); so a retry cannot create a second order |
| `office_fills` | Simulated fill: time, price, price source (BAR_CLOSE, BAR_OPEN, EXTERNAL_MODELLED), costs by type; `simulated` is always true and `broker_fill` always false (check constraints) | unique (order, leg) |
| `office_positions`, `office_marks` | Open and closed positions with per-strategy attribution; daily marks | unique (book, session) on marks |
| `office_external_records` | Results summaries uploaded from the owner's Mac for EXTERNAL_LOCAL deployments: session, counts, net figures, the source file's hash | unique (deployment, session, source hash) |
| `office_evaluations` | QA scorecards: operational and strategy assessments kept separate, maturity state, protocol, verdict, limitations | unique (deployment, kind, as-of) ; append-only |
| `office_proposals` | A learning proposal; accepting one creates a new `office_strategy_versions` row and never changes an old one | append-only |

Rules built into the tables rather than left to code: a fill time cannot be earlier than its signal time
(check); a worker that arrives after the entry deadline writes the opportunity as MISSED and no order; a
pause blocks new orders and leaves exits, marks and scoring running.

## 3. Staged pull requests

Each line under "Shows" is the acceptance evidence from the brief's section 14 that the pull request's
tests produce on the disposable database.

### PR 2a: office tasks, runs and leases
Migration 0029 (first four tables), `stock_machine/office/tasks.py`, `config/trading_office.yml` with
everything disabled. **Shows:** two concurrent workers claim a task once; an expired lease is recovered
with bounded attempts; a repeated enqueue with the same key makes one task (items 5, 9).

### PR 2b: provider adapter and budget enforcement
`office/providers.py`: one interface, three implementations (Anthropic, OpenAI, and a recorded-response
fake for tests). Budget reservations. Default provider `none`, which refuses every call. **Shows:** the cap
holds under concurrent reservations; an unknown cost is charged at the maximum; an uncertain call is not
resent; no key appears in a log, error or stored row (items 10, 11).

### PR 2c: role contracts and scheduler-owner switch
Typed role definitions and their tool allowlists (no shell, no SQL, no GitHub administration, no
credentials). `office_scheduler_owner` seeded with today's owners; both the Vercel routes and the GitHub
scripts read it and refuse when they are not the owner. **Shows:** the non-owner caller is refused; existing
stage receipts and keys are unchanged; existing protocol hashes are unchanged (item 12).

### PR 3a: scout, analyst and reviewer
Migration 0030. The scout turns new `company_event_snapshots` and `filings` rows into deduplicated
`office_events`. The analyst writes a memo from typed read tools; the reviewer, in a separate run with
separate permissions, checks each numeric claim against its source pointer. One revision round, then the
claim stays blocked. Source text is treated as data, never as instructions. **Shows, with the fake
provider:** one event → one memo → one review → saved findings → a briefing; a memo with an invalid
citation is blocked (items 1, 8). A run with a real provider waits for the owner's key and go-ahead.

### PR 3b: office status and dashboard
Read-only Office view in the existing admin panel (owner login), and read-only MCP tools for office
status, strategy evidence, paper outcomes and briefings. **Shows:** the HTTP MCP still has no writing tool;
an unauthenticated request gets nothing (item 11).

### PR 4a: strategy inventory and admission
Migration 0031 (inventory, admission, deployments, books). Loads the inventory from this repository's own
records and from a versioned adapter file for the research strategies (version, hashes, figures, defects;
no market data). The Portfolio Manager applies the six checks in code. **Shows:** development-positive,
tested, pending, failed and missing evidence are told apart and original labels are untouched; a
qualifying fixture candidate gets exactly one deployment however often admission runs; a closed strategy
cannot be admitted under a new label (items 2, 3).

### PR 4b: scanner and experimental paper ledger
Opportunities, reservations, orders, fills, positions, marks; daily-bar strategies only. **Shows, on
fixtures:** a real rule firing produces one risk-checked simulated order and fill with the right time and
costs; no signal gives NO_OPPORTUNITY; a late worker records MISSED; stale prices, an unsupported
instrument or a failed risk check block the action; retries create nothing twice; a pause stops entries
and lets exits run; short sales are refused unless the book declares a borrow policy (items 4, 5, 8, 10).

### PR 4c: importer for local records (only if the owner chooses "mirror")
`scripts/office_export_local_record.py`, run by the owner on the Mac after the evening settle. It reads
the frozen recorders' output, checks the frozen hashes, and uploads a summary to `office_external_records`
through the scoped office API. It never uploads bars or quotes. **Shows:** a changed hash refuses the
upload; a repeated upload adds nothing.

### PR 5: QA, outcome scoring, proposals, briefings
`office_evaluations`, `office_proposals`. QA reads immutable signals and fills and writes two separate
assessments. Reports trades and unique dates, no-trade days, concentration, costs and the benchmark, and
refuses to print a significance figure from fewer dates than the protocol allows. **Shows:** fills
reconcile to cash and equity; matured, pending and shadow results are separate; a proposal creates a new
version and cannot change the old one or its history (items 6, 7).

### PR 6a: GitHub worker and secret bootstrap helper
`office-worker.yml` (manual trigger first, schedule added only after the canary), minimum permissions,
environment `Production`, secrets scoped to the steps that need them and never given to pull-request jobs.
`scripts/office_secrets_bootstrap.py`: takes an explicit list of allowed names, reads each value from the
process environment or a hidden prompt, passes it to `gh secret set` on standard input, and prints only
CREATED, PRESENT, MISSING or BLOCKED_PERMISSION. It never prints, logs or writes a value, and it skips a
name that is already PRESENT unless told to replace that one name. **The owner runs it.**

### PR 6b: canary, cutover and rollback runbook
`docs/TRADING_OFFICE_RUNBOOK.md`; office checks added to the watchdog. Each enabling step is listed for
the owner to perform or approve one at a time. **Shows:** real GitHub run IDs with matching `office_runs`
rows (item 13). Until those exist the status is "configured, not verified".

Acceptance items that depend on real events (a real filing producing a memo; a real signal in an admitted
strategy) will be marked PENDING until they happen. They will not be simulated in production.

## 4. Where this plan differs from the brief, and why

| Brief | This plan | Reason |
|---|---|---|
| GitHub is the scheduler; controller every 10 minutes on GitHub | Vercel keeps the clock for the timed stages; GitHub drains queued work | GitHub starts this repository's schedules 4–8 hours late (baseline, finding 1) |
| Claude sets GitHub Secrets and Variables | The owner runs the bootstrap helper | Standing rule: I do not read or move secret values |
| Use the normal merge and deploy process, then activate the pilot | Every merge, migration, enable and cutover waits for the owner's yes | Standing rule |
| FLOW/STRESS-like strategies run in the app's paper book | They stay on the Mac and are mirrored | IBKR minute bars may not leave this machine; blocked rather than worked around |
| Reuse a suitable existing ledger | New `office_*` ledger | The existing one holds one position per ticker with no strategy attribution, and it is under a registered evaluation that must not be disturbed |

## 5. Decisions for the owner

Nothing below has been done. Each needs a yes or a choice.

**A. Review this draft pull request.** Merge it if the plan is acceptable; it contains documents only.

**B. Strategy admission.** Recommended: mirror FLOW, STRESS and QM-1 from the Mac; do not admit
`sector_timed_120`; start the native experimental book empty.
- **Owner's answer, 2026-11-13 (B1, first half):** "Sure we can mirror the flow stress and qm-1 from the
  research we have done". So PR 4c (the importer) is in scope. Still open: what the upload may contain
  (B1, second half), B2, B3, and whether the native book starts empty. The frozen rules and research
  records stay unchanged and are read only; licensed data stays on the Mac.
- B1. Mirror the three local records into the office? If yes, may the upload include per-trade net bps and
  book equity (derived figures), or counts and totals only? No bars or quotes are sent in either case.
- B2. Admit `sector_timed_120` to the native book? It would need a declared short-borrow simulation
  policy first.
- B3. Port QM-1 to the app's own prices as a new, separately labelled version? Not recommended now.

**C. Model provider and budget.**
- C1. Provider: Anthropic or OpenAI (one is enough), and the exact model for analyst and reviewer.
- C2. Ceilings. The brief proposes $5 a day, $100 a month, 2 concurrent tasks, 3 new memos a day, one
  revision round. No lower cap exists in the repository today.

**D. Secrets and Variables** (the owner runs the helper from PR 6a, or sets them by hand):
- D1. `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in the `Production` environment: needed for any real memo.
- D2. Delete the three plain **Variables** `DATABASE_URL`, `FMP_API_KEY`, `STOCK_MACHINE_ADMIN_TOKEN`, and
  rotate those credentials if the Variables held real values.
- D3. Make `STOCK_MACHINE_ADMIN_TOKEN` match between GitHub and Vercel (fixes the failing option capture).
- D4. `SEC_USER_AGENT` as a `Production` Variable, if the owner wants a contact address on SEC requests.
  The owner sets the value; I will not put an email address in a request or a file.
- D5. Optional: `OFFICE_DATABASE_URL` for a database role limited to office tables; `OFFICE_API_TOKEN` for
  the scoped command API; `MASSIVE_API_KEY` if GitHub workers should fetch news.
- D6. Confirm which of these names exist in Vercel: `CRON_SECRET`, `STOCK_MACHINE_ADMIN_TOKEN`,
  `ADMIN_PASSWORD`, `MASSIVE_API_KEY`. I could not check.

**E. Scheduling.** Accept that the timed stages stay on Vercel (recommended), or ask for a measured trial
of one stage on GitHub first.

**F. Paper book limits.** The brief's pilot defaults for the new book: $100,000 virtual equity, 5% maximum
position, 50% maximum gross, 20% per sector, 10 positions, review at a 10% drawdown. The existing ledger
uses 10% and 50%. Confirm or change before any deployment is activated; they are frozen at activation.

**G. Protect `main`** (require CI before merge). Optional, and separate from the office.

**H. Operations note, research side.** The X2 shadow has no commit and no settle recorded for its first
five sessions (2026-11-09 to 2026-11-13). Worth a look before any mirror is built on it.

## 6. Rollout states, to be reported separately each time

Implemented (code on a branch) → merged → migrated (production schema) → deployed → enabled
(`OFFICE_ENABLED`, then `OFFICE_PAPER_ENABLED`) → verified (real run IDs and database receipts). Today
every part of the office is at "planned".
