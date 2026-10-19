"""Pre-registered daily event-strategy replay (development and hold-out stages).

Strategies, entries and exits are the pure rules in
agent_intelligence.strategy_signals; this module only replays them over the
covered universe and scores them the way every earlier replay did (reward v5:
stock-specific residual against SPY, round-trip costs, ex-ante volatility over
the strategy's maximum hold).

Stages: "dev" scores signals from 2016-01-04 to 2023-12-29 and is descriptive
only. "holdout" scores 2024-01-02 to 2026-08-31 once, as the confirmatory test
of S1-S3 at family-wise 95% (Bonferroni); it refuses to run until the protocol
is complete (earnings-announcement data pinned) and its hash matches the
pinned value. S4 is exploratory in both stages. Nothing here trades.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import mean

from ..agent_intelligence import inference, reward
from ..agent_intelligence import strategy_signals as ss
from ..agents.contracts import digest
from ..market_calendar import session_dates
from . import direction_replay as dr
from . import fair_value_replay as fv

DEV = ("2016-01-04", "2023-12-29")
HOLDOUT = ("2024-01-02", "2026-08-31")
HAC_LAG = 3
Z_FAMILY = 2.393980          # two-sided 98.33%: three confirmatory hypotheses at family-wise 95%
MIN_ACTIVE_MONTHS = 18
MIN_TRADES = 60
CONFIRMATORY = ("S1_volume_breakout", "S2_trend_pullback", "S3_post_earnings")
EXPLORATORY = ("S4_value_momentum",)
STRATEGY_BEHAVIOR_SHA256 = "82103fafbce22ef7a06c3c9cf4154a67380f87f8819b10319ea61f2291ee39be"
EARNINGS_ANNOUNCEMENTS_SHA256 = "6937f99aaaf97eb9405b7ca170d07f4ce474e6a5dc94d48463762df060fab4f5"
EARNINGS_ANNOUNCEMENTS_PATH = "docs/evidence/earnings-announcements-v1.json"

PROTOCOL = {
    "protocol_id": "event-strategy-replay.v1",
    "base_sha256": dr.PROTOCOL_SHA256,
    "strategies": {k: {**ss.RULES[k], "definition": ss.DEFINITIONS[k]} for k in ss.RULES},
    "exits": ss.DEFINITIONS["exits"],
    "timing": ss.DEFINITIONS["timing"],
    "strategy_behavior_sha256": STRATEGY_BEHAVIOR_SHA256,
    "earnings_announcements_sha256": EARNINGS_ANNOUNCEMENTS_SHA256,
    "reward": "risk-scaled-residual-paper-reward.v5 for LONG between entry and exit, scaled by ex-ante "
              "volatility over the strategy's maximum hold (decision-time state only)",
    "unit": "calendar month of the signal session: sum of that month's trade rewards divided by the number of "
            "universe stocks with a complete bar and 60 sessions of history at the month's last session; months "
            "without trades score 0 (an equal-weight book holding cash)",
    "splits": {"dev": list(DEV), "holdout": list(HOLDOUT), "assignment": "by signal session"},
    "interval": f"Newey-West lag {HAC_LAG} on monthly values; Student t(n-1)",
    "family": {"hypotheses": list(CONFIRMATORY), "z": Z_FAMILY,
               "test": "hold-out monthly mean > 0 and its family-wise lower bound > 0"},
    "coverage": f"hold-out needs at least {MIN_ACTIVE_MONTHS} active months and {MIN_TRADES} scored trades per "
                "strategy, else INSUFFICIENT_COVERAGE",
    "exclusions": "a trade whose path lacks a bar is EXCLUDED_MISSING_BARS, counted, and locks the stock until "
                  "its maximum hold so exclusion cannot depend on the outcome",
    "exploratory": {"strategies": list(EXPLORATORY),
                    "diagnostics": ["fixed maximum-hold exit from the same entries (paired difference)",
                                    "costs doubled"], "interval": "unadjusted 95%"},
    "use": "evidence for review; never changes trading, the live protocol or promotion",
    "caveats": dr.PROTOCOL["caveats"] + [
        "The hold-out overlaps data seen by earlier research (momentum subperiods, technical setups); treat it "
        "as pseudo-out-of-sample. The forward shadow, registered before the hold-out run, is the clean test.",
        "Trailing stops and exits are judged on closes and filled at the next close; intraday stops are not "
        "modelled.",
    ],
}
PROTOCOL_SHA256 = digest(PROTOCOL)


def split_of(session: str) -> str | None:
    if DEV[0] <= session <= DEV[1]:
        return "dev"
    if HOLDOUT[0] <= session <= HOLDOUT[1]:
        return "holdout"
    return None


def holdout_ready() -> tuple[bool, str]:
    if EARNINGS_ANNOUNCEMENTS_SHA256 is None:
        return False, "EARNINGS_DATA_NOT_PINNED"
    if ss.behavior_digest() != STRATEGY_BEHAVIOR_SHA256:
        return False, "STRATEGY_BEHAVIOR_CHANGED"
    return True, "READY"


# ---- trades ------------------------------------------------------------------------

def _features(rows, spy_rows, session):
    from ..agent_intelligence.features import compute
    out = compute(rows, as_of=session, market_rows=spy_rows)
    return out["features"] if out.get("status") == "OK" and out.get("as_of") == session else None


def trades_for(ticker: str, *, rows: list[dict], spy_rows: list[dict], bars: ss.Bars, spy_bars: ss.Bars,
               strategy: str, entry, stage_end: str) -> list[dict]:
    """Every trade one strategy takes in one stock, oldest first (one open at a time)."""
    rule = ss.RULES[strategy]
    adj, spy_adj = dr._adjusted(rows), dr._adjusted(spy_rows)
    out, free_from = [], 0
    for i, session in enumerate(bars.sessions):
        if session > stage_end:
            break
        if i < free_from or i < ss.WINDOW or i + 1 >= len(bars.sessions) or split_of(session) is None:
            continue
        context = entry(bars, i)
        if not context:
            continue
        gap_level = context.get("gap_level") if isinstance(context, dict) else None
        atr_ref = context.get("atr_ref", bars.atr[i]) if isinstance(context, dict) else bars.atr[i]
        exit_i, reason = ss.simulate_exit(bars, i + 1, strategy, atr_ref=atr_ref, gap_level=gap_level)
        if exit_i == "PENDING":
            out.append({"ticker": ticker, "strategy": strategy, "signal": session, "status": "PENDING"})
            free_from = len(bars.sessions)
            continue
        if exit_i == "EXCLUDED_MISSING_BARS":
            out.append({"ticker": ticker, "strategy": strategy, "signal": session,
                        "status": "EXCLUDED_MISSING_BARS"})
            free_from = i + 1 + rule["max_hold"]
            continue
        features = _features(rows, spy_rows, session)
        if features is None:
            out.append({"ticker": ticker, "strategy": strategy, "signal": session, "status": "NO_FEATURES"})
            free_from = exit_i
            continue
        entry_s, exit_s = bars.sessions[i + 1], bars.sessions[exit_i]
        path = dr.rewards_between(entry_s, exit_s, {"features": features}, stock=adj, spy=spy_adj,
                                  horizon=rule["max_hold"])
        fixed_i = min(i + 1 + rule["max_hold"], len(bars.sessions) - 1)
        fixed = dr.rewards_between(entry_s, bars.sessions[fixed_i], {"features": features}, stock=adj,
                                   spy=spy_adj, horizon=rule["max_hold"])
        if path is None:
            out.append({"ticker": ticker, "strategy": strategy, "signal": session,
                        "status": "EXCLUDED_MISSING_BARS"})
            free_from = i + 1 + rule["max_hold"]
            continue
        scale = reward.risk_scale(features, rule["max_hold"])["risk_scale_pct"]
        value = path["rewards"]["LONG"]
        out.append({"ticker": ticker, "strategy": strategy, "signal": session, "entry": entry_s, "exit": exit_s,
                    "reason": reason, "status": "SCORED", "sessions_held": exit_i - (i + 1),
                    "reward": value, "reward_cost_2x": value - 0.2 / scale,
                    "reward_fixed_hold": fixed["rewards"]["LONG"] if fixed else None})
        free_from = exit_i
    return out


def book(trades: list[dict], denominators: dict, months: list[str]) -> list[float]:
    by_month = defaultdict(list)
    for t in trades:
        if t["status"] == "SCORED":
            by_month[t["signal"][:7]].append(t["reward"])
    return [sum(by_month.get(m, [])) / denominators[m] for m in months if denominators.get(m)]


def interval(values: list[float], z: float | None = None) -> dict:
    n = len(values)
    if n < 3:
        return {"months": n, "mean": mean(values) if values else None, "lower": None, "upper": None}
    m = mean(values)
    se = inference.newey_west_se(values, lag=HAC_LAG)
    t = inference.t_quantile(n - 1, z) if z else inference.t_quantile_975(n - 1)
    return {"months": n, "mean": m, "newey_west_se": se, "lower": m - t * se, "upper": m + t * se}


def evaluate(trades: list[dict], denominators: dict, *, stage: str) -> dict:
    splits = ("dev",) if stage == "dev" else ("dev", "holdout")
    out = {"protocol_id": PROTOCOL["protocol_id"], "protocol_sha256": PROTOCOL_SHA256, "stage": stage,
           "strategies": {}}
    for strategy in (*CONFIRMATORY, *EXPLORATORY):
        mine = [t for t in trades if t["strategy"] == strategy]
        result = {}
        for split in splits:
            lo, hi = (DEV if split == "dev" else HOLDOUT)
            months = sorted({m for m in denominators if lo[:7] <= m <= hi[:7]})
            scoped = [t for t in mine if split_of(t["signal"]) == split]
            scored = [t for t in scoped if t["status"] == "SCORED"]
            values = book(scoped, denominators, months)
            active = len({t["signal"][:7] for t in scored})
            confirm = split == "holdout" and strategy in CONFIRMATORY
            stat = interval(values, Z_FAMILY if confirm else None)
            entry = {**stat, "trades": len(scored), "active_months": active,
                     "excluded": sum(t["status"] == "EXCLUDED_MISSING_BARS" for t in scoped),
                     "no_features": sum(t["status"] == "NO_FEATURES" for t in scoped),
                     "mean_reward_per_trade": mean(t["reward"] for t in scored) if scored else None,
                     "hit_rate": sum(t["reward"] > 0 for t in scored) / len(scored) if scored else None,
                     "exit_reasons": {r: sum(t["reason"] == r for t in scored)
                                      for r in sorted({t["reason"] for t in scored})},
                     "mean_sessions_held": mean(t["sessions_held"] for t in scored) if scored else None,
                     "diagnostics": {
                         "path_minus_fixed_hold": interval(book(
                             [{**t, "reward": t["reward"] - t["reward_fixed_hold"]} for t in scored
                              if t["reward_fixed_hold"] is not None], denominators, months)),
                         "costs_doubled": interval(book([{**t, "reward": t["reward_cost_2x"]} for t in scored],
                                                        denominators, months)),
                     }}
            if confirm:
                if active < MIN_ACTIVE_MONTHS or len(scored) < MIN_TRADES:
                    entry["verdict"] = "INSUFFICIENT_COVERAGE"
                else:
                    entry["verdict"] = ("SUPPORTED" if stat["mean"] > 0 and stat["lower"] > 0
                                        else "NOT_SUPPORTED")
                entry["label"] = "PSEUDO_OUT_OF_SAMPLE"
            result[split] = entry
        out["strategies"][strategy] = result
    return out


def earnings_events(path=None) -> dict:
    """ticker -> reaction sessions, from the pinned SEC 8-K item 2.02 dataset."""
    import hashlib
    import json
    from pathlib import Path
    file = Path(path or Path(__file__).resolve().parents[2] / EARNINGS_ANNOUNCEMENTS_PATH)
    raw = file.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EARNINGS_ANNOUNCEMENTS_SHA256:
        raise ValueError("EARNINGS_DATA_CHANGED")
    out = {}
    for e in json.loads(raw)["events"]:
        out.setdefault(e["ticker"], set()).add(e["reaction_session"])
    return {t: sorted(v) for t, v in out.items()}


def s3_entries(bars: ss.Bars, spy_bars: ss.Bars, rows, spy_rows, reaction_sessions) -> dict:
    """signal index (second reaction session) -> exit context, for reactions of at least z_min."""
    out = {}
    rule = ss.RULES["S3_post_earnings"]
    for session in reaction_sessions:
        r = bars.index.get(session)
        if r is None or r < ss.WINDOW or r + rule["reaction_sessions"] - 1 >= len(bars.sessions):
            continue
        before = bars.sessions[r - 1]
        features = _features(rows, spy_rows, before)
        if features is None or bars.atr[r - 1] is None:
            continue
        z = ss.reaction_z(bars, spy_bars, r, beta=features.get("beta_63_vs_spy"), vol20=features.get("realized_vol_20"))
        if z is not None and z >= rule["z_min"]:
            out[r + rule["reaction_sessions"] - 1] = {"gap_level": bars.close[r - 1], "atr_ref": bars.atr[r - 1],
                                                     "reaction_z": z, "reaction_session": session}
    return out


# ---- loading and running -----------------------------------------------------------

def _s4_eligibility(conn, tickers, sessions_by_month):
    """month -> set of tickers eligible for S4 during the following month."""
    universe, _, _, _ = dr.load(conn, tickers)
    for t in tickers:
        data = universe[t]["ticker_data"]
        universe[t].update(observe=lambda as_of, d=data: d.observe(as_of, detail=True), sector=data.sector)
    eligible = {}
    for month_end in sessions_by_month:
        observed = fv.observe_session(universe, month_end)
        gaps = fv.sector_gaps({t: (sec, pe) for t, (_, _, sec, pe) in observed.items()})
        eligible[month_end] = {t for t, (_, detail, _, _) in observed.items()
                               if fv.solid(detail) and (gaps.get(t) or -1) >= ss.RULES["S4_value_momentum"]["gap_min"]}
    return eligible


def run(conn, *, tickers: list[str], stage: str, end: str) -> dict:
    from .. import db
    if stage not in ("dev", "holdout"):
        raise ValueError("EVENT_REPLAY_STAGE_INVALID")
    if stage == "holdout":
        ready, reason = holdout_ready()
        if not ready:
            return {"protocol_id": PROTOCOL["protocol_id"], "stage": stage, "status": "REFUSED", "reason": reason}
    stage_end = DEV[1] if stage == "dev" else HOLDOUT[1]
    sessions = session_dates("2015-06-01", end)
    spy_rows = db.fetch_prices(conn, "SPY")
    spy_bars = ss.Bars(spy_rows, end, sessions)
    month_ends = sorted({max(s for s in sessions if s[:7] == m) for m in {s[:7] for s in sessions}})
    eligible = _s4_eligibility(conn, tickers, [m for m in month_ends if m >= "2015-12-01"])
    announcements = earnings_events()
    trades, history = [], defaultdict(int)
    for ticker in sorted(tickers):
        rows = db.fetch_prices(conn, ticker)
        bars = ss.Bars(rows, end, sessions)
        for m in month_ends:
            i = bars.index.get(m)
            if i is not None and bars.window(i) is not None:
                history[m[:7]] += 1

        def s4(b, i, t=ticker):
            prior = [m for m in month_ends if m < b.sessions[i]]
            return (bool(prior) and t in eligible.get(prior[-1], set())
                    and (ss.s1_entry(b, i) or ss.s2_entry(b, i)))
        s3_map = s3_entries(bars, spy_bars, rows, spy_rows, announcements.get(ticker, []))

        def s3(b, i, m=s3_map):
            return m.get(i, False)
        for strategy, entry in (("S1_volume_breakout", ss.s1_entry), ("S2_trend_pullback", ss.s2_entry),
                                ("S3_post_earnings", s3), ("S4_value_momentum", s4)):
            trades.extend(trades_for(ticker, rows=rows, spy_rows=spy_rows, bars=bars, spy_bars=spy_bars,
                                     strategy=strategy, entry=entry, stage_end=stage_end))
    if stage == "dev":
        trades = [t for t in trades if split_of(t["signal"]) == "dev"]
    result = evaluate(trades, dict(history), stage=stage)
    return {"protocol": PROTOCOL, "result": result, "trades": trades}
