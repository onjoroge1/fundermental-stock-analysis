"""Heuristic direction replay: live rule, point-in-time inputs, pre-registered tests."""

import math

import pytest

from tests.test_research_postgres import pg  # noqa: F401  (Postgres fixture)

from stock_machine.agents.contracts import digest
from stock_machine.backtest import direction_replay as dr
from stock_machine.market_calendar import session_dates, session_offset

SESSIONS = session_dates("2015-01-01", "2016-12-31")


def series(drift: float, wobble: float = 0.01, start: float = 100.0) -> list[dict]:
    rows, price = [], start
    for i, day in enumerate(SESSIONS):
        price *= 1 + drift + wobble * math.sin(i / 3)
        rows.append({"date": day, "open": price, "high": price * 1.01, "low": price * 0.99,
                     "close": price, "adj_close": price, "volume": 1_000_000})
    return rows


SPY = series(0.0003, 0.004)


def test_month_end_sessions_are_the_last_session_of_each_month():
    ends = dr.month_end_sessions("2016-01-01", "2016-03-31")
    assert ends == ["2016-01-29", "2016-02-29", "2016-03-31"]


def test_decision_uses_the_live_rule_and_no_later_prices():
    prices = series(0.002)
    session = "2016-03-31"
    a = dr.decide("UP", session, prices=prices, composite=85, spy=SPY, sector=None, regime=None)
    # Strong uptrend plus a high composite is bullish under state.assemble.
    assert a["heuristic"] == "LONG" and a["momentum"] == "LONG"
    assert a["fundamental_signal"] == pytest.approx(1.0)
    # Changing every price after the decision session changes nothing.
    future = [dict(r, adj_close=r["adj_close"] * (0.2 if r["date"] > session else 1),
                   close=r["close"] * (0.2 if r["date"] > session else 1)) for r in prices]
    b = dr.decide("UP", session, prices=future, composite=85, spy=SPY, sector=None, regime=None)
    assert {k: a[k] for k in dr.POLICIES} == {k: b[k] for k in dr.POLICIES}
    # A weak composite can flip the full heuristic but never the fundamental-free one.
    weak = dr.decide("UP", session, prices=prices, composite=20, spy=SPY, sector=None, regime=None)
    assert weak["no_fundamental"] == a["no_fundamental"]
    assert weak["bias"] < a["bias"]


def test_a_session_without_a_bar_or_with_short_history_is_skipped():
    prices = [r for r in series(0.002) if r["date"] != "2016-03-31"]
    assert dr.decide("X", "2016-03-31", prices=prices, composite=None, spy=SPY, sector=None, regime=None) is None
    short = series(0.002)[:30]
    assert dr.decide("X", short[-1]["date"], prices=short, composite=None, spy=SPY, sector=None,
                     regime=None) is None


def test_reward_is_the_live_v5_residual_over_the_frozen_window():
    session = "2016-03-31"
    stock = {r["date"]: r["adj_close"] for r in series(0.002)}
    spy = {r["date"]: r["adj_close"] for r in SPY}
    decision = {"features": {"beta_63_vs_spy": 1.5, "realized_vol_20": 0.30}}
    out = dr.rewards(session, decision, stock=stock, spy=spy)
    entry, due = session_offset(session, 1), session_offset(session_offset(session, 1), 20)
    assert (out["entry_session"], out["due_session"]) == (entry, due)
    residual = (stock[due] / stock[entry] - 1) - 1.5 * (spy[due] / spy[entry] - 1)
    scale = 0.30 * math.sqrt(20 / 252) * 100
    assert out["rewards"]["LONG"] == pytest.approx((residual * 100 - 0.2) / scale, abs=1e-5)
    assert out["rewards"]["SHORT"] == pytest.approx((-residual * 100 - 0.2) / scale, abs=1e-5)
    del stock[session_offset(entry, 5)]
    assert dr.rewards(session, decision, stock=stock, spy=spy) is None  # incomplete path


def test_replay_rows_and_pre_registered_verdicts():
    universe = {
        "UP": {"prices": series(0.002), "sector_rows": None, "observe": lambda d: {"composite": 85}},
        "DOWN": {"prices": series(-0.002, start=300), "sector_rows": None, "observe": lambda d: None},
    }
    rows = dr.replay(universe, spy=SPY, qqq=None, sessions=dr.month_end_sessions("2015-06-01", "2016-10-31"))
    assert rows and {r["ticker"] for r in rows} == {"UP", "DOWN"}
    assert all(r["session"] < r["entry_session"] < r["due_session"] for r in rows)
    result = dr.evaluate(rows)
    assert result["protocol_sha256"] == dr.PROTOCOL_SHA256 == digest(dr.PROTOCOL)
    h1 = result["hypotheses"]["H1_heuristic_vs_abstention"]
    assert h1["verdict"] in ("SUPPORTED", "NOT_SUPPORTED")
    assert h1["full"]["months"] == len({r["session"][:7] for r in rows})
    assert set(result["direction_mix"]) == set(dr.POLICIES)


def test_verdicts_follow_the_lower_bound():
    def rows(values):
        return [{"session": f"2016-{m:02d}-28", "ticker": "X", "heuristic": "LONG", "no_fundamental": "FLAT",
                 "momentum": "FLAT", "rewards": {"LONG": v, "SHORT": -v}} for m, v in values]
    good = dr.evaluate(rows([(m, 0.5 + 0.05 * (m % 3)) for m in range(1, 13)]))
    assert good["hypotheses"]["H1_heuristic_vs_abstention"]["verdict"] == "SUPPORTED"
    assert good["hypotheses"]["H3_heuristic_minus_no_fundamental"]["verdict"] == "SUPPORTED"
    noisy = dr.evaluate(rows([(m, (-1) ** m) for m in range(1, 13)]))
    assert noisy["hypotheses"]["H1_heuristic_vs_abstention"]["verdict"] == "NOT_SUPPORTED"
    assert dr.evaluate(rows([(1, 1.0), (2, 1.0)]))["hypotheses"]["H2_heuristic_minus_momentum"][
        "verdict"] == "INSUFFICIENT_MONTHS"


def test_run_reads_the_database_read_only(pg):
    rows = series(0.002)
    with pg() as conn:
        for ticker, data in (("VZ", rows), ("SPY", SPY)):
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO prices_daily VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(ticker, r["date"], r["open"], r["high"], r["low"], r["close"], r["adj_close"], r["volume"])
                     for r in data])
        conn.commit()
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        conn.commit()
        out = dr.run(conn, end="2016-12-30", tickers=["VZ"])
    result = out["result"]
    assert result["stocks"] == 1 and result["decisions"] == len(out["rows"]) > 0
    # No filings in the fixture: the fundamental signal is missing, never imputed.
    assert all(r["fundamental_signal"] is None for r in out["rows"])
    assert all(r["heuristic"] == r["no_fundamental"] for r in out["rows"])
