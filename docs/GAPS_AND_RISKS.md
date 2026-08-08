# What's missing, and what could make this analysis a failure

Audit date: 2026-07-24. Ordered by how likely each item is to silently corrupt
a conclusion, not by engineering effort.

## A. Analytical-validity risks (could make every conclusion wrong)

### A1. The scores are unvalidated — the machine has no proven edge
The scoring thresholds ("10% revenue growth = 65 points") and component
weights are hand-set conventions, never fitted to outcomes, and the whole
composite has never been backtested against forward returns. The crisp
numbers in the UI project more authority than they have earned. **Until the
walk-forward backtest exists, the composite is a screening heuristic. The
kill criterion from the spec applies: if it can't beat a naive value+quality
screen out of sample, the machine is narrative, not edge.**

### A2. Sector-blind scoring
TSLA's 18.9% gross margin is scored on the same curve as META's 82%. Autos,
retailers, semis and software have structurally different economics; the
current thresholds implicitly assume "software-like is good." Banks/insurers/
REITs cannot be scored at all (their statements don't map). Sector adapters
(spec §8) are required before the composite can rank across sectors honestly.

### A3. No expectations data — the machine cannot see what the market believes
Without point-in-time consensus we cannot distinguish "good quarter" from
"better-than-expected quarter," which is the thing that actually moves
prices. The expectations component is null, surprise/revision analysis is
prohibited by the gate, and every forecast is uncalibrated (confidence LOW).
This is the single highest-value data purchase.

### A4. Scenario probabilities are judgment with no calibration loop
The bear/base/bull probabilities and multiples are analyst assumptions. No
mechanism yet records whether past scenario sets were well-calibrated
(outcomes falling inside ranges at the stated rates). Until forecasts are
frozen and scored (spec §10), expected returns are structured opinion.
The 3/6-month interpolation is explicitly a placeholder (linear convergence).

### A5. Point-in-time is conservative, not exact: the 8-K gap
`available_at` = 10-Q/10-K filing date. But companies announce results in
8-K press releases days or weeks earlier. The machine therefore *lags* the
market's knowledge between the 8-K and the 10-Q. Safe against look-ahead,
but it means "latest quarter" can be stale versus reality, and backtests
would misdate the market's information set. Fix: parse 8-K earnings exhibits
(EX-99) and record both availability timestamps.

### A6. First-reported-wins has a flip side
Canonical values are the original filings (correct for point-in-time
reconstruction; restatements are logged, not applied). But a *current-view*
analyst usually wants the restated history. There is no "current best view"
toggle yet — a materially restated history (e.g. AAPL's 2009 retrospective
revision, which the pipeline caught) stays in bundles at its original values.
Fix: dual-view periods (as-first-reported vs as-latest-restated).

### A7. GAAP distortions pass through by design
GOOGL's TTM "earnings" include ~$97B of non-operating investment marks; the
gate now flags NI > 1.25× OI, but net margin, ROE, P/E, and the valuation
score still ingest the distorted figure. Similar traps: one-time tax items,
impairments. Fix: operating-basis metric variants alongside GAAP.

## B. Data gaps (ranked)

1. **Point-in-time consensus + guidance history** (paid; unlocks the
   expectations pillar and calibrated forecasts).
2. **Segment data** — GOOGL cloud vs search, AMZN AWS vs retail, TSLA auto
   vs energy are invisible; several report theses are explicitly blocked on
   this. XBRL segment dimensions are absent from companyfacts; needs full
   filing parsing (Phase 2).
3. **8-K earnings exhibits** (see A5).
4. **Earnings-call transcripts** (licensed source) — management language,
   Q&A evasions.
5. **Macro vintages (FRED/ALFRED)** — the P/E-percentile-vs-own-history
   metric ignores the rate regime; a 2020 multiple is not comparable to a
   2026 multiple without it.
6. **True D&A → EBITDA** — leverage/coverage ratios currently use operating
   income as proxy (overstates safety); interest expense is missing where
   issuers stopped itemizing it (AAPL).
7. **Delisted/acquired companies** — the universe is 7 surviving mega-caps;
   any backtest on it is survivorship-biased to the point of meaninglessness.
8. **Insider transactions, 13F holdings, short interest** (Tier 2).
9. **Peer groups** — valuation is vs own history only; no cross-sectional
   percentile.

## C. Data-source and infrastructure risks

- **Prices are single-vendor via Yahoo's unofficial endpoint.** It verified
  against SEC-day closes and live quotes, but it is undocumented, can break
  or rate-limit without notice, and its terms don't cover redistribution or
  commercial use. Same for share-count fallbacks. Phase 2: licensed vendor,
  two-source reconciliation with a conflict log (spec's vendor-disagreement
  control is unimplemented — there is nothing to disagree *with* yet).
- **Stooq lesson generalizes:** free endpoints rot. The ingestion layer
  should treat provider failure as expected (it currently fails loudly,
  which is correct — never fill gaps silently).
- **Raw store has no backup.** Postgres (Neon) is rebuildable *from* raw,
  but data/raw/ exists only on this laptop. Losing it loses point-in-time
  provenance (SEC can be refetched but *as-of-today*, not as-of-then).
  Fix: sync data/raw/ to object storage.
- **Scheduler dependency:** the daily task runs only while the Claude app is
  open; a missed day silently widens the freshness gap (the gate's staleness
  check will catch >5-day-old prices, which mitigates).
- **XBRL tag coverage is finite.** The NVDA incident (revenue under a
  different tag in the latest 10-Q) was caught because revenue is a critical
  field; non-critical fields (e.g. `interest_expense`, `inventory`) can go
  silently null for a tag variant we don't map. Fix: per-field coverage
  matrix in the data-quality report + alert on newly-null fields.
- **Fiscal-calendar heuristics.** fy/fp labeling is by vote; Q4 relabel is a
  heuristic; 53-week years fit the duration windows but 14-week quarters at
  retailers (Jan year-ends) and fiscal-year changes are untested territory.
  TTM contiguity is now enforced (gap > 21 days → no TTM) — fixed 2026-07-24.

## D. Process risks

- **Narrative drift (mitigated, watch it):** reports embed point-in-time
  numbers in prose; data refreshes daily. The refresh flags staleness and
  the UI banners it; the rule is *never* auto-regenerate narratives. The
  failure mode returns if anyone wires `run_analyses.py` into the cron.
- **Confirmation bias in scenario authorship:** the same analyst layer picks
  assumptions and writes the adversarial review. The spec's intent is an
  *independent* adversarial pass (separate prompt/session, ideally blind to
  the conclusion). Current reports were authored with the bear case in the
  same sitting.
- **Prompt injection surface is currently near-zero** (no document text is
  ingested) but arrives with transcripts/filings text. The
  UNTRUSTED_SOURCE_CONTENT policy fields exist in bundles; enforcement must
  be built when documents land.
- **No CI.** Tests run when remembered. A pre-commit/pre-refresh test run
  would catch normalization regressions before they hit the DB.

## E. What "failure" concretely looks like, ranked by probability

1. **The machine works mechanically but predicts nothing** — scores never
   validated, user allocates on them anyway. (Mitigation: backtest with kill
   criteria before any capital decisions; until then treat output as a
   research organizer.)
2. **Quiet data rot** — a provider changes format, a tag goes unmapped, a
   field nulls out, and metrics shift without anyone noticing. (Mitigation:
   daily refresh logs + data-quality gate + field-coverage alerting.)
3. **Stale-narrative trust** — user reads a report whose facts predate a
   filing. (Mitigation: staleness banners, now live.)
4. **Regime break** — thresholds and multiples calibrated implicitly to the
   2021-2026 mega-cap regime stop describing the world (rates, sector
   rotation). (Mitigation: macro vintages + peer-relative metrics.)
5. **Single-vendor price failure** — Yahoo endpoint breaks; market snapshot
   and valuation go stale together. (Mitigation: second source.)
6. **Judgment presented as computation** — any new "score" that is hand-set
   rather than computed. (House rule: prohibited; enforced in review.)
