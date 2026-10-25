# EXT-FADE-CB.v1 (constrained book): the one check, 2020-01..2023-12

`ext-fade-portfolio.v1`, generated 2026-10-25T12:52:57+00:00. EXT-FADE.v1's 5,131 check trades (lab log entry 21, verdict FAIL) on the constrained book frozen on 2026-10-24, against v1's equal-notional book at equal average gross. The verdict against the pass bar fixed beforehand is final. Log: `docs/evidence/ext-fade-portfolio-log.json`.

**Verdict: FAIL**. v1 did not pass its check: this result is recorded and does not go forward.

| Gate | What | Value | Pass |
|---|---|---|---|
| G1 | k at most 2.5 | k 1.1879 | yes |
| G2 | R_ES below 1 and its UB95 below 1 | R_ES 1.0348, UB95 1.0847 | no |
| G3 | W10, DD and MDD all better | W10 no, DD no, MDD no | no |
| G4 | LB95 of dSR above -0.30 | dSR -0.2461, LB95 -0.4585 | no |

Daily P&L in bps of one base notional, at 5 bps a side on stock and 0.5 on SPY, 1,006 sessions (831 with a trade):

| | Sharpe | Mean/day | sd/day | ES5 | W10 | Worst day | DD | MDD |
|---|---|---|---|---|---|---|---|---|
| v1 | 0.22 | +11.1 | +809.1 | +1921.7 | -3743.1 | -10498.5 | +590.7 | -20620.6 |
| CB at equal gross | -0.03 | -1.5 | +835.7 | +1988.5 | -3915.5 | -12470.9 | +646.9 | -26638.8 |

90% intervals (paired block bootstrap, 10,000 replicates): R_ES [0.9843984941282086, 1.0846622080432091], dSR [-0.45854196962280563, -0.05009448461181503], W10 ratio [0.9260153046258883, 1.141827862409567], DD ratio [1.0156852043267304, 1.1531840466141299], MDD ratio [0.94878573769051, 1.596925609103267].

- **Candidates:** 5,154 at 09:31 ({'ENTRY_BEYOND_TARGET': 17, 'NO_ENTRY_MINUTE': 2, 'RULE_201': 3, 'TRADE': 5132}), 5,131 of them v1's trades; 1 unscorable; late-excluded 0 (none).
- **Hedge:** netted SPY cost per trade-unit 1.343 bps (v1), 1.328 (CB), against 1.414 unnetted; 67 entries and exits in the same bar.
- **Binding:** cluster on 126 sessions, group on 0, book on 57 (of 834 with a candidate); mean s 0.842, 40.1% of positions below 1, minimum 0.176.

## Reported, not gating

| Version | k | R_ES | UB95 | dSR | LB95 | Gates |
|---|---|---|---|---|---|---|
| stock 10 bps | 1.1879 | 1.0192 | 1.0678 | -0.2121 | -0.4666 | 1000 |
| stock 15 bps | 1.1879 | 1.0027 | 1.0519 | -0.1750 | -0.4783 | 1000 |
| spy 0 bps | 1.1879 | 1.0374 | 1.0874 | -0.2512 | -0.4585 | 1000 |
| spy 1 bps | 1.1879 | 1.0322 | 1.0819 | -0.2406 | -0.4572 | 1000 |
| post-skip candidates | 1.1872 | 1.0342 | 1.0840 | -0.2444 | -0.4563 | 1000 |
| without 2020 | 1.1768 | 1.0312 | 1.0836 | -0.5163 | -0.7715 | 1000 |
| without 2021 | 1.2108 | 1.0485 | 1.1069 | -0.1562 | -0.3979 | 1000 |
| without 2022 | 1.1412 | 1.0245 | 1.0793 | -0.1506 | -0.3572 | 1000 |
| without 2023 | 1.2154 | 1.0435 | 1.1060 | -0.2505 | -0.4937 | 1000 |

| Normalisation | k | R_ES | W10 | DD | MDD |
|---|---|---|---|---|---|
| equal average entry gross | 1.1879 | 1.0348 | 1.046 | 1.095 | 1.292 |
| k cal | 1.0870 | 0.9469 | 0.957 | 1.002 | 1.182 |
| time weighted | 1.2144 | 1.0579 | 1.069 | 1.120 | 1.321 |
| equal vol | 1.1500 | 1.0018 | 1.013 | 1.060 | 1.251 |
| unnormalised | 1.0000 | 0.8711 | 0.881 | 0.922 | 1.088 |

| Year (at the global k) | Sharpe v1 / CB | ES5 v1 / CB | MDD v1 / CB | Capped sessions |
|---|---|---|---|---|
| 2020 | 0.34 / 0.44 | 2897 / 3081 | -15121 / -15148 | 44 |
| 2021 | 1.16 / 0.67 | 1147 / 1300 | -5529 / -6309 | 18 |
| 2022 | -0.17 / -0.88 | 1858 / 1745 | -12394 / -14073 | 50 |
| 2023 | -0.80 / -1.00 | 1441 / 1617 | -11179 / -12605 | 20 |

- **Intraday trough:** ES5 2712 (v1) vs 2723 (CB); worst -15025 vs -17848.
- **Gross:** peak 47.0 vs 19.0 units (normalised); p99 of active sessions 36.7 vs 19.0.
- **Concentration:** best 10 days 3.27 (v1) vs -21.97 (CB) of P&L; top 3 stocks 0.85 vs 2.87; correlation of the daily series 0.966.

PASS, with v1 PASS: the CB enters the 2024+ hold-out as its own pre-registered secondary hypothesis to v1, in its own family. It does not join, and does not change, the three overlays' registered Holm family. Its hold-out test is this frozen spec and pass bar, unchanged, at one-sided 5%, registered before any 2024 bar is read.

FAIL: final for these data, and v1 stays on its plain equal-notional book. Any change to a constant, the map, the nesting or the normalisation is a new variant, testable only on new data or new universes.
