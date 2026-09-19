"""Point-in-time technical and market-state features for Agent Intelligence v2.

All features use only rows dated <= the requested as-of session. They are
descriptive state, not trading recommendations or calibrated probabilities.
"""
from __future__ import annotations

from math import log, sqrt
from statistics import mean, pstdev

TRADING_DAYS = 252


def _clean(rows: list[dict], as_of: str | None = None) -> list[dict]:
    by_date: dict[str, dict] = {}
    for row in rows or []:
        day = str(row.get("date") or "")[:10]
        price = row.get("adj_close") or row.get("close")
        if not day or price is None or float(price) <= 0:
            continue
        if as_of and day > as_of:
            continue
        by_date[day] = {**row, "date": day, "_price": float(price)}
    return [by_date[d] for d in sorted(by_date)]


def _ret(rows: list[dict], width: int) -> float | None:
    if len(rows) <= width:
        return None
    a, b = rows[-width-1]["_price"], rows[-1]["_price"]
    return b / a - 1.0 if a > 0 else None


def _sma(rows: list[dict], width: int) -> float | None:
    if len(rows) < width:
        return None
    return mean(r["_price"] for r in rows[-width:])


def _sma_slope(rows: list[dict], width: int, lag: int = 5) -> float | None:
    if len(rows) < width + lag:
        return None
    now = mean(r["_price"] for r in rows[-width:])
    prior = mean(r["_price"] for r in rows[-width-lag:-lag])
    return now / prior - 1.0 if prior > 0 else None


def _vol(rows: list[dict], width: int) -> float | None:
    if len(rows) < width + 1:
        return None
    prices = [r["_price"] for r in rows[-width-1:]]
    rets = [log(prices[i] / prices[i-1]) for i in range(1, len(prices))]
    if len(rets) < 2:
        return None
    return pstdev(rets) * sqrt(TRADING_DAYS)


def _drawdown(rows: list[dict], width: int) -> float | None:
    if not rows:
        return None
    window = rows[-min(width, len(rows)):]
    peak = max(r["_price"] for r in window)
    return rows[-1]["_price"] / peak - 1.0 if peak > 0 else None


def _rsi(rows: list[dict], width: int = 14) -> float | None:
    if len(rows) < width + 1:
        return None
    prices = [r["_price"] for r in rows[-width-1:]]
    changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains = sum(max(x, 0.0) for x in changes) / width
    losses = sum(max(-x, 0.0) for x in changes) / width
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    rs = gains / losses
    return 100.0 - 100.0 / (1.0 + rs)


def _zscore(rows: list[dict], width: int = 20) -> float | None:
    if len(rows) < width:
        return None
    values = [r["_price"] for r in rows[-width:]]
    sd = pstdev(values)
    return (values[-1] - mean(values)) / sd if sd > 0 else 0.0


def _atr_pct(rows: list[dict], width: int = 14) -> float | None:
    if len(rows) < width + 1:
        return None
    values = []
    sample = rows[-width-1:]
    for i in range(1, len(sample)):
        high, low = sample[i].get("high"), sample[i].get("low")
        prev_close = sample[i-1].get("close") or sample[i-1]["_price"]
        if high is None or low is None or prev_close is None:
            return None
        high, low, prev_close = float(high), float(low), float(prev_close)
        values.append(max(high-low, abs(high-prev_close), abs(low-prev_close)))
    price = rows[-1]["_price"]
    return mean(values) / price if values and price > 0 else None


def _volume_ratio(rows: list[dict], short: int = 20, long: int = 60) -> float | None:
    if len(rows) < long:
        return None
    short_values = [float(r["volume"]) for r in rows[-short:] if r.get("volume") is not None]
    long_values = [float(r["volume"]) for r in rows[-long:] if r.get("volume") is not None]
    if len(short_values) != short or len(long_values) != long:
        return None
    base = mean(long_values)
    return mean(short_values) / base if base > 0 else None


def _beta_corr(stock: list[dict], market: list[dict], width: int = 63) -> tuple[float | None, float | None]:
    s = {r["date"]: r["_price"] for r in stock}
    m = {r["date"]: r["_price"] for r in market}
    dates = sorted(set(s) & set(m))
    if len(dates) < width + 1:
        return None, None
    dates = dates[-width-1:]
    sr = [log(s[dates[i]]/s[dates[i-1]]) for i in range(1, len(dates))]
    mr = [log(m[dates[i]]/m[dates[i-1]]) for i in range(1, len(dates))]
    sm, mm = mean(sr), mean(mr)
    cov = sum((a-sm)*(b-mm) for a,b in zip(sr,mr)) / len(sr)
    var_m = sum((b-mm)**2 for b in mr) / len(mr)
    var_s = sum((a-sm)**2 for a in sr) / len(sr)
    beta = cov / var_m if var_m > 0 else None
    corr = cov / sqrt(var_s*var_m) if var_s > 0 and var_m > 0 else None
    return beta, corr


def _relative_momentum(stock: list[dict], benchmark: list[dict], width: int = 63) -> float | None:
    s, b = _ret(stock, width), _ret(benchmark, width)
    return s - b if s is not None and b is not None else None


def compute(rows: list[dict], *, as_of: str | None = None,
            market_rows: list[dict] | None = None,
            sector_rows: list[dict] | None = None) -> dict:
    stock = _clean(rows, as_of)
    if not stock:
        return {"schema_version": "agent-features.v1", "status": "INSUFFICIENT_HISTORY",
                "as_of": as_of, "features": {}, "classification": {}}
    market = _clean(market_rows or [], as_of)
    sector = _clean(sector_rows or [], as_of)

    price = stock[-1]["_price"]
    sma20, sma50, sma200 = (_sma(stock, n) for n in (20,50,200))
    vol20, vol60 = _vol(stock,20), _vol(stock,60)
    beta63, corr63 = _beta_corr(stock, market,63) if market else (None,None)
    features = {
        "price": price,
        "momentum_5": _ret(stock,5),
        "momentum_21": _ret(stock,21),
        "momentum_63": _ret(stock,63),
        "momentum_126": _ret(stock,126),
        "price_vs_sma20": price/sma20-1 if sma20 else None,
        "price_vs_sma50": price/sma50-1 if sma50 else None,
        "price_vs_sma200": price/sma200-1 if sma200 else None,
        "sma20_slope_5": _sma_slope(stock,20,5),
        "sma50_slope_5": _sma_slope(stock,50,5),
        "realized_vol_20": vol20,
        "realized_vol_60": vol60,
        "atr14_pct": _atr_pct(stock,14),
        "rsi14": _rsi(stock,14),
        "price_zscore20": _zscore(stock,20),
        "drawdown_63": _drawdown(stock,63),
        "drawdown_252": _drawdown(stock,252),
        "volume_20_vs_60": _volume_ratio(stock,20,60),
        "beta_63_vs_spy": beta63,
        "correlation_63_vs_spy": corr63,
        "relative_momentum_63_vs_spy": _relative_momentum(stock,market,63) if market else None,
        "relative_momentum_63_vs_sector": _relative_momentum(stock,sector,63) if sector else None,
    }
    trend_votes = [v for v in (
        features["price_vs_sma20"], features["price_vs_sma50"],
        features["sma20_slope_5"], features["momentum_63"],
    ) if v is not None]
    vote_share = (sum(v > 0 for v in trend_votes)/len(trend_votes)
                  if trend_votes else None)
    if vote_share is None:
        trend = "UNKNOWN"
    elif vote_share >= .75:
        trend = "UP"
    elif vote_share <= .25:
        trend = "DOWN"
    else:
        trend = "MIXED"
    if vol20 is None or vol60 is None:
        vol_regime = "UNKNOWN"
    elif vol20 > vol60 * 1.25:
        vol_regime = "EXPANDING"
    elif vol20 < vol60 * .80:
        vol_regime = "CONTRACTING"
    else:
        vol_regime = "NORMAL"
    status = "OK" if len(stock) >= 64 else "PARTIAL"
    return {
        "schema_version": "agent-features.v1",
        "status": status,
        "as_of": stock[-1]["date"],
        "observations": len(stock),
        "features": features,
        "classification": {
            "trend": trend,
            "trend_positive_vote_share": round(vote_share,3) if vote_share is not None else None,
            "volatility_regime": vol_regime,
        },
        "limitations": [
            "descriptive point-in-time features only; no indicator is a trading rule",
            "adjusted-close returns are used for momentum; OHLC fields are used only when present",
        ],
    }


def build_for_ticker(conn, ticker: str, *, as_of: str | None = None) -> dict:
    from .. import db
    from ..regime import sector_etf
    company = db.fetch_company(conn, ticker) or {}
    stock = db.fetch_prices(conn, ticker, as_of)
    market = db.fetch_prices(conn, "SPY", as_of)
    sector_symbol = sector_etf(company.get("sector"))
    sector = db.fetch_prices(conn, sector_symbol, as_of) if sector_symbol else []
    result = compute(stock, as_of=as_of, market_rows=market, sector_rows=sector)
    result["ticker"] = ticker
    result["market_proxy"] = "SPY"
    result["sector_proxy"] = sector_symbol
    return result
