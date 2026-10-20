"""Implied-volatility comparison: exact-date joins, purged calibration, the variance book, gates, local runs."""

import importlib.util
import json
import math
from pathlib import Path

import pytest

from stock_machine.agents.contracts import AGENT_UNIVERSE, digest
from stock_machine.backtest import implied_vol_replay as ivr
from stock_machine.backtest import volatility_replay as vr

PINNED_PROTOCOL_SHA256 = "50f81997e6e72271eb3d133375034843e8b1066cf7a9f388eb525cec2e478509"
PINNED_BEHAVIOR_SHA256 = "9276ba8306e0f721e0abd071b4b4199ded441965e44fea110bb963d59f506ee2"
ROOT = Path(__file__).resolve().parents[1]


def test_the_protocol_builds_on_the_registered_volatility_replay_and_is_pinned():
    assert ivr.PROTOCOL_SHA256 == digest(ivr.PROTOCOL) == PINNED_PROTOCOL_SHA256
    assert ivr.behavior_digest() == ivr.BEHAVIOR_SHA256 == PINNED_BEHAVIOR_SHA256
    assert ivr.PROTOCOL["builds_on"]["protocol_sha256"] == vr.REGISTERED_PROTOCOL_SHA256 == vr.PROTOCOL_SHA256
    assert ivr.CONFIRMATORY == ("V3", "V4") and ivr.PROTOCOL["family"]["t_critical"] == vr.T7_FAMILY
    assert ivr.PROTOCOL["universe"] == sorted(AGENT_UNIVERSE)


def test_the_protocol_and_its_inputs_are_registered_and_the_holdout_is_ready_on_this_code():
    assert ivr.REGISTERED_PROTOCOL_SHA256 == PINNED_PROTOCOL_SHA256 == ivr.PROTOCOL_SHA256
    assert set(ivr.REGISTERED_INPUTS) == {"prices_sha256", "implied_vol_sha256"}
    assert all(len(v) == 64 for v in ivr.REGISTERED_INPUTS.values())
    assert ivr.holdout_ready(sorted(AGENT_UNIVERSE)) == (True, "READY")


def test_the_holdout_refuses_unless_registered(monkeypatch):
    universe = sorted(AGENT_UNIVERSE)
    monkeypatch.setattr(ivr, "REGISTERED_PROTOCOL_SHA256", None)
    assert ivr.holdout_ready(universe) == (False, "PROTOCOL_NOT_REGISTERED_FOR_HOLDOUT")
    monkeypatch.setattr(ivr, "REGISTERED_PROTOCOL_SHA256", "a" * 64)
    assert ivr.holdout_ready(universe) == (False, "PROTOCOL_CHANGED_SINCE_REGISTRATION")
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
    # Our forecast rises with k; realized volatility does too, so the right legs earn and flipped legs lose.
    rows = [row(f"S{k}", 0.30, 0.20 + 0.02 * k, 0.20 + 0.02 * k) for k in range(9)]
    rich, cheap = rows[:3], rows[-3:]                                     # IV richest vs forecast at low k
    expected = (sum(ivr.short_payoff(r) for r in rich) / 3) + (sum(ivr.long_payoff(r) for r in cheap) / 3)
    flipped = (sum(ivr.long_payoff(r) for r in rich) / 3) + (sum(ivr.short_payoff(r) for r in cheap) / 3)
    assert ivr.book(rows) == pytest.approx(expected) and expected > 0 > flipped
    assert ivr.book(list(reversed(rows))) == pytest.approx(expected)         # ranking, not input order
    assert ivr.book(rows[:8]) is None


def test_the_join_uses_implied_vol_at_exactly_the_decision_session():
    prices, spy, earnings, iv = ivr.behavior_fixture()
    joined = ivr.join(prices, spy, earnings, iv, "2019-12-31")
    sample = joined["rows"][0]
    assert sample["iv"] == iv[sample["ticker"]][sample["decision"]]
    shifted = {t: {d: v for d, v in series.items() if not d.endswith(("-28", "-29", "-30", "-31"))}
               for t, series in iv.items()}
    fewer = ivr.join(prices, spy, earnings, shifted, "2019-12-31")
    no_iv = sum(b.get("NO_IV", 0) for k, b in fewer["dropped"].items() if k != "panel")
    assert no_iv > 0 and len(fewer["rows"]) < len(joined["rows"])                   # never filled from earlier


def test_implausible_prints_and_spac_months_are_excluded_and_counted():
    series = {"2026-07-24": 0.29, "2026-07-27": 0.28, "2026-07-28": 0.30, "2026-07-29": 0.29, "2026-07-30": 0.29,
              "2026-07-31": 0.146}
    assert not ivr.plausible(series, "2026-07-31")                        # half the previous prints: a bad tick
    assert ivr.plausible({**series, "2026-07-31": 0.20}, "2026-07-31")    # an earnings crush is kept
    assert not ivr.plausible({"2021-01-07": 1.6e-5}, "2021-01-07") and not ivr.plausible({"x": 3.5}, "x")
    later = {**series, "2026-08-03": 0.01}
    assert ivr.plausible(later, "2026-07-30")                             # later prints are never used
    prices, spy, earnings, iv = ivr.behavior_fixture()
    renamed = {("HIMS" if t == "S0" else t): v for t, v in prices.items()}
    iv2 = {("HIMS" if t == "S0" else t): v for t, v in iv.items()}
    joined = ivr.join(renamed, spy, {("HIMS" if t == "S0" else t): v for t, v in earnings.items()}, iv2, "2019-12-31")
    assert not any(r["ticker"] == "HIMS" for r in joined["rows"])          # all fixture months precede 2021-01-21
    assert sum(b.get("PRE_LISTING", 0) for k, b in joined["dropped"].items() if k != "panel") > 0


def test_the_confirmatory_rule_needs_the_family_wise_bound_and_v3_needs_the_raw_quote_too():
    clear, between, crash = (vr.interval(v, vr.T7_FAMILY) for v in ivr.VERDICT_CASES)
    assert vr.interval(ivr.VERDICT_CASES[1], vr.T7_975)["lower"] > 0 > between["lower"]
    assert ivr.verdict(clear) == "SUPPORTED" and ivr.verdict(between) == "NOT_SUPPORTED"
    assert ivr.verdict(crash) == "NOT_SUPPORTED"                          # one large month is not enough
    assert ivr.verdict(clear, between) == "NOT_SUPPORTED" and ivr.verdict(clear, clear) == "SUPPORTED"
    out = ivr.robustness([0.30, -0.05, 0.01])
    assert out["median_month"] == 0.01 and out["mean_without_largest_month"] == pytest.approx(-0.02)


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
    def completed(decision):
        return len({r["month"] for r in rows if r["window_end"] and r["window_end"] <= decision})
    all_decisions = sorted({r["decision"] for r in rows})
    first = all_decisions.index(decisions[0])
    assert completed(decisions[0]) >= ivr.MIN_TRAIN_MONTHS > completed(all_decisions[first - 1])
    assert all(isinstance(r["combined_har_coef"], float) for r in out)
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
    assert "rows" not in result and "CALIBRATION_UNTRAINED" in result["excluded"]["dev"]
    assert all(ivr.split_of(r["month"]) == "dev" for r in out["rows"])          # no hold-out rows, even locally
    text = json.dumps(result)
    assert not any(repr(r["iv"]) in text or repr(r["realized_var"]) in text for r in out["rows"][:50])
    held = ivr.run(prices_dir=pdir, iv_dir=vdir, stage="holdout", end="2019-12-31", tickers=sorted(AGENT_UNIVERSE))
    assert held["status"] == "REFUSED" and held["reason"] == "INPUTS_CHANGED_SINCE_REGISTRATION"   # not the IBKR files
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


def test_a_registered_holdout_runs_only_on_the_registered_files(tmp_path, monkeypatch):
    pdir, vdir, tickers = _write_inputs(tmp_path)
    monkeypatch.setattr(vr, "earnings_events", lambda: ivr.behavior_fixture()[2])
    monkeypatch.setattr(ivr, "holdout_ready", lambda t: (True, "READY"))
    monkeypatch.setattr(ivr, "REGISTERED_INPUTS", None)
    refused = ivr.run(prices_dir=pdir, iv_dir=vdir, stage="holdout", end="2019-12-31", tickers=tickers)
    assert refused["reason"] == "INPUTS_NOT_REGISTERED"
    _, prices_sha = ivr.load_prices(pdir)
    _, iv_sha = ivr.load_iv(vdir)
    monkeypatch.setattr(ivr, "REGISTERED_INPUTS", {"prices_sha256": prices_sha, "implied_vol_sha256": iv_sha})
    first = ivr.run(prices_dir=pdir, iv_dir=vdir, stage="holdout", end="2019-12-31", tickers=tickers)
    again = ivr.run(prices_dir=pdir, iv_dir=vdir, stage="holdout", end="2019-12-31", tickers=tickers)
    assert first["result"] == again["result"]                     # deterministic: a rerun repeats the answer
    (vdir / "S1.json").write_text((vdir / "S2.json").read_text())
    changed = ivr.run(prices_dir=pdir, iv_dir=vdir, stage="holdout", end="2019-12-31", tickers=tickers)
    assert changed["reason"] == "INPUTS_CHANGED_SINCE_REGISTRATION"


def test_the_holdout_judges_v3_jointly_with_the_raw_quote_at_the_family_wise_bound():
    prices, spy, earnings, iv = ivr.behavior_fixture()
    rows = ivr.calibrated(ivr.join(prices, spy, earnings, iv, "2019-12-31")["rows"])
    months = sorted({r["month"] for r in rows})
    relabel = {m: f"{2024 + i // 12}-{i % 12 + 1:02d}" for i, m in enumerate(months)}
    held = ivr.evaluate([dict(r, month=relabel[r["month"]]) for r in rows], stage="holdout")["splits"]["holdout"]
    assert held["label"] == "PSEUDO_OUT_OF_SAMPLE"
    v3, v4, v1 = (held["hypotheses"][k] for k in ("V3", "V4", "V1"))
    assert v3["verdict"] == ivr.verdict(v3, v3["raw_quote_condition"]) and "verdict" in v4
    assert "verdict" not in v1 and "raw_quote_condition" not in v4                # descriptive rows are not judged
    assert v4["upper"] - v4["mean"] == pytest.approx(vr.T7_FAMILY * v4["block_se"])    # family-wise, not 95%
    assert v1["upper"] - v1["mean"] == pytest.approx(vr.T7_975 * v1["block_se"])
