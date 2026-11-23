#!/usr/bin/env python3
"""Write the office's summary of the unified strategy ledger: one entry per registered rule.

Run it on the owner's machine, where the ledger lives:

    python scripts/office_export_unified_summary.py --research ~/code/fsa-research

It only reads the ledger's own small result files (registry, per-rule summary, parity report). It does not
read the ledger, a trade file or a bar. For each registered rule it writes the rule text, its family, its
status, whether the re-run reproduced the original record, and per period the order and trade counts, the
number of trading dates, the mean result per trade before and after costs and its t.

An entry has no ticker, no price, no date of a trade and no file path. The script has no network code. It
writes one file, ``stock_machine/office/unified_ledger_summary.json`` unless ``--out`` says otherwise; that
file reaches the office only through a reviewed change to this repository. Standard library only.

Exit status 0 means the file was written; 1 means it was refused, with a code.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from pathlib import Path

SCHEMA = "office.unified_ledger.v1"
FOLDER = "research8/unified_2026-11-19/work"
OUT = Path(__file__).resolve().parent.parent / "stock_machine/office/unified_ledger_summary.json"
SOURCES = ("registry.json", "registry_daily.json", "summary_unified.csv", "summary_daily.csv", "parity.csv",
           "parity_daily.csv")
PERIODS = {"2016-23": "development", "2024-26": "research-exposed"}
LOOK_AHEAD = "ORIGINAL_RULE_USES_LATER_CANDLES"
NO_ROWS = "NO_ROWS_OF_ITS_OWN"
PICKS_ONLY = "PARITY_ON_MONTHLY_PICKS_ONLY"
MIN_FIGURE_TRADES = 20                                  # below this a period shows counts only
MIN_TRADES = 100                                        # below this a period's mean is not counted as positive or negative
NOTES = [
    "Every figure restates a rule that had already been run and counted. Nothing here is new evidence.",
    "2016-23 was used to develop these rules. 2024-26 has been read in research too. No period is untouched.",
    "The stocks are 54 hand-picked names. No figure says anything about other stocks.",
    "X2, X2-FLOW and X2-STRESS are subsets of EXT-FADE.v1 and are one bet, not three.",
    "The four exits of one GAP-1 entry are four rows on the same fill: never count them as four trades.",
    "Fills are modelled from bars. None is a broker fill.",
]
COST_MODEL = ("One schedule for every rule: tiered commissions plus slippage on a 10,000-dollar order and on its "
              "SPY hedge, at modelled prices. 'net' is the primary schedule; 'stress' is the harsher one.")


class Refused(Exception):
    """The file was not written. ``args[0]`` is a short code."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path: Path) -> list[dict]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def number(text, digits: int = 2):
    """A finite number rounded for display, or None where the source has none."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return round(value, digits) if math.isfinite(value) else None


_FILE_REF = re.compile(r";?\s*[~\w./-]*\.(?:py|pkl|csv|json|gz|md|parquet|txt|ipynb|xlsx|db)\b")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
REWORD = {"the last to the 2023-12-29 close": "the last to the close of the final session of 2023"}


def plain(text: str) -> str:
    """Registry text as it is, refused if it names a file or a calendar date."""
    if re.search(r"\S/\S", text) or "~" in text or "\\" in text or _ISO_DATE.search(text):
        raise Refused("TEXT_HAS_PATH_OR_DATE")
    return text


def rule_text(text: str) -> str:
    """The rule as the registry words it, without a file reference or a calendar date. Only a token ending
    in a file extension is removed; any other path-like text is refused, not rewritten."""
    for old, new in REWORD.items():
        text = text.replace(old, new)
    return plain(_FILE_REF.sub("", text).strip())


def figures(trades: int, **values) -> dict:
    """Means and t of a period, or none of them when it has fewer than MIN_FIGURE_TRADES trades."""
    return {k: (number(v) if trades >= MIN_FIGURE_TRADES else None) for k, v in values.items()}


def count(text) -> int:
    value = float(text)
    if value != int(value) or value < 0:
        raise Refused("SOURCE_COUNT_INVALID")
    return int(value)


def parity_block(row: dict | None) -> dict:
    if row is None:
        raise Refused("PARITY_ROW_MISSING")
    return {"verdict": row["verdict"], "recorded": count(row["recorded"]), "matched": count(row["matched"])}


def minute_entries(work: Path) -> list[dict]:
    registry = json.loads((work / "registry.json").read_text())
    parity = {r["strategy_id"]: r for r in rows(work / "parity.csv")}
    periods: dict[str, list[dict]] = {}
    for r in rows(work / "summary_unified.csv"):
        if r["span"] not in PERIODS:
            raise Refused("SOURCE_PERIOD_UNKNOWN")
        periods.setdefault(r["strategy_id"], []).append({
            "period": r["span"], "label": PERIODS[r["span"]], "orders": count(r["orders"]),
            "trades": count(r["trades"]), "dates": count(r["dates"]),
            **figures(count(r["trades"]), mean_hedged_bps=r["hedged_bps"], t_hedged=r["t_hedged"],
                      mean_net_bps=r["net_primary_bps"], t_net=r["t_net"], mean_net_stress_bps=r["net_stress_bps"])})
    if set(periods) - set(registry):
        raise Refused("SUMMARY_ID_NOT_IN_REGISTRY")
    out = []
    for strategy_id, meta in registry.items():
        mine = sorted(periods.get(strategy_id, []), key=lambda p: p["period"])
        flags = ([LOOK_AHEAD] if strategy_id.startswith("R8.C.") else []) + ([] if mine else [NO_ROWS])
        out.append({"strategy_id": strategy_id, "bars": "minute", "family": plain(meta["family"]), "status": plain(meta["status"]),
                    "subset_of": meta["subset_of"], "rule": rule_text(meta["rule"]),
                    "parity": parity_block(parity.get(strategy_id)), "flags": flags, "periods": mine})
    return out


def daily_entries(work: Path) -> list[dict]:
    registry = json.loads((work / "registry_daily.json").read_text())
    parity = {r["strategy_id"]: r for r in rows(work / "parity_daily.csv")}
    summary = {r["strategy_id"]: r for r in rows(work / "summary_daily.csv")}
    if set(summary) - set(registry):
        raise Refused("SUMMARY_ID_NOT_IN_REGISTRY")
    out = []
    for strategy_id, meta in registry.items():
        r = summary.get(strategy_id)
        # The daily summary has one row per rule over its whole span and no t.
        mine = [] if r is None else [{
            "period": meta["span"], "label": "development" if meta["span"].endswith("2023-12") else "development and research-exposed",
            "orders": None, "trades": count(r["trades"]), "dates": count(r["dates"]),
            **figures(count(r["trades"]), mean_hedged_bps=r["hedged_bps"], mean_net_bps=r["net_primary_bps"]),
            "t_hedged": None, "t_net": None, "mean_net_stress_bps": None}]
        flags = ([PICKS_ONLY] if strategy_id.startswith("QM-1.") else []) + ([] if mine else [NO_ROWS])
        out.append({"strategy_id": strategy_id, "bars": "daily", "family": plain(meta["family"]), "status": plain(meta["status"]),
                    "subset_of": None, "rule": rule_text(meta["rule"]), "parity": parity_block(parity.get(strategy_id)),
                    "flags": flags, "periods": mine})
    return out


def positive(entries: list[dict]) -> dict:
    """Of the minute rules with at least MIN_TRADES trades in a period, how many have a positive mean after
    costs there, and which have one in both periods. The office recomputes this when it reads the file."""
    good = lambda p: p["trades"] >= MIN_TRADES and p["mean_net_bps"] is not None
    out = {"rules_with_rows": sum(bool(e["periods"]) for e in entries), "min_trades": MIN_TRADES}
    for period in PERIODS:
        have = [p["mean_net_bps"] for e in entries for p in e["periods"] if p["period"] == period and good(p)]
        out[period] = {"rules": len(have), "net_positive": sum(m > 0 for m in have)}
    means = lambda e: {p["period"]: p["mean_net_bps"] for p in e["periods"] if good(p)}
    counted = [e for e in entries if len(means(e)) == len(PERIODS)]
    return {**out, "rules_counted_in_both": len(counted),
            "net_positive_in_both": sorted(e["strategy_id"] for e in counted if all(m > 0 for m in means(e).values()))}


def build(research: Path) -> dict:
    work = research / FOLDER
    for name in SOURCES:
        if not (work / name).is_file():
            raise Refused(f"SOURCE_MISSING:{name}")
    minute, daily = minute_entries(work), daily_entries(work)
    ids = [e["strategy_id"] for e in minute + daily]
    if len(set(ids)) != len(ids):
        raise Refused("STRATEGY_ID_REPEATED")
    if any(e["parity"]["verdict"] != "PASS" for e in minute + daily):
        raise Refused("PARITY_NOT_PASSED")              # a rule that did not reproduce is fixed first, not exported
    return {"schema": SCHEMA, "ledger_version": "unified-1", "span": "2016-01..2026-08",
            "universe": "54 hand-picked US stocks", "cost_model": COST_MODEL, "notes": NOTES,
            "counts": {"minute_rules": len(minute), "daily_rules": len(daily)},
            "minute_net_after_costs": positive(minute),
            "sources": {name: sha256(work / name) for name in SOURCES},
            "rules": minute + daily}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--research", required=True, type=Path, help="the research folder (read only)")
    parser.add_argument("--out", type=Path, default=OUT, help="the file to write")
    args = parser.parse_args(argv)
    research, out = args.research.expanduser().resolve(), args.out.expanduser().resolve()
    if out == research or research in out.parents:
        print("REFUSED OUTPUT_INSIDE_RESEARCH_FOLDER")
        return 1
    try:
        summary = build(research)
    except Refused as error:
        print(f"REFUSED {error.args[0]}")
        return 1
    except (KeyError, TypeError, ValueError):
        print("REFUSED SOURCE_UNREADABLE")
        return 1
    out.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    counts, net = summary["counts"], summary["minute_net_after_costs"]
    print(f"WRITTEN {out.name}: {counts['minute_rules']} minute rules, {counts['daily_rules']} daily rules; "
          f"net positive in both periods: {len(net['net_positive_in_both'])} of {net['rules_counted_in_both']} "
          f"with at least {net['min_trades']} trades in each")
    return 0


if __name__ == "__main__":
    sys.exit(main())
