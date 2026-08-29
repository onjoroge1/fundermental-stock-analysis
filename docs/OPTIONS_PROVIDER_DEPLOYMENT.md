# Options provider deployment boundary

The stock research API can run in Vercel because its normalized fundamentals,
forecasts, reports, and snapshots live in the configured database. Live IBKR
market data is different: the default `tws` provider expects TWS or IB Gateway
to be reachable from the process running the API.

A Vercel function cannot use `127.0.0.1` to reach TWS running on a user's local
computer. It may also lack the optional `ibapi` package. These are provider
availability conditions, not stock-machine application failures.

The operational API therefore normalizes provider import/configuration/socket
failures to `MarketDataUnavailable`, which production renders as HTTP 503 with
a structured JSON body. Research endpoints remain available.

For live option-chain selection, use one of these deployment patterns:

1. Run the stock-machine API locally on the same network/host as TWS or IB
   Gateway.
2. Expose a securely hosted, authenticated IBKR Client Portal Gateway that the
   API can reach and set `IBKR_PROVIDER=client_portal`.
3. Add a cloud-accessible licensed option-data provider for quotes/chains while
   retaining IBKR separately for execution.

Do not interpret a 503 from `/api/options/*` as a failure of the fundamental
forecast. It means the requested live market-data service is not reachable in
that deployment environment.
