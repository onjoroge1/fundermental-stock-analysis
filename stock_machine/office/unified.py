"""The unified strategy ledger, as the office sees it: one summary entry per registered rule.

The research side re-ran every registered rule under one set of fills and costs, after each re-run had
reproduced its original record. ``scripts/office_export_unified_summary.py`` turns that result into the
checked-in file ``unified_ledger_summary.json``; this module checks and reads it.

An entry holds the rule's text, family and status, whether the re-run reproduced the original record, and
per period counts, means and t. It has no ticker, no price, no trade date and no file path: ``validate``
refuses any field it does not know.

These entries are reference material for the roles that read strategy evidence. They are not candidates:
nothing here is admitted, deployed or traded, and no entry is new evidence. The figures restate rules that
had already been run and counted, on years that research has already read.
"""
from __future__ import annotations

import copy
import json
import math
import re
from functools import lru_cache
from pathlib import Path

SCHEMA = "office.unified_ledger.v1"
PATH = Path(__file__).with_name("unified_ledger_summary.json")
TOP = ("schema", "ledger_version", "span", "universe", "cost_model", "notes", "counts",
       "minute_net_after_costs", "sources", "rules")
RULE = ("strategy_id", "bars", "family", "status", "subset_of", "rule", "parity", "flags", "periods")
PERIOD = ("period", "label", "orders", "trades", "dates", "mean_hedged_bps", "t_hedged", "mean_net_bps", "t_net",
          "mean_net_stress_bps")
FIGURES = PERIOD[5:]
SOURCES = ("registry.json", "registry_daily.json", "summary_unified.csv", "summary_daily.csv", "parity.csv",
           "parity_daily.csv")
LOOK_AHEAD, NO_ROWS, PICKS_ONLY = "ORIGINAL_RULE_USES_LATER_CANDLES", "NO_ROWS_OF_ITS_OWN", "PARITY_ON_MONTHLY_PICKS_ONLY"
FLAGS = {LOOK_AHEAD: "The original rule reads candles after its decision minute. Its figures reproduce the "
                     "experiment as it ran and are not usable.",
         NO_ROWS: "Its orders are another rule's rows; it was registered only for the parity check.",
         PICKS_ONLY: "The original record holds no result per holding, so the re-run was checked on the monthly "
                     "picks only."}
MINUTE_PERIODS = {"2016-23": "development", "2024-26": "research-exposed"}
MIN_FIGURE_TRADES = 20            # a period with fewer trades shows counts only
MIN_TRADES = 100                  # a period with fewer trades is not counted as positive or negative
_ID = re.compile(r"[A-Za-z0-9._:-]{1,80}")
_SHA = re.compile(r"[0-9a-f]{64}")
_DAILY_PERIOD = re.compile(r"(\d{4})-(0[1-9]|1[0-2])\.\.(\d{4})-(0[1-9]|1[0-2])")
# Text that names a file, a calendar date or a price is refused wherever it appears.
_NOT_PLAIN = re.compile(r"\S[/\u2044\u2215]\S|(?:^|\s)/\S|\.(?:py|pkl|csv|json|gz|md|parquet|txt|ipynb|xlsx|db)\b|\d{4}-\d{2}-\d{2}|[$~\\\\]")


class UnifiedSummaryError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _need(ok: bool, code: str) -> None:
    if not ok:
        raise UnifiedSummaryError(code)


def _text(value, top: int) -> bool:
    """Plain text of a bounded length that names no file, no calendar date and no price."""
    return isinstance(value, str) and 0 < len(value) <= top and value.isprintable() and not _NOT_PLAIN.search(value)


def _id(value) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def _count(value, *, optional: bool = False) -> bool:
    return (optional and value is None) or (type(value) is int and 0 <= value <= 100_000_000)


def _number(value) -> bool:
    return value is None or (type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e6
                             and round(value, 2) == value)


def _period(row: dict, period) -> None:
    _need(isinstance(period, dict) and set(period) == set(PERIOD), "PERIOD_FIELDS_INVALID")
    name = period["period"]
    if row["bars"] == "minute":
        _need(isinstance(name, str) and MINUTE_PERIODS.get(name) == period["label"], "PERIOD_NAME_INVALID")
    else:
        match = _DAILY_PERIOD.fullmatch(name) if isinstance(name, str) else None
        _need(match is not None and (match[1], match[2]) <= (match[3], match[4]), "PERIOD_NAME_INVALID")
        _need(period["label"] == ("development" if match[3] <= "2023" else "development and research-exposed"),
              "PERIOD_NAME_INVALID")
    _need(_count(period["orders"], optional=row["bars"] == "daily") and _count(period["trades"])
          and _count(period["dates"]) and period["dates"] <= period["trades"]
          and (period["orders"] is None or period["trades"] <= period["orders"]), "PERIOD_COUNT_INVALID")
    _need(all(_number(period[k]) for k in FIGURES), "PERIOD_FIGURE_INVALID")
    _need(period["trades"] >= MIN_FIGURE_TRADES or all(period[k] is None for k in FIGURES), "PERIOD_SAMPLE_TOO_SMALL")


def _rule(row) -> None:
    _need(isinstance(row, dict) and set(row) == set(RULE), "RULE_FIELDS_INVALID")
    _need(_id(row["strategy_id"]), "RULE_ID_INVALID")
    _need(row["bars"] in ("minute", "daily"), "RULE_BARS_INVALID")
    _need(_text(row["family"], 80) and _text(row["status"], 80) and _text(row["rule"], 600), "RULE_TEXT_INVALID")
    _need(row["subset_of"] is None or (_id(row["subset_of"]) and row["subset_of"] != row["strategy_id"]),
          "RULE_SUBSET_INVALID")
    parity = row["parity"]
    _need(isinstance(parity, dict) and set(parity) == {"verdict", "recorded", "matched"}
          and parity["verdict"] == "PASS" and _count(parity["recorded"]) and _count(parity["matched"])
          and parity["matched"] <= parity["recorded"], "RULE_PARITY_INVALID")
    flags = row["flags"]
    _need(isinstance(flags, list) and all(isinstance(f, str) and f in FLAGS for f in flags)
          and len(set(flags)) == len(flags), "RULE_FLAG_UNKNOWN")
    # The marker of a rule that reads later candles cannot be dropped, and cannot be put on another rule.
    _need((LOOK_AHEAD in flags) == row["strategy_id"].startswith("R8.C."), "RULE_FLAG_MISSING")
    periods = row["periods"]
    _need(isinstance(periods, list) and len(periods) <= 2 and bool(periods) != (NO_ROWS in flags), "RULE_PERIODS_INVALID")
    for period in periods:
        _period(row, period)
    _need(len({p["period"] for p in periods}) == len(periods), "RULE_PERIODS_INVALID")


def net_after_costs(rules: list[dict]) -> dict:
    """Of the minute rules with at least MIN_TRADES trades in a period, how many have a positive mean after
    costs there, and which have one in both periods. Computed from the entries, never taken on trust."""
    minute = [r for r in rules if r["bars"] == "minute"]
    good = lambda p: p["trades"] >= MIN_TRADES and p["mean_net_bps"] is not None
    out = {"rules_with_rows": sum(bool(r["periods"]) for r in minute), "min_trades": MIN_TRADES}
    for name in MINUTE_PERIODS:
        have = [p["mean_net_bps"] for r in minute for p in r["periods"] if p["period"] == name and good(p)]
        out[name] = {"rules": len(have), "net_positive": sum(m > 0 for m in have)}
    means = lambda r: {p["period"]: p["mean_net_bps"] for p in r["periods"] if good(p)}
    counted = [r for r in minute if len(means(r)) == len(MINUTE_PERIODS)]
    return {**out, "rules_counted_in_both": len(counted),
            "net_positive_in_both": sorted(r["strategy_id"] for r in counted if all(m > 0 for m in means(r).values()))}


def validate(payload) -> dict:
    """Return the summary unchanged if every field is known and well formed; otherwise raise a code."""
    _need(isinstance(payload, dict) and set(payload) == set(TOP), "SUMMARY_FIELDS_INVALID")
    _need(payload["schema"] == SCHEMA, "SUMMARY_SCHEMA_UNKNOWN")
    _need(_text(payload["ledger_version"], 40) and _text(payload["universe"], 80) and _text(payload["cost_model"], 400)
          and isinstance(payload["span"], str) and bool(_DAILY_PERIOD.fullmatch(payload["span"])), "SUMMARY_TEXT_INVALID")
    notes = payload["notes"]
    _need(isinstance(notes, list) and len(notes) <= 20 and all(_text(n, 300) for n in notes), "SUMMARY_TEXT_INVALID")
    sources = payload["sources"]
    _need(isinstance(sources, dict) and set(sources) == set(SOURCES)
          and all(isinstance(v, str) and bool(_SHA.fullmatch(v)) for v in sources.values()), "SUMMARY_SOURCES_INVALID")
    rules = payload["rules"]
    _need(isinstance(rules, list) and 0 < len(rules) <= 5_000, "SUMMARY_RULES_INVALID")
    for row in rules:
        _rule(row)
    ids = [r["strategy_id"] for r in rules]
    _need(len(set(ids)) == len(ids), "RULE_ID_REPEATED")
    _need(all(r["subset_of"] is None or r["subset_of"] in ids for r in rules), "RULE_SUBSET_INVALID")
    _need(payload["counts"] == {"minute_rules": sum(r["bars"] == "minute" for r in rules),
                                "daily_rules": sum(r["bars"] == "daily" for r in rules)}, "SUMMARY_COUNTS_INVALID")
    _need(payload["minute_net_after_costs"] == net_after_costs(rules), "SUMMARY_NET_INVALID")
    return payload


@lru_cache(maxsize=1)
def _read(path: Path) -> dict:
    return validate(json.loads(path.read_text()))


def read(path: Path | None = None) -> dict:
    """The checked summary. Each caller gets its own copy."""
    return copy.deepcopy(_read(path or PATH))


def overview() -> dict:
    """The few figures the owner's console shows. No rule text."""
    data = read()
    return {"ledger_version": data["ledger_version"], "span": data["span"], **data["counts"],
            "parity": "ALL_PASS", "minute_net_after_costs": data["minute_net_after_costs"],
            "not_usable": sorted(r["strategy_id"] for r in data["rules"] if LOOK_AHEAD in r["flags"]),
            "new_evidence": False, "candidates": False}


def rule(strategy_id: str) -> dict | None:
    """One rule's entry with the notes that limit how it may be read, or None if the ledger has no such rule."""
    data = read()
    row = next((r for r in data["rules"] if r["strategy_id"] == strategy_id), None)
    if row is None:
        return None
    return {**row, "flag_notes": [FLAGS[f] for f in row["flags"]], "ledger_version": data["ledger_version"],
            "universe": data["universe"], "cost_model": data["cost_model"], "notes": data["notes"],
            "new_evidence": False, "candidate": False}


def family(name: str) -> list[dict]:
    """Every rule of one family: id, status, flags and periods, without the rule text."""
    return [{k: r[k] for k in ("strategy_id", "status", "subset_of", "flags", "periods")}
            for r in read()["rules"] if r["family"] == name]
