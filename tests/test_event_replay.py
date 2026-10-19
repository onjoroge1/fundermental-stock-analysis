"""Event-strategy replay: pre-registered stages, one trade at a time, v5 book."""

import math

import pytest

from stock_machine.agent_intelligence import strategy_signals as ss
from stock_machine.agents.contracts import digest
from stock_machine.backtest import direction_replay as dr
from stock_machine.backtest import event_replay as er
from stock_machine.market_calendar import session_dates
from tests.test_research_postgres import pg  # noqa: F401  (Postgres fixture)

SESSIONS = session_dates("2018-01-02", "2020-12-31")


def rows_for(drift, wobble, start, *, seed=0):
    rows, price = [], start
    for i, day in enumerate(SESSIONS):
        price *= 1 + drift + wobble * math.sin((i + seed) / 4)
        volume = 3_000_000 if i % 97 == 0 else 1_000_000
        rows.append({"date": day, "open": price * 0.995, "high": price * 1.01, "low": price * 0.985,
                     "close": price, "adj_close": price, "volume": volume})
    return rows


SPY = rows_for(0.0003, 0.004, 300.0)


def test_protocol_is_pinned_to_the_strategy_rules():
    assert er.PROTOCOL_SHA256 == digest(er.PROTOCOL)
    assert er.STRATEGY_BEHAVIOR_SHA256 == ss.behavior_digest()
    assert er.PROTOCOL["family"]["hypotheses"] == list(er.CONFIRMATORY)
    assert er.split_of("2023-12-29") == "dev" and er.split_of("2024-01-02") == "holdout"
    assert er.split_of("2026-09-01") is None and er.split_of("2015-12-31") is None


def test_the_holdout_refuses_until_the_protocol_is_complete(monkeypatch):
    assert er.holdout_ready() == (False, "EARNINGS_DATA_NOT_PINNED")
    assert er.run(None, tickers=[], stage="holdout", end="2026-10-19")["status"] == "REFUSED"
    monkeypatch.setattr(er, "EARNINGS_ANNOUNCEMENTS_SHA256", "a" * 64)
    monkeypatch.setattr(er, "STRATEGY_BEHAVIOR_SHA256", "b" * 64)
    assert er.holdout_ready() == (False, "STRATEGY_BEHAVIOR_CHANGED")
    with pytest.raises(ValueError):
        er.run(None, tickers=[], stage="peek", end="2026-10-19")


def trades(strategy="S2_trend_pullback", entry=ss.s2_entry, rows=None, end="2020-12-31"):
    rows = rows or rows_for(0.0015, 0.012, 50.0)
    bars, spy_bars = ss.Bars(rows, end, SESSIONS), ss.Bars(SPY, end, SESSIONS)
    return er.trades_for("X", rows=rows, spy_rows=SPY, bars=bars, spy_bars=spy_bars, strategy=strategy,
                         entry=entry, stage_end=er.DEV[1]), bars


def test_one_trade_at_a_time_and_each_scored_with_the_replay_reward():
    from stock_machine.agent_intelligence.features import compute
    out, bars = trades()
    scored = [t for t in out if t["status"] == "SCORED"]
    assert scored
    for a, b in zip(scored, scored[1:]):
        assert b["signal"] >= a["exit"]                         # no new trade before the last exit
    t = scored[0]
    features = compute(rows_for(0.0015, 0.012, 50.0), as_of=t["signal"], market_rows=SPY)["features"]
    expected = dr.rewards_between(t["entry"], t["exit"], {"features": features},
                                  stock=dr._adjusted(rows_for(0.0015, 0.012, 50.0)), spy=dr._adjusted(SPY),
                                  horizon=ss.RULES["S2_trend_pullback"]["max_hold"])
    assert t["reward"] == pytest.approx(expected["rewards"]["LONG"])
    assert t["entry"] == bars.sessions[bars.index[t["signal"]] + 1]   # next-close entry


def test_a_missing_bar_excludes_the_trade_and_locks_the_stock():
    rows = rows_for(0.0015, 0.012, 50.0)
    out, bars = trades(rows=rows)
    first = next(t for t in out if t["status"] == "SCORED")
    gap = bars.sessions[bars.index[first["entry"]] + 3]
    out2, _ = trades(rows=[r for r in rows if r["date"] != gap])
    hit = next(t for t in out2 if t["signal"] == first["signal"])
    assert hit["status"] == "EXCLUDED_MISSING_BARS"
    later = [t for t in out2 if t["signal"] > hit["signal"]]
    lock_end = bars.sessions[bars.index[hit["signal"]] + 1 + ss.RULES["S2_trend_pullback"]["max_hold"]]
    assert all(t["signal"] >= lock_end for t in later)


def test_the_book_divides_by_eligible_stocks_and_counts_empty_months_as_zero():
    t = [{"status": "SCORED", "signal": "2019-01-15", "reward": 0.6},
         {"status": "SCORED", "signal": "2019-01-20", "reward": -0.2},
         {"status": "EXCLUDED_MISSING_BARS", "signal": "2019-02-04"}]
    assert er.book(t, {"2019-01": 4, "2019-02": 4}, ["2019-01", "2019-02"]) == [pytest.approx(0.1), 0.0]


def test_confirmatory_hold_out_uses_the_family_quantile():
    from stock_machine.agent_intelligence import inference
    values = [0.02 * (1 + (m % 3)) for m in range(32)]
    family = er.interval(values, er.Z_FAMILY)
    plain = er.interval(values)
    assert family["lower"] < plain["lower"]
    assert family["mean"] - family["lower"] == pytest.approx(
        inference.t_quantile(31, er.Z_FAMILY) * inference.newey_west_se(values, lag=er.HAC_LAG))


def test_dev_evaluation_never_reports_holdout_numbers():
    out, _ = trades()
    result = er.evaluate(out, {f"{y}-{m:02d}": 10 for y in (2018, 2019, 2020) for m in range(1, 13)}, stage="dev")
    for strategy in result["strategies"].values():
        assert set(strategy) == {"dev"}
    assert result["strategies"]["S2_trend_pullback"]["dev"]["trades"] > 0


def test_run_reads_the_database_and_stays_in_the_dev_window(pg):  # noqa: F811
    from stock_machine import db
    rows = rows_for(0.0015, 0.012, 50.0)
    with pg() as conn:
        with conn.cursor() as cur:
            for ticker, data in (("VZ", rows), ("SPY", SPY)):
                cur.executemany("INSERT INTO prices_daily VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                                [(ticker, r["date"], r["open"], r["high"], r["low"], r["close"], r["adj_close"],
                                  r["volume"]) for r in data])
        conn.commit()
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        conn.commit()
        out = er.run(conn, tickers=["VZ"], stage="dev", end="2020-12-31")
    assert out["result"]["stage"] == "dev"
    assert out["result"]["strategies"]["S3_post_earnings"] == {"status": "PENDING_EARNINGS_DATA"}
    assert out["trades"] and all(er.split_of(t["signal"]) == "dev" for t in out["trades"])
    assert db is not None
