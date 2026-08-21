"""Walk-forward evaluation of complete, fixed stock-selection policies.

The existing backtest measures isolated factor rank correlation.  The Strategy
Lab tests the thing a portfolio would actually do: rank a point-in-time
cross-section, hold the top quintile for one quarter, rebalance, and pay a
turnover cost.  Strategy definitions and promotion gates are fixed in code so
the dashboard cannot manufacture a winner after seeing the results.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any


SCHEMA_VERSION = "strategy_lab.v1"
HORIZON = "fwd_3m_pct"
MIN_NAMES = 8
MIN_EVALUATION_PERIODS = 8
DEVELOPMENT_SHARE = 0.60
TOP_FRACTION = 0.20
DEFAULT_COST_BPS = 15.0

# All signals are higher-is-better. Missing values receive a neutral rank, but
# a date is skipped when fewer than half of the universe has a real value.
STRATEGIES = {
    "earnings_yield": {
        "label": "Earnings yield",
        "signals": (("factors", "earnings_yield_pct"),),
        "kind": "baseline",
    },
    "revenue_growth": {
        "label": "Revenue growth",
        "signals": (("factors", "revenue_yoy_pct"),),
        "kind": "baseline",
    },
    "momentum": {
        "label": "12-month momentum",
        "signals": (("factors", "momentum_12m_pct"),),
        "kind": "baseline",
    },
    "value_quality": {
        "label": "Value + quality",
        "signals": (("factors", "earnings_yield_pct"),
                    ("factors", "roic_pct")),
        "kind": "multi_factor",
    },
    "growth_quality": {
        "label": "Growth + quality",
        "signals": (("factors", "revenue_yoy_pct"),
                    ("factors", "roic_pct")),
        "kind": "multi_factor",
    },
    "quality_momentum": {
        "label": "Quality + momentum",
        "signals": (("components", "profitability"),
                    ("factors", "momentum_12m_pct")),
        "kind": "multi_factor",
    },
    "fundamental_composite": {
        "label": "Existing fundamental composite",
        "signals": (("composite", None),),
        "kind": "multi_factor",
    },
}


def _value(row: dict, path: tuple[str, str | None]) -> float | None:
    top, sub = path
    value = row.get(top)
    if sub is not None:
        value = value.get(sub) if isinstance(value, dict) else None
    return float(value) if value is not None else None


def _percentile_ranks(rows: list[dict], path: tuple[str, str | None]
                      ) -> dict[str, float] | None:
    present = [(r["ticker"], _value(r, path)) for r in rows]
    present = [(ticker, value) for ticker, value in present
               if value is not None]
    if len(present) < max(3, math.ceil(len(rows) / 2)):
        return None
    if len({value for _, value in present}) < 2:
        return None
    ordered = sorted(present, key=lambda item: item[1])
    ranks: dict[str, float] = {}
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        average = (i + j) / 2
        percentile = average / (len(ordered) - 1) if len(ordered) > 1 else 0.5
        for k in range(i, j + 1):
            ranks[ordered[k][0]] = percentile
        i = j + 1
    return ranks


def _scores(rows: list[dict], definition: dict) -> dict[str, float] | None:
    ranks = [_percentile_ranks(rows, path) for path in definition["signals"]]
    if any(r is None for r in ranks):
        return None
    return {
        row["ticker"]: sum(rank.get(row["ticker"], 0.5) for rank in ranks)
        / len(ranks)
        for row in rows
    }


def _compound(returns_pct: list[float]) -> float:
    nav = 1.0
    for value in returns_pct:
        nav *= 1 + value / 100
    return nav


def _max_drawdown(returns_pct: list[float]) -> float | None:
    if not returns_pct:
        return None
    nav = peak = 1.0
    worst = 0.0
    for value in returns_pct:
        nav *= 1 + value / 100
        peak = max(peak, nav)
        worst = min(worst, nav / peak - 1)
    return round(worst * 100, 2)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = _mean(values)
    return math.sqrt(sum((x - mean) ** 2 for x in values)
                     / (len(values) - 1))


def _metrics(periods: list[dict]) -> dict[str, Any]:
    if not periods:
        return {"periods": 0}
    net = [p["net_return_pct"] for p in periods]
    benchmark = [p["benchmark_return_pct"] for p in periods]
    excess = [a - b for a, b in zip(net, benchmark)]
    years = len(periods) / 4
    strategy_ann = (_compound(net) ** (1 / years) - 1) * 100
    benchmark_ann = (_compound(benchmark) ** (1 / years) - 1) * 100
    net_sd, excess_sd = _stdev(net), _stdev(excess)
    return {
        "periods": len(periods),
        "start": periods[0]["as_of"],
        "end": periods[-1]["as_of"],
        "annualized_return_pct": round(strategy_ann, 2),
        "benchmark_annualized_return_pct": round(benchmark_ann, 2),
        "annualized_excess_pct": round(strategy_ann - benchmark_ann, 2),
        "annualized_volatility_pct": round(net_sd * 2, 2) if net_sd else None,
        "sharpe_zero_rate": round((_mean(net) / net_sd) * 2, 2)
        if net_sd else None,
        "information_ratio": round((_mean(excess) / excess_sd) * 2, 2)
        if excess_sd else None,
        "outperform_share": round(sum(x > 0 for x in excess) / len(excess), 3),
        "positive_period_share": round(sum(x > 0 for x in net) / len(net), 3),
        "max_drawdown_pct": _max_drawdown(net),
        "worst_period_pct": round(min(net), 2),
        "average_turnover": round(_mean([p["turnover"] for p in periods]), 3),
        "cumulative_return_pct": round((_compound(net) - 1) * 100, 2),
        "benchmark_cumulative_return_pct": round(
            (_compound(benchmark) - 1) * 100, 2),
    }


def _strategy_periods(by_date: dict[str, list[dict]], definition: dict,
                      cost_bps: float) -> list[dict]:
    periods, previous = [], set()
    for as_of, rows in sorted(by_date.items()):
        scores = _scores(rows, definition)
        if scores is None:
            continue
        count = max(2, math.ceil(len(rows) * TOP_FRACTION))
        picks = [ticker for ticker, _ in sorted(
            scores.items(), key=lambda item: (-item[1], item[0]))[:count]]
        selected = set(picks)
        turnover = (1.0 if not previous else
                    1 - len(previous & selected) / max(len(previous), len(selected)))
        by_ticker = {r["ticker"]: r for r in rows}
        gross = _mean([by_ticker[t]["forward"][HORIZON] for t in picks])
        benchmark = _mean([r["forward"][HORIZON] for r in rows])
        cost_pct = turnover * cost_bps / 100
        net = max(-100.0, gross - cost_pct)
        periods.append({
            "as_of": as_of,
            "holdings": picks,
            "gross_return_pct": round(gross, 4),
            "cost_pct": round(cost_pct, 4),
            "net_return_pct": round(net, 4),
            "benchmark_return_pct": round(benchmark, 4),
            "turnover": round(turnover, 4),
        })
        previous = selected
    return periods


def run(panel: list[dict], *, cost_bps: float = DEFAULT_COST_BPS) -> dict:
    """Evaluate fixed policies on a chronological development/evaluation split."""
    if cost_bps < 0:
        raise ValueError("cost_bps must be non-negative")
    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in panel:
        if (row.get("forward") or {}).get(HORIZON) is not None:
            by_date[row["as_of"]].append(row)
    by_date = {d: rows for d, rows in by_date.items()
               if len(rows) >= MIN_NAMES}
    dates = sorted(by_date)
    if len(dates) < MIN_EVALUATION_PERIODS + 4:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "INSUFFICIENT_HISTORY",
            "reason": ("need at least 12 quarterly cross-sections with "
                       f"{MIN_NAMES}+ names; have {len(dates)}"),
        }
    split = max(4, math.floor(len(dates) * DEVELOPMENT_SHARE))
    evaluation_dates = set(dates[split:])

    results = {}
    for name, definition in STRATEGIES.items():
        periods = _strategy_periods(by_date, definition, cost_bps)
        development = [p for p in periods if p["as_of"] not in evaluation_dates]
        evaluation = [p for p in periods if p["as_of"] in evaluation_dates]
        results[name] = {
            "label": definition["label"],
            "kind": definition["kind"],
            "signals": [".".join(x for x in path if x is not None)
                        for path in definition["signals"]],
            "development": _metrics(development),
            "evaluation": _metrics(evaluation),
            "evaluation_periods": evaluation,
        }

    baseline_rows = [(name, r) for name, r in results.items()
                     if r["kind"] == "baseline"
                     and r["evaluation"].get("annualized_excess_pct") is not None]
    best_baseline = max(
        baseline_rows,
        key=lambda item: item[1]["evaluation"]["annualized_excess_pct"],
        default=None,
    )
    baseline_bar = (best_baseline[1]["evaluation"]["annualized_excess_pct"]
                    if best_baseline else None)
    for result in results.values():
        if result["kind"] == "baseline":
            result["promotion"] = {"status": "BASELINE", "gates": {}}
            continue
        metrics = result["evaluation"]
        gates = {
            "minimum_evaluation_periods": (
                metrics.get("periods", 0) >= MIN_EVALUATION_PERIODS),
            "beats_best_single_factor": (
                baseline_bar is not None
                and metrics.get("annualized_excess_pct") is not None
                and metrics["annualized_excess_pct"] > baseline_bar),
            "outperforms_more_than_half": (
                (metrics.get("outperform_share") or 0) >= 0.55),
            "positive_information_ratio": (
                (metrics.get("information_ratio") or 0) > 0
                or ((metrics.get("outperform_share") or 0) == 1
                    and (metrics.get("annualized_excess_pct") or 0) > 0)),
            "drawdown_within_limit": (
                metrics.get("max_drawdown_pct") is not None
                and metrics["max_drawdown_pct"] >= -35),
        }
        result["promotion"] = {
            "status": "PAPER_ELIGIBLE" if all(gates.values()) else "REJECTED",
            "gates": gates,
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "OK",
        "execution_status": "RESEARCH_ONLY",
        "horizon": HORIZON,
        "date_split": {
            "method": "chronological 60/40; evaluation dates untouched by policy design",
            "development_start": dates[0],
            "development_end": dates[split - 1],
            "evaluation_start": dates[split],
            "evaluation_end": dates[-1],
            "development_periods": split,
            "evaluation_periods": len(dates) - split,
        },
        "config": {
            "top_fraction": TOP_FRACTION,
            "cost_bps_per_turnover": cost_bps,
            "minimum_names": MIN_NAMES,
            "minimum_evaluation_periods": MIN_EVALUATION_PERIODS,
        },
        "best_single_factor": best_baseline[0] if best_baseline else None,
        "best_single_factor_excess_pct": baseline_bar,
        "strategies": results,
        "limitations": [
            "Universe is current coverage and therefore survivorship-biased.",
            "Quarterly adjusted-close returns omit taxes, borrow, and market impact.",
            "PAPER_ELIGIBLE permits paper monitoring only, never live execution.",
        ],
    }
