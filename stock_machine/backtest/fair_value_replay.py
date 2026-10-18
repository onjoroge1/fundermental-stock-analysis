"""Pre-registered replay: trade solid companies toward a fundamental fair value.

The owner's design: fundamentals select solid companies and set a fair value;
the trade goes toward it, long below and short above, timed by price action;
the gap closes over months, not weeks. No systematic historical fair value
existed (report fair values are analyst-authored), so two simple,
assumption-light ones are rebuilt point in time each month:

- own_history: the stock's median P/E over the previous 36-60 months times
  current TTM earnings, so gap = median P/E / current P/E - 1.
- sector: the median current P/E of its sector peers (at least 4 with
  positive earnings), else the universe median.

Solid company: profitability and financial-health component scores of at
least 50 and positive TTM earnings, all as of the decision. Trade toward
fair value when the gap is at least 20%; "timed" enters only when the
technical component already points toward fair value. Windows of 60 and 120
sessions use reward v5 (stock-specific, costs, ex-ante volatility).

Five hypotheses are tested together: each 95% interval is widened to a
two-sided 99.5% t quantile (Bonferroni), with a Newey-West lag matching the
overlap of monthly decisions. News is not testable (no history).
"""
from __future__ import annotations

from collections import defaultdict
from math import ceil
from statistics import mean, median

from ..agents.contracts import digest
from ..agent_intelligence import inference
from . import direction_replay as dr

HORIZONS = (60, 120)
GAP_THRESHOLD = 0.20
QUALITY_MIN = 50.0
HISTORY_MONTHS = (36, 60)
HISTORY_START = "2012-01-01"
MIN_SECTOR_NAMES = 4
MIN_UNIVERSE_NAMES = 9
MIN_ACTIVE_MONTHS = 24
Z_FAMILY = 2.575829  # two-sided 99.5% normal quantile: 5 tests at family-wise 95%

POLICIES = {
    "own_timed_120": ("own_gap", True, 120),
    "own_untimed_120": ("own_gap", False, 120),
    "own_timed_60": ("own_gap", True, 60),
    "sector_timed_120": ("sector_gap", True, 120),
}

PROTOCOL = {
    "protocol_id": "fair-value-convergence.v1",
    "base_sha256": dr.PROTOCOL_SHA256,
    "fair_value": {
        "own_history": f"median P/E of the stock's previous {HISTORY_MONTHS[0]}-{HISTORY_MONTHS[1]} month-end "
                       f"observations (from {HISTORY_START}), gap = median / current P/E - 1",
        "sector": f"median current P/E of sector peers with positive earnings (>= {MIN_SECTOR_NAMES}), else the "
                  f"universe median (>= {MIN_UNIVERSE_NAMES}); gap = median / current P/E - 1",
    },
    "solid_company": f"profitability and financial-health scores >= {QUALITY_MIN:g} and positive TTM earnings",
    "trade": f"LONG if gap >= +{GAP_THRESHOLD:.0%}, SHORT if gap <= -{GAP_THRESHOLD:.0%}; timed variants only "
             "when the technical component has the same sign",
    "windows": list(HORIZONS),
    "reward": "risk-scaled-residual-paper-reward.v5 over the window; FLAT = 0",
    "unit": "calendar month of the decision (2016-01 onward)",
    "interval": "Newey-West lag ceil(window/21) on monthly means; t(n-1) at a two-sided 99.5% quantile "
                "(Bonferroni over the five hypotheses)",
    "hypotheses": {
        "H9": "own-history fair value, solid companies, timed, 120 sessions beats abstention (primary)",
        "H10": "the same, untimed",
        "H11": "the same, timed, 60 sessions",
        "H12": "sector fair value, solid companies, timed, 120 sessions",
        "H13": "timing adds value: H9 policy minus H10 policy",
    },
    "coverage": f"at least {MIN_ACTIVE_MONTHS} active months, else INSUFFICIENT_COVERAGE",
    "holdout": "decisions after 2026-08 form a confirmatory forward sample; this run uses 2016-01..2026-08",
    "not_testable": ["news timing (no historical headlines)", "option expression (no historical surfaces)"],
    "use": dr.PROTOCOL["use"],
    "caveats": dr.PROTOCOL["caveats"] + [
        "P/E fair values ignore growth differences; sector labels are today's.",
        "Monthly decisions with 60/120-session windows overlap; the HAC lag accounts for it.",
    ],
}
PROTOCOL_SHA256 = digest(PROTOCOL)


def _direction(gap: float | None) -> str:
    if gap is None:
        return "FLAT"
    return "LONG" if gap >= GAP_THRESHOLD else "SHORT" if gap <= -GAP_THRESHOLD else "FLAT"


def solid(detail: dict | None) -> bool:
    d = detail or {}
    pe = d.get("pe_ttm")
    return (pe is not None and pe > 0
            and (d.get("profitability_score") or 0) >= QUALITY_MIN
            and (d.get("financial_health_score") or 0) >= QUALITY_MIN)


def own_history_gap(history: list[float], pe: float | None) -> float | None:
    """history: previous month-end P/E values (positive only), oldest first."""
    window = history[-HISTORY_MONTHS[1]:]
    if pe is None or pe <= 0 or len(window) < HISTORY_MONTHS[0]:
        return None
    return median(window) / pe - 1


def sector_gaps(observations: dict) -> dict:
    """observations: ticker -> (sector, pe). Returns ticker -> gap."""
    positive = {t: (s, pe) for t, (s, pe) in observations.items() if pe is not None and pe > 0}
    universe = median(pe for _, pe in positive.values()) if len(positive) >= MIN_UNIVERSE_NAMES else None
    by_sector = defaultdict(list)
    for sector, pe in positive.values():
        by_sector[sector].append(pe)
    out = {}
    for t, (sector, pe) in positive.items():
        peers = by_sector.get(sector) or []
        reference = median(peers) if len(peers) >= MIN_SECTOR_NAMES else universe
        if reference is not None:
            out[t] = reference / pe - 1
    return out


def replay(universe: dict, *, spy: list[dict], sessions: list[str], decision_start: str,
           regime_for=None) -> list[dict]:
    """universe: ticker -> {"prices", "sector_rows", "sector", "observe" (detail=True)}."""
    spy_adj = dr._adjusted(spy)
    snapshots = defaultdict(dict)  # session -> ticker -> (sector, pe)
    per_ticker = {}
    for ticker, data in sorted(universe.items()):
        history, rows = [], []
        for session in sessions:
            observation = data["observe"](session) or {}
            detail = observation.get("detail") or {}
            pe = detail.get("pe_ttm")
            gap = own_history_gap(history, pe)
            if pe is not None and pe > 0:
                history.append(float(pe))
                snapshots[session][ticker] = (data.get("sector"), float(pe))
            if session >= decision_start:
                rows.append((session, observation, detail, gap))
        per_ticker[ticker] = rows
    sector_by_session = {s: sector_gaps(obs) for s, obs in snapshots.items()}

    out = []
    for ticker, rows in per_ticker.items():
        data = universe[ticker]
        stock_adj = dr._adjusted(data["prices"])
        for session, observation, detail, gap in rows:
            decision = dr.decide(ticker, session, prices=data["prices"], composite=observation.get("composite"),
                                 spy=spy, sector=data.get("sector_rows"),
                                 regime=regime_for(ticker, session) if regime_for else None)
            if decision is None:
                continue
            windows = {h: dr.rewards(session, decision, stock=stock_adj, spy=spy_adj, horizon=h) for h in HORIZONS}
            if any(w is None for w in windows.values()):
                continue
            row = {"ticker": ticker, "session": session, "solid": solid(detail),
                   "own_gap": gap, "sector_gap": sector_by_session.get(session, {}).get(ticker),
                   "technical_signal": decision["technical_signal"],
                   "rewards": {str(h): w["rewards"] for h, w in windows.items()}}
            for name, (field, timed, _) in POLICIES.items():
                side = _direction(row[field]) if row["solid"] else "FLAT"
                t = row["technical_signal"]
                if timed and side != "FLAT" and not (t is not None and (t > 0) == (side == "LONG") and t != 0):
                    side = "FLAT"
                row[name] = side
            out.append(row)
    return out


def _score(row: dict, policy: str) -> float:
    side, horizon = row[policy], POLICIES[policy][2]
    return 0.0 if side == "FLAT" else float(row["rewards"][str(horizon)][side])


def _interval(values: list[float], horizon: int) -> dict:
    n = len(values)
    if n < 3:
        return {"months": n, "mean": mean(values) if values else None, "lower": None, "upper": None}
    m = mean(values)
    se = inference.newey_west_se(values, lag=ceil(horizon / 21))
    t = inference.t_quantile(n - 1, Z_FAMILY)
    return {"months": n, "mean": m, "newey_west_se": se, "lower": m - t * se, "upper": m + t * se}


def evaluate(rows: list[dict]) -> dict:
    by_month = defaultdict(list)
    for r in rows:
        by_month[r["session"][:7]].append(r)
    months = sorted(by_month)

    def monthly(fn):
        return [mean(fn(r) for r in by_month[m]) for m in months]

    def active(policy):
        return sum(any(r[policy] != "FLAT" for r in by_month[m]) for m in months)

    def test(values, horizon, coverage):
        stat = {**_interval(values, horizon), "active_months": coverage}
        if coverage < MIN_ACTIVE_MONTHS:
            return {**stat, "verdict": "INSUFFICIENT_COVERAGE"}
        if stat["lower"] is None:
            return {**stat, "verdict": "INSUFFICIENT_MONTHS"}
        return {**stat, "verdict": "SUPPORTED" if stat["mean"] > 0 and stat["lower"] > 0 else "NOT_SUPPORTED"}

    names = {"H9": "own_timed_120", "H10": "own_untimed_120", "H11": "own_timed_60", "H12": "sector_timed_120"}
    hypotheses = {h: test(monthly(lambda r, p=p: _score(r, p)), POLICIES[p][2], active(p)) for h, p in names.items()}
    hypotheses["H13"] = test(monthly(lambda r: _score(r, "own_timed_120") - _score(r, "own_untimed_120")),
                             120, active("own_timed_120"))
    split = "2021-01"
    subperiods = {p: {"2016_2020": _interval([v for m, v in zip(months, monthly(lambda r, p=p: _score(r, p)))
                                              if m < split], POLICIES[p][2]),
                      "2021_present": _interval([v for m, v in zip(months, monthly(lambda r, p=p: _score(r, p)))
                                                 if m >= split], POLICIES[p][2])}
                  for p in POLICIES}
    coverage = {}
    for p in POLICIES:
        act = [_score(r, p) for r in rows if r[p] != "FLAT"]
        coverage[p] = {"active_decisions": len(act), "active_months": active(p),
                       "mean_reward_when_active": mean(act) if act else None,
                       "hit_rate_when_active": sum(v > 0 for v in act) / len(act) if act else None,
                       "long": sum(r[p] == "LONG" for r in rows), "short": sum(r[p] == "SHORT" for r in rows)}
    return {"protocol_id": PROTOCOL["protocol_id"], "protocol_sha256": PROTOCOL_SHA256,
            "decisions": len(rows), "stocks": len({r["ticker"] for r in rows}), "months": len(months),
            "solid_share": (sum(r["solid"] for r in rows) / len(rows)) if rows else None,
            "first_session": min((r["session"] for r in rows), default=None),
            "last_session": max((r["session"] for r in rows), default=None),
            "hypotheses": hypotheses, "subperiods": subperiods, "coverage": coverage,
            "not_testable": PROTOCOL["not_testable"], "use": PROTOCOL["use"], "caveats": PROTOCOL["caveats"]}


def load(conn, tickers: list[str]):
    universe, spy, qqq, regime_for = dr.load(conn, tickers)
    for ticker in tickers:
        data = universe[ticker]["ticker_data"]
        universe[ticker].update(observe=lambda as_of, d=data: d.observe(as_of, detail=True), sector=data.sector)
    return universe, spy, regime_for


def run(conn, *, end: str, tickers: list[str]) -> dict:
    universe, spy, regime_for = load(conn, tickers)
    rows = replay(universe, spy=spy, sessions=dr.month_end_sessions(HISTORY_START, end),
                  decision_start=dr.PROTOCOL["start"], regime_for=regime_for)
    return {"protocol": PROTOCOL, "result": evaluate(rows), "rows": rows}
