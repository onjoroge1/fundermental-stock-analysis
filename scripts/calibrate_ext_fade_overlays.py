"""Calibrate the three EXT-FADE.v1 overlays on 2016-01..2019-12 before they are frozen (local only, real data).

  python scripts/calibrate_ext_fade_overlays.py [--dev-root ...] [--prices-dir ...] [--iv-dir ...] [--json out.json]

(stock_machine/backtest/ext_fade_overlays.py)

Replays EXT-FADE.v1 over 2016-01..2019-12 with lab.replay(split='confirm'), unlogged (the same call the research
round made), applies the overlays and checks that the module reproduces the research synthesis exactly: the mean
S5 over the 1,900 v1 trades, SW's gain at equal exposure in each half, P2's kept trades and mean at 10 bps, IVR's kept
trades, mean at 5 bps and kept-minus-removed. A mismatch is a bug in the module, never a reason to change a constant.
It also prints the in-sample dry run of the pass bars and the stress tables (they prove nothing: these months chose
the overlays).

Reads nothing dated 2020-01-01 or later: the bar loader refuses later months and the daily prices and the implied and
historical volatility are cut at 2019-12-31 as they are loaded. Never calls intraday_lab.run (it appends to the lab's
log) and writes nothing to the repository.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stock_machine.agents.contracts import AGENT_UNIVERSE  # noqa: E402
from stock_machine.backtest import ext_fade_overlays as ov  # noqa: E402
from stock_machine.backtest import implied_vol_replay as ivr  # noqa: E402
from stock_machine.backtest import intraday_lab as lab  # noqa: E402
from stock_machine.backtest import intraday_replay as ir  # noqa: E402

# The research synthesis (round2_synthesis.txt, FREEZE PLAN item 3), at the precision it was reported.
EXPECTED = {
    "mean_s5": (3.186, 3), "v1_trades": (1900, 0),
    "sw_gain_H1": (10.1, 1), "sw_gain_H2": (11.4, 1), "sw_gain_all": (10.9, 1),
    "p2_kept": (1016, 0), "p2_kept_mean_at_10": (32.1, 1),
    "ivr_kept": (1112, 0), "ivr_kept_mean_at_5": (43.3, 1), "ivr_kept_minus_removed_at_5": (44.1, 1),
}


def in_period(row: dict, period: tuple[str, str]) -> bool:
    return period[0] <= row["month"] <= period[1]


def summary(rows: list[dict], sessions: list[str]) -> dict:
    """The synthesis's own figures for one period (s1_candidates / s2_passbar_dryrun)."""
    norm, w_bar = ov.sw_weights(rows)
    clusters = [r["session"] for r in rows]
    out = {"trades": len(rows), "mean_s5": ov._mean([r["s5"] for r in rows]), "w_bar": w_bar,
           "v1": {f"mean_at_{c}": ov._mean([ov.net(r, c) for r in rows]) for c in (5, 10)}
           | {"t_at_5": lab.session_t([ov.net(r, 5) for r in rows], clusters)}}
    gain = [(w - 1) * ov.net(r, 5) for w, r in zip(norm, rows)]
    unit = [w * ov.net(r, 5) for w, r in zip(norm, rows)]
    out["sw"] = {"unit_at_5": ov._mean(unit), "gain_at_5": ov._mean(gain), "gain_t": lab.session_t(gain, clusters),
                 "gain_at_10": ov._mean([(w - 1) * ov.net(r, 10) for w, r in zip(norm, rows)]),
                 "no_trade": sum(1 for r in rows if r["s5"] == 0)}
    for sid, key, cost in ((ov.P2, "keep_p2", 10), (ov.IVR, "keep_ivr", 5)):
        kept = [bool(r[key]) for r in rows]
        k = [ov.net(r, cost) for r, x in zip(rows, kept) if x]
        rm = [ov.net(r, cost) for r, x in zip(rows, kept) if not x]
        out[sid] = {"kept": len(k), "cost_bps": cost, "kept_mean": ov._mean(k), "removed_mean": ov._mean(rm),
                    "kept_minus_removed": ov._mean(k) - ov._mean(rm),
                    "kept_t": lab.session_t(k, [r["session"] for r, x in zip(rows, kept) if x]),
                    "kept_minus_removed_t": ov.diff_t([ov.net(r, cost) for r in rows], kept, clusters),
                    "kept_mean_at_5": ov._mean([ov.net(r, 5) for r, x in zip(rows, kept) if x])}
    sec = ov.secondary(rows, years=())
    out["secondary"] = {k: sec[k] for k in ("among", "kept", "kept_mean", "removed", "removed_mean", "mean", "t")}
    ones = [1.0] * len(rows)
    out["sharpe"] = {}
    for cost in (5, 10):
        out["sharpe"][f"at_{cost}"] = {
            ov.BASE: ov.book_sharpe(rows, ones, cost, sessions), ov.SW: ov.book_sharpe(rows, norm, cost, sessions),
            ov.P2: ov.book_sharpe(rows, [float(r["keep_p2"]) for r in rows], cost, sessions),
            ov.IVR: ov.book_sharpe(rows, [float(r["keep_ivr"]) for r in rows], cost, sessions)}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dev-root", default=str(ROOT / "data/ibkr_intraday/v1"))
    parser.add_argument("--prices-dir", default=str(ROOT / "data/ibkr_prices/2026-10-20"))
    parser.add_argument("--iv-dir", default=str(ROOT / "data/ibkr_iv/2026-10-20"))
    parser.add_argument("--json", help="also write the calibration figures here (outside the repository)")
    args = parser.parse_args()
    last = ov.CALIBRATION_LAST_DAY
    raw, prices_sha = ivr.load_prices(args.prices_dir)
    prices = ov.cut_prices(raw, last)
    del raw
    vols, vol_sha = ov.load_vols(args.iv_dir, last)

    def bars_for(ticker, month):
        if month > ov.CALIBRATION[1]:
            raise ValueError(f"CALIBRATION_REFUSES_{month}")
        return ir.load_month(args.dev_root, ticker, month)
    tickers = sorted(AGENT_UNIVERSE)
    run = lab.replay(bars_for, prices, ov.CALIBRATION, tickers,
                     {ov.BASE: ov.BASE_SPEC, ov.ENTRY_0932: ov.STRESS["entry_0932"]}, split="confirm")
    v1 = [t for t in run["trades"] if t["variant"] == ov.BASE]
    days = ov.stock_days(bars_for, prices, ov.CALIBRATION, tickers)
    if days["eligible"] != run["eligible"]:
        raise SystemExit("ELIGIBILITY_DISAGREES_WITH_THE_LAB")
    rows = ov.features(v1, days, prices, vols)
    rows_0932 = ov.features([t for t in run["trades"] if t["variant"] == ov.ENTRY_0932], days, prices, vols)
    sessions = sorted(run["eligible"])
    report = {"months": list(ov.CALIBRATION), "last_day_read": last, "lab_code_sha256": lab.CODE_SHA,
              "module_sha256": ov.module_sha(), "daily_prices_sha256": prices_sha, "iv_hv_sha256": vol_sha,
              "skips": {k: v for k, v in run["skips"].items() if k.startswith(ov.BASE + ":")},
              "periods": {}}
    for name, period in (("H1", ov.HALVES["H1"]), ("H2", ov.HALVES["H2"]), ("all", ov.CALIBRATION)):
        rs = [r for r in rows if in_period(r, period)]
        report["periods"][name] = summary(rs, [s for s in sessions if period[0] <= s[:7] <= period[1]])
    a = report["periods"]
    got = {"mean_s5": a["all"]["mean_s5"], "v1_trades": a["all"]["trades"],
           "sw_gain_H1": a["H1"]["sw"]["gain_at_5"], "sw_gain_H2": a["H2"]["sw"]["gain_at_5"],
           "sw_gain_all": a["all"]["sw"]["gain_at_5"],
           "p2_kept": a["all"][ov.P2]["kept"], "p2_kept_mean_at_10": a["all"][ov.P2]["kept_mean"],
           "ivr_kept": a["all"][ov.IVR]["kept"], "ivr_kept_mean_at_5": a["all"][ov.IVR]["kept_mean"],
           "ivr_kept_minus_removed_at_5": a["all"][ov.IVR]["kept_minus_removed"]}
    report["reproduction"] = {k: {"expected": e, "got": got[k], "rounded": round(got[k], d),
                                  "match": round(got[k], d) == e} for k, (e, d) in EXPECTED.items()}
    report["s5_counts"] = {s: sum(1 for r in rows if r["s5"] == s) for s in range(6)}
    report["dry_run"] = ov.evaluate(rows, sessions, years=("2016", "2017", "2018", "2019"))
    report["stress_dry_run"] = ov.stress(rows, rows_0932, years=("2016", "2017", "2018", "2019"))
    print(render(report))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True, default=str))
    return 0 if all(x["match"] for x in report["reproduction"].values()) else 1


def _f(x, fmt="+.1f"):
    return "–" if x is None else format(x, fmt)


def render(report: dict) -> str:
    a = report["periods"]
    lines = [f"EXT-FADE.v1 overlays: calibration on {report['months'][0]}..{report['months'][1]} "
             f"(nothing after {report['last_day_read']} read)",
             f"lab code {report['lab_code_sha256'][:12]}  module {report['module_sha256'][:12]}  "
             f"daily prices {report['daily_prices_sha256'][:12]}  IV/HV files {report['iv_hv_sha256'][:12]}",
             f"v1 skips: {report['skips']}", "", "Reproduction of the research synthesis:"]
    for k, x in report["reproduction"].items():
        lines.append(f"  {k:30s} expected {x['expected']:>8}  got {x['got']!s:>22}  -> {x['rounded']!s:>8}  "
                     f"{'MATCH' if x['match'] else 'MISMATCH'}")
    lines += ["", f"S5 counts (2016-19): {report['s5_counts']}", ""]
    for name in ("H1", "H2", "all"):
        p = a[name]
        sw, p2, iv, sec = p["sw"], p[ov.P2], p[ov.IVR], p["secondary"]
        lines += [f"{name}: {p['trades']} v1 trades, mean S5 {p['mean_s5']:.4f}, mean weight {p['w_bar']:.4f}",
                  f"  v1   net at 5 {_f(p['v1']['mean_at_5'])} (t {_f(p['v1']['t_at_5'], '.2f')}), at 10 "
                  f"{_f(p['v1']['mean_at_10'])}",
                  f"  SW   unit at 5 {_f(sw['unit_at_5'])}; gain at equal exposure {_f(sw['gain_at_5'])} "
                  f"(t {_f(sw['gain_t'], '.2f')}), at 10 {_f(sw['gain_at_10'])}; no trade {sw['no_trade']}",
                  f"  P2   kept {p2['kept']}: at 10 {_f(p2['kept_mean'])} (t {_f(p2['kept_t'], '.2f')}), at 5 "
                  f"{_f(p2['kept_mean_at_5'])}; removed at 10 {_f(p2['removed_mean'])}; kept - removed at 10 "
                  f"{_f(p2['kept_minus_removed'])} (t {_f(p2['kept_minus_removed_t'], '.2f')})",
                  f"  IVR  kept {iv['kept']}: at 5 {_f(iv['kept_mean'])} (t {_f(iv['kept_t'], '.2f')}); removed at 5 "
                  f"{_f(iv['removed_mean'])}; kept - removed at 5 {_f(iv['kept_minus_removed'])} "
                  f"(t {_f(iv['kept_minus_removed_t'], '.2f')})",
                  f"  secondary: among {sec['among']} with PXC <= 1, IV>=HV {sec['kept']} at {_f(sec['kept_mean'])} vs "
                  f"IV<HV {sec['removed']} at {_f(sec['removed_mean'])}: diff {_f(sec['mean'])} "
                  f"(t {_f(sec['t'], '.2f')})",
                  "  book Sharpe " + "; ".join(f"{c}: " + ", ".join(f"{b} {_f(v, '.2f')}" for b, v in s.items())
                                              for c, s in p["sharpe"].items())]
    d = report["dry_run"]
    lines += ["", "Dry run of the pass bars on 2016-2019 with its own four years (in-sample: proves nothing):"]
    sw = d[ov.SW]
    lines.append(f"  {ov.SW}: {sw['verdict']}  standalone {_f(sw['standalone']['mean'])} (t "
                 f"{_f(sw['standalone']['t'], '.2f')}, {sw['standalone']['positive_years']} years)  gain "
                 f"{_f(sw['gain']['mean'])} (t {_f(sw['gain']['t'], '.2f')}, {sw['gain']['positive_years']} years)  "
                 f"Sharpe {_f(sw['book_sharpe']['overlay'], '.2f')} vs {_f(sw['book_sharpe'][ov.BASE], '.2f')}")
    for sid in (ov.P2, ov.IVR):
        x = d[sid]
        lab_bar = x["kept"].get("lab_check_bar")
        lines.append(f"  {sid}: {x['verdict']}  kept {x['kept_trades']} {_f(x['kept']['mean'])} (t "
                     f"{_f(x['kept']['t'], '.2f')}, {x['kept']['positive_years']} years)  kept - removed "
                     f"{_f(x['kept_minus_removed']['mean'])} (t {_f(x['kept_minus_removed']['t'], '.2f')}, "
                     f"{x['kept_minus_removed']['positive_years']} years)  Sharpe "
                     f"{_f(x['book_sharpe']['overlay'], '.2f')} vs {_f(x['book_sharpe'][ov.BASE], '.2f')}"
                     + (f"  lab check bar {lab_bar['pass']}" if lab_bar else ""))
    sec = d[ov.IVR]["secondary"]
    lines.append(f"  secondary: {sec['verdict']} diff {_f(sec['mean'])} (t {_f(sec['t'], '.2f')})")
    lines += ["", "Stress dry run (mean net per unit of position, bps; t):"]
    for book, table in report["stress_dry_run"].items():
        cells = [f"{k} {_f(v['mean'])} ({_f(v['t'], '.2f')})" for k, v in table.items() if k != "by_year"]
        years = [f"{y} {_f(v['at_5']['mean'])}/{_f(v['at_10']['mean'])}" for y, v in table["by_year"].items()]
        lines.append(f"  {book}: " + "; ".join(cells))
        lines.append(f"  {' ' * len(book)}  by year at 5/10: " + "; ".join(years))
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
