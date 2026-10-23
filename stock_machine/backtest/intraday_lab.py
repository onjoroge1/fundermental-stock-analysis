"""Intraday lab: exploratory strategy variants on the development bars, logged, checked once, never the hold-out.

Nothing here touches the pre-registered replay (intraday_replay.py, protocol intraday-first-hour.v1); the lab only
reuses its data handling (eligibility, the dividend-adjusted gap, missing minutes), its fills (strictly through a
level, the bar path), its scoring (reward v5 against beta x SPY over the same minutes) and its random-direction twin.
Four rules keep the search honest:

  - explore on the discovery months 2016-01..2017-06 as often as wanted; every run of every variant is appended to a
    committed log, and a change to the code counts as a new try;
  - confirm a frozen variant on 2017-07..2019-12 once, against the pass bar fixed in PASS_BARS before those months were
    looked at;
  - check a confirmed variant on 2020-01..2023-12 once, unchanged (the same code and inputs as its confirm), against
    the check's own pass bar;
  - never read 2024-01 or later, the hold-out of intraday-first-hour.v1 and of anything the lab hands over.

Each later stage needs the earlier one, with the same code for the exploration and no second look at the same strategy
(keyed on its spec, whatever its id); replay() refuses months past the split run() asks for, and run() logs under a lock
before it returns. A variant that survives its check can be written into a pre-registered protocol for the one hold-out
run. Every variant tried stays in the log, so the number of tries is part of the record.

Signals use 5-minute bars built from the 1-minute bars that traded; fills, stops and exits use the 1-minute bars.
Information only: nothing here trades.
"""
from __future__ import annotations

import fcntl
import hashlib
import inspect
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
EXPLORE = ("2016-01", "2017-06")       # the discovery months
CONFIRM = ("2017-07", "2019-12")       # held back from the discovery research; one look per strategy
CHECK = ("2020-01", "2023-12")
PASS_BARS = {                          # both fixed before the confirm months were read: they are in the code hash and
                                       # the definitions, so a later edit strands a passed confirm's check
    "confirm": {"cost_bps": 5, "min_t": 2.0, "min_positive_periods": 2,
                "periods": (("2017-07", "2017-12"), ("2018-01", "2018-12"), ("2019-01", "2019-12"))},
    "check": {"cost_bps": 5, "min_t": 2.0, "min_positive_periods": 3,
              "periods": tuple((f"{y}-01", f"{y}-12") for y in range(2020, 2024))},
}
EXPLORE_PERIODS = (("2016-01", "2016-12"), ("2017-01", "2017-06"))   # for the reference table only
FROZEN = ("EXT-FADE.v1", "EXT-FADE.v2", "EARN-FH.v1", "EARN-FH15.v1")   # frozen 2026-10-22 for the one confirm
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
    "signals": "GAP_UP_PULLBACK and GAP_DOWN_REVERSAL: 5-minute bars built from the minutes that traded, judged at "
               f"each 5-minute close up to {ir._clock(LAB_ENTRY_LAST - 1)} (a signal must leave a minute to enter by "
               f"{ir._clock(LAB_ENTRY_LAST)}); running highs and lows are tracked minute by minute. The session's own "
               "daily close is read only as a data check (intraday_replay's eligibility) and as the exit price at the "
               "close, never as a signal",
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
             "stock's trailing volatility for the variant's `scale` window (the replay's windows: default "
             f"{SCALE_WINDOW}, {ir._clock(ir.WINDOWS[ir.WINDOW_OF[SCALE_WINDOW]][0])} to the close; E1, "
             f"{ir._clock(ir.WINDOWS[ir.WINDOW_OF['E1']][0])} to the close); the scale moves the rewards and the "
             "book, and reaches net bps and the pass bar only through scorability (a trade whose window has under "
             f"{ir.NORM_MIN} sessions of history is UNSCORABLE); the random-direction twin takes the other side at "
             "the same fill with reflected stop and target and the mirrored turn exit",
    "costs": f"reported at {', '.join(str(c) for c in COSTS_BPS)} bps a side; {HEADLINE_COST_BPS} bps is the headline",
    "book": "one value per session: the sum of the session's rewards divided by its eligible stock-days",
    "fades": "EXTENSION_FADE: at 09:30, the gap (09:30 open over the dividend-adjusted prior close) and the prior "
             "session's move (its official close over the one before) are both at least `gap` and `prior`, the same "
             "way, and SPY's own gap (its 09:30 open over its dividend-adjusted prior close) is under `spy_gap_max` "
             "if set; side against the gap; a market entry at bar `entry_bar` (or the next traded minute); skipped "
             "if that fill is already at or past the prior close (ENTRY_BEYOND_TARGET); a resting exit at the prior "
             "close, filled when price trades strictly through it; else the official close; no stop; at most `cap` "
             "per session, largest gap first, among the trades left after skips (a skipped trade frees its slot). "
             "EARNINGS_FIRST_HOUR: a PRIMARY reaction session (an 8-K item 2.02 accepted at least 30 days after the "
             "ticker's one before; item 2.02 is results of operations, so a few are not earnings releases) accepted "
             "before 09:30 or after 16:00 the session before, on a full session by the calendar (SPY trades past "
             "15:00), raw gap at least `gap`: when the 10:29 close's first-hour move "
             "(hedged by beta x SPY over the same minutes) points against the hedged gap, follow it from 10:30 to the "
             "official close; no stop, no target. Both skip a session without SPY's 09:30 bar (MISSING_SPY_OPEN). "
             "Shorts obey Rule 201 (intraday_replay.ssr_active) and are skipped and counted when it binds; the twin "
             "is never restricted",
    "stages": f"explore {EXPLORE[0]}..{EXPLORE[1]}; confirm {CONFIRM[0]}..{CONFIRM[1]} once per strategy after an "
              f"explore with the same code; check {CHECK[0]}..{CHECK[1]} once per strategy after a passed confirm; "
              "intraday_replay's previews and development run read no further than the explore months until the "
              "frozen variants' confirms are logged, and no further than the confirm months until the checks of "
              "those that passed are (replay_reads_through)",
    "pass_bar": "a confirm or check passes when, at the bar's cost a side, the mean net per trade is above 0, its "
                "t-statistic is above the bar's (the leave-one-session-out jackknife standard error: each session's "
                "trades are dropped together, so one session cannot carry the mean), and the mean net per trade is "
                "above 0 in enough of the bar's periods: "
                + "; ".join(f"{split} {b['cost_bps']} bps, t above {b['min_t']:g}, {b['min_positive_periods']} of "
                            + ", ".join(f"{a}..{z}" for a, z in b["periods"]) for split, b in PASS_BARS.items()),
    "inputs": "a run's inputs are the tickers, the pinned 8-K data and the pinned daily prices; a confirm needs an "
              "explore with the same inputs, and a check a passed confirm with the same inputs, code and definition",
}

BASE = {"gap": 0.03, "bounce": 0.01, "retrace": 0.5, "target_r": 2.0}
VARIANTS = {
    "GU-LIM.v1": {"setup": "GAP_UP_PULLBACK", "entry": "LIMIT_RETRACE", **BASE},
    "GU-OPEN.v1": {"setup": "GAP_UP_PULLBACK", "entry": "OPEN_RECLAIM", **BASE},
    "GU-REV.v1": {"setup": "GAP_UP_PULLBACK", "entry": "REVERSAL_BAR", **BASE},
    "GD-LIM.v1": {"setup": "GAP_DOWN_REVERSAL", "entry": "LIMIT_RETRACE", **BASE},
    "GD-OPEN.v1": {"setup": "GAP_DOWN_REVERSAL", "entry": "OPEN_RECLAIM", **BASE},
    "GD-REV.v1": {"setup": "GAP_DOWN_REVERSAL", "entry": "REVERSAL_BAR", **BASE},
    # frozen 2026-10-22 from the discovery research, before the confirm months were read
    "EXT-FADE.v1": {"setup": "EXTENSION_FADE", "gap": 0.01, "prior": 0.01, "spy_gap_max": None, "entry_bar": 1,
                    "cap": None, "scale": "X1"},
    "EXT-FADE.v2": {"setup": "EXTENSION_FADE", "gap": 0.01, "prior": 0.01, "spy_gap_max": 0.005, "entry_bar": 5,
                    "cap": 3, "scale": "X1"},
    "EARN-FH.v1": {"setup": "EARNINGS_FIRST_HOUR", "gap": 0.0, "scale": "E1"},
    "EARN-FH15.v1": {"setup": "EARNINGS_FIRST_HOUR", "gap": 0.015, "scale": "E1"},
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
    if spec["setup"] == "EXTENSION_FADE":
        quiet = f", SPY's own gap under {spec['spy_gap_max']:.1%}" if spec["spy_gap_max"] is not None else ""
        cap = f", at most {spec['cap']} a session (largest gap first)" if spec["cap"] else ""
        return (f"a gap of at least {spec['gap']:.0%} the same way as yesterday's move of at least {spec['prior']:.0%}"
                f"{quiet}: fade it from {ir._clock(spec['entry_bar'])}, out at the gap fill or the close{cap}")
    if spec["setup"] == "EARNINGS_FIRST_HOUR":
        size = f" of at least {spec['gap']:.1%}" if spec["gap"] else ""
        return (f"an earnings gap{size} (released outside market hours) that the first hour pushed against: follow "
                "the first hour from 10:30 to the close")
    return (DESCRIPTIONS[spec["setup"]] + ", " + DESCRIPTIONS[spec["entry"]]).format(**spec) + \
        f"; target {spec['target_r']:g}R, out on a turn against or at the close"


def definition_sha(variant_id: str, spec: dict | None = None) -> str:
    return digest({"variant": variant_id, "spec": spec or VARIANTS[variant_id], "conventions": CONVENTIONS})


def lab_code_sha() -> str:
    """The lab, the replay it borrows from and the runner, as they are on disk, and the daily-price loader."""
    h = hashlib.sha256()
    for path in (Path(__file__), Path(ir.__file__), ROOT / "scripts" / "run_intraday_lab.py"):
        h.update(path.read_bytes() if path.exists() else b"")
    for fn in (ir.ivr.load_prices, ir.ivr._files_sha):
        h.update(inspect.getsource(fn).encode())
    return h.hexdigest()


CODE_SHA = lab_code_sha()               # the code this process imported: what a logged result was produced by


def inputs_sha(tickers: list[str]) -> str:
    """What a run reads besides the bars: the tickers, the pinned 8-K data and the pinned daily prices."""
    return digest({"tickers": sorted(tickers), "earnings_sha256": ir.er.EARNINGS_ANNOUNCEMENTS_SHA256,
                   "daily_prices_sha256": ir.DAILY_PRICES_SHA256})


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


def gap_up_pullback(day, f, gap: float, spec: dict, ctx: dict | None = None) -> dict | None:
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


def gap_down_reversal(day, f, gap: float, spec: dict, ctx: dict | None = None) -> dict | None:
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


def _held(side: int, bar: int, price: float, target: float | None, rank: float) -> dict:
    """A trade held to the close with no stop: the fades."""
    return {"side": side, "entry_bar": bar, "entry": price, "stop": None, "target": target, "risk": None,
            "kind": "market", "turn": False, "rank": rank}


def extension_fade(day, f, gap: float, spec: dict, ctx: dict) -> dict | None:
    """Fade a gap that extends yesterday's move (long after a down day that gaps down, short after an up day that
    gaps up), at a quiet-market open if spy_gap_max is set."""
    prior = f["prior_close_raw"] / f["close_before_prior"] - 1
    if abs(gap) < spec["gap"] or abs(prior) < spec["prior"] or (gap > 0) != (prior > 0):
        return None
    if ctx["spy"]["o"][0] is None:
        return {"skip": "MISSING_SPY_OPEN"}
    if spec["spy_gap_max"] is not None:
        spy_gap = ctx["spy"]["o"][0] / ctx["spy_f"]["prior_close"] - 1
        if abs(spy_gap) >= spec["spy_gap_max"]:
            return None
    side = -1 if gap > 0 else 1
    fill = ir.next_open(day, spec["entry_bar"], len(day["o"]) - 1)
    if fill is None:
        return {"skip": "NO_ENTRY_MINUTE"}
    if side < 0 and ir.ssr_active(f, day, fill[0] - 1):
        return {"skip": "RULE_201"}
    target = f["prior_close"]
    if (side < 0 and fill[1] <= target) or (side > 0 and fill[1] >= target):
        return {"skip": "ENTRY_BEYOND_TARGET"}                 # the gap already filled before the entry
    return _held(side, fill[0], fill[1], target, abs(gap))


def earnings_first_hour(day, f, gap: float, spec: dict, ctx: dict) -> dict | None:
    """An earnings gap released outside market hours that the first hour pushed against: follow the first hour from
    10:30 to the close."""
    spy, spy_f = ctx["spy"], ctx["spy_f"]
    if ctx.get("timing") not in ("PRE_OPEN", "AFTER_CLOSE") or abs(gap) < spec["gap"] or len(spy["c"]) <= 330:
        return None                                  # a half session by the calendar (SPY's minutes) is not traded
    if spy["o"][0] is None:
        return {"skip": "MISSING_SPY_OPEN"}
    if not ir._complete(day, "c", [59]) or not ir._complete(spy, "c", [59]):
        return {"skip": "MISSING_MINUTE"}
    hedged_gap = gap - f["beta"] * (spy["o"][0] / spy_f["prior_close"] - 1)
    first_hour = (day["c"][59] / day["o"][0] - 1) - f["beta"] * (spy["c"][59] / spy["o"][0] - 1)
    if hedged_gap == 0 or first_hour == 0 or (first_hour > 0) == (hedged_gap > 0):
        return None
    side = 1 if first_hour > 0 else -1
    if side < 0 and ir.ssr_active(f, day, 59):
        return {"skip": "RULE_201"}
    fill = ir.next_open(day, 60, len(day["o"]) - 1)
    if fill is None:
        return {"skip": "NO_ENTRY_MINUTE"}
    return _held(side, fill[0], fill[1], None, abs(gap))


SETUPS = {"GAP_UP_PULLBACK": gap_up_pullback, "GAP_DOWN_REVERSAL": gap_down_reversal,
          "EXTENSION_FADE": extension_fade, "EARNINGS_FIRST_HOUR": earnings_first_hour}


# ---- exits and scoring -----------------------------------------------------------

def lab_exit(day, side: int, trade: dict, stop: float | None, target: float | None, close_price: float) -> dict:
    """Stop first, then target, then a turn against the side (where the setup uses it); else the official close."""
    n = len(day["c"])
    b0, entry = trade["entry_bar"], trade["entry"]
    if trade.get("instant_stop"):                    # filled through the stop: flat at once, and so is the twin
        return {"exit": entry, "exit_bar": b0, "reason": "STOP", "at_open": True}
    first, limit = b0, trade["kind"] == "limit"
    if limit:                                        # the fill minute: the stop only, along the path after the fill
        rest = ir.after_fill(day, b0, -1, entry)     # a buy limit fills as price falls through it
        if stop is not None and ((side > 0 and ir.below(min(rest), stop)) or (side < 0 and ir.above(max(rest), stop))):
            return {"exit": stop, "exit_bar": b0, "reason": "STOP", "at_open": False}
        first = b0 + 1
    turning, use_turn = False, trade.get("turn", True)
    for b in range(first, n):
        o, h, l = day["o"][b], day["h"][b], day["l"][b]
        if o is not None:
            if turning:
                return {"exit": o, "exit_bar": b, "reason": "TURN", "at_open": True}
            later = b > b0
            if stop is not None and ((side > 0 and ir.below(l, stop)) or (side < 0 and ir.above(h, stop))):
                gapped = later and (not ir.above(o, stop) if side > 0 else not ir.below(o, stop))
                return {"exit": o if gapped else stop, "exit_bar": b, "reason": "STOP", "at_open": gapped}
            if target is not None and ((side > 0 and ir.above(h, target)) or (side < 0 and ir.below(l, target))):
                gapped = later and (not ir.below(o, target) if side > 0 else not ir.above(o, target))
                return {"exit": o if gapped else target, "exit_bar": b, "reason": "TARGET", "at_open": gapped}
        start = 5 * (b // 5)
        if use_turn and b % 5 == 4 and (start > b0 or (start == b0 and not limit)):
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


def score(trade: dict, day, spy_day, f: dict, spy_f: dict, scale: float, *, side: int | None = None) -> dict | None:
    """The trade's own side by default; the twin passes the other side."""
    side = trade["side"] if side is None else side
    entry = trade["entry"]
    stop, target = trade["stop"], trade["target"]
    if side != trade["side"]:                        # the twin: same fill, reflected brackets, the mirrored turn exit
        stop = None if stop is None else 2 * entry - stop
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
           split: str = "explore", events: dict | None = None) -> dict:
    """The lab's pass over the bars: the replay's grid, eligibility and history, the variants' trades. Months past the
    split run() asks for are refused (the discovery months by default); the hold-out always is."""
    if months[0] > months[1]:
        raise ValueError("INTRADAY_LAB_MONTHS_INVERTED")
    if months[1] >= HOLDOUT_FROM:
        raise ValueError("INTRADAY_LAB_NEVER_READS_THE_HOLDOUT")
    limit = {"explore": EXPLORE[1], "confirm": CONFIRM[1], "check": CHECK[1]}[split]
    if months[1] > limit:
        raise ValueError(f"INTRADAY_LAB_{split.upper()}_READS_ONLY_THROUGH_{limit}")
    events = ir.earnings() if events is None else events
    spy_rows = prices["SPY"]
    loaded = [ir.previous_month(months[0])] + ir.months_between(*months)
    detect = ir.detection_months(months)              # earlier months too, only to find contradicted closes
    spy_seen = {m: bars_for("SPY", m) for m in detect}
    spy_days = {s: d for m in loaded for s, d in spy_seen[m].items()}
    spy_feat, spy_bad = ir.spy_features(spy_rows, {s: d for m in detect for s, d in spy_seen[m].items()})
    calendar = [r["date"] for r in spy_rows]
    grid = sorted({*spy_days, *calendar} & {s for s in calendar if months[0] <= s[:7] <= months[1]})
    sessions, eligible = set(grid), defaultdict(int)
    trades, skips, excluded = [], defaultdict(int), defaultdict(int)
    for ticker in tickers:
        rows = prices.get(ticker, [])
        by_month = {month: bars_for(ticker, month) for month in detect}
        feats = ir.daily_features(rows, spy_rows,
                                  ir.off_rows(rows, {s: d for m in detect for s, d in by_month[m].items()}), spy_bad)
        daily = {"first": rows[0]["date"] if rows else None, "dates": {r["date"] for r in rows}}
        ev = events.get(ticker, {"primary": {}, "any": set()})
        hist = ir.History()
        seen = set()
        for month in loaded:
            days = by_month[month]
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
                        ctx = {"spy": spy_days[session], "spy_f": spy_feat[session],
                               "timing": ev["primary"].get(session)}
                        for vid, spec in variants.items():
                            t = SETUPS[spec["setup"]](day, f, gap, spec, ctx)
                            if t is None:
                                continue
                            if "skip" in t:
                                skips[f"{vid}:{t['skip']}"] += 1
                                continue
                            scored = _score_pair(t, day, spy_days[session], f, spy_feat[session], hist,
                                                 spec.get("scale", SCALE_WINDOW))
                            if scored is None:
                                skips[f"{vid}:UNSCORABLE"] += 1
                                continue
                            trades.append({"variant": vid, "ticker": ticker, "session": session,
                                           "month": session[:7], "gap": gap, "rank": t.get("rank", 0.0), **scored})
                hist.update(day, session)
        for session in grid:
            if session not in seen:
                excluded["PRE_LISTING" if session < ir.LISTED.get(ticker, "") else
                         ir._no_features(session, daily) if session not in feats else "NO_MINUTE_BARS"] += 1
    kept = []                                          # caps per session, largest rank (gap) first
    for vid, spec in variants.items():
        mine = [t for t in trades if t["variant"] == vid]
        if not spec.get("cap"):
            kept += mine
            continue
        by_session = defaultdict(list)
        for t in mine:
            by_session[t["session"]].append(t)
        for session, ts in by_session.items():
            ts = sorted(ts, key=lambda t: (-t["rank"], t["ticker"]))
            kept += ts[:spec["cap"]]
            if len(ts) > spec["cap"]:
                skips[f"{vid}:CAP"] += len(ts) - spec["cap"]
    return {"trades": kept, "sessions": sorted(sessions), "eligible": dict(eligible), "skips": dict(skips),
            "excluded": dict(excluded)}


def _score_pair(t, day, spy_day, f, spy_f, hist, window: str = SCALE_WINDOW) -> dict | None:
    scale = hist.scale(window, day)
    if scale is None:
        return None
    own = score(t, day, spy_day, f, spy_f, scale)
    mirror = score(t, day, spy_day, f, spy_f, scale, side=-t["side"])
    if own is None or mirror is None:
        return None
    return {"entry_bar": t["entry_bar"], "kind": t["kind"], "reason": own["reason"], "minutes": own["minutes"],
            "r_gross": own["r_gross"], "net_bps": {c: (own["residual"] - 2 * c / 1e4) * 1e4 for c in COSTS_BPS},
            "gross_bps": own["residual"] * 1e4, "reward": own["rewards"],
            "skill": (own["rewards"][HEADLINE_COST_BPS] - mirror["rewards"][HEADLINE_COST_BPS]) / 2,
            "control": (own["rewards"][HEADLINE_COST_BPS] + mirror["rewards"][HEADLINE_COST_BPS]) / 2}


def evaluate(run: dict, variants: dict, split: str = "explore") -> dict:
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
            "pass_bar": pass_bar(trades, split),
            "skips": {k.split(":", 1)[1]: v for k, v in run["skips"].items() if k.startswith(f"{vid}:")},
        }
    return out


def _mean(values):
    values = [v for v in values if v is not None]
    return math.fsum(values) / len(values) if values else None


def session_t(values: list[float], clusters: list[str]) -> float | None:
    """t of the mean of `values` with the leave-one-cluster-out jackknife standard error (clusters here are
    sessions): var = (G - 1) / G x sum over clusters g of (the mean without g - the mean)^2. With one value per
    cluster this is the ordinary t; a cluster that carries the mean alone inflates the variance by its own weight."""
    n = len(values)
    by = defaultdict(list)
    for x, c in zip(values, clusters):
        by[c].append(x)
    g = len(by)
    if g < 2:
        return None
    total = math.fsum(values)
    m = total / n
    var = (g - 1) / g * math.fsum(((total - math.fsum(xs)) / (n - len(xs)) - m) ** 2 for xs in by.values())
    return m / math.sqrt(var) if var > 1e-24 else None


def pass_bar(trades: list[dict], split: str = "confirm") -> dict:
    """The split's pass bar (PASS_BARS), fixed before its months were looked at. Explore shows the confirm's
    thresholds on the explore periods, for reference only."""
    bar = PASS_BARS.get(split) or {**PASS_BARS["confirm"], "periods": EXPLORE_PERIODS}
    cost = bar["cost_bps"]
    values = [t["net_bps"][cost] for t in trades]
    t_stat = session_t(values, [t["session"] for t in trades])
    per_session = _count(t["session"] for t in trades)
    periods = {f"{a}..{b}": _mean([t["net_bps"][cost] for t in trades if a <= t["month"] <= b])
               for a, b in bar["periods"]}
    mean = _mean(values)
    positive = sum(1 for v in periods.values() if v is not None and v > 0)
    passed = (mean is not None and mean > 0 and t_stat is not None and t_stat > bar["min_t"]
              and positive >= bar["min_positive_periods"])
    return {"split": split, "cost_bps": cost, "min_t": bar["min_t"], "min_positive_periods": bar["min_positive_periods"],
            "trades": len(trades), "sessions": len(per_session), "mean_net_bps": mean, "session_t": t_stat,
            "largest_session_share": max(per_session.values()) / len(trades) if trades else None,
            "periods": periods, "positive_periods": positive,
            "verdict": ("PASS" if passed else "FAIL") if split in PASS_BARS else None}


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
    if split not in ("explore", "confirm", "check"):
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
        months = CONFIRM if split == "confirm" else CHECK
        if ir.missing_months(dev_root, symbols, [ir.previous_month(months[0])] + ir.months_between(*months)):
            return {"status": "REFUSED", "reason": f"{split.upper()}_FETCH_INCOMPLETE"}
        allowed = confirm_allowed if split == "confirm" else check_allowed
        refused = {v: why for v in variant_ids for ok, why in [allowed(v, log, inputs_sha(tickers))] if not ok}
        if refused:
            return {"status": "REFUSED", "reason": f"{split.upper()}_NOT_ALLOWED", "variants": refused}
    prices, prices_sha = ivr.load_prices(prices_dir)
    if prices_sha != ir.DAILY_PRICES_SHA256:
        return {"status": "REFUSED", "reason": "DAILY_PRICES_NOT_REGISTERED"}
    if ir.behavior_digest() != ir.BEHAVIOR_SHA256:
        return {"status": "REFUSED", "reason": "REPLAY_CODE_CHANGED"}
    variants = {v: VARIANTS[v] for v in variant_ids}
    used = ir.detection_months(months)
    result = replay(lambda t, m: ir.load_month(dev_root, t, m), prices, months, sorted(tickers), variants,
                    split=split)
    evaluation = evaluate(result, variants, split)
    bars_sha = ir.files_sha(dev_root, used)
    entries = log_entries(split, months, bars_sha, evaluation, log, sorted(tickers))
    write_log(log + entries, log_path)
    return {"status": "OK", "lab_id": LAB_ID, "split": split, "months": list(months),
            "generated_at": entries[0]["at"], "sessions": len(result["sessions"]),
            "eligible_stock_days": sum(result["eligible"].values()), "excluded": result["excluded"],
            "variants": evaluation, "bars_sha256": bars_sha, "lab_code_sha256": CODE_SHA,
            "confirms_so_far": sum(1 for e in log + entries if e["split"] == "confirm"),
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


def _same_strategy(e: dict, strategy: str) -> bool:
    return e.get("spec_sha256", spec_sha(e["spec"]) if "spec" in e else None) == strategy


def _this_run(e: dict, variant_id: str, inputs: str) -> bool:
    """The entry was made of this definition, with this code and these inputs."""
    return (e["definition_sha256"] == definition_sha(variant_id) and e["lab_code_sha256"] == CODE_SHA
            and e.get("inputs_sha256") == inputs)


def _explored(variant_id: str, log: list[dict], inputs: str) -> bool:
    return any(e["split"] == "explore" and _this_run(e, variant_id, inputs) for e in log)


def confirm_allowed(variant_id: str, log: list[dict], inputs: str) -> tuple[bool, str]:
    """A confirm needs an explore of this definition with this code and these inputs, and no earlier confirm of the
    same strategy."""
    strategy = spec_sha(VARIANTS[variant_id])
    if any(e["split"] == "confirm" and _same_strategy(e, strategy) for e in log):
        return False, "ALREADY_CONFIRMED"
    if not _explored(variant_id, log, inputs):
        return False, "NOT_EXPLORED_WITH_THIS_CODE"
    return True, "OK"


def check_allowed(variant_id: str, log: list[dict], inputs: str) -> tuple[bool, str]:
    """A check needs a passed confirm of the same strategy made with this definition, code and inputs (so the check
    runs exactly what passed), and no earlier check of the same strategy (the spec, whatever its id or the wording of
    the conventions)."""
    strategy = spec_sha(VARIANTS[variant_id])
    if any(e["split"] == "check" and _same_strategy(e, strategy) for e in log):
        return False, "ALREADY_CHECKED"
    passed = [e for e in log if e["split"] == "confirm" and _same_strategy(e, strategy) and e.get("verdict") == "PASS"]
    if not passed:
        return False, "NOT_CONFIRMED"
    if not any(_this_run(e, variant_id, inputs) for e in passed):
        return False, "CONFIRMED_WITH_OTHER_CODE"
    return True, "OK"


def log_entries(split: str, months: tuple[str, str], bars_sha: str, evaluation: dict, log: list[dict],
                tickers: list[str] | None = None) -> list[dict]:
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    start = len(log)
    return [{"entry": start + i + 1, "at": at, "lab_id": LAB_ID, "split": split, "months": list(months),
             "variant": vid, "definition_sha256": definition_sha(vid), "spec": VARIANTS[vid],
             "spec_sha256": spec_sha(VARIANTS[vid]), "lab_code_sha256": CODE_SHA, "bars_sha256": bars_sha,
             "tickers_sha256": digest(sorted(tickers or [])), "inputs_sha256": inputs_sha(tickers or []),
             "trades": r["trades"], "net_bps_at_10": r["per_trade"]["net_bps_at_10"],
             "net_bps_at_2": r["per_trade"]["net_bps_at_2"], "gross_bps": r["per_trade"]["gross_bps"],
             "book_at_10": {k: r["book"]["at_10"].get(k) for k in ("mean", "lower", "upper")},
             "direction_skill": {k: r["direction_skill"].get(k) for k in ("mean", "lower", "upper")},
             "pass_bar": r.get("pass_bar"),
             **({"verdict": r["pass_bar"]["verdict"]} if split in ("confirm", "check") and r.get("pass_bar") else {})}
            for i, (vid, r) in enumerate(evaluation.items())]


def replay_reads_through(log: list[dict] | None = None) -> str | None:
    """The last month intraday_replay's previews and development run may read while the lab's one-time looks are
    pending: the explore months until every frozen variant's confirm is logged, then the confirm months until every
    one that passed has its check logged; None once nothing is pending."""
    log = read_log() if log is None else log
    confirms = {e["variant"]: e for e in log if e["split"] == "confirm"}
    if any(v not in confirms for v in FROZEN):
        return EXPLORE[1]
    checked = {e["variant"] for e in log if e["split"] == "check"}
    if any(confirms[v].get("verdict") == "PASS" and v not in checked for v in FROZEN):
        return CONFIRM[1]
    return None


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
    lead = ("Exploratory: these numbers decide nothing." if report["split"] not in PASS_BARS else
            f"The one {report['split']} of each variant below: its verdict against the pass bar fixed beforehand is "
            "final" + (" and decides whether it may be checked." if report["split"] == "confirm" else
                       " and decides whether it may go to a hold-out protocol."))
    lines = [f"# Intraday lab: {report['split']} run, {report['months'][0]}..{report['months'][1]}", "",
             f"`{LAB_ID}`, generated {report['generated_at']}. {lead} Every run is in "
             f"`docs/evidence/intraday-lab-log.json`: **{report['variants_tried']} variant tries so far** (a "
             f"definition run with a given version of the code), **{report.get('confirms_so_far', 0)} one-time "
             f"confirms** ({CONFIRM[0]}..{CONFIRM[1]}) and **{report.get('checks_so_far', 0)} one-time checks** "
             f"({CHECK[0]}..{CHECK[1]}). The more variants are tried, the more one of them will look good by luck, "
             "which is why survivors are confirmed once, then checked once, and only then handed to the one "
             "hold-out run.", "",
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
    bars = {vid: r["pass_bar"] for vid, r in report["variants"].items() if r.get("pass_bar")}
    if bars:
        decides = report["split"] in PASS_BARS
        heading = ("**The pass bar**" if decides else
                   "**The pass bar, for reference** (the confirm's thresholds on the explore periods; it decides "
                   "only on the confirm and check months)")
        first = next(iter(bars.values()))
        names = list(first["periods"])
        lines += ["", f"{heading}: at {first['cost_bps']} bps a side, mean net per trade above 0, t above "
                  f"{first['min_t']:g} (leave-one-session-out jackknife), positive in at least "
                  f"{first['min_positive_periods']} of the {len(names)} periods.", "",
                  "| Variant | Trades | Sessions | Largest session | Mean net bps | t | " + " | ".join(names)
                  + " | Verdict |", "|---|---|---|---|---|---|" + "---|" * len(names) + "---|"]
        for vid, pb in bars.items():
            t_stat = "–" if pb["session_t"] is None else f"{pb['session_t']:.2f}"
            share = "–" if pb["largest_session_share"] is None else f"{pb['largest_session_share']:.0%}"
            lines.append(f"| {vid} | {pb['trades']} | {pb['sessions']} | {share} | {_bps(pb['mean_net_bps'])} | "
                         f"{t_stat} | " + " | ".join(_bps(v) for v in pb["periods"].values())
                         + f" | {pb['verdict'] or '–'} |")
    skips = {vid: r["skips"] for vid, r in report["variants"].items() if r["skips"]}
    lines += ["", "**Skips:** " + ("; ".join(f"{vid} " + ", ".join(f"{k} {v}" for k, v in sk.items())
                                             for vid, sk in skips.items()) if skips else "none") + "."]
    return "\n".join(lines) + "\n"
