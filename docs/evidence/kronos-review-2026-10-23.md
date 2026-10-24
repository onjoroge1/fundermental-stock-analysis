# Kronos review, 2026-10-23: not pursued

Tracker item 59. The owner asked whether [Kronos](https://github.com/shiyu-coder/Kronos), a pretrained model of
financial candlesticks that predicts the next candles, fits this project and could add value to it. **It does not,
and the owner decided to skip it.** This note records the evidence so the question does not have to be reopened
from scratch.

**How it was reviewed**
- **Read only.** Four analysts read the paper, the code, the independent evidence and this repo. A skeptic, a
  leakage auditor and a test designer then checked their findings.
- **Nothing was installed, downloaded or run.** The repo (commit `67b630e6`, 2026-04-13) and the Hugging Face model
  cards were read through their APIs. No weights were fetched, and none of our data left the machine.
- **The sources are untrusted.** The GitHub issues include spam; no page contained instructions that were acted on.
- **Third-party reports are unverified.** Results from GitHub issues are labelled as such below and carry little
  weight.

## What Kronos is

- **What it does:** a tokenizer turns each candle (open, high, low, close, volume, amount) into a discrete token.
  A decoder-only transformer then predicts the next tokens, the way a language model predicts words.
- **Where it comes from:** Shi et al., "Kronos: A Foundation Model for the Language of Financial Markets",
  arXiv 2508.02739 (v1, 2025-08-02); AAAI-26, pp. 25366–25373. Code and weights are MIT-licensed.
- **Which models are released:**

  | Model | Parameters | Context | Released |
  |---|---|---|---|
  | Kronos-mini | 4.1M | 2,048 | yes |
  | Kronos-small | 24.7M | 512 | yes |
  | Kronos-base | 102.3M | 512 | yes |
  | Kronos-large | 499.2M | 512 | **no**; most of the paper's best numbers are this model |

- **What it was trained on:** 12.11B candles from about 96,600 assets on 40-plus exchanges, at 1-minute to weekly
  bars (paper Table 13). This includes NYSE (7,073 stocks and ETFs) and Nasdaq (8,725) from 2000, about 38% of all
  candles.
- **Popularity:** about 43,000 GitHub stars. That says nothing about forecasting skill.

## What the evidence shows

**1. It has almost certainly seen our test data.**
- **The cutoff is stated but cannot be checked.**
  - The paper (Appendix D) says the pretraining data runs "up to June 2024".
  - The owner says otherwise in issues #14, #40, #117 and #204: training through Feb 2024, validation Mar–May 2024,
    out-of-sample from June 2024.
  - The data, the ticker list and the pretraining code are not released.
  - The only hard limit is the upload date of the weights: 2025-06-30 (mini 2025-07-01), unchanged since.
- **Our 54 stocks and SPY must be assumed to be in it.** Daily overlap is near certain. Minute overlap is unknown:
  Table 13 averages about 292,000 candles per US asset, far short of full 1-minute history since 2000.

| Window | For a Kronos test |
|---|---|
| 2016–2023: our development stages and the intraday lab's explore, confirm and check months | contaminated |
| 2024-01..2024-06 | contaminated (training and validation; June ambiguous) |
| 2024-07..2025-06 | clean only on the authors' word |
| 2025-07 onward | clean: after the weights were uploaded |
| forecasts frozen before the outcome (forward) | clean by construction |

Our daily 2024-07..2026-08 has also been read before, by the volatility and implied-volatility hold-outs and by the
direction, signal, fair-value and event-strategy replays. For us those months are only pseudo-out-of-sample, for
direction as well as volatility.

**2. The paper's results do not measure tradable value.**
- **Weak baselines:** it has no random-walk, zero-return or HAR baseline. Its price metric (the correlation of
  predicted and true paths) cannot even score a flat forecast.
- **Small correlations, mostly from the unreleased model:**
  - The headline "+93%" is Kronos-large's price RankIC, 0.0267 against 0.0138 for the best zero-shot baseline
    (Tables 14–15). The released Kronos-small gets 0.0254.
  - Return correlations are small (IC about 0.07) and pooled over all samples, not measured across stocks on each
    date (Tables 16–17).
- **No reported uncertainty:** no test end date, no sample counts, no standard errors and no stated limitations.
- **The only backtest is A-shares** (the CSI300 and CSI800 universes, Table 10).
  - It is long-only and unhedged, and is scored by annualized excess return over the index.
  - The paper gives no dates beyond a July-2024 start; the repo's demo config uses 2024-07-01..2025-06-05, about 11
    months.
  - The released Kronos-small beats the best baseline by about 1.1 points of excess return a year and +0.05
    information ratio.

**3. On US stocks, its own paper shows it losing at volatility.** In the Nasdaq rows of Table 18, plain GARCH beats
every Kronos size:

| Model | Nasdaq volatility MAE | R² |
|---|---|---|
| GARCH | 0.1259 | 0.2271 |
| Kronos-small | 0.1540 | 0.1169 |
| Kronos-base | 0.1407 | 0.0961 |
| Kronos-large | 0.1503 | 0.0978 |

The "about 9% lower error than GARCH" headline is an average over 11 markets (Kronos-large 0.0370 against GARCH
0.0405), which hides this.

**4. Independent results are weak.**
- **The most careful US test** is GitHub issue #375. It is unverified.
  - Setup: zero-shot Kronos-small, 182 US stocks, 85 rebalance dates from 2024-07-01 to 2026-07-07.
  - Signal: IC +0.022 (Newey-West t 1.39), not significant even before costs, and weaker than a plain 5-day reversal
    (+0.032, t 1.97).
  - Trading: a decile long-short Sharpe of 0.77 before costs falls to 0.35 at 10 bps a side, with 91% of the profit
    from 5 of the 85 dates.
  - Its first year, 2024-07..2025-06, is after the cutoff only on the authors' word.
- **Too-narrow bands:** third-party reports say its sampled bands are far too narrow. A nominal 90% band covered
  58% of outcomes in #375; the 25% in #269 comes from weaker evidence.
- **Reversion bias:** the owner says its forecasts lean toward reversal (#33, #46).
- **The wider research agrees, in 2025–2026 preprints:**
  - Zero-shot time-series foundation models do not beat a zero-return forecast for US daily stock returns
    (Rahimikia, Ni and Wang, arXiv 2511.18578: R² −1.37% for Chronos-large, −2.80% for TimesFM).
  - For realized volatility they are competitive with, but mostly do not beat, Log-HAR (Brini, arXiv 2607.05291;
    Kronos was not tested there).

**5. The code has problems on the documented path.** Model and file references are to commit `67b630e6`.
- **No evaluation mode:** `KronosPredictor` never calls `.eval()` (`model/kronos.py` 484–506).
  - The safetensors loader in the pinned huggingface_hub 0.33.1 does not call it either; huggingface_hub added it to
    that path only in v1.9.0 (2026-04-02).
  - So the README path, as pinned, runs with dropout on, and the second-token cross-attention sees only the first
    position.
- **No uncertainty output:** `predict` returns the average of its sampled paths (465–467), not a distribution.
  There is also no seed argument.
- **Leaky fine-tuning:**
  - The A-share path normalised each window over its future bars until 2026-04-11 (commit `79d6d403`). A smaller
    leak remains: the loss still covers targets inside the lookback window, whose scaling includes their own later
    bars.
  - The CSV path still does it (PR #263 open).
  - Whether pretraining had the same flaw is unknown. The released weights predate the fix.
- **Leaky examples:** the README example and the demo backtest build future timestamps from the stock's own future
  rows. The demo also gives a stock a signal only if its next 10 rows exist.
- **Prediction itself has no look-ahead:** normalisation uses only the lookback window (544–547).

## How it fits this project

| Possible use | Verdict |
|---|---|
| Next-candle or first-hour intraday signal | **No.** Price action after the open barely predicts the rest of the day (correlations −0.07 to +0.09; `intraday-discovery-2026-10-22.md`). What looked promising in discovery, still unconfirmed, rests mostly on context known at 09:30: an earnings day, a market-wide gap, an extension of yesterday's move. A single-stock candle model sees neither the earnings calendar nor SPY's gap. Any Kronos test would also belong in new modules: an edit to `intraday_lab.py`, `intraday_replay.py`, `scripts/run_intraday_lab.py` or the daily-price loader changes the lab's code hash. The four frozen variants would then need a new logged explore before their confirm, and an edit between a passed confirm and its check strands that check (`CONFIRMED_WITH_OTHER_CODE`). |
| 20-session direction | **No.** The agent's 20-session direction is indistinguishable from abstaining (item 41, `direction-replay-2026-10-18.md`: −0.0017 [−0.036, +0.033]). No signal or strategy tested since has a supported edge (items 42, 43 and 45). The best clean US test of Kronos fails after costs. |
| Volatility forecast | **The only plausible use, and already beaten.** HAR_PLUS beat simple rules (item 55: QLIKE 0.236 against 0.294 for HAR), but calibrated implied volatility beats HAR_PLUS (item 57: 0.193 against 0.238 on IBKR bars, better in 27 of 32 months), and HAR_PLUS adds nothing on top of it. A Kronos forecast would have to add to implied volatility. Its own paper loses to GARCH on US stocks. |
| Synthetic data | **No.** README design rule #1 ("no synthetic data, ever": every number traces to a filing, a market feed or a deterministic computation over them) rules out Kronos-generated candles as research data or evidence. |

## The test that was designed but not run

If it is ever reopened, this is the cheapest honest test.
- **Registration and scope:**
  - Pre-registered before any Kronos output exists.
  - Daily bars only, in new modules; no registered file is edited.
  - Settings fixed in advance: Kronos-small with the paper's settings, in evaluation mode, on CPU with fixed seeds.
  - Pinned weight revisions and hashes; future timestamps taken from the exchange calendar.
- **Volatility (`kronos-volatility.v1`):** does adding a calibrated Kronos forecast lower the QLIKE error of HAR_PLUS
  and of calibrated implied volatility (as V3 of `implied-vol-comparison.v1`)? Calibrated on 2024-07..2025-06 and
  scored only on 2025-07..2026-08. Prior odds of beating HAR_PLUS: about 10%; beating implied volatility too is
  less likely still.
- **Direction (`kronos-direction.v1`):** do its 20-session paths rank the 54 stocks for profit after 20 bps round
  trip and the beta × SPY hedge, above a random-direction twin and a 5-day reversal? Scored on 2024-07..2026-08,
  with the same sign required from 2025-07. Prior odds: about 3–5%, close to the test's false-pass rate.
- **Forward (`kronos-forward.v1`):** forecasts frozen at each month-end for 24 months. This is the only test immune
  to the cutoff question.
- **Cost:**
  - about 4–6 agent-days of code and 3–4 PRs;
  - a separate torch environment and about 115 MB of weights;
  - an overnight CPU run on the Mac mini (Apple M4, 16 GB), estimated at 4–18 hours and not measured.

## Decision

**Not pursued** (owner, 2026-10-23). The expected value is low: the one use our evidence leaves open is already
dominated by implied volatility. The one-time confirm of the four frozen variants (EXT-FADE.v2 and EARN-FH.v1, with
their control and sub-test) is the higher-value next step. Nothing in the
repo depends on Kronos, and nothing was added.

**What would reopen it:**
- a pre-registered, evaluation-mode US volatility test on data after 2025-06-30 in which Kronos adds to both HAR_PLUS
  and implied volatility;
- or a large US cross-section (500+ stocks, after the cutoff) with a residual IC of at least 0.05 and a Newey-West t
  above 3, after costs and a beta hedge, not explained by 5-day reversal;
- or a published, checkable cutoff and ticker list for the released weights, or the release of Kronos-large with an
  independent evaluation.
