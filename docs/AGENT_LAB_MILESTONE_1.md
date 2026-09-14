# Agent Lab milestone 1: research journal

## Shipped scope

A five-stock, research-only journal at `/agents`, with individual views at
`/agents/{ticker}`. The reviewed initial policy is `research-pilot-v1` and its
universe is AAPL, MSFT, UBER, HIMS and VZ. These are research coverage choices,
not portfolio allocations or investment recommendations. Missing coverage
records a failure rather than silently substituting a different company.

This release is a **deterministic recorder of the existing research packet**,
not a deployed LLM, news collector, simulator, reinforcement learner or trading
agent. It freezes the original analyst thesis, opposing evidence, invalidation
conditions, packet hash, source-model status and decision time. Source text is
attributed and displayed as untrusted text, never executed as instructions.
The API does not expose a report-vintage timestamp; the capture timestamp must
not be mistaken for the original analyst report's publication date.

Each capture records WATCH or NO_TRADE, with RECORDED, BLOCKED or FAILED status.
WATCH means research can be observed, not a trade is qualified. The prospective
20-session evaluation horizon is reserved; existing 12-month scenarios are
not reinterpreted as calibrated 20-session probabilities.

No broker order, fill, position, P&L, reward or explore/exploit branch is
created. Those values remain unavailable, not fabricated zeros. Existing
Forward Paper v2 cohorts, promotion gates, option capture and forecasting
semantics remain unchanged. The existing `/api/v1` research contract is not
extended with writes.

## Storage and invariants

Migration `0019_agent_lab` adds separate tables for frozen policies, evidence,
decisions, review events and an app-report outbox. UPDATE, DELETE and TRUNCATE
are rejected by database triggers; database administrators can still alter
DDL, so this is not a claim of cryptographic tamper-proof storage.

Captures use a database advisory transaction lock scoped to policy, ticker and
request key. Identical retries return the original decision before calling the
reader. Evidence, decision, initial event and outbox commit in one transaction.
Failure to create the outbox rolls back the record. Reviews append; reusing a
review key with different contents produces a conflict. Events that claim
submission, fills or rewards are not accepted in this milestone.

The full current packet is saved as observed. Missing, stale, invalid or future
inputs cause abstention. This is a prospective capture system, **not a
historical replay endpoint**. Clients cannot choose an earlier decision date.
Runtime capture explicitly calls the existing research reader with
`include_live_quote=False`; page loads read only the new journal tables.

The report outbox persists app-visible events only. No external delivery
worker, email/chat notification, scheduled capture or scheduled digest is
activated. A UTC-day filter supplies a daily journal view; this is not a
portfolio performance report. Filtered counts are computed over the full
matching ledger, not just the limited page of decision cards.

## API

- `GET /api/agent-lab`: persisted dashboard; optional ticker, status, day
  (YYYY-MM-DD, UTC) and limit (1-100).
- `GET /api/agent-lab/decisions/{uuid}`: immutable decision, original evidence,
  and append-only review history.
- `POST /api/admin/agents/{ticker}/capture`: body contains only
  `idempotency_key` (8-120 alphanumeric/underscore/dot/colon/hyphen characters).
- `POST /api/admin/agent-decisions/{uuid}/reviews`: idempotency_key and message.

Writes require the existing `STOCK_MACHINE_ADMIN_TOKEN` and
`AGENT_LAB_ENABLED=true`. Authorization is checked before journal or research
access. Capture is **disabled by default**. These routes cannot change broker
permissions, strategies, capital or the pilot universe.

Read access follows the existing public research dashboard convention. Do not
import private account information into this journal without adding separately
reviewed read authorization. The UI stores no admin token in browser storage.
Errors return fixed messages rather than provider exceptions or credentials.

## Deployment / activation

1. Review and merge the PR after the PostgreSQL and full-suite checks pass.
2. Apply `alembic upgrade head` through the existing controlled database release
   process and deploy the matching application/worker revision. Coordinate this
   transition: the schema sentinel moves from 0018 to 0019, and older workers
   must not continue using the old exact-version gate after migration.
3. Keep capture disabled until `/agents` and `/api/agent-lab` read the new tables.
   Before migration, API 503 is intentional; the UI must not display empty
   success or invented journal totals.
4. Enable `AGENT_LAB_ENABLED=true` in the server environment and redeploy.
5. From an authorized environment with the existing admin token and HTTPS
   `STOCK_MACHINE_API_BASE_URL`, manually run:

   ```sh
   python scripts/capture_agent_pilot.py --run-id agent-pilot-initial-001
   ```

   Use `--ticker AAPL` for a single-stock smoke check. Reuse the same run ID on
   any retry, especially after a timeout. No provider or database credentials
   are required by this HTTP caller.
6. Verify five individually persisted results, frozen evidence, replay identity
   and BLOCKED/FAILED reasons. Never seed artificial trades to populate the UI.

Code publication, migration, deployment and successful research capture are
separate checkpoints. A merged PR alone does not prove any of the latter.

## Acceptance tests

`tests/test_agent_lab.py`: permission contracts, frozen reasons, missing/stale/
future inputs, source attribution, nonfinite-data rejection, authentication,
feature flag, error redaction, inert GETs, absent order routes and UI text safety.

`tests/test_agent_lab_postgres.py`: real PostgreSQL migration in an isolated test
schema, concurrent replay, rollback, immutable records, review conflicts,
outbox atomicity, error records, full-ledger counts and invalid execution events.
Requires `TEST_DATABASE_URL`; never point it at a production database.

Synthetic values exist only inside isolated tests. Runtime code contains no
sample financial observations and never creates demonstration trades.

## Next milestone

First verify actual prospective capture on the deployed database. Then add
bounded, evidence-cited company/news research and candidate proposals. After
that, build shadow accounting, deterministic outcomes and rewards, and only
then constrained exploration. None can be inferred from this recorder's WATCH
status or a source model's diagnostic output.
