# Intraday lab: explore run, 2016-01..2017-06

`intraday-lab.v1`, generated 2026-10-22T23:36:04+00:00. Exploratory: these numbers decide nothing. Every run is in `docs/evidence/intraday-lab-log.json`: **16 variant tries so far** (a definition run with a given version of the code), **0 one-time confirms** (2017-07..2019-12) and **0 one-time checks** (2020-01..2023-12). The more variants are tried, the more one of them will look good by luck, which is why survivors are confirmed once, then checked once, and only then handed to the one hold-out run.

- **Sessions:** 377; **eligible stock-days:** 15,439.
- **Net bps** are per trade after costs and the beta x SPY hedge, at 10, 5, 2 and 0 bps a side (10 is the headline; liquid large caps at IBKR are often nearer 2-5). **R** is the gross move over the risk to the stop.

| Variant | What it does | Trades | Stocks | Net bps at 10 | at 5 | at 2 | at 0 | Hit rate at 10 | Mean R | Minutes held | Exits |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **GU-LIM.v1** | gap up of at least 3%; after the high reaches 1% above the open, buy the pullback, with a resting buy at 50% of the bounce on the way down; stop at the bounce's base; target 2R, out on a turn against or at the close | 52 | 28 | -28.5 | -18.5 | -12.5 | -8.5 | 25% | -0.12 | 13 | stop 26, target 9, turn 17 |
| **GU-OPEN.v1** | gap up of at least 3%; after the high reaches 1% above the open, buy the pullback, when a 5-minute bar crosses back above the 09:30 open after trading below it; stop at the low; target 2R, out on a turn against or at the close | 27 | 15 | -54.2 | -44.2 | -38.2 | -34.2 | 19% | -0.35 | 17 | stop 12, target 2, turn 13 |
| **GU-REV.v1** | gap up of at least 3%; after the high reaches 1% above the open, buy the pullback, when a 5-minute bar closes above the high of the red 5-minute bar before it; stop at the pullback low; target 2R, out on a turn against or at the close | 24 | 16 | -14.0 | -4.0 | +2.0 | +6.0 | 29% | +0.12 | 26 | target 3, turn 21 |
| **GD-LIM.v1** | gap down of at least 3%; after a 1% bounce off the session low, buy the reversal, with a resting buy at 50% of the bounce on the way down; stop at the bounce's base; target 2R, out on a turn against or at the close | 124 | 40 | -13.6 | -3.6 | +2.4 | +6.4 | 37% | +0.24 | 16 | stop 32, target 31, turn 61 |
| **GD-OPEN.v1** | gap down of at least 3%; after a 1% bounce off the session low, buy the reversal, when a 5-minute bar crosses back above the 09:30 open after trading below it; stop at the low; target 2R, out on a turn against or at the close | 107 | 39 | -36.2 | -26.2 | -20.2 | -16.2 | 31% | +0.05 | 29 | stop 16, target 6, turn 85 |
| **GD-REV.v1** | gap down of at least 3%; after a 1% bounce off the session low, buy the reversal, when a 5-minute bar closes above the high of the red 5-minute bar before it; stop at the pullback low; target 2R, out on a turn against or at the close | 49 | 29 | -36.4 | -26.4 | -20.4 | -16.4 | 20% | -0.15 | 23 | stop 7, target 2, turn 40 |
| **EXT-FADE.v1** | a gap of at least 1% the same way as yesterday's move of at least 1%: fade it from 09:31, out at the gap fill or the close | 547 | 45 | +11.7 | +21.7 | +27.7 | +31.7 | 59% | – | 285 | close 359, target 188 |
| **EXT-FADE.v2** | a gap of at least 1% the same way as yesterday's move of at least 1%, SPY's own gap under 0.5%: fade it from 09:35, out at the gap fill or the close, at most 3 a session (largest gap first) | 263 | 41 | +30.9 | +40.9 | +46.9 | +50.9 | 66% | – | 262 | close 158, target 105 |
| **EARN-FH.v1** | an earnings gap (released outside market hours) that the first hour pushed against: follow the first hour from 10:30 to the close | 109 | 35 | +23.6 | +33.6 | +39.6 | +43.6 | 62% | – | 329 | close 109 |
| **EARN-FH15.v1** | an earnings gap of at least 1.5% (released outside market hours) that the first hour pushed against: follow the first hour from 10:30 to the close | 74 | 33 | +39.3 | +49.3 | +55.3 | +59.3 | 65% | – | 329 | close 74 |

**The book** (one value per session, per eligible stock-day; 95% Newey-West intervals, descriptive):

| Variant | At 10 bps | At 2 bps | Direction skill (vs the twin) |
|---|---|---|---|
| GU-LIM.v1 | -6.91e-04 [-1.22e-03, -1.62e-04] | -3.15e-04 [-8.08e-04, +1.78e-04] | -2.54e-04 [-7.38e-04, +2.30e-04] |
| GU-OPEN.v1 | -5.58e-04 [-9.03e-04, -2.13e-04] | -3.61e-04 [-6.60e-04, -6.09e-05] | -2.24e-04 [-5.07e-04, +5.96e-05] |
| GU-REV.v1 | -2.76e-04 [-5.34e-04, -1.82e-05] | -9.35e-05 [-3.35e-04, +1.48e-04] | -2.46e-05 [-2.42e-04, +1.92e-04] |
| GD-LIM.v1 | -9.88e-04 [-1.89e-03, -8.37e-05] | +1.15e-05 [-9.12e-04, +9.35e-04] | +1.25e-04 [-8.00e-04, +1.05e-03] |
| GD-OPEN.v1 | -1.62e-03 [-2.89e-03, -3.45e-04] | -7.00e-04 [-1.70e-03, +3.04e-04] | -2.73e-04 [-1.27e-03, +7.20e-04] |
| GD-REV.v1 | -9.17e-04 [-1.57e-03, -2.60e-04] | -4.99e-04 [-9.48e-04, -4.93e-05] | -2.00e-04 [-6.01e-04, +2.02e-04] |
| EXT-FADE.v1 | +3.11e-03 [-3.10e-04, +6.54e-03] | +6.94e-03 [+3.49e-03, +1.04e-02] | +6.87e-03 [+3.71e-03, +1.00e-02] |
| EXT-FADE.v2 | +3.65e-03 [+1.25e-03, +6.04e-03] | +5.50e-03 [+2.96e-03, +8.03e-03] | +5.67e-03 [+3.13e-03, +8.22e-03] |
| EARN-FH.v1 | +1.23e-03 [-5.53e-04, +3.02e-03] | +2.44e-03 [+5.07e-04, +4.37e-03] | +2.74e-03 [+7.64e-04, +4.72e-03] |
| EARN-FH15.v1 | +1.21e-03 [-1.95e-04, +2.62e-03] | +2.01e-03 [+4.98e-04, +3.53e-03] | +2.21e-03 [+6.67e-04, +3.76e-03] |

**By year** (trades: net bps at 10; – under 20 trades):

| Variant | 2016 | 2017 |
|---|---|---|
| GU-LIM.v1 | 33: -25.0 | 19: – |
| GU-OPEN.v1 | 18: – | 9: – |
| GU-REV.v1 | 12: – | 12: – |
| GD-LIM.v1 | 98: -11.0 | 26: -23.7 |
| GD-OPEN.v1 | 88: -38.2 | 19: – |
| GD-REV.v1 | 42: -33.2 | 7: – |
| EXT-FADE.v1 | 412: +5.5 | 135: +30.8 |
| EXT-FADE.v2 | 161: +32.9 | 102: +27.7 |
| EARN-FH.v1 | 71: +17.1 | 38: +35.7 |
| EARN-FH15.v1 | 51: +36.5 | 23: +45.4 |

**The pass bar, for reference** (the confirm's thresholds on the explore periods; it decides only on the confirm and check months): at 5 bps a side, mean net per trade above 0, t above 2 (leave-one-session-out jackknife), positive in at least 2 of the 2 periods.

| Variant | Trades | Sessions | Largest session | Mean net bps | t | 2016-01..2016-12 | 2017-01..2017-06 | Verdict |
|---|---|---|---|---|---|---|---|---|
| GU-LIM.v1 | 52 | 46 | 4% | -18.5 | -1.48 | -15.0 | -24.7 | – |
| GU-OPEN.v1 | 27 | 26 | 7% | -44.2 | -3.06 | -38.4 | -55.7 | – |
| GU-REV.v1 | 24 | 22 | 8% | -4.0 | -0.20 | -12.0 | +4.0 | – |
| GD-LIM.v1 | 124 | 62 | 19% | -3.6 | -0.46 | -1.0 | -13.7 | – |
| GD-OPEN.v1 | 107 | 53 | 19% | -26.2 | -2.82 | -28.2 | -16.8 | – |
| GD-REV.v1 | 49 | 24 | 22% | -26.4 | -3.64 | -23.2 | -45.6 | – |
| EXT-FADE.v1 | 547 | 207 | 4% | +21.7 | 2.61 | +15.5 | +40.8 | – |
| EXT-FADE.v2 | 263 | 158 | 1% | +40.9 | 3.70 | +42.9 | +37.7 | – |
| EARN-FH.v1 | 109 | 89 | 3% | +33.6 | 2.52 | +27.1 | +45.7 | – |
| EARN-FH15.v1 | 74 | 65 | 4% | +49.3 | 3.20 | +46.5 | +55.4 | – |

**Skips:** EXT-FADE.v1 ENTRY_BEYOND_TARGET 1; EXT-FADE.v2 ENTRY_BEYOND_TARGET 5, CAP 31.
