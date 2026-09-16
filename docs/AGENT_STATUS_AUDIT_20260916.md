# Agent flow inspection — September 16, 2026

Observed through connected Vercel API reads during approximately 15:40-15:47
UTC. This records application state, not independently verified investment
research or claims about the latest external filing availability.

## Operational state

`GET /api/agent-lab` returned HTTP 200 and status OK. Capture was false;
decision counts were zero; AAPL/MSFT/UBER/HIMS/VZ were NOT_STARTED. The deployed
policy was research-pilot-v1 / deterministic_research_recorder.v1. All order,
simulation, exploration, reward and qualified-forward-paper flags were false.
The migration and read path work; this does not mean capture is authorized or
scheduled. The last inspected release job on September 14 lacked GitHub
Production admin and Vercel tokens; their current secret values were not read.

## Input freshness versus analysis freshness

`GET /api/data-quality` showed all five pilot names READY at the required-data
gate. All five stored price series extended through September 15, the latest
completed session at the time of this inspection. Refresh checks were recorded
on September 15 between 23:34 and 23:46 UTC. This is end-of-day coverage, not
September 16 intraday quotes.

| Stock | Latest stored fundamental period | Original saved analysis as_of |
| --- | --- | --- |
| AAPL | 2026-06-27 | 2026-08-08 |
| MSFT | 2026-06-30 | 2026-08-08 |
| UBER | 2026-06-30 | 2026-08-08 |
| HIMS | 2026-06-30 | 2026-09-05 |
| VZ | 2026-06-30 | 2026-08-08 |

Original dates were read from `/api/report/{ticker}`, not inferred from the
packet assembly time. HIMS consensus and earnings-surprise manifests were
PENDING with no rows. These optional inputs do not change the required-data
READY status, but they constrain expectations-based research.

AAPL's `/api/predict/AAPL` was current as of September 15, generated at
23:47:10 UTC. It reported drift-neutral bootstrap as primary, forecast_edge=false,
overall canonical readiness DIAGNOSTIC, and LSTM unavailable because torch was
not installed for that computation. We did not independently check all five
full forecast payloads. CI having an LSTM test job is not proof the production
forecast worker ran LSTM.

AAPL's `/api/v1/stocks/AAPL/research` was assembled on September 16 while still
carrying the August 8 saved thesis. It did not expose that report's as_of in its
analysis section. Its quote source was YAHOO:CHART:AAPL, live_quote was null,
and the structured next-earnings date was unavailable. Old narrative catalyst
dates are not a substitute for a current sourced event record.

## Priorities

1. Complete explicit capture configuration and verify the five original records.
2. Surface component dates and review stale narratives before decision use;
   the MCP wrapper adds visibility, not a newly refreshed analyst thesis.
3. Integrate/verify Massive server-side; a configured key alone is not ingestion.
4. Add the bounded analysis worker and scheduler with a decision audit trail.
5. Only then add simulated positions/outcomes and an independently scored learner.
