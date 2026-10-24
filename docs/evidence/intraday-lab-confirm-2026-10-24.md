# Intraday lab: confirm run, 2017-07..2019-12

`intraday-lab.v1`, generated 2026-10-24T16:05:04+00:00. The one confirm of each variant below: its verdict against the pass bar fixed beforehand is final and decides whether it may be checked. Every run is in `docs/evidence/intraday-lab-log.json`: **16 variant tries so far** (a definition run with a given version of the code), **4 one-time confirms** (2017-07..2019-12) and **0 one-time checks** (2020-01..2023-12). The more variants are tried, the more one of them will look good by luck, which is why survivors are confirmed once, then checked once, and only then handed to the one hold-out run.

- **Sessions:** 629; **eligible stock-days:** 27,734.
- **Net bps** are per trade after costs and the beta x SPY hedge, at 10, 5, 2 and 0 bps a side (10 is the headline; liquid large caps at IBKR are often nearer 2-5). **R** is the gross move over the risk to the stop.

| Variant | What it does | Trades | Stocks | Net bps at 10 | at 5 | at 2 | at 0 | Hit rate at 10 | Mean R | Minutes held | Exits |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **EXT-FADE.v1** | a gap of at least 1% the same way as yesterday's move of at least 1%: fade it from 09:31, out at the gap fill or the close | 1,353 | 48 | +16.3 | +26.3 | +32.3 | +36.3 | 59% | – | 260 | close 795, target 558 |
| **EXT-FADE.v2** | a gap of at least 1% the same way as yesterday's move of at least 1%, SPY's own gap under 0.5%: fade it from 09:35, out at the gap fill or the close, at most 3 a session (largest gap first) | 505 | 47 | -4.8 | +5.2 | +11.2 | +15.2 | 54% | – | 264 | close 319, target 186 |
| **EARN-FH.v1** | an earnings gap (released outside market hours) that the first hour pushed against: follow the first hour from 10:30 to the close | 178 | 42 | -3.1 | +6.9 | +12.9 | +16.9 | 48% | – | 328 | close 178 |
| **EARN-FH15.v1** | an earnings gap of at least 1.5% (released outside market hours) that the first hour pushed against: follow the first hour from 10:30 to the close | 138 | 41 | -5.4 | +4.6 | +10.6 | +14.6 | 48% | – | 329 | close 138 |

**The book** (one value per session, per eligible stock-day; 95% Newey-West intervals, descriptive):

| Variant | At 10 bps | At 2 bps | Direction skill (vs the twin) |
|---|---|---|---|
| EXT-FADE.v1 | +3.87e-03 [-1.75e-05, +7.75e-03] | +8.73e-03 [+4.40e-03, +1.31e-02] | +1.01e-02 [+5.69e-03, +1.46e-02] |
| EXT-FADE.v2 | -9.85e-04 [-2.64e-03, +6.71e-04] | +8.88e-04 [-7.71e-04, +2.55e-03] | +1.56e-03 [-7.58e-05, +3.19e-03] |
| EARN-FH.v1 | -3.01e-04 [-1.51e-03, +9.05e-04] | +6.28e-04 [-5.76e-04, +1.83e-03] | +8.60e-04 [-3.49e-04, +2.07e-03] |
| EARN-FH15.v1 | -3.44e-04 [-1.39e-03, +7.06e-04] | +3.44e-04 [-6.99e-04, +1.39e-03] | +5.16e-04 [-5.28e-04, +1.56e-03] |

**By year** (trades: net bps at 10; – under 20 trades):

| Variant | 2017 | 2018 | 2019 |
|---|---|---|---|
| EXT-FADE.v1 | 113: +9.9 | 669: +29.7 | 571: +1.8 |
| EXT-FADE.v2 | 90: +10.2 | 233: -5.4 | 182: -11.4 |
| EARN-FH.v1 | 34: +4.4 | 83: -16.3 | 61: +10.8 |
| EARN-FH15.v1 | 24: +18.1 | 71: -21.7 | 43: +8.3 |

**The pass bar**: at 5 bps a side, mean net per trade above 0, t above 2 (leave-one-session-out jackknife), positive in at least 2 of the 3 periods.

| Variant | Trades | Sessions | Largest session | Mean net bps | t | 2017-07..2017-12 | 2018-01..2018-12 | 2019-01..2019-12 | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| EXT-FADE.v1 | 1353 | 408 | 3% | +26.3 | 3.80 | +19.9 | +39.7 | +11.8 | PASS |
| EXT-FADE.v2 | 505 | 288 | 1% | +5.2 | 0.65 | +20.2 | +4.6 | -1.4 | FAIL |
| EARN-FH.v1 | 178 | 132 | 2% | +6.9 | 0.52 | +14.4 | -6.3 | +20.8 | FAIL |
| EARN-FH15.v1 | 138 | 110 | 3% | +4.6 | 0.29 | +28.1 | -11.7 | +18.3 | FAIL |

**Skips:** EXT-FADE.v1 ENTRY_BEYOND_TARGET 4; EXT-FADE.v2 ENTRY_BEYOND_TARGET 20, CAP 82; EARN-FH.v1 RULE_201 1, MISSING_MINUTE 1; EARN-FH15.v1 MISSING_MINUTE 1.
