# Cloud-to-IBKR market-data bridge

## Goal

Allow the Vercel stock-machine deployment to read live/delayed IBKR quotes and
option chains from TWS / IB Gateway running on a trusted Mac without exposing
the TWS socket to the public Internet and without adding any order capability.

The bridge is deliberately narrow:

- `GET /healthz`
- `GET /v1/session`
- `GET /v1/underlyings/{symbol}`
- `GET /v1/quotes/{symbol}`
- `GET /v1/options/{symbol}/expirations`
- `GET /v1/options/{symbol}/strikes?month=AUG26`
- `POST /v1/options/{symbol}/chain`

There are no account, portfolio, order, generic proxy, or arbitrary upstream
request endpoints.

## Architecture

```text
ChatGPT / browser
       |
       v
Vercel stock-machine
IBKR_PROVIDER=remote_bridge
       |
       | HTTPS + Bearer token
       v
Cloudflare Tunnel hostname
       |
       | outbound tunnel only
       v
127.0.0.1:8765 on Mac
stock-machine-ibkr-bridge
       |
       | TWS socket API
       v
127.0.0.1:7497 (paper) / 7496 (live)
TWS or IB Gateway
```

Cloudflare Tunnel is a good fit because `cloudflared` establishes outbound
connections from the Mac; no inbound router port or public origin IP is
required. Use a named/remotely managed tunnel for production, not a Quick
Tunnel.

## 1. Configure TWS / IB Gateway

In TWS: **Global Configuration -> API -> Settings**.

- Enable **ActiveX and Socket Clients**.
- Keep **Read-Only API** enabled.
- Confirm the socket port. Typical defaults are 7497 for paper TWS and 7496
  for live TWS; IB Gateway commonly uses 4002 paper / 4001 live.
- Keep the bridge on the same machine and use `IBKR_TWS_HOST=127.0.0.1`.

The TWS API requires a running authenticated TWS or IB Gateway session. The
bridge does not bypass Interactive Brokers authentication or session rules.

## 2. Install the local Python TWS API

The bridge process needs Interactive Brokers' `ibapi` Python package. Install
it from the Python client included with the official TWS API download, then
verify:

```bash
python3 -c "import ibapi; print(ibapi.__file__)"
```

Install this repository in the same Python environment:

```bash
python3 -m pip install -e .
```

## 3. Generate a bridge secret

```bash
openssl rand -hex 32
```

Put the generated value in the Mac environment as `IBKR_BRIDGE_TOKEN`. Use the
same value later in Vercel. Do not commit it.

Example local environment:

```bash
export IBKR_BRIDGE_TOKEN='<64-hex-character-secret>'
export IBKR_TWS_HOST='127.0.0.1'
export IBKR_TWS_PORT='7497'
export IBKR_TWS_CLIENT_ID='17'
export IBKR_TWS_MARKET_DATA_TYPE='3'
```

Market-data type 3 requests delayed data when real-time entitlement is not
available. Real-time options quotes/Greeks still require the appropriate IBKR
market-data subscriptions.

## 4. Start and test the local bridge

```bash
stock-machine-ibkr-bridge
```

The process binds to `127.0.0.1:8765` by default. It intentionally refuses a
non-loopback bind.

Process health:

```bash
curl http://127.0.0.1:8765/healthz
```

Authenticated TWS status:

```bash
curl \
  -H "Authorization: Bearer $IBKR_BRIDGE_TOKEN" \
  http://127.0.0.1:8765/v1/session
```

Quote test:

```bash
curl \
  -H "Authorization: Bearer $IBKR_BRIDGE_TOKEN" \
  http://127.0.0.1:8765/v1/quotes/SBUX
```

Do not continue to the cloud tunnel until these local calls work.

## 5. Publish through a named Cloudflare Tunnel

Install `cloudflared` on the Mac. Create a named tunnel in Cloudflare and map a
stable hostname such as:

```text
ibkr-bridge.your-domain.com -> http://localhost:8765
```

Run `cloudflared` on the Mac using the tunnel token/configuration supplied by
Cloudflare. A named tunnel gives a stable hostname; Quick Tunnels are intended
for development/testing only.

The application-level bearer token remains required even behind Cloudflare.
For defense in depth, a Cloudflare Access service-token policy can also be
placed in front of the hostname later.

## 6. Configure Vercel

Set these **Production** environment variables on the
`fundermental-stock-analysis` project:

```text
IBKR_PROVIDER=remote_bridge
IBKR_BRIDGE_BASE_URL=https://ibkr-bridge.your-domain.com
IBKR_BRIDGE_TOKEN=<same secret used by the Mac bridge>
IBKR_BRIDGE_TIMEOUT_S=30
IBKR_BRIDGE_VERIFY_SSL=true
```

Redeploy production after changing the variables.

## 7. Acceptance tests

From the deployed stock-machine host:

```text
GET /api/quote/SBUX
GET /api/options/expirations/SBUX
GET /api/options/strikes/SBUX?month=<MONYY>
```

A successful `/api/options/expirations/SBUX` proves the complete path:

```text
Vercel -> HTTPS tunnel -> local bridge -> TWS/IBGW -> IBKR
```

After an expiration month is selected, request a small strike ladder through
`/api/options/generate/{ticker}` or `/api/options/scan/{ticker}`. The bridge
limits option-chain requests to 20 strikes per call to keep the TWS workload
bounded.

## Operational behavior

- If the Mac is asleep, TWS is logged out, the tunnel is down, or IBKR is
  unavailable, Vercel returns a structured HTTP 503 from market-data routes.
- Fundamental research, forecasts, bearish scans and stored data remain
  available because they do not depend on the bridge.
- TWS / IB Gateway is designed around an authenticated desktop session. Plan
  for periodic restarts/re-authentication rather than treating the bridge as a
  permanently headless broker daemon.
- Keep the Mac awake while the bridge is expected to serve cloud requests.
- Rotate `IBKR_BRIDGE_TOKEN` if it is ever exposed; update the Mac and Vercel
  values together.

## Security invariants

1. TWS listens only on loopback for this design.
2. The bridge itself binds only to loopback.
3. `cloudflared` creates outbound connectivity; no router port-forward is
   required.
4. Every data endpoint requires a long bearer secret.
5. The bridge contains no order/account endpoints and no generic proxy.
6. Production remote URLs must use HTTPS with certificate verification.
7. TWS **Read-Only API** should remain enabled as an independent broker-side
   control.
