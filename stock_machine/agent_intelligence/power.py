"""Power of the two pre-registered promotion tests, by simulation.

Runs the real evaluation code (direction.evaluate and
shadow.weights_promotion_test) on synthetic panels shaped like production:
every covered stock decides daily, outcomes are overlapping 20-session
returns in volatility units with a shared market factor, and signals are
persistent. A heuristic (incumbent) and a challenger signal each carry part
of a true, slowly varying edge; the challenger's share sets the effect size.

Synthetic results bound what the tests can detect under these assumptions;
`calibrate` re-derives detectable differences from real matured records.
"""

from __future__ import annotations

import random
from math import sqrt
from statistics import mean

from ..agents.contracts import digest
from . import direction, inference, shadow

HORIZON = 20
COST = 0.02  # round-trip fill cost in volatility units (10 bps/side on ~10% 20-session vol)


def _ar1(rng, n, phi):
    x, out, scale = rng.gauss(0, 1), [], sqrt(1 - phi * phi)
    for _ in range(n):
        x = phi * x + scale * rng.gauss(0, 1)
        out.append(x)
    return out


def _direction(value, band=0.25):
    return "LONG" if value > band else "SHORT" if value < -band else "FLAT"


def simulate_panel(
    seed: int,
    blocks: int,
    *,
    edge: float = 0.15,
    rho_incumbent: float = 0.3,
    rho_challenger: float = 0.3,
    stocks: int = 54,
    market_share: float = 0.3,
    phi: float = 0.97,
    epoch: str = "2026-10-01",
    weights_protocol_sha256: str | None = None,
):
    """Daily decisions for `blocks` x 20 sessions; returns (direction rows, weights rows).

    edge: expected 20-session return (vol units) per unit of the true signal.
    rho_*: correlation of each signal with the true signal.
    """
    from ..market_calendar import session_dates

    rng = random.Random(seed)
    n = blocks * HORIZON
    sessions = session_dates(epoch, "2035-12-31")[: n + HORIZON + 1]
    market = [rng.gauss(0, 1) for _ in sessions]
    direction_rows, weights_rows = [], []
    for _ in range(stocks):
        truth = _ar1(rng, len(sessions), phi)
        noise_a, noise_b = _ar1(rng, len(sessions), phi), _ar1(rng, len(sessions), phi)
        a = [rho_incumbent * t + sqrt(1 - rho_incumbent**2) * u for t, u in zip(truth, noise_a)]
        b = [rho_challenger * t + sqrt(1 - rho_challenger**2) * u for t, u in zip(truth, noise_b)]
        # Daily standardized returns: 20 of them sum to unit variance.
        idio = [
            edge * truth[i] / HORIZON
            + sqrt((1 - market_share) / HORIZON) * rng.gauss(0, 1)
            for i in range(len(sessions))
        ]
        mkt = [sqrt(market_share / HORIZON) * m for m in market]
        for t in range(n):
            window = range(t + 1, t + 1 + HORIZON)
            residual = sum(idio[i] for i in window)
            z = residual + sum(mkt[i] for i in window)
            direction_rows.append(
                {
                    "execution_session": sessions[t],
                    "incumbent": _direction(a[t] / 2),
                    "challenger": _direction(b[t] / 2),
                    "rewards": {"LONG": z - COST, "SHORT": -z - COST},
                }
            )
            weights_rows.append(
                {
                    "origin_session": sessions[t],
                    "horizon_sessions": HORIZON,
                    "residual_z": residual,
                    "candidate_score": max(-1.0, min(1.0, b[t] / 2)),
                    "baseline_score": max(-1.0, min(1.0, a[t] / 2)),
                    "forecast_scores": {},
                    "weights_protocol_sha256": weights_protocol_sha256,
                }
            )
    return direction_rows, weights_rows


def _protocols(bootstrap_samples=None):
    # v2 protocols have no bootstrap; the argument is kept for call compatibility.
    return dict(direction.PROTOCOL), dict(shadow.WEIGHTS_PROTOCOL)


def run_once(args) -> dict:
    seed, blocks, rho_challenger, edge, rho_incumbent, bootstrap_samples = args
    d_protocol, w_protocol = _protocols(bootstrap_samples)
    d_rows, w_rows = simulate_panel(
        seed, blocks, edge=edge, rho_incumbent=rho_incumbent,
        rho_challenger=rho_challenger, weights_protocol_sha256=digest(w_protocol),
    )
    d = direction.evaluate(d_rows, d_protocol)
    w = shadow.weights_promotion_test(w_rows, w_protocol)
    return {
        "direction_pass": d["status"] == "PASS_REQUIRES_INDEPENDENT_REVIEW",
        "direction_difference": d.get("mean_paired_difference"),
        "direction_detectable": d.get("detectable_difference_80pct"),
        "weights_pass": w["status"] == "PASS_REQUIRES_INDEPENDENT_REVIEW",
        "weights_difference": w.get("ic_difference"),
        "weights_detectable": w.get("detectable_difference_80pct"),
    }


def power_table(
    *,
    rho_challengers=(0.3, 0.45, 0.6, 0.8, 1.0),
    block_counts=(24, 36, 48),
    sims: int = 100,
    edge: float = 0.15,
    rho_incumbent: float = 0.3,
    bootstrap_samples: int = 1000,
    workers: int = 1,
) -> list[dict]:
    jobs = [
        (1000 * i + b, b, rho, edge, rho_incumbent, bootstrap_samples)
        for rho in rho_challengers
        for b in block_counts
        for i in range(sims)
    ]
    if workers > 1:
        from multiprocessing import Pool

        with Pool(workers) as pool:
            results = pool.map(run_once, jobs, chunksize=4)
    else:
        results = [run_once(j) for j in jobs]
    table = []
    for rho in rho_challengers:
        for b in block_counts:
            cell = [r for j, r in zip(jobs, results) if j[1] == b and j[2] == rho]

            def avg(key):
                values = [r[key] for r in cell if r[key] is not None]
                return mean(values) if values else None

            table.append(
                {
                    "rho_challenger": rho,
                    "rho_incumbent": rho_incumbent,
                    "edge": edge,
                    "blocks": b,
                    "sims": len(cell),
                    "direction_power": avg("direction_pass"),
                    "direction_mean_difference": avg("direction_difference"),
                    "direction_detectable_80pct": avg("direction_detectable"),
                    "weights_power": avg("weights_pass"),
                    "weights_mean_ic_difference": avg("weights_difference"),
                    "weights_detectable_80pct": avg("weights_detectable"),
                }
            )
    return table


# --- Calibration of the decision rule itself -------------------------------

def block_statistics(d_rows, w_rows, blocks, epoch="2026-10-01"):
    """Per-block sufficient statistics for both tests (block = 20 sessions)."""
    from ..market_calendar import session_dates

    index = {d: i for i, d in enumerate(session_dates(epoch, "2035-12-31"))}
    d_blocks = [[0.0, 0.0, 0] for _ in range(blocks)]
    w_blocks = [[0.0] * 6 for _ in range(blocks)]
    for r in d_rows:
        k = index[r["execution_session"]] // HORIZON
        value = {d: (0.0 if d == "FLAT" else r["rewards"][d]) for d in (r["challenger"], r["incumbent"])}
        d_blocks[k][0] += value[r["challenger"]] - value[r["incumbent"]]
        d_blocks[k][1] += value[r["challenger"]]
        d_blocks[k][2] += 1
    for r in w_rows:
        k, z = index[r["origin_session"]] // HORIZON, r["residual_z"]
        c, b = r["candidate_score"], r["baseline_score"]
        acc = w_blocks[k]
        acc[0] += c * z; acc[1] += c * c; acc[2] += z * z
        acc[3] += b * z; acc[4] += b * b; acc[5] += z * z
    return d_blocks, w_blocks


def _ic_difference(index, w_blocks):
    t = [sum(w_blocks[i][j] for i in index) for j in range(6)]
    return t[0] / sqrt(t[1] * t[2]) - t[3] / sqrt(t[4] * t[5])


newey_west_se = inference.newey_west_se


def rule_comparison_once(args) -> tuple:
    """Pass/fail under the v1 rule (independent block percentile bootstrap, 2.5%)
    and the v2 rule (Newey-West lag-1 + t(n-1)), on one simulated panel."""
    seed, blocks, rho, draws = args
    d_rows, w_rows = simulate_panel(seed, blocks, rho_challenger=rho)
    d_blocks, w_blocks = block_statistics(d_rows, w_rows, blocks)
    full = list(range(blocks))
    means = [b[0] / b[2] for b in d_blocks]
    d = sum(means) / blocks
    ch = sum(b[1] / b[2] for b in d_blocks) / blocks
    w = _ic_difference(full, w_blocks)
    rng = random.Random(seed)
    d_boot = sorted(sum(means[rng.randrange(blocks)] for _ in full) / blocks for _ in range(draws))
    w_boot = sorted(_ic_difference([rng.randrange(blocks) for _ in full], w_blocks) for _ in range(draws))
    q = int(0.025 * draws)
    pseudo = [blocks * w - (blocks - 1) * _ic_difference([j for j in full if j != i], w_blocks) for i in full]
    t = inference.t_quantile_975(blocks - 1)
    return (
        blocks, rho,
        d > 0 and ch > 0 and d_boot[q] > 0,
        w > 0 and w_boot[q] > 0,
        d > 0 and ch > 0 and d - t * newey_west_se(means) > 0,
        w > 0 and w - t * newey_west_se(pseudo) > 0,
    )


def rule_comparison(*, rhos=(0.3, 0.6, 0.8), block_counts=(12, 24, 36), sims=200,
                    null_sims=500, draws=1000, workers=1, seed=30000) -> list[dict]:
    jobs = [
        (seed + 17 * i + b, b, rho, draws)
        for rho in rhos
        for b in block_counts
        for i in range(null_sims if rho == 0.3 else sims)
    ]
    if workers > 1:
        from multiprocessing import Pool

        with Pool(workers) as pool:
            results = pool.map(rule_comparison_once, jobs, chunksize=4)
    else:
        results = [rule_comparison_once(j) for j in jobs]
    table = []
    for rho in rhos:
        for b in block_counts:
            cell = [r for r in results if r[0] == b and r[1] == rho]
            table.append({
                "rho_challenger": rho, "blocks": b, "sims": len(cell),
                "direction_v1_bootstrap": mean(r[2] for r in cell),
                "weights_v1_bootstrap": mean(r[3] for r in cell),
                "direction_v2_newey_west_t": mean(r[4] for r in cell),
                "weights_v2_newey_west_t": mean(r[5] for r in cell),
            })
    return table


def calibrate(conn) -> dict:
    """Detectable differences implied by real matured records (read-only)."""
    d = direction.summary(conn)
    w = shadow.weights_promotion_test(shadow.cumulative_rows(conn))
    out = {"direction": {k: d.get(k) for k in (
        "status", "blocks", "decisions", "newey_west_se",
        "detectable_difference_80pct", "detectable_at_minimum_blocks")}}
    sd = d.get("newey_west_se")
    out["direction"]["projected_detectable"] = (
        {str(b): inference.detectable_difference(sd * sqrt(d.get("blocks") or 1) / sqrt(b), b)
         for b in (24, 36, 48)}
        if sd and d.get("blocks") else None
    )
    out["weights"] = {k: w.get(k) for k in (
        "status", "blocks", "observations", "detectable_difference_80pct",
        "detectable_at_minimum_blocks")}
    mde, blocks = w.get("detectable_difference_80pct"), w.get("blocks")
    out["weights"]["projected_detectable"] = (
        {str(b): mde * sqrt(blocks / b) for b in (24, 36, 48)} if mde and blocks else None
    )
    return out
