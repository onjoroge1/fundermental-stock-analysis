# P0 Unified Alpha Model

This slice adds a point-in-time cross-sectional learner that combines the existing fundamental/valuation feature panel with expectations features.

## Target

12-month cross-sectional excess return. Each test date is ranked against names available on that same date.

## Features

Fundamental/valuation features:
- growth
- profitability
- earnings quality
- financial health
- capital allocation
- valuation
- earnings yield
- FCF yield
- revenue growth
- ROIC
- 12-month momentum

Expectations features:
- EPS revision
- revenue revision
- latest EPS surprise
- trailing four-quarter EPS surprise

## Validation

- per-date cross-sectional z-scoring
- missing values impute to the same-date cross-sectional mean
- 370-day embargo before each test date
- minimum eight historical training dates
- minimum eight names per test cross-section
- comparisons use identical test dates

The model remains diagnostic unless its mean Spearman information coefficient beats the strongest of revenue growth, 12-month momentum, and the existing composite score on the same test dates.

## Point-in-time rule

Expectation features are reconstructed only from consensus vintages whose `snapshot_date <= as_of` and earnings surprises whose event date is `<= as_of`. Future revisions or surprises are never carried backward.
