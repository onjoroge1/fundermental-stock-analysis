# Intraday first-hour replay: protocol draft, 2026-10-20

Tracker item 58. Everything else in this project tests 20-session directional calls. This tests whether trading
the first hour of the session, the way Meir Barak teaches it, or the published opening-range breakout, beats
not trading once costs are paid. Several other published intraday effects are tested alongside, as exploratory
checks.

**Status: DRAFT.** Nothing is registered yet. The thresholds below are fixed now, taken from Barak's published
rules and from the papers. After the development run, only fixes for errors in the code or the data handling may
change, and each fix is listed here before registration. The hold-out bars are fetched only after
registration.

## Identity

| | |
|---|---|
| Protocol id | `intraday-first-hour.v1` |
| Module | `stock_machine/backtest/intraday_replay.py` |
| Runner | `scripts/run_intraday_replay.py --stage dev` (then `--stage holdout`, once) |
| Fetcher | `scripts/fetch_ibkr_intraday.py` (read-only market data from a local IB Gateway; refuses hold-out months without `--allow-holdout`) |
| Protocol sha256 | set at registration (the draft's current value is `PROTOCOL_SHA256` in the module) |
| Behaviour pin (replay, both stages' reports and the verdict rules, on synthetic bars and books) | `BEHAVIOR_SHA256` in the module; re-pinned with any code fix before registration |
| Daily prices (pinned) | `data/ibkr_prices/2026-10-20`, sha256 `2f8bc6ff…` (registered by the implied-vol comparison) |
| Earnings dataset (pinned) | `docs/evidence/earnings-announcements-v1.json`, sha256 `6937f99a…` |
| Development | 2016-01..2023-12 (96 months), descriptive |
| Hold-out | 2024-01..2026-08 (32 months), run once |

## Data, and keeping it small

- **Bars:** IBKR TRADES 1-minute bars, regular hours, for the 54 covered stocks and SPY:
  - fetched a week per request; any session that comes back short is asked for again on its own, and a
    request that times out is retried rather than kept;
  - split-adjusted, rounded to cents and not dividend-adjusted;
  - a bar with no trades (IBKR's placeholder) counts as a missing minute.
- **Daily bars:** the IBKR daily TRADES and ADJUSTED_LAST bars (`data/ibkr_prices/2026-10-20`) supply the
  prior close, the dividend factor, beta, ATR and the official close.
- **Earnings sessions:** the pinned SEC 8-K item 2.02 dataset.
- **Completeness:** every ticker-month needs a finished fetch (a final manifest). The development run refuses
  otherwise, except as a labelled preview; the hold-out refuses outright.
- **Storage:** files are split by stock and month (`data/ibkr_intraday/v1/<TICKER>/<YYYY-MM>.csv.gz`, about
  140 KB each), so a fetch resumes where it stopped and space stays bounded:
  - development is about 0.75 GB;
  - the hold-out is about 0.25 GB, in its own folder.
- **The bars never leave the machine.** IBKR's terms restrict redistributing its data, so only aggregates are
  committed: means, intervals, counts and input hashes. No per-trade prices.

## The strategies

**Confirmatory: five tests, with a family-wise interval** (two-sided, Bonferroni over five, z = 2.5758):

| | Setup | Entry | Stop / target | Exit |
|---|---|---|---|---|
| **G1** Gap and go (Barak) | Gap ≥ 3%. A counter-move against the gap (from the first bar closing against it) fails at minutes 3–9: no new extreme, and a close beyond the previous bar's low (for a gap down) or high (for a gap up). The counter-move has filled less than half the gap. | With the gap, next bar's open | Counter-move extreme / 1R | 10:30 |
| **G2** Gap fill (Barak) | Gap of 1–3%, not yet filled. A 5-minute bar from 09:35–10:14 closes against the gap, beyond the previous 5-minute bar's extreme, with the prior close at least 1R away. | Against the gap, next open | That bar's extreme / the prior close | 10:30 |
| **B1** Break of structure (Barak) | Gap ≥ 2%. The first 5 minutes' range. | Stop order beyond it in the gap's direction, 09:35–10:15 | Other side of the range | 10:30 |
| **C1** The "h structure" | Four consecutive bars, each narrower than the last relative to that minute's normal range (mean over the prior 20 sessions), all at most 0.6 of normal, ending 09:33–10:14. | Stop orders both ways beyond the run's high and low, live for 10 bars; one cancels the other | Other side of the run | 10:30 |
| **X1** Opening-range breakout on stocks in play (Zarattini, Barbon & Aziz 2024) | ATR(14) ≥ $0.50; first-5-minute volume ≥ its 14-session mean; the 5 highest such ratios that session. The direction comes from the first 5-minute candle. | Stop order beyond its range, from 09:35 | 10% of ATR from the fill | Official close |

**Exploratory: 95% intervals, never a verdict:**

| | Effect | Trade |
|---|---|---|
| **E1** | Earnings-day continuation | PRIMARY 8-K releases accepted before 09:30, or at 16:00 or later the day before; a residual move by 10:00 of at least one daily sigma; with it, 10:00 to the close. About 15% of releases (mostly pre-open press releases whose 8-K came mid-morning) are not traded. |
| **M1** | Intraday momentum (Gao, Han, Li & Zhou 2018) | SPY, in the direction of its move from the prior close to 10:00 (none when unchanged), 15:30 to the close |
| **R1** | End-of-day reversal (Baltussen, Da & Soebhag) | Rank every stock with a 14:59 close by its move from the prior close to 15:00: long the lowest fifth, short the highest fifth, 15:30 to the close |
| **A1** | Attention-driven gap fade (Berkman, Koch, Tuttle & Zhang 2012) | No earnings that session or the one before; an overnight residual of at least 2 sigma (60 sessions) and a gap of at least 1.5%: short, 09:35 to the close |

**Claim checks.** Barak's own numbers are checked against the development data, as descriptions only:

| Claim | Checked as |
|---|---|
| G1 wins 68% of the time and averages +0.38R | Target rate and gross R |
| 80% of 1–3% gaps fill | The unconditional fill rate by 10:30 and by the close, over every 1–3% gap |
| 80% of breakouts fail | B1's stop rate |
| The paper's ORB earns about +0.08R gross | X1's gross R |

## Shared conventions

**Eligible stock-days.** SPY's daily sessions are the calendar. Every stock-session on it is either eligible
or excluded with a reason, including sessions with no minute bars at all (`PRE_LISTING`, `NO_DAILY_BARS`,
`NO_MINUTE_BARS`); exclusions are reported per ticker. Every strategy draws only on eligible stock-days, and they
are each session's denominator. A stock-day is eligible when all of these hold:
- the stock is listed (HIMS from 2021-01-21, SOFI from 2021-06-01, the SPAC shells before);
- its two prior daily rows are the two prior sessions (`STALE_PRIOR_CLOSE` otherwise);
- it is not a listed corporate action: IBM 2021-11-04 (Kyndryl spin-off, the prior close not adjusted for it)
  and T 2022-04-11 (the first session after the WarnerMedia distribution);
- its split-adjusted prior close is at least $10, which bounds cent rounding at 10 bps;
- the 09:30 bar traded, and its open is within 0.2% of the daily open;
- it has at least 15 prior sessions of bars;
- SPY's session is present.

**What the $10 floor costs.** Prices are split-adjusted, so stocks that split later lose their early years even
though they always traded above $10: NVDA about 56% of its 2016–23 sessions, CMG 36%, KLAC 24%, LRCX 11%,
NFLX 7%. These are future winners, so the floor leans the sample away from them; the per-ticker exclusions
show it. AMD and F are genuinely under $10 at times. The ATR $0.50 and C1 $0.05 thresholds use the same
adjusted prices.

**Known gaps in coverage.** UAL's IBKR daily history starts 2018-09-10, so its earlier sessions are
`NO_DAILY_BARS`; AVGO starts 2016-02-02.

**Prior close and gap.**
- The prior close is the split-adjusted close times the dividend factor: the ADJUSTED_LAST/close ratio of the
  prior session over that of the session.
- The factor is set to 1 within the rounding bound (0.0002 + 0.01/adjusted price + 0.01/price). Above 1 plus
  the bound it is counted as an anomaly and set to 1.
- Gap = 09:30 open / prior close − 1.

**Fills.**
- A signal is known at its bar's close. A market entry fills at the next bar's open.
- A stop order is live from the next bar. It fills only when price trades strictly through the level, at the
  level, or at the bar's open if the bar gaps through.
- A stop and a target in the same bar: the stop.
- **Inside a bar,** prices are taken to run open, low, high, close for an up bar, and open, high, low, close for
  a down bar. A stop order's fill bar is checked for the stop only, along that path after the fill. So a bar
  that opened below a long's stop, rose through the entry and closed above the stop is not a stop; one that
  closed back below it is. A C1 bar through both levels fills the level its path reaches first, and is then
  stopped inside it.
- **The whole-bar check** (any low under the stop counts) is reported as a diagnostic. It only ever hits the own
  trade, not the twin, so it would bias direction skill down; it moved X1's gross R by about 0.1 on early data.
- First-hour entries run from 09:31 to 10:15; the forced exit is the 10:29 close (the last earlier close if
  that bar is missing).
- A missing minute carries the position to the next bar.

**Short-sale rule (Reg SHO 201).** There is no short entry if any of these hold (skipped and counted):
- the prior session's low was at least 10% below the close before it;
- the open is at least 8% below the prior close (taken as a pre-market trigger);
- any bar up to the signal traded at least 10% below the prior close.

This applies to R1's short leg as well. C1 keeps its buy stop when its sell stop is barred. The twin is never
restricted.

**Skips** are counted by strategy and reason: `RULE_201`; `ENTRY_BEYOND_STOP` and `ENTRY_BEYOND_TARGET` (a G1 or
G2 entry whose open is already past its stop or target); and `UNSCORABLE` (no volatility scale yet, or SPY's
bar is missing). A skipped stock-day stays in the denominator.

**One trade** per stock, strategy and session: no adds, partials or re-entries. **Costs** are 10 bps a side.

## Scoring

**Reward (reward v5 on intraday windows):**

  (side × stock return − side × beta × SPY return over the same minutes − 20 bps) / the stock's realized
  volatility over that strategy's window

- **SPY's price** is taken:
  - at its bar's open for an open fill;
  - at the bar's midpoint for a fill inside a bar;
  - at the bar's close for a timed exit;
  - at the official close for a close exit.
- **Beta:** 63-session daily beta, clipped to [0, 3].
- **Volatility:** the prior 20 sessions' realized variance per minute over the window, times the window's
  minutes (so half days and full days share one scale), floored at 5% a year.

**The random-direction twin.** Every trade has a mirror trade taken the other way at the same fill, with its
stop and target reflected through the entry. The twin splits each result into two parts:
- the part any bracket of that shape earns, whichever way it points: control = (own + mirror) / 2;
- the part the direction adds: direction skill = (own − mirror) / 2.

**The daily book.** One value per session: the sum of that session's rewards over its eligible stock-days. A
session without a trade scores 0, so the book asks whether trading beats not trading.

**Statistic:**
- the mean over sessions;
- a Newey-West standard error with lag ⌊4(n/100)^(2/9)⌋ (6 for the hold-out);
- t(n−1).

## Verdicts (hold-out, confirmatory only)

| Verdict | Condition |
|---|---|
| `SUPPORTED` | At the 10 bps cost, the mean and its family-wise lower bound are above 0, the direction-skill book's family-wise lower bound is above 0, and coverage is met. |
| `INSUFFICIENT_COVERAGE` | Fewer than 150 trades, trades in fewer than 18 of the 32 months, fewer than 15 stocks, or one stock above 25% of trades. |
| `NOT_SUPPORTED` | Otherwise. |

**Labels:**
- `ROBUST` when the monthly book's lower bound (Newey-West lag 3) and its mean without the best month are both
  above 0, else `FRAGILE`;
- `COST_SENSITIVE` when the result clears 0 at 10 bps but not at 20 bps.

**Diagnostics, reported but never deciding:**
- zero and doubled costs; the whole-bar fill check;
- long and short sides, earnings sessions and others, each year;
- exit reasons, stops inside the fill bar, bars through both C1 levels;
- every result's 80%-power detectable difference.

## Power, before any result

- **Expected trades:** gaps of 3% or more occur on a few percent of stock-days. Over the hold-out's ~670 sessions
  and ~50 eligible stocks, G1 should see a few hundred trades, and B1 and G2 more.
- **Detectable edge:** at the family-wise t of about 2.58, the 80%-power detectable edge is (2.58 + 0.84) × the
  per-trade spread / √N: about 0.20 reward units at 300 trades and 0.15 at 500, if the spread is near 1.
- **Why it is likely worse:** trades happen on gap days, which are more volatile than the trailing scale, and
  they cluster (earnings seasons, market-wide shocks), which lowers the effective N. A pass also needs
  direction skill on top. Expect 0.15–0.3 reward units per trade, roughly 15–30 bps on a 1% first-hour
  volatility, against a 20 bps round-trip cost.
- **What NOT_SUPPORTED means:** smaller edges cannot be told apart from zero, so NOT_SUPPORTED is not evidence of
  no edge.
- **Next estimate:** the development run reports the realized detectable differences.

## What it is not

- **Only pseudo-out-of-sample.** The hold-out's daily bars (2024-01..2026-08) are already on disk and were used
  by other tests, so its gaps and daily moves have been seen. Its minute bars have not. The rules were fixed from
  Barak's and the papers' published values, not from those daily bars. The hold-out label says so:
  `INTRADAY_HOLDOUT_PSEUDO_OUT_OF_SAMPLE`.

- **No live quotes or queue position.** It replays trade bars with a flat 10 bps a side. It never sees a live
  quote, a spread or a queue position. A measured-spread check needs quote data and is deferred.
- **Survivors only.** It uses today's coverage list. Stocks that delisted or fell out of favor are missing,
  which flatters any long-biased rule.
- **Information only.** A pass authorizes review, never trading (`promotion: NOT_AUTHORIZED`,
  `broker_submission: false`). Nothing here places, routes or sizes an order.

## Steps

1. **Development fetch** (running since 2026-10-21, one worker, about two days: IBKR serves roughly one
   stock-month every 30 seconds, and a second worker adds nothing): 2015-12..2023-12. December 2015 only
   warms up the history. Then `--repair` re-asks, session by session, for any short session.
2. **Development run:** `scripts/run_intraday_replay.py --stage dev`, descriptive. It writes
   `data/intraday_replay/dev-<date>.json` (aggregates only), which is committed as
   `docs/evidence/intraday-replay-dev-<date>.json` with a written summary beside it. Before the fetch finishes,
   `--allow-incomplete` gives a labelled preview.
3. **Registration:**
   - commit `REGISTERED_PROTOCOL_SHA256`, `BEHAVIOR_SHA256` and `REGISTERED_DEV_INPUTS` (the bar files' hash);
   - list here any fixes made after the development look.
4. **Hold-out fetch:** `--months 2024-01:2026-08 --allow-holdout --out data/ibkr_intraday/v1-holdout`, then
   register its hash (`REGISTERED_HOLDOUT_INPUTS`).
5. **Hold-out run:** `--stage holdout`, once. It refuses until steps 3 and 4 match the code.

## Reproduce

- `pytest tests/test_intraday_replay.py tests/test_fetch_ibkr_intraday.py` (no IBKR needed; the pin test passes
  on this code and fails on any change to the replay until it is re-pinned).
- Re-running the replay needs your own IBKR market-data subscription and the fetcher; the committed hashes say
  whether your files match the registered ones.
