# EXT-FADE.v1 overlays: registration, 2026-10-24

Tracker item 58. EXT-FADE.v1 is the one lab strategy that passed its confirm (`intraday-lab.md`). It fades a gap of
at least 1% that extends yesterday's move of at least 1%, from 09:31 to the gap fill or the close. A second research
round looked at all of 2016-2019 through four new lenses. It found no second strategy. It found three overlays on
v1's own trades, and they are frozen here before any 2020 data is read. Each gets **one** check on 2020-01..2023-12,
run after v1's own check, against pass bars fixed now.

Machine-readable record: [`ext-fade-overlays-registration-2026-10-24.json`](ext-fade-overlays-registration-2026-10-24.json).
Code: `stock_machine/backtest/ext_fade_overlays.py`, the one-time runner `scripts/run_ext_fade_overlays.py`, the
calibration `scripts/calibrate_ext_fade_overlays.py` and `tests/test_ext_fade_overlays.py`. Information only:
nothing here trades.

## What is frozen

| Id | What it does to v1's trades | Why it might help |
|---|---|---|
| **EXT-FADE-SW.v1** | Sizes each trade by a five-flag score S5: weight = S5 / 3.186. S5 = 0 means no trade. | Puts more money on the big, high-conviction extensions in volatile names, which earned more in both halves. |
| **EXT-FADE-P2.v1** | Keeps a trade only if yesterday's move was at least 2% (v1 asks 1%). | About the same money at 5 bps a side with half the trades, so much more room for costs. |
| **EXT-FADE-IVR.v1** | Keeps a trade only if at least 2 of 3 implied-volatility conditions held at the prior close. | The removed trades made about 0 at 5 bps a side and lost at 10. |

Plus one question that is reported and does not gate anything: within the price-only stand-ins, does IV ≥ HV add
anything? If it does not, the options data adds nothing over prices for this strategy.

Every input is known before the 09:31 entry. The overlays never change a trade's entry, exit, hedge or score. They
only weight it or drop it. Nothing else is frozen: the parked leads (a 125%-of-gap target, holding to the close,
dropping low 09:30 ranges, up-sizing earnings days) are not tested on 2020-2023.

### The five SW flags (one point each)

| Flag | Met when | From |
|---|---|---|
| 1. Prior move | \|prior close / the close before − 1\| ≥ 2% | v1's own prior-move number |
| 2. Gap | \|09:30 open / dividend-adjusted prior close − 1\| ≥ 1.5% | v1's own gap |
| 3. Prior range | (prior session's high − low) / ATR14 ≥ 0.879 | the registered daily price rows |
| 4. Prior late leg | (prior session's last traded close / its 10:29 close − 1) × the gap's sign ≥ 0 | the prior session's minute bars |
| 5. Implied vol | the stock's 30-day ATM IV at the prior close ≥ 28.05% | the IV file |

- **Weights:** S5 = 0 to 5 gives 0, 0.31, 0.63, 0.94, 1.26 and 1.57. 3.186 is the mean S5 over v1's 1,900 trades of
  2016-2019. The beta × SPY hedge scales with the position.
- **Evaluation at equal exposure:** the check divides each weight by the check period's own mean weight, so SW is
  compared with v1 at the same capital.

### The three IVR conditions (keep the trade if at least 2 hold)

| Condition | Met when |
|---|---|
| a. IV ≥ HV | the stock's 30-day ATM IV / its 30-day historical vol ≥ 1, both at the prior close |
| b. Market fear rose | SPY's IV at the prior close / SPY's IV at the observation before it − 1 > 0 |
| c. Volatile name | the stock's IV at the prior close ≥ 28% |

**The secondary question:** PXC counts two price-only stand-ins, SPY's close fell on the prior session, and the
stock's trailing 20-day volatility × √252 ≥ 28%. Among trades with PXC at most 1, compare the mean net at 5 bps of
IV ≥ HV trades with IV < HV trades (trades with both IV and HV known). Which SPY close "fell" reads, adjusted or raw,
is the owner's choice before merge (below). The spec reads the adjusted close for now.

### Missing data

- A flag or condition whose input is missing counts as 0: no daily row for the prior session, no ATR14, no IV or HV
  at the prior close (never an older one), no SPY IV at the prior close or before it, no SPY row for the prior session
  or the one before, no 20-day volatility.
- **Flag 4** is 0 if the prior session is not one of the run's eligible stock-days, or if its 10:29 minute did not
  trade. The lab judges eligibility only within a run's months, so a run's first session has no prior leg. On
  2016-2019 that matches the research. At the check it affects only trades on 2020-01-02.

## A choice for the owner before merge: which SPY close PXC reads

The frozen rule text says SPY's **adjusted** close. The research script that produced the evidence used SPY's **raw**
close. The two differ only on the session after an SPY ex-dividend day, when the raw close drops by the dividend. On
2016-2019 that is 7 of the 1,900 trades, on the sessions after 2016-03-18, 2017-06-16, 2017-09-15 and 2018-03-16.

| SPY close | 2016-17: IV ≥ HV vs IV < HV (trades), t | 2018-19 | 2016-19 |
|---|---|---|---|
| Adjusted (the rule as written; the spec now) | +23.1 vs −7.5 (312 / 176), t 2.01 | +34.5 vs −3.6 (421 / 444), t 3.32 | +29.6 vs −4.7, t 3.79 |
| Raw (the research's evidence) | +23.8 vs −7.4 (311 / 175), t 2.03 | +34.5 vs −4.0 (421 / 442), t 3.34 | +29.9 vs −4.9, t 3.84 |

Net bps per trade at 5 bps a side, among trades with PXC at most 1. Both rows come from the module's own code; the raw
row equals the research's figures trade for trade.

- The secondary does not gate any verdict. Either way the reading on 2016-2019 is the same.
- **Adjusted:** record `"choice": "adjusted"` in the JSON's `owner_decisions.pxc_spy_close`. No hash changes.
- **Raw:** change the spec's `"close"` to `"raw"` in `ext_fade_overlays.py`. That changes the IVR spec and parameter
  digests and the module hash, so the calibration is rerun and this registration regenerated. Then record
  `"choice": "raw"`.
- Until the choice is recorded and equals the spec's, the runner refuses (`OWNER_CHOICE_NOT_RECORDED`).

**Owner's choice:** pending.

## The pass bars, fixed now

Costs are per side. **t** is the lab's leave-one-session-out jackknife (`lab.session_t`): each session's trades are
dropped together, so one busy day cannot carry a result. A kept-minus-removed difference uses the same jackknife.
**Years** are 2020, 2021, 2022 and 2023, and a year counts when its mean is above 0. **Book Sharpe** adds up each
session's P&L over every session with an eligible stock-day (sessions without trades count as 0), then takes mean /
sd × √252.

| Overlay | Every one of these must hold |
|---|---|
| **SW** | **Standalone:** per unit of position, v_i = (w_i / w̄) × net_i at 5 bps, mean above 0, t above 2, positive in at least 3 of 4 years. **Gain over v1:** d_i = (w_i / w̄ − 1) × net_i at 5 bps, mean above 0, t at least 2.0, positive in at least 3 of 4 years. **Book Sharpe** at 5 bps at least v1's. w̄ is the check period's mean weight. |
| **P2** | **Kept trades at 10 bps:** mean above 0, t above 2, positive in at least 3 of 4 years, and the lab's own check bar at 5 bps (`lab.pass_bar(kept, 'check')`). **Kept minus removed at 10 bps:** above 0, t at least 2.0. **Book Sharpe** at 10 bps at least v1's at 10. |
| **IVR** | **Kept trades at 5 bps:** mean above 0, t above 2, positive in at least 3 of 4 years. **Kept minus removed at 5 bps:** above 0, t at least 2.0, positive in at least 3 of 4 years. **Book Sharpe** at 5 bps at least v1's. |
| Secondary (not gating) | Among PXC ≤ 1, IV ≥ HV minus IV < HV at 5 bps: above 0, t at least 2.0. If it fails, record that the options data adds nothing over prices here. |

**Stress tables**, reported and not gating, for v1 and each overlay: entry at 09:32; costs of 10 and 15 bps; without
the best 20 sessions; without the top 3 stocks; the best and worst 20 sessions trimmed; each year.

**What the verdicts mean**
- An overlay counts only if EXT-FADE.v1 also passes its own check.
- If v1 fails, an overlay that passes its standalone part (SW standalone, P2 or IVR kept trades) becomes a new
  strategy and can go forward only as one.
- Overlays that pass go into the 2024+ hold-out protocol as pre-registered secondary hypotheses to v1, with a Holm
  correction across them.
- A failure is final for these data. Changing a spec makes a new variant, which could only be tested on new names or
  new data.

## What the runner enforces

`python scripts/run_ext_fade_overlays.py check` refuses unless all of these hold:
- the module's sha256 equals this registration's, and the module on disk is the one the process imported; the same
  for the runner as it started;
- the three spec digests (wording included) and the three parameter digests equal this registration's;
- the owner's choice for PXC's SPY close is recorded here and equals the spec's;
- the lab's code is the code of EXT-FADE.v1's **check** entry in `intraday-lab-log.json`, that entry exists and is the
  only one, its months are 2020-01..2023-12, and the lab's code has not changed since import;
- the check's tickers, bars and daily prices are the ones v1's check used, every check month is fetched, and the
  replay's behaviour is the registered one;
- the IV/HV files' sha256 equals this registration's;
- its own log, `ext-fade-overlays-log.json`, holds no check entry at all.

**Once only.** Only this check writes that log, and it checks the three overlays together. So any check entry in it
refuses the run, whatever its overlay id, spec id, wording or digest, and so does any entry with these overlays' id or
one of their spec or parameter digests. A reworded or renamed spec cannot get a second check. Each spec has two
digests. The spec digest covers the wording too, so any edit to the text refuses the run until it is registered
again. The parameter digest covers the parameters alone (the kind of overlay, v1's spec, each cut-off, the SPY close,
S-bar, how many must be met, PXC's limit), so it is the same for any rewording. Both are written into each log entry.

It then replays v1 on 2020-01..2023-12 with the lab's own replay (`split='check'`, nothing logged). It refuses
unless the trade count and the mean net at 10 bps equal v1's logged check **exactly**, and unless its own pass over
the stock-days finds the same eligible days as the lab. Only then does it apply the overlays to those trades. It
writes one log entry per overlay under a lock before it returns, then the aggregates to `data/ext_fade_overlays/`.

**What the check reads.** It opens no intraday bar month after 2023-12. The overlays' own daily rows and the IV/HV
series are cut at 2023-12-31. The daily price rows go to the lab's replay, and to the overlays' pass over the
stock-days, uncut, exactly as the lab passed them for v1's own check, because that check must be reproduced exactly.
There the lab's printing-error test judges a row with the next session's open, so the eligibility of the last 2023
session (2023-12-29) reads the 2024-01-02 open. The daily and IV/HV files are parsed whole before they are cut.

## The run order

1. Record the owner's choice for PXC's SPY close (above). Then merge this registration to main in its own PR, with
   the owner's explicit approval, before any 2020-2023 bar is opened. It is not stacked on another PR.
2. Finish the 2020-01..2023-12 intraday fetch.
3. Run EXT-FADE.v1's one-time check, unchanged: `python scripts/run_intraday_lab.py check --variants EXT-FADE.v1`.
4. Run this check once: `python scripts/run_ext_fade_overlays.py check`.

Until step 4, the lab (`intraday_lab.py`), the replay (`intraday_replay.py`), the lab's runner
(`scripts/run_intraday_lab.py`) and `implied_vol_replay.py` must not change. A test pins all four, and
`lab.CODE_SHA` must stay `518b5557…`. The overlay module and its runner must not change either, or the runner
refuses. Recording the owner's choice as "adjusted" changes only the JSON.

## The hashes

| What | sha256 |
|---|---|
| `stock_machine/backtest/ext_fade_overlays.py` | `a1b546803aa3303ef11a7a2b5f401743321a85cad2c556e96f50632195a56c1c` |
| `scripts/run_ext_fade_overlays.py` | `f46fe30e572205103037f7e10544a4f96b94de09914a5142bf159f217ba741af` |
| EXT-FADE-SW.v1 spec (wording included) | `0e3b5ae165e6217661a637f90056ba84e6340a7826c9fa542e506104e52a769d` |
| EXT-FADE-SW.v1 parameters | `dff9ed3c59dcd5f648fdcfa2eae5322b8b77e25cba1232c37edb6011928568a8` |
| EXT-FADE-P2.v1 spec (wording included) | `ba936efba2907617ee54dfca64611b2402e9e1f197fb4229920c727aa51ed3da` |
| EXT-FADE-P2.v1 parameters | `317eb95f15441088d4cf860a14b3f20723e6c73a357bbc7b1110722596c07b5b` |
| EXT-FADE-IVR.v1 spec (wording included) | `66008d6bd23085d9156db1b5bc8d334e415774681721d28391c13fb6eb7f0f42` |
| EXT-FADE-IVR.v1 parameters | `30a3210a46a801d80445e8000701a43e591d3b85497aa1c95961b67ebeb2c101` |
| IV/HV files, `data/ibkr_iv/2026-10-20` (55 files) | `37e9e03bec8806754df868b9c808adea159594e553d8ece69716f82aa05fdffa` |
| Lab code (`lab.CODE_SHA`, v1's explore and confirm) | `518b55575f3cd4d7ba453927eb63cdb4af21989acd1f653f39af552fca5db150` |
| Daily prices (`intraday_replay.DAILY_PRICES_SHA256`) | `2f8bc6ff4e45eb7bb4525cf56b5f22d5cf68a452db207c35e6d2d5b6a8db147f` |

- The IV/HV hash is the same as `implied_vol_replay`'s registered implied-vol input hash: they are the same files.
  The series are IBKR's computed values (current vintage), not observed option quotes, so a re-fetch could restate
  history. That is why they are pinned.
- The four protected files' hashes are in the JSON record.

## How many tries

This round ran about **1,413 evaluations** on 2016-2019, on top of the lab log's **16** variant tries (1,429 in
all). All of 2016-2019 has now been seen, and 2018-19 was visible while most of these overlays' features were picked.
So every overlay is a hypothesis, and the research expected them to shrink:

| Overlay | In-sample (2016-19) | Expected after shrinkage | Chance of passing, as the research judged it |
|---|---|---|---|
| SW | +10.9 bps a trade over v1 at the same capital | +4 to +6 | roughly 40-55% |
| P2 | kept minus removed +36.7 at 10 bps | +15 to +20 | roughly 50% |
| IVR | kept minus removed +44.1 at 5 bps | +15 to +25 | roughly 50%; the secondary lower |

## Calibration: the module reproduces the research

`scripts/calibrate_ext_fade_overlays.py` replays v1 on 2016-01..2019-12 with the lab's own replay (nothing logged),
applies the overlays and compares the results with the research synthesis. It was run on the final module
(`a1b546803aa3…`). Every required figure matches:

| Figure | Research | Module |
|---|---|---|
| Mean S5 over v1's 1,900 trades | 3.186 | 3.1858 |
| SW gain over v1 at equal exposure, 2016-17 | +10.1 | +10.08 |
| SW gain, 2018-19 | +11.4 | +11.38 |
| SW gain, 2016-19 | +10.9 | +10.93 |
| P2 trades kept | 1,016 | 1,016 |
| P2 kept, net at 10 bps | +32.1 | +32.06 |
| IVR trades kept | 1,112 | 1,112 |
| IVR kept, net at 5 bps | +43.3 | +43.27 |
| IVR kept minus removed, at 5 bps | +44.1 | +44.13 |

Checked trade by trade against the research tables, every flag, S5 and every IVR condition agree on all 1,900
trades. PXC agrees on all 1,900 with the raw close and on 1,893 with the adjusted close. v1's own net matches the
research's to within 1e-13 bps.

**By half** (net bps per trade or per unit of position; t is the session jackknife):

| | 2016-17 | 2018-19 | 2016-19 |
|---|---|---|---|
| v1 trades | 660 | 1,240 | 1,900 |
| v1 net at 5 bps | +21.4 (t 2.86) | +26.9 (t 3.64) | +25.0 (t 4.55) |
| v1 net at 10 bps | +11.4 | +16.9 | +15.0 |
| Mean S5 | 3.185 | 3.186 | 3.186 |
| SW per unit at 5 bps | +31.5 | +38.2 | +35.9 |
| SW gain over v1 at 5 bps | +10.1 (t 3.45) | +11.4 (t 3.50) | +10.9 (t 4.66) |
| SW no-trade (S5 = 0) | 10 | 20 | 30 |
| P2 kept | 348 | 668 | 1,016 |
| P2 kept at 10 bps | +30.1 (t 2.49) | +33.1 (t 2.91) | +32.1 (t 3.78) |
| P2 removed at 10 bps | −9.4 | −2.1 | −4.7 |
| P2 kept minus removed at 10 bps | +39.5 (t 2.70) | +35.2 (t 2.89) | +36.7 (t 3.91) |
| IVR kept | 419 | 693 | 1,112 |
| IVR kept at 5 bps | +34.5 (t 3.28) | +48.6 (t 4.65) | +43.3 (t 5.60) |
| IVR removed at 5 bps | −1.3 | −0.7 | −0.9 |
| IVR kept minus removed at 5 bps | +35.8 (t 2.57) | +49.2 (t 3.98) | +44.1 (t 4.69) |
| Book Sharpe at 5 bps: v1 / SW / P2 / IVR | 2.08 / 2.42 / 2.48 / 2.49 | 2.31 / 2.43 / 2.30 / 2.71 | 2.10 / 2.26 / 2.20 / 2.48 |
| Book Sharpe at 10 bps: v1 / SW / P2 / IVR | 1.11 / 1.66 / 1.87 / 1.76 | 1.52 / 1.89 / 1.85 / 2.26 | 1.31 / 1.70 / 1.75 / 1.98 |
| Secondary (adjusted close): IV ≥ HV vs IV < HV within PXC ≤ 1 | +23.1 (312) vs −7.5 (176), t 2.01 | +34.5 (421) vs −3.6 (444), t 3.32 | +29.6 vs −4.7, t 3.79 |

**Dry run of the pass bars on 2016-2019**, with its own four years in place of 2020-2023: all three overlays pass
every part, and so does the secondary. These months chose the overlays, so this proves only that the code applies the
bars as written.

**Stress, 2016-2019** (net bps per unit of position at 5 bps a side unless shown):

| Book | At 5 | At 10 | At 15 | Entry 09:32 | Without best 20 sessions | Without top 3 stocks | Best and worst 20 trimmed | 2016 / 2017 / 2018 / 2019 |
|---|---|---|---|---|---|---|---|---|
| v1 | +25.0 | +15.0 | +5.0 | +20.4 | +9.6 | +19.2 | +21.1 | +15.5 / +31.3 / +39.7 / +11.8 |
| SW | +35.9 | +25.9 | +15.9 | +30.0 | +16.3 | +28.5 | +30.1 | +23.4 / +44.9 / +51.6 / +19.3 |
| P2 | +42.1 | +32.1 | +22.1 | +36.2 | +18.6 | +34.5 | +38.2 | +26.6 / +62.0 / +52.8 / +26.5 |
| IVR | +43.3 | +33.3 | +23.3 | +37.8 | +22.3 | +33.7 | +37.2 | +23.2 / +55.2 / +63.3 / +29.0 |

### Where the module differs from the research scripts

- **The secondary's "SPY fell": the spec reads SPY's adjusted close, as the frozen rule says; the research script read
  the raw close.** On 2016-2019 that changes 7 of the 1,900 trades, all on the session after an SPY ex-dividend day.
  With the raw close the module matches the research on all 1,900 trades. None of the required figures uses PXC. This
  is the owner's choice before merge (see above).
- **The t of a difference** (kept minus removed, and the secondary) is the lab's jackknife, centred on the full-sample
  difference as `lab.session_t` is. The research's dry run centred it on the replicates' mean, and its secondary used
  a session-clustered regression. Kept minus removed agrees to two decimals. With the raw close, on the research's
  own trades, the secondary's t is 2.03 / 3.34 here against the regression's 2.04 / 3.38.

## What has been read

No intraday bar, daily price, or implied or historical vol value dated 2020-01-01 or later has been read for this
work:
- The calibration's bar loader refuses every month after 2019-12, so no 2020 bar file was opened.
- The daily price and IV/HV files hold one JSON document per ticker. They are parsed whole, as the research round's
  loaders parse them, and cut at 2019-12-31 before any value is used; the lab's replay in the calibration gets the cut
  daily rows too. Nothing dated 2020 or later entered a computation, a printout or this record.
- Their sha256 is taken over the files' bytes.
- The tests use synthetic data only.
