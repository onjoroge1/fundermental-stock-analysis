"""Implied-volatility comparison: exact-date joins, purged calibration, the variance book, gates, local runs."""

import importlib.util
import json
import math
from pathlib import Path

import pytest

from stock_machine.agents.contracts import AGENT_UNIVERSE, digest
from stock_machine.backtest import implied_vol_replay as ivr
from stock_machine.backtest import volatility_replay as vr

PINNED_PROTOCOL_SHA256 = "e3935098bfbe8d0f79bf620c404ad928b394c337bfddb3091ba48ff5ef31b3c8"
PINNED_BEHAVIOR_SHA256 = "2c8564ec4d7d0318bedcf02135c8458ae5ee156d1f91e99b529cf18f51e679d1"
ROOT = Path(__file__).resolve().parents[1]


def test_the_protocol_builds_on_the_registered_volatility_replay_and_is_pinned():
    assert ivr.PROTOCOL_SHA256 == digest(ivr.PROTOCOL) == PINNED_PROTOCOL_SHA256
    assert ivr.behavior_digest() == ivr.BEHAVIOR_SHA256 == PINNED_BEHAVIOR_SHA256
    assert ivr.PROTOCOL["builds_on"]["protocol_sha256"] == vr.REGISTERED_PROTOCOL_SHA256 == vr.PROTOCOL_SHA256
    assert ivr.CONFIRMATORY == ("V3", "V4") and ivr.PROTOCOL["family"]["t_critical"] == vr.T7_FAMILY
    assert ivr.PROTOCOL["universe"] == sorted(AGENT_UNIVERSE)


def test_the_holdout_refuses_until_registered(monkeypatch):
    universe = sorted(AGENT_UNIVERSE)
    assert ivr.holdout_ready(universe) == (False, "PROTOCOL_NOT_REGISTERED_FOR_HOLDOUT")
    monkeypatch.setattr(ivr, "REGISTERED_PROTOCOL_SHA256", ivr.PROTOCOL_SHA256)
    assert ivr.holdout_ready(universe) == (True, "READY")
    assert ivr.holdout_ready(universe[:5]) == (False, "UNIVERSE_NOT_REGISTERED")
    monkeypatch.setattr(ivr, "BEHAVIOR_SHA256", "0" * 64)
    assert ivr.holdout_ready(universe) == (False, "COMPARISON_CODE_CHANGED")
    monkeypatch.setattr(vr, "BEHAVIOR_SHA256", "0" * 64)
    assert ivr.holdout_ready(universe) == (False, "VOLATILITY_REPLAY_CHANGED")


def row(ticker, iv, realized_vol, har_vol, month="2018-03", earn=0.0):
    return {"ticker": ticker, "month": month, "decision": month + "-30", "iv": iv, "earn": earn,
            "realized_var": realized_vol ** 2, "har_var": har_vol ** 2, "har_log": math.log(har_vol),
            "window_end": month + "-30"}


def test_variance_payoffs_include_the_cost_on_each_side():
    r = row("A", 0.30, 0.25, 0.25)
    assert ivr.short_payoff(r) == pytest.approx(1 - 0.0625 / 0.29 ** 2)
    assert ivr.long_payoff(r) == pytest.approx(0.0625 / 0.31 ** 2 - 1)
    assert ivr.short_payoff(row("B", 0.30, 0.30, 0.3)) < 0 < ivr.short_payoff(row("C", 0.30, 0.20, 0.3)) + 1


def test_the_book_shorts_rich_implied_vol_and_buys_cheap_and_skips_thin_months():
    rows = [row(f"S{k}", 0.30, 0.30, 0.20 + 0.02 * k) for k in range(9)]   # our forecast rises with k
    rich, cheap = rows[:3], rows[-3:]                                     # IV richest vs forecast at low k
    expected = (sum(ivr.short_payoff(r) for r in rich) / 3) + (sum(ivr.long_payoff(r) for r in cheap) / 3)
    assert ivr.book(rows) == pytest.approx(expected)
    assert ivr.book(rows[:8]) is None


def test_the_join_uses_implied_vol_at_exactly_the_decision_session():
    prices, spy, earnings, iv = ivr.behavior_fixture()
    joined = ivr.join(prices, spy, earnings, iv, "2019-12-31")
    sample = joined["rows"][0]
    assert sample["iv"] == iv[sample["ticker"]][sample["decision"]]
    shifted = {t: {d: v for d, v in series.items() if not d.endswith(("-28", "-29", "-30", "-31"))}
               for t, series in iv.items()}
    fewer = ivr.join(prices, spy, earnings, shifted, "2019-12-31")
    assert fewer["dropped"]["NO_IV"] > 0 and len(fewer["rows"]) < len(joined["rows"])   # never filled from earlier


def test_calibration_is_purged_and_needs_a_year_of_joined_data(monkeypatch):
    prices, spy, earnings, iv = ivr.behavior_fixture()
    rows = ivr.join(prices, spy, earnings, iv, "2019-12-31")["rows"]
    seen = []
    real = ivr._fit

    def spy_fit(train, features):
        seen.append(max(r["window_end"] for r in train))
        return real(train, features)
    monkeypatch.setattr(ivr, "_fit", spy_fit)
    out = ivr.calibrated(rows)
    decisions = sorted({r["decision"] for r in out})
    assert decisions and all(last <= d for last, d in zip(seen[::2], decisions))
    first_joined = min(r["month"] for r in rows)
    assert min(r["month"] for r in out) > first_joined                  # the first year only trains
    assert all({"IV_RAW", "IV_CAL", "HAR_PLUS", "COMBINED"} <= set(r["var"]) for r in out)


def test_an_informed_market_is_not_mistaken_for_our_edge():
    prices, spy, earnings, iv = ivr.behavior_fixture()
    rows = ivr.calibrated(ivr.join(prices, spy, earnings, iv, "2019-12-31")["rows"])
    dev = ivr.evaluate(rows, stage="dev")["splits"]["dev"]
    assert dev["hypotheses"]["V2"]["mean"] < 0             # the fixture's IV knows half the future: it wins
    assert dev["hypotheses"]["V4"]["mean"] < 0             # and the book loses after costs
    assert dev["hypotheses"]["V1"]["mean"] > 0             # the 15% premium is harvested by short variance
    assert "verdict" not in dev["hypotheses"]["V3"]


def _write_inputs(tmp_path):
    prices, spy, _, iv = ivr.behavior_fixture()
    pdir, vdir = tmp_path / "prices", tmp_path / "iv"
    pdir.mkdir()
    vdir.mkdir()
    for t, rows in {**prices, "SPY": spy}.items():
        (pdir / f"{t}.json").write_text(json.dumps({"ticker": t, "rows": rows}))
    for t, series in iv.items():
        rows = [{"date": d, "close": v} for d, v in sorted(series.items())]
        (vdir / f"{t}.json").write_text(json.dumps({"ticker": t, "series": {"OPTION_IMPLIED_VOLATILITY": {"rows": rows}}}))
    return pdir, vdir, sorted(prices)


def test_a_local_run_records_input_hashes_and_never_returns_values_in_the_result(tmp_path, monkeypatch):
    pdir, vdir, tickers = _write_inputs(tmp_path)
    monkeypatch.setattr(vr, "earnings_events", lambda: ivr.behavior_fixture()[2])
    out = ivr.run(prices_dir=pdir, iv_dir=vdir, stage="dev", end="2019-12-31", tickers=tickers)
    result = out["result"]
    assert set(result["splits"]) == {"dev"} and len(result["inputs"]["implied_vol_sha256"]) == 64
    assert "rows" not in result and "CALIBRATION_UNTRAINED" in result["excluded"]
    text = json.dumps(result)
    some_iv = next(iter(ivr.behavior_fixture()[3]["S0"].values()))
    assert str(round(some_iv, 6))[:8] not in text
    missing = ivr.run(prices_dir=pdir, iv_dir=vdir, stage="dev", end="2019-12-31", tickers=tickers + ["ZZZ"])
    assert missing["status"] == "REFUSED" and missing["missing"] == ["ZZZ"]


def test_the_price_fetcher_joins_adjusted_closes_without_borrowing_raw_ones(tmp_path):
    spec = importlib.util.spec_from_file_location("fetch_ibkr_prices", ROOT / "scripts" / "fetch_ibkr_prices.py")
    fetch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch)
    trades = [{"date": "2024-01-02", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 5.0},
              {"date": "2024-01-03", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 6.0}]
    adjusted = [{"date": "2024-01-02", "close": 9.5}]
    merged = fetch.merge(trades, adjusted)
    assert merged[0]["adj_close"] == 9.5 and merged[1]["adj_close"] is None

    class Session:
        calls = []

        def daily_bars(self, symbol, what, duration):
            self.calls.append((symbol, what))
            return (trades if what == "TRADES" else adjusted), []
    s = Session()
    fetch.run(s, ["AAPL"], tmp_path, log=lambda *_: None)
    fetch.run(s, ["AAPL"], tmp_path, log=lambda *_: None)
    assert s.calls == [("AAPL", "TRADES"), ("AAPL", "ADJUSTED_LAST")]             # resumed, not refetched
    text = (ROOT / "scripts" / "fetch_ibkr_prices.py").read_text()
    assert "placeOrder" not in text and "db.connect" not in text
