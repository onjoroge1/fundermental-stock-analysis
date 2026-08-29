"""Point-in-time portfolio risk estimators using stored adjusted closes only."""
from __future__ import annotations

from math import log, sqrt
from statistics import mean

TRADING_DAYS = 252


def _map(rows: list[dict]) -> dict[str, float]:
    out = {}
    for row in rows or []:
        value = row.get("adj_close") or row.get("close")
        if value is not None and float(value) > 0:
            out[str(row["date"])[:10]] = float(value)
    return out


def aligned_log_returns(a_rows: list[dict], b_rows: list[dict], window: int = 252):
    a, b = _map(a_rows), _map(b_rows)
    dates = sorted(set(a) & set(b))
    if len(dates) < 3:
        return [], []
    dates = dates[-(window + 1):]
    ar, br = [], []
    for d0, d1 in zip(dates, dates[1:]):
        ar.append(log(a[d1] / a[d0]))
        br.append(log(b[d1] / b[d0]))
    return ar, br


def realized_vol(rows: list[dict], window: int = 63) -> float | None:
    px = _map(rows)
    dates = sorted(px)
    if len(dates) < window + 1:
        return None
    dates = dates[-(window + 1):]
    rets = [log(px[b] / px[a]) for a, b in zip(dates, dates[1:])]
    if len(rets) < 2:
        return None
    m = mean(rets)
    var = sum((x - m) ** 2 for x in rets) / (len(rets) - 1)
    return sqrt(var) * sqrt(TRADING_DAYS)


def beta(stock_rows: list[dict], benchmark_rows: list[dict], window: int = 252) -> float | None:
    s, b = aligned_log_returns(stock_rows, benchmark_rows, window)
    if len(s) < 20:
        return None
    mb = mean(b)
    ms = mean(s)
    var_b = sum((x - mb) ** 2 for x in b)
    if var_b <= 1e-12:
        return None
    cov = sum((x - ms) * (y - mb) for x, y in zip(s, b))
    return cov / var_b


def correlation(a_rows: list[dict], b_rows: list[dict], window: int = 126) -> float | None:
    a, b = aligned_log_returns(a_rows, b_rows, window)
    if len(a) < 20:
        return None
    ma, mb = mean(a), mean(b)
    da = [x - ma for x in a]
    db = [x - mb for x in b]
    va = sum(x * x for x in da)
    vb = sum(x * x for x in db)
    if va <= 1e-12 or vb <= 1e-12:
        return None
    return sum(x * y for x, y in zip(da, db)) / sqrt(va * vb)
