# Trading Office: strategy evidence inventory

Taken 2026-11-13. Sources: this repository at `4f011b5`, and the research records under
`~/code/fsa-research` (`research8/strategy_board/strategies.json`, the ledger
`research8/audit_2026-11-02/LEDGER.md`, and each strategy's own frozen files). Nothing was re-run; the
numbers are copied from those records. Nothing here is advice to buy or sell anything.

## 1. The answer

**No candidate passes all six admission checks of the brief's section 6 today.** Nothing should be switched
to EXPERIMENTAL_PAPER on the strength of this document.

Four candidates pass the evidence checks in the weak sense the brief allows ("a small or in-sample positive
result may justify collecting experimental paper evidence") and are held back by named, fixable items:

| Candidate | Exact version | What blocks admission today |
|---|---|---|
| X2-FLOW v1 | `X2-FLOW_SPEC_v1.md` sha256 `9ebd450a…`, evaluated under `X2_SHADOW_SPEC_v1.2.md` `b231c6b2…` | Cannot run in the cloud app: needs IBKR minute bars, which stay on the owner's Mac (licence). The app has no 09:31 fills, no SPY hedge and no short-borrow policy. No reviewer sign-off yet. |
| X2-STRESS v1 | `X2_SHADOW_SPEC_v1.1.md` section 1 `ebdd1a9a…`, evaluated under v1.2 | Same as FLOW. Also about 88% the same trades as FLOW, so the two must share one risk allocation. |
| QM-1 default | `discovery/exp_qm1/SPEC.md` `81ca9793…`; observation record OBS-QM-3, spec `aedae619…` | No app-side implementation of its rule exists; a port would be new code that must first reproduce the frozen results. No reviewer sign-off yet. |
| Sector P/E fair value `sector_timed_120` | `fair-value-convergence.v1:sector_timed_120:forward.v1` (this repository) | Weakest evidence of the four: its interval includes zero and its registered replay verdict was NOT_SUPPORTED. A third of its trades are shorts, and the app has no borrow policy. No reviewer sign-off yet. |

Everything else is excluded (section 4).

My recommendation, for the owner to accept or change:

- **FLOW, STRESS, QM-1: mirror, do not copy.** They are already in forward observation on the owner's Mac
  under frozen rules. The office registers each as an external deployment that points at the frozen version,
  and the owner's evening run uploads a results summary (no licensed prices) so the office dashboard and QA
  can cover them. The frozen recorders stay the only source of their signals and simulated fills. A second
  implementation in the cloud would use different prices and would be a different, weaker record.
- **`sector_timed_120`: do not admit by default.** It is the only candidate the app could simulate by
  itself, but its evidence does not show an edge. Admit it only if the owner wants the office's paper
  machinery exercised on one real strategy, and then long-only is not an option without a new version
  (dropping the shorts changes the rule).
- **Native experimental book: build and test it on fixtures, start it empty.** "No opportunity" and "no
  active strategy" are valid states.

## 2. The six checks, per candidate

Checks (brief, section 6): 1 positive net result on the declared development sample, reproducible;
2 no unresolved look-ahead, timing, price-basis, accounting or critical data defect; 3 frozen rules, inputs
and cost model, and a supported paper execution implementation; 4 forward horizon, benchmarks, maturity and
stop criteria recorded before the first new signal; 5 a separate reviewer confirms the evidence is fairly
represented and the candidate can be tested; 6 capacity within paper limits, not already active.

| Candidate | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| X2-FLOW v1 | Yes, development only | Open: borrow assumed, real costs unmeasured | **No** in the app (data and execution); yes on the Mac as a modelled shadow | Yes (v1.2, frozen 2026-11-07) | **No**, reviewer role not built | Yes |
| X2-STRESS v1 | Yes, development only | Open: same as FLOW | **No** in the app; yes on the Mac | Yes (v1.2) | **No** | Yes, shared with FLOW |
| QM-1 default | Yes in absolute terms; edge over equal weight inconclusive | Open: universe picked with hindsight (not a timing defect) | **No** in the app; yes on the Mac (OBS-QM-3 recorder) | Yes (OBS-QM-3: first evaluation from 2030-01-02, second from 2032-01-02) | **No** | Yes |
| `sector_timed_120` | Point estimate positive, interval includes zero | Open: survivorship-biased universe | Partly: rules and reward frozen; no order/fill implementation, no borrow policy | Yes (24 active months, registered) | **No** | Yes |

Check 5 fails for every candidate because the independent reviewer is part of PR 3 and PR 4 in the plan.
It cannot be ticked by the author of this inventory.

## 3. Candidate detail

### 3.1 X2-FLOW v1 (research repository)

- **Status there:** forward shadow observation since 2026-11-09. Frozen. Files match their recorded hashes
  on 2026-11-13 (`x2_shadow.py` `c8790af8…`, `protocol_v12.py` `2aa614ff…`, `evalstats.py` `94f22295…`).
- **Rule:** fade an opening gap of at least 2 ATR that extends the previous day's move, entering at 09:31;
  exit at the prior close if touched, otherwise at the close; hedge with beta × SPY. Only when there is no
  earnings release behind the gap, SPY gapped at least 0.3% the same way, and shorts only when SPY is below
  its 50-day average. No stop. Intraday; flat by the close.
- **Instruments and data:** the 54 stocks and SPY; IBKR one-minute bars and daily bars; SEC 8-K item 2.02
  acceptance times (public).
- **Evidence tier: development only, in both periods.** The filters were chosen after the 2016–2026 trades
  had been scored and read. No untouched period exists.

| Period | Trades | Active dates | Mean net bps per trade | t | Longs / shorts | Largest date's share of the total |
|---|---|---|---|---|---|---|
| 2016–23 | 510 | 144 | +55.2 | 3.94 | 454 / 56 | 8% |
| 2024–26 | 205 | 57 | +78.0 | 2.67 | 188 / 17 | 31% |

- **Costs assumed:** 5 bps a side on the stock and 0.5 × beta bps a side on the SPY hedge (about 11 bps a
  round trip), at modelled prices (the 09:31 open; the target or the official close).
- **Known defects and limits:** 82% of its 2024–26 profit came from three crash days. Short borrow is
  assumed available at no cost and has not been verified. Real fill costs are unmeasured (PAPER-COST-1 has
  0 fills). The universe is hand-picked. The search that led here counted 167,203 looks.
- **Forward record so far:** five sessions have passed (2026-11-09 to 2026-11-13). `ledger/` is empty: no
  morning commit and no settle has been recorded. The one logged commit attempt (2026-11-11) stopped with
  `NO_LIVE_SPY_OPEN`. Under the spec a later settle can still score those sessions; the morning commits for
  them cannot be made after the fact. This is an operations gap for the owner to look at, not a result.
- **Registered decision:** one joint test with STRESS at the first session with at least 200 FLOW trades on
  at least 30 dates, or 2029-11-09. Needs mean ≥ 15 bps, family-wise one-sided p < 2.3% (Holm), and a book
  drawdown no worse than −10%. Stated power at a true +40 bps: 18–42% at 30 dates. A "supported" verdict
  only sends it to an IBKR paper account run by the owner.
- **Can a GitHub runner or Vercel run it?** No. **BLOCKED**: the minute bars are licensed to this machine.

### 3.2 X2-STRESS v1 (research repository)

- **Rule:** the same fade, only on stress days (market not in a bull regime, or SPY 20-day volatility above
  22.1%, or SPY down 1.65% or more the day before), and not on earnings days.
- **Evidence tier: development only.**

| Period | Trades | Active dates | Mean net bps per trade | t | Longs / shorts | Largest date's share |
|---|---|---|---|---|---|---|
| 2016–23 | 400 | 129 | +53.3 | 3.19 | 300 / 100 | 10% |
| 2024–26 | 136 | 27 | +108.3 | 3.13 | 122 / 14 | 44% |

- **Known defects and limits:** 89% of its 2024–26 profit came from three crash days. 88% of its 2024–26
  trades are also FLOW trades. Trades come in bursts: 0 in 2021, 131 in 2020. Its 2024–26 record has 27
  dates, fewer than the 30 its own protocol requires to be judged. Costs, borrow, universe, forward record
  and runner status are as for FLOW.
- **Registered decision:** judged with FLOW; needs at least 60 trades on 30 dates or it is NOT EVALUABLE.

### 3.3 QM-1 default strategy (research repository)

- **Status there:** passed its seven pre-registered discovery criteria (2026-10-31); prospective record
  OBS-QM-3 frozen 2026-11-01, running since 2026-11-02 (8 sessions, 0 orders so far, as designed); first
  funded rebalance 2026-12-01.
- **Rule:** on the first session of each month, rank the eligible stocks on the average of 12-1 month
  momentum rank and a quality rank (gross profitability, return on equity, low leverage, from SEC
  companyfacts filed at least one session earlier); hold the top fifth (about 7–10 names) at equal weight,
  bought at the open; no stop; hold to the next rebalance. Long only.
- **Evidence tier: development (2016–2023, 96 monthly rebalances), 11 looks, all spent.**

| Measure | Value |
|---|---|
| Compounded return, 1× | +363.5% (CAGR +21.2%) |
| Maximum drawdown | −38.8% |
| Mean monthly return | +1.83%, interval [+0.40%, +3.25%] |
| Equal-weight portfolio of the same 54, same engine | +300.9% |
| Monthly difference against that equal-weight portfolio | +0.22%, interval [−0.58%, +1.00%]: **inconclusive**; ahead in 49 of 96 months |
| SPY | +168.5% |
| Concentration | NVDA held in 75 of 96 months, 19.6% of contributions (limit 20%) |

- **Costs assumed:** the research engine's M0-TRAIL cost model; a second account doubles opening slippage.
- **Known defects and limits:** the absolute result mainly reflects the hindsight choice of the 54 stocks.
  A survivorship-free replication is blocked on full-history data the owner has not yet bought. Monthly, so
  evidence builds slowly.
- **Can a GitHub runner run it?** The frozen recorder reads IBKR daily bars, so as frozen: no, **BLOCKED**
  on the same licence. A port to the app's own daily prices and public SEC data is possible but is new code
  and a new price basis; it would be a new deployment version that references QM-1 and proves parity on
  the Mac first. It would not be OBS-QM-3.

### 3.4 Sector P/E fair value, `sector_timed_120` (this repository)

- **Status:** forward tracking, registered; 1 month frozen, 0 missed (watchdog 2026-11-13). Information
  only: `strategy_evidence.py` states no strategy is qualified to trade.
- **Rule:** at each month-end, for solid companies whose price is at least 20% from a fair value built from
  earnings × the sector median P/E, take the side toward fair value when price action agrees; hold 120
  sessions. Long and short.
- **Evidence tier: one replay over 2016-01 to 2026-03, which is development.**

| Measure | Value |
|---|---|
| Active decisions | 448 (299 long / 149 short), 120 active months |
| Book mean per month | +0.0075, family-wise 95% interval [−0.0131, +0.0281] |
| Mean per active trade | +0.109, hit rate 54.9% |
| Registered verdict | NOT_SUPPORTED (positive, not significant) |

- **Units:** reward v5, the stock-specific return after costs divided by expected volatility over the hold.
  These are not dollars, percentages or basis points and cannot be compared with the X2 or QM-1 figures.
- **Known defects and limits:** survivorship-biased universe; one of five hypotheses, four of which were
  negative; long holds overlap, so trades are far from independent.
- **Registered decision:** once, on the first 24 months with an active position. A pass only authorises a
  review.

## 4. Excluded

| Candidate | Where | Status in its own record | Why excluded |
|---|---|---|---|
| EXT-FADE-X2, all days | research | Parked | Its one sealed 2024–26 test gave +23.0 bps, t 1.33, short of its bar (608 trades). It lives on only through FLOW and STRESS. Not relabelled. |
| EXT-FADE.v1, its three overlays, the constrained book | this repository, #176 | Closed, FAIL | Failed the one-time 2020–23 check: −6.5 bps at 10 bps a side over 5,131 trades. |
| GU-L gap-up long | research | Closed 2026-11-01 | The profit was market beta; hedged, every candidate averages −9.0 bps (6,848 trades). |
| GD-S gap-down short | research | Closed | Negative in nearly every regime. |
| GU-L AI gate | research | Closed 2026-11-09 | Pilot showed no selection skill; no prospective session was run. |
| FCF-SEL-1 limit fade | research | Archived 2026-10-31 | No drift after the fill; costs about 90% of the loss. |
| GAPML-1 | research | Closed, FAIL | Selected trades −2.2 bps, t −0.35 (1,636 trades). |
| GAPML-2 | research | Closed, FAIL at the development gate | t 0.38 against 2.0 needed. |
| X2 trade selection by a model | research | Closed before any build | Feasibility memo: too few trades to detect a realistic effect. |
| Candle doji fade; doji with RSI | research | Closed | 0 of 84 and 0 of 168 rules passed; −11.5 and −11.9 bps. |
| X2 on quarterly expiry Fridays | research | Closed | 13 and 7 trades; missed its registered bar (p 0.010 against 0.006). |
| Option-selling benchmarks | research | Closed | 0 of 6 beat their market exposure before costs. The app also has no option-chain execution, which the brief says blocks the instrument. |
| UVM-1, PEC-1 | research | Closed, FAIL | Failed their discovery tests. |
| S1 volume breakout | this repository, `event-strategy-replay.v1` | Forward shadow, pending to 2028-10 | Development mean negative (−0.0090 per month, 1,045 trades); hold-out NOT_SUPPORTED. |
| S2 trend pullback | same | same | Development mean +0.0056 with interval [−0.0118, +0.0229] (1,713 trades), then hold-out mean −0.0064, NOT_SUPPORTED. The later period contradicts the earlier one. |
| S3 post-earnings | same | same | Development mean negative (374 trades); hold-out NOT_SUPPORTED. |
| S4 value momentum | same | same, exploratory | Development mean negative (168 trades). |
| Own-history P/E policies (H9–H11), value-gap signals (H6, H7) | this repository | NOT_SUPPORTED | Negative means. |
| Heuristic direction rule | this repository | Replay negative | −0.0017 per month, interval [−0.0365, +0.0330]. |
| Agent bandit paper ledger | this repository | Already in PAPER, evaluation pending | Not a fixed-rule strategy; already has its own registered evaluation. The brief says not to route it through the new lane. |
| Strategy Lab v2 policies | this repository | No eligible policy (run 42399143885) | Nothing cleared its gate. |
| Eight daily technical setups | this repository | Shadow, collecting | No registered positive result exists yet. |
| Paper-fill cost study; FLOW and STRESS on new stocks | research | Measuring; planned | Measurements, not strategies. |

The S1–S4, fair-value and paper-ledger forward tests keep running exactly as registered. Admission to an
experimental lane, now or later, does not touch their protocol hashes, windows or thresholds.

## 5. What the office must show for any admitted candidate

So that weak evidence stays visible (brief, section 6):

- the tier ("development only") next to every figure, and the full test history including failures;
- trades **and** unique dates, and the share of the result carried by the largest date or week;
- for FLOW and STRESS: that they are close to one bet, and that three days carry most of 2024–26;
- for QM-1: that its edge over simply holding the same 54 stocks equally is inconclusive;
- whether fills are modelled from bars, and that none is a broker fill;
- the original registered decision date and criteria, unchanged.
