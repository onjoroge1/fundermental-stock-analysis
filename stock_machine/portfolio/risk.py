"""Pure point-in-time risk estimators for P2 portfolio construction."""
from __future__ import annotations

from math import log, sqrt

TRADING_DAYS = 252


def _series(rows: list[dict]) -> dict[str, float]:
    out = {}
    for row in rows or []:
        px = row.get("adj_close") or row.get("close")
        if px is not None and float(px) > 0:
            out[str(row["date"])[:10]] = float(px)
    return out


def log_returns(rows: list[dict], lookback: int = 126) -> dict[str, float]:
    px = _series(rows)
    dates = sorted(px)
    out = {}
    for i in range(1, len(dates)):
        d0, d1 = dates[i - 1], dates[i]
        out[d1] = log(px[d1] / px[d0])
    if lookback and len(out) > lookback:
        keep = sorted(out)[-lookback:]
        out = {d: out[d] for d in keep}
    return out


def annualized_vol(rows: list[dict], lookback: int = 126) -> float | None:
    vals = list(log_returns(rows, lookback).values())
    if len(vals) < 20:
        return None
    m = sum(vals) / len(vals)
    var = sum((x - m) ** 2 for x in vals) / (len(vals) - 1)
    return sqrt(var) * sqrt(TRADING_DAYS)


def beta_to_benchmark(stock_rows: list[dict], benchmark_rows: list[dict],
                      lookback: int = 126) -> float | None:
    s = log_returns(stock_rows, lookback * 2)
    b = log_returns(benchmark_rows, lookback * 2)
    dates = sorted(set(s) & set(b))[-lookback:]
    if len(dates) < 30:
        return None
    xs = [b[d] for d in dates]
    ys = [s[d] for d in dates]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var_x = sum((x - mx) ** 2 for x in xs)
    if var_x <= 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / var_x


def correlation(a_rows: list[dict], b_rows: list[dict],
                lookback: int = 126) -> float | None:
    a = log_returns(a_rows, lookback * 2)
    b = log_returns(b_rows, lookback * 2)
    dates = sorted(set(a) & set(b))[-lookback:]
    if len(dates) < 30:
        return None
    xs, ys = [a[d] for d in dates], [b[d] for d in dates]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    den = sqrt(sum(x * x for x in dx) * sum(y * y for y in dy))
    if den <= 0:
        return None
    return sum(x * y for x, y in zip(dx, dy)) / den
