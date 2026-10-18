"""Pre-registered replay of candidate signals: earnings surprise, estimate
revisions, a reverse-DCF value gap, and the value gap timed by technicals.

Same decision sessions, window, reward (v5), monthly unit and interval as
`direction_replay` (protocol v1, whose result stands). Each policy maps a
point-in-time signal to LONG, SHORT or FLAT; FLAT scores zero, so a sparse
signal is judged as an equal-weight book that holds cash when it has nothing
to say. A policy needs at least MIN_ACTIVE_MONTHS months with an active
position before its hypothesis is tested; otherwise its verdict is
INSUFFICIENT_COVERAGE, never a pass.

Signals (all known on or before the decision session):
- surprise: the latest earnings surprise from `known_surprises` (strict
  availability), if the event is within SURPRISE_MAX_AGE_DAYS.
- revision: 30-day consensus EPS revision from `consensus_revision`.
- value_gap: trailing 3-year revenue CAGR minus the growth the price implies
  (`implied_growth_from_price` on trailing FCF, 9% discount, 2.5% terminal,
  5 years). Positive means the business has grown faster than the price needs.
- value_timed: the value-gap tercile, entered only when the technical
  component agrees in sign.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import mean

from ..agents.contracts import digest
from ..expectations import consensus_revision, known_surprises
from ..valuation_tools import implied_growth_from_price
from . import direction_replay as dr

SURPRISE_MAX_AGE_DAYS = 92
SURPRISE_THRESHOLD_PCT = 2.0
REVISION_THRESHOLD_PCT = 1.0
MIN_ACTIVE_MONTHS = 24
MIN_GAP_NAMES = 9  # terciles need at least three names each
POLICIES = ("surprise", "revision", "value_gap", "value_timed")

PROTOCOL = {
    "protocol_id": "signal-replay.v2",
    "supersedes": "v1 computed the value gap from a 3y revenue CAGR the engine never filled (annual periods "
                  "are not passed), so no value-gap position was ever taken; no value-gap outcome was seen. "
                  "Surprise and revision definitions are unchanged.",
    "base": dr.PROTOCOL["protocol_id"] + " sessions, window, reward, unit and interval",
    "base_sha256": dr.PROTOCOL_SHA256,
    "signals": {
        "surprise": f"latest known EPS surprise (strict availability), event within {SURPRISE_MAX_AGE_DAYS} "
                    f"days: LONG >= +{SURPRISE_THRESHOLD_PCT}%, SHORT <= -{SURPRISE_THRESHOLD_PCT}%",
        "revision": f"30-day consensus EPS revision: LONG >= +{REVISION_THRESHOLD_PCT}%, "
                    f"SHORT <= -{REVISION_THRESHOLD_PCT}%",
        "value_gap": "3y revenue CAGR (TTM revenue vs TTM revenue 12 quarters earlier, annualized) minus "
                     "reverse-DCF implied CAGR (TTM FCF, 9% discount, 2.5% terminal, "
                     f"5 years); monthly terciles among names with a gap (>= {MIN_GAP_NAMES}): top LONG, "
                     "bottom SHORT",
        "value_timed": "value_gap direction, entered only when the technical component has the same sign",
    },
    "hypotheses": {
        "H4": "surprise beats abstention",
        "H5": "revision beats abstention",
        "H6": "value_gap beats abstention",
        "H7": "value_timed beats abstention",
        "H8": "value_timed beats value_gap (technical timing adds value)",
    },
    "test": "mean monthly reward (or paired difference) > 0 and its lower 95% bound > 0",
    "coverage": f"at least {MIN_ACTIVE_MONTHS} months with an active position, else INSUFFICIENT_COVERAGE",
    "not_testable": "fair value delivered through options: no historical option surfaces exist "
                    "(capture is forward-only)",
    "use": dr.PROTOCOL["use"],
    "caveats": dr.PROTOCOL["caveats"] + [
        "Surprise and consensus history exist only where this system stored them with their observation "
        "time; nothing is backfilled from current estimates.",
        "Reverse-DCF conventions (9%, 2.5%, 5 years) are documented choices, not fitted.",
    ],
}
PROTOCOL_SHA256 = digest(PROTOCOL)


def _sign(value, threshold):
    if value is None:
        return "FLAT"
    return "LONG" if value >= threshold else "SHORT" if value <= -threshold else "FLAT"


def surprise_signal(surprises: list[dict], session: str) -> float | None:
    past = known_surprises(surprises, session)
    if not past:
        return None
    latest = past[-1]
    age = (date.fromisoformat(session) - date.fromisoformat(str(latest["date"])[:10])).days
    return float(latest["surprise_pct"]) if age <= SURPRISE_MAX_AGE_DAYS else None


def revision_signal(consensus: list[dict], session: str) -> float | None:
    return consensus_revision(consensus, session)["eps_revision_pct"]


def value_gap(detail: dict | None) -> float | None:
    d = detail or {}
    cagr = d.get("revenue_cagr_3y_pct")
    implied = implied_growth_from_price(d.get("market_cap"), d.get("net_debt"), d.get("fcf_ttm"))
    if cagr is None or not implied or implied.get("implied_cagr_pct") is None:
        return None
    return float(cagr) - float(implied["implied_cagr_pct"])


def _terciles(gaps: dict) -> dict:
    """ticker -> LONG (top third), SHORT (bottom third) or FLAT, per month."""
    if len(gaps) < MIN_GAP_NAMES:
        return {t: "FLAT" for t in gaps}
    ordered = sorted(gaps, key=lambda t: (gaps[t], t))
    third = len(ordered) // 3
    out = {t: "FLAT" for t in ordered}
    out.update({t: "SHORT" for t in ordered[:third]})
    out.update({t: "LONG" for t in ordered[-third:]})
    return out


def replay(universe: dict, *, spy: list[dict], sessions: list[str], regime_for=None) -> list[dict]:
    """universe: ticker -> {"prices", "sector_rows", "observe" (detail=True), "surprises", "consensus"}."""
    spy_adj = dr._adjusted(spy)
    pending = []
    for ticker, data in sorted(universe.items()):
        stock_adj = dr._adjusted(data["prices"])
        for session in sessions:
            observation = data["observe"](session)
            decision = dr.decide(ticker, session, prices=data["prices"],
                                 composite=(observation or {}).get("composite"), spy=spy,
                                 sector=data.get("sector_rows"),
                                 regime=regime_for(ticker, session) if regime_for else None)
            if decision is None:
                continue
            outcome = dr.rewards(session, decision, stock=stock_adj, spy=spy_adj)
            if outcome is None:
                continue
            technical = decision["technical_signal"]
            pending.append({
                "ticker": ticker, "session": session, **outcome, "technical_signal": technical,
                "surprise_pct": surprise_signal(data.get("surprises") or [], session),
                "revision_pct": revision_signal(data.get("consensus") or [], session),
                "value_gap_pct": value_gap((observation or {}).get("detail")),
            })
    by_session = defaultdict(dict)
    for r in pending:
        if r["value_gap_pct"] is not None:
            by_session[r["session"]][r["ticker"]] = r["value_gap_pct"]
    tercile = {s: _terciles(g) for s, g in by_session.items()}
    for r in pending:
        r["surprise"] = _sign(r["surprise_pct"], SURPRISE_THRESHOLD_PCT)
        r["revision"] = _sign(r["revision_pct"], REVISION_THRESHOLD_PCT)
        r["value_gap"] = tercile.get(r["session"], {}).get(r["ticker"], "FLAT")
        t = r["technical_signal"]
        agrees = t is not None and ((r["value_gap"] == "LONG" and t > 0) or (r["value_gap"] == "SHORT" and t < 0))
        r["value_timed"] = r["value_gap"] if agrees else "FLAT"
    return pending


def evaluate(rows: list[dict]) -> dict:
    by_month = defaultdict(list)
    for r in rows:
        by_month[r["session"][:7]].append(r)
    months = sorted(by_month)

    def score(r, p):
        return dr._score(r, p)

    def series(fn):
        return [mean(fn(r) for r in by_month[m]) for m in months]

    def active_months(p):
        return sum(any(r[p] != "FLAT" for r in by_month[m]) for m in months)

    def verdict(values, coverage):
        stat = dr._interval(values)
        if coverage < MIN_ACTIVE_MONTHS:
            return {**stat, "active_months": coverage, "verdict": "INSUFFICIENT_COVERAGE"}
        if stat["lower_95"] is None:
            return {**stat, "active_months": coverage, "verdict": "INSUFFICIENT_MONTHS"}
        ok = stat["mean"] > 0 and stat["lower_95"] > 0
        return {**stat, "active_months": coverage, "verdict": "SUPPORTED" if ok else "NOT_SUPPORTED"}

    coverage = {p: active_months(p) for p in POLICIES}
    hypotheses = {
        "H4_surprise": verdict(series(lambda r: score(r, "surprise")), coverage["surprise"]),
        "H5_revision": verdict(series(lambda r: score(r, "revision")), coverage["revision"]),
        "H6_value_gap": verdict(series(lambda r: score(r, "value_gap")), coverage["value_gap"]),
        "H7_value_timed": verdict(series(lambda r: score(r, "value_timed")), coverage["value_timed"]),
        "H8_value_timed_minus_value_gap": verdict(
            series(lambda r: score(r, "value_timed") - score(r, "value_gap")), coverage["value_timed"]),
    }
    active = {p: [score(r, p) for r in rows if r[p] != "FLAT"] for p in POLICIES}
    return {
        "protocol_id": PROTOCOL["protocol_id"], "protocol_sha256": PROTOCOL_SHA256,
        "decisions": len(rows), "stocks": len({r["ticker"] for r in rows}), "months": len(months),
        "first_session": min((r["session"] for r in rows), default=None),
        "last_session": max((r["session"] for r in rows), default=None),
        "hypotheses": hypotheses,
        "coverage": {p: {"active_decisions": len(active[p]), "active_months": coverage[p],
                         "first_active": min((r["session"] for r in rows if r[p] != "FLAT"), default=None),
                         "mean_reward_when_active": mean(active[p]) if active[p] else None,
                         "hit_rate_when_active": (sum(v > 0 for v in active[p]) / len(active[p]))
                         if active[p] else None,
                         "mix": {d: sum(r[p] == d for r in rows) for d in ("LONG", "SHORT", "FLAT")}}
                     for p in POLICIES},
        "not_testable": PROTOCOL["not_testable"],
        "use": PROTOCOL["use"], "caveats": PROTOCOL["caveats"],
    }


def load(conn, tickers: list[str]):
    universe, spy, qqq, regime_for = dr.load(conn, tickers)
    for ticker in tickers:
        data = universe[ticker]["ticker_data"]
        universe[ticker].update(observe=lambda as_of, d=data: d.observe(as_of, detail=True),
                                surprises=data.surprise_history, consensus=data.consensus_history)
    return universe, spy, regime_for


def run(conn, *, end: str, tickers: list[str]) -> dict:
    universe, spy, regime_for = load(conn, tickers)
    rows = replay(universe, spy=spy, sessions=dr.month_end_sessions(dr.PROTOCOL["start"], end),
                  regime_for=regime_for)
    return {"protocol": PROTOCOL, "result": evaluate(rows), "rows": rows}
