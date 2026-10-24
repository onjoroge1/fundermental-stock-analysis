"""EXT-FADE.v1 overlays: the flags and conditions on hand-built stock-days, the missing-data rules, the weights, the
pass bars, the stress tables, and every refusal of the one-time runner. Synthetic data only."""

import copy
import hashlib
import importlib.util
import json
import math
import subprocess
from pathlib import Path

import pytest

from stock_machine.backtest import ext_fade_overlays as ov
from stock_machine.backtest import implied_vol_replay as ivr
from stock_machine.backtest import intraday_lab as lab
from stock_machine.backtest import intraday_replay as ir

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = json.loads(ov.REGISTRATION_PATH.read_text())
S0, P, S = "2021-03-08", "2021-03-09", "2021-03-10"    # the session before the prior, the prior session, the session


# ---- one stock-day, every flag met unless a test changes it -----------------------------------------------------

def world():
    """One v1 trade (T1 on S, a 2% gap up after a 3.1% up day) and what the overlays read for it."""
    f = {"prior_date": P, "prior_close": 100.0, "prior_close_raw": 100.0, "close_before_prior": 97.0, "atr14": 2.0,
         "sigma_d": 0.02, "open": 102.0}
    prices = {"T1": [{"date": S0, "open": 96.0, "high": 97.5, "low": 95.5, "close": 97.0, "adj_close": 97.0},
                     {"date": P, "open": 97.5, "high": 101.0, "low": 99.0, "close": 100.0, "adj_close": 100.0},
                     {"date": S, "open": 102.0, "high": 102.5, "low": 100.5, "close": 101.0, "adj_close": 101.0}],
              "SPY": [{"date": S0, "open": 300.0, "high": 301.0, "low": 299.0, "close": 300.0, "adj_close": 300.0},
                      {"date": P, "open": 300.0, "high": 300.5, "low": 296.0, "close": 297.0, "adj_close": 297.0},
                      {"date": S, "open": 297.0, "high": 299.0, "low": 296.5, "close": 298.0, "adj_close": 298.0}]}
    days = {"features": {("T1", S): f}, "closes": {("T1", P): (99.0, 100.0)}, "eligible": {P: 1, S: 1}}
    vols = {"iv": {"T1": {S0: 0.33, P: 0.35}, "SPY": {S0: 0.15, P: 0.16}}, "hv": {"T1": {S0: 0.30, P: 0.30}}}
    trade = {"ticker": "T1", "session": S, "gap": 102.0 / 100.0 - 1, "gross_bps": 30.0,
             "net_bps": {0: 30.0, 2: 26.0, 5: 20.0, 10: 10.0}}
    return {"trades": [trade], "days": days, "prices": prices, "vols": vols}


def one(w):
    return ov.features(w["trades"], w["days"], w["prices"], w["vols"])[0]


def test_every_flag_met_gives_s5_five_and_the_largest_weight():
    r = one(world())
    assert r["flags"] == {"prior_move": 1, "gap": 1, "prior_range_atr": 1, "prior_leg": 1, "iv": 1}
    assert r["s5"] == 5 and r["w_sw"] == pytest.approx(5 / 3.186) and round(r["w_sw"], 2) == 1.57
    assert r["inputs"]["prior_move"] == pytest.approx(100 / 97 - 1) and r["inputs"]["prior_range_atr"] == 1.0
    assert r["inputs"]["prior_leg"] == pytest.approx(100 / 99 - 1)
    assert r["conditions"] == {"iv_over_hv": 1, "spy_iv_change": 1, "iv": 1} and r["keep_ivr"] == 1
    assert r["keep_p2"] == 1 and r["pxc"] == 2 and r["iv_ge_hv"] is True


def test_each_flag_turns_off_at_its_cut_off():
    w = world()
    w["days"]["features"][("T1", S)]["close_before_prior"] = 98.1          # yesterday +1.9%
    assert one(w)["flags"]["prior_move"] == 0 and one(w)["keep_p2"] == 0
    w = world()
    w["days"]["features"][("T1", S)]["open"] = 101.4                       # a 1.4% gap
    w["trades"][0]["gap"] = 101.4 / 100.0 - 1
    assert one(w)["flags"]["gap"] == 0
    w = world()
    w["days"]["features"][("T1", S)]["atr14"] = 1000.0
    w["prices"]["T1"][1].update(high=1879.0, low=1000.0)                   # exactly 0.879 of the ATR: met
    assert one(w)["inputs"]["prior_range_atr"] == 0.879 and one(w)["flags"]["prior_range_atr"] == 1
    w["prices"]["T1"][1].update(high=1878.0)
    assert one(w)["flags"]["prior_range_atr"] == 0
    w = world()
    w["days"]["closes"][("T1", P)] = (100.0, 100.0)                        # flat from 10:29: met (at least 0)
    assert one(w)["flags"]["prior_leg"] == 1
    w["days"]["closes"][("T1", P)] = (100.0, 99.9)                         # the late session went against the gap
    assert one(w)["flags"]["prior_leg"] == 0
    w = world()
    w["vols"]["iv"]["T1"][P] = 0.2805
    assert one(w)["flags"]["iv"] == 1
    w["vols"]["iv"]["T1"][P] = 0.28
    r = one(w)
    assert r["flags"]["iv"] == 0 and r["conditions"]["iv"] == 1            # SW cuts at 28.05%, IVR at 28%
    w["vols"]["iv"]["T1"][P] = 0.2799
    assert one(w)["conditions"]["iv"] == 0


def test_a_gap_down_is_scored_the_same_way_with_the_leg_signed_by_the_gap():
    w = world()
    f = w["days"]["features"][("T1", S)]
    f.update(close_before_prior=103.0, open=98.0)                          # down 2.9% yesterday, a 2% gap down
    w["trades"][0]["gap"] = 98.0 / 100.0 - 1
    w["days"]["closes"][("T1", P)] = (101.0, 100.0)                        # 10:29 to the close fell: the gap's way
    r = one(w)
    assert r["flags"]["prior_move"] == 1 and r["flags"]["gap"] == 1 and r["flags"]["prior_leg"] == 1
    assert r["inputs"]["prior_leg"] == pytest.approx(-(100 / 101 - 1)) and r["keep_p2"] == 1


def test_missing_inputs_make_their_flags_and_conditions_zero():
    w = world()
    del w["days"]["closes"][("T1", P)]                                     # the prior session was not eligible
    assert one(w)["flags"]["prior_leg"] == 0 and one(w)["inputs"]["prior_leg"] is None
    w = world()
    w["days"]["closes"][("T1", P)] = (None, 100.0)                         # its 10:29 minute did not trade
    assert one(w)["flags"]["prior_leg"] == 0
    w = world()
    w["days"]["features"][("T1", S)]["atr14"] = None
    assert one(w)["flags"]["prior_range_atr"] == 0
    w = world()
    del w["prices"]["T1"][1]                                               # no daily row for the prior session
    assert one(w)["flags"]["prior_range_atr"] == 0
    w = world()
    del w["vols"]["iv"]["T1"][P]                                       # no IV at the prior close: never an older one
    r = one(w)
    assert r["flags"]["iv"] == 0 and r["conditions"]["iv"] == 0 and r["conditions"]["iv_over_hv"] == 0
    assert r["iv_ge_hv"] is None and r["s5"] == 4
    w = world()
    del w["vols"]["hv"]["T1"][P]
    assert one(w)["conditions"]["iv_over_hv"] == 0 and one(w)["iv_ge_hv"] is None
    w = world()
    del w["vols"]["iv"]["SPY"][P]
    assert one(w)["conditions"]["spy_iv_change"] == 0
    w = world()
    del w["vols"]["iv"]["SPY"][S0]                                         # no SPY IV before the prior close
    assert one(w)["conditions"]["spy_iv_change"] == 0
    w = world()
    w["days"]["features"][("T1", S)]["sigma_d"] = None
    assert one(w)["pxc"] == 1
    w = world()
    del w["prices"]["SPY"][0]                                              # no SPY row before the prior session
    assert one(w)["pxc"] == 1


def test_the_iv_regime_needs_two_of_three_and_its_edges():
    w = world()
    w["vols"]["hv"]["T1"][P] = 0.35                                        # IV equal to HV: met
    assert one(w)["conditions"]["iv_over_hv"] == 1
    w["vols"]["hv"]["T1"][P] = 0.36
    r = one(w)
    assert r["conditions"]["iv_over_hv"] == 0 and r["ivr_met"] == 2 and r["keep_ivr"] == 1
    w["vols"]["iv"]["SPY"][P] = 0.15                                       # SPY IV unchanged: not met (strictly above)
    r = one(w)
    assert r["conditions"]["spy_iv_change"] == 0 and r["ivr_met"] == 1 and r["keep_ivr"] == 0


def test_the_spy_iv_change_uses_the_observation_just_before_the_prior_close_and_nothing_later():
    w = world()
    w["vols"]["iv"]["SPY"] = {"2021-03-01": 0.30, S0: 0.17, P: 0.16, S: 0.50}   # S is the session itself: unseen
    r = one(w)
    assert r["inputs"]["spy_iv_change"] == pytest.approx(0.16 / 0.17 - 1) and r["conditions"]["spy_iv_change"] == 0
    del w["vols"]["iv"]["SPY"][S0]                                         # a gap in SPY's series: the one before it
    assert one(w)["inputs"]["spy_iv_change"] == pytest.approx(0.16 / 0.30 - 1)


def test_the_price_only_stand_in_uses_spys_adjusted_close():
    w = world()
    w["prices"]["SPY"][1].update(close=299.0, adj_close=301.0)         # ex-dividend: the raw close fell, adjusted rose
    r = one(w)
    assert r["inputs"]["spy_prior_return"] == pytest.approx(301 / 300 - 1) and r["pxc"] == 1
    w["days"]["features"][("T1", S)]["sigma_d"] = 0.28 / math.sqrt(252) * 0.99
    assert one(w)["pxc"] == 0


def test_features_refuse_a_trade_that_is_not_point_in_time_or_not_the_labs():
    w = world()
    w["trades"][0]["session"] = "2021-03-11"
    with pytest.raises(ValueError, match="NOT_AN_ELIGIBLE_STOCK_DAY"):
        one(w)
    w = world()
    w["trades"][0]["gap"] = 0.021
    with pytest.raises(ValueError, match="GAP_DISAGREES"):
        one(w)
    w = world()
    w["days"]["features"][("T1", S)]["prior_date"] = S
    with pytest.raises(ValueError, match="NOT_POINT_IN_TIME"):
        one(w)


def test_the_stock_day_pass_judges_eligibility_exactly_as_the_lab_does():
    bars_for, prices, events, tickers, _ = ir.behavior_fixture()
    run = lab.replay(bars_for, prices, ir.BEHAVIOR_MONTHS, tickers, {ov.BASE: ov.BASE_SPEC}, split="confirm",
                     events=events)
    days = ov.stock_days(bars_for, prices, ir.BEHAVIOR_MONTHS, tickers)
    assert run["trades"] and days["eligible"] == run["eligible"]
    assert set(days["features"]) == set(days["closes"]) and len(days["features"]) == sum(run["eligible"].values())
    vols = {"iv": {t: {r["date"]: 0.3 for r in prices[t]} for t in tickers + ["SPY"]}, "hv": {}}
    rows = ov.features(run["trades"], days, prices, vols)
    assert [(r["ticker"], r["session"]) for r in rows] == [(t["ticker"], t["session"]) for t in run["trades"]]
    first = min(days["eligible"])
    assert all(r["inputs"]["prior_leg"] is None for r in rows if r["session"] == first)   # its prior was not judged
    with pytest.raises(ValueError, match="HOLDOUT"):
        ov.stock_days(bars_for, prices, ("2023-12", "2024-01"), tickers)


# ---- weights, statistics and the pass bars -----------------------------------------------------------------------

def test_the_weights_are_s5_over_3186_and_evaluation_normalises_by_the_periods_mean():
    assert [round(s / ov.S_BAR, 2) for s in range(6)] == [0, 0.31, 0.63, 0.94, 1.26, 1.57]
    rows = [row(f"2020-01-{d:02d}", "A", 10.0, s5=s) for d, s in zip(range(2, 8), (0, 1, 2, 3, 4, 5))]
    norm, w_bar = ov.sw_weights(rows)
    assert w_bar == pytest.approx(2.5 / 3.186) and math.fsum(norm) / len(norm) == pytest.approx(1.0)
    assert norm[0] == 0.0 and norm[5] == pytest.approx(2.0)
    nets = [r["net_bps"][5] for r in rows]
    gain = [(w - 1) * x for w, x in zip(norm, nets)]
    assert math.fsum(gain) / len(gain) == pytest.approx(0.0)                # equal nets: sizing adds nothing


def test_the_jackknife_matches_lab_session_t_and_drops_a_session_together():
    xs = [3.0, -1.0, 4.0, 1.0, 5.0, -2.0, 6.0, 2.0]
    cl = ["a", "a", "b", "c", "c", "d", "e", "e"]
    part = ov._mean_part(xs, cl, ["2020"] * 8, ("2020",), {"mean_above": 0.0, "t_above": 2.0})
    assert part["t"] == lab.session_t(xs, cl)
    kept = [True, False, True, False, True, False, True, False]
    d = (3 + 4 + 5 + 6) / 4 - (-1 + 1 - 2 + 2) / 4
    reps = []
    for g in "abcde":
        k = [x for x, c, m in zip(xs, cl, kept) if c != g and m]
        r = [x for x, c, m in zip(xs, cl, kept) if c != g and not m]
        reps.append(sum(k) / len(k) - sum(r) / len(r))
    expected = d / math.sqrt(4 / 5 * sum((x - d) ** 2 for x in reps))
    assert ov.diff_t(xs, kept, cl) == pytest.approx(expected)
    assert ov.diff_t([1.0, 2.0], [True, False], ["a", "a"]) is None            # one session: no t
    assert ov.diff_t([1.0, 2.0, 3.0], [True, True, False], ["a", "b", "b"]) is None   # removing b empties a side


def test_t_bars_are_strict_or_not_as_written():
    assert not ov._t_ok(2.0, {"t_above": 2.0}) and ov._t_ok(2.0001, {"t_above": 2.0})
    assert ov._t_ok(2.0, {"t_at_least": 2.0}) and not ov._t_ok(None, {"t_at_least": 2.0})
    assert PASSES_STRICT == {(ov.SW, "standalone"), (ov.P2, "kept"), (ov.IVR, "kept")}


PASSES_STRICT = {(sid, part) for sid, bars in ov.PASS_BARS.items() for part, bar in bars.items()
                 if isinstance(bar, dict) and "t_above" in bar}


def row(session, ticker, net5, *, s5=3, p2=1, ivr=1, pxc=1, iv_ge_hv=True):
    return {"ticker": ticker, "session": session, "month": session[:7], "year": session[:4], "gross_bps": net5 + 10,
            "net_bps": {0: net5 + 10, 2: net5 + 6, 5: net5, 10: net5 - 10}, "s5": s5, "w_sw": s5 / ov.S_BAR,
            "keep_p2": p2, "keep_ivr": ivr, "pxc": pxc, "iv_ge_hv": iv_ge_hv}


def book(sign_by_year=None):
    """Four years of trades: S5 = 5, P2- and IVR-kept trades earn well; S5 = 1, removed trades lose a little."""
    sign_by_year = sign_by_year or {}
    rows = []
    for y in ov.YEARS:
        for m in range(1, 13):
            for d in (3, 10, 17):
                k = (int(y) * 37 + m * 5 + d) % 7 - 3                    # deterministic noise
                good = 60.0 + 6 * k
                bad = -10.0 + 4 * k
                s = sign_by_year.get(y, 1)
                rows.append(row(f"{y}-{m:02d}-{d:02d}", "A", s * good, s5=5, p2=1, ivr=1, pxc=1, iv_ge_hv=True))
                rows.append(row(f"{y}-{m:02d}-{d:02d}", "B", bad, s5=1, p2=0, ivr=0, pxc=0, iv_ge_hv=False))
    return rows


def sessions_of(rows):
    return sorted({r["session"] for r in rows} | {"2020-02-01", "2021-02-01"})      # two days without trades


def test_the_pass_bars_pass_a_clear_book_and_fail_when_a_part_fails():
    rows = book()
    out = ov.evaluate(rows, sessions_of(rows))
    for sid in (ov.SW, ov.P2, ov.IVR):
        assert out[sid]["verdict"] == "PASS", sid
    assert out[ov.SW]["gain"]["positive_years"] == 4 and out[ov.SW]["w_bar"] == pytest.approx(3 / ov.S_BAR)
    assert out[ov.P2]["kept"]["lab_check_bar"]["pass"] and out[ov.P2]["kept_trades"] == len(rows) // 2
    lab_bar = lab.pass_bar([{"session": r["session"], "month": r["month"], "net_bps": r["net_bps"]}
                            for r in rows if r["keep_p2"]], "check")
    assert out[ov.P2]["kept"]["lab_check_bar"]["t"] == lab_bar["session_t"]       # the lab's own bar on 2020-2023
    assert out[ov.IVR]["secondary"]["verdict"] == "PASS" and out[ov.IVR]["secondary"]["among"] == len(rows)
    two_bad_years = book({"2021": -1, "2023": -1})                 # the good trades lose in two of the four years
    out = ov.evaluate(two_bad_years, sessions_of(two_bad_years))
    assert out[ov.SW]["verdict"] == "FAIL" and out[ov.SW]["gain"]["positive_years"] == 2
    assert out[ov.IVR]["verdict"] == "FAIL" and out[ov.IVR]["kept"]["positive_years"] == 2
    assert out[ov.P2]["verdict"] == "FAIL" and not out[ov.P2]["kept"]["pass"]


def test_a_filter_that_keeps_everything_ties_v1s_sharpe_and_one_that_drops_winners_fails_it():
    rows = [dict(r, keep_p2=1) for r in book()]
    out = ov.evaluate(rows, sessions_of(rows))
    s = out[ov.P2]["book_sharpe"]
    assert s["overlay"] == s[ov.BASE] and s["pass"]                  # at least v1's: a tie passes
    assert out[ov.P2]["kept_minus_removed"]["mean"] is None and not out[ov.P2]["kept_minus_removed"]["pass"]
    rows = [dict(r, keep_ivr=0 if r["ticker"] == "A" else 1) for r in book()]
    assert not ov.evaluate(rows, sessions_of(rows))[ov.IVR]["book_sharpe"]["pass"]


def test_the_book_sharpe_counts_sessions_without_trades_as_zero():
    rows = [row("2020-01-02", "A", 10.0), row("2020-01-02", "B", 20.0), row("2020-01-03", "A", -5.0)]
    pnl = [30.0, -5.0, 0.0, 0.0]
    m = sum(pnl) / 4
    sd = math.sqrt(sum((x - m) ** 2 for x in pnl) / 3)
    got = ov.book_sharpe(rows, [1.0] * 3, 5, ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"])
    assert got == pytest.approx(m / sd * math.sqrt(252))
    assert ov.net(row("2020-01-02", "A", 10.0), 15) == 10.0 + 10 - 30                     # 15 bps from the gross


def test_the_secondary_compares_iv_above_hv_within_the_price_only_stand_ins():
    rows = book()
    rows.append(row("2022-05-05", "C", 500.0, pxc=2, iv_ge_hv=True))     # PXC 2: outside the comparison
    rows.append(row("2022-05-05", "D", 500.0, pxc=0, iv_ge_hv=None))     # IV or HV unknown: outside too
    sec = ov.secondary(rows)
    assert sec["among"] == len(rows) - 2 and sec["kept"] == sec["removed"] == (len(rows) - 2) // 2
    assert not ov.PASS_BARS[ov.IVR]["secondary"]["gating"]


def test_the_stress_tables_cover_every_book_and_drop_what_they_say():
    rows = book()
    late = [dict(r, net_bps={k: v - 3 for k, v in r["net_bps"].items()}, gross_bps=r["gross_bps"] - 3) for r in rows]
    out = ov.stress(rows, late)
    assert list(out) == list(ov.BOOKS)
    v1 = out[ov.BASE]
    assert v1["at_5"]["trades"] == len(rows) and v1["at_15"]["mean"] == pytest.approx(v1["at_5"]["mean"] - 20)
    assert v1["entry_0932_at_5"]["mean"] == pytest.approx(v1["at_5"]["mean"] - 3)
    assert v1["without_best_20_sessions"]["trades"] == len(rows) - 40
    assert v1["best_and_worst_20_sessions_trimmed"]["trades"] == len(rows) - 80
    top = out[ov.P2]["without_top_3_stocks"]
    assert top["stocks"] == ["A", "B"] and top["trades"] == 0
    standalone = ov.evaluate(rows, sessions_of(rows))[ov.SW]["standalone"]
    assert out[ov.SW]["at_5"]["mean"] == pytest.approx(standalone["mean"])
    assert set(out[ov.IVR]["by_year"]) == set(ov.YEARS) and out[ov.IVR]["by_year"]["2020"]["at_10"]["trades"] == 36
    assert out[ov.IVR]["at_5"]["trades"] == len(rows) // 2


# ---- the one-time runner -------------------------------------------------------------------------------------------

def synthetic_check():
    """One stock and SPY over the check months (and the lab's warm-up and detection months), with 2% gaps that
    extend a 1.2% day often enough for v1 to trade."""
    sessions = ir.fixture_sessions("2019-08-01", "2023-12-31")
    bars, prices = {}, {}
    for k, t in enumerate(["T1", "SPY"]):
        rows, close, prev = [], 50.0 + 300 * k, 0.0
        bars[t] = {}
        for i, s in enumerate(sessions):
            gap = (0.02 if prev > 0 else -0.02) if t == "T1" and i % 3 == 0 and abs(prev) >= 0.012 else 0.0
            o = round(close * (1 + gap), 2)
            day = ir.synthetic_day(1000 * k + i, o, 0.0)
            bars[t][s] = day
            rows.append({"date": s, "open": o, "high": max(day["h"]), "low": min(day["l"]), "close": day["c"][-1],
                         "adj_close": day["c"][-1], "volume": 1e6})
            prev, close = day["c"][-1] / close - 1, day["c"][-1]
        rows.append({**rows[-1], "date": "2024-01-02"})                    # the hold-out's first day: never used
        prices[t] = rows
    return sessions, bars, prices


@pytest.fixture
def check_world(tmp_path, monkeypatch):
    sessions, bars, prices = synthetic_check()

    def load_month(root, ticker, month):
        assert month <= "2023-12", "read a hold-out month"
        return {s: d for s, d in bars[ticker].items() if s[:7] == month}
    monkeypatch.setattr(ir, "load_month", load_month)
    monkeypatch.setattr(ir, "missing_months", lambda root, tickers, months: [])
    monkeypatch.setattr(ir, "files_sha", lambda root, months=None: "b" * 64)
    prices_dir = tmp_path / "prices"                  # real files: the lab's code hash covers load_prices' source
    prices_dir.mkdir()
    for t, rows in prices.items():
        (prices_dir / f"{t}.json").write_text(json.dumps({"ticker": t, "rows": rows}))
    monkeypatch.setattr(ir, "DAILY_PRICES_SHA256", ivr.load_prices(prices_dir)[1])
    iv_dir = tmp_path / "iv"
    iv_dir.mkdir()
    for k, t in enumerate(["T1", "SPY"]):
        series = {name: {"rows": [{"date": s, "close": 0.2 + 0.1 * k + 0.05 * ((i * (3 + k)) % 5) / 4 + base}
                                  for i, s in enumerate(sessions + ["2024-01-02"])]}
                  for name, base in ((ov.IV_SERIES, 0.02), (ov.HV_SERIES, 0.0))}
        (iv_dir / f"{t}.json").write_text(json.dumps({"ticker": t, "series": series}))
    (iv_dir / "summary.json").write_text("{}")
    _, vol_sha = ov.load_vols(iv_dir, ov.CHECK_LAST_DAY)
    run = lab.replay(lambda t, m: load_month(None, t, m), prices, ov.CHECK, ["T1"], {ov.BASE: ov.BASE_SPEC},
                     split="check", events={})
    v1 = run["trades"]
    check = {"entry": 21, "split": "check", "months": list(ov.CHECK), "variant": ov.BASE, "spec": ov.BASE_SPEC,
             "spec_sha256": lab.spec_sha(ov.BASE_SPEC), "lab_code_sha256": lab.CODE_SHA, "bars_sha256": "b" * 64,
             "inputs_sha256": lab.inputs_sha(["T1"]), "trades": len(v1),
             "net_bps_at_10": lab._mean([t["net_bps"][10] for t in v1]), "verdict": "PASS"}
    confirm = {**check, "entry": 17, "split": "confirm", "months": list(lab.CONFIRM)}
    registration = {"module_sha256": ov.module_sha(), "runner_sha256": ov.file_sha(ov.RUNNER_PATH),
                    "specs": {sid: {"spec_sha256": ov.spec_sha(sid)} for sid in ov.SPECS},
                    "lab_code_sha256": lab.CODE_SHA, "iv_hv": {"sha256": vol_sha}}
    w = {"tmp": tmp_path, "iv_dir": iv_dir, "prices_dir": prices_dir, "check": check, "confirm": confirm,
         "registration": registration, "log": tmp_path / "overlays-log.json", "trades": v1}
    w["write"] = lambda: (
        (tmp_path / "registration.json").write_text(json.dumps(w["registration"])),
        (tmp_path / "lab-log.json").write_text(json.dumps([w["confirm"], w["check"]])))
    w["run"] = lambda **kw: (w["write"](), ov.run_check(
        dev_root=tmp_path, prices_dir=prices_dir, iv_dir=iv_dir, tickers=["T1"],
        registration_path=tmp_path / "registration.json", lab_log_path=tmp_path / "lab-log.json",
        log_path=w["log"], runner_path=kw.get("runner_path", ov.RUNNER_PATH)))[1]
    return w


def test_the_one_check_runs_logs_each_overlay_and_never_runs_twice(check_world):
    w = check_world
    out = w["run"]()
    assert out["status"] == "OK", out
    log = ov.read_log(w["log"])
    assert [e["spec_id"] for e in log] == [ov.SW, ov.P2, ov.IVR] and [e["entry"] for e in log] == [1, 2, 3]
    assert all(e["split"] == "check" and e["v1_check_entry"] == 21 and e["verdict"] in ("PASS", "FAIL") for e in log)
    assert {e["spec_sha256"] for e in log} == {ov.spec_sha(s) for s in ov.SPECS}
    assert out["result"]["trades"] == len(w["trades"]) and out["v1_check"]["trades"] == len(w["trades"])
    assert set(out["stress"]) == set(ov.BOOKS) and out["stress"][ov.BASE]["entry_0932_at_5"]["trades"] > 0
    text = ov.render(out)
    assert "the one check" in text and ov.SW in text and "secondary" in text
    again = w["run"]()
    assert again == {"status": "REFUSED", "overlay_id": ov.OVERLAY_ID, "reason": "ALREADY_CHECKED"}
    assert ov.read_log(w["log"]) == log                                     # a refusal is never logged
    spec = importlib.util.spec_from_file_location("run_ext_fade_overlays", ov.RUNNER_PATH)
    importlib.util.module_from_spec(spec)


def test_the_runner_refuses_unless_v1s_check_is_reproduced_exactly(check_world):
    w = check_world
    w["check"]["trades"] += 1
    assert w["run"]()["reason"] == "V1_CHECK_NOT_REPRODUCED"
    w["check"]["trades"] -= 1
    w["check"]["net_bps_at_10"] += 1e-9
    assert w["run"]()["reason"] == "V1_CHECK_NOT_REPRODUCED"
    assert not w["log"].exists()
    assert ov.v1_reproduced(w["trades"], {"trades": len(w["trades"]),
                                          "net_bps_at_10": lab._mean([t["net_bps"][10] for t in w["trades"]])})


def test_the_runner_refuses_wrong_hashes(check_world, tmp_path):
    w = check_world
    good = copy.deepcopy(w["registration"])
    w["registration"]["module_sha256"] = "0" * 64
    assert w["run"]()["reason"] == "MODULE_NOT_REGISTERED"
    w["registration"] = {**good, "runner_sha256": "0" * 64}
    assert w["run"]()["reason"] == "RUNNER_NOT_REGISTERED"
    other_runner = tmp_path / "runner.py"
    other_runner.write_text(ov.RUNNER_PATH.read_text() + "\n# edited\n")
    w["registration"] = good
    assert w["run"](runner_path=other_runner)["reason"] == "RUNNER_NOT_REGISTERED"
    w["registration"] = {**good, "specs": {**good["specs"], ov.P2: {"spec_sha256": "0" * 64}}}
    assert w["run"]()["reason"] == "SPECS_NOT_REGISTERED"
    w["registration"] = {**good, "iv_hv": {"sha256": "0" * 64}}
    assert w["run"]()["reason"] == "IV_HV_NOT_REGISTERED"
    w["registration"] = {**good, "lab_code_sha256": "0" * 64}
    assert w["run"]()["reason"] == "LAB_CODE_NOT_REGISTERED"
    w["registration"] = good
    (w["tmp"] / "registration.json").unlink()
    assert ov.run_check(dev_root=tmp_path, prices_dir=w["prices_dir"], iv_dir=w["iv_dir"], tickers=["T1"],
                        registration_path=tmp_path / "registration.json", lab_log_path=tmp_path / "lab-log.json",
                        log_path=w["log"])["reason"] == "NOT_REGISTERED"
    assert not w["log"].exists()


def test_the_runner_refuses_without_v1s_check_or_with_other_lab_code(check_world, monkeypatch):
    w = check_world
    check = w["check"]
    w["check"] = {**check, "split": "explore"}                             # the lab log holds no check of v1
    assert w["run"]()["reason"] == "NO_V1_CHECK_IN_THE_LAB_LOG"
    other = {**ov.BASE_SPEC, "gap": 0.02}
    w["check"] = {**check, "spec": other, "spec_sha256": lab.spec_sha(other)}
    assert w["run"]()["reason"] == "NO_V1_CHECK_IN_THE_LAB_LOG"           # a check of another strategy is not v1's
    w["check"] = {**check, "lab_code_sha256": "0" * 64}
    assert w["run"]()["reason"] == "LAB_CODE_IS_NOT_V1_CHECK_CODE"
    w["check"] = {**check, "inputs_sha256": lab.inputs_sha(["T1", "T2"])}
    assert w["run"]()["reason"] == "INPUTS_ARE_NOT_V1_CHECK_INPUTS"
    w["check"] = {**check, "bars_sha256": "c" * 64}
    assert w["run"]()["reason"] == "BARS_ARE_NOT_V1_CHECK_BARS"
    w["check"] = check
    monkeypatch.setattr(lab, "CODE_SHA", "0" * 64)
    assert w["run"]()["reason"] == "LAB_CODE_CHANGED_SINCE_IMPORT"
    assert not w["log"].exists()


def test_the_runner_refuses_a_second_check_of_any_of_the_specs_whatever_its_id(check_world):
    w = check_world
    for sid in ov.SPECS:
        earlier = [{"entry": 1, "split": "check", "spec_id": "renamed", "spec_sha256": ov.spec_sha(sid)}]
        ov.write_log(earlier, w["log"])
        assert w["run"]()["reason"] == "ALREADY_CHECKED"
        assert ov.read_log(w["log"]) == earlier
    ov.write_log([{"entry": 1, "split": "check", "spec_sha256": "0" * 64}], w["log"])   # another spec's check
    assert w["run"]()["status"] == "OK"


def test_the_check_reads_nothing_after_2023():
    with pytest.raises(ValueError, match="HOLDOUT"):
        ov.load_vols(ROOT, "2024-01-01")
    assert ov.CHECK == ("2020-01", "2023-12") and ov.CHECK_LAST_DAY < ov.HOLDOUT_FROM
    assert ov.cut_prices({"X": [{"date": "2023-12-29"}, {"date": "2024-01-02"}]}, ov.CHECK_LAST_DAY) == \
        {"X": [{"date": "2023-12-29"}]}


# ---- what is frozen ----------------------------------------------------------------------------------------------

def test_the_four_protected_files_and_the_lab_code_are_unchanged():
    assert lab.CODE_SHA.startswith("518b5557") and lab.lab_code_sha() == lab.CODE_SHA
    confirm = [e for e in lab.read_log() if e["split"] == "confirm" and e["variant"] == ov.BASE]
    assert len(confirm) == 1 and confirm[0]["lab_code_sha256"] == lab.lab_code_sha()
    assert ir.behavior_digest() == ir.BEHAVIOR_SHA256
    for path in ov.PROTECTED:
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == REGISTRATION["protected_files"][path], path


def test_the_four_protected_files_are_byte_identical_to_origin_main():
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if git("rev-parse", "--verify", "--quiet", "origin/main").returncode != 0:
        pytest.skip("no origin/main in this checkout (a shallow clone); the pinned hashes still apply")
    for path in ov.PROTECTED:
        main = git("show", f"origin/main:{path}")
        assert main.returncode == 0 and (ROOT / path).read_bytes() == main.stdout, path


def test_the_registration_matches_the_code():
    reg = REGISTRATION
    assert reg["module_sha256"] == ov.module_sha() and reg["runner_sha256"] == ov.file_sha(ov.RUNNER_PATH)
    assert reg["module_sha256"] not in ov.Path(ov.__file__).read_text()            # the module never holds its own hash
    assert {sid: v["spec_sha256"] for sid, v in reg["specs"].items()} == {sid: ov.spec_sha(sid) for sid in ov.SPECS}
    assert {sid: v["spec"] for sid, v in reg["specs"].items()} == json.loads(json.dumps(ov.SPECS))
    assert reg["pass_bars"] == json.loads(json.dumps(ov.PASS_BARS))
    assert reg["stress"] == json.loads(json.dumps(ov.STRESS))
    assert reg["lab_code_sha256"] == lab.CODE_SHA
    assert reg["iv_hv"]["sha256"] == ivr.REGISTERED_INPUTS["implied_vol_sha256"]
    assert reg["daily_prices_sha256"] == ir.DAILY_PRICES_SHA256
    assert reg["variants"] == {"this_round": ov.VARIANTS_THIS_ROUND, "lab_log": lab.variants_tried(lab.read_log()),
                               "total": ov.VARIANTS_THIS_ROUND + lab.variants_tried(lab.read_log())}
    assert reg["variants"]["this_round"] == 1413 and reg["variants"]["lab_log"] == 16


def test_the_recorded_calibration_reproduces_the_research_synthesis():
    spec = importlib.util.spec_from_file_location("calibrate", ROOT / "scripts" / "calibrate_ext_fade_overlays.py")
    calibrate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(calibrate)
    expected = {"mean_s5": 3.186, "v1_trades": 1900, "sw_gain_H1": 10.1, "sw_gain_H2": 11.4, "sw_gain_all": 10.9,
                "p2_kept": 1016, "p2_kept_mean_at_10": 32.1, "ivr_kept": 1112, "ivr_kept_mean_at_5": 43.3,
                "ivr_kept_minus_removed_at_5": 44.1}
    assert {k: v[0] for k, v in calibrate.EXPECTED.items()} == expected
    got = REGISTRATION["calibration"]["reproduction"]
    assert {k: v["expected"] for k, v in got.items()} == expected
    assert all(v["match"] and round(v["got"], calibrate.EXPECTED[k][1]) == v["expected"] for k, v in got.items())
    assert REGISTRATION["calibration"]["last_day_read"] == "2019-12-31"
    assert REGISTRATION["calibration"]["module_sha256"] == ov.module_sha()
