# Intraday lab: exploring gap strategies on the development bars

Tracker item 58, alongside the pre-registered replay (`intraday-replay-protocol-v1.md`). The replay tests fixed
rules once. The lab is where new ideas are tried and refined. It does that without fooling itself, and without
touching the bars that are kept for the final test.

## The rules

| Period | What happens there |
|---|---|
| **Explore, 2016-01..2017-06** (the discovery months) | Try and refine variants as often as wanted. Every run of every variant is appended to [`intraday-lab-log.json`](intraday-lab-log.json). |
| **Confirm, 2017-07..2019-12** (held back from the discovery research) | A frozen variant is run **once** against the pass bar, which was fixed before these months were read. |
| **Check, 2020-01..2023-12** | A variant that passed its confirm is run **once**, unchanged. |
| **Hold-out, 2024-01 onwards** | The lab never reads it. A variant that survives its check can be written into a pre-registered protocol for the one hold-out run. |

**The pass bars**, both fixed before the confirm months were read:

| Stage | Cost a side | Mean net per trade | t-statistic | Positive periods |
|---|---|---|---|---|
| Confirm | 5 bps | above 0 | above 2 | at least 2 of 2017H2, 2018, 2019 |
| Check | 5 bps | above 0 | above 2 | at least 3 of 2020, 2021, 2022, 2023 |

- **The t-statistic uses a leave-one-session-out (jackknife) standard error.** Each session's trades are dropped
  together, so a news day with many trades cannot carry a variant alone. With one trade a session it is the ordinary
  t. Each table also shows the largest single session's share of the trades.
- **The check bar can be changed only until the first confirm runs.** It is part of the lab's code hash and of each
  definition, so changing it after a confirm stops that confirm's check from ever running
  (`CONFIRMED_WITH_OTHER_CODE`), and the strategy cannot be confirmed again.

**What enforces this**, in `stock_machine/backtest/intraday_lab.py`:
- **Stages run in order.**
  - A confirm needs an explore of the same definition, made with the same code and inputs.
  - A check needs a passed confirm made with the same definition, code and inputs, so the check runs exactly what
    passed. Do not change the lab, the replay or the runner between a confirm and its check: the check would be
    refused (`CONFIRMED_WITH_OTHER_CODE`).
  - The inputs are the tickers, the pinned 8-K data and the pinned daily prices.
- **Each strategy gets one confirm and one check**, keyed on its spec, so renaming a variant or rewording the
  conventions does not reopen it.
- **Code changes count as new tries.** The code hash covers the lab, the replay, the runner and the daily-price
  loader.
- **Each stage reads only its own months,** and run() logs under a lock before it returns.
- **The replay waits too.** The pre-registered replay's previews and development run would read the held-back
  months (`replay_reads_through`):
  - they stop at 2017-06 until the four frozen variants' confirms are logged;
  - they stop at 2019-12 until the checks of those that passed are logged.
- **Every summary prints** the number of variant tries, one-time confirms and one-time checks. Each confirm or check
  report gets its own file, named with its log entry.

**Run it:**
- `python scripts/run_intraday_lab.py explore [--variants …]` runs on the finished discovery months.
- `… confirm --variants EXT-FADE.v2` runs the one confirm.
- `… check --variants …` runs the one check.
- `… log` shows the record.
- A new idea is a new variant id, for example `EXT-FADE.v3`. The old one stays in the log.

**Frozen 2026-10-22** from the discovery research (`intraday-discovery-2026-10-22.md`), before the confirm months
were read, and chosen by the owner:
- `EXT-FADE.v2` (quiet-market extension fade), with `EXT-FADE.v1` as its control;
- `EARN-FH.v1` (earnings first-hour fade), with the 1.5% gap sub-test `EARN-FH15.v1`.

**The logged explore of this code** (2026-10-22, log entries 7-16, `intraday-lab-explore-2026-10-22.md`):

| Variant | Trades | Net bps at 5 a side | t (jackknife) | 2016 | 2017H1 |
|---|---|---|---|---|---|
| EXT-FADE.v1 | 547 | +21.7 | 2.61 | +15.5 | +40.8 |
| EXT-FADE.v2 | 263 | +40.9 | 3.70 | +42.9 | +37.7 |
| EARN-FH.v1 | 109 | +33.6 | 2.52 | +27.1 | +45.7 |
| EARN-FH15.v1 | 74 | +49.3 | 3.20 | +46.5 | +55.4 |

- These are the discovery months, so they only show the code reproduces the research. The confirm decides.
- The six gap variants stay negative at 10 bps a side over the longer explore window.

## The confirm, 2026-10-24: one of four passed

Run once on 2017-07..2019-12 with the explore's code (`518b5557`): log entries 17-20,
`intraday-lab-confirm-2026-10-24.md`. 629 sessions, 27,734 eligible stock-days.

| Variant | Trades | Net bps at 5 a side | t (jackknife) | 2017H2 | 2018 | 2019 | Verdict | Explore, net at 5 |
|---|---|---|---|---|---|---|---|---|
| EXT-FADE.v1 | 1,353 | +26.3 | 3.80 | +19.9 | +39.7 | +11.8 | **PASS** | +21.7 |
| EXT-FADE.v2 | 505 | +5.2 | 0.65 | +20.2 | +4.6 | −1.4 | FAIL | +40.9 |
| EARN-FH.v1 | 178 | +6.9 | 0.52 | +14.4 | −6.3 | +20.8 | FAIL | +33.6 |
| EARN-FH15.v1 | 138 | +4.6 | 0.29 | +28.1 | −11.7 | +18.3 | FAIL | +49.3 |

**What it means**
- **The plain extension fade held up; the refinements did not.** The control, EXT-FADE.v1, earned more than in
  discovery. The quiet-market version, v2, fell from +40.9 to +5.2. This is the reading fixed before the run: if v2
  fails while v1 passes, the SPY filter was overfit. The earnings first-hour rule kept a fifth (v1) or a tenth (the 1.5% version) of its discovery
  edge, too little to tell from luck.
- **EXT-FADE.v1 still has weak spots.**
  - It leans on 2018 (669 of its 1,353 trades, +39.7). 2019 was +11.8 at 5 bps a side and only +1.8 at 10.
  - At 10 bps a side it averages +16.3, and its book's lower bound at 10 bps is about 0.
  - It enters at 09:31, when spreads are widest.
- **Next:** EXT-FADE.v1's one check on 2020-01..2023-12, unchanged. It needs at least 3 of the 4 years positive at 5
  bps a side, with the jackknife t above 2. It runs when the fetch finishes 2023-12.
  - Until then, the lab, the replay, the runner and the daily-price loader must not change, or the check is refused
    (`CONFIRMED_WITH_OTHER_CODE`).
  - The replay's previews and development run stop at 2019-12 until the check is logged.
- **The three that failed are closed.** They cannot be confirmed again; a new idea needs a new variant and its own
  confirm on months not yet read.

## The check, 2026-10-25: EXT-FADE.v1 failed

Run once on 2020-01..2023-12 with the confirm's code (`518b5557`): log entry 21, `intraday-lab-check-2026-10-25.md`.
The three overlays and the constrained book then ran once each on the same trades
(`ext-fade-overlays-check-2026-10-25.md`, `ext-fade-portfolio-check-2026-10-25.md`). Before the runs, the fetcher's
repair pass re-asked IBKR for the 249 short sessions in 2020-2023 and changed none: they are real (listing days,
halts, thin minutes).

| Test | Result on 2020-2023 | Verdict |
|---|---|---|
| EXT-FADE.v1 | 5,131 trades. +3.5 bps net at 5 a side (t 0.70). By year: 2020 +4.9, 2021 +15.2, 2022 +0.3, 2023 -5.4. At 10 bps a side: -6.5. | **FAIL** (t far under 2) |
| EXT-FADE-SW (score sizing) | +5.9 per unit (t 1.00); gain over v1 +2.4 (t 1.85) | FAIL |
| EXT-FADE-P2 (prior move of 2% or more) | Kept trades -0.0 at 10 bps. Kept minus removed +17.8 (t 2.34), positive in every year. | FAIL |
| EXT-FADE-IVR (IV regime) | Kept +4.4 (t 0.72); kept minus removed +2.8 (t 0.32) | FAIL |
| Secondary: IV at or above HV, beyond price data | -3.6 (t -0.36) | The options data adds nothing here |
| EXT-FADE-CB (constrained book) | At equal gross: worst-5%-days loss 1.03x v1's, Sharpe 0.25 lower. Peak gross 47 -> 19 units. | FAIL, as its registration expected |

**What it means**
- **The edge shrank by about two thirds.** v1 earned +13.5 bps per trade before costs in 2020-2023, against +36.3 in
  the confirm and +31.7 in discovery. Costs eat the rest.
- **It also traded far more:** about 1,280 trades a year against about 540 in 2017-2019. The volatile 2020-2022
  markets produced many more 1% extensions, and most of them were weak.
- **The direction is still right more often than not.** The book beat its random-direction twin, with an interval
  above zero, but the move it catches is too small to pay for the trade.
- **One fact held in every year:** larger prior moves fade better (P2's kept minus removed). But even the stronger
  half nets about zero at a realistic 10 bps a side.
- **All five verdicts are final for these data.** v1 goes to no hold-out protocol, and the lab hands nothing on.
  - The lab's months are all used. intraday_replay's previews and development run may now read all of 2016-2023
    (`replay_reads_through` is open).
  - Any new idea, such as a version of the 2% effect built for lower costs, needs data never read: forward data, or
    a new set of stocks (tracker item 80).

## The six variants (v1)

**Every variant shares:**
- a gap of at least 3%;
- a "bounce" of at least 1%: for gap-ups, the high above the 09:30 open; for gap-downs, off the session low;
- entries from 09:35 to 10:29, as a long position;
- signals on 5-minute bars, and fills, stops and exits on 1-minute bars;
- an exit at whichever comes first:
  - the stop;
  - a target of 2× the risk;
  - a **turn against the trade**: a 5-minute bar closing below the previous 5-minute bar's low, sold at the next minute's open;
  - the close.

| Id | Setup | Entry | Stop |
|---|---|---|---|
| GU-LIM.v1 | Gap up, buy the pullback | A resting buy at half the bounce, on the way down | The 09:30 open |
| GU-OPEN.v1 | Gap up, buy the pullback | A 5-minute bar crosses back above the open after dipping below it | The pullback low |
| GU-REV.v1 | Gap up, buy the pullback | A 5-minute bar closes above the high of the red bar before it | The pullback low |
| GD-LIM.v1 | Gap down, buy the reversal | A resting buy at half the bounce off the low, on the way down | The session low |
| GD-OPEN.v1 | Gap down, buy the reversal | A 5-minute bar crosses back above the gap-down open | The session low |
| GD-REV.v1 | Gap down, buy the reversal | A 5-minute bar closes above the high of the red bar before it | The pullback low |

**Shared with the replay:**
- the eligibility rules, the dividend-adjusted gap and the handling of missing minutes;
- the fill rules (strictly through a level, and the inside-the-bar path);
- the scoring: the return net of costs and of beta × SPY over the same minutes, over the stock's own volatility;
- the opposite-direction twin trade that measures direction skill.

**Costs:** results are shown at 10, 5, 2 and 0 bps a side. 10 is the headline. Liquid large caps at IBKR are often
nearer 2–5.

## Before the log started

**The unlogged smoke run.** A first version was run once on the bars then downloaded (2016-01..10), without logging.

**The audit.** That code was then audited on the real bars: each variant was rebuilt with independent detectors,
and a skeptic re-checked every finding. The audit found that the code did not do what the rules say:
- gap-down "bounces" were taken from before the low;
- the gap-down open reclaim fired a bar late;
- the half-bounce buy went live late on fast days;
- the reversal accepted any earlier red bar;
- a few exit edge cases were wrong.

**The fixes.** All of these were fixed to match the stated rules, and the honesty checks above were tightened. The
smoke numbers are not in the log. The log starts with the first run of this code.
