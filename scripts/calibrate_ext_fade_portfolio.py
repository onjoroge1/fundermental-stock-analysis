"""Calibrate EXT-FADE-CB.v1 on 2016-01..2019-12 before it is registered (local only, real data).

  python scripts/calibrate_ext_fade_portfolio.py [--dev-root ...] [--prices-dir ...] [--json out.json]

(stock_machine/backtest/ext_fade_portfolio.py)

Replays EXT-FADE.v1 over 2016-01..2019-12 with lab.replay(split='confirm'), unlogged, runs the module's own pass
over the stock-days, reconciles it with the lab (eligibility, candidates, every trade re-scored), sizes and hedges
the book, and checks that the module reproduces the frozen calibration record exactly (EXPECTED, from the designer's
preview and the reviewer's independent re-implementation). A mismatch is a bug in the module, never a reason to
change a constant. It also prints the in-sample dry run of the pass bar and the reported tables, and each half
(2016-17, 2018-19). They prove nothing: every constant was fixed before these months were previewed.

Uses nothing dated 2020-01-01 or later: the bar loader refuses later months, the daily price files are parsed whole
and cut at 2019-12-31 before any value is used (the lab's replay and the module's pass both get the cut rows), and
the lab's replay is not handed the 8-K event file (EXT-FADE.v1 does not read it). Never calls intraday_lab.run (it
appends to the lab's log) and writes nothing to the repository. No IV or HV data is read.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stock_machine.agents.contracts import AGENT_UNIVERSE  # noqa: E402
from stock_machine.backtest import ext_fade_portfolio as cb  # noqa: E402
from stock_machine.backtest import implied_vol_replay as ivr  # noqa: E402
from stock_machine.backtest import intraday_lab as lab  # noqa: E402
from stock_machine.backtest import intraday_replay as ir  # noqa: E402

# The frozen calibration record (EXT-FADE-CB.v1 spec, sections 2, 4, 5, 11 and 14), at the precision it was
# recorded: (value, decimals).
EXPECTED = {
    "candidates": (1905, 0), "candidates_that_trade": (1900, 0), "entry_beyond_target_skips": (5, 0),
    "late_excluded": (0, 0), "v1_trades": (1900, 0), "rescored_within_1e-6_bps": (1900, 0), "sessions": (1006, 0),
    "v1_total_at_5_c_spy_0": (47438.8985, 4),
    "netted_spy_cost_per_unit_v1": (1.371, 3), "netted_spy_cost_per_unit_cb": (1.367, 3),
    "unnetted_spy_cost_per_unit_v1": (1.437, 3),
    "cluster_binds_sessions": (36, 0), "group_binds_sessions": (0, 0), "book_binds_sessions": (12, 0),
    "mean_s": (0.920, 3), "share_s_below_1": (0.234, 3), "min_s": (0.309, 3),
    "k": (1.086957, 6),
    "v1_sharpe": (2.0019, 4), "v1_es5": (574.6364, 4), "v1_mdd": (-3756.4702, 4),
    "cb_sharpe": (2.0319, 4), "cb_es5": (612.3747, 4), "cb_mdd": (-3963.358, 4),
    "ub95_r_es": (1.1058, 4), "lb95_dsr": (-0.1203, 4),
}
DRY_RUN = {"G1": True, "G2": False, "G3": False, "G4": True, "verdict": "FAIL"}


def figures(result: dict) -> dict:
    c, b, bd, h = result["comparison"], result["comparison"]["bootstrap"], result["binding"], result["hedge"]
    kinds = result["candidates"]["kinds"]
    return {
        "candidates": result["candidates"]["count"], "candidates_that_trade": result["candidates"]["end_as_trades"],
        "entry_beyond_target_skips": kinds.get("ENTRY_BEYOND_TARGET", 0),
        "late_excluded": result["candidates"]["late_excluded"], "v1_trades": result["trades"],
        "rescored_within_1e-6_bps": result["reconciliation"]["rescored"]
        if result["reconciliation"]["max_residual_diff_bps"] <= 1e-6 else -1,
        "sessions": result["sessions"],
        "v1_total_at_5_c_spy_0": result["reconciliation"]["v1_total_c_spy_0"],
        "netted_spy_cost_per_unit_v1": h["spy_cost_per_unit"]["v1"],
        "netted_spy_cost_per_unit_cb": h["spy_cost_per_unit"]["cb"],
        "unnetted_spy_cost_per_unit_v1": h["unnetted_per_unit"]["v1"],
        "cluster_binds_sessions": bd["sessions_binding"]["cluster"],
        "group_binds_sessions": bd["sessions_binding"]["group"], "book_binds_sessions": bd["sessions_binding"]["book"],
        "mean_s": bd["positions"]["mean_s"], "share_s_below_1": bd["positions"]["share_below_1"],
        "min_s": bd["positions"]["min_s"], "k": result["k"],
        "v1_sharpe": c["v1"]["sharpe"], "v1_es5": c["v1"]["es5"], "v1_mdd": c["v1"]["mdd"],
        "cb_sharpe": c["cb"]["sharpe"], "cb_es5": c["cb"]["es5"], "cb_mdd": c["cb"]["mdd"],
        "ub95_r_es": b["ub95_r_es"], "lb95_dsr": b["lb95_dsr"],
    }


def in_period(session: str, period: tuple[str, str]) -> bool:
    return period[0] <= session[:7] <= period[1]


def calibrate(dev_root, prices_dir) -> dict:
    """The calibration report: the module on 2016-01..2019-12, its reproduction of EXPECTED, and each half."""
    t0 = time.time()
    last = cb.CALIBRATION_LAST_DAY
    raw, prices_sha = ivr.load_prices(prices_dir)
    prices = {t: [r for r in rows if r["date"] <= last] for t, rows in raw.items()}
    del raw
    if max(r["date"] for rows in prices.values() for r in rows) > last:
        raise SystemExit("A_DAILY_ROW_AFTER_2019_WAS_KEPT")

    def bars_for(ticker, month):
        if month > cb.CALIBRATION[1]:
            raise ValueError(f"CALIBRATION_REFUSES_{month}")
        return ir.load_month(dev_root, ticker, month)
    tickers = sorted(AGENT_UNIVERSE)
    run = lab.replay(bars_for, prices, cb.CALIBRATION, tickers, {cb.BASE: cb.BASE_SPEC}, split="confirm", events={})
    v1 = [t for t in run["trades"] if t["variant"] == cb.BASE]
    days = cb.stock_days(bars_for, prices, cb.CALIBRATION, tickers)
    trades, why = cb.reconcile(run, v1, days)
    if trades is None:
        raise SystemExit(f"RECONCILIATION_FAILED {why}")
    sessions = sorted(run["eligible"])
    result, why = cb.evaluate(trades, days["candidates"], sessions, cb.CALIBRATION_YEARS)
    if result is None:
        raise SystemExit(f"RECONCILIATION_FAILED {why}")
    got = figures(result)
    report = {"months": list(cb.CALIBRATION), "last_day_read": last, "lab_code_sha256": lab.CODE_SHA,
              "module_sha256": cb.MODULE_SHA, "daily_prices_sha256": prices_sha,
              "spec_sha256": cb.spec_sha(), "params_sha256": cb.params_sha(), "sector_map_sha256": cb.map_sha(),
              "v1_skips": {k: v for k, v in run["skips"].items() if k.startswith(cb.BASE + ":")},
              "reproduction": {k: {"expected": e, "got": got[k], "rounded": round(got[k], d),
                                   "match": round(got[k], d) == e} for k, (e, d) in EXPECTED.items()},
              "dry_run": {**{g: result["gates"][g]["pass"] for g in ("G1", "G2", "G3", "G4")},
                          "verdict": result["verdict"]},
              "result": result, "halves": {}}
    report["dry_run_match"] = report["dry_run"] == DRY_RUN
    for name, period in cb.HALVES.items():
        ss = [s for s in sessions if in_period(s, period)]
        ts = [t for t in trades if in_period(t["session"], period)]
        cs = [c for c in days["candidates"] if in_period(c["session"], period)]
        half, _ = cb.evaluate(ts, cs, ss, [y for y in cb.CALIBRATION_YEARS if in_period(f"{y}-06", period)],
                              full=False)
        report["halves"][name] = {"sessions": half["sessions"], "trades": half["trades"], "k": half["k"],
                                  "v1": half["comparison"]["v1"], "cb": half["comparison"]["cb"],
                                  "ratios": half["comparison"]["ratios"],
                                  "ub95_r_es": half["comparison"]["bootstrap"]["ub95_r_es"],
                                  "lb95_dsr": half["comparison"]["bootstrap"]["lb95_dsr"], "gates": half["gates"]}
    if cb.module_sha() != cb.MODULE_SHA:
        raise SystemExit("MODULE_CHANGED_DURING_THE_CALIBRATION")
    report["seconds"] = round(time.time() - t0)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dev-root", default=str(ROOT / "data/ibkr_intraday/v1"))
    parser.add_argument("--prices-dir", default=str(ROOT / "data/ibkr_prices/2026-10-20"))
    parser.add_argument("--json", help="also write the calibration figures here (outside the repository)")
    args = parser.parse_args()
    report = calibrate(args.dev_root, args.prices_dir)
    print(render(report))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True, default=str))
    ok = all(x["match"] for x in report["reproduction"].values()) and report["dry_run_match"]
    return 0 if ok else 1


def _n(x, fmt="+.4f"):
    return "–" if x is None else format(x, fmt)


def render(report: dict) -> str:
    r = report["result"]
    lines = [f"EXT-FADE-CB.v1: calibration on {report['months'][0]}..{report['months'][1]} "
             f"(nothing after {report['last_day_read']} read; {report['seconds']} s)",
             f"lab code {report['lab_code_sha256'][:12]}  module {report['module_sha256'][:12]}  daily prices "
             f"{report['daily_prices_sha256'][:12]}  spec {report['spec_sha256'][:12]}  params "
             f"{report['params_sha256'][:12]}  map {report['sector_map_sha256'][:12]}",
             f"v1 skips: {report['v1_skips']}", "", "Reproduction of the frozen calibration record:"]
    for k, x in report["reproduction"].items():
        lines.append(f"  {k:32s} expected {x['expected']!s:>12}  got {x['got']!s:>24}  -> {x['rounded']!s:>12}  "
                     f"{'MATCH' if x['match'] else 'MISMATCH'}")
    lines.append(f"  dry run {report['dry_run']}  {'MATCH' if report['dry_run_match'] else 'MISMATCH'}")
    c = r["comparison"]
    lines += ["", f"k {r['k']:.6f}; R_ES {_n(c['ratios']['r_es'])} (UB95 {_n(c['bootstrap']['ub95_r_es'])}); dSR "
              f"{_n(c['ratios']['dsr'])} (LB95 {_n(c['bootstrap']['lb95_dsr'])}); 90% CI {c['bootstrap']['ci90']}"]
    for name in ("v1", "cb"):
        m = c[name]
        lines.append(f"  {name}: " + ", ".join(f"{k} {_n(m[k], '.4f')}" for k in
                                                ("sharpe", "mean", "sd", "es5", "w10", "worst", "dd", "mdd", "total")))
    for name, h in report["halves"].items():
        lines.append(f"  {name}: k {h['k']:.4f} R_ES {_n(h['ratios']['r_es'])} UB95 {_n(h['ub95_r_es'])} dSR "
                     f"{_n(h['ratios']['dsr'])} LB95 {_n(h['lb95_dsr'])} gates "
                     + "".join("1" if h["gates"][g]["pass"] else "0" for g in ("G1", "G2", "G3", "G4"))
                     + f" {h['gates']['verdict']}")
    lines += ["", f"candidates {r['candidates']}", f"hedge {r['hedge']}", f"binding {r['binding']}",
              f"normalisations {json.dumps(r['normalisations'], default=str)}"]
    st = r["stress"]
    for name, x in {**st["costs"], "post_skip": st["post_skip_candidates"],
                    **{f"without_{y}": v for y, v in st["leave_one_year_out"].items()}}.items():
        lines.append(f"  {name:16s} k {_n(x['k'])} R_ES {_n(x['ratios'].get('r_es'))} UB95 {_n(x['ub95_r_es'])} "
                     f"dSR {_n(x['ratios'].get('dsr'))} LB95 {_n(x['lb95_dsr'])} SR v1/CB "
                     f"{_n(x['v1']['sharpe'], '.2f')}/{_n(x['cb']['sharpe'], '.2f')} gates "
                     + "".join("1" if x["gates"][g]["pass"] else "0" for g in ("G1", "G2", "G3", "G4")))
    for y, x in st["by_year"].items():
        lines.append(f"  {y}: SR {_n(x['v1']['sharpe'], '.2f')}/{_n(x['cb']['sharpe'], '.2f')} ES5 "
                     f"{_n(x['v1']['es5'], '.0f')}/{_n(x['cb']['es5'], '.0f')} MDD {_n(x['v1']['mdd'], '.0f')}/"
                     f"{_n(x['cb']['mdd'], '.0f')} capped sessions {x['capped_sessions']}")
    lines += [f"  intraday trough {st['intraday_trough']}", f"  gross {st['gross']}",
              f"  concentration {st['concentration']}"]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
