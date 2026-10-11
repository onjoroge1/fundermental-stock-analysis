# Power and size of the pre-registered promotion tests (2026-10-11)

Tracker item 21. This applies to both tests: the learned direction vs the heuristic
(`learned-direction-vs-heuristic.v1`, #102) and the candidate shadow weights vs the
heuristic bias (`shadow-candidate-weights-vs-heuristic.v1`, #107). The protocols are
unchanged by this work.

Everything below can be reproduced with `scripts/power_promotion_tests.py`, using the
default seeds, and `stock_machine/agent_intelligence/power.py`.

## How it was simulated

The **live evaluation code** (`direction.evaluate`, `shadow.weights_promotion_test`)
ran on synthetic panels shaped like production:

- **Panel:** 54 stocks deciding every session. Outcomes are overlapping 20-session
  returns in volatility units, with 30% of variance from a shared market factor.
  Costs are 0.02 vol units per round trip.
- **Edge:** a slowly varying true edge (AR(1), φ = 0.97) worth 0.15 vol units per unit
  of signal.
- **Signals:** the heuristic signal correlates 0.30 with the true edge. The challenger
  correlates ρ, and ρ sets the effect size. ρ = 0.30 means no improvement.
- **Grid:** 100 simulations per cell (more for the size checks) at 12, 24 and 36
  blocks. A block is 20 sessions, so 12 blocks is about a year and 36 about three.

These are assumptions, not measurements. `calibrate` re-derives the noise from real
matured records once they exist.

## Power of the tests as written

Share of simulations that reach `PASS_REQUIRES_INDEPENDENT_REVIEW`:

| Challenger ρ | Blocks | Direction power | Mean reward difference | Direction 80% detectable | Weights power | Mean IC difference | Weights 80% detectable |
|---|---|---|---|---|---|---|---|
| 0.30 (no edge) | 12 / 24 / 36 | 6% / 2% / 4% | 0.00 | 0.078 / 0.060 / 0.048 | 9% / 4% / 4% | 0.00 | 0.100 / 0.078 / 0.064 |
| 0.45 | 12 / 24 / 36 | 10% / 10% / 12% | 0.01 | ≈ same | 15% / 12% / 16% | 0.02 | ≈ same |
| 0.60 | 12 / 24 / 36 | 19% / 25% / 26% | 0.02 | ≈ same | 35% / 37% / 44% | 0.04 | ≈ same |
| 0.80 | 12 / 24 / 36 | 36% / 50% / 67% | 0.04 | 0.073 / 0.054 / 0.044 | 53% / 71% / 81% | 0.065 | 0.093 / 0.070 / 0.057 |
| 1.00 | 12 / 24 / 36 | 58% / 82% / 94% | 0.054 | 0.073 / 0.051 / 0.043 | 70% / 91% / 99% | 0.09 | 0.092 / 0.066 / 0.054 |

**Reading the units.** The direction test's difference is in reward units: net
20-session return divided by ex-ante 20-session volatility. For a 35%-volatility stock,
0.05 is about 0.5 percentage points per 20-session position, or roughly 6% a year on
that position. The weights test's difference is an information coefficient; 0.05 is
already a strong stock-selection IC.

**What this means:**

- **After one year (12 blocks),** only very large improvements are detected reliably:
  at least about 0.07 reward units for direction and 0.09 IC for weights.
- **A NOT_SUPERIOR result at 12 blocks is weak evidence.** It rules out only those large
  improvements. Both tests now report `detectable_difference_80pct` (from the observed
  noise) and `detectable_at_minimum_blocks`, so each result shows what it could and
  couldn't have detected.
- **Moderate improvements** (ρ 0.45–0.60) need several years, or are effectively
  undetectable, under these assumptions.

## Size: the rules pass too often when there is no improvement

The protocols' "bootstrap lower 2.5% bound > 0" is meant to give at most a 2.5% false
pass rate. With ρ = 0.30 (no edge), 500 simulations per cell (Monte Carlo SE ≈ ±0.7
percentage points):

| Blocks | Direction, current rule | Direction, Newey–West + t | Weights, current rule | Weights, Newey–West + t |
|---|---|---|---|---|
| 12 | 6.6% | 4.6% | 7.4% | 5.6% |
| 24 | 5.2% | 3.0% | 5.8% | 2.8% |
| 36 | 5.6% | 3.8% | 5.6% | 3.4% |

**Diagnosis.** Across no-edge simulations at 36 blocks, the true spread of each
statistic was 22% (direction) and 26% (weights) larger than the bootstrap's estimate.

- **Correlated neighbouring blocks.** Block means have lag-1 autocorrelation of about
  +0.16, because outcome windows reach into the next block and signals persist.
  Independent block resampling ignores this.
- **Small samples** add a smaller bias.

**What didn't fix it:**

- **Moving-block bootstrap:** runs of 2–3 blocks barely moved the size.
- **Faster-fading signals:** φ = 0.90 behaved like 0.97, so persistence isn't the
  driver.
- **A stricter 1% bootstrap bound:** it still passed 3.5–5.8% of the time.

**What did fix it, from 24 blocks on:** a Newey–West lag-1 standard error with a
t(n−1) cutoff. It's computed on block means for direction, and on per-block jackknife
pseudo-values for the weights IC difference. No rule tried is well calibrated at
12 blocks.

| Power with Newey–West + t | 12 | 24 | 36 blocks |
|---|---|---|---|
| Direction, ρ 0.6 / 0.8 | 12% / 25% | 19% / 40% | 27% / 59% |
| Weights, ρ 0.6 / 0.8 | 19% / 36% | 24% / 57% | 35% / 73% |

## Recommendation (pending owner decision)

Adopt v2 of both protocols:

- a Newey–West lag-1 + t(n−1) lower bound in place of the percentile bootstrap;
- a **minimum of 24 blocks** in place of 12.

This brings the false pass rate close to the nominal 2.5%. It costs about 8–10 points of
power and moves the earliest possible review from about one year to about two. Because
the protocols are hashed, adopting v2 restarts their evidence; that's cheap now, since
both have been live only about a day. Until then, treat a PASS as carrying about a 5–7%
false-positive risk under these assumptions, and treat a NOT_SUPERIOR result as bounded
by its reported detectable difference.
