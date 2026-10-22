"""Intraday lab: exploratory strategy variants on the development bars, logged, checked once, never the hold-out.

Nothing here touches the pre-registered replay (intraday_replay.py, protocol intraday-first-hour.v1); the lab only
reuses its data handling (eligibility, the dividend-adjusted gap, missing minutes), its fills (strictly through a
level, the bar path), its scoring (reward v5 against beta x SPY over the same minutes) and its random-direction twin.
Three rules keep the search honest:

  - explore on 2016-01..2019-12 as often as wanted; every run of every variant is appended to a committed log, and a
    change to the code counts as a new try;
  - check a variant on 2020-01..2023-12 once, unchanged: the variant must have been explored with the same code, and
    the log refuses a second check of the same definition; replay() refuses the check months unless run() asks for
    them after those tests, and run() logs before it returns;
  - never read 2024-01 or later, the hold-out of intraday-first-hour.v1 and of anything the lab hands over.

A variant that survives its check can be written into a pre-registered protocol for the one hold-out run. Every
variant tried stays in the log, so the number of tries is part of the record.

Signals use 5-minute bars built from the 1-minute bars that traded; fills, stops and exits use the 1-minute bars.
Information only: nothing here trades.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ..agents.contracts import digest
from . import intraday_replay as ir

LAB_ID = "intraday-lab.v1"
EXPLORE = ("2016-01", "2019-12")
CHECK = ("2020-01", "2023-12")
HOLDOUT_FROM = "2024-01"
ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = ROOT / "docs" / "evidence" / "intraday-lab-log.json"
COSTS_BPS = (0, 2, 5, 10)               # per side; 10 is the replay's verdict cost and the lab's headline
HEADLINE_COST_BPS = 10
LAB_ENTRY_LAST = 59                     # 10:29: the last minute a lab entry can fill (the replay's is 10:15)
SCALE_WINDOW = "X1"                     # the volatility scale of a trade held from 09:35 to the close
ENTRY_DELAY = ir.ENTRY_DELAY

CONVENTIONS = {
    "lab_id": LAB_ID,
    "data": "intraday_replay's eligibility, dividend-adjusted gap, missing-minute handling and daily features, on "
            "SPY's session grid",
    "signals": "5-minute bars built from the minutes that traded, judged at each 5-minute close up to "
               f"{ir._clock(LAB_ENTRY_LAST - 1)} (a signal must leave a minute to enter by {ir._clock(LAB_ENTRY_LAST)}); "
               "running highs and lows are tracked minute by minute",
    "bounce": "gap up: the high reaches `bounce` above the 09:30 open; gap down: the high since the session low (of "
              "the minute that made the low, only the part after the low along the bar path) reaches `bounce` above "
              "that low, and a new low starts the setup over. LIMIT_RETRACE goes live the minute after the bounce first "
              "prints; OPEN_RECLAIM and REVERSAL_BAR need it at a 5-minute close (a gap-down reclaim bar may complete "
              "it)",
    "entries": "LIMIT_RETRACE: a resting buy at `retrace` of the bounce, re-priced at each minute from the high "
               "through the previous minute, filled when price trades strictly below it, at the level or at the "
               "minute's open if it opens below; a fill at an open at or below the stop is stopped at once, and so is "
               "its twin. OPEN_RECLAIM: after price traded below the 09:30 open (gap up: a dip after the 5-minute close "
               "at which the bounce is seen), a 5-minute bar crosses back above it (it closes above the open, and the "
               "last 5-minute close before it, the 09:30 open for the first bar, was at or below the open, or the bar "
               "itself traded below it). REVERSAL_BAR: a red "
               "5-minute bar after the high's bar, then the next 5-minute bar closes above that red bar's high. Market "
               f"entries fill at the next traded minute (up to {ENTRY_DELAY} later), by {ir._clock(LAB_ENTRY_LAST)}",
    "stops": "LIMIT_RETRACE: the bounce's base (gap up: the 09:30 open; gap down: the session low); OPEN_RECLAIM: gap "
             "up, the pullback low since the high; gap down, the session low; REVERSAL_BAR: the pullback low since "
             "the high (the high's own minute counts only if it is a down bar whose high is its own high, not the "
             "close of a minute that made the low)",
    "exits": "the stop, a target of `target_r` times the risk, a turn against the trade (a 5-minute bar that starts at "
             "or after a market fill's minute, or after a limit fill's minute, closes below the previous 5-minute "
             "bar's low: out at the next traded minute's open, however late), else the official close; a limit fill's "
             "own minute is checked for the stop only, along the bar path after the fill; a market fill's minute is "
             "checked like any later minute; an open at a level counts as at the open",
    "score": "intraday_replay reward v5: (side x return - side x beta x SPY over the same minutes - costs) / the "
             f"stock's trailing volatility for the {SCALE_WINDOW} window (09:35 to the close); the random-direction "
             "twin takes the other side at the same fill with reflected stop and target and the mirrored turn exit",
    "costs": f"reported at {', '.join(str(c) for c in COSTS_BPS)} bps a side; {HEADLINE_COST_BPS} bps is the headline",
    "book": "one value per session: the sum of the session's rewards divided by its eligible stock-days",
}

BASE = {"gap": 0.03, "bounce": 0.01, "retrace": 0.5, "target_r": 2.0}
VARIANTS = {
    "GU-LIM.v1": {"setup": "GAP_UP_PULLBACK", "entry": "LIMIT_RETRACE", **BASE},
    "GU-OPEN.v1": {"setup": "GAP_UP_PULLBACK", "entry": "OPEN_RECLAIM", **BASE},
    "GU-REV.v1": {"setup": "GAP_UP_PULLBACK", "entry": "REVERSAL_BAR", **BASE},
    "GD-LIM.v1": {"setup": "GAP_DOWN_REVERSAL", "entry": "LIMIT_RETRACE", **BASE},
    "GD-OPEN.v1": {"setup": "GAP_DOWN_REVERSAL", "entry": "OPEN_RECLAIM", **BASE},
    "GD-REV.v1": {"setup": "GAP_DOWN_REVERSAL", "entry": "REVERSAL_BAR", **BASE},
}
DESCRIPTIONS = {
    "GAP_UP_PULLBACK": "gap up of at least {gap:.0%}; after the high reaches {bounce:.0%} above the open, buy the pullback",
    "GAP_DOWN_REVERSAL": "gap down of at least {gap:.0%}; after a {bounce:.0%} bounce off the session low, buy the reversal",
    "LIMIT_RETRACE": "with a resting buy at {retrace:.0%} of the bounce on the way down; stop at the bounce's base",
    "OPEN_RECLAIM": "when a 5-minute bar crosses back above the 09:30 open after trading below it; stop at the low",
    "REVERSAL_BAR": "when a 5-minute bar closes above the high of the red 5-minute bar before it; stop at the pullback low",
}


def describe(variant_id: str) -> str:
    spec = VARIANTS[variant_id]
    return (DESCRIPTIONS[spec["setup"]] + ", " + DESCRIPTIONS[spec["entry"]]).format(**spec) + \
        f"; target {spec['target_r']:g}R, out on a turn against or at the close"


def definition_sha(variant_id: str, spec: dict | None = None) -> str:
    return digest({"variant": variant_id, "spec": spec or VARIANTS[variant_id], "conventions": CONVENTIONS})


def lab_code_sha() -> str:
    """The lab, the replay it borrows from and the runner, as they are on disk."""
    h = hashlib.sha256()
    for path in (Path(__file__), Path(ir.__file__), ROOT / "scripts" / "run_intraday_lab.py"):
        h.update(path.read_bytes() if path.exists() else b"")
    return h.hexdigest()


CODE_SHA = lab_code_sha()               # the code this process imported: what a logged result was produced by


def spec_sha(spec: dict) -> str:
    """The strategy itself (setup, entry, parameters), whatever its id or wording: the once-only check's key."""
    return digest(spec)


# ---- signals ---------------------------------------------------------------------

def _trade(spec, bar, price, stop, *, kind):
    if not stop < price:
        if kind == "limit":                      # filled at an open already through the stop: stopped at once
            return {"side": 1, "entry_bar": bar, "entry": price, "stop": stop, "target": None, "risk": None,
                    "kind": kind, "instant_stop": True}
        return {"skip": "ENTRY_BEYOND_STOP"}
    risk = price - stop
    return {"side": 1, "entry_bar": bar, "entry": price, "stop": stop, "target": price + spec["target_r"] * risk,
            "risk": risk, "kind": kind}


def _market(spec, day, b, stop):
    fill = ir.next_open(day, b + 1, LAB_ENTRY_LAST)
    if fill is None:
        return {"skip": "NO_ENTRY_MINUTE"}
    return _trade(spec, fill[0], fill[1], stop, kind="market")


def _crosses_open(bar, last_close, o0) -> bool:
    return ir.above(bar["c"], o0) and (not ir.above(last_close, o0) or ir.below(bar["l"], o0))


def gap_up_pullback(day, f, gap: float, spec: dict) -> dict | None:
    """Gap up: once the high has risen the bounce above the open, buy the pullback (long, with the gap)."""
    if gap < spec["gap"]:
        return None
    o0 = day["o"][0]
    bounce_at = o0 * (1 + spec["bounce"])
    limit = spec["entry"] == "LIMIT_RETRACE"
    high = high_b = high_k = None           # the running high, its minute and its 5-minute bar
    live = bounced = dipped = False
    prev5 = start = None
    last_close = o0                         # the last 5-minute close that traded
    for b in range(min(LAB_ENTRY_LAST, len(day["o"]) - 1) + 1):
        if b % 5 == 0:
            start = high_b                  # a 5-minute signal measures its pullback from the high when its bar began
        o, h, l = day["o"][b], day["h"][b], day["l"][b]
        if o is not None:
            if limit and live:              # the resting buy, priced before this minute's high
                level = high - spec["retrace"] * (high - o0)
                if ir.below(l, level):
                    return _trade(spec, b, min(level, o), o0, kind="limit")
            if high is None or ir.above(h, high):
                high, high_b, high_k = h, b, b // 5
            if limit and not ir.below(high, bounce_at):
                live = True                 # working from the next minute
            if bounced and ir.below(l, o0):
                dipped = True
        if limit or b % 5 != 4:
            continue
        bar = ir.five_minute(day, b // 5)
        if bar is None:
            prev5 = None
            continue
        if bounced and b < LAB_ENTRY_LAST:
            if spec["entry"] == "OPEN_RECLAIM" and dipped and _crosses_open(bar, last_close, o0):
                return _market(spec, day, b, _low_between(day, start, b))
            if spec["entry"] == "REVERSAL_BAR" and prev5 is not None and prev5["red_after_high"] \
                    and ir.above(bar["c"], prev5["h"]):
                return _market(spec, day, b, _low_between(day, start, b))
        if not ir.below(high, bounce_at):
            bounced = True
        bar["red_after_high"] = b // 5 > high_k and bar["c"] < bar["o"]
        prev5, last_close = bar, bar["c"]
    return None


def gap_down_reversal(day, f, gap: float, spec: dict) -> dict | None:
    """Gap down: after a bounce off the session low, buy the reversal (long, against the gap)."""
    if gap > -spec["gap"]:
        return None
    o0 = day["o"][0]
    limit = spec["entry"] == "LIMIT_RETRACE"
    low = low_b = None                      # the session low so far and its minute
    high = high_b = high_k = None           # the highest price after that low, its minute and its 5-minute bar
    live = bounced = dipped = False
    prev5 = start = None
    last_close = o0
    for b in range(min(LAB_ENTRY_LAST, len(day["o"]) - 1) + 1):
        if b % 5 == 0:
            start = (high_b, high_b != low_b)  # the pullback is measured from the high when the bar began
        o, h, l, c = day["o"][b], day["h"][b], day["l"][b], day["c"][b]
        if o is not None:
            if limit and live:
                level = high - spec["retrace"] * (high - low)
                if ir.below(l, level):
                    return _trade(spec, b, min(level, o), low, kind="limit")
            if low is None or ir.below(l, low):       # a new session low: the setup starts over
                after = h if c >= o else c            # the part of this minute after its low (the bar path)
                low, low_b, high, high_b, high_k, live, bounced = l, b, after, b, b // 5, False, False
            elif ir.above(h, high):
                high, high_b, high_k = h, b, b // 5
            if limit and not ir.below(high, low * (1 + spec["bounce"])):
                live = True
            if ir.below(l, o0):
                dipped = True
        if limit or b % 5 != 4:
            continue
        bar = ir.five_minute(day, b // 5)
        if bar is None:
            prev5 = None
            continue
        bounce_now = bounced or not ir.below(high, low * (1 + spec["bounce"]))
        if b < LAB_ENTRY_LAST:
            if spec["entry"] == "OPEN_RECLAIM" and bounce_now and dipped and _crosses_open(bar, last_close, o0):
                return _market(spec, day, b, low)
            if spec["entry"] == "REVERSAL_BAR" and bounced and prev5 is not None and prev5["red_after_high"] \
                    and ir.above(bar["c"], prev5["h"]):
                return _market(spec, day, b, _low_between(day, start[0], b, own_high=start[1]))
        bounced = bounce_now
        bar["red_after_high"] = bounced and b // 5 > high_k and bar["c"] < bar["o"]
        prev5, last_close = bar, bar["c"]
    return None


def _low_between(day, high_b: int, last: int, *, own_high: bool = True) -> float:
    """The pullback's low: the lowest low after the high through the signal minute. The high's own minute counts only
    when it is a down bar (open, high, low, close) whose high is its own high: in an up bar its low came before the
    high, and when the "high" is the close of the minute that made the session low, that low came first."""
    lows = [x for x in day["l"][high_b + 1:last + 1] if x is not None]
    if own_high and day["c"][high_b] < day["o"][high_b]:
        lows.append(day["l"][high_b])
    return min(lows) if lows else day["l"][high_b]


SETUPS = {"GAP_UP_PULLBACK": gap_up_pullback, "GAP_DOWN_REVERSAL": gap_down_reversal}


# ---- exits and scoring -----------------------------------------------------------

def lab_exit(day, side: int, trade: dict, stop: float, target: float | None, close_price: float) -> dict:
    """Stop first, then target, then a turn against the side; else the official close."""
    n = len(day["c"])
    b0, entry = trade["entry_bar"], trade["entry"]
    if trade.get("instant_stop"):                    # filled through the stop: flat at once, and so is the twin
        return {"exit": entry, "exit_bar": b0, "reason": "STOP", "at_open": True}
    first, limit = b0, trade["kind"] == "limit"
    if limit:                                        # the fill minute: the stop only, along the path after the fill
        rest = ir.after_fill(day, b0, -1, entry)     # a buy limit fills as price falls through it
        if (side > 0 and ir.below(min(rest), stop)) or (side < 0 and ir.above(max(rest), stop)):
            return {"exit": stop, "exit_bar": b0, "reason": "STOP", "at_open": False}
        first = b0 + 1
    turning = False
    for b in range(first, n):
        o, h, l = day["o"][b], day["h"][b], day["l"][b]
        if o is not None:
            if turning:
                return {"exit": o, "exit_bar": b, "reason": "TURN", "at_open": True}
            later = b > b0
            if (side > 0 and ir.below(l, stop)) or (side < 0 and ir.above(h, stop)):
                gapped = later and (not ir.above(o, stop) if side > 0 else not ir.below(o, stop))
                return {"exit": o if gapped else stop, "exit_bar": b, "reason": "STOP", "at_open": gapped}
            if target is not None and ((side > 0 and ir.above(h, target)) or (side < 0 and ir.below(l, target))):
                gapped = later and (not ir.below(o, target) if side > 0 else not ir.above(o, target))
                return {"exit": o if gapped else target, "exit_bar": b, "reason": "TARGET", "at_open": gapped}
        start = 5 * (b // 5)
        if b % 5 == 4 and (start > b0 or (start == b0 and not limit)):
            bar, prev = ir.five_minute(day, b // 5), ir.five_minute(day, b // 5 - 1)
            if bar is not None and prev is not None and \
                    ((side > 0 and ir.below(bar["c"], prev["l"])) or (side < 0 and ir.above(bar["c"], prev["h"]))):
                turning = True                       # out at the next traded minute's open
    return {"exit": close_price, "exit_bar": None, "reason": "CLOSE", "at_open": False}


def _spy(spy_day, b: int | None, at_open: bool, spy_close: float) -> float | None:
    if b is None:
        return spy_close
    if b >= len(spy_day["o"]) or spy_day["o"][b] is None or spy_day["c"][b] is None:
        return None
    return spy_day["o"][b] if at_open else (spy_day["o"][b] + spy_day["c"][b]) / 2


def score(trade: dict, day, spy_day, f: dict, spy_f: dict, scale: float, *, side: int = 1) -> dict | None:
    entry = trade["entry"]
    stop, target = trade["stop"], trade["target"]
    if side != trade["side"]:                        # the twin: same fill, reflected brackets, the mirrored turn exit
        stop = 2 * entry - stop
        target = None if target is None else 2 * entry - target
    out = lab_exit(day, side, trade, stop, target, f["daily_close"])
    b = trade["entry_bar"]
    s_in = _spy(spy_day, b, trade["kind"] == "market" or entry == day["o"][b], spy_f["daily_close"])
    s_out = _spy(spy_day, out["exit_bar"], out["at_open"], spy_f["daily_close"])
    if s_in is None or s_out is None:
        return None
    gross = side * (out["exit"] / entry - 1)
    resid = gross - side * f["beta"] * (s_out / s_in - 1)
    minutes = (out["exit_bar"] if out["exit_bar"] is not None else len(day["c"]) - 1) - b
    return {"reason": out["reason"], "residual": resid, "minutes": minutes,
            "rewards": {c: (resid - 2 * c / 1e4) / scale for c in COSTS_BPS},
            "r_gross": side * (out["exit"] - entry) / trade["risk"] if trade["risk"] else None}


# ---- the run ---------------------------------------------------------------------

def replay(bars_for, prices: dict, months: tuple[str, str], tickers: list[str], variants: dict, *,
           allow_check: bool = False) -> dict:
    """The lab's pass over the bars: the replay's grid, eligibility and history, the variants' trades. The check
    months are refused unless run() asks for them; the hold-out always is."""
    if months[0] > months[1]:
        raise ValueError("INTRADAY_LAB_MONTHS_INVERTED")
    if months[1] >= HOLDOUT_FROM:
        raise ValueError("INTRADAY_LAB_NEVER_READS_THE_HOLDOUT")
    if months[1] >= CHECK[0] and not allow_check:
        raise ValueError("INTRADAY_LAB_CHECK_MONTHS_ONLY_THROUGH_RUN")
    spy_rows = prices["SPY"]
    loaded = [ir.previous_month(months[0])] + ir.months_between(*months)
    spy_days = {}
    for month in loaded:
        spy_days.update(bars_for("SPY", month))
    spy_feat = ir.daily_features(spy_rows, spy_rows)
    calendar = [r["date"] for r in spy_rows]
    grid = sorted({*spy_days, *calendar} & {s for s in calendar if months[0] <= s[:7] <= months[1]})
    sessions, eligible = set(grid), defaultdict(int)
    trades, skips, excluded = [], defaultdict(int), defaultdict(int)
    for ticker in tickers:
        rows = prices.get(ticker, [])
        feats = ir.daily_features(rows, spy_rows)
        daily = {"first": rows[0]["date"] if rows else None, "dates": {r["date"] for r in rows}}
        hist = ir.History()
        seen = set()
        for month in loaded:
            days = bars_for(ticker, month)
            for session in sorted(days):
                day = days[session]
                if months[0] <= session[:7] <= months[1]:
                    seen.add(session)
                    sessions.add(session)
                    f = feats.get(session)
                    reason = ir.eligibility(ticker, session, day, f, hist, spy_days.get(session),
                                            spy_feat.get(session), ir.CORPORATE_ACTIONS.get(ticker, ()), daily)
                    if reason:
                        excluded[reason] += 1
                    else:
                        eligible[session] += 1
                        gap = day["o"][0] / f["prior_close"] - 1
                        for vid, spec in variants.items():
                            t = SETUPS[spec["setup"]](day, f, gap, spec)
                            if t is None:
                                continue
                            if "skip" in t:
                                skips[f"{vid}:{t['skip']}"] += 1
                                continue
                            scored = _score_pair(t, day, spy_days[session], f, spy_feat[session], hist)
                            if scored is None:
                                skips[f"{vid}:UNSCORABLE"] += 1
                                continue
                            trades.append({"variant": vid, "ticker": ticker, "session": session,
                                           "month": session[:7], "gap": gap, **scored})
                hist.update(day)
        for session in grid:
            if session not in seen:
                excluded["PRE_LISTING" if session < ir.LISTED.get(ticker, "") else
                         ir._no_features(session, daily) if session not in feats else "NO_MINUTE_BARS"] += 1
    return {"trades": trades, "sessions": sorted(sessions), "eligible": dict(eligible), "skips": dict(skips),
            "excluded": dict(excluded)}


def _score_pair(t, day, spy_day, f, spy_f, hist) -> dict | None:
    scale = hist.scale(SCALE_WINDOW, day)
    if scale is None:
        return None
    own = score(t, day, spy_day, f, spy_f, scale)
    mirror = score(t, day, spy_day, f, spy_f, scale, side=-1)
    if own is None or mirror is None:
        return None
    return {"entry_bar": t["entry_bar"], "kind": t["kind"], "reason": own["reason"], "minutes": own["minutes"],
            "r_gross": own["r_gross"], "net_bps": {c: (own["residual"] - 2 * c / 1e4) * 1e4 for c in COSTS_BPS},
            "gross_bps": own["residual"] * 1e4, "reward": own["rewards"],
            "skill": (own["rewards"][HEADLINE_COST_BPS] - mirror["rewards"][HEADLINE_COST_BPS]) / 2,
            "control": (own["rewards"][HEADLINE_COST_BPS] + mirror["rewards"][HEADLINE_COST_BPS]) / 2}


def evaluate(run: dict, variants: dict) -> dict:
    sessions, denominators = run["sessions"], run["eligible"]
    out = {}
    for vid in variants:
        trades = [t for t in run["trades"] if t["variant"] == vid]
        rows = [{"session": t["session"], **{f"reward_{c}": t["reward"][c] for c in COSTS_BPS},
                 "skill": t["skill"], "control": t["control"]} for t in trades]
        by_year = {}
        for y in sorted({t["session"][:4] for t in trades}):
            ts = [t for t in trades if t["session"][:4] == y]
            by_year[y] = {"trades": len(ts),
                          "net_bps_at_10": _mean([t["net_bps"][HEADLINE_COST_BPS] for t in ts]) if len(ts) >= 20 else None}
        out[vid] = {
            "description": describe(vid) if vid in VARIANTS else None,
            "trades": len(trades), "stocks": len({t["ticker"] for t in trades}),
            "months_with_trades": len({t["month"] for t in trades}),
            "per_trade": {f"net_bps_at_{c}": _mean([t["net_bps"][c] for t in trades]) for c in COSTS_BPS} |
                         {"gross_bps": _mean([t["gross_bps"] for t in trades]),
                          "hit_rate_at_10": _mean([1.0 if t["net_bps"][HEADLINE_COST_BPS] > 0 else 0.0 for t in trades]),
                          "mean_R": _mean([t["r_gross"] for t in trades]),
                          "minutes_held": _mean([t["minutes"] for t in trades])},
            "exits": dict(sorted(_count(t["reason"] for t in trades).items())),
            "book": {f"at_{c}": ir.interval(ir.book(rows, sessions, denominators, f"reward_{c}"), ir.Z_95)
                     for c in COSTS_BPS},
            "direction_skill": ir.interval(ir.book(rows, sessions, denominators, "skill"), ir.Z_95),
            "random_twin": ir.interval(ir.book(rows, sessions, denominators, "control"), ir.Z_95),
            "by_year": by_year,
            "skips": {k.split(":", 1)[1]: v for k, v in run["skips"].items() if k.startswith(f"{vid}:")},
        }
    return out


def _mean(values):
    values = [v for v in values if v is not None]
    return math.fsum(values) / len(values) if values else None


def _count(items) -> dict:
    out = defaultdict(int)
    for x in items:
        out[x] += 1
    return dict(out)


def finished_through(root, symbols: list[str], first: str, last: str) -> str | None:
    """The last month of the unbroken run of finished months from `first` (its warm-up month included)."""
    through = None
    for month in [ir.previous_month(first)] + ir.months_between(first, last):
        if ir.missing_months(root, symbols, [month]):
            break
        through = month
    return through if through and through >= first else None


def run(split: str, variant_ids: list[str], *, dev_root, prices_dir, tickers: list[str], log_path=None) -> dict:
    """One logged lab run. Explore: the finished months of the explore split. Check: all of the check split, once
    per definition, explored with this code first. The log entries are written before this returns."""
    log_path = Path(log_path or LOG_PATH)
    if split not in ("explore", "check"):
        raise ValueError("INTRADAY_LAB_SPLIT_INVALID")
    unknown = [v for v in variant_ids if v not in VARIANTS]
    if unknown or not variant_ids:
        return {"status": "REFUSED", "reason": f"UNKNOWN_VARIANT {', '.join(unknown)}".strip()}
    if lab_code_sha() != CODE_SHA:
        return {"status": "REFUSED", "reason": "CODE_CHANGED_SINCE_IMPORT"}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path.with_name(log_path.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)             # one run at a time reads, checks and appends the log
        return _run_locked(split, variant_ids, dev_root=dev_root, prices_dir=prices_dir, tickers=tickers,
                           log_path=log_path)


def _run_locked(split, variant_ids, *, dev_root, prices_dir, tickers, log_path) -> dict:
    from . import implied_vol_replay as ivr
    log = read_log(log_path)
    symbols = sorted(tickers) + ["SPY"]
    if split == "explore":
        through = finished_through(dev_root, symbols, *EXPLORE)
        if through is None:
            return {"status": "REFUSED", "reason": "NO_FINISHED_MONTH"}
        months = (EXPLORE[0], through)
    else:
        if ir.missing_months(dev_root, symbols, [ir.previous_month(CHECK[0])] + ir.months_between(*CHECK)):
            return {"status": "REFUSED", "reason": "CHECK_FETCH_INCOMPLETE"}
        refused = {v: why for v in variant_ids for ok, why in [check_allowed(v, log)] if not ok}
        if refused:
            return {"status": "REFUSED", "reason": "CHECK_NOT_ALLOWED", "variants": refused}
        months = CHECK
    prices, prices_sha = ivr.load_prices(prices_dir)
    if prices_sha != ir.DAILY_PRICES_SHA256:
        return {"status": "REFUSED", "reason": "DAILY_PRICES_NOT_REGISTERED"}
    if ir.behavior_digest() != ir.BEHAVIOR_SHA256:
        return {"status": "REFUSED", "reason": "REPLAY_CODE_CHANGED"}
    variants = {v: VARIANTS[v] for v in variant_ids}
    used = [ir.previous_month(months[0])] + ir.months_between(*months)
    result = replay(lambda t, m: ir.load_month(dev_root, t, m), prices, months, sorted(tickers), variants,
                    allow_check=split == "check")
    evaluation = evaluate(result, variants)
    bars_sha = ir.files_sha(dev_root, used)
    entries = log_entries(split, months, bars_sha, evaluation, log, sorted(tickers))
    write_log(log + entries, log_path)
    return {"status": "OK", "lab_id": LAB_ID, "split": split, "months": list(months),
            "generated_at": entries[0]["at"], "sessions": len(result["sessions"]),
            "eligible_stock_days": sum(result["eligible"].values()), "excluded": result["excluded"],
            "variants": evaluation, "bars_sha256": bars_sha, "lab_code_sha256": CODE_SHA,
            "checks_so_far": sum(1 for e in log + entries if e["split"] == "check"),
            "variants_tried": variants_tried(log + entries), "log_entries": entries, "use": "INFORMATION_ONLY"}


# ---- the log ---------------------------------------------------------------------

def read_log(path=None) -> list[dict]:
    path = Path(path or LOG_PATH)
    return json.loads(path.read_text()) if path.exists() else []


def write_log(entries: list[dict], path=None) -> None:
    path = Path(path or LOG_PATH)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as fh:
        fh.write(json.dumps(entries, indent=1, default=str) + "\n")
    os.replace(fh.name, path)


def check_allowed(variant_id: str, log: list[dict]) -> tuple[bool, str]:
    """A check needs an explore of this definition with this code, and no earlier check of the same strategy (the
    spec, whatever its id or the wording of the conventions)."""
    sha, strategy = definition_sha(variant_id), spec_sha(VARIANTS[variant_id])
    if any(e["split"] == "check" and e.get("spec_sha256", spec_sha(e["spec"])) == strategy for e in log):
        return False, "ALREADY_CHECKED"
    if not any(e["definition_sha256"] == sha and e["split"] == "explore" and e["lab_code_sha256"] == CODE_SHA
               for e in log):
        return False, "NOT_EXPLORED_WITH_THIS_CODE"
    return True, "OK"


def log_entries(split: str, months: tuple[str, str], bars_sha: str, evaluation: dict, log: list[dict],
                tickers: list[str] | None = None) -> list[dict]:
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    start = len(log)
    return [{"entry": start + i + 1, "at": at, "lab_id": LAB_ID, "split": split, "months": list(months),
             "variant": vid, "definition_sha256": definition_sha(vid), "spec": VARIANTS[vid],
             "spec_sha256": spec_sha(VARIANTS[vid]), "lab_code_sha256": CODE_SHA, "bars_sha256": bars_sha,
             "tickers_sha256": digest(sorted(tickers or [])),
             "trades": r["trades"], "net_bps_at_10": r["per_trade"]["net_bps_at_10"],
             "net_bps_at_2": r["per_trade"]["net_bps_at_2"], "gross_bps": r["per_trade"]["gross_bps"],
             "book_at_10": {k: r["book"]["at_10"].get(k) for k in ("mean", "lower", "upper")},
             "direction_skill": {k: r["direction_skill"].get(k) for k in ("mean", "lower", "upper")}}
            for i, (vid, r) in enumerate(evaluation.items())]


def variants_tried(log: list[dict]) -> int:
    """Distinct (definition, code) pairs: a change to the code is a new try."""
    return len({(e["definition_sha256"], e["lab_code_sha256"]) for e in log})


# ---- the summary -----------------------------------------------------------------

def _bps(x) -> str:
    return "–" if x is None else f"{x:+.1f}"


def _book(r: dict) -> str:
    if r.get("mean") is None:
        return "–"
    return f"{r['mean']:+.2e} [{r['lower']:+.2e}, {r['upper']:+.2e}]"


def render(report: dict) -> str:
    """The lab run's summary in plain terms: per-trade results at each cost first, then the book."""
    lines = [f"# Intraday lab: {report['split']} run, {report['months'][0]}..{report['months'][1]}", "",
             f"`{LAB_ID}`, generated {report['generated_at']}. Exploratory: these numbers decide nothing. Every run is "
             f"in `docs/evidence/intraday-lab-log.json`; **{report['variants_tried']} variant tries so far** (a "
             f"definition run with a given version of the code) and **{report.get('checks_so_far', 0)} one-time "
             "checks**. The more variants are tried, the more one of them "
             "will look good by luck, which is why survivors are checked once on 2020-2023 and only then handed to "
             "the one hold-out run.", "",
             f"- **Sessions:** {report['sessions']:,}; **eligible stock-days:** {report['eligible_stock_days']:,}.",
             "- **Net bps** are per trade after costs and the beta x SPY hedge, at 10, 5, 2 and 0 bps a side "
             "(10 is the headline; liquid large caps at IBKR are often nearer 2-5). **R** is the gross move over the "
             "risk to the stop.", ""]
    lines += ["| Variant | What it does | Trades | Stocks | Net bps at 10 | at 5 | at 2 | at 0 | Hit rate at 10 | "
              "Mean R | Minutes held | Exits |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for vid, r in report["variants"].items():
        pt = r["per_trade"]
        exits = ", ".join(f"{k.lower()} {v}" for k, v in r["exits"].items()) or "–"
        hit = "–" if pt["hit_rate_at_10"] is None else f"{pt['hit_rate_at_10']:.0%}"
        mean_r = "–" if pt["mean_R"] is None else f"{pt['mean_R']:+.2f}"
        held = "–" if pt["minutes_held"] is None else f"{pt['minutes_held']:.0f}"
        lines.append(f"| **{vid}** | {r['description']} | {r['trades']:,} | {r['stocks']} | "
                     f"{_bps(pt['net_bps_at_10'])} | {_bps(pt['net_bps_at_5'])} | {_bps(pt['net_bps_at_2'])} | "
                     f"{_bps(pt['net_bps_at_0'])} | {hit} | {mean_r} | {held} | {exits} |")
    lines += ["", "**The book** (one value per session, per eligible stock-day; 95% Newey-West intervals, descriptive):",
              "", "| Variant | At 10 bps | At 2 bps | Direction skill (vs the twin) |", "|---|---|---|---|"]
    for vid, r in report["variants"].items():
        lines.append(f"| {vid} | {_book(r['book']['at_10'])} | {_book(r['book']['at_2'])} | {_book(r['direction_skill'])} |")
    years = sorted({y for r in report["variants"].values() for y in r["by_year"]})
    if years:
        lines += ["", "**By year** (trades: net bps at 10; – under 20 trades):", "", "| Variant | " + " | ".join(years)
                  + " |", "|---|" + "---|" * len(years)]
        for vid, r in report["variants"].items():
            lines.append(f"| {vid} | " + " | ".join(
                f"{r['by_year'][y]['trades']}: {_bps(r['by_year'][y]['net_bps_at_10'])}" if y in r["by_year"] else "–"
                for y in years) + " |")
    skips = {vid: r["skips"] for vid, r in report["variants"].items() if r["skips"]}
    lines += ["", "**Skips:** " + ("; ".join(f"{vid} " + ", ".join(f"{k} {v}" for k, v in sk.items())
                                             for vid, sk in skips.items()) if skips else "none") + "."]
    return "\n".join(lines) + "\n"
