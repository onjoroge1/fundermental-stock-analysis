"""Historical replay of the agent's heuristic direction: a baseline, not a promotion.

The live direction test (agent_intelligence.direction) is forward-only and
needs 24 blocks of 20 sessions. This replay asks what the same heuristic rule
would have earned historically, so the forward evidence has a baseline:

- The rule is the live code, `state.assemble`, unchanged. Its inputs are
  rebuilt point in time at each month-end session: the fundamental composite
  from `backtest.engine.TickerData.observe` (filings available on or before
  the date), technical features from `features.compute` and regime from
  `RegimeFeatureProvider` (prices on or before the date). No news history
  exists, so news is missing, as in the live state when headlines are absent.
- Outcomes use the live reward v5: entry at the next close, exit 20 sessions
  later, stock return minus beta x SPY, round-trip costs, scaled by ex-ante
  volatility. FLAT scores zero.
- Hypotheses, unit and interval are fixed in PROTOCOL and hashed before any
  run. A result never changes trading, the live protocol or promotion.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import mean

from ..agents.contracts import digest
from ..agent_intelligence import inference, reward
from ..agent_intelligence.features import compute as technical_features
from ..agent_intelligence.outcomes import BETA_RANGE, DEFAULT_BETA, FILL_COST_BPS_PER_SIDE, HORIZON_SESSIONS
from ..agent_intelligence.state import assemble
from ..market_calendar import session_dates, session_offset

DIRECTIONS = {"BULLISH": "LONG", "BEARISH": "SHORT", "NEUTRAL": "FLAT"}
THRESHOLD = 0.25  # state.assemble's bias threshold, reused for the momentum baseline
POLICIES = ("heuristic", "no_fundamental", "momentum")

PROTOCOL = {
    "protocol_id": "heuristic-direction-replay.v1",
    "rule": "live state.assemble; fundamental composite PIT (TickerData.observe), technical (features.compute), "
            "regime (RegimeFeatureProvider) as of the decision session; news missing",
    "decision_sessions": "last XNYS session of each calendar month",
    "start": "2016-01-01",
    "window": f"entry at the next session close, exit {HORIZON_SESSIONS} sessions later (adjusted closes)",
    "reward": "risk-scaled-residual-paper-reward.v5: (direction x (stock return - beta x SPY return) - "
              f"{2 * FILL_COST_BPS_PER_SIDE:g} bps) / ex-ante volatility over the window; FLAT = 0",
    "beta": f"beta_63_vs_spy at the decision session, clipped to {list(BETA_RANGE)}, default {DEFAULT_BETA}",
    "unit": "calendar month of the decision: mean across stocks with a decision that month",
    "interval": "Newey-West lag-1 standard error of the monthly means, Student t(n-1) 97.5% quantile",
    "hypotheses": {
        "H1": "heuristic beats abstention: mean monthly reward > 0 and its lower 95% bound > 0",
        "H2": "heuristic beats the momentum baseline (technical component alone, same +/-0.25 threshold): "
              "mean paired monthly difference > 0 and lower bound > 0",
        "H3": "the fundamental component adds value: heuristic minus heuristic without the fundamental "
              "signal, mean paired monthly difference > 0 and lower bound > 0",
    },
    "reported_not_tested": ["2016-2020 and 2021-present subperiods", "direction mix", "hit rate"],
    "use": "baseline for the forward direction test; never changes trading, the live protocol or promotion",
    "caveats": [
        "Universe is today's coverage list: survivorship-biased.",
        "Fundamental scoring thresholds were hand-set in 2026 with knowledge of these histories.",
        "No historical headlines: the news component is missing throughout.",
        "The replay composite has no earnings-surprise (expectations) input.",
        "Short positions carry no borrow cost; fills are at closes with fixed costs.",
        "Adjusted closes are today's vintage.",
    ],
}
PROTOCOL_SHA256 = digest(PROTOCOL)


def month_end_sessions(start: str, end: str) -> list[str]:
    last = {}
    for day in session_dates(start, end):
        last[day[:7]] = day
    return [last[m] for m in sorted(last)]


def _direction_from(value: float | None) -> str:
    if value is None:
        return "FLAT"
    return "LONG" if value >= THRESHOLD else "SHORT" if value <= -THRESHOLD else "FLAT"


def _window_return(by_date: dict, entry: str, due: str) -> float | None:
    days = session_dates(entry, due)
    prices = [by_date.get(d) for d in days]
    if not prices or any(p is None or p <= 0 for p in prices):
        return None
    return prices[-1] / prices[0] - 1


def _adjusted(rows: list[dict]) -> dict:
    return {str(r["date"])[:10]: float(r["adj_close"]) for r in rows
            if r.get("adj_close") is not None and float(r["adj_close"]) > 0}


def decide(ticker: str, session: str, *, prices: list[dict], composite, spy: list[dict],
           sector: list[dict] | None, regime: dict | None) -> dict | None:
    """The three policies' directions at one decision session, or None."""
    technical = technical_features(prices, as_of=session, market_rows=spy, sector_rows=sector)
    if technical.get("as_of") != session or technical.get("status") != "OK":
        return None  # no bar for this session, or too little history
    news = {"status": "UNAVAILABLE"}
    full = assemble(ticker, {"fundamental_scores": {"composite_score": composite}}, technical, news,
                    regime=regime)
    without = assemble(ticker, {"fundamental_scores": {}}, technical, news, regime=regime)
    return {
        "heuristic": DIRECTIONS[full["direction"]],
        "no_fundamental": DIRECTIONS[without["direction"]],
        "momentum": _direction_from(full["signal_components"]["technical"]),
        "bias": full["bias_score"],
        "fundamental_signal": full["signal_components"]["fundamental"],
        "features": technical["features"],
    }


def rewards(session: str, decision: dict, *, stock: dict, spy: dict) -> dict | None:
    """Reward v5 for LONG and SHORT over the frozen window (FLAT is 0)."""
    entry = session_offset(session, 1)
    due = session_offset(entry, HORIZON_SESSIONS)
    stock_r, spy_r = _window_return(stock, entry, due), _window_return(spy, entry, due)
    if stock_r is None or spy_r is None:
        return None
    beta = decision["features"].get("beta_63_vs_spy")
    beta = DEFAULT_BETA if beta is None else min(BETA_RANGE[1], max(BETA_RANGE[0], float(beta)))
    scale = reward.risk_scale(decision["features"], HORIZON_SESSIONS)["risk_scale_pct"]
    out = {}
    for name, sign in (("LONG", 1), ("SHORT", -1)):
        out[name] = reward.compute(
            gross_return_pct=sign * (stock_r - beta * spy_r) * 100, max_drawdown_pct=0.0,
            capital_used_pct=10.0, turnover_pct=20.0, costs_pct=2 * FILL_COST_BPS_PER_SIDE / 100,
            risk_scale_pct=scale)["reward"]
    return {"entry_session": entry, "due_session": due, "beta": beta, "rewards": out}


def replay(universe: dict, *, spy: list[dict], qqq: list[dict] | None, sessions: list[str],
           regime_for=None) -> list[dict]:
    """One row per (stock, decision session) with a decision and a matured window.

    universe: ticker -> {"prices", "sector_rows", "observe": callable(as_of) -> obs or None}.
    regime_for: callable(ticker, session) -> regime dict (None leaves regime missing).
    """
    spy_adj = _adjusted(spy)
    rows = []
    for ticker, data in sorted(universe.items()):
        stock_adj = _adjusted(data["prices"])
        for session in sessions:
            observation = data["observe"](session)
            composite = (observation or {}).get("composite")
            regime = regime_for(ticker, session) if regime_for else None
            decision = decide(ticker, session, prices=data["prices"], composite=composite, spy=spy,
                              sector=data.get("sector_rows"), regime=regime)
            if decision is None:
                continue
            outcome = rewards(session, decision, stock=stock_adj, spy=spy_adj)
            if outcome is None:
                continue
            rows.append({"ticker": ticker, "session": session, **outcome,
                         **{p: decision[p] for p in POLICIES},
                         "bias": decision["bias"], "fundamental_signal": decision["fundamental_signal"]})
    return rows


def _score(row: dict, policy: str) -> float:
    choice = row[policy]
    return 0.0 if choice == "FLAT" else float(row["rewards"][choice])


def _interval(monthly: list[float]) -> dict:
    n = len(monthly)
    if n < 3:
        return {"months": n, "mean": mean(monthly) if monthly else None, "lower_95": None, "upper_95": None}
    m = mean(monthly)
    se = inference.newey_west_se(monthly)
    t = inference.t_quantile_975(n - 1)
    return {"months": n, "mean": m, "newey_west_se": se, "lower_95": m - t * se, "upper_95": m + t * se}


def evaluate(rows: list[dict], *, split: str = "2021-01-01") -> dict:
    by_month = defaultdict(list)
    for r in rows:
        by_month[r["session"][:7]].append(r)
    months = sorted(by_month)

    def monthly(fn, keep=lambda m: True):
        return [mean(fn(r) for r in by_month[m]) for m in months if keep(m)]

    def summary(fn):
        return {"full": _interval(monthly(fn)),
                "2016_2020": _interval(monthly(fn, lambda m: m < split[:7])),
                "2021_present": _interval(monthly(fn, lambda m: m >= split[:7]))}

    policies = {p: summary(lambda r, p=p: _score(r, p)) for p in POLICIES}
    h2 = summary(lambda r: _score(r, "heuristic") - _score(r, "momentum"))
    h3 = summary(lambda r: _score(r, "heuristic") - _score(r, "no_fundamental"))

    def verdict(stat):
        lower = stat["full"]["lower_95"]
        if lower is None:
            return "INSUFFICIENT_MONTHS"
        return "SUPPORTED" if stat["full"]["mean"] > 0 and lower > 0 else "NOT_SUPPORTED"

    mix = {p: {d: sum(r[p] == d for r in rows) for d in ("LONG", "SHORT", "FLAT")} for p in POLICIES}
    active = [r for r in rows if r["heuristic"] != "FLAT"]
    return {
        "protocol_id": PROTOCOL["protocol_id"], "protocol_sha256": PROTOCOL_SHA256,
        "decisions": len(rows), "stocks": len({r["ticker"] for r in rows}),
        "first_session": rows[0]["session"] if rows else None,
        "last_session": max((r["session"] for r in rows), default=None),
        "policies": policies,
        "hypotheses": {
            "H1_heuristic_vs_abstention": {**policies["heuristic"], "verdict": verdict(policies["heuristic"])},
            "H2_heuristic_minus_momentum": {**h2, "verdict": verdict(h2)},
            "H3_heuristic_minus_no_fundamental": {**h3, "verdict": verdict(h3)},
        },
        "direction_mix": mix,
        "heuristic_hit_rate": (sum(_score(r, "heuristic") > 0 for r in active) / len(active)) if active else None,
        "use": PROTOCOL["use"],
        "caveats": PROTOCOL["caveats"],
    }


def load(conn, tickers: list[str]) -> tuple[dict, list[dict], list[dict], callable]:
    """Read-only inputs for the replay from the research database."""
    from .. import db
    from ..regime import RegimeFeatureProvider, sector_etf
    from .engine import TickerData

    spy, qqq = db.fetch_prices(conn, "SPY"), db.fetch_prices(conn, "QQQ")
    sector_cache: dict = {}
    universe, providers = {}, {}
    for ticker in tickers:
        data = TickerData(conn, ticker)
        symbol = sector_etf(data.sector)
        if symbol and symbol not in sector_cache:
            sector_cache[symbol] = db.fetch_prices(conn, symbol)
        sector_rows = sector_cache.get(symbol) or []
        universe[ticker] = {"prices": data.prices, "sector_rows": sector_rows, "observe": data.observe}
        providers[ticker] = RegimeFeatureProvider(spy, qqq_rows=qqq, sector_rows=sector_rows)

    def regime_for(ticker, session):
        return providers[ticker].features_as_of(session)

    return universe, spy, qqq, regime_for


def run(conn, *, end: str, tickers: list[str]) -> dict:
    universe, spy, qqq, regime_for = load(conn, tickers)
    sessions = month_end_sessions(PROTOCOL["start"], end)
    rows = replay(universe, spy=spy, qqq=qqq, sessions=sessions, regime_for=regime_for)
    return {"protocol": PROTOCOL, "result": evaluate(rows), "rows": rows}
