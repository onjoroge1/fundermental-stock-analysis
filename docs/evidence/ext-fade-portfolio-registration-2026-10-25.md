# EXT-FADE-CB.v1, EXT-FADE.v1 on a constrained book: registration, 2026-10-25

Tracker item 77 (owner-approved candidate 2). EXT-FADE.v1 is the one lab strategy that passed its confirm
(`intraday-lab.md`). It fades a gap of at least 1% that extends yesterday's move of at least 1%, from 09:31 to the gap
fill or the close. Every trade gets the same notional and its own beta × SPY hedge. This spec keeps v1's trades
exactly as they are and asks one question: **does a book with position limits have a smaller tail than v1's
equal-notional book at the same average exposure, without losing Sharpe?** It is one frozen spec, not a family. It
gets **one** check on 2020-01..2023-12, after v1's own check and after the three overlays' check, against a pass bar
fixed now.

Machine-readable record: [`ext-fade-portfolio-registration-2026-10-25.json`](ext-fade-portfolio-registration-2026-10-25.json).
Code: `stock_machine/backtest/ext_fade_portfolio.py`, the one-time runner `scripts/run_ext_fade_portfolio.py`, the
calibration `scripts/calibrate_ext_fade_portfolio.py` and `tests/test_ext_fade_portfolio.py`. Information only:
nothing here trades.

The spec was designed on 2026-10-24 and reviewed and corrected the same day by an independent skeptic. Neither the
design nor the review read anything dated 2020-01-01 or later. Every cap, cost and evaluation constant was fixed
before the single in-sample preview (2016-19). None was changed after the preview or after the review. The review
changed only build mechanics (data handling, reconciliations, RNG pinning, runner checks). It also added the
end-of-day exclusions to the candidate set (no effect in 2016-19), separated the hold-out family and corrected the
disclosures.

## What is frozen

The spec changes only two things. Signals, entries, exits, Rule 201, the ENTRY_BEYOND_TARGET skip and each trade's
residual are v1's, untouched. **No trade is ever added or dropped.**

1. **Position size.** Each candidate at 09:31 starts at one unit (v1's notional). Once per session, from that
   session's candidates alone, the positions are scaled down in proportion so that:
   - at most **K = 4** units sit on one side of one industry group (a *cluster*);
   - at most **S = 16/3** units sit in one group, both sides together;
   - at most **G = 16** units sit in the whole book.

   The layers apply in that order, narrowest first. Within a layer every affected position shrinks by the same
   factor, and a position never grows. The cluster factor is a_c = min(1, 4 / n_c). The group factor is
   b_s = min(1, (16/3) / the group's gross after a). The book factor is d = min(1, 16 / the gross after a and b). A
   trade's size is s_i = a × b × d.
2. **The SPY hedge.** The book holds one aggregate SPY position. Each trade's hedge, −side × beta × size, is added
   when the trade enters and removed when it exits, with no rebalancing in between. So no hedge ever outlives its
   trade. The SPY the book trades at each event (the entries at a bar's open, the target exits during a bar, the MOC
   at the close) is netted. An entry and an exit in the same bar count as two events; that happened 16 times in
   2016-19.

### The candidates at 09:31

The caps are computed from every stock-day that could have traded at 09:31, not only from the trades that happened.
A candidate is a stock-day where v1's own setup returns a trade, or a skip that happens at the entry (Rule 201, no
entry minute, or the fill already past the target). Its gap is taken at 09:30. It must also be one of these:
- **eligible**, as the lab judges it; or
- **late-excluded**: dropped by the lab only for a daily-row printing error or a daily close that disagrees with the
  minute bars. Those two checks read the session's own close or the next session's open, which nobody knows at 09:31.
  So they must not shape the 09:31 book.

  The stock-day must still pass every check the lab would have made after those two: the corporate-action dates,
  the $10 price floor, the 09:30 bar, the official open against that bar, the trailing history and SPY's day. The lab
  stops at the first reason it finds, so it never makes these checks on a late-flagged stock-day; the module makes
  them itself (`later_exclusion`, tested against the lab's own `eligibility`). A stock-day is therefore a candidate
  exactly when it would be one with its two late checks ignored, and those checks cannot change who is a candidate.

Two dependences are inherited from v1 and are the same for both books. A stock-day with no daily row for the session
itself has no features and is never a candidate. The SPY checks read SPY's own day, including its session row, but
they exclude every stock-day of the session at once, so on such a session v1 has no trade and the caps size nothing.

A skip for a missing SPY open is not a candidate. Late-excluded candidates, skipped candidates and trades the lab
later drops as unscorable all use their share of the cap budget and never trade. That budget is never handed to the
others.

Calibration, 2016-19: 1,905 candidates (1,900 trades and 5 ENTRY_BEYOND_TARGET skips), none late-excluded. The only
late-flagged stock-days are three CMCSA sessions that read its 2017-02-17 printing error (2017-02-17, -21 and -22).
They pass the later checks but do not trigger v1's setup.

### The sector map: 13 GICS industry groups

Each name's GICS industry group under the structure in force from 2018-09-28 to 2023-03-17, as of 2019-12-31, the
last day before the test. That is the point-in-time classification on 2020-01-02, in force for 38.5 of the 48 test
months. Names listed later take their first assignment under that structure. The 2023-03-17 GICS change (TGT, BKNG,
AMZN, COST/WMT, SOFI, UBER) is deliberately not applied: one frozen map.

| Group | Names |
|---|---|
| Semiconductors & Semiconductor Equipment (SEMI) | ADI, AMAT, AMD, AVGO, INTC, KLAC, LRCX, MU, NVDA, QCOM, TXN |
| Software & Services (SOFT) | ADBE, CRM, IBM, INTU, MSFT, NOW, ORCL, PLTR |
| Technology Hardware & Equipment (HARD) | AAPL, CSCO, DELL, HPQ |
| Media & Entertainment (MEDIA) | CMCSA, DIS, GOOGL, META, NFLX |
| Telecommunication Services (TELCO) | T, TMUS, VZ |
| Retailing (RETAIL) | ABNB, AMZN, BKNG, HD, LOW, TGT |
| Food & Staples Retailing (STAPLES) | COST, WMT |
| Consumer Services (CONSVC) | CMG, MCD, SBUX |
| Consumer Durables & Apparel (DURABLES) | NKE |
| Automobiles & Components (AUTOS) | F, GM, RIVN, TSLA |
| Transportation (TRANSPORT) | AAL, DAL, LUV, UAL, UBER |
| Diversified Financials (DIVFIN) | SOFI |
| Health Care Equipment & Services (HCES) | HIMS |

- **Why not the 2016 assignments** (FB in Software & Services, NFLX in Retailing): for 2020-23 they would be stale,
  not more point-in-time, since FB/META was reclassified on 2018-09-28.
- **Why industry groups and not sectors:** at sector level, 23 of the 54 names are Information Technology.
- **Disclosure:** the choice of level was partly informed by knowing that semis and software carry v1's in-sample
  P&L. It does not favour the CB: at sector level R_ES is 1.110, against 1.066.
- The repo's `stock_machine/sectors.py` (SIC ranges) is not used.
- The map was assigned by hand without network access. The least certain names are ABNB (placed with BKNG), HIMS (a
  singleton either way) and UBER (Industrials / Road & Rail). Because the map is frozen, this affects interpretation,
  not validity.
- The module asserts that the map covers the universe exactly. The runner refuses (`UNMAPPED_TICKER`) if any run
  ticker is missing.

### The daily book

- **P&L, per session:** the sum over the session's trades of size × (the lab's hedged residual − 2 × 5 bps), minus
  0.5 bps × the netted SPY traded. It is in bps of one base notional. A session with no trade counts as 0. The
  sessions are every session with an eligible stock-day.
- **Both books pay both costs.** v1's lab figures do not charge the SPY leg; here both books do.
- **The hedge's P&L** is each trade's own lab hedge leg, which is already inside its residual. Netting changes only
  the SPY traded, not the hedge P&L.
- **Convention, disclosed:** within one exit bar, a gapped exit's leg uses SPY's open and a non-gapped exit's leg
  SPY's mid, while the book trades one netted order. That is the lab's own convention.
- **Equal exposure:** k = v1's total entry gross / the CB's, over the check sessions, and the CB's daily P&L is
  multiplied by k. Every risk comparison is this normalised CB against v1. k uses exposure only, never P&L. It must be
  at most 2.5 (the capacity band: $250k on a $100k base).

### The measures

- **Sharpe:** mean / sd × √252.
- **ES5:** minus the mean of the worst ceil(5% × T) days.
- **W10:** the mean of the 10 worst days.
- **DD:** the downside deviation, √(mean of min(x, 0)²).
- **MDD:** the maximum drawdown of the cumulative P&L, starting from 0. It is additive: fixed notional, no
  compounding.

**Intervals:** a paired circular block bootstrap of sessions, with blocks of 21 sessions (one trading month), 10,000
replicates and seed 20261024 (`numpy.random.default_rng`). All block starts are drawn in one call. Both books use the
same resampled sessions, and k is recomputed in each replicate. UB95 is the replicates' 95th percentile and LB95
their 5th.

## The pass bar, fixed now

All at 5 bps a side on stock and 0.5 bps a side on SPY, at equal average gross, on 2020-01..2023-12. **R_ES** is the
CB's ES5 over v1's. **dSR** is the CB's Sharpe minus v1's.

| Gate | Must hold |
|---|---|
| **G1** feasibility | k ≤ 2.5 |
| **G2** tail (primary) | R_ES < 1 **and** its UB95 < 1 |
| **G3** consistency | all three: W10 less severe than v1's, DD below v1's, MDD shallower than v1's (point estimates) |
| **G4** Sharpe non-inferiority | LB95 of dSR > −0.30 |

**PASS** if and only if all four hold. Joined by intersection-union, the joint false-pass rate is at most 5%. A bound
over replicates any of which is undefined is undefined, and fails its gate. None arose in 2016-19.

**Reported, not gating:**
- the worst day;
- the intraday trough's ES5 and worst;
- peak and p99 gross;
- the k_cal (1.086957, frozen from 2016-19 exposure), equal-volatility, time-weighted and k = 1 versions;
- 10 and 15 bps stock costs, and 0 and 1 bp SPY costs;
- the candidates as the post-skip v1 trades only;
- leave-one-year-out and per-year tables;
- how the caps bind;
- concentration (the best 10 days, the top 3 stocks) and the correlation of the two daily series;
- the late-excluded candidates and their sessions.

**What the verdict means**
- The result counts only if **EXT-FADE.v1's own check passes**. There is no standalone path: if v1 fails, the CB's
  result is recorded and does not go forward.
- **PASS with v1 PASS:** the CB enters the 2024+ hold-out as its own pre-registered secondary hypothesis to v1, in its
  own family. It does not join, and does not change, the three overlays' registered Holm family. Its hold-out test is
  this frozen spec and pass bar, unchanged, at one-sided 5%, registered before any 2024 bar is read.
- **FAIL:** final for these data, and v1 stays on its plain equal-notional book. Changing a constant, the map, the
  nesting or the normalisation makes a new variant, testable only on new data or new universes.

## What the runner enforces

`python scripts/run_ext_fade_portfolio.py check` refuses, each refusal named, unless all of these hold:
- **Its own code:** the module's sha256 equals this registration's, and the module on disk is the one the process
  imported. The same holds for the runner as it started.
- **Its spec:** the spec digest (wording included), the parameter digest (constants, map and pass bar) and the
  sector-map digest equal this registration's.
- **The lab:** its code is `518b5557…`, unchanged since import.
- **The overlays' module:** `ext_fade_overlays.py` (on disk and as imported) is the registered one. Its functions
  find v1's check and enforce the run order.
- **v1's check:** `intraday-lab-log.json` holds exactly one check of EXT-FADE.v1. It was made with this lab code, on
  2020-01..2023-12.
- **The run order:** the overlays' one check is in `ext-fade-overlays-log.json` (`OVERLAYS_NOT_YET_CHECKED`
  otherwise).
- **Once only:** its own log, `ext-fade-portfolio-log.json`, holds no check entry, and no entry with this spec's id
  or digests (`ALREADY_CHECKED`).
- **The inputs:** every ticker is in the map. The tickers, bars and daily prices are the ones v1's check used, every
  check month is fetched, and the replay's behaviour is the registered one.

It then replays v1 on 2020-01..2023-12 with the lab's own replay (`split='check'`, nothing logged). It refuses unless
all of these reconcile exactly:
- **v1's trades:** the trade count and the mean net at 10 bps equal v1's logged check.
- **Eligibility:** its own pass over the stock-days finds the same eligible days as the lab.
- **The candidates:** those that end as trades are exactly v1's trades, with the same entry bars, and the skips on
  eligible stock-days are the lab's.
- **Each trade:** re-scored on the module's own candidate, it gives the lab's exit reason, minutes, gap, side and
  residual (to 1e-6 bps).
- **The book:** v1's book at zero SPY cost sums to the lab's nets at 5 bps.

Only then does it size and hedge the book. It writes its one log entry under a lock before it returns, then the
aggregates to `data/ext_fade_portfolio/`.

**What the check reads:**
- **Bars:** no intraday bar month after 2023-12 is opened.
- **Daily rows:** they go uncut to the lab's replay and to the module's pass over the stock-days, exactly as the lab
  passed them for v1's own check, because eligibility must be judged as the lab judges it. The lab's printing-error
  test reads the next session's open, so the eligibility of the last 2023 session reads the 2024-01-02 open. Nothing
  else in the module reads a daily row.
- **No IV or HV data is read.**

## The run order

1. Merge this registration to main in its own PR, not stacked on another, with the owner's explicit approval, on a
   fresh main, before any 2020 bar is opened.
2. Finish the 2020-01..2023-12 intraday fetch.
3. Run EXT-FADE.v1's one-time check, unchanged: `python scripts/run_intraday_lab.py check --variants EXT-FADE.v1`.
4. Run the overlays' one-time check: `python scripts/run_ext_fade_overlays.py check`.
5. Run this check once: `python scripts/run_ext_fade_portfolio.py check`.

Until step 5, none of these may change: the lab, the replay, the lab's runner, `implied_vol_replay.py`, the overlays'
module and runner, and this module and runner. Tests pin all of them byte for byte against main, and `lab.CODE_SHA`
must stay `518b5557…`. **Disclosed risk:** if the overlays' runner is permanently refused, this check cannot run. No
fallback is registered.

## The hashes

| What | sha256 |
|---|---|
| `stock_machine/backtest/ext_fade_portfolio.py` | `4210cf59676880c0509798eb50a1d906de2c4388923484f86586712399df1960` |
| `scripts/run_ext_fade_portfolio.py` | `eafeedf63df31f9912daa14c2294bd4c7d3ad4db93a3e9ee6eb923b425045de9` |
| `scripts/calibrate_ext_fade_portfolio.py` | `54ee5d4cdc9aadd654b8c10a15542e7b4e85e921662b13bbc64e9d31b603aad6` |
| EXT-FADE-CB.v1 spec (wording included) | `510383e08ef863d08b1c3905df884f0a97aa5b7c025e9ca7592a7d4b399e8081` |
| EXT-FADE-CB.v1 parameters (constants, map, pass bar) | `3170c3a8bcaf00f48e26a77498c84c5fe475de41efe919ce25a8511b66440790` |
| Sector map (`contracts.digest(SECTOR_MAP)`) | `61623a16dc09804cb6989ec9bae0283f92d026e93fbac2b723ac8ef894e9e6c4` |
| Lab code (`lab.CODE_SHA`, v1's explore and confirm) | `518b55575f3cd4d7ba453927eb63cdb4af21989acd1f653f39af552fca5db150` |
| Daily prices (`intraday_replay.DAILY_PRICES_SHA256`) | `2f8bc6ff4e45eb7bb4525cf56b5f22d5cf68a452db207c35e6d2d5b6a8db147f` |
| `ext-fade-overlays-registration-2026-10-24.json` | `6a6574ba97b4c770ae6d697cbf77bc1ef229382dfe1981eeb228e1c01bc8f68d` |
| `stock_machine/backtest/ext_fade_overlays.py` (protected) | `a1b546803aa3303ef11a7a2b5f401743321a85cad2c556e96f50632195a56c1c` |
| `scripts/run_ext_fade_overlays.py` (protected) | `f46fe30e572205103037f7e10544a4f96b94de09914a5142bf159f217ba741af` |

The four lab files' hashes (the same as the overlays registration's) are in the JSON record.

## How many tries

- **This freeze:** 1 spec, with 1 in-sample preview. The variant count is **1**, on top of round 2's **1,413**
  evaluations and the lab log's **16** tries (**1,430** in all).
- **The review:** it also evaluated 34 alternative cap, map or candidate configurations and 10 evaluation variants on
  2016-19, plus simulated worlds. None passed and none was adopted. With them the count is 1,474.

## Calibration on 2016-2019 (it proves nothing)

`scripts/calibrate_ext_fade_portfolio.py` replays v1 on 2016-01..2019-12 with the lab's own replay
(`split='confirm'`, nothing logged). It runs the module's own pass over the stock-days, reconciles it with the lab,
sizes and hedges the book, and compares the result with the frozen calibration record. It was run on the final
module (`4210cf596768…`). **Every required figure matches**, and so does the dry run:

| Figure | Record | Module |
|---|---|---|
| Candidates (trades + ENTRY_BEYOND_TARGET) | 1,905 (1,900 + 5) | 1,905 (1,900 + 5) |
| Late-excluded candidates | 0 | 0 |
| v1 trades re-scored within 1e-6 bps | 1,900 of 1,900 | 1,900 of 1,900 |
| v1 book at 5 bps, no SPY cost (= the lab's nets) | 47,438.8985 | 47,438.8985 |
| Netted SPY cost per trade-unit, v1 / CB (unnetted v1) | 1.371 / 1.367 (1.437) | 1.371 / 1.367 (1.437) |
| Sessions where the cluster / group / book layer binds | 36 / 0 / 12 | 36 / 0 / 12 |
| Mean s; share of positions with s < 1; minimum s | 0.920; 23.4%; 0.309 | 0.920; 23.4%; 0.309 |
| k | 1.086957 | 1.086957 |
| v1: Sharpe, ES5, MDD | 2.0019, 574.6364, −3,756.4702 | 2.0019, 574.6364, −3,756.4702 |
| CB at equal gross: Sharpe, ES5, MDD | 2.0319, 612.3747, −3,963.358 | 2.0319, 612.3747, −3,963.358 |
| UB95 of R_ES; LB95 of dSR | 1.1058; −0.1203 | 1.1058; −0.1203 |
| Dry run: G1 / G2 / G3 / G4, verdict | yes / no / no / yes, FAIL | yes / no / no / yes, FAIL |

**v1 against the CB at equal gross, 2016-19**, in bps of one base notional per session (1,006 sessions):

| | Sharpe | Mean/day | sd/day | ES5 | W10 | Worst day | DD | MDD |
|---|---|---|---|---|---|---|---|---|
| v1 | 2.00 | +44.6 | 353 | 575 | −1,031 | −1,343 | 156.9 | −3,756 |
| CB | 2.03 | +39.8 | 311 | 612 | −1,078 | −1,441 | 166.3 | −3,963 |

- **The ratios:** R_ES 1.066 (UB95 1.106); dSR +0.030 (LB95 −0.120).
- **90% intervals:** DD ratio 1.017-1.103, MDD ratio 0.954-1.183, W10 ratio 0.971-1.110.
- **By half:**
  - 2016-17: R_ES 1.000 (UB95 1.041), dSR +0.075. It fails G2 only.
  - 2018-19: R_ES 1.115 (UB95 1.161), dSR −0.066. It fails G2 and G3.
- **Leave one year out:** R_ES 1.094 / 1.076 / 1.038 / 1.054 without 2016 / 2017 / 2018 / 2019.
- **How the caps bind:** they bind on 36 of 615 active sessions. Those sessions hold 49% of v1's P&L but only 9% of
  its ES5 loss: 4 of the 51 worst days.
- **Other normalisations (R_ES):** entry gross 1.066, time-weighted 1.065, k_cal 1.066, equal volatility 1.212, k = 1
  0.980. Without normalisation the caps remove 8% of gross but only 2% of ES5. Equal average gross is the fair middle
  choice; equal vol is harsher on the CB.
- **Costs:** at 10 bps a side the CB's Sharpe trails v1's (1.10 against 1.20). At 15 bps G4 fails too.
- **Gross:** peak gross falls from 36.0 to 17.4 units (normalised), and p99 from 23.0 to 17.4. The worst intraday
  trough goes from −2,417 to −1,973, and the trough's ES5 from 839 to 860.
- **Concentration:** the best 10 days carry 51% of v1's P&L and 46% of the CB's. The top 3 stocks carry 35% and 34%.
  The two daily series correlate at 0.963.
- **The hedge:** netting saves about 5% of the SPY cost, because 87% of trades fall on one-sided days. The hedge's
  value is operational; it is not a source of risk improvement.

**The review's sensitivity** (no constant changed):
- **The full grid**, K {3, 4, 5} × S {4, 16/3, 20/3} × G {12, 16, 20}:
  - R_ES 1.035-1.096, UB95 1.059-1.156, dSR −0.019 to +0.072;
  - 0 of 27 points pass: G2 and G3 fail at every point, and G4 passes at every point;
  - S has no effect anywhere in that range.
- **Single layers:** cluster only 1.044, book only 1.044, group only 1.018. Looser caps move R_ES toward 1 from above
  and never below it.
- **Other maps:** 2016-structure groups 1.068; GICS sectors 1.110; the repo's `sectors.py` 1.071; one group per side
  1.266.
- **Evaluation constants ±25%** (ES level 3.75% / 6.25%, block 16 / 26, NI margin 0.225 / 0.375, other seeds): no
  gate changes.

**Validity**, from the review on 2016-19:
- **Size:** in worlds resampled from 2016-19 with the true R_ES set to exactly 1, G2 passed 4.4% of 1,000 four-year
  draws, against a nominal 5%.
- **The in-sample world:** where R_ES is 1.066, G2 never passed, and UB95 covered the true ratio 90.4% of the time.
- **Power:** in a world with R_ES 0.928, G2 passed 75%.

## Why the constants are what they are

These are judgement, stated before the preview; none was searched on 2016-19.
- **One unit per trade**, as in v1. Stock-dependent caps were rejected: they would re-weight v1's cross-section, which
  is SW's ground.
- **K = 4.** An assumed stress correlation of 0.5 for same-side names in one group on a crowded day, plus an
  80%-of-ceiling rule: the effective number of bets reaches 80% of its ceiling at 4 names. Both are judgement; with a
  90% rule the same derivation gives 9.
- **G = 16.** The same 80% rule with a 0.2 correlation across groups. Also 4× day-trading buying power at 25% of
  capital per base position (a single-name 10% adverse move costs 2.5% of capital). These are two judgements that
  give the same number, not independent evidence. Round 2's cap grid (3-12.5), and its finding that tight caps hurt
  in-sample, were visible.
- **S = G / 3.** At the full budget the book must span at least three groups. With K = 4 it binds only on two-sided
  groups.
- **K_MAX = 2.5:** the capacity band, $250k / $100k.
- **Costs:** 5 bps a side is v1's check-bar cost. 0.5 bps on SPY covers spread, commission and impact, rounded up.
- **ES at 5%:** about 50 tail sessions. W10 is about the 1% tail.
- **Bootstrap:** block 21 (a month), 10,000 replicates, seed = the freeze date. One-sided 95% bounds.
- **NI margin 0.30 Sharpe:** about 15% of v1's in-sample Sharpe, and 2-3× round 2's paired standard error.

## Risks and recommendation

1. **Expect a FAIL.** The mechanism is structural. Equal-average-gross normalisation levers every uncapped day by k,
   and the caps bind on crowded days, which carried v1's profit, not its tail.
2. **A pass that counts is unlikely, in the low single digits of percent.**
   - In crash-episode worlds, a random 10-50% of the capped sessions was turned into losses (40 draws each). v1
     passed its own bar in 39/39/33/24/14 draws; the CB in 0/0/1/4/14; both together in 0/0/0/0/1.
   - Under uniform stress, the bar first passes where v1's Sharpe is 0.12 and v1 fails its own check.
   - The test is valid (size 4.4%), but it is close to unreachable together with a v1 pass.
3. **Do not swap the endpoint now.** The caps' visible benefits (lower peak gross, a shallower worst intraday trough,
   Sharpe unchanged) are about margin and capacity. Re-registering around them now would choose the endpoint after
   seeing the preview.
4. **The group layer** has no effect on one-sided days. This is disclosed, not tuned.
5. **The judgement constants:** the two correlations, the 80% rule and the 25% position size are judgement. The
   sensitivity shows they do not decide the in-sample answer.
6. **The sector map** is unverified for ABNB, HIMS and UBER, and the 2023 GICS change is not applied.
7. **Low power:** the bootstrap sd of R_ES is about 0.025, so a true improvement under about 4-5% usually goes
   undetected.
8. **Costs:** at 10 bps the CB's Sharpe trails v1's by 0.10 in-sample. Netting the SPY hedge saves only about 5%.
9. **k is ex post** (exposure only). k_cal, the version tradable ex ante, is reported.
10. **The result depends on v1**, and 2019 was v1's weak year. The test spends no new data.
11. **The run order:** the CB is blocked if the overlays' runner can never succeed.
12. **Factor risk:** the SPY hedge leaves semis and software factor risk on the many uncapped days. A sector-ETF hedge
    is not testable on the current data (no ETF minute bars) and is not part of this spec.

**Recommendation:**
- Build and register now only if the owner wants item 77 closed with a clean, pre-registered answer. It is cheap and
  spends no new data, and the time before any 2020 bar is opened is the only time it can be clean.
- Running waits for v1's check and the overlays' check, which need the finished 2020-23 fetch.
- If the owner's real goal is lower peak leverage or margin, adopt the caps as an operating policy without claiming
  a risk improvement. That needs no test.

## Where the build differs from the design text

- **File names.** The design named the files `ext_fade_book.*` and `ext-fade-book-log.json`. The build uses
  `ext_fade_portfolio.*`, `ext-fade-portfolio-log.json` and `data/ext_fade_portfolio/`, as the build request asked.
- **One added refusal, `OVERLAYS_CODE_NOT_REGISTERED`.** The runner relies on the overlays' module to find v1's check
  and to enforce the run order, so that module must be the registered one.
- **Stronger reconciliations.**
  - The candidate check also requires the skips counted on eligible stock-days to equal the lab's.
  - The trade check also requires each trade's gap and side to equal the lab's.
- **Condition 2 is corrected, so the candidates are point in time as the spec says.** As first written, a
  late-excluded candidate needed only its features, its 09:30 bar and SPY's day. Because the lab stops at the first
  reason, a stock-day that a check known before the open would drop (a corporate action, the price floor, the
  official open, too little history, SPY's day) became a candidate whenever it also carried a late flag. That flag can
  come from the session's own close or the next session's open, which contradicted the spec's own claim. The build's
  review found it on the synthetic world. The fix makes condition 2 also require every check the lab makes after the
  two late ones.
  - **Effect:** none in 2016-19. There were no late-excluded candidates, and the 3 late-flagged stock-days (CMCSA) pass
    the later checks and do not trigger the setup. The calibration is unchanged, figure for figure. In 2020-23 it can
    matter wherever a check known before the open applies: sub-$10 stretches (F, AAL, SOFI, HIMS), new listings
    without history (PLTR, ABNB, RIVN, SOFI, HIMS) and the IBM and T corporate-action days. These are named from
    general market knowledge and the replay's own corporate-action list; no 2020+ data was read to find them.
  - **Wording:** the spec text changed with it (condition 2, what the candidates read, and the history), so the spec
    digest changed. The constants, the map and the pass bar did not, so the parameter digest is the same.
  - **Owner's sign-off:** this edits the frozen text of condition 2, so it needs the owner's sign-off, which the
    registration's merge asks for anyway. If the owner prefers condition 2 as first written, the fallback is to
    restore it and correct the claim instead, stating that a late flag can then admit a stock-day that a check known
    before the open would drop.
- **The intraday trough.** Each trade counts from its entry bar, is frozen at its exit, and the session's trough is
  never above 0. The designer's preview charged costs from 09:31 for a trade not yet entered and held float32 paths.
  This build gives trough ES5 839.4 / 859.7 and worst −2,417.5 / −1,973.4 (v1 / CB), against the preview's 839 / 859
  and −2,417 / −1,973. It is reported, not gating.
- **p99 gross** is taken over the sessions with a trade.

## What has been read

No intraday bar, daily price, IV or other value dated 2020-01-01 or later has been read for this work:
- **Bars:** the calibration's bar loader refuses every month after 2019-12, so no 2020 bar file was opened.
- **Daily prices:** the files hold one JSON document per ticker. They are parsed whole, as the research loaders parse
  them, and cut at 2019-12-31 before any value is used. The lab's replay and the module's pass both get the cut rows.
  Their sha256 is taken over the files' bytes.
- **Events:** the calibration does not hand the lab's replay the 8-K event file (EXT-FADE.v1 does not read it), so no
  event dates were loaded either.
- **Tests:** they use synthetic data, except one that reruns this calibration on the pinned 2016-19 data and is
  skipped when that data is absent.
