# Intraday lab: check run, 2020-01..2023-12

`intraday-lab.v1`, generated 2026-10-25T12:45:42+00:00. The one check of each variant below: its verdict against the pass bar fixed beforehand is final and decides whether it may go to a hold-out protocol. Every run is in `docs/evidence/intraday-lab-log.json`: **16 variant tries so far** (a definition run with a given version of the code), **4 one-time confirms** (2017-07..2019-12) and **1 one-time checks** (2020-01..2023-12). The more variants are tried, the more one of them will look good by luck, which is why survivors are confirmed once, then checked once, and only then handed to the one hold-out run.

- **Sessions:** 1,006; **eligible stock-days:** 50,483.
- **Net bps** are per trade after costs and the beta x SPY hedge, at 10, 5, 2 and 0 bps a side (10 is the headline; liquid large caps at IBKR are often nearer 2-5). **R** is the gross move over the risk to the stop.

| Variant | What it does | Trades | Stocks | Net bps at 10 | at 5 | at 2 | at 0 | Hit rate at 10 | Mean R | Minutes held | Exits |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **EXT-FADE.v1** | a gap of at least 1% the same way as yesterday's move of at least 1%: fade it from 09:31, out at the gap fill or the close | 5,131 | 54 | -6.5 | +3.5 | +9.5 | +13.5 | 55% | – | 262 | close 3017, target 2114 |

**The book** (one value per session, per eligible stock-day; 95% Newey-West intervals, descriptive):

| Variant | At 10 bps | At 2 bps | Direction skill (vs the twin) |
|---|---|---|---|
| EXT-FADE.v1 | -4.77e-03 [-8.59e-03, -9.44e-04] | +3.33e-03 [-5.85e-04, +7.25e-03] | +4.91e-03 [+1.07e-03, +8.75e-03] |

**By year** (trades: net bps at 10; – under 20 trades):

| Variant | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|
| EXT-FADE.v1 | 1680: -5.1 | 929: +5.2 | 1632: -9.7 | 890: -15.4 |

**The pass bar**: at 5 bps a side, mean net per trade above 0, t above 2 (leave-one-session-out jackknife), positive in at least 3 of the 4 periods.

| Variant | Trades | Sessions | Largest session | Mean net bps | t | 2020-01..2020-12 | 2021-01..2021-12 | 2022-01..2022-12 | 2023-01..2023-12 | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| EXT-FADE.v1 | 5131 | 831 | 1% | +3.5 | 0.70 | +4.9 | +15.2 | +0.3 | -5.4 | FAIL |

**Skips:** EXT-FADE.v1 ENTRY_BEYOND_TARGET 17, RULE_201 3, UNSCORABLE 1, NO_ENTRY_MINUTE 2.
