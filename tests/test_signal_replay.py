"""Candidate-signal replay: strict point-in-time signals, pre-registered tests."""

import pytest

from stock_machine.agents.contracts import digest
from stock_machine.backtest import direction_replay as dr
from stock_machine.backtest import signal_replay as sr
from tests.test_direction_replay import SPY, series
from tests.test_research_postgres import pg  # noqa: F401  (Postgres fixture)


def test_protocol_is_hashed_and_builds_on_the_direction_replay():
    assert sr.PROTOCOL_SHA256 == digest(sr.PROTOCOL)
    assert sr.PROTOCOL["base_sha256"] == dr.PROTOCOL_SHA256


def test_surprise_is_known_only_after_its_observation_and_expires():
    rows = [{"date": "2016-01-28", "surprise_pct": 6.0, "available_at": "2016-01-29T13:00:00+00:00"}]
    assert sr.surprise_signal(rows, "2016-01-29") is None        # not yet known at that close
    assert sr.surprise_signal(rows, "2016-02-29") == 6.0
    assert sr.surprise_signal(rows, "2016-06-30") is None        # older than 92 days
    late = [{"date": "2016-01-28", "surprise_pct": 6.0, "available_at": "2026-10-01T00:00:00+00:00"}]
    assert sr.surprise_signal(late, "2016-02-29") is None        # observed in 2026: never backfilled


def test_revision_uses_only_snapshots_available_by_the_session():
    def snap(day, eps):
        return {"snapshot_date": day, "available_at": day + "T21:00:00+00:00", "period_type": "quarter",
                "forecast_period_end": "2016-06-30", "eps_mean": eps, "revenue_mean": 100.0, "source": "s"}
    rows = [snap("2016-01-04", 1.00), snap("2016-02-26", 1.05)]
    assert sr.revision_signal(rows, "2016-02-29") == pytest.approx(5.0)
    assert sr.revision_signal(rows, "2016-02-25") is None  # one snapshot only


def test_value_gap_is_growth_delivered_minus_growth_priced():
    detail = {"market_cap": 1_000.0, "net_debt": 0.0, "fcf_ttm": 50.0, "revenue_cagr_3y_pct": 10.0}
    gap = sr.value_gap(detail)
    implied = sr.implied_growth_from_price(1_000.0, 0.0, 50.0)["implied_cagr_pct"]
    assert gap == 10.0 - implied
    assert sr.value_gap({**detail, "fcf_ttm": -5.0}) is None   # undefined growth: never guessed
    assert sr.value_gap({**detail, "revenue_cagr_3y_pct": None}) is None


def test_terciles_need_enough_names():
    gaps = {f"T{i}": float(i) for i in range(9)}
    split = sr._terciles(gaps)
    assert [split[f"T{i}"] for i in range(9)] == ["SHORT"] * 3 + ["FLAT"] * 3 + ["LONG"] * 3
    assert set(sr._terciles({"A": 1.0, "B": 2.0}).values()) == {"FLAT"}


def universe():
    out = {}
    for i in range(9):
        drift = (i - 4) * 0.0008
        gap = float((i * 5) % 9)  # value gap unrelated to the price trend
        out[f"T{i}"] = {
            "prices": series(drift, start=50 + 10 * i), "sector_rows": None,
            "observe": lambda as_of, g=gap: {"composite": 70, "detail": {
                "market_cap": 1_000.0, "net_debt": 0.0, "fcf_ttm": 50.0, "revenue_cagr_3y_pct": g}},
            "surprises": [], "consensus": [],
        }
    return out


def test_value_timed_enters_only_when_technicals_agree():
    rows = sr.replay(universe(), spy=SPY, sessions=dr.month_end_sessions("2015-06-01", "2016-10-31"))
    assert rows and all(r["surprise"] == "FLAT" and r["revision"] == "FLAT" for r in rows)
    for r in rows:
        if r["value_timed"] != "FLAT":
            assert r["value_timed"] == r["value_gap"]
            assert (r["technical_signal"] > 0) == (r["value_gap"] == "LONG")
    assert any(r["value_gap"] != "FLAT" and r["value_timed"] == "FLAT" for r in rows)


def test_sparse_signals_are_insufficient_not_passed():
    rows = sr.replay(universe(), spy=SPY, sessions=dr.month_end_sessions("2015-06-01", "2016-10-31"))
    result = sr.evaluate(rows)
    assert result["hypotheses"]["H4_surprise"]["verdict"] == "INSUFFICIENT_COVERAGE"
    assert result["coverage"]["surprise"]["active_decisions"] == 0
    # Fewer than 24 active months in this short fixture: no verdict can pass.
    assert result["hypotheses"]["H6_value_gap"]["verdict"] == "INSUFFICIENT_COVERAGE"
    assert result["coverage"]["value_gap"]["active_months"] > 0


def test_signal_run_reads_the_database(pg):
    rows = series(0.002)
    with pg() as conn:
        with conn.cursor() as cur:
            for ticker, data in (("VZ", rows), ("SPY", SPY)):
                cur.executemany("INSERT INTO prices_daily VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                                [(ticker, r["date"], r["open"], r["high"], r["low"], r["close"], r["adj_close"],
                                  r["volume"]) for r in data])
        conn.commit()
        out = sr.run(conn, end="2016-12-30", tickers=["VZ"])
    assert out["result"]["decisions"] == len(out["rows"]) > 0
    assert all(r[p] == "FLAT" for r in out["rows"] for p in sr.POLICIES)  # no filings or estimates stored
