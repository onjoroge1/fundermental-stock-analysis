"""Pre-registered intraday replay: Meir Barak's first-hour setups and the published opening-range breakout.

Five confirmatory strategies on IBKR 1-minute regular-session bars of the covered stocks (SPY the hedge):

  G1  gap and go (gaps of 3% or more): a counter-move against the gap fails at minutes 3-9; trade with the gap,
      stop at the counter-move extreme, 1R target, flat by 10:30 (Barak's flagship setup)
  G2  gap fill (gaps of 1-3%): a 5-minute reversal bar against the gap; target the prior close, stop at the
      reversal bar, flat by 10:30 (Barak)
  B1  break of structure: the first 5-minute range broken in the gap's direction on gaps of 2% or more, stop at
      the other side, flat by 10:30 (the 5-minute breakout taught on his channel)
  C1  the "h structure": four bars each narrower than the last relative to that minute's normal range, then a
      breakout either way, stop at the other side, flat by 10:30
  X1  the published opening-range breakout on stocks in play (Zarattini, Barbon & Aziz 2024): the five highest
      first-5-minute relative volumes, 10%-of-ATR stop, held to the close

plus exploratory E1 (earnings-day continuation), M1 (SPY intraday momentum), R1 (end-of-day reversal) and A1
(attention-driven gap fade). Every trade is scored as reward v5 (residual against beta x SPY over the same
minutes, net of 10 bps a side, over the stock's own realized volatility in that window) and paired with a
mirror trade taken the other way (the random-direction twin), so direction skill is measured apart from the
bracket. One value per session: the sum of the session's rewards over its eligible stock-days.

Development (2016-01..2023-12) is descriptive; the hold-out (2024-01..2026-08) is fetched only after the
protocol and the development inputs are registered and runs once. The bars stay local (IBKR's terms): only
aggregates are committed. Information only: nothing here trades.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from collections import defaultdict, deque
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from ..agent_intelligence import inference
from ..agents.contracts import AGENT_UNIVERSE, digest
from . import event_replay as er
from . import implied_vol_replay as ivr

PROTOCOL_ID = "intraday-first-hour.v1"
DEV = ("2016-01", "2023-12")
HOLDOUT = ("2024-01", "2026-08")
CONFIRMATORY = ("G1", "G2", "B1", "C1", "X1")
EXPLORATORY = ("E1", "M1", "R1", "A1")
Z_FAMILY = 2.575829        # two-sided, Bonferroni over the five confirmatory strategies at family-wise 95%
Z_95 = 1.959964
COST_PER_SIDE = 0.0010     # outcomes.FILL_COST_BPS_PER_SIDE (10 bps), the verdict cost
LISTED = {"HIMS": "2021-01-21", "SOFI": "2021-06-01"}   # SPAC shells before (implied_vol_replay)
CORPORATE_ACTIONS = {"IBM": ("2021-11-04",),             # Kyndryl spin-off: the prior close is not adjusted for it
                     "T": ("2022-04-11",)}               # WarnerMedia spin-off: first session after the distribution
DAILY_PRICES_SHA256 = ivr.REGISTERED_INPUTS["prices_sha256"]   # data/ibkr_prices/2026-10-20, as registered there
ET = ZoneInfo("America/New_York")

# X0: shared conventions
PRICE_FLOOR = 10.0         # split-adjusted prior close: the bars are split-adjusted and rounded to cents, so this
                           # bounds the rounding to 10 bps (and subsumes the $5 floors of X1 and R1)
OPEN_MATCH = 0.002         # the first minute's open within 0.2% of the daily open (guards a split between fetches)
NORM_SESSIONS = 20         # trailing sessions for minute norms and window volatility
NORM_MIN = 15
ENTRY_LAST = 45            # 10:15: last entry bar for the first-hour strategies
EXIT_BAR = 59              # 10:29: its close is the 10:30 price
BETA_SESSIONS = 63
BETA_RANGE = (0.0, 3.0)
MIN_ANNUAL_VOL = 0.05      # scale floor: 5% annual volatility over the window
SSR_DROP = 0.10            # Reg SHO Rule 201 trigger
SSR_PREOPEN = 0.92         # an open this far below the prior close: assume a pre-market trigger
# G1
G1_GAP = 0.03
G1_SIGNAL = (3, 9)
G1_RETRACE = 0.5
G1_TARGET_R = 1.0
# G2
G2_GAP = (0.01, 0.03)
G2_FIVE_MINUTE = (1, 8)
G2_MIN_RR = 1.0
# B1
B1_GAP = 0.02
OR_BARS = 5
# C1
C1_K = 4
C1_X = 0.60
C1_RUN_END = (3, 44)
C1_ORDER_LIFE = 10
C1_RESOLUTION = 0.05
# X1
X1_ATR_MIN = 0.50
X1_RV_MIN = 1.0
X1_TOP = 5
X1_ATR_STOP = 0.10
RV_SESSIONS = 14
# E1
E1_THRESHOLD = 1.0
E1_BAR = 30                # 10:00
# R1
R1_MIN_HISTORY = 126
R1_RANK_BAR = 329          # the 14:59 close (15:00)
LAST_HALF_HOUR = 360       # 15:30
# A1
A1_SIGMA = 2.0
A1_MIN_GAP = 0.015
A1_LOOKBACK = 60
A1_BAR = 5                 # 09:35
# coverage per confirmatory strategy in the hold-out
MIN_TRADES = 150
MIN_ACTIVE_MONTHS = 18
MIN_STOCKS = 15
MAX_STOCK_SHARE = 0.25
# claim checks (Barak's own statements; the paper's gross R)
CLAIMS = {"G1_hit_rate": 0.68, "G1_gross_R": 0.38, "G2_fill_rate": 0.80, "B1_failure_rate": 0.80,
          "X1_gross_R": 0.08}
# realized-volatility windows (first bar, last bar; None = the session's last bar)
WINDOWS = {"FIRST_HOUR": (0, EXIT_BAR), "AFTER_0935": (OR_BARS, None), "AFTER_1000": (E1_BAR, None),
           "LAST_HALF_HOUR": (LAST_HALF_HOUR, None)}
WINDOW_OF = {"G1": "FIRST_HOUR", "G2": "FIRST_HOUR", "B1": "FIRST_HOUR", "C1": "FIRST_HOUR", "X1": "AFTER_0935",
             "E1": "AFTER_1000", "A1": "AFTER_0935", "R1": "LAST_HALF_HOUR", "M1": "LAST_HALF_HOUR"}

REGISTERED_PROTOCOL_SHA256 = None
REGISTERED_DEV_INPUTS = None
REGISTERED_HOLDOUT_INPUTS = None
BEHAVIOR_SHA256 = "a98e44bf769c19bdf1a5da71107608c803d62d22cd869bace65bdc79ecae656a"


def _pct(x: float) -> str:
    return f"{x * 100:g}%"


def _clock(bar: int) -> str:
    return f"{9 + (30 + bar) // 60:02d}:{(30 + bar) % 60:02d}"


def _protocol() -> dict:
    cost = f"{COST_PER_SIDE * 1e4:g} bps"
    return {
        "protocol_id": PROTOCOL_ID,
        "question": "do Meir Barak's first-hour setups, or the published opening-range breakout, beat not trading "
                    "after costs, with direction skill beyond a random-direction twin of the same bracket",
        "universe": sorted(AGENT_UNIVERSE), "hedge": "SPY", "listed": LISTED,
        "corporate_actions": {k: list(v) for k, v in CORPORATE_ACTIONS.items()},
        "data": "IBKR TRADES 1-minute bars, regular hours, fetched a week per request (split-adjusted, rounded to "
                "cents, not dividend-adjusted; a bar with no trades is a missing minute); IBKR daily TRADES and "
                "ADJUSTED_LAST bars for the prior close, the dividend factor, beta, ATR and the official close; the "
                "pinned SEC 8-K item 2.02 dataset for earnings sessions",
        "inputs": {"daily_prices_sha256": DAILY_PRICES_SHA256,
                   "earnings_sha256": er.EARNINGS_ANNOUNCEMENTS_SHA256,
                   "bars": "development and hold-out bar files are registered by content hash, every ticker-month "
                           "with a finished fetch"},
        "splits": {"development": list(DEV), "holdout": list(HOLDOUT),
                   "holdout_rule": "fetched only after this protocol and the development inputs are registered; run "
                                   "once; pseudo-out-of-sample: the hold-out's daily bars were already used by other "
                                   "tests, its minute bars were never seen"},
        "conventions": {
            "calendar": "SPY's daily sessions; every ticker-session is eligible or excluded with a reason, including "
                        "sessions without minute bars (PRE_LISTING, NO_DAILY_BARS, NO_MINUTE_BARS)",
            "prior_close": "split-adjusted TRADES close of the prior session times the dividend factor "
                           "(ADJUSTED_LAST/close of the prior session over that of the session, set to 1 within the "
                           "rounding bound 0.0002 + 0.01/adjusted price + 0.01/price, and above 1 plus the bound "
                           "counted as an anomaly and set to 1); the stock's two prior daily rows must be the two "
                           "prior sessions (else STALE_PRIOR_CLOSE)",
            "eligible": f"listed; prior daily rows current; not a listed corporate action; prior close of at least "
                        f"{PRICE_FLOOR:g} split-adjusted (cent rounding of the adjusted bars; stocks that split later "
                        f"lose early years: excluded counts are reported per ticker); the 09:30 bar traded, its open "
                        f"within {_pct(OPEN_MATCH)} of the daily open; at least {NORM_MIN} prior sessions of bars; "
                        "SPY's session present. Every strategy draws only on eligible stock-days, which are each "
                        "session's denominator",
            "fills": "a signal is known at its bar's close and a market entry fills at the next bar's open; a stop entry "
                     "is live from the next bar and fills when price trades strictly through the level, at the level or "
                     "at the bar's open if it opens through; protective stops and targets likewise, at the level or the "
                     "open; a stop and a target in the same bar: the stop. Inside a bar prices are taken to run open, "
                     "low, high, close (an up bar) or open, high, low, close (a down bar): a stop entry's fill bar is "
                     "checked for the stop only, along that path after the fill, and a C1 bar through both levels "
                     "fills the level its path reaches first. The whole-bar check is reported as a diagnostic. "
                     f"First-hour entries on bars 1-{ENTRY_LAST} ({_clock(1)}-{_clock(ENTRY_LAST)}), forced exit at "
                     f"the {_clock(EXIT_BAR)} close (the last earlier close if that bar is missing); a missing bar "
                     "after entry carries the position to the next bar",
            "short_sale_rule": f"no short entry if the prior session's low was {_pct(SSR_DROP)} or more below the close "
                               f"before it, if the open is {_pct(1 - SSR_PREOPEN)} or more below the prior close, or "
                               f"if any bar up to the signal traded {_pct(SSR_DROP)} or more below the prior close "
                               "(skipped and counted; R1's short leg too; C1 keeps its buy stop); the twin is never "
                               "restricted",
            "one_trade": "at most one trade per stock, strategy and session; no adds, partials or re-entries",
            "skips": "counted by strategy and reason: RULE_201, ENTRY_BEYOND_STOP and ENTRY_BEYOND_TARGET (a G1 or G2 "
                     "entry open already past its stop or target), UNSCORABLE (no volatility scale yet, or SPY's bar "
                     "missing); the stock-day stays in the denominator",
            "score": f"reward = (side x stock return - side x beta x SPY return over the same minutes - 2 x {cost}) / "
                     "the stock's realized volatility over the strategy's window (the trailing "
                     f"{NORM_SESSIONS} sessions' realized variance per minute, at least {NORM_MIN} sessions, times the "
                     f"window's minutes; floored at {_pct(MIN_ANNUAL_VOL)} a year); SPY is taken at its bar's open for "
                     "an open fill, the bar's midpoint (open + close)/2 for a fill inside a bar, the bar's close for "
                     "a timed exit and its official close for a close exit; beta = "
                     f"{BETA_SESSIONS}-session daily beta to SPY through the prior session, clipped to "
                     f"[{BETA_RANGE[0]:g}, {BETA_RANGE[1]:g}], 1 if fewer than 20 returns; also reported in bps and in "
                     "R (gross price move over the entry risk)",
            "twin": "every trade has a mirror taken the other way at the same fill, with stop and target reflected "
                    "through the entry, the same exits and costs: control = (own + mirror) / 2, direction skill = "
                    "(own - mirror) / 2",
            "unit": "one value per session: the sum of that session's rewards over its eligible stock-days (M1: over "
                    "one); sessions without a trade score 0",
        },
        "strategies": {
            "G1": f"gap of at least {_pct(G1_GAP)}; at the close of bars {G1_SIGNAL[0]}-{G1_SIGNAL[1]}, the first bar "
                  "that follows a counter-move (from the first bar closing against the gap), makes no new counter-move "
                  "extreme and closes beyond the previous bar's extreme in the gap direction, while the counter-move "
                  f"has filled less than {G1_RETRACE:g} of the gap: enter with the gap at the next open; stop at the "
                  f"counter-move extreme, target {G1_TARGET_R:g}R, exit by 10:30",
            "G2": f"gap from {_pct(G2_GAP[0])} to below {_pct(G2_GAP[1])}; the first 5-minute bar "
                  f"{G2_FIVE_MINUTE[0]}-{G2_FIVE_MINUTE[1]} ({_clock(5 * G2_FIVE_MINUTE[0])}-"
                  f"{_clock(5 * G2_FIVE_MINUTE[1] + 4)}) closing against the gap beyond the previous 5-minute bar's "
                  "extreme before price has traded through the prior close, with the prior close at least "
                  f"{G2_MIN_RR:g}R away: enter against the gap at the next open; stop at that 5-minute bar's extreme, "
                  "target the prior close, exit by 10:30",
            "B1": f"gap of at least {_pct(B1_GAP)}; the range of the first {OR_BARS} minutes; a stop entry beyond it "
                  f"in the gap's direction (bars {OR_BARS}-{ENTRY_LAST}); stop at the other side; exit by 10:30",
            "C1": f"{C1_K} consecutive bars each strictly narrower than the last relative to that minute's mean log "
                  f"range over the prior {NORM_SESSIONS} sessions (at least {NORM_MIN}), all at most {C1_X:g} of it, "
                  f"ending at bars {C1_RUN_END[0]}-{C1_RUN_END[1]}, each minute's normal range at least "
                  f"{C1_RESOLUTION:g} dollars; stop entries both ways beyond the run's high and low for "
                  f"{C1_ORDER_LIFE} bars up to bar {ENTRY_LAST} (one cancels the other); stop at the other side; exit "
                  "by 10:30; after the orders expire, a later run may arm them again",
            "X1": f"14-day ATR (split-adjusted) of at least {X1_ATR_MIN:g}; first-5-minute volume over its "
                  f"{RV_SESSIONS}-session mean of at least {X1_RV_MIN:g}; the {X1_TOP} highest of the session among "
                  "those; direction of the first 5-minute candle (none on a doji); a stop entry beyond its range from "
                  f"{_clock(OR_BARS)}; stop {X1_ATR_STOP:g} x ATR from the fill; exit at the official close",
        },
        "exploratory": {
            "E1": f"pinned PRIMARY 8-K 2.02 events accepted before 09:30 on the session or at 16:00 or later before "
                  f"it (others, about 15%, are not traded): at {_clock(E1_BAR)}, the residual move from the prior "
                  f"close of at least {E1_THRESHOLD:g} daily sigma (20 sessions): enter with it at {_clock(E1_BAR)}, "
                  "exit at the close",
            "M1": f"SPY: the sign of the move from the prior close to {_clock(E1_BAR)} (none when unchanged); trade "
                  f"SPY with it from {_clock(LAST_HALF_HOUR)} to the close (Gao, Han, Li & Zhou 2018); sessions "
                  "trading past 15:30",
            "R1": f"stocks with {R1_MIN_HISTORY} sessions of daily history: rank every one with a "
                  f"{_clock(R1_RANK_BAR)} close by its move from the prior close to {_clock(R1_RANK_BAR + 1)}; long "
                  f"the lowest fifth, short the highest fifth from {_clock(LAST_HALF_HOUR)} to the close (Baltussen, "
                  "Da & Soebhag); sessions trading past 15:30",
            "A1": f"no earnings event that session or the one before: an overnight residual of at least {A1_SIGMA:g} "
                  f"sigma ({A1_LOOKBACK} sessions) and a gap of at least {_pct(A1_MIN_GAP)}: short at "
                  f"{_clock(A1_BAR)}, exit at the close (Berkman, Koch, Tuttle & Zhang 2012)",
            "diagnostics": "costs doubled and zero; the whole-bar fill check; long and short sides; earnings sessions "
                           "and others; each year; exit reasons; stops inside the fill bar; bars through both C1 "
                           "levels; the unconditional fill rate of 1-3% gaps; exclusions per ticker",
        },
        "statistic": "mean of the session values; Newey-West with lag floor(4(n/100)^(2/9)); t(n-1); confirmatory "
                     f"intervals in the hold-out at the family-wise z {Z_FAMILY}, development and exploratory at 95%",
        "verdict": f"SUPPORTED when, at the {cost} cost, the mean and its family-wise lower bound are above 0, the "
                   "direction-skill book's family-wise lower bound is above 0, and coverage is met; "
                   "INSUFFICIENT_COVERAGE when coverage fails; else NOT_SUPPORTED",
        "coverage": f"at least {MIN_TRADES} trades, {MIN_ACTIVE_MONTHS} months with a trade, {MIN_STOCKS} stocks, no "
                    f"stock above {MAX_STOCK_SHARE:g} of trades",
        "labels": "ROBUST when the monthly book's lower bound (Newey-West lag 3) and the mean without its best month "
                  f"are both above 0, else FRAGILE; COST_SENSITIVE when the lower bound clears 0 at {cost} a side but "
                  f"not at {COST_PER_SIDE * 2e4:g} bps",
        "claims": CLAIMS,
        "qualification": "information only; a pass authorizes review, never trading (promotion NOT_AUTHORIZED, "
                         "broker_submission false); survivors only (today's coverage list)",
        "behavior_sha256": BEHAVIOR_SHA256,
    }


PROTOCOL = _protocol()
PROTOCOL_SHA256 = digest(PROTOCOL)


# ---- data ----------------------------------------------------------------------

def bar_index(minute: str) -> int:
    hh, mm = map(int, minute.split(":"))
    return (hh - 9) * 60 + mm - 30


def load_month(root, ticker: str, month: str) -> dict:
    """session -> bars (lists o, h, l, c, v indexed by minute from 09:30; None where a minute is missing). A bar
    with no trades (IBKR's placeholder, count 0) is a missing minute."""
    path = Path(root) / ticker / f"{month}.csv.gz"
    if not path.exists():
        return {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return days_from_rows(csv.DictReader(fh))


def days_from_rows(rows) -> dict:
    days = {}
    for r in rows:
        day = days.setdefault(r["session"], {k: [None] * 390 for k in "ohlcv"})
        i = bar_index(r["minute"])
        if 0 <= i < 390 and int(r["count"] or 0) > 0:
            for k, col in zip("ohlcv", ("open", "high", "low", "close", "volume")):
                day[k][i] = float(r[col])
    for day in days.values():
        n = max((i for i, v in enumerate(day["c"]) if v is not None), default=-1) + 1
        for k in "ohlcv":
            day[k] = day[k][:n]
    return days


def files_sha(root) -> str:
    """sha256 over every bar file's path and decompressed content under a root (the registered input hash; the
    gzip header's write time is left out, so rewriting the same bars keeps the hash)."""
    h = hashlib.sha256()
    for path in sorted(Path(root).glob("*/*.csv.gz")):
        content = gzip.decompress(path.read_bytes())
        h.update(f"{path.parent.name}/{path.name}".encode() + b"\0" + hashlib.sha256(content).digest())
    return h.hexdigest()


def missing_months(root, tickers: list[str], months: list[str]) -> list[str]:
    """Ticker-months without a finished fetch (no final manifest, or progress still partial)."""
    out = []
    for t in tickers:
        for m in months:
            folder = Path(root) / t
            if not (folder / f"{m}.json").exists() or (folder / f"{m}.partial.json").exists() \
                    or not (folder / f"{m}.csv.gz").exists():
                out.append(f"{t}/{m}")
    return out


def release_timing(acceptance_utc: str, reaction_session: str) -> str:
    stamp = datetime.fromisoformat(acceptance_utc).astimezone(ET).strftime("%Y-%m-%d %H:%M")
    if stamp[:10] == reaction_session and stamp[11:] < "09:30":
        return "PRE_OPEN"
    if stamp[:10] < reaction_session and stamp[11:] >= "16:00":
        return "AFTER_CLOSE"
    return "OTHER"


def earnings(path=None) -> dict:
    """ticker -> {"primary": {reaction_session: timing}, "any": set(reaction_sessions)} from the pinned 8-K data."""
    file = Path(path or Path(__file__).resolve().parents[2] / er.EARNINGS_ANNOUNCEMENTS_PATH)
    raw = file.read_bytes()
    if hashlib.sha256(raw).hexdigest() != er.EARNINGS_ANNOUNCEMENTS_SHA256:
        raise ValueError("EARNINGS_DATA_CHANGED")
    out = {}
    for e in json.loads(raw)["events"]:
        entry = out.setdefault(e["ticker"], {"primary": {}, "any": set()})
        entry["any"].add(e["reaction_session"])
        if e.get("role") == "PRIMARY":
            entry["primary"][e["reaction_session"]] = release_timing(e["acceptance_utc"], e["reaction_session"])
    return out


# ---- daily features ------------------------------------------------------------

def daily_features(rows: list[dict], spy_rows: list[dict]) -> dict:
    """session -> features known before the open (prior closes, dividend factor, beta, ATR, sigma, history),
    plus the session's own daily open and official close. SPY's daily rows are the session calendar: when the
    stock's two prior rows are not the two prior sessions, the prior close is stale and the session is marked."""
    spy = {r["date"]: r for r in spy_rows}
    order = {r["date"]: k for k, r in enumerate(spy_rows)}
    out, returns, spy_returns, overnight = {}, [], [], []
    for i in range(2, len(rows)):
        d, p, pp = rows[i], rows[i - 1], rows[i - 2]
        k = order.get(d["date"])
        stale = k is None or k < 2 or spy_rows[k - 1]["date"] != p["date"] or spy_rows[k - 2]["date"] != pp["date"]
        f, anomaly = dividend_factor(p, d)
        pc = p["close"] * f
        s, sp, spp = spy.get(d["date"]), spy.get(p["date"]), spy.get(pp["date"])
        if sp and spp:
            returns.append(_adj(p) / _adj(pp) - 1)
            spy_returns.append(_adj(sp) / _adj(spp) - 1)
        tr = [max(rows[k]["high"], rows[k - 1]["close"]) - min(rows[k]["low"], rows[k - 1]["close"])
              for k in range(max(1, i - 14), i)]
        beta = _beta(returns[-BETA_SESSIONS:], spy_returns[-BETA_SESSIONS:])
        features = {"prior_close_raw": p["close"], "prior_close": pc, "dividend_anomaly": anomaly, "stale": stale,
                    "prior_low": p["low"], "close_before_prior": pp["close"], "daily_open": d["open"],
                    "daily_close": d["close"], "beta": beta, "atr14": sum(tr) / len(tr) if len(tr) == 14 else None,
                    "sigma_d": _sd(returns[-20:]) if len(returns) >= 20 else None, "history": i,
                    "overnight": None, "overnight_spy": None, "overnight_sd": None}
        if s and sp:
            spy_factor, _ = dividend_factor(sp, s)
            features["overnight"] = d["open"] / pc - 1
            features["overnight_spy"] = s["open"] / (sp["close"] * spy_factor) - 1
            features["overnight_sd"] = _sd(overnight[-A1_LOOKBACK:]) if len(overnight) >= A1_LOOKBACK else None
            overnight.append(features["overnight"] - beta * features["overnight_spy"])
        out[d["date"]] = features
    return out


def _adj(row: dict) -> float:
    return row.get("adj_close") or row["close"]


def dividend_factor(prev: dict, day: dict) -> tuple[float, bool]:
    """(factor, anomaly): ADJUSTED_LAST/close of the prior session over that of the session."""
    try:
        r_prev = prev["adj_close"] / prev["close"]
        r_day = day["adj_close"] / day["close"]
        eps = 0.0002 + 0.01 / min(prev["adj_close"], day["adj_close"]) + 0.01 / min(prev["close"], day["close"])
    except (KeyError, TypeError, ZeroDivisionError):
        return 1.0, False
    f = r_prev / r_day
    if f >= 1 - eps:
        return 1.0, f > 1 + eps
    return f, False


def _sd(values: list[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    m = sum(values) / n
    return math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1))


def _beta(r: list[float], m: list[float]) -> float:
    n = len(r)
    if n < 20:
        return 1.0
    mr, mm = sum(r) / n, sum(m) / n
    var = sum((x - mm) ** 2 for x in m)
    if var <= 0:
        return 1.0
    b = sum((x - mr) * (y - mm) for x, y in zip(r, m)) / var
    return min(max(b, BETA_RANGE[0]), BETA_RANGE[1])


# ---- fills ---------------------------------------------------------------------

def stop_entry_fill(day, side: int, level: float, b: int) -> float | None:
    o, h, l = day["o"][b], day["h"][b], day["l"][b]
    if o is None:
        return None
    if side > 0 and h > level:
        return max(level, o)
    if side < 0 and l < level:
        return min(level, o)
    return None


def bar_path(o: float, h: float, l: float, c: float) -> list[float]:
    """The assumed order of prices inside a bar: open, low, high, close for an up bar (close at or above the open);
    open, high, low, close for a down bar."""
    return [o, l, h, c] if c >= o else [o, h, l, c]


def after_fill(day, b: int, fill_side: int, level: float) -> list[float]:
    """The prices along bar b's path from the moment a stop order at `level` filled (fill_side +1: bought on the
    way up through it, -1: sold on the way down); from the open when the bar opened through it."""
    path = bar_path(day["o"][b], day["h"][b], day["l"][b], day["c"][b])
    if (fill_side > 0 and path[0] >= level) or (fill_side < 0 and path[0] <= level):
        return path
    for k in range(1, 4):
        if (fill_side > 0 and path[k] > level) or (fill_side < 0 and path[k] < level):
            return [level] + path[k:]
    return [level]


def simulate_exit(day, side: int, entry_bar: int, stop: float | None, target: float | None, last_bar: int,
                  *, entry_by_stop: bool, close_price: float | None = None, entry: float | None = None,
                  fill_side: int | None = None, fill_bar: str = "path") -> dict:
    """Walk the bars from the entry bar: the stop first, then the target, else the forced exit (the official close
    when close_price is given, else the last bar's close). A stop entry's fill bar is checked for the stop only,
    along the bar's path after the fill ("path"), or over the whole bar ("worst", a diagnostic)."""
    n = len(day["c"])
    for b in range(entry_bar, min(last_bar, n - 1) + 1):
        o, h, l = day["o"][b], day["h"][b], day["l"][b]
        if o is None:
            continue                                  # carried through a missing minute
        later = b > entry_bar
        if entry_by_stop and not later:
            rest = [l, h] if fill_bar == "worst" else after_fill(day, b, fill_side or side, entry)
            if stop is not None and ((side > 0 and min(rest) < stop) or (side < 0 and max(rest) > stop)):
                return {"exit": stop, "exit_bar": b, "reason": "STOP", "at_open": False}
            continue
        if stop is not None and ((side > 0 and l < stop) or (side < 0 and h > stop)):
            gapped = later and (o <= stop if side > 0 else o >= stop)
            return {"exit": o if gapped else stop, "exit_bar": b, "reason": "STOP", "at_open": gapped}
        if target is not None:
            if (side > 0 and h > target) or (side < 0 and l < target):
                gapped = later and (o >= target if side > 0 else o <= target)
                return {"exit": o if gapped else target, "exit_bar": b, "reason": "TARGET", "at_open": gapped}
    if close_price is not None:
        return {"exit": close_price, "exit_bar": None, "reason": "CLOSE", "at_open": False}
    b = min(last_bar, n - 1)
    while b > entry_bar and day["c"][b] is None:
        b -= 1
    return {"exit": day["c"][b], "exit_bar": b, "reason": "TIME", "at_open": False}


def ssr_active(features: dict, day, through_bar: int) -> bool:
    pc = features["prior_close_raw"]
    if features["prior_low"] <= (1 - SSR_DROP) * features["close_before_prior"]:
        return True
    if day["o"][0] is not None and day["o"][0] <= SSR_PREOPEN * pc:
        return True
    return any(x is not None and x <= (1 - SSR_DROP) * pc for x in day["l"][:through_bar + 1])


# ---- strategies (each returns a trade, {"skip": reason}, or None) ----------------

def _trade(strategy, side, entry_bar, entry, stop, target, last_bar, *, by_stop, close=None, both=False):
    return {"strategy": strategy, "side": side, "entry_bar": entry_bar, "entry": entry, "stop": stop,
            "target": target, "last_bar": last_bar, "by_stop": by_stop, "close": close,
            "risk": abs(entry - stop) if stop is not None else None, "both": both}


def _complete(day, keys: str, bars) -> bool:
    return all(b < len(day[k]) and day[k][b] is not None for k in keys for b in bars)


def g1(day, f, gap: float) -> dict | None:
    if abs(gap) < G1_GAP:
        return None
    side = -1 if gap < 0 else 1
    pc, o0 = f["prior_close"], day["o"][0]
    for n in range(G1_SIGNAL[0], G1_SIGNAL[1] + 1):
        if not _complete(day, "ohlc", range(n + 1)):
            return None
        counter = [i for i in range(n) if (day["c"][i] > day["o"][i] if side < 0 else day["c"][i] < day["o"][i])]
        if not counter:
            continue
        i0 = counter[0]
        if side < 0:
            extreme = max(day["h"][i0:n])
            failed = day["h"][n] <= extreme and day["c"][n] < day["l"][n - 1]
            retraced = (extreme - o0) >= G1_RETRACE * (pc - o0)
        else:
            extreme = min(day["l"][i0:n])
            failed = day["l"][n] >= extreme and day["c"][n] > day["h"][n - 1]
            retraced = (o0 - extreme) >= G1_RETRACE * (o0 - pc)
        if not failed or retraced:
            continue
        if side < 0 and ssr_active(f, day, n):
            return {"skip": "RULE_201"}
        if not _complete(day, "o", [n + 1]):
            return None
        entry = day["o"][n + 1]
        if (side < 0 and entry >= extreme) or (side > 0 and entry <= extreme):
            return {"skip": "ENTRY_BEYOND_STOP"}
        return _trade("G1", side, n + 1, entry, extreme, entry + side * G1_TARGET_R * abs(entry - extreme),
                      EXIT_BAR, by_stop=False)
    return None


def five_minute(day, k: int) -> dict | None:
    idx = range(5 * k, 5 * k + 5)
    if not _complete(day, "ohlc", idx):
        return None
    return {"o": day["o"][5 * k], "h": max(day["h"][i] for i in idx), "l": min(day["l"][i] for i in idx),
            "c": day["c"][5 * k + 4]}


def gap_filled(day, pc: float, gap: float, through_bar: int) -> bool:
    """Price traded strictly through the prior close (the test a target order at it fills on)."""
    seen = [x for x in (day["l"] if gap > 0 else day["h"])[:through_bar + 1] if x is not None]
    return any(x < pc for x in seen) if gap > 0 else any(x > pc for x in seen)


def g2(day, f, gap: float) -> dict | None:
    if not G2_GAP[0] <= abs(gap) < G2_GAP[1]:
        return None
    side = -1 if gap > 0 else 1          # fade the gap
    pc = f["prior_close"]
    for k in range(G2_FIVE_MINUTE[0], G2_FIVE_MINUTE[1] + 1):
        bar, prev = five_minute(day, k), five_minute(day, k - 1)
        if bar is None or prev is None or gap_filled(day, pc, gap, 5 * k + 4):
            return None
        if side < 0:
            reversal = bar["c"] < bar["o"] and bar["c"] < prev["l"]
            stop, reward, risk = bar["h"], bar["c"] - pc, bar["h"] - bar["c"]
        else:
            reversal = bar["c"] > bar["o"] and bar["c"] > prev["h"]
            stop, reward, risk = bar["l"], pc - bar["c"], bar["c"] - bar["l"]
        if not reversal or reward < G2_MIN_RR * risk:
            continue
        if side < 0 and ssr_active(f, day, 5 * k + 4):
            return {"skip": "RULE_201"}
        b = 5 * k + 5
        if b >= len(day["o"]) or day["o"][b] is None:
            return None
        entry = day["o"][b]
        if (side < 0 and entry >= stop) or (side > 0 and entry <= stop):
            return {"skip": "ENTRY_BEYOND_STOP"}
        if (side < 0 and entry <= pc) or (side > 0 and entry >= pc):
            return {"skip": "ENTRY_BEYOND_TARGET"}
        return _trade("G2", side, b, entry, stop, pc, EXIT_BAR, by_stop=False)
    return None


def opening_range(day) -> tuple[float, float] | None:
    if not _complete(day, "ohlc", range(OR_BARS)):
        return None
    return max(day["h"][:OR_BARS]), min(day["l"][:OR_BARS])


def _breakout(strategy, day, f, side, level, stop_of, last_entry, last_bar, close=None) -> dict | None:
    for b in range(OR_BARS, min(last_entry, len(day["o"]) - 1) + 1):
        fill = stop_entry_fill(day, side, level, b)
        if fill is None:
            continue
        if side < 0 and ssr_active(f, day, b - 1):
            return {"skip": "RULE_201"}
        return _trade(strategy, side, b, fill, stop_of(fill), None, last_bar, by_stop=True, close=close)
    return None


def b1(day, f, gap: float) -> dict | None:
    if abs(gap) < B1_GAP:
        return None
    rng = opening_range(day)
    if rng is None:
        return None
    high, low = rng
    side = 1 if gap > 0 else -1
    level, stop = (high, low) if side > 0 else (low, high)
    return _breakout("B1", day, f, side, level, lambda fill: stop, ENTRY_LAST, EXIT_BAR)


def relative_ranges(day, f, norms: list[float | None]) -> list[float | None]:
    pc = f["prior_close_raw"]
    out = []
    for b in range(min(EXIT_BAR + 1, len(day["c"]))):
        h, l, norm = day["h"][b], day["l"][b], norms[b]
        out.append(math.log(h / l) / norm if h is not None and l is not None and norm and norm * pc >= C1_RESOLUTION
                   else None)
    return out


def c1(day, f, norms: list[float | None] | None) -> dict | None:
    if norms is None:
        return None
    nu = relative_ranges(day, f, norms)
    n = len(day["c"])
    blocked = False
    j = C1_RUN_END[0]
    while j <= min(C1_RUN_END[1], len(nu) - 1):
        run = nu[j - C1_K + 1:j + 1]
        if None in run or max(run) > C1_X or any(run[i] <= run[i + 1] for i in range(C1_K - 1)):
            j += 1
            continue
        high, low = max(day["h"][j - C1_K + 1:j + 1]), min(day["l"][j - C1_K + 1:j + 1])
        last = min(j + C1_ORDER_LIFE, ENTRY_LAST, n - 1)
        for b in range(j + 1, last + 1):
            o, h, l = day["o"][b], day["h"][b], day["l"][b]
            if o is None:
                continue
            short_ok = not (l < low and ssr_active(f, day, b - 1))     # Rule 201 bars the sell stop only
            blocked = blocked or not short_ok
            if not (h > high or (short_ok and l < low)):
                continue
            side = None                  # the first level the bar's path goes through fills; the other is cancelled
            for x in bar_path(o, h, l, day["c"][b]):
                if x > high or (short_ok and x < low):
                    side = 1 if x > high else -1
                    break
            fill = max(high, o) if side > 0 else min(low, o)
            return _trade("C1", side, b, fill, low if side > 0 else high, None, EXIT_BAR, by_stop=True,
                          both=h > high and l < low)
        j = last + 1                     # the orders expired: look for a later run
    return {"skip": "RULE_201"} if blocked else None


def x1_candidate(day, f) -> dict | None:
    """The trade (or skip) of a stock that passed the filters; None when no breakout triggers."""
    rng = opening_range(day)
    if rng is None:
        return None
    high, low = rng
    side = 1 if day["c"][OR_BARS - 1] > day["o"][0] else -1 if day["c"][OR_BARS - 1] < day["o"][0] else 0
    if side == 0:
        return None
    distance = X1_ATR_STOP * f["atr14"]
    last = len(day["o"]) - 1
    return _breakout("X1", day, f, side, high if side > 0 else low, lambda fill: fill - side * distance, last, last,
                     close=f["daily_close"])


def e1(day, f, timing: str | None, spy_day, spy_f) -> dict | None:
    if timing not in ("PRE_OPEN", "AFTER_CLOSE") or f["sigma_d"] is None:
        return None
    if not _complete(day, "c", [E1_BAR - 1]) or not _complete(day, "o", [E1_BAR]) \
            or not _complete(spy_day, "c", [E1_BAR - 1]):
        return None
    e = (day["c"][E1_BAR - 1] / f["prior_close"] - 1) - f["beta"] * (spy_day["c"][E1_BAR - 1] / spy_f["prior_close"] - 1)
    if abs(e) < E1_THRESHOLD * f["sigma_d"]:
        return None
    side = 1 if e > 0 else -1
    if side < 0 and ssr_active(f, day, E1_BAR - 1):
        return {"skip": "RULE_201"}
    return _trade("E1", side, E1_BAR, day["o"][E1_BAR], None, None, len(day["o"]) - 1, by_stop=False,
                  close=f["daily_close"])


def a1(day, f, any_event: set, prev_session: str | None, session: str) -> dict | None:
    if session in any_event or (prev_session and prev_session in any_event):
        return None
    if f["overnight_sd"] is None or not _complete(day, "o", [A1_BAR]):
        return None
    resid = f["overnight"] - f["beta"] * f["overnight_spy"]
    if resid < A1_SIGMA * f["overnight_sd"] or f["overnight"] < A1_MIN_GAP:
        return None
    if ssr_active(f, day, A1_BAR - 1):
        return {"skip": "RULE_201"}
    return _trade("A1", -1, A1_BAR, day["o"][A1_BAR], None, None, len(day["o"]) - 1, by_stop=False,
                  close=f["daily_close"])


def r1_trade(day, f) -> dict | None:
    """The long leg at 15:30 (the short leg is its mirror), for a session that trades past 15:30."""
    if (len(day["c"]) <= LAST_HALF_HOUR or f["history"] < R1_MIN_HISTORY or day["c"][R1_RANK_BAR] is None
            or day["o"][LAST_HALF_HOUR] is None):
        return None
    return {**_trade("R1", 1, LAST_HALF_HOUR, day["o"][LAST_HALF_HOUR], None, None, len(day["o"]) - 1,
                     by_stop=False, close=f["daily_close"]),
            "rank_move": day["c"][R1_RANK_BAR] / f["prior_close"] - 1}


# ---- scoring -------------------------------------------------------------------

def _spy_price(spy_day, exit_: dict, spy_close: float) -> float | None:
    b = exit_["exit_bar"]
    if b is None:
        return spy_close
    if b >= len(spy_day["c"]) or spy_day["o"][b] is None:
        return None
    if exit_["reason"] == "TIME":
        return spy_day["c"][b]
    return spy_day["o"][b] if exit_["at_open"] else (spy_day["o"][b] + spy_day["c"][b]) / 2


def score(trade: dict, day, spy_day, spy_close: float, beta: float, scale: float, *, side: int | None = None,
          fill_bar: str = "path") -> dict | None:
    """Exit, residual return and reward for one side of a trade (the mirror passes the other side)."""
    side = trade["side"] if side is None else side
    entry, stop, target = trade["entry"], trade["stop"], trade["target"]
    if side != trade["side"]:                 # the mirror: same fill, brackets reflected through the entry
        stop = None if stop is None else 2 * entry - stop
        target = None if target is None else 2 * entry - target
    exit_ = simulate_exit(day, side, trade["entry_bar"], stop, target, trade["last_bar"],
                          entry_by_stop=trade["by_stop"], close_price=trade["close"], entry=entry,
                          fill_side=trade["side"], fill_bar=fill_bar)
    b = trade["entry_bar"]
    if b >= len(spy_day["o"]) or spy_day["o"][b] is None or spy_day["c"][b] is None:
        return None
    at_open = not trade["by_stop"] or entry == day["o"][b]
    s_in = spy_day["o"][b] if at_open else (spy_day["o"][b] + spy_day["c"][b]) / 2
    s_out = _spy_price(spy_day, exit_, spy_close)
    if s_out is None or exit_["exit"] is None:
        return None
    gross = side * (exit_["exit"] / entry - 1)
    resid = gross - side * beta * (s_out / s_in - 1)
    return {"side": side, "exit": exit_["exit"], "reason": exit_["reason"], "gross": gross, "residual": resid,
            "reward": (resid - 2 * COST_PER_SIDE) / scale, "reward_2x": (resid - 4 * COST_PER_SIDE) / scale,
            "reward_gross": resid / scale,
            "r_gross": side * (exit_["exit"] - entry) / trade["risk"] if trade["risk"] else None,
            "stop_in_fill_bar": trade["by_stop"] and exit_["reason"] == "STOP" and exit_["exit_bar"] == b}


def day_square_returns(day) -> list[float | None]:
    """Prefix sums of squared 1-minute log returns (close to previous available close; None before the first)."""
    out, total, prev = [], 0.0, None
    for c in day["c"]:
        if c is not None:
            if prev is not None:
                total += math.log(c / prev) ** 2
            prev = c
        out.append(total if prev is not None else None)
    return out


def window_var(day, prefix, first: int, last: int | None) -> float | None:
    """Realized variance over bars first..last: the first bar's open to close, then close to close."""
    n = len(day["c"])
    last = n - 1 if last is None else last
    if first >= n or last >= n or day["o"][first] is None or day["c"][first] is None or prefix[last] is None:
        return None
    return math.log(day["c"][first] / day["o"][first]) ** 2 + prefix[last] - prefix[first]


def window_minutes(day, first: int, last: int | None) -> int:
    return ((len(day["c"]) - 1) if last is None else last) - first + 1


def window_scale(history: deque, minutes: int) -> float | None:
    """Volatility over a window of `minutes`, from the trailing sessions' realized variance per minute."""
    if len(history) < NORM_MIN:
        return None
    return max(math.sqrt(sum(history) / len(history) * minutes), MIN_ANNUAL_VOL * math.sqrt(minutes / 390 / 252))


# ---- the run -------------------------------------------------------------------

def months_between(first: str, last: str) -> list[str]:
    out, (year, m) = [], map(int, first.split("-"))
    while f"{year:04d}-{m:02d}" <= last:
        out.append(f"{year:04d}-{m:02d}")
        year, m = (year + 1, 1) if m == 12 else (year, m + 1)
    return out


def previous_month(month: str) -> str:
    year, m = map(int, month.split("-"))
    return f"{year - 1:04d}-12" if m == 1 else f"{year:04d}-{m - 1:02d}"


class History:
    """Trailing per-stock state, updated after each session (so a session sees only earlier ones)."""

    def __init__(self):
        self.ranges = deque(maxlen=NORM_SESSIONS)
        self.volume5 = deque(maxlen=RV_SESSIONS)
        self.var = {w: deque(maxlen=NORM_SESSIONS) for w in WINDOWS}

    def norms(self) -> list[float | None] | None:
        if len(self.ranges) < NORM_MIN:
            return None
        out = []
        for b in range(EXIT_BAR + 1):
            vals = [r[b] for r in self.ranges if r[b] is not None]
            out.append(sum(vals) / len(vals) if len(vals) >= NORM_MIN else None)
        return out

    def rv5(self, day) -> float | None:
        if len(self.volume5) < RV_SESSIONS or not _complete(day, "v", range(OR_BARS)) or sum(self.volume5) <= 0:
            return None
        return sum(day["v"][:OR_BARS]) / (sum(self.volume5) / len(self.volume5))

    def scale(self, strategy: str, day) -> float | None:
        first, last = WINDOWS[WINDOW_OF[strategy]]
        return window_scale(self.var[WINDOW_OF[strategy]], window_minutes(day, first, last))

    def update(self, day) -> None:
        self.ranges.append([math.log(day["h"][b] / day["l"][b]) if _complete(day, "hl", [b]) else None
                            for b in range(EXIT_BAR + 1)])
        if _complete(day, "v", range(OR_BARS)):
            self.volume5.append(sum(day["v"][:OR_BARS]))
        prefix = day_square_returns(day)
        for w, (first, last) in WINDOWS.items():
            v = window_var(day, prefix, first, last)
            if v is not None:
                self.var[w].append(v / window_minutes(day, first, last))


def replay(bars_for, prices: dict, events: dict, months: tuple[str, str], tickers: list[str], *,
           corporate_actions: dict | None = None) -> dict:
    """bars_for(ticker, month) -> {session: day}. Returns scored trades, per-session denominators and counts. SPY's
    sessions are the grid: every ticker-session on it is eligible or excluded with a reason."""
    actions = CORPORATE_ACTIONS if corporate_actions is None else corporate_actions
    spy_rows = prices["SPY"]
    loaded = [previous_month(months[0])] + months_between(*months)
    spy_days = {}
    for month in loaded:
        spy_days.update(bars_for("SPY", month))
    spy_feat = daily_features(spy_rows, spy_rows)
    calendar = [r["date"] for r in spy_rows]
    previous = dict(zip(calendar[1:], calendar))
    grid = sorted({*spy_days, *(r["date"] for r in spy_rows)} & {s for s in calendar if months[0] <= s[:7] <= months[1]})
    sessions, eligible = set(grid), defaultdict(int)
    trades, skips, excluded, counts = [], defaultdict(int), defaultdict(int), defaultdict(int)
    by_ticker = defaultdict(lambda: defaultdict(int))
    x1_pool, r1_pool = defaultdict(list), defaultdict(list)
    for ticker in tickers:
        feats = daily_features(prices.get(ticker, []), spy_rows)
        ev = events.get(ticker, {"primary": {}, "any": set()})
        hist = History()
        seen = set()
        for month in loaded:
            days = bars_for(ticker, month)
            for session in sorted(days):
                day = days[session]
                if months[0] <= session[:7] <= months[1]:
                    seen.add(session)
                    sessions.add(session)
                    out = _session(ticker, session, day, feats.get(session), ev, previous.get(session), hist,
                                   spy_days.get(session), spy_feat.get(session), actions.get(ticker, ()))
                    if out["reason"]:
                        excluded[out["reason"]] += 1
                        by_ticker[ticker][out["reason"]] += 1
                    else:
                        eligible[session] += 1
                    trades.extend(out["trades"])
                    for key in out["skips"]:
                        skips[key] += 1
                    for key in out["counts"]:
                        counts[key] += 1
                    if out.get("x1"):
                        x1_pool[session].append(out["x1"])
                    if out.get("r1"):
                        r1_pool[session].append(out["r1"])
                hist.update(day)
        for session in grid:                           # sessions with no minute bars at all
            if session not in seen:
                reason = ("PRE_LISTING" if session < LISTED.get(ticker, "") else
                          "NO_DAILY_BARS" if session not in feats else "NO_MINUTE_BARS")
                excluded[reason] += 1
                by_ticker[ticker][reason] += 1
    for session, pool in sorted(x1_pool.items()):     # stocks in play: the highest first-5-minute volumes
        for c in sorted(pool, key=lambda c: (-c["rv5"], c["ticker"]))[:X1_TOP]:
            if c.get("trade"):
                trades.append(c["trade"])
            elif c.get("skip"):
                skips[f"X1:{c['skip']}"] += 1
    for session, pool in sorted(r1_pool.items()):     # end-of-day reversal: the extreme fifths of all ranked
        pool = sorted(pool, key=lambda r: (r["rank_move"], r["ticker"]))
        k = len(pool) // 5
        for r in pool[:k] if k else []:
            if r["long"]:
                trades.append(r["long"])
            else:
                skips["R1:UNSCORABLE"] += 1
        for r in pool[-k:] if k else []:
            if r["short_blocked"]:
                skips["R1:RULE_201"] += 1
            elif r["short"]:
                trades.append(r["short"])
            else:
                skips["R1:UNSCORABLE"] += 1
    return {"trades": trades + _m1(spy_days, spy_feat, months), "eligible": dict(eligible),
            "sessions": sorted(sessions), "skips": dict(skips), "excluded": dict(excluded), "counts": dict(counts),
            "excluded_by_ticker": {t: dict(v) for t, v in sorted(by_ticker.items())}}


def _session(ticker, session, day, f, ev, prev_session, hist, spy_day, spy_f, actions=()) -> dict:
    out = {"reason": None, "trades": [], "skips": [], "counts": []}
    if session < LISTED.get(ticker, ""):
        out["reason"] = "PRE_LISTING"
    elif f is None:
        out["reason"] = "NO_DAILY_BARS"
    elif f["stale"]:
        out["reason"] = "STALE_PRIOR_CLOSE"
    elif session in actions:
        out["reason"] = "CORPORATE_ACTION"
    elif f["prior_close_raw"] < PRICE_FLOOR:
        out["reason"] = "PRICE_FLOOR"
    elif not day["o"] or day["o"][0] is None:
        out["reason"] = "MISSING_OPEN_BAR"
    elif abs(day["o"][0] / f["daily_open"] - 1) > OPEN_MATCH:
        out["reason"] = "PRICE_MISMATCH"
    elif len(hist.ranges) < NORM_MIN:
        out["reason"] = "NO_HISTORY"
    elif spy_day is None or spy_f is None:
        out["reason"] = "NO_SPY"
    if out["reason"]:
        return out
    if f["dividend_anomaly"]:
        out["counts"].append("DIVIDEND_ANOMALY")
    gap = day["o"][0] / f["prior_close"] - 1
    n = len(day["c"])
    base = {"ticker": ticker, "session": session, "month": session[:7], "gap": gap, "earnings": session in ev["any"]}
    if G2_GAP[0] <= abs(gap) < G2_GAP[1]:              # Barak's "most 1-3% gaps fill": the unconditional rate
        out["counts"].append("G2_GAP")
        if gap_filled(day, f["prior_close"], gap, EXIT_BAR):
            out["counts"].append("G2_GAP_FILLED_BY_1030")
        if gap_filled(day, f["prior_close"], gap, n - 1):
            out["counts"].append("G2_GAP_FILLED_BY_CLOSE")
    candidates = [g1(day, f, gap), g2(day, f, gap), b1(day, f, gap), c1(day, f, hist.norms()),
                  e1(day, f, ev["primary"].get(session), spy_day, spy_f), a1(day, f, ev["any"], prev_session, session)]
    for name, t in zip(("G1", "G2", "B1", "C1", "E1", "A1"), candidates):
        if t is None:
            continue
        if "skip" in t:
            out["skips"].append(f"{name}:{t['skip']}")
            continue
        scored = _score_pair(t, day, spy_day, f, spy_f, hist, base)
        if scored:
            out["trades"].append(scored)
        else:
            out["skips"].append(f"{name}:UNSCORABLE")
    rv5 = hist.rv5(day)
    if rv5 is not None and rv5 >= X1_RV_MIN and f["atr14"] is not None and f["atr14"] >= X1_ATR_MIN:
        t = x1_candidate(day, f)
        candidate = {"ticker": ticker, "rv5": rv5}
        if t is not None and "skip" in t:
            candidate["skip"] = t["skip"]
        elif t is not None:
            candidate["trade"] = _score_pair(t, day, spy_day, f, spy_f, hist, base)
            if candidate["trade"] is None:
                candidate["skip"] = "UNSCORABLE"
        out["x1"] = candidate
    if f["history"] >= R1_MIN_HISTORY and n > LAST_HALF_HOUR and day["c"][R1_RANK_BAR] is not None:
        row = {"ticker": ticker, "rank_move": day["c"][R1_RANK_BAR] / f["prior_close"] - 1, "long": None,
               "short": None, "short_blocked": ssr_active(f, day, LAST_HALF_HOUR - 1)}
        t = r1_trade(day, f)
        long_ = _score_pair(t, day, spy_day, f, spy_f, hist, base) if t else None
        if long_ is not None:
            row["long"] = long_
            row["short"] = {**long_, "side": -1, "reward": long_["mirror_reward"], "mirror_reward": long_["reward"],
                            "reward_2x": long_["mirror_reward_2x"], "reward_gross": long_["mirror_reward_gross"],
                            "net_bps": long_["mirror_net_bps"], "gross_bps": long_["mirror_gross_bps"],
                            "skill": -long_["skill"], "reward_worst_fill": long_["mirror_reward"],
                            "skill_worst_fill": -long_["skill"]}
        out["r1"] = row
    return out


def _score_pair(t, day, spy_day, f, spy_f, hist, base) -> dict | None:
    scale = hist.scale(t["strategy"], day)
    if scale is None:
        return None
    own = score(t, day, spy_day, spy_f["daily_close"], f["beta"], scale)
    mirror = score(t, day, spy_day, spy_f["daily_close"], f["beta"], scale, side=-t["side"])
    if own is None or mirror is None:
        return None
    worst = (score(t, day, spy_day, spy_f["daily_close"], f["beta"], scale, fill_bar="worst")["reward"]
             if t["by_stop"] else own["reward"])
    return {"reward_worst_fill": worst, "skill_worst_fill": (worst - mirror["reward"]) / 2, **base, "strategy": t["strategy"], "side": t["side"], "entry_bar": t["entry_bar"],
            "reason": own["reason"], "reward": own["reward"], "reward_2x": own["reward_2x"],
            "reward_gross": own["reward_gross"], "net_bps": (own["residual"] - 2 * COST_PER_SIDE) * 1e4,
            "gross_bps": own["residual"] * 1e4, "r_gross": own["r_gross"],
            "control": (own["reward"] + mirror["reward"]) / 2, "skill": (own["reward"] - mirror["reward"]) / 2,
            "mirror_reward": mirror["reward"], "mirror_reward_2x": mirror["reward_2x"],
            "mirror_reward_gross": mirror["reward_gross"],
            "mirror_net_bps": (mirror["residual"] - 2 * COST_PER_SIDE) * 1e4, "mirror_gross_bps": mirror["residual"] * 1e4,
            "both": t["both"], "stop_in_fill_bar": own["stop_in_fill_bar"]}


def _m1(spy_days, spy_feat, months) -> list[dict]:
    out, var = [], deque(maxlen=NORM_SESSIONS)
    first, _ = WINDOWS["LAST_HALF_HOUR"]
    for session in sorted(spy_days):
        day, f = spy_days[session], spy_feat.get(session)
        if len(day["c"]) <= first or f is None:
            continue                                  # half days end before 15:30
        scale = window_scale(var, window_minutes(day, first, None))
        move = day["c"][E1_BAR - 1] / f["prior_close"] - 1 if day["c"][E1_BAR - 1] is not None else 0.0
        if months[0] <= session[:7] <= months[1] and scale and move and day["o"][first] is not None:
            side = 1 if move > 0 else -1
            gross = side * (f["daily_close"] / day["o"][first] - 1)
            reward = (gross - 2 * COST_PER_SIDE) / scale
            out.append({"ticker": "SPY", "session": session, "month": session[:7], "gap": None, "earnings": False,
                        "strategy": "M1", "side": side, "entry_bar": first, "reason": "CLOSE", "reward": reward,
                        "reward_2x": (gross - 4 * COST_PER_SIDE) / scale, "reward_gross": gross / scale,
                        "net_bps": (gross - 2 * COST_PER_SIDE) * 1e4, "gross_bps": gross * 1e4, "r_gross": None,
                        "control": -2 * COST_PER_SIDE / scale, "skill": gross / scale, "reward_worst_fill": reward,
                        "skill_worst_fill": gross / scale, "both": False, "stop_in_fill_bar": False})
        v = window_var(day, day_square_returns(day), first, None)
        if v is not None:
            var.append(v / window_minutes(day, first, None))
    return out


# ---- evaluation ----------------------------------------------------------------

def nw_lag(n: int) -> int:
    return int(4 * (n / 100) ** (2 / 9))


def interval(values: list[float], z: float, lag: int | None = None) -> dict:
    n = len(values)
    if n < 3:
        return {"n": n, "mean": None, "se": None, "lower": None, "upper": None}
    lag = nw_lag(n) if lag is None else lag
    m = sum(values) / n
    se = inference.newey_west_se(values, lag)
    t = inference.t_quantile(n - 1, z)
    return {"n": n, "mean": m, "se": se, "lower": m - t * se, "upper": m + t * se, "lag": lag,
            "detectable_difference_80pct": (t + inference.Z_80) * se if se > 0 else None}


def book(trades: list[dict], sessions: list[str], denominators: dict, field: str) -> list[float]:
    by = defaultdict(float)
    for t in trades:
        by[t["session"]] += t[field]
    return [by[s] / denominators[s] if denominators.get(s) else 0.0 for s in sessions]


def monthly(values: list[float], sessions: list[str]) -> list[float]:
    by = defaultdict(float)
    for v, s in zip(values, sessions):
        by[s[:7]] += v
    return [by[k] for k in sorted(by)]


def wilson(hits: int, n: int, z: float) -> tuple[float, float] | None:
    if not n:
        return None
    p = hits / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - spread) / (1 + z * z / n), (centre + spread) / (1 + z * z / n)


def coverage(trades: list[dict], months: tuple[str, str]) -> dict:
    stocks = defaultdict(int)
    for t in trades:
        stocks[t["ticker"]] += 1
    share = max(stocks.values()) / len(trades) if trades else None
    active = len({t["month"] for t in trades})
    met = (len(trades) >= MIN_TRADES and active >= MIN_ACTIVE_MONTHS and len(stocks) >= MIN_STOCKS
           and share <= MAX_STOCK_SHARE)
    return {"trades": len(trades), "active_months": active, "months": len(months_between(*months)),
            "stocks": len(stocks), "max_stock_share": share, "met": met}


def _above_zero(x) -> bool:
    return x is not None and x > 0


def evaluate(run: dict, months: tuple[str, str], *, holdout: bool) -> dict:
    sessions, denominators = run["sessions"], run["eligible"]
    out = {}
    for name in (*CONFIRMATORY, *EXPLORATORY):
        trades = [t for t in run["trades"] if t["strategy"] == name]
        denom = {s: 1 for s in sessions} if name == "M1" else denominators
        z = Z_FAMILY if holdout and name in CONFIRMATORY else Z_95
        main = book(trades, sessions, denom, "reward")
        result = {"role": "confirmatory" if name in CONFIRMATORY else "exploratory", "z": z,
                  "coverage": coverage(trades, months), "reward": interval(main, z),
                  "direction_skill": interval(book(trades, sessions, denom, "skill"), z),
                  "random_twin": interval(book(trades, sessions, denom, "control"), z),
                  "costs_doubled": interval(book(trades, sessions, denom, "reward_2x"), z),
                  "zero_cost": interval(book(trades, sessions, denom, "reward_gross"), z),
                  "worst_fill_bar": {"reward": interval(book(trades, sessions, denom, "reward_worst_fill"), z),
                                     "direction_skill": interval(book(trades, sessions, denom, "skill_worst_fill"), z)},
                  "per_trade": _summary(trades, minimum=1),
                  "exits": _count(t["reason"] for t in trades),
                  "stops_in_fill_bar": sum(1 for t in trades if t["stop_in_fill_bar"]),
                  "through_both_levels": sum(1 for t in trades if t["both"]),
                  "sides": {k: _summary([t for t in trades if t["side"] == s]) for k, s in (("long", 1), ("short", -1))},
                  "earnings": {k: _summary([t for t in trades if t["earnings"] is v])
                               for k, v in (("earnings", True), ("other", False))},
                  "by_year": {y: _summary([t for t in trades if t["session"][:4] == y])
                              for y in sorted({t["session"][:4] for t in trades})}}
        values = monthly(main, sessions)
        mon = interval(values, z, lag=3)
        best = max(range(len(values)), key=values.__getitem__) if values else None
        without = _mean([v for i, v in enumerate(values) if i != best])
        result["monthly"] = {**mon, "mean_without_best_month": without}
        passes = _above_zero(result["reward"]["lower"])
        result["labels"] = {
            "robustness": "ROBUST" if _above_zero(mon["lower"]) and _above_zero(without) else "FRAGILE",
            "cost_sensitive": (passes and not _above_zero(result["costs_doubled"]["lower"])) if passes else None}
        result["claims"] = _claims(name, trades, run, z)
        if holdout and name in CONFIRMATORY:
            r, s = result["reward"], result["direction_skill"]
            result["verdict"] = ("INSUFFICIENT_COVERAGE" if not result["coverage"]["met"] else
                                 "SUPPORTED" if _above_zero(r["mean"]) and _above_zero(r["lower"])
                                 and _above_zero(s["lower"]) else "NOT_SUPPORTED")
        out[name] = result
    return out


def _claims(name, trades, run, z) -> dict:
    n = len(trades)
    r = [t["r_gross"] for t in trades if t["r_gross"] is not None]
    if name == "G1" and n:
        hits = sum(1 for t in trades if t["reason"] == "TARGET")
        return {"hit_rate": hits / n, "hit_rate_interval": wilson(hits, n, z), "claimed_hit_rate": CLAIMS["G1_hit_rate"],
                "mean_gross_R": _mean(r), "claimed_gross_R": CLAIMS["G1_gross_R"]}
    if name == "G2":
        gaps, filled = run["counts"].get("G2_GAP", 0), run["counts"].get("G2_GAP_FILLED_BY_1030", 0)
        hits = sum(1 for t in trades if t["reason"] == "TARGET")
        return {"gaps": gaps, "filled_by_1030": filled / gaps if gaps else None,
                "filled_by_1030_interval": wilson(filled, gaps, z),
                "filled_by_close": run["counts"].get("G2_GAP_FILLED_BY_CLOSE", 0) / gaps if gaps else None,
                "traded_target_rate": hits / n if n else None, "claimed_fill_rate": CLAIMS["G2_fill_rate"]}
    if name == "B1" and n:
        stopped = sum(1 for t in trades if t["reason"] == "STOP")
        return {"stopped_rate": stopped / n, "stopped_rate_interval": wilson(stopped, n, z),
                "claimed_failure_rate": CLAIMS["B1_failure_rate"]}
    if name == "X1" and n:
        return {"mean_gross_R": _mean(r), "claimed_gross_R": CLAIMS["X1_gross_R"]}
    return {}


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _count(items) -> dict:
    out = defaultdict(int)
    for x in items:
        out[x] += 1
    return dict(sorted(out.items()))


def _summary(trades: list[dict], minimum: int = 20) -> dict:
    """Trade count and per-trade means (means only from `minimum` trades, so thin cells print no number)."""
    enough = len(trades) >= minimum
    return {"trades": len(trades),
            "mean_reward": _mean(t["reward"] for t in trades) if enough else None,
            "mean_net_bps": _mean(t["net_bps"] for t in trades) if enough else None,
            "mean_gross_bps": _mean(t["gross_bps"] for t in trades) if enough else None,
            "hit_rate": _mean(1.0 if t["net_bps"] > 0 else 0.0 for t in trades) if enough else None}


# ---- gates ---------------------------------------------------------------------

def holdout_ready(dev_root, holdout_root, tickers: list[str], prices_sha: str) -> tuple[bool, str]:
    if REGISTERED_PROTOCOL_SHA256 is None:
        return False, "PROTOCOL_NOT_REGISTERED_FOR_HOLDOUT"
    if REGISTERED_PROTOCOL_SHA256 != PROTOCOL_SHA256:
        return False, "PROTOCOL_CHANGED_SINCE_REGISTRATION"
    if behavior_digest() != BEHAVIOR_SHA256:
        return False, "REPLAY_CODE_CHANGED"
    if sorted(tickers) != PROTOCOL["universe"]:
        return False, "UNIVERSE_NOT_REGISTERED"
    if prices_sha != DAILY_PRICES_SHA256:
        return False, "DAILY_PRICES_NOT_REGISTERED"
    if REGISTERED_DEV_INPUTS is None or files_sha(dev_root) != REGISTERED_DEV_INPUTS:
        return False, "DEVELOPMENT_INPUTS_NOT_REGISTERED"
    if REGISTERED_HOLDOUT_INPUTS is None:
        return False, "HOLDOUT_INPUTS_NOT_REGISTERED"
    symbols = sorted(tickers) + ["SPY"]
    if missing_months(holdout_root, symbols, months_between(*HOLDOUT)) \
            or missing_months(dev_root, symbols, [previous_month(HOLDOUT[0])]):
        return False, "HOLDOUT_FETCH_INCOMPLETE"
    if files_sha(holdout_root) != REGISTERED_HOLDOUT_INPUTS:
        return False, "HOLDOUT_INPUTS_CHANGED"
    return True, "READY"


def stage_report(result: dict, stage: str, months: tuple[str, str]) -> dict:
    """What a stage publishes: aggregates only. Pinned with the replay, so the label and the verdict rules of
    each stage are part of the behaviour pin."""
    holdout = stage == "holdout"
    return {"stage": stage, "months": list(months),
            "label": "INTRADAY_HOLDOUT_PSEUDO_OUT_OF_SAMPLE" if holdout else "DEVELOPMENT_DESCRIPTIVE",
            "sessions": len(result["sessions"]), "eligible_stock_days": sum(result["eligible"].values()),
            "excluded": result["excluded"], "excluded_by_ticker": result["excluded_by_ticker"],
            "skips": result["skips"], "counts": result["counts"],
            "strategies": evaluate(result, months, holdout=holdout)}


# ---- behaviour pin -------------------------------------------------------------

def synthetic_day(seed: int, open_: float, drift: float, n: int = 390, squeeze_at: int | None = None,
                  crash: bool = False) -> dict:
    """Deterministic synthetic minute bars (a fixed linear-congruential walk). With squeeze_at, four bars there
    narrow in turn and the next bar breaks out; with crash, bars 1-4 fall 0.8% each."""
    x = seed * 2654435761 % 2**31
    o, h, l, c, v = [], [], [], [], []
    price = open_
    for b in range(n):
        x = (1103515245 * x + 12345) % 2**31
        step = ((x % 2001) / 1000 - 1) * 0.0015 + drift
        width = 0.0008 * (1.5 - min(b, 60) / 120)
        if squeeze_at is not None and squeeze_at <= b < squeeze_at + 4:
            step, width = 0.0, 0.00045 * (squeeze_at + 4 - b) / 4
        if squeeze_at is not None and b == squeeze_at + 4:
            step = 0.004 * (1 if seed % 2 else -1)
            width = 0.0001 if seed % 3 else width       # most breakouts clear the box; some sweep both sides
        if crash and 1 <= b <= 4:
            step = -0.008
        close = price * (1 + step)
        o.append(round(price, 2))
        c.append(round(close, 2))
        h.append(round(max(price, close) * (1 + width), 2))
        l.append(round(min(price, close) * (1 - width), 2))
        v.append(float(10_000 + (x % 5000) * (4 if b < 5 else 1)))
        price = close
    return {"o": o, "h": h, "l": l, "c": c, "v": v}


FIXTURE_HOLIDAYS = {"2018-07-04", "2018-09-03", "2018-11-22", "2018-12-05", "2018-12-25", "2019-01-01", "2019-01-21",
                    "2019-02-18"}


def fixture_sessions(first: str, last: str) -> list[str]:
    """Weekdays less a fixed holiday list: no exchange-calendar release can move the pin."""
    from datetime import date, timedelta
    day, end, out = date.fromisoformat(first), date.fromisoformat(last), []
    while day <= end:
        if day.weekday() < 5 and day.isoformat() not in FIXTURE_HOLIDAYS:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def behavior_fixture():
    """Six synthetic stocks and SPY over a past window: gaps of each size (one deep enough for the short-sale
    rule at the open, one that crashes through it within minutes), squeezes, earnings sessions, an ex-dividend
    session, a dividend anomaly, a missing open bar, a mismatched daily open, a missing daily row and a
    corporate action. Synthetic tickers, so a universe change cannot move the pin."""
    sessions = fixture_sessions("2018-07-02", "2019-03-29")
    gaps = {5: 0.035, 9: -0.04, 13: 0.015, 17: -0.02, 21: 0.025, 26: -0.012, 33: 0.05, 38: -0.031}
    forced = {29: 0.08, 41: -0.09, 44: -0.07}   # an attention gap up; gaps down for Rule 201 at the open and later
    tickers = ["T1", "T2", "T3", "T4", "T5", "T6"]
    prices, bars, big = {}, defaultdict(dict), defaultdict(list)
    for k, t in enumerate([*tickers, "SPY"]):
        rows, close = [], 30.0 + 20 * k
        for i, s in enumerate(sessions):
            slot = (i + 3 * k) % 45
            gap = gaps.get(slot, 0.0) * (1 if (i + k) % 2 else -1)
            gap = gap * 0.2 if t == "SPY" else forced.get(slot, gap)
            if abs(gap) >= 0.03 and slot not in forced and s >= "2019-02-01":
                big[t].append(s)
            open_ = round(close * (1 + gap), 2)
            day = synthetic_day(1000 * k + i, open_, 0.00003 * (1 if (i + k) % 3 else -1),
                                squeeze_at=8 + (i % 30) if (i + k) % 4 == 0 else None,
                                crash=t != "SPY" and slot == 44)
            bars[t][s] = day
            adj = 0.99 if t == "T1" and i < 160 else 1.01 if t == "T3" and i == 170 else 1.0
            rows.append({"date": s, "open": open_ * (1.01 if t == "T4" and i == 165 else 1.0), "high": max(day["h"]),
                         "low": min(day["l"]), "close": day["c"][-1], "adj_close": round(day["c"][-1] * adj, 2),
                         "volume": 1e6})
            close = day["c"][-1]
        prices[t] = rows
    bars["T5"][sessions[175]]["o"][0] = None
    prices["T6"] = [r for r in prices["T6"] if r["date"] >= "2018-12-17"]   # a short history: beta's fallback
    del prices["T2"][168]                       # a missing daily row: that session and the next are not scored
    late = [s for s in sessions if s >= "2019-02-01"]
    events = {"T1": {"primary": {big["T1"][0]: "PRE_OPEN", big["T1"][1]: "AFTER_CLOSE", late[30]: "AFTER_CLOSE"},
                     "any": {big["T1"][0], big["T1"][1], late[30]}},
              "T2": {"primary": {big["T2"][0]: "AFTER_CLOSE", late[20]: "OTHER"}, "any": {big["T2"][0], late[20]}}}

    def bars_for(ticker, month):
        return {s: d for s, d in bars[ticker].items() if s[:7] == month}
    return bars_for, prices, events, tickers, {"T4": (late[10],)}


BEHAVIOR_MONTHS = ("2019-02", "2019-03")


def behavior_run() -> dict:
    bars_for, prices, events, tickers, actions = behavior_fixture()
    return replay(bars_for, prices, events, BEHAVIOR_MONTHS, tickers, corporate_actions=actions)


def synthetic_book(*, reward=0.5, skill=0.3, stocks=20, months=24, cost_drag=0.6, spike=0.0) -> dict:
    """A replay result with one G1 trade a session, for pinning the verdict and labels on known books."""
    sessions = [f"{2024 + m // 12}-{m % 12 + 1:02d}-{d:02d}" for m in range(months) for d in range(1, 11)]
    trades = []
    for i, s in enumerate(sessions):
        wobble = (0.2 if i % 2 else -0.2) + (spike if i < 10 else 0.0)
        trades.append({"strategy": "G1", "session": s, "month": s[:7], "ticker": f"T{i % stocks}", "side": 1,
                       "reward": reward + wobble, "skill": skill + wobble, "control": reward - skill,
                       "reward_2x": reward - cost_drag + wobble, "reward_gross": reward + 0.1 + wobble,
                       "reward_worst_fill": reward + wobble, "skill_worst_fill": skill + wobble, "net_bps": 5.0,
                       "gross_bps": 25.0, "r_gross": 0.2, "reason": "TARGET", "earnings": False, "both": False,
                       "stop_in_fill_bar": False})
    return {"trades": trades, "sessions": sessions, "eligible": {s: 50 for s in sessions}, "counts": {},
            "excluded": {}, "excluded_by_ticker": {}, "skips": {}}


def _pad(bars: list[tuple], n: int = 60) -> dict:
    bars = list(bars) + [(bars[-1][3],) * 4] * (n - len(bars))
    return {k: [b[i] for b in bars] for i, k in enumerate("ohlc")} | {"v": [1000.0] * n}


def strategy_cases() -> dict:
    """Each setup on hand-built bars at its edges (gap thresholds, retrace, fill and target checks, the bar path),
    so a change to any rule moves the pin even where the synthetic walk never reaches it."""
    f = {"prior_close_raw": 100.0, "prior_close": 100.0, "prior_low": 99.0, "close_before_prior": 100.0,
         "daily_close": 101.5, "atr14": 2.0, "beta": 1.0, "sigma_d": 0.02, "overnight": 0.04, "overnight_spy": 0.0,
         "overnight_sd": 0.01, "daily_open": 100.0, "history": 300}
    ssr = {**f, "prior_low": 89.0}
    g1_down = [(96.0, 96.2, 95.5, 95.6), (95.6, 96.4, 95.5, 96.3), (96.3, 96.8, 96.1, 96.6), (96.6, 96.7, 96.0, 96.05),
               (96.0, 96.1, 95.0, 95.2)]
    g2_up = ([(102.0 + 0.1 * i, 102.1 + 0.1 * i, 101.8 + 0.1 * i, 102.1 + 0.1 * i) for i in range(5)]
             + [(102.5, 102.7, 102.3, 102.4), (102.4, 102.5, 102.1, 102.2), (102.2, 102.3, 101.9, 102.0),
                (102.0, 102.1, 101.7, 101.8), (101.8, 101.9, 101.5, 101.6)])
    opening = [(102.5, 103.0, 101.5, 102.8)] + [(102.8, 102.9, 102.0, 102.6)] * 4
    box = [(100.0, 100.1, 99.9, 100.0)] * 10 + [(100.0, 100.055, 99.945, 100.0), (100.0, 100.045, 99.955, 100.0),
                                                (100.0, 100.035, 99.965, 100.0), (100.0, 100.025, 99.975, 100.0)]
    norms = [math.log(100.1 / 99.9)] * 60
    x1_open = [(100.0, 100.6, 99.8, 100.2)] + [(100.2, 100.4, 100.0, 100.3)] * 3 + [(100.3, 100.5, 100.1, 100.4)]
    days = {"g1": _pad(g1_down + [(96.0, 96.1, 95.9, 96.0)]),
            "g1_retraced": _pad(g1_down[:2] + [(96.3, 98.1, 96.1, 96.6)] + g1_down[3:]),
            "g2": _pad(g2_up + [(101.6, 101.7, 101.0, 101.1), (101.1, 101.2, 99.9, 100.4)]),
            "g2_open_past_target": _pad(g2_up + [(99.8, 99.9, 99.5, 99.6)]),
            "b1": _pad(opening + [(102.6, 102.7, 102.5, 102.6), (102.9, 103.2, 102.8, 103.1)]),
            "b1_gap_through": _pad(opening + [(103.4, 103.5, 103.3, 103.4)]),
            "c1_up": _pad(box + [(100.0, 100.3, 99.98, 100.25)]), "c1_both_up_bar": _pad(box + [(100.0, 100.3, 99.7, 100.2)]),
            "c1_both_down_bar": _pad(box + [(100.0, 100.3, 99.7, 99.8)]),
            "x1_opens_below_stop": _pad(x1_open + [(100.0, 100.75, 99.98, 100.7)], 390),
            "x1_falls_back": _pad(x1_open + [(100.0, 100.75, 99.98, 100.3)], 390)}
    spy = {k: [400.0] * 390 for k in "ohlc"} | {"v": [1.0] * 390}
    out = {}
    for name, day in days.items():
        for label, feats in (("plain", f), ("ssr", ssr)):
            gap = {"g1": -0.04, "g2": 0.02, "b1": 0.025, "c1": 0.0, "x1": 0.0}[name[:2]]
            fn = {"g1": lambda d, x: g1(d, x, gap), "g2": lambda d, x: g2(d, x, gap), "b1": lambda d, x: b1(d, x, gap),
                  "c1": lambda d, x: c1(d, x, norms), "x1": x1_candidate}[name[:2]]
            t = fn(day, feats)
            entry = {"trade": t}
            if t and "skip" not in t:
                entry["own"] = score(t, day, spy, 400.0, 1.0, 0.01)
                entry["mirror"] = score(t, day, spy, 400.0, 1.0, 0.01, side=-t["side"])
                entry["worst"] = score(t, day, spy, 400.0, 1.0, 0.01, fill_bar="worst")
            out[f"{name}:{label}"] = entry
    return out


VERDICT_CASES = {"pass": {}, "no_skill": {"skill": 0.0}, "no_mean": {"reward": 0.0}, "few_stocks": {"stocks": 10},
                 "few_months": {"months": 17}, "fragile": {"reward": 0.05, "skill": 0.05, "spike": 30.0},
                 "cost_robust": {"cost_drag": 0.2}}


@lru_cache(maxsize=1)
def behavior_digest() -> str:
    """The replay and both stages' reports on the synthetic fixture, and the verdict and labels on known books."""
    result = behavior_run()

    def rounded(x):
        if isinstance(x, float):
            return round(x, 9) if math.isfinite(x) else str(x)
        if isinstance(x, dict):
            return {str(k): rounded(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))}
        if isinstance(x, (list, tuple)):
            return [rounded(v) for v in x]
        return x
    trades = sorted(result["trades"], key=lambda t: (t["session"], t["ticker"], t["strategy"], t["side"]))
    verdicts = {}
    for name, kw in VERDICT_CASES.items():
        book_ = synthetic_book(**kw)
        g1 = evaluate(book_, ("2024-01", "2025-12"), holdout=True)["G1"]
        verdicts[name] = {"verdict": g1["verdict"], "labels": g1["labels"], "reward": g1["reward"],
                          "monthly": g1["monthly"]}
    rows = [{"session": "2019-01-02", "minute": m, "open": "10", "high": "10.2", "low": "9.9", "close": "10.1",
             "volume": "5", "count": c} for m, c in (("09:30", "3"), ("09:31", "0"), ("09:33", "2"), ("15:59", "0"))]
    timing = [release_timing(a, r) for a, r in (("2019-01-29T21:05:00+00:00", "2019-01-30"),
                                                ("2019-01-30T13:00:00+00:00", "2019-01-30"),
                                                ("2019-01-30T14:45:00+00:00", "2019-01-30"),
                                                ("2019-07-29T20:01:00+00:00", "2019-07-30"))]
    return digest(rounded({"trades": trades, "eligible": result["eligible"], "parsed": days_from_rows(rows),
                           "cases": strategy_cases(),
                           "timing": timing,
                           "dev": stage_report(result, "dev", BEHAVIOR_MONTHS),
                           "holdout": stage_report(result, "holdout", BEHAVIOR_MONTHS), "verdicts": verdicts}))


# ---- the stages ----------------------------------------------------------------

def run(*, dev_root, holdout_root=None, prices_dir, stage: str, tickers: list[str], allow_incomplete: bool = False) -> dict:
    """Development (descriptive) or the one hold-out run; returns aggregates only. allow_incomplete runs the
    development stage on a partial fetch, labelled a preview."""
    from ..market_calendar import session_dates
    if stage not in ("dev", "holdout"):
        raise ValueError("INTRADAY_REPLAY_STAGE_INVALID")
    if stage == "holdout" and holdout_root is None:
        raise ValueError("INTRADAY_REPLAY_HOLDOUT_ROOT_REQUIRED")
    months = DEV if stage == "dev" else HOLDOUT
    loaded = [previous_month(months[0])] + months_between(*months)
    prices, prices_sha = ivr.load_prices(prices_dir)

    def refused(reason, **extra):
        return {"protocol_id": PROTOCOL_ID, "stage": stage, "status": "REFUSED", "reason": reason, **extra}
    if stage == "holdout":
        ready, reason = holdout_ready(dev_root, holdout_root, tickers, prices_sha)
        if not ready:
            return refused(reason)
    elif prices_sha != DAILY_PRICES_SHA256:
        return refused("DAILY_PRICES_NOT_REGISTERED")
    calendar = session_dates(f"{loaded[0]}-01", f"{months[1]}-31")
    if [r["date"] for r in prices["SPY"] if calendar[0] <= r["date"] <= calendar[-1]] != calendar:
        return refused("SPY_DAILY_INCOMPLETE")
    symbols = sorted(tickers) + ["SPY"]
    missing = missing_months(dev_root, symbols, loaded) if stage == "dev" else []
    if missing and not allow_incomplete:
        return refused("INPUTS_INCOMPLETE", missing=len(missing), first_missing=missing[:5])

    def bars_for(ticker, month):
        if stage == "holdout" and month != previous_month(HOLDOUT[0]):
            return load_month(holdout_root, ticker, month)
        return load_month(dev_root, ticker, month)      # development, or the hold-out's warm-up month
    result = replay(bars_for, prices, earnings(), months, sorted(tickers))
    report = stage_report(result, stage, months)
    if missing:
        report["label"] = "DEVELOPMENT_PREVIEW_INCOMPLETE_FETCH"
        report["missing_ticker_months"] = len(missing)
    root = dev_root if stage == "dev" else holdout_root
    return {"protocol_id": PROTOCOL_ID, "protocol_sha256": PROTOCOL_SHA256, "behavior_sha256": BEHAVIOR_SHA256,
            "status": "OK", **report,
            "inputs": {"bars_sha256": files_sha(root), "prices_sha256": prices_sha,
                       "earnings_sha256": er.EARNINGS_ANNOUNCEMENTS_SHA256},
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "use": "INFORMATION_ONLY", "promotion": "NOT_AUTHORIZED", "broker_submission": False}
