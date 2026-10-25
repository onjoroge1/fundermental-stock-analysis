# Intraday discovery research, 2026-10-22

Tracker item 58. The owner's six gap variants all lost in 2016 (`intraday-lab.md`), so five analysts looked
directly at the discovery bars to see how these 54 large caps actually trade intraday.

**How the study ran**
- **Data:** 2016-01..2017-06, 15,442 eligible stock-days.
- **Lenses**, one per analyst:
  - gap anatomy;
  - time of day;
  - volume;
  - market context;
  - extremes.
- **Skeptics:** one per analyst tried to break each finding. They checked for look-ahead, re-ran every number
  themselves, checked stability by half-year, stock concentration and parameter sensitivity, and asked whether the
  number of tries makes the result likely luck.
- **Held back:** the months 2017-07..2019-12 were not read. They confirm what was found.
- **Results are reported** net of the beta × SPY hedge and of costs (2, 5 or 10 bps a side).

## What the data say

1. **There is little drift after the first half hour.** Once SPY is hedged out, the typical stock-specific move is
   73 bps from 09:30 to 10:00, but only 20–25 bps per half hour from lunch to 15:30, with no half hour drifting more
   than ±0.7 bps on average.
2. **Price action after the open barely predicts the rest of the day.** On gap days the first 5, 15 or 30 minutes
   correlate −0.07 to +0.09 with what follows.
   - Opening-range breaks and failed-break fades earned about 0 bps before costs, over thousands of trades.
   - That is why the six variants lost: every one of their entries (on the way down, a cross of the open, a
     reversal bar) is a decision taken after the open.
3. **What predicts a gap day is known at 09:30:**
   - whether the gap extends yesterday's move;
   - whether it is an earnings day;
   - whether the whole market gapped too.
4. **"Most gaps fill" is false for these names.** By the close the prior close is reached by:

   | Gap | Share that fill by the close |
   |---|---|
   | 1–2% | 40% |
   | 2–3% | 24–32% |
   | 3–5% | about 10% |
   | 5% or more | about 3% |

   Gap-ups drift lower against SPY all day: −8 bps (1–2%), −30 (3–5%), −47 on earnings. Buying gap-up pullbacks
   fights that.
5. **Earnings days behave unlike other days.**
   - Earnings gap-downs keep falling for 26–40 bps between 09:45 and 10:30, as informed sellers keep selling. The
     owner's gap-down buy loses on them and makes +17 bps per trade at 2 bps a side without them.
   - Heavy early volume means "fade" on no-news days and "follow" on earnings days.
6. **Fades build slowly, so tight exits and stops give them away.** The extension fade is +17 bps by 10:00, +31 by
   12:30 and +34 at the close. The 5-minute turn exit cuts it to +11, and every stop tested hurt.
7. **Volume says how far a stock will move, not which way.** The stocks-in-play momentum documented for small caps
   does not carry over to these large caps.
8. **The well-known market timing patterns were absent in this window:** SPY's intraday momentum, the late-day
   reversal, and laggards catching up with their sector.
9. **Costs decide most of it.** Spreads are widest in the first minutes: the median 1-minute range is 34 bps at
   09:30 and 5 at lunch.

## Frozen for one confirm on 2017-07..2019-12

The owner chose two candidates. They were frozen as lab variants **before** the held-back months were read
(`stock_machine/backtest/intraday_lab.py`).

| Variant | Rule | Discovery: trades, before costs, after 5 bps a side |
|---|---|---|
| `EXT-FADE.v2` | A gap of 1%+ the same way as yesterday's 1%+ move, while SPY's own gap is under 0.5%. Fade it from 09:35; out at the gap fill or the close; no stop; at most 3 a session. | 263, +50.9, +40.9 |
| `EXT-FADE.v1` | The control: the same without the SPY filter or the cap, from 09:31. If v2 fails while v1 passes, the SPY filter was overfit. | 547, +31.7, +21.7 |
| `EARN-FH.v1` | On an earnings day released outside market hours: at 10:30, if the first hour pushed against the gap (hedged), follow it to the close. | 109, +43.6, +33.6 |
| `EARN-FH15.v1` | The same, with gaps of 1.5% or more. | 74, +59.3, +49.3 |

**Why these effects might exist**
- **The extension fade:** attention buying after a big up day (and forced selling after a big down day) pushes the
  opening print too far, and liquidity providers are paid as it gives back. When the whole market gaps, the move
  is a real repricing, so there is nothing to fade.
- **The earnings first-hour rule:** earnings opening prints are set in thin pre-market trading and overshoot. Once
  the first hour of institutional trading has pushed back, the open's buyers or sellers are caught on the wrong side.

**The pass bar**, fixed before the confirm months were read. A variant passes only if all three hold at 5 bps a side:
- the mean net per trade is above 0;
- its t-statistic is above 2, with a leave-one-session-out (jackknife) standard error, so one session cannot carry
  it;
- the variant is positive in at least 2 of 2017H2, 2018 and 2019.

A variant that passes goes on to the one 2020–23 check. That has its own bar: the same cost and t, and positive in at
least 3 of the 4 years (`intraday-lab.md`).

**Not carried forward, by the owner's choice:**
- the gap-down buy without earnings days (weak, low-cost only);
- a sector-wide gap fade (mostly the same effect as the extension fade);
- an opening-spike fade (fragile to fill quality);
- a month-end short of losers (18 dates, could be luck).

**The confirm (2026-10-24)** passed only the control, `EXT-FADE.v1`: +26.3 bps net at 5 a side over 1,353 trades,
t 3.80, positive in all three periods. `EXT-FADE.v2` (+5.2), `EARN-FH.v1` (+6.9) and `EARN-FH15.v1` (+4.6) failed.
The refinements shrank most, as warned below (`intraday-lab.md`). **The check (2026-10-25)** then failed `EXT-FADE.v1` on 2020-2023: +3.5 bps net at 5 a side, t 0.70, with its
gross edge down from about +36 to +13.5 bps. All three overlays and the constrained book failed too
(`intraday-lab.md`).

## Warnings

- **These numbers are optimistic.** About 2,500 variants were tried across the lenses, so expect 30–50% shrinkage:
  the extension fade's +50 might really be +25–35 before costs.
- **Few independent days.** The quiet-market extension fade falls from +50 to +13.5 before costs without its 20 best
  sessions. Profits bunch on news days: 2016-11-09 (the election), 2016-06-24 (Brexit), the June 2017 tech selloff.
- **Fat tails.** There are no stops; the per-trade standard deviation is 170–190 bps, and the worst single trade
  lost 892 bps. Size small.
- **Survivorship.** The universe is today's large caps, which flatters longs and hurts shorts in later winners.
- **Fill realism.** The bars are trades, not quotes, and spreads right after the open can exceed 10 bps.
- **A data error found along the way:** IBKR's daily bar for CMCSA on 2017-02-17 printed half the close. The replay
  now excludes it and the two sessions after it (`DAILY_ROW_ERROR`) and keeps it out of the trailing beta, sigma and
  ATR. It also excludes any session whose daily close disagrees with its minute bars by more than 5%
  (`DAILY_CLOSE_MISMATCH`). The discovery numbers above were computed before this fix. The lab's logged explore
  run with the fixed code (log entries 13-16, `intraday-lab-explore-2026-10-22.md`) gives the same trades and the
  same numbers for all four frozen variants.
