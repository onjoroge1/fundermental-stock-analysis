# Agent Trading v1

Agent Trading v1 adds a deterministic paper portfolio downstream of the immutable Agent Lab research journal.

## Operating modes

- **RESEARCH** — capture research decisions only. No simulated position is created.
- **PAPER** — after a research decision is recorded, translate its exact source report into a deterministic paper intent and simulate the approved fill.

There is deliberately no `LIVE` mode and no broker-order submission function in v1.

The owner changes RESEARCH/PAPER from `/admin`. The setting is stored in the isolated Agent Trading ledger; no Vercel feature flag or redeploy is required.

## Decision-to-intent mapping

The frozen research decision remains `mode=RESEARCH` and `execution_status=NOT_ENABLED`. Agent Trading never edits it.

The downstream mapper loads the exact `source_report_id` referenced by the decision:

| Source classification | Desired paper side |
| --- | --- |
| ATTRACTIVE | LONG |
| UNATTRACTIVE | SHORT |
| WATCH | FLAT |
| INSUFFICIENT_DATA | FLAT |

Actions are deterministic:

- no position + LONG -> `OPEN_LONG`
- no position + SHORT -> `OPEN_SHORT`
- no position + FLAT -> `NO_TRADE`
- matching position -> `HOLD`
- conflicting side or FLAT -> `CLOSE`

A reversal never closes and reopens from the same decision. The current position is flattened first. A later independently recorded decision is required to open the opposite side.

Blocked or failed research decisions can only create a `BLOCKED/NO_TRADE` paper intent and never a fill.

## Risk contract

Agent Trading v1 uses fixed code-level paper limits:

- starting paper equity: **$100,000**
- maximum one-position notional: **10%** of current paper equity
- maximum gross paper exposure: **50%** of current paper equity
- maximum simultaneous open positions: **5**
- minimum new paper position: **$500**
- simulated transaction cost: **10 bps per fill**
- universe: the five Agent Lab pilot symbols only

Existing open positions must have current completed-session prices before a new position can consume risk capacity. Missing or stale required prices fail closed.

## Pricing and P&L

Paper fills use the latest completed-session adjusted close already stored by Stock Machine. They are not live quotes and are never represented as broker fills.

Paper units may be fractional because this layer measures decision economics, not broker lot constraints.

For an open position:

- LONG unrealized P&L = `(mark - entry) * units - entry cost`
- SHORT unrealized P&L = `(entry - mark) * units - entry cost`

When closed, realized P&L additionally deducts the simulated exit cost.

## Evidence and idempotency

Every paper intent has a unique source `decision_id`. Reprocessing the same decision returns the original intent instead of creating another fill or position.

Every open/close fill records:

- source intent
- source decision through its position
- ticker and side
- completed market date
- simulated price and units
- notional
- simulated cost
- `simulated=true`

## Storage and release simplicity

### Owner trade history

The admin portfolio displays all open equity positions and the latest 50 closed
positions, with an explicit count when older history is omitted. The existing
owner-authenticated dashboard reads this history; no migration is required.

- Opened time is the position's recorded `created_at`; closed time is the saved
  closing fill's `created_at`. The UI labels these in America/New_York time.
- Entry and exit market dates are separate: this simulator prices from stored
  completed-session closes, which may predate the actual recording time.
- Opening and closing reasons, selector state, costs, and decision IDs come from
  their saved intents/fills. The source thesis comes from the opening decision,
  not the latest report or latest signal for that ticker.
- Units, prices, notional, and P&L are shown. Closed P&L includes entry and exit
  costs; open P&L includes entry cost only. Missing marks or legacy metadata are
  displayed as unavailable, not invented dates, reasons, or zero returns.
- A recorded research horizon is context, not a promised or scheduled closing
  date. Open trades remain labelled "Still open" until a closing fill exists.

Agent Trading v1 does **not** add an Alembic migration. The isolated paper tables are initialized idempotently only by an authenticated owner write that first switches to PAPER (or by a PAPER write after that). Normal reads never create schema.

This keeps the core application migration head unchanged and avoids a separate trading migration/release workflow.

## Explicit non-capabilities

Agent Trading v1 does not:

- connect to IBKR for execution
- submit, modify, or cancel broker orders
- move cash
- claim actual fills
- enable live trading
- allow the model to choose position size
- allow the model to bypass freshness/source/risk gates
- perform reinforcement learning or policy promotion

A future live-execution version must consume approved intents through a separately reviewed broker adapter and additional deterministic account-level risk controls. It should not give the research agent direct broker credentials or unrestricted broker methods.
