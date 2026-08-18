# Phase 2: forecast and market-data contracts

Phase 2 creates the stable boundary that the options analytics engine will use.
It does not recommend strategies, size positions, or place orders.

## Canonical forecast contract

Every forecast source is normalized to `forecast_distribution.v1`:

- symbol, as-of date, generation time, and spot price;
- one or more unique trading-day horizons;
- P(up), central return/price, and ordered P10/P25/P50/P75/P90 bands;
- an explicit central-estimate method (`mean`, `median`, or model output);
- model confidence kept separate from calibration status;
- baseline result (`leads`, `beats_baseline`, `failed`, or not compared);
- source methodology and limitations.

The existing bootstrap/LSTM prediction lab now emits this contract alongside
its legacy payload. The attached 5/10/20-day LightGBM/conformal format has an
adapter, but is not copied into the repository as a second training system.
Its confidence score remains a model score unless its payload explicitly
declares probability calibration.

## Canonical market-data contract

The market-data package defines provider-neutral models for:

- underlying contracts and quotes;
- available call/put strikes;
- option contracts;
- option top-of-book, mark, volume, open interest, IV, and Greeks;
- chain snapshots with data-quality warnings.

Every quote labels IBKR availability as real-time, delayed, frozen,
not-subscribed, incomplete, or unknown. A strategy engine can therefore reject
stale or unsubscribed data instead of silently treating it as live.

## IBKR read-only workflow

The adapter follows Interactive Brokers' required Client Portal sequence:

1. manually authenticate the local Client Portal Gateway with 2FA;
2. verify `/iserver/auth/status`;
3. search the underlying with `/iserver/secdef/search`;
4. retrieve a month's potential strikes with `/iserver/secdef/strikes`;
5. confirm explicit strikes through `/iserver/secdef/info`;
6. initialize `/iserver/accounts` and preflight market-data snapshots;
7. batch selected contract IDs through `/iserver/marketdata/snapshot`.

The implementation caps one chain request at 20 explicitly selected strikes
and 50 contract IDs, and paces requests below IBKR's global ten-request-per-
second limit. There is no generic public request method and no order endpoint.

## Operational limitations

- Client Portal Gateway authentication cannot be automated for an individual
  account; reauthentication is required at least daily.
- Market-data endpoints require an active brokerage session and the relevant
  subscriptions. The same username can have only one brokerage session.
- The local gateway uses a self-signed certificate by default. Disabling TLS
  verification is permitted only for localhost by this adapter.
- The first snapshot is a preflight; the adapter repeats it after a short wait.
- Live integration tests require the user's authenticated gateway and are not
  run in CI. All parsing and sequencing are covered with recorded-shape mocks.

Official reference:

- https://ibkrcampus.com/campus/ibkr-api-page/cpapi-v1/
- https://ibkrcampus.com/docs/web-api/api-reference/trading-market-data/get-md-snapshot
