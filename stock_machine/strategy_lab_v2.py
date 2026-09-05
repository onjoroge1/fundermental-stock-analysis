"""Strategy Lab v2: PIT portfolio-policy evaluation on the current research stack.

This replaces the superseded 2026-08-21 draft lab. It intentionally does not
backfill today's P2 probability forecasts into historical dates. Instead it
evaluates fixed, auditable signal policies on the existing point-in-time
fundamental/expectations panel in both long-only and dollar-neutral long/short
forms. The current calibrated P2 proposal policy remains forward-only until
real forecast vintages mature.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any
from .backtest.statistics import mean_uncertainty

SCHEMA_VERSION = "strategy_lab.v2.1"
HORIZON = "fwd_3m_pct"
MIN_NAMES = 8
MIN_EVALUATION_PERIODS = 8
DEVELOPMENT_SHARE = 0.60
TAIL_FRACTION = 0.20
MAX_SECTOR_SHARE_PER_LEG = 0.50
DEFAULT_COST_BPS = 15.0
MODES = ("long_only", "long_short")

STRATEGIES = {
    "earnings_yield": {
        "label": "Earnings yield", "kind": "baseline",
        "signals": (("factors", "earnings_yield_pct"),),
    },
    "revenue_growth": {
        "label": "Revenue growth", "kind": "baseline",
        "signals": (("factors", "revenue_yoy_pct"),),
    },
    "momentum": {
        "label": "12-month momentum", "kind": "baseline",
        "signals": (("factors", "momentum_12m_pct"),),
    },
    "value_quality": {
        "label": "Value + quality", "kind": "multi_factor",
        "signals": (("factors", "earnings_yield_pct"),
                    ("factors", "roic_pct")),
    },
    "growth_quality": {
        "label": "Growth + quality", "kind": "multi_factor",
        "signals": (("factors", "revenue_yoy_pct"),
                    ("components", "profitability"),
                    ("components", "earnings_quality")),
    },
    "quality_momentum": {
        "label": "Quality + momentum", "kind": "multi_factor",
        "signals": (("components", "profitability"),
                    ("components", "earnings_quality"),
                    ("factors", "momentum_12m_pct")),
    },
    "expectations_quality": {
        "label": "Expectations + quality", "kind": "multi_factor",
        "signals": (("expectations", "eps_revision_pct"),
                    ("expectations", "latest_eps_surprise_pct"),
                    ("components", "earnings_quality")),
    },
    "fundamental_composite": {
        "label": "Fundamental composite", "kind": "multi_factor",
        "signals": (("composite", None),),
    },
}


def _value(row: dict, path: tuple[str, str | None]) -> float | None:
    top, sub = path
    value = row.get(top)
    if sub is not None:
        value = value.get(sub) if isinstance(value, dict) else None
    return float(value) if value is not None else None


def _percentile_ranks(rows: list[dict], path: tuple[str, str | None]) -> dict[str, float] | None:
    present = [(r["ticker"], _value(r, path)) for r in rows]
    present = [(t, v) for t, v in present if v is not None]
    if len(present) < max(3, math.ceil(len(rows) / 2)):
        return None
    if len({v for _, v in present}) < 2:
        return None
    ordered = sorted(present, key=lambda x: x[1])
    ranks: dict[str, float] = {}
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        pct = ((i + j) / 2) / (len(ordered) - 1)
        for k in range(i, j + 1):
            ranks[ordered[k][0]] = pct
        i = j + 1
    return ranks


def score_policy_rows(rows: list[dict], definition: dict) -> dict | None:
    ranks = [_percentile_ranks(rows, path) for path in definition["signals"]]
    if any(r is None for r in ranks):
        return None
    labels = [".".join(x for x in path if x is not None)
              for path in definition["signals"]]
    scores = {
        row["ticker"]: sum(rank.get(row["ticker"], 0.5) for rank in ranks) / len(ranks)
        for row in rows
    }
    return {
        "scores": scores,
        "signal_percentiles": {
            ticker: {label: round(rank.get(ticker, 0.5), 6)
                     for label, rank in zip(labels, ranks)}
            for ticker in scores
        },
    }


def _select(ranked: list[str], by_ticker: dict[str, dict], count: int,
            max_sector_share: float) -> list[str]:
    """Equal-weight selection with a deterministic per-leg sector count cap."""
    cap = max(1, math.floor(count * max_sector_share))
    picked: list[str] = []
    sectors: dict[str, int] = defaultdict(int)
    for ticker in ranked:
        sector = by_ticker[ticker].get("sector") or "Unknown"
        if sectors[sector] >= cap:
            continue
        picked.append(ticker)
        sectors[sector] += 1
        if len(picked) == count:
            break
    # A short-filled basket has a different denominator: enforce the cap
    # against actual holdings too, rather than silently relaxing it.
    while picked and max(sectors.values()) / len(picked) > max_sector_share:
        sector = max(sectors, key=sectors.get)
        removed = next(t for t in reversed(picked) if (by_ticker[t].get("sector") or "Unknown") == sector)
        picked.remove(removed)
        sectors[sector] -= 1
    return picked


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    return math.sqrt(sum((x - m) ** 2 for x in values) / (len(values) - 1))


def _compound(returns_pct: list[float]) -> float:
    nav = 1.0
    for value in returns_pct:
        nav *= max(0.0, 1 + value / 100)
    return nav


def _max_drawdown(returns_pct: list[float]) -> float | None:
    if not returns_pct:
        return None
    nav = peak = 1.0
    worst = 0.0
    for value in returns_pct:
        nav *= max(0.0, 1 + value / 100)
        peak = max(peak, nav)
        worst = min(worst, nav / peak - 1)
    return round(worst * 100, 2)


def _metrics(periods: list[dict]) -> dict[str, Any]:
    if not periods:
        return {"periods": 0}
    net = [p["net_return_pct"] for p in periods]
    control = [p["control_return_pct"] for p in periods]
    excess = [a - b for a, b in zip(net, control)]
    final_target = periods[-1].get("target_date") or (date.fromisoformat(periods[-1]["as_of"]) + timedelta(days=91)).isoformat()
    years = max(1, (date.fromisoformat(final_target) - date.fromisoformat(periods[0].get("entry_date") or periods[0]["as_of"])).days) / 365.25
    ann = (_compound(net) ** (1 / years) - 1) * 100
    ctrl_ann = (_compound(control) ** (1 / years) - 1) * 100
    net_sd, excess_sd = _stdev(net), _stdev(excess)
    return {
        "periods": len(periods),
        "start": periods[0]["as_of"], "end": periods[-1]["as_of"],
        "annualized_return_pct": round(ann, 2),
        "control_annualized_return_pct": round(ctrl_ann, 2),
        "annualized_excess_pct": round(ann - ctrl_ann, 2),
        "annualized_volatility_pct": round(net_sd * math.sqrt(len(periods) / years), 2) if net_sd else None,
        "information_ratio": round((_mean(excess) / excess_sd) * math.sqrt(len(periods) / years), 2) if excess_sd else None,
        "outperform_share": round(sum(x > 0 for x in excess) / len(excess), 3),
        "positive_period_share": round(sum(x > 0 for x in net) / len(net), 3),
        "max_drawdown_pct": _max_drawdown(net),
        "drawdown_sampling": "period endpoints only; intraperiod drawdown unavailable",
        "elapsed_years": years,
        "worst_period_pct": round(min(net), 2),
        "average_turnover": round(_mean([p["turnover"] for p in periods]), 3),
        "cumulative_return_pct": round((_compound(net) - 1) * 100, 2),
        "control_cumulative_return_pct": round((_compound(control) - 1) * 100, 2),
    }


def _turnover(previous: set[str], current: set[str]) -> float:
    if not previous:
        return 1.0
    return 1 - len(previous & current) / max(len(previous), len(current), 1)


def _periods(by_date: dict[str, list[dict]], definition: dict,
             mode: str, cost_bps: float) -> list[dict]:
    periods: list[dict] = []
    prev_long: set[str] = set()
    prev_short: set[str] = set()
    for as_of, rows in sorted(by_date.items()):
        scored = score_policy_rows(rows, definition)
        if not scored:
            continue
        by_ticker = {r["ticker"]: r for r in rows}
        ranking = sorted(scored["scores"], key=lambda t: (-scored["scores"][t], t))
        count = max(2, math.ceil(len(rows) * TAIL_FRACTION))
        longs = _select(ranking, by_ticker, count, MAX_SECTOR_SHARE_PER_LEG)
        if len(longs) < 2:
            continue
        long_ret = _mean([by_ticker[t]["forward"][HORIZON] for t in longs])
        universe_ret = _mean([r["forward"][HORIZON] for r in rows])
        if long_ret is None or universe_ret is None:
            continue

        shorts: list[str] = []
        if mode == "long_short":
            short_rank = [t for t in reversed(ranking) if t not in set(longs)]
            shorts = _select(short_rank, by_ticker, count, MAX_SECTOR_SHARE_PER_LEG)
            if len(shorts) < 2:
                continue
            short_ret = _mean([by_ticker[t]["forward"][HORIZON] for t in shorts])
            gross_return = 0.5 * long_ret - 0.5 * short_ret
            turnover = 0.5 * _turnover(prev_long, set(longs)) + 0.5 * _turnover(prev_short, set(shorts))
            control = 0.0
        else:
            gross_return = long_ret
            turnover = _turnover(prev_long, set(longs))
            control = universe_ret

        cost_pct = turnover * cost_bps / 100.0
        periods.append({
            "as_of": as_of,
            "entry_date": max(r.get("forward_entry_date") or as_of for r in rows),
            "target_date": max((r.get("forward_target_dates") or {}).get(HORIZON)
                               or (date.fromisoformat(as_of) + timedelta(days=91)).isoformat() for r in rows),
            "mode": mode,
            "longs": longs,
            "shorts": shorts,
            "gross_return_pct": round(gross_return, 4),
            "cost_pct": round(cost_pct, 4),
            "net_return_pct": round(max(-100.0, gross_return - cost_pct), 4),
            "control_return_pct": round(control, 4),
            "universe_return_pct": round(universe_ret, 4),
            "turnover": round(turnover, 4),
        })
        prev_long, prev_short = set(longs), set(shorts)
    return periods


def run(panel: list[dict], *, cost_bps: float = DEFAULT_COST_BPS) -> dict:
    if cost_bps < 0:
        raise ValueError("cost_bps must be non-negative")
    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in panel:
        by_date[row["as_of"]].append(row)
    by_date = {d: rows for d, rows in by_date.items() if len(rows) >= MIN_NAMES
               and all((r.get("forward") or {}).get(HORIZON) is not None for r in rows)}
    dates = sorted(by_date)
    if len(dates) < MIN_EVALUATION_PERIODS + 4:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "INSUFFICIENT_HISTORY",
            "reason": f"need at least 12 quarterly cross-sections with {MIN_NAMES}+ names; have {len(dates)}",
        }
    split = max(4, math.floor(len(dates) * DEVELOPMENT_SHARE))
    evaluation_dates = set(dates[split:])

    results: dict[str, dict] = {}
    for mode in MODES:
        mode_results = {}
        for name, definition in STRATEGIES.items():
            periods = _periods(by_date, definition, mode, cost_bps)
            development = [p for p in periods if p["as_of"] not in evaluation_dates
                           and p["target_date"] < dates[split]]
            evaluation = [p for p in periods if p["as_of"] in evaluation_dates]
            mode_results[name] = {
                "label": definition["label"], "kind": definition["kind"],
                "signals": [".".join(x for x in p if x is not None) for p in definition["signals"]],
                "development": _metrics(development),
                "development_periods": development,
                "evaluation": _metrics(evaluation),
                "evaluation_periods": evaluation,
            }

        # Freeze both candidate and comparator using development data only.
        # Other policies remain descriptive trials and cannot be promoted
        # by choosing whichever looks best on the evaluation block.
        candidates = [(n, r) for n, r in mode_results.items()
                      if r["kind"] != "baseline" and r["development"].get("annualized_excess_pct") is not None]
        baselines = [(n, r) for n, r in mode_results.items()
                     if r["kind"] == "baseline" and r["development"].get("annualized_excess_pct") is not None]
        criterion = lambda pair: (pair[1]["development"]["annualized_excess_pct"], pair[0])
        selected = max(candidates, key=criterion, default=None)
        best = max(baselines, key=criterion, default=None)
        hurdle = best[1]["evaluation"].get("annualized_excess_pct") if best else None
        baseline_periods = {p["as_of"]: p for p in best[1]["evaluation_periods"]} if best else {}
        for name, item in mode_results.items():
            if item["kind"] == "baseline":
                item["promotion"] = {"status": "BASELINE", "gates": {}}
                continue
            if selected is None or name != selected[0]:
                item["promotion"] = {"status": "NOT_SELECTED_ON_DEVELOPMENT", "gates": {}}
                continue
            paired = [p for p in item["evaluation_periods"] if p["as_of"] in baseline_periods]
            advantage = mean_uncertainty(
                [p["net_return_pct"] - baseline_periods[p["as_of"]]["net_return_pct"] for p in paired], lags=1)
            m = _metrics(paired)
            drawdown_limit = -20.0 if mode == "long_short" else -35.0
            gates = {
                "minimum_evaluation_periods": len(paired) >= MIN_EVALUATION_PERIODS,
                "beats_best_single_factor": advantage["status"] == "OK" and advantage["lower"] > 0,
                "outperform_share": (m.get("outperform_share") or 0) >= 0.55,
                "positive_information_ratio": (m.get("information_ratio") or 0) > 0,
                "drawdown_within_limit": m.get("max_drawdown_pct") is not None and m["max_drawdown_pct"] >= drawdown_limit,
                "turnover_within_limit": m.get("average_turnover", 99) <= 0.80,
            }
            item["promotion"] = {
                "status": "ELIGIBLE_FOR_FORWARD_PAPER_REVIEW" if all(gates.values()) else "REJECTED",
                "gates": gates, "paired_baseline_advantage": advantage,
                "paired_evaluation_periods": len(paired),
            }
        results[mode] = {
            "control": "equal_weight_universe" if mode == "long_only" else "zero_return_dollar_neutral_control",
            "best_single_factor": best[0] if best else None,
            "selected_candidate": selected[0] if selected else None,
            "selection_basis": "development block only; labels purged at evaluation boundary",
            "best_single_factor_excess_pct": hurdle,
            "strategies": mode_results,
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "OK", "execution_status": "RESEARCH_ONLY",
        "horizon": HORIZON,
        "date_split": {
            "method": "chronological 60/40; candidate and baseline selected on matured development outcomes only",
            "development_start": dates[0], "development_end": dates[split - 1],
            "evaluation_start": dates[split], "evaluation_end": dates[-1],
            "development_periods": split, "evaluation_periods": len(dates) - split,
        },
        "config": {
            "tail_fraction": TAIL_FRACTION,
            "max_sector_share_per_leg": MAX_SECTOR_SHARE_PER_LEG,
            "cost_bps_per_turnover": cost_bps,
            "minimum_names": MIN_NAMES,
        },
        "modes": results,
        "p2_current_policy": {
            "status": "FORWARD_ONLY_NOT_BACKFILLED",
            "reason": "historical P2 calibrated probability/portfolio-proposal vintages do not exist for the full backtest; current forecasts are never copied backward",
        },
        "limitations": [
            "Universe is current coverage and therefore survivorship-biased.",
            "Historical sector labels use current taxonomy.",
            "Policy definitions were designed with historical knowledge; a fresh prospective incubation is still required.",
            "Dates with missing outcomes are excluded as entire cross-sections; missing stocks are never silently removed from rankings.",
            "Turnover uses membership changes and does not include weight drift; endpoint drawdown omits intraperiod losses.",
            "The long/short book is dollar-neutral at entry; market-beta neutrality and borrow availability are not established.",
            "Quarterly adjusted-close returns omit taxes, borrow fees, slippage and market impact beyond configured turnover cost.",
            "ELIGIBLE_FOR_FORWARD_PAPER_REVIEW permits paper incubation only; it never authorizes live capital.",
        ],
    }
