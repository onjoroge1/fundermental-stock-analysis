# Intraday lab: exploring gap strategies on the development bars

Tracker item 58, alongside the pre-registered replay (`intraday-replay-protocol-v1.md`). The replay tests fixed
rules once. The lab is where new ideas are tried and refined. It does that without fooling itself, and without
touching the bars that are kept for the final test.

## The rules

| Period | What happens there |
|---|---|
| **Explore, 2016-01..2019-12** | Try and refine variants as often as wanted. Every run of every variant is appended to [`intraday-lab-log.json`](intraday-lab-log.json). |
| **Check, 2020-01..2023-12** | A variant that looks worth keeping is run here **once**, unchanged. |
| **Hold-out, 2024-01 onwards** | The lab never reads it. A variant that survives its check can be written into a pre-registered protocol for the one hold-out run. |

**What enforces this**, in `stock_machine/backtest/intraday_lab.py`:
- **The check runs once.** It needs an explore entry for the same definition made with the same code, and a second check of the same definition is refused.
- **Code changes count as new tries.** The code hash covers the lab, the replay it borrows from and the runner, so changing the code counts as a new try and needs a fresh explore before any check.
- **The check months go through `run()`, which logs before it returns.** The lab's replay refuses those months unless `run()` asks for them. It refuses 2024 onwards always.
- **Every try is counted.** Each summary prints how many variant tries the log holds. The more variants are tried, the more one of them looks good by luck, and the count keeps that visible.

**Run it:**
- `python scripts/run_intraday_lab.py explore` runs the six variants on the finished explore months.
- `… check --variants GU-REV.v1` runs the one check.
- `… log` shows the record.
- A new idea is a new variant id in `VARIANTS`, for example `GU-REV.v2` with a changed parameter. The old one stays in the log.

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
