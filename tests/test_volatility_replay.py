"""Volatility-forecast replay: point-in-time features, purged refits, QLIKE, block inference, gates."""

import math
import random

import pytest

from stock_machine.agents.contracts import AGENT_UNIVERSE, digest
from stock_machine.backtest import volatility_replay as vr
from stock_machine.market_calendar import session_dates
from tests.test_research_postgres import pg  # noqa: F401  (Postgres fixture)

PINNED_PROTOCOL_SHA256 = "63fbb13b99b8b3270e570bdfbb08a25456bd36b8befad8f8d1257fe0bb93449c"
PINNED_BEHAVIOR_SHA256 = "435a839c39c65fed7296842fcbd2ab2731317ec79c70bd8049c7b8ef25425fff"
SESSIONS = session_dates("2014-01-02", "2020-12-31")


def garch_rows(seed, *, start=50.0, omega=2e-6, alpha=0.08, beta=0.9, sessions=SESSIONS, jumps=()):
    """Daily bars whose volatility clusters and mean-reverts (GARCH(1,1)), with optional event jumps."""
    rnd = random.Random(seed)
    var, price, rows = omega / (1 - alpha - beta), start, []
    for day in sessions:
        r = rnd.gauss(0, math.sqrt(var)) + (rnd.choice((-1, 1)) * 0.06 if day in jumps else 0.0)
        var = omega + alpha * r * r + beta * var
        price *= math.exp(r)
        spread = abs(rnd.gauss(0, math.sqrt(var)))
        rows.append({"date": day, "open": price, "high": price * math.exp(spread), "low": price * math.exp(-spread),
                     "close": price, "adj_close": price, "volume": 1_000_000})
    return rows


def quarterly_releases():
    return {min(d for d in SESSIONS if d >= f"{y}-{m}-05") for y in range(2014, 2021) for m in ("02", "05", "08", "11")}


def dev_result(prices, spy, earnings=None, **kw):
    built = vr.panel(prices, spy, earnings or {}, "2020-12-31", **kw)
    scored = [s for s in vr.forecasts(built["rows"]) if vr.split_of(s["month"]) == "dev"]
    return vr.evaluate(scored, stage="dev", dropped=built["dropped"]), built


def test_the_protocol_and_the_forecasting_code_are_pinned():
    assert vr.PROTOCOL_SHA256 == digest(vr.PROTOCOL) == PINNED_PROTOCOL_SHA256   # change => new protocol_id
    assert vr.behavior_digest() == vr.BEHAVIOR_SHA256 == PINNED_BEHAVIOR_SHA256
    assert vr.PROTOCOL["behavior_sha256"] == PINNED_BEHAVIOR_SHA256
    assert vr.PROTOCOL["universe"] == sorted(AGENT_UNIVERSE)
    assert vr.CONFIRMATORY == ("H2", "H4") and vr.PROTOCOL["family"]["hypotheses"] == ["H2", "H4"]
    assert vr.PROTOCOL["splits"]["holdout_label"] == "PSEUDO_OUT_OF_SAMPLE"


def test_the_protocol_is_registered_and_the_holdout_is_ready_on_this_code():
    assert vr.REGISTERED_PROTOCOL_SHA256 == PINNED_PROTOCOL_SHA256 == vr.PROTOCOL_SHA256
    assert vr.holdout_ready(sorted(AGENT_UNIVERSE)) == (True, "READY")


def test_the_holdout_refuses_unless_registered_and_while_code_or_universe_differ(monkeypatch):
    universe = sorted(AGENT_UNIVERSE)
    monkeypatch.setattr(vr, "REGISTERED_PROTOCOL_SHA256", None)
    assert vr.holdout_ready(universe) == (False, "PROTOCOL_NOT_REGISTERED_FOR_HOLDOUT")
    assert vr.run(None, tickers=universe, stage="holdout", end="2026-10-19")["status"] == "REFUSED"
    monkeypatch.setattr(vr, "REGISTERED_PROTOCOL_SHA256", "a" * 64)
    assert vr.holdout_ready(universe) == (False, "PROTOCOL_CHANGED_SINCE_REGISTRATION")
    monkeypatch.setattr(vr, "REGISTERED_PROTOCOL_SHA256", vr.PROTOCOL_SHA256)
    assert vr.holdout_ready(universe) == (True, "READY")
    assert vr.holdout_ready(universe[:10]) == (False, "UNIVERSE_NOT_REGISTERED")
    monkeypatch.setattr(vr, "BEHAVIOR_SHA256", "f" * 64)
    assert vr.holdout_ready(universe) == (False, "FORECASTING_CODE_CHANGED")
    with pytest.raises(ValueError):
        vr.run(None, tickers=[], stage="peek", end="2026-10-19")


def test_measurements_and_missing_return_tolerance():
    assert vr.realized_vol([0.01] * 21) == pytest.approx(math.sqrt(252) * 0.01)
    assert vr.realized_vol([0.01] * 20 + [None]) is None and vr.realized_vol([]) is None
    assert vr.realized_vol([0.01] * 19 + [None, None], missing_share=0.1) == pytest.approx(math.sqrt(252) * 0.01)
    assert vr.realized_vol([0.01] * 18 + [None] * 3, missing_share=0.1) is None
    bars = [{"high": 101.0, "low": 99.0}] * 21
    assert vr.parkinson_vol(bars) == pytest.approx(math.sqrt(252 * math.log(101 / 99) ** 2 / (4 * math.log(2))))
    assert vr.parkinson_vol(bars[:18] + [None] * 3) is None and vr.parkinson_vol(bars[:19] + [None] * 2)
    assert vr.ewma_vol([0.02] * 252) == pytest.approx(math.sqrt(252) * 0.02)
    assert vr.qlike(1.0, 1.0) == 0.0 and vr.qlike(2.0, 1.0) > 0 and vr.qlike(0.5, 1.0) > 0
    assert vr.qlike(1.0, 0.5) > vr.qlike(1.0, 2.0)          # under-forecasting variance is punished more


def test_block_means_inference():
    values = [1.0] * 4 + [3.0] * 4 + [1.0] * 4 + [3.0] * 4 + [1.0] * 4 + [3.0] * 4 + [1.0] * 4 + [3.0] * 4
    assert vr.block_means(values) == [1.0, 3.0] * 4
    out = vr.interval(values)
    assert out["blocks"] == 8 and out["mean"] == 2.0
    assert out["lower"] == pytest.approx(2.0 - vr.T7_975 * math.sqrt(8 * 1.0 / 7 / 8))
    assert vr.interval(values[:7])["lower"] is None
    assert len(vr.block_means(list(range(79)))) == 8


def test_the_expected_release_uses_last_years_window_only():
    reactions = ["2025-11-05", "2026-02-03"]
    assert vr.expected_release(reactions, "2026-10-30")          # 2025-10-31 < 2025-11-05 <= 2025-11-30
    assert not vr.expected_release(reactions, "2026-09-30")
    assert not vr.expected_release(["2026-11-04"], "2026-10-30")  # a future release is never used


def test_features_use_only_data_through_the_decision_close():
    rows, spy = garch_rows(1), garch_rows(2, start=300.0)
    series, spy_series = vr.Series(rows, SESSIONS), vr.Series(spy, SESSIONS)
    i = series.index["2017-06-30"]
    before, _ = vr.observe(series, spy_series, i, [], [])
    altered = [dict(r, close=r["close"] * 3, adj_close=r["adj_close"] * 3, high=r["high"] * 3, low=r["low"] * 3)
               if r["date"] > "2017-06-30" else r for r in rows]
    after, _ = vr.observe(vr.Series(altered, SESSIONS), spy_series, i, [], [])
    assert before == after and set(vr.PLUS_FEATURES) <= set(before)


def test_refits_use_only_targets_completed_before_the_decision(monkeypatch):
    rows = vr.panel({"A": garch_rows(3), "B": garch_rows(4)}, garch_rows(5, start=300.0), {}, "2020-12-31")["rows"]
    seen = []
    real = vr._ols

    def spy_ols(train, feats):
        seen.append((max(r["window_end"] for r in train), min(r["month"] for r in train)))
        return real(train, feats)
    monkeypatch.setattr(vr, "_ols", spy_ols)
    scored = vr.forecasts(rows)
    assert seen and min(s["month"] for s in scored if "HAR" in s["forecast_var"]) >= "2017-06"
    for decision, (last_end, first_month) in zip([s["decision"] for s in scored if "HAR" in s["forecast_var"]
                                                  and s["ticker"] == "A"], seen[::2]):
        assert last_end <= decision and first_month >= vr.TRAIN_START   # purged, and from the earnings start


def test_the_standard_model_beats_last_months_volatility_when_volatility_clusters():
    prices = {f"S{k}": garch_rows(10 + k, start=40.0 + k) for k in range(6)}
    result, _ = dev_result(prices, garch_rows(99, start=300.0, alpha=0.05, beta=0.93))
    dev = result["splits"]["dev"]
    assert set(result["splits"]) == {"dev"} and "verdict" not in dev["hypotheses"]["H1"]
    assert dev["months"] >= 40 and dev["hypotheses"]["H1"]["mean"] > 0   # 2017-06 to 2020-11 and dev["hypotheses"]["H1"]["blocks"] == 8
    assert dev["mean_qlike"]["HAR"] < dev["mean_qlike"]["RV21"]
    assert 0.5 < dev["forecast_to_realized"]["HAR"] < 2.0


def test_uninformative_extras_do_not_win_the_primary_test_on_different_stock_levels():
    # Stocks at very different long-run volatility levels; the extra inputs carry nothing new.
    prices = {f"S{k}": garch_rows(30 + k, omega=1e-6 * (1 + 3 * k)) for k in range(8)}
    result, _ = dev_result(prices, garch_rows(97, start=300.0))
    h1, h2 = result["splits"]["dev"]["hypotheses"]["H1"], result["splits"]["dev"]["hypotheses"]["H2"]
    assert h1["mean"] > 0 and abs(h2["mean"]) < 0.25 * h1["mean"]
    assert h2["lower"] < 0 < h2["upper"]


def test_the_earnings_term_learns_scheduled_event_volatility():
    releases = quarterly_releases()
    prices = {f"S{k:02d}": garch_rows(20 + k, jumps=releases) for k in range(12)}
    result, built = dev_result(prices, garch_rows(98, start=300.0), {t: sorted(releases) for t in prices})
    assert any(r["x"]["earn"] == 1.0 and r["x"]["earn_size"] > 0 for r in built["rows"])
    h4 = result["splits"]["dev"]["hypotheses"]["H4"]
    assert h4["stock_months"] > 0 and h4["mean"] > 0


def test_isolated_missing_bars_are_tolerated_and_every_exclusion_is_counted():
    rows = garch_rows(41)
    gap = {"2017-03-15"}
    spy = [r for r in garch_rows(42, start=300.0) if r["date"] not in {"2018-05-01", "2018-05-02", "2018-05-03"}]
    built = vr.panel({"A": [r for r in rows if r["date"] not in gap]}, spy, {}, "2020-12-31")
    months = {r["month"] for r in built["rows"]}
    assert "2017-04" in months and "2017-12" in months        # one missing bar does not drop a year
    assert "2018-05" not in months and built["dropped"].get("SPY_HISTORY") == 1
    holed = [r for r in rows if not ("2017-03-01" <= r["date"] <= "2017-03-31")]
    assert vr.panel({"A": holed}, garch_rows(42, start=300.0), {}, "2020-12-31")["dropped"]["STOCK_HISTORY"] > 0


def test_run_reads_prices_read_only_and_stays_in_the_dev_window(pg):  # noqa: F811
    with pg() as conn:
        with conn.cursor() as cur:
            for ticker, data in (("VZ", garch_rows(7)), ("T", garch_rows(8)), ("SPY", garch_rows(9, start=300.0))):
                cur.executemany("INSERT INTO prices_daily VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                                [(ticker, r["date"], r["open"], r["high"], r["low"], r["close"], r["adj_close"],
                                  r["volume"]) for r in data])
        conn.commit()
        conn.read_only = True
        out = vr.run(conn, tickers=["VZ", "T"], stage="dev", end="2020-12-31")
    assert out["result"]["stage"] == "dev" and set(out["result"]["splits"]) == {"dev"}
    assert out["rows"] and all(vr.split_of(r["month"]) == "dev" for r in out["rows"])
    assert out["protocol"]["protocol_id"] == "volatility-forecast.v1" and "UNTRAINED" in out["result"]["excluded"]
