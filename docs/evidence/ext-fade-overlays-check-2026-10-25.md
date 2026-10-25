# EXT-FADE.v1 overlays: the one check, 2020-01..2023-12

`ext-fade-overlays.v1`, generated 2026-10-25T12:49:18+00:00. The three overlays frozen on 2026-10-24, run once on EXT-FADE.v1's check trades (lab log entry 21, verdict FAIL). Each verdict against the pass bar fixed beforehand is final. Log: `docs/evidence/ext-fade-overlays-log.json`.

- **EXT-FADE.v1:** 5,131 trades; net bps per trade +3.5 at 5 bps a side (t 0.70), -6.5 at 10; book Sharpe 0.35 at 5, -0.65 at 10. 1,006 sessions.

## EXT-FADE-SW.v1 (sizing by S5 / 3.186): FAIL

Mean S5 3.519; no trade (S5 = 0) on 21 of 5131.

| Part | Mean | t | 2020 | 2021 | 2022 | 2023 | Pass |
|---|---|---|---|---|---|---|---|
| per unit of position, 5 bps | +5.9 | 1.00 | +9.5 | +20.6 | +0.7 | -6.5 | no |
| gain over v1 at equal exposure, 5 bps | +2.4 | 1.85 | +4.6 | +5.4 | +0.5 | -1.2 | no |

Book Sharpe at 5 bps: 0.50 vs v1 0.35 (pass: yes).

## EXT-FADE-P2.v1 (filter): FAIL

Kept 3269 of 5131 trades.

| Part | Mean | t | 2020 | 2021 | 2022 | 2023 | Pass |
|---|---|---|---|---|---|---|---|
| kept, 10 bps | -0.0 | -0.00 | +0.1 | +21.6 | -4.5 | -14.8 | no |
| kept minus removed, 10 bps | +17.8 | 2.34 | +15.8 | +41.1 | +15.5 | +1.2 | yes |

Book Sharpe at 10 bps: -0.00 vs v1 -0.65 (pass: yes).
The lab's check bar on the kept trades: mean +10.0 at 5 bps, t 1.44, positive in 3 of 4 years (pass: no).

## EXT-FADE-IVR.v1 (filter): FAIL

Kept 3483 of 5131 trades.

| Part | Mean | t | 2020 | 2021 | 2022 | 2023 | Pass |
|---|---|---|---|---|---|---|---|
| kept, 5 bps | +4.4 | 0.72 | +4.7 | +21.2 | +0.2 | -6.1 | no |
| kept minus removed, 5 bps | +2.8 | 0.32 | -0.7 | +19.8 | -0.2 | -2.2 | no |

Book Sharpe at 5 bps: 0.36 vs v1 0.35 (pass: yes).

## The secondary question (not gating)

Among 2933 trades with PXC at most 1: IV >= HV -6.2 (1576) vs IV < HV -2.6 (1357) at 5 bps; difference -3.6, t -0.36: FAIL, the options data adds nothing over prices here.

## Stress (not gating): mean net per unit of position, bps

| Book | at 5 | at 10 | at 15 | entry 0932 at 5 | entry 0932 at 10 | without best 20 sessions | without top 3 stocks | best and worst 20 sessions trimmed |
|---|---|---|---|---|---|---|---|---|
| EXT-FADE.v1 | +3.5 (t 0.70) | -6.5 (t -1.30) | -16.5 (t -3.30) | +0.0 (t 0.01) | -10.0 (t -2.08) | -8.6 (t -1.87) | +0.6 (t 0.11) | +3.5 (t 1.01) |
| EXT-FADE-SW.v1 | +5.9 (t 1.00) | -4.1 (t -0.68) | -14.1 (t -2.38) | +1.9 (t 0.33) | -8.1 (t -1.43) | -8.9 (t -1.63) | +2.7 (t 0.46) | +6.3 (t 1.62) |
| EXT-FADE-P2.v1 | +10.0 (t 1.44) | -0.0 (t -0.00) | -10.0 (t -1.45) | +4.8 (t 0.72) | -5.2 (t -0.78) | -7.3 (t -1.13) | +6.3 (t 0.90) | +11.0 (t 2.52) |
| EXT-FADE-IVR.v1 | +4.4 (t 0.72) | -5.6 (t -0.92) | -15.6 (t -2.56) | +0.6 (t 0.10) | -9.4 (t -1.59) | -11.4 (t -2.05) | -0.2 (t -0.03) | +4.1 (t 1.02) |

| Book | 2020 at 5 | 2020 at 10 | 2021 at 5 | 2021 at 10 | 2022 at 5 | 2022 at 10 | 2023 at 5 | 2023 at 10 |
|---|---|---|---|---|---|---|---|---|
| EXT-FADE.v1 | +4.9 | -5.1 | +15.2 | +5.2 | +0.3 | -9.7 | -5.4 | -15.4 |
| EXT-FADE-SW.v1 | +9.2 | -0.8 | +21.4 | +11.4 | +0.7 | -9.3 | -6.9 | -16.9 |
| EXT-FADE-P2.v1 | +10.1 | +0.1 | +31.6 | +21.6 | +5.5 | -4.5 | -4.8 | -14.8 |
| EXT-FADE-IVR.v1 | +4.7 | -5.3 | +21.2 | +11.2 | +0.2 | -9.8 | -6.1 | -16.1 |

An overlay counts only if EXT-FADE.v1 also passes its check. If v1 fails, an overlay that passes its own standalone part becomes a new strategy and can go forward only as such. Overlays that pass go into the 2024+ hold-out protocol as pre-registered secondary hypotheses to v1, with a Holm correction across them. A failure is final for these data: any change to a spec is a new variant.
