"""Daily-bar technical candidates. Historical reconstruction is never live evidence."""

from __future__ import annotations
from math import exp, isfinite, sqrt
from statistics import mean, pstdev
from ..agents.contracts import digest
from ..market_calendar import session_dates, session_offset

VERSION = "technical-setups.v1"
NAMES = (
    "trend",
    "breakout",
    "pullback",
    "rejection",
    "engulfing",
    "compression_breakout",
    "volume_breakout",
    "pullback_rejection",
)
POLICY = {
    "version": VERSION,
    "horizons": [5, 10, 20],
    "warmup": 60,
    "history_sessions": 1260,
    "train_sessions": 252,
    "test_sessions": 63,
    "minimum_train_sessions": 252,
    "minimum_active": 20,
    "prior_strength": 32,
    "prior_units": "non-overlapping holding windows",
    "standard_error_penalty": 1.645,
    "effective_sample": "non-overlapping holding windows per stock plus pooled windows counting each date once",
    "temperature": 0.005,
    "round_trip_cost": 0.002,
    "threshold": 0.25,
    "entry": "next_session_close",
    "mixtures": "nonnegative_simplex",
    "no_positive_training_edge": "ABSTAIN",
    "candidate_selection": "training_only_shrunk_mean_net_return_minus_one_standard_error",
}
POLICY_HASH = digest(POLICY)


def normalized(rows, cutoff):
    """Adjust every OHLC field to the same stored adjusted-close vintage."""
    result = {}
    for r in rows:
        day = str(r["date"])[:10]
        if day > cutoff:
            continue
        vals = [
            r.get(k) for k in ("open", "high", "low", "close", "adj_close", "volume")
        ]
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v)
            for v in vals
        ):
            continue
        o, h, l, c, a, v = map(float, vals)
        if min(o, h, l, c, a) <= 0 or v < 0 or h < max(o, c) or l > min(o, c) or h < l:
            continue
        factor = a / c
        result[day] = {
            "date": day,
            "open": o * factor,
            "high": h * factor,
            "low": l * factor,
            "close": a,
            "volume": v,
            "adjustment_factor": factor,
        }
    return result


def features(window):
    if len(window) != 60:
        raise ValueError("TECHNICAL_SETUP_WARMUP_REQUIRED")
    r, p = window[-1], window[-2]
    closes = [x["close"] for x in window]
    sma20, sma50 = mean(closes[-20:]), mean(closes[-50:])
    trend = 1 if closes[-1] > sma20 > sma50 else -1 if closes[-1] < sma20 < sma50 else 0
    prior = window[-21:-1]
    breakout = (
        1
        if r["close"] > max(x["high"] for x in prior)
        else -1 if r["close"] < min(x["low"] for x in prior) else 0
    )
    span = r["high"] - r["low"]
    body = abs(r["close"] - r["open"]) / span if span else 0
    upper = (r["high"] - max(r["open"], r["close"])) / span if span else 0
    lower = (min(r["open"], r["close"]) - r["low"]) / span if span else 0
    rejection = (
        1
        if lower >= 0.6 and upper <= 0.2
        else -1 if upper >= 0.6 and lower <= 0.2 else 0
    )
    engulf = (
        1
        if p["close"] < p["open"]
        and r["close"] > r["open"]
        and r["open"] <= p["close"]
        and r["close"] >= p["open"]
        else (
            -1
            if p["close"] > p["open"]
            and r["close"] < r["open"]
            and r["open"] >= p["close"]
            and r["close"] <= p["open"]
            else 0
        )
    )
    pullback = (
        trend
        if trend == 1
        and r["low"] <= sma20
        and r["close"] > sma20
        and r["close"] > p["close"]
        else (
            trend
            if trend == -1
            and r["high"] >= sma20
            and r["close"] < sma20
            and r["close"] < p["close"]
            else 0
        )
    )
    ranges = [(x["high"] - x["low"]) / x["close"] for x in window[:-1]]
    compressed = mean(ranges[-5:]) < 0.7 * mean(ranges[-20:])
    volume_base = mean(x["volume"] for x in prior)
    # A split in the comparison window invalidates raw-volume confirmation.
    factors = [x["adjustment_factor"] for x in window[-21:]]
    volume_valid = max(factors) / min(factors) < 1.2 and volume_base > 0
    ratio = r["volume"] / volume_base if volume_valid else None
    signals = dict(
        zip(
            NAMES,
            (
                trend,
                breakout,
                pullback,
                rejection,
                engulf,
                breakout if compressed else 0,
                breakout if ratio is not None and ratio >= 1.5 else 0,
                pullback if pullback == rejection else 0,
            ),
        )
    )
    return {
        "signals": signals,
        "body_ratio": body,
        "upper_wick_ratio": upper,
        "lower_wick_ratio": lower,
        "volume_confirmation_ratio": ratio,
        "compressed": compressed,
        "price_vs_sma20": r["close"] / sma20 - 1,
        "price_vs_sma50": r["close"] / sma50 - 1,
    }


def examples(rows, cutoff, horizon):
    if horizon not in POLICY["horizons"]:
        raise ValueError("TECHNICAL_HORIZON_INVALID")
    bars = normalized(rows, cutoff)
    if not bars:
        return [], None
    dates = session_dates(min(bars), cutoff)[-POLICY["history_sessions"] :]
    samples = []
    current = None
    for i in range(59, len(dates)):
        window = dates[i - 59 : i + 1]
        if not all(d in bars for d in window):
            continue
        f = features([bars[d] for d in window])
        origin = dates[i]
        if origin == cutoff:
            current = f
        end = i + 1 + horizon
        if end >= len(dates) or not all(d in bars for d in dates[i + 1 : end + 1]):
            continue
        entry_price = bars[dates[i + 1]]["close"]
        lows = [bars[d]["low"] for d in dates[i + 2 : end + 1]]
        highs = [bars[d]["high"] for d in dates[i + 2 : end + 1]]
        excursions = {
            "long_mae": min(0, min(lows) / entry_price - 1),
            "long_mfe": max(0, max(highs) / entry_price - 1),
            "short_mae": min(0, 1 - max(highs) / entry_price),
            "short_mfe": max(0, 1 - min(lows) / entry_price),
        }
        samples.append(
            {
                "origin": origin,
                **excursions,
                "entry": dates[i + 1],
                "due": dates[end],
                "signals": f["signals"],
                "return": bars[dates[end]]["close"] / bars[dates[i + 1]]["close"] - 1,
            }
        )
    return samples, current


def non_overlapping_windows(samples):
    """Holding windows that share no session: a conservative effective count.

    Daily signals on one stock re-use most of the same forward window, and
    stocks on the same date share market moves, so neither active days nor
    stock-days are independent observations.
    """
    count, last_due = 0, None
    for s in sorted(samples, key=lambda s: (s["origin"], s["due"])):
        if last_due is None or s["entry"] > last_due:
            count += 1
            last_due = s["due"]
    return count


def pooled_statistics(samples):
    stats = {}
    for name in NAMES:
        active = [s for s in samples if s["signals"][name]]
        values = [s["signals"][name] * s["return"] - POLICY["round_trip_cost"] for s in active]
        by_date = {s["origin"]: s for s in active}
        stats[name] = {
            "active": len(values),
            "dates": len(by_date),
            # Each date counts once across stocks.
            "windows": non_overlapping_windows(list(by_date.values())),
            "mean": mean(values) if values else 0.0,
            "risk": pstdev(values) if len(values) >= 2 else 0.0,
        }
    return stats


def fit(local, pooled, *, pooled_stats=None):
    utilities = {}
    counts = {}
    prior = POLICY["prior_strength"]
    stats = pooled_stats if pooled_stats is not None else pooled_statistics(pooled)
    for name in NAMES:
        active = [s for s in local if s["signals"][name]]
        values = [s["signals"][name] * s["return"] - POLICY["round_trip_cost"] for s in active]
        pool = stats[name]
        usable = pool["dates"] >= POLICY["minimum_active"]
        base = pool["mean"] if usable else 0.0
        local_risk = pstdev(values) if len(values) >= 2 else pool["risk"]
        # Shrink in effective (non-overlapping) windows: overlapping trade-days
        # once gave a stock with ~6 independent windows the weight of ~120.
        n_local = non_overlapping_windows(active)
        n_pool = pool.get("windows", 0) if usable else 0
        w_local = n_local / (n_local + prior) if values else 0.0
        local_mean = mean(values) if values else 0.0
        avg = w_local * local_mean + (1 - w_local) * base
        # Penalize uncertainty in that shrunk average, not per-trade
        # dispersion: v1's "mean - 0.5 std" demanded a per-trade Sharpe above
        # 0.5 (about 3.5 annualized at 5 sessions), so it abstained on any
        # realistic edge. Missing windows fall back to one (JSON has no infinity).
        variance = (w_local**2 * local_risk**2 / max(n_local, 1)
                    + (1 - w_local) ** 2 * pool["risk"] ** 2 / max(n_pool, 1))
        utilities[name] = avg - POLICY["standard_error_penalty"] * sqrt(variance)
        counts[name] = {
            "stock_active": len(values),
            "pooled_active": pool["active"],
            "pooled_active_dates": pool["dates"],
            "stock_windows": n_local,
            "pooled_windows": n_pool,
        }
    available = [
        n for n in NAMES if counts[n]["pooled_active_dates"] >= POLICY["minimum_active"]
    ]
    if not available:
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "weights": {n: 0.0 for n in NAMES},
            "counts": counts,
            "utilities": utilities,
        }
    top = max(utilities[n] for n in available)
    if top <= 0:
        return {
            "status": "NO_POSITIVE_TRAINING_EDGE",
            "weights": {n: 0.0 for n in NAMES},
            "counts": counts,
            "utilities": utilities,
        }
    scores = {
        n: exp(max(-50, (utilities[n] - top) / POLICY["temperature"]))
        for n in available
    }
    total = sum(scores.values())
    return {
        "status": "SHADOW_CANDIDATE",
        "weights": {n: scores.get(n, 0) / total for n in NAMES},
        "counts": counts,
        "utilities": utilities,
    }


def action(signals, weights):
    score = sum(signals[n] * weights.get(n, 0) for n in NAMES)
    return (
        1
        if score >= POLICY["threshold"]
        else -1 if score <= -POLICY["threshold"] else 0
    )


def _wilson(successes, n, z=1.959964):
    if n <= 0:
        return None
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [centre - half, centre + half]


def metrics(values):
    # Values are individual overlapping opportunities, never portfolio returns.
    active = [v for v in values if v["action"]]
    net = [v["action"] * v["return"] - POLICY["round_trip_cost"] for v in active]
    adverse = [v.get("long_mae" if v["action"] == 1 else "short_mae") for v in active]
    adverse = [v for v in adverse if v is not None]
    favorable = [v.get("long_mfe" if v["action"] == 1 else "short_mfe") for v in active]
    favorable = [v for v in favorable if v is not None]
    # Overlapping trades share most of their window: intervals use the count
    # of non-overlapping holding windows, not the raw trade count.
    effective = non_overlapping_windows(active) if active and "entry" in active[0] else len(active)
    mean_net = mean(net) if net else None
    sd = pstdev(net) if len(net) >= 2 else None
    wins = sum(x > 0 for x in net)
    return {
        "return_std": sd,
        "worst_net_return": min(net) if net else None,
        "mean_adverse_excursion": mean(adverse) if adverse else None,
        "mean_favorable_excursion": mean(favorable) if favorable else None,
        "opportunities": len(values),
        "trades": len(active),
        "effective_trades": effective,
        "mean_net_return": mean_net,
        "mean_net_return_ci95": (
            [mean_net - 1.959964 * sd / sqrt(effective), mean_net + 1.959964 * sd / sqrt(effective)]
            if sd is not None and effective >= 2 else None
        ),
        "mean_net_return_per_opportunity": sum(net) / len(values) if values else None,
        "net_win_rate": wins / len(net) if net else None,
        "net_win_rate_ci95": _wilson(wins / len(net) * effective, effective) if net and effective else None,
    }


PLACEBO_SEED = "technical-setups-placebo.v1"


def placebo(samples):
    """Returns sign-flipped by origin date, shared across stocks: no setup has an edge.

    Same-date stocks keep their common move, so the placebo keeps the
    cross-sectional structure the real evaluation faces.
    """
    def flip(day):
        return 1 if int(digest({"seed": PLACEBO_SEED, "date": day})[:8], 16) % 2 else -1

    return [{**s, "return": flip(s["origin"]) * s["return"]} for s in samples]


def prepare_walk_forward(pooled, cutoff):
    pooled = [s for s in pooled if s["due"] <= cutoff and s["origin"] <= cutoff]
    origins = sorted({s["origin"] for s in pooled})
    contexts = []
    for start in range(
        POLICY["minimum_train_sessions"], len(origins), POLICY["test_sessions"]
    ):
        block = origins[start : start + POLICY["test_sessions"]]
        first, last = block[0], block[-1]
        earliest = origins[max(0, start - POLICY["train_sessions"])]
        train = [
            s for s in pooled if earliest <= s["origin"] < first and s["due"] < first
        ]
        contexts.append(
            {
                "test_start": first,
                "test_end": last,
                "train_origin_start": earliest,
                "train_max_due": max((s["due"] for s in train), default=None),
                "pooled_train_count": len(train),
                "train_hash": digest(train),
                "stats": pooled_statistics(train),
                "placebo_stats": pooled_statistics(placebo(train)),
            }
        )
    current_start = session_offset(cutoff, -POLICY["train_sessions"])
    train = [s for s in pooled if s["origin"] >= current_start and s["due"] <= cutoff]
    return {
        "folds": contexts,
        "current_start": current_start,
        "current_stats": pooled_statistics(train),
        "current_train_hash": digest(train),
        "current_train_max_due": max((s["due"] for s in train), default=None),
    }


def walk_forward(samples, pooled, cutoff, *, context=None):
    context = context if context is not None else prepare_walk_forward(pooled, cutoff)
    folds = []
    predictions = []
    placebo_acting = []
    for c in context["folds"]:
        first, last, earliest = c["test_start"], c["test_end"], c["train_origin_start"]
        local = [
            s for s in samples if earliest <= s["origin"] < first and s["due"] < first
        ]
        test = [
            s for s in samples if first <= s["origin"] <= last and s["due"] <= cutoff
        ]
        if not test:
            continue
        weights = fit(local, [], pooled_stats=c["stats"])
        placebo_fit = fit(placebo(local), [], pooled_stats=c["placebo_stats"])
        placebo_acting.append(placebo_fit["status"] == "SHADOW_CANDIDATE")
        folds.append(
            {
                **{k: v for k, v in c.items() if k not in ("stats", "placebo_stats")},
                "train_count": len(local),
                "weights": weights["weights"],
                "status": weights["status"],
            }
        )
        for s in test:
            predictions.append(
                {
                    **s,
                    "fold": len(folds) - 1,
                    "action": action(s["signals"], weights["weights"]),
                }
            )
    local = [
        s
        for s in samples
        if s["origin"] >= context["current_start"] and s["due"] <= cutoff
    ]
    candidate = fit(local, [], pooled_stats=context["current_stats"])
    equal = {n: 1 / len(NAMES) for n in NAMES}
    comparisons = {
        "learned_mix": metrics(predictions),
        "equal_mix": metrics(
            [{**s, "action": action(s["signals"], equal)} for s in predictions]
        ),
        "buy_and_hold": metrics([{**s, "action": 1} for s in predictions]),
    }
    for n in NAMES:
        comparisons[n] = metrics(
            [{**s, "action": s["signals"][n]} for s in predictions]
        )
    return {
        "candidate": candidate,
        "folds": folds,
        # How often the same fitting would act with no edge anywhere: a
        # per-run false-positive check on the training rule.
        "placebo": {
            "method": "returns sign-flipped by origin date, shared across stocks",
            "folds": len(placebo_acting),
            "acting_folds": sum(placebo_acting),
            "act_rate": sum(placebo_acting) / len(placebo_acting) if placebo_acting else None,
        },
        "oos": comparisons,
        "oos_predictions": predictions,
        "current_train_hash": context["current_train_hash"],
        "current_train_max_due": context["current_train_max_due"],
    }
