"""Report on the intraday first-hour replay (protocol intraday-first-hour.v1).

  python scripts/report_intraday_replay.py preview [--dev-root data/ibkr_intraday/v1] [--prices-dir ...]
      Runs the development stage on the months whose fetch is finished for every symbol (from 2016-01 up to the
      first unfinished month), labelled DEVELOPMENT_PREVIEW, and writes the aggregates and a summary. While the
      intraday lab's one-time looks are pending it stops where the lab says (intraday_lab.replay_reads_through):
      2017-06 until the frozen variants' confirms are logged, 2019-12 until the checks of those that passed are.
  python scripts/report_intraday_replay.py render data/intraday_replay/<stage>-<date>.json [--md out.md]
      Writes the summary of a stage run (scripts/run_intraday_replay.py).

Both read and write aggregates only: no bars or per-trade prices (IBKR's terms). Previews and the development run
are descriptive; only the one hold-out run carries verdicts.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine.agents.contracts import AGENT_UNIVERSE  # noqa: E402
from stock_machine.backtest import event_replay as er  # noqa: E402
from stock_machine.backtest import implied_vol_replay as ivr  # noqa: E402
from stock_machine.backtest import intraday_lab as lab  # noqa: E402
from stock_machine.backtest import intraday_replay as ir  # noqa: E402

NAMES = {"G1": "Gap and go (Barak)", "G2": "Gap fill (Barak)", "B1": "Break of structure (Barak)",
         "C1": "Narrowing range, the \"h structure\"", "X1": "Opening-range breakout, stocks in play",
         "E1": "Earnings-day continuation", "M1": "SPY intraday momentum", "R1": "End-of-day reversal",
         "A1": "Attention gap fade"}
ROUTINE = {"PRE_LISTING", "BEFORE_FIRST_DAILY_BAR", "NO_PRIOR_SESSIONS", "NO_HISTORY"}   # not yet listed, warm-up
COVERAGE_GAPS = {"UAL": "listed throughout; IBKR's daily history starts 2018-09-10",
                 "AVGO": "IBKR's daily history starts 2016-02-02 (after the Avago-Broadcom merger)"}


def finished_through(root, symbols: list[str], first: str, last: str) -> str | None:
    """The last month of the unbroken run of finished months from `first` (its warm-up month included)."""
    through = None
    for month in [ir.previous_month(first)] + ir.months_between(first, last):
        if ir.missing_months(root, symbols, [month]):
            break
        through = month
    return through if through and through >= first else None


def preview(dev_root, prices_dir) -> dict:
    tickers = sorted(AGENT_UNIVERSE)
    limit = lab.replay_reads_through()               # the lab's held-back months stay unread until its looks are done
    through = finished_through(dev_root, tickers + ["SPY"], ir.DEV[0], min(ir.DEV[1], limit or ir.DEV[1]))
    if through is None:
        return {"status": "REFUSED", "reason": "NO_FINISHED_MONTH"}
    prices, prices_sha = ivr.load_prices(prices_dir)
    if prices_sha != ir.DAILY_PRICES_SHA256:
        return {"status": "REFUSED", "reason": "DAILY_PRICES_NOT_REGISTERED"}
    if ir.behavior_digest() != ir.BEHAVIOR_SHA256:
        return {"status": "REFUSED", "reason": "REPLAY_CODE_CHANGED"}
    months = (ir.DEV[0], through)
    used = [ir.previous_month(months[0])] + ir.months_between(*months)
    try:
        events = ir.earnings()
    except ValueError as exc:
        return {"status": "REFUSED", "reason": str(exc)}
    result = ir.replay(lambda t, m: ir.load_month(dev_root, t, m), prices, events, months, tickers)
    report = ir.stage_report(result, "dev", months)
    return {"protocol_id": ir.PROTOCOL_ID, "protocol_sha256": ir.PROTOCOL_SHA256, "behavior_sha256": ir.BEHAVIOR_SHA256,
            "status": "OK", **report, "label": "DEVELOPMENT_PREVIEW", "lab_limit": limit,
            "inputs": {"prices_sha256": prices_sha, "earnings_sha256": er.EARNINGS_ANNOUNCEMENTS_SHA256,
                       "bars_sha256": ir.files_sha(dev_root, used), "bars": f"finished months {used[0]}..{used[-1]}"},
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "use": "INFORMATION_ONLY", "promotion": "NOT_AUTHORIZED", "broker_submission": False}


# ---- rendering -------------------------------------------------------------------

def _num(x, digits=3, signed=True) -> str:
    if x is None:
        return "–"
    return f"{x:+.{digits}f}" if signed else f"{x:.{digits}f}"


def _sci(x) -> str:
    return "–" if x is None else f"{x:+.2e}"


def _pct(x, digits=0) -> str:
    return "–" if x is None else f"{x * 100:.{digits}f}%"


def _interval(r: dict) -> str:
    if r.get("mean") is None:
        return "–"
    return f"{_sci(r['mean'])} [{_sci(r['lower'])}, {_sci(r['upper'])}]"


def _sign(r: dict) -> str:
    """Where the interval sits: above 0, below 0, or across it."""
    if r.get("lower") is None:
        return "–"
    return "above 0" if r["lower"] > 0 else "below 0" if r["upper"] < 0 else "includes 0"


def _labels(r: dict) -> str:
    """The labels describe a book whose lower bound clears 0; a losing book is always FRAGILE, so it shows none."""
    labels = r.get("labels", {})
    if not (r.get("reward", {}).get("lower") or 0) > 0:
        return "–"
    return labels.get("robustness", "–") + (", COST_SENSITIVE" if labels.get("cost_sensitive") else "")


def _ordered(strategies: dict) -> list[tuple[str, dict]]:
    """Protocol order (confirmatory, then exploratory), whatever order the JSON keeps."""
    order = [*ir.CONFIRMATORY, *ir.EXPLORATORY]
    return [(n, strategies[n]) for n in order if n in strategies] + \
        [(n, r) for n, r in strategies.items() if n not in order]


def render(report: dict) -> str:
    s = _ordered(report["strategies"])
    holdout = report["stage"] == "holdout"
    level = "family-wise for the confirmatory five, 95% for the rest" if holdout else "95%, descriptive"
    inputs = report.get("inputs", {})
    stale = [name for key, name, now in (("protocol_sha256", "protocol", ir.PROTOCOL_SHA256),
                                         ("behavior_sha256", "replay code", ir.BEHAVIOR_SHA256))
             if report.get(key) != now]

    def short(x):
        return f"`{x[:12]}…`" if x else "not recorded"
    lines = [f"# Intraday first-hour replay: {report['label'].replace('_', ' ').lower()}, "
             f"{report['months'][0]}..{report['months'][1]}", "",
             f"Protocol `{report.get('protocol_id')}` ({short(report.get('protocol_sha256'))}, behaviour pin "
             f"{short(report.get('behavior_sha256'))}). Generated {report.get('generated_at', '–')}. "
             "Aggregates only; information only, nothing here trades.", ""]
    if stale:
        lines += [f"**Warning: this report was produced by a different {' and '.join(stale)} than the current "
                  "module; its numbers may not match the current rules.**", ""]
    lines += [
             f"- **Sessions:** {report['sessions']:,}; **eligible stock-days:** {report['eligible_stock_days']:,}.",
             f"- **Intervals:** {level}.",
             "- **Units:** a reward is a trade's return net of costs and of beta x SPY over the same minutes (M1, "
             "a SPY trade, is unhedged), divided by the stock's trailing 20-session volatility for the strategy's "
             "window. The daily book adds a session's rewards and divides by its eligible stock-days (M1 by one), "
             "so book means are per eligible stock-day and small; the per-trade columns are the readable ones. Net "
             "bps are after the 20 bps round trip; gross bps are before costs but after the SPY hedge. Hit rate is "
             "the share of trades with a positive net result.",
             "- **Labels:** shown only for a book whose lower bound clears 0. ROBUST: the monthly book's lower bound "
             "and its mean without the best month are both above 0, else FRAGILE. COST_SENSITIVE: it clears 0 at "
             "10 bps a side but not at 20.",
             f"- **Inputs:** daily prices {short(inputs.get('prices_sha256'))}, earnings "
             f"{short(inputs.get('earnings_sha256'))}, bars {short(inputs.get('bars_sha256'))}"
             + (f" ({inputs['bars']})" if inputs.get("bars") else "") + ".", ""]
    if not holdout:
        lines += ["**Development numbers are descriptive.** They decide nothing; the one hold-out run does. A strategy "
                  "that looks good here has been looked at, and is not evidence.", ""]

    lines += ["## Results", "",
              "| | Strategy | Role | Trades | Stocks | Months | Top stock | Book mean [interval] | "
              "Direction skill [interval] | Per trade: reward | net bps | gross bps | hit rate | Labels |"
              + (" Verdict |" if holdout else ""),
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|" + ("---|" if holdout else "")]
    for name, r in s:
        pt, cov = r["per_trade"], r["coverage"]
        empty = not cov["trades"]
        months = f"{cov.get('active_months', '–')}/{cov.get('months', '–')}"
        lines.append(f"| **{name}** | {NAMES.get(name, name)} | {r.get('role', '–')} | {cov['trades']:,} | {cov['stocks']} | "
                     f"{months} | {_pct(cov.get('max_stock_share'))} | "
                     + ("no trades | – | – | – | – | – | – |" if empty else
                        f"{_interval(r['reward'])} | {_interval(r['direction_skill'])} | {_num(pt['mean_reward'])} | "
                        f"{_num(pt['mean_net_bps'], 1)} | {_num(pt['mean_gross_bps'], 1)} | {_pct(pt['hit_rate'])} | "
                        f"{_labels(r)} |")
                     + (f" {r.get('verdict', '–')} |" if holdout else ""))
    lines += ["", "Where each book sits: " + "; ".join(
        f"{n} {_sign(r['reward'])} (skill {_sign(r['direction_skill'])})" for n, r in s
        if r["coverage"]["trades"]) + ".", ""]

    claims = {n: r.get("claims") or {} for n, r in s}
    lines += ["## Barak's claims, checked", "", "| Claim | Measured | Interval | Claimed |", "|---|---|---|---|"]
    g1, g2, b1, x1 = (claims.get(k, {}) for k in ("G1", "G2", "B1", "X1"))
    if g1:
        lines.append(f"| G1 trades filled at their 1R target | {_pct(g1['hit_rate'])} | {_wilson(g1['hit_rate_interval'])} | "
                     f"{_pct(g1['claimed_hit_rate'])} |")
        lines.append(f"| G1 average gross R | {_num(g1['mean_gross_R'], 2)} | – | {_num(g1['claimed_gross_R'], 2)} |")
    if g2.get("gaps"):
        lines.append(f"| 1–3% gaps reach the prior close by 10:30 (all {g2['gaps']:,} such gaps) | "
                     f"{_pct(g2.get('touched_by_1030'))} | {_wilson(g2.get('touched_by_1030_interval'))} | "
                     f"{_pct(g2['claimed_fill_rate'])} |")
        lines.append(f"| … trade through it by 10:30 (what a target order needs) | {_pct(g2['filled_by_1030'])} | "
                     f"{_wilson(g2['filled_by_1030_interval'])} | – |")
        lines.append(f"| … reach it by the close / trade through it by the close | {_pct(g2.get('touched_by_close'))} "
                     f"/ {_pct(g2['filled_by_close'])} | – | – |")
        lines.append(f"| G2 trades filled at their target (the prior close) | {_pct(g2['traded_target_rate'])} | – | – |")
    if b1:
        b1_result = dict(s).get("B1", {})
        b1_trades = b1_result.get("coverage", {}).get("trades")
        still_open = b1_result.get("exits", {}).get("TIME", 0) / b1_trades if b1_trades else None
        lines.append(f"| B1 breakouts stopped out at the far side of the range by 10:30 ({_pct(still_open)} still open "
                     f"at 10:30) | {_pct(b1['stopped_rate'])} | {_wilson(b1['stopped_rate_interval'])} | "
                     f"{_pct(b1['claimed_failure_rate'])} |")
    if x1:
        lines.append(f"| X1 average gross R (the paper) | {_num(x1['mean_gross_R'], 2)} | – | "
                     f"{_num(x1['claimed_gross_R'], 2)} |")
    lines.append("")

    lines += ["## Diagnostics", "", "| | Costs doubled | Zero cost | Whole-bar fill check: book | …: skill | "
              "Random twin | Stops in fill bar | Through both levels | Exits |", "|---|---|---|---|---|---|---|---|---|"]
    for name, r in s:
        if not r["coverage"]["trades"]:
            lines.append(f"| {name} | no trades | | | | | | | |")
            continue
        exits = ", ".join(f"{k.lower()} {v}" for k, v in r["exits"].items())
        lines.append(f"| {name} | {_interval(r['costs_doubled'])} | {_interval(r['zero_cost'])} | "
                     f"{_interval(r['worst_fill_bar']['reward'])} | {_interval(r['worst_fill_bar']['direction_skill'])} "
                     f"| {_interval(r['random_twin'])} | {r['stops_in_fill_bar']} | {r['through_both_levels']} | {exits} |")
    lines += ["", "**By side and by earnings session** (trades: per-trade mean net bps; – under 20 trades):", "",
              "| | Long | Short | Earnings sessions | Other sessions |", "|---|---|---|---|---|"]
    for name, r in s:
        cells = [r["sides"]["long"], r["sides"]["short"], r["earnings"]["earnings"], r["earnings"]["other"]]
        lines.append(f"| {name} | " + " | ".join(f"{c['trades']:,}: {_num(c['mean_net_bps'], 1)}" for c in cells) + " |")
    years = sorted({y for _, r in s for y in r["by_year"]})
    if years:
        lines += ["", "**By year** (trades: per-trade mean net bps):", "", "| | " + " | ".join(years) + " |",
                  "|---|" + "---|" * len(years)]
        for name, r in s:
            lines.append(f"| {name} | " + " | ".join(
                f"{r['by_year'][y]['trades']:,}: {_num(r['by_year'][y]['mean_net_bps'], 1)}" if y in r["by_year"]
                else "–" for y in years) + " |")
    lines += ["", "**Detectable edge** (80% power, in book units): " + "; ".join(
        f"{n} {_sci(r['reward'].get('detectable_difference_80pct'))}" for n, r in s if r["coverage"]["trades"]) + ".",
        ""]

    lines += ["## Exclusions and skips", ""]
    excluded = report.get("excluded", {})
    total = report["eligible_stock_days"] + sum(excluded.values())
    lines += ["| Reason | Stock-days | Share of all stock-days |", "|---|---|---|"]
    for reason, n in sorted(excluded.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {reason} | {n:,} | {_pct(n / total, 1) if total else '–'} |")
    heavy = []
    for ticker, reasons in report.get("excluded_by_ticker", {}).items():
        notable = {k: v for k, v in reasons.items() if k not in ROUTINE}
        if sum(notable.values()) >= 0.05 * report["sessions"]:
            heavy.append(f"{ticker} ({', '.join(f'{k} {v}' for k, v in sorted(notable.items(), key=lambda kv: -kv[1]))})")
    lines += ["", "**Stocks with 5% or more of sessions excluded** (not counting sessions before listing or before "
              "IBKR's daily history, and warm-up): " + ("; ".join(heavy) if heavy else "none") + ".", "",
              "**PRICE_FLOOR** uses split-adjusted prices, so stocks that split later (NVDA, KLAC, LRCX, CMG, NFLX) "
              "lose early years in which they traded well above $10.", ""]
    before = {t: sum(v for k, v in reasons.items() if k in ("PRE_LISTING", "BEFORE_FIRST_DAILY_BAR"))
              for t, reasons in report.get("excluded_by_ticker", {}).items()}
    absent = [f"{t} {n}" + (f" ({COVERAGE_GAPS[t]})" if t in COVERAGE_GAPS else "")
              for t, n in sorted(before.items()) if n >= 0.05 * report["sessions"]]
    lines += ["**Not yet in the data** (sessions before listing or before IBKR's daily history; the ones in brackets "
              "are coverage gaps, not late listings): " + ("; ".join(absent) if absent else "none") + ".", ""]
    skips, counts = report.get("skips", {}), report.get("counts", {})
    lines += ["**Skips:** " + ("; ".join(f"{k} {v}" for k, v in sorted(skips.items())) if skips else "none") + ".", "",
              "**Counts:** " + ("; ".join(f"{k} {v:,}" for k, v in sorted(counts.items())) if counts else "none") + ".",
              ""]
    return "\n".join(lines)


def _wilson(pair) -> str:
    return "–" if not pair else f"[{_pct(pair[0])}, {_pct(pair[1])}]"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("preview")
    p.add_argument("--dev-root", default="data/ibkr_intraday/v1")
    p.add_argument("--prices-dir", default="data/ibkr_prices/2026-10-20")
    r = sub.add_parser("render")
    r.add_argument("path")
    r.add_argument("--md")
    args = parser.parse_args()
    if args.command == "preview":
        out = preview(args.dev_root, args.prices_dir)
        if out["status"] != "OK":
            print(json.dumps(out), file=sys.stderr)
            return 2
        directory = Path("data/intraday_replay")
        directory.mkdir(parents=True, exist_ok=True)
        stem = directory / f"preview-{out['months'][0]}-{out['months'][1]}"
        stem.with_suffix(".json").write_text(json.dumps(out, indent=1, sort_keys=True, default=str))
        stem.with_suffix(".md").write_text(render(out))
        print(f"wrote {stem}.json and {stem}.md", file=sys.stderr)
        return 0
    report = json.loads(Path(args.path).read_text())
    text = render(report)
    if args.md:
        Path(args.md).write_text(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
