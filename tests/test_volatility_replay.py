"""Volatility-forecast replay: point-in-time features, purged refits, QLIKE, stage gates."""

import math
import random

import pytest

from stock_machine.agents.contracts import digest
from stock_machine.backtest import volatility_replay as vr
from stock_machine.market_calendar import session_dates
from tests.test_research_postgres import pg  # noqa: F401  (Postgres fixture)

PINNED_PROTOCOL_SHA256 = "e7e87d68d8c2df39325c10621b90bb02d59b2c00b3cc67a85e5cd70436485371"
SESSIONS = session_dates("2012-01-03", "2018-12-31")


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


def test_the_protocol_is_hashed_and_the_holdout_waits_for_registration():
    assert vr.PROTOCOL_SHA256 == digest(vr.PROTOCOL) == PINNED_PROTOCOL_SHA256   # change => new protocol_id
    assert vr.holdout_ready() == (False, "PROTOCOL_NOT_REGISTERED_FOR_HOLDOUT")
    assert vr.run(None, tickers=[], stage="holdout", end="2026-10-19")["status"] == "REFUSED"
    with pytest.raises(ValueError):
        vr.run(None, tickers=[], stage="peek", end="2026-10-19")
    assert set(vr.PROTOCOL["hypotheses"]) == set(vr.PROTOCOL["family"]["hypotheses"]) == {"H1", "H2", "H3", "H4"}


def test_measurements():
    assert vr.realized_vol([0.01] * 21) == pytest.approx(math.sqrt(252) * 0.01)
    assert vr.realized_vol([0.01] * 20 + [None]) is None and vr.realized_vol([]) is None
    bars = [{"high": 101.0, "low": 99.0}] * 21
    assert vr.parkinson_vol(bars) == pytest.approx(math.sqrt(252 * math.log(101 / 99) ** 2 / (4 * math.log(2))))
    assert vr.ewma_vol([0.02] * 252) == pytest.approx(math.sqrt(252) * 0.02)
    assert vr.qlike(1.0, 1.0) == 0.0 and vr.qlike(2.0, 1.0) > 0 and vr.qlike(0.5, 1.0) > 0
    assert vr.qlike(1.0, 0.5) > vr.qlike(1.0, 2.0)          # under-forecasting variance is punished more


def test_the_expected_release_uses_last_years_window_only():
    reactions = ["2025-11-05", "2026-02-03"]
    assert vr.expected_release(reactions, "2026-10-30")          # 2025-10-31 < 2025-11-05 <= 2025-11-30
    assert not vr.expected_release(reactions, "2026-09-30")
    assert not vr.expected_release(["2026-11-04"], "2026-10-30")  # a future release is never used


def test_features_use_only_data_through_the_decision_close():
    rows, spy = garch_rows(1), garch_rows(2, start=300.0)
    series, spy_series = vr.Series(rows, SESSIONS), vr.Series(spy, SESSIONS)
    i = series.index["2015-06-30"]
    before = vr.observe(series, spy_series, i, [], [])
    altered = [dict(r, close=r["close"] * 3, adj_close=r["adj_close"] * 3, high=r["high"] * 3, low=r["low"] * 3)
               if r["date"] > "2015-06-30" else r for r in rows]
    after = vr.observe(vr.Series(altered, SESSIONS), spy_series, i, [], [])
    assert before == after and set(vr.PLUS_FEATURES) <= set(before)
    assert vr.realized_vol(series.future(i)) != vr.realized_vol(vr.Series(altered, SESSIONS).future(i))


def test_refits_use_only_targets_completed_before_the_decision(monkeypatch):
    rows = vr.panel({"A": garch_rows(3), "B": garch_rows(4)}, garch_rows(5, start=300.0), {}, "2018-12-31")
    seen = []
    real = vr._ols

    def spy_ols(train, feats):
        seen.append(max(r["window_end"] for r in train))
        return real(train, feats)
    monkeypatch.setattr(vr, "_ols", spy_ols)
    scored = vr.forecasts(rows)
    decisions = sorted({s["decision"] for s in scored if "HAR" in s["forecast_var"]})
    assert decisions and len(seen) >= len(decisions)
    fitted = sorted({r["decision"] for r in rows if r["target"]})
    for decision, last_target in zip([d for d in fitted if d >= decisions[0]], seen[::2]):
        assert last_target <= decision                          # purged: no window ends after the decision


def test_the_standard_model_beats_last_months_volatility_when_volatility_clusters():
    prices = {f"S{k}": garch_rows(10 + k, start=40.0 + k) for k in range(6)}
    rows = vr.panel(prices, garch_rows(99, start=300.0, alpha=0.05, beta=0.93), {}, "2018-12-31")
    result = vr.evaluate([s for s in vr.forecasts(rows) if vr.split_of(s["month"]) == "dev"], stage="dev")
    dev = result["splits"]["dev"]
    assert set(result["splits"]) == {"dev"} and "verdict" not in dev["hypotheses"]["H1"]
    assert dev["months"] >= 30 and dev["hypotheses"]["H1"]["mean"] > 0          # HAR beats RV21
    assert dev["mean_qlike"]["HAR"] < dev["mean_qlike"]["RV21"]
    assert 0.5 < dev["forecast_to_realized"]["HAR"] < 2.0


def quarterly_releases():
    """The first session on or after the 5th of February, May, August and November: four a year."""
    out = []
    for year in range(2012, 2019):
        for month in ("02", "05", "08", "11"):
            out.append(min(d for d in SESSIONS if d >= f"{year}-{month}-05"))
    return set(out)


def test_the_earnings_term_learns_scheduled_event_volatility():
    jumps = {f"S{k:02d}": quarterly_releases() for k in range(12)}
    prices = {t: garch_rows(20 + k, jumps=jumps[t]) for k, t in enumerate(sorted(jumps))}
    earnings = {t: sorted(jumps[t]) for t in jumps}
    rows = vr.panel(prices, garch_rows(98, start=300.0), earnings, "2018-12-31")
    assert any(r["x"]["earn"] == 1.0 and r["x"]["earn_size"] > 0 for r in rows)
    dev = vr.evaluate([s for s in vr.forecasts(rows) if vr.split_of(s["month"]) == "dev"], stage="dev")["splits"]["dev"]
    assert dev["hypotheses"]["H4"]["stock_months"] > 0 and dev["hypotheses"]["H4"]["mean"] > 0


def test_run_reads_prices_read_only_and_stays_in_the_dev_window(pg):  # noqa: F811
    with pg() as conn:
        with conn.cursor() as cur:
            for ticker, data in (("VZ", garch_rows(7)), ("T", garch_rows(8)), ("SPY", garch_rows(9, start=300.0))):
                cur.executemany("INSERT INTO prices_daily VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                                [(ticker, r["date"], r["open"], r["high"], r["low"], r["close"], r["adj_close"],
                                  r["volume"]) for r in data])
        conn.commit()
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        conn.commit()
        out = vr.run(conn, tickers=["VZ", "T"], stage="dev", end="2018-12-31")
    assert out["result"]["stage"] == "dev" and set(out["result"]["splits"]) == {"dev"}
    assert out["rows"] and all(vr.split_of(r["month"]) == "dev" for r in out["rows"])
    assert out["protocol"]["protocol_id"] == "volatility-forecast.v1"
