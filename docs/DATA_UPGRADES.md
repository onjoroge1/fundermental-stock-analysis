# Data upgrades — what to buy, what unlocks, what's already prepared

The code side of both upgrades is DONE — capabilities activate automatically
once the account changes; verify anytime with:

```bash
.venv/bin/python -m stock_machine planprobe
```

## 1. FMP Starter (unblocks consensus for the full universe)

**Action (yours — requires payment):** upgrade the existing key at
https://site.financialmodelingprep.com/developer/docs/pricing (Starter tier).
The API key stays the same; nothing to reconfigure.

What activates automatically (adaptive plan detection is already wired):
- all 43 symbols (21 currently gated) → consensus coverage 43/43
- quarterly analyst estimates (currently annual-only)
- deep history (`limit` cap lifts from 5 → 40): ~10 years of surprise
  history per name instead of ~1 year
- expectations scores for the whole universe; vintage accumulation on all
  names via the daily 7:30am refresh

## 2. Survivorship-free universe (unblocks trustworthy backtests)

Current state, probed:
- **Delisted-companies list: FREE** — already accessible with today's key
  (`/stable/delisted-companies`, tickers + delisting dates).
- Delisted-name **fundamentals: FREE** — SEC EDGAR keeps all filings; our
  pipeline keys on CIK and works unchanged for dead companies.
- Delisted-name **prices: PAID** — the missing piece. Options:
  - FMP Premium ($59-ish/mo): delisted symbols open up on the same
    endpoints we already use — zero code changes.
  - Sharadar (Nasdaq Data Link, ~$59/mo for SEP): the reference dataset for
    survivorship-free US equity prices; would need a small new prices
    provider module (~1 hour of work).

**Recommendation:** FMP Premium covers both upgrades in one subscription
(full symbols + quarterly estimates + delisted prices). After purchase, the
build-out is: pull the delisted list for 2014+ tech/consumer names, ingest
their SEC fundamentals + FMP prices, re-run `backtest` — the harness needs
no changes.

## Blocked until then (tracked as PENDING on the System page)
- Survivorship-free backtest verdicts (current ones are indicative only)
- Consensus-revision prediction (also needs vintage history: our own
  snapshots reach usefulness at ~90 days of accumulation)
- Transcripts (FMP premium tier)
