# Remote research MCP: API-backed, read-only

## Purpose and connection

This adds a separate Streamable HTTP facade at `/mcp/` to the existing
`stock_machine.webapp_automation:app`. After the PR is reviewed, merged and the
matching deployment verified, its production endpoint is:

```
https://fundermental-stock-analysis.vercel.app/mcp/
```

This is not the existing local stdio MCP. That server has direct database and
report-writing tools and is deliberately not imported by the remote facade.

The initial network facade mirrors only already-public research API reads.
It has no MCP authentication of its own, no new provider keys, and no admin
secret lookup. Do not add private account data or licensed redistribution data
to these tools without a separately reviewed authorization/licensing design.
Connecting a tool in ChatGPT is a separate user/workspace action; publishing
this code does not install a ChatGPT connection automatically.

Official protocol/runtime references:
- https://py.sdk.modelcontextprotocol.io/v1/server/
- https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt

Use a compatible remote MCP client. In ChatGPT, eligible users/workspaces can
create a custom app in developer mode, enter the verified endpoint, select no
auth for this public-read-only version and review the eight discovered tools.
Plan and workspace permissions determine availability. Never enter a Massive,
IBKR, database or admin token in the chat or the endpoint URL.

## Tool-to-API mapping

| Tool | Existing GET route |
| --- | --- |
| get_agent_status | /api/agent-lab |
| get_stock_research | /api/v1/stocks/{ticker}/research plus original report for separate vintage |
| get_saved_analysis | /api/report/{ticker} |
| get_forecast | /api/predict/{ticker} |
| get_data_freshness | /api/data-quality, filtered to one stock, plus original report date |
| list_agent_decisions | /api/agent-lab with bounded filters |
| get_agent_decision | /api/agent-lab/decisions/{uuid} |
| get_price_history | /api/prices/{ticker}, 1-500 observations |

The gateway makes in-process HTTP GET requests using httpx ASGITransport. It
uses existing route implementations rather than another SQL client or public
loopback network calls. A route's own authentication still applies. Incoming
client tokens/cookies are not passed downstream and admin credentials are
never supplied on the client's behalf. URLs and route families are fixed;
callers cannot provide arbitrary fetch URLs, SQL, scripts or provider endpoints.

## Data and permission boundaries

Tool descriptions and annotations are read-only, but enforcement is structural:
there are no capture, refresh, save-report, order, reward or policy-write tools.
All source text remains untrusted evidence. Exceptions are fixed safe error
codes; unavailable data is not shown as an empty successful portfolio.

The research API currently mixes a newly assembled packet with an older saved
analyst report. The MCP wrapper reads the original report separately, compares
the copied analysis sections, and reports its as_of only when they match.
Otherwise the vintage is UNVERIFIED. This is not an atomic historical snapshot.
Stored expected returns are never silently recalculated or described as current
just because the packet was assembled today. The current API freshness-aware
forecast endpoint remains authoritative for forecast status.

Large forecast graph/fold arrays are omitted by a documented projection while
readiness, methodology, calibration, promotion and source identities remain.
Full original forecast data remains available through the cited source API.
Response delivery is capped at 2 MiB (oversized responses fail explicitly).
Inbound requests are capped at 64 KiB, including chunked requests, with a
10-second body deadline. These limits do not replace platform rate limiting.

Only stateless Streamable HTTP POST is exposed. GET/DELETE streaming sessions
are not supported. Each request owns a short-lived SDK manager, avoiding a
persistent session manager across Vercel invocations or different event loops.
Host/Origin protection stays enabled for the canonical domains, the platform's
specific preview hostname and local development hosts outside Vercel. A reverse
proxy must preserve an allowed host; do not disable protection to make it work.

## Deployment / approval

No migration, capture flag, scheduler or trading permission is changed.
The schema remains 0019. Tests exercise actual MCP initialize, discovery,
separate requests, concurrency, all eight reads, blocked writes, bad inputs,
HTTP failures, stale analysis dates, Host/Origin and production route mounting.

IMPORTANT: the old `release_agent_lab_0019` script pins reviewed runtime files
against PR54. Merging this MCP runtime deliberately exceeds its release-file
allowlist. Finish the already-authorized initial capture on the current runtime
first, or separately review a new release baseline before running that script
on an MCP-enabled revision. This PR does not weaken or silently update the gate.

Verify the new preview/production endpoint with initialize, tools/list and a
read such as get_agent_status. A READY Vercel build alone is not a verified MCP
connection, and a successful tools/list call is not a successful research run.

## Remaining agent work

MCP is an interface, not an autonomous worker. Capture enablement, authorized
first captures, recurring worker orchestration, fresh evidence-cited analysis,
Massive entitlement testing/ingestion, simulation, rewards and exploration are
separate milestones. Do not claim these began because MCP became reachable.
