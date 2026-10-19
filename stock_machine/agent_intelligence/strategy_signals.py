"""Pre-registered event strategies: entry and exit rules on normalized daily bars.

Pure functions shared by the historical event replay, the forward shadow and
the agent-facing signal view. Every rule is judged on a session's completed
bar (its close) and executed at the next session's close; no rule reads a bar
after the session it judges. Long only. Nothing here trades.

Bars come from technical_setups.normalized (OHLC rescaled to the adjusted
close, incomplete bars dropped). ATR is computed here from those bars:
features._atr_pct mixes unadjusted high/low with the adjusted close and is not
used.
"""
from __future__ import annotations

from math import sin, sqrt
from statistics import mean

from ..agents.contracts import digest
from . import technical_setups

VERSION = "event-strategies.v1"
WINDOW = 60
ATR_SESSIONS = 14
ATR_MULTIPLE = 2.0
RISING_LAG = 5
MOMENTUM_SESSIONS = 63
BETA_RANGE = (0.0, 3.0)
DEFAULT_BETA = 1.0
MIN_VOL = 0.05

RULES = {
    "S1_volume_breakout": {"role": "confirmatory", "max_hold": 20, "exits": ("trailing_stop", "trend_failure")},
    "S2_trend_pullback": {"role": "confirmatory", "max_hold": 20, "exits": ("trailing_stop", "trend_failure")},
    "S3_post_earnings": {"role": "confirmatory", "max_hold": 25, "exits": ("gap_fill", "trailing_stop"),
                         "z_min": 2.0, "reaction_sessions": 2},
    "S4_value_momentum": {"role": "exploratory", "max_hold": 30, "exits": ("trailing_stop", "trend_failure"),
                          "gap_min": 0.20},
}
DEFINITIONS = {
    "S1_volume_breakout": "close above the prior 20-session high with volume at least 1.5x the prior 20-session "
                          "average (technical_setups volume_breakout, split-guarded), close above the 50-session "
                          f"average, and that average rising over {RISING_LAG} sessions",
    "S2_trend_pullback": "close above the 20-session average above the 50-session average, the session's low at or "
                         "below the 20-session average, close back above it and above the prior close "
                         f"(technical_setups pullback); the 50-session average rising over {RISING_LAG} sessions "
                         f"and {MOMENTUM_SESSIONS}-session momentum positive",
    "S3_post_earnings": "earnings release (SEC 8-K item 2.02, primary results) whose stock-specific reaction over "
                        "the two sessions from the first close after acceptance, measured against the close before "
                        "and beta-adjusted against SPY, is at least 2.0 ex-ante volatility units",
    "S4_value_momentum": "S1 or S2 entry for a solid company at least 20% below its sector-median P/E fair value "
                         "at the latest month-end (exploratory)",
    "exits": f"judged on each session's close from entry: trailing stop when the close falls more than "
             f"{ATR_MULTIPLE:g}x the signal-day ATR({ATR_SESSIONS}) below the highest close since entry; trend "
             "failure when the close is below the 50-session average; gap fill when the close returns to or below "
             "the pre-announcement close (S3); executed at the next close; otherwise exit at the maximum hold",
    "timing": "signal at a session's close; entry at the next session's close; one open trade per stock and "
              "strategy",
}


class Bars:
    """Normalized bars aligned to exchange sessions, with trailing indicators.

    sessions: the exchange sessions to align to (ascending). A session without a
    complete bar is None; any indicator whose window touches it is None.
    """

    def __init__(self, rows: list[dict], cutoff: str, sessions: list[str]):
        normalized = technical_setups.normalized(rows, cutoff)
        self.sessions = [s for s in sessions if s <= cutoff]
        self.index = {s: i for i, s in enumerate(self.sessions)}
        self.bars = [normalized.get(s) for s in self.sessions]
        self.close = [b["close"] if b else None for b in self.bars]
        n = len(self.sessions)
        self.sma20 = [self._mean(i, 20) for i in range(n)]
        self.sma50 = [self._mean(i, 50) for i in range(n)]
        self.atr = [self._atr(i) for i in range(n)]

    def _complete(self, i: int, length: int) -> bool:
        return i - length + 1 >= 0 and all(self.close[j] is not None for j in range(i - length + 1, i + 1))

    def _mean(self, i: int, length: int):
        return mean(self.close[i - length + 1:i + 1]) if self._complete(i, length) else None

    def _atr(self, i: int):
        if not self._complete(i, ATR_SESSIONS + 1):
            return None
        ranges = []
        for j in range(i - ATR_SESSIONS + 1, i + 1):
            b, prior = self.bars[j], self.close[j - 1]
            ranges.append(max(b["high"], prior) - min(b["low"], prior))
        return mean(ranges)

    def window(self, i: int, length: int = WINDOW):
        return self.bars[i - length + 1:i + 1] if self._complete(i, length) else None

    def rising50(self, i: int) -> bool:
        a, b = self.sma50[i], (self.sma50[i - RISING_LAG] if i >= RISING_LAG else None)
        return a is not None and b is not None and a > b

    def momentum(self, i: int, length: int = MOMENTUM_SESSIONS):
        if i < length or self.close[i] is None or self.close[i - length] is None:
            return None
        return self.close[i] / self.close[i - length] - 1


def _setup(bars: Bars, i: int):
    window = bars.window(i)
    return technical_setups.features(window) if window else None


def s1_entry(bars: Bars, i: int) -> bool:
    f = _setup(bars, i)
    return bool(f) and f["signals"]["volume_breakout"] == 1 and f["price_vs_sma50"] > 0 and bars.rising50(i)


def s2_entry(bars: Bars, i: int) -> bool:
    f = _setup(bars, i)
    momentum = bars.momentum(i)
    return (bool(f) and f["signals"]["pullback"] == 1 and bars.rising50(i)
            and momentum is not None and momentum > 0)


def reaction_z(bars: Bars, spy: Bars, reaction_i: int, *, beta, vol20) -> float | None:
    """Stock-specific reaction over sessions R and R+1 versus the close before R."""
    last = reaction_i + RULES["S3_post_earnings"]["reaction_sessions"] - 1
    points = (reaction_i - 1, last)
    if reaction_i < 1 or last >= len(bars.sessions) or any(
            bars.close[j] is None or spy.close[j] is None for j in points):
        return None
    b = DEFAULT_BETA if beta is None else min(BETA_RANGE[1], max(BETA_RANGE[0], float(beta)))
    stock = bars.close[last] / bars.close[reaction_i - 1] - 1
    market = spy.close[last] / spy.close[reaction_i - 1] - 1
    scale = max(vol20 if vol20 else 0.0, MIN_VOL) * sqrt(RULES["S3_post_earnings"]["reaction_sessions"] / 252)
    return (stock - b * market) / scale


def simulate_exit(bars: Bars, entry_i: int, strategy: str, *, atr_ref: float, gap_level: float | None = None):
    """(exit_index, reason) once the path is complete, ("PENDING", None) before
    the maximum hold has passed, or ("EXCLUDED_MISSING_BARS", None)."""
    rule = RULES[strategy]
    last = entry_i + rule["max_hold"]
    if last >= len(bars.sessions):
        # Not matured: still decide early exits that have already happened.
        for k in range(entry_i, len(bars.sessions) - 1):
            if bars.close[k] is None:
                return "EXCLUDED_MISSING_BARS", None
            reason = _exit_reason(bars, entry_i, k, rule, atr_ref, gap_level)
            if reason:
                if bars.close[k + 1] is None:
                    return "EXCLUDED_MISSING_BARS", None
                return k + 1, reason
        return "PENDING", None
    if any(bars.close[j] is None for j in range(entry_i, last + 1)):
        return "EXCLUDED_MISSING_BARS", None
    for k in range(entry_i, last):
        reason = _exit_reason(bars, entry_i, k, rule, atr_ref, gap_level)
        if reason:
            return k + 1, reason
    return last, "MAX_HOLD"


def _exit_reason(bars: Bars, entry_i: int, k: int, rule: dict, atr_ref: float, gap_level):
    close = bars.close[k]
    peak = max(bars.close[entry_i:k + 1])
    if "gap_fill" in rule["exits"] and gap_level is not None and close <= gap_level:
        return "GAP_FILL"
    if "trailing_stop" in rule["exits"] and atr_ref and close < peak - ATR_MULTIPLE * atr_ref:
        return "TRAILING_STOP"
    if "trend_failure" in rule["exits"] and bars.sma50[k] is not None and close < bars.sma50[k]:
        return "TREND_FAILURE"
    return None


def live_flags(bars: Bars, i: int) -> dict:
    """Today's technical entry flags (S3 needs an event, S4 a fundamentals snapshot)."""
    return {"S1_volume_breakout": s1_entry(bars, i), "S2_trend_pullback": s2_entry(bars, i)}


# ---- behaviour pin -----------------------------------------------------------------

def _fixture_bars(drift, wobble, start, *, spike_at=None, spike_volume=1.0, gap_at=None, gap=0.0, length=220,
                  turn_at=None, drift_after=0.0):
    days = []
    from datetime import date, timedelta
    d = date(2024, 1, 2)
    while len(days) < length:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    rows, price = [], start
    for i, day in enumerate(days):
        step = drift_after if turn_at is not None and i >= turn_at else drift
        price *= 1 + step + wobble * sin(i / 4) + (gap if i == gap_at else 0.0)
        volume = 1_000_000 * (spike_volume if i == spike_at else 1.0)
        rows.append({"date": day, "open": price * 0.995, "high": price * 1.01, "low": price * 0.985,
                     "close": price, "adj_close": price, "volume": volume})
    return rows, days


def behavior_digest() -> str:
    """Entries and exits on fixed synthetic paths (rounded), pinned by the replay protocol."""
    out = []
    cases = [
        ("trend_up", dict(drift=0.002, wobble=0.012, start=50.0)),
        ("breakout", dict(drift=0.001, wobble=0.004, start=80.0, spike_at=150, spike_volume=3.0, gap_at=150, gap=0.06)),
        ("reversal", dict(drift=0.003, wobble=0.01, start=30.0, gap_at=170, gap=-0.15)),
        ("flat", dict(drift=0.0, wobble=0.015, start=60.0)),
        ("rounded_top", dict(drift=0.002, wobble=0.006, start=40.0, turn_at=140, drift_after=-0.001)),
    ]
    for name, spec in cases:
        rows, days = _fixture_bars(**spec)
        bars = Bars(rows, days[-1], days)
        for i in range(WINDOW, len(days)):
            for strategy, check in (("S1_volume_breakout", s1_entry), ("S2_trend_pullback", s2_entry)):
                if check(bars, i) and i + 1 < len(days):
                    exit_i, reason = simulate_exit(bars, i + 1, strategy, atr_ref=bars.atr[i])
                    out.append([name, strategy, days[i], exit_i if isinstance(exit_i, str) else days[exit_i],
                                reason, round(bars.atr[i] or 0.0, 6)])
    return digest({"version": VERSION, "rules": RULES, "events": out})
