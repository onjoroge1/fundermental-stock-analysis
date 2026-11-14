#!/usr/bin/env python3
"""Write results summaries of the strategies that run on the owner's machine (X2-FLOW, X2-STRESS, QM-1).

Run it on that machine after the evening settle:

    python scripts/office_export_local_record.py --research ~/code/fsa-research --out ~/code/fsa-office-uploads

It only reads the research folder. For each strategy it

1. recomputes the hashes of the frozen rule files and stops if one differs from the office inventory;
2. reads the recorder's own output file and takes sessions, counts and net figures from it;
3. writes one small JSON file into ``--out``.

A summary has no ticker, no price, no bar and no free text. This script has no network code: it uploads
nothing. The owner uploads the files it writes. It uses the standard library only.

Exit status 0 means every requested summary was written; 1 means at least one was refused, with a code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "office.external_record.v1"
INVENTORY = Path(__file__).resolve().parent.parent / "stock_machine/office/strategy_inventory.json"
# inventory hash name -> the frozen file, relative to the research folder
HASH_FILES = {
    "X2-FLOW_SPEC_v1.md": "research8/x2_shadow/X2-FLOW_SPEC_v1.md",
    "X2_SHADOW_SPEC_v1.1.md": "research8/x2_shadow/X2_SHADOW_SPEC_v1.1.md",
    "X2_SHADOW_SPEC_v1.2.md": "research8/x2_shadow/X2_SHADOW_SPEC_v1.2.md",
    "x2_shadow.py": "research8/x2_shadow/x2_shadow.py",
    "protocol_v12.py": "research8/x2_shadow/protocol_v12.py",
    "evalstats.py": "research8/x2_shadow/evalstats.py",
    "discovery/exp_qm1/SPEC.md": "discovery/exp_qm1/SPEC.md",
    "OBS-QM-3 spec": "discovery/observe/records/OBS-QM-3/SPEC.md",
}
X2_SETTLED = "research8/x2_shadow/ledger/settled.json"
QM_LEDGER = "discovery/observe/records/OBS-QM-3/ledger.jsonl"
QM_ACCOUNT, QM_START_EQUITY = "D-1x", 100_000.0          # the primary account in the frozen OBS-QM-3 spec
STRATEGIES = {("X2-FLOW", "v1"): "X2 shadow v1.2", ("X2-STRESS", "v1"): "X2 shadow v1.2",
              ("QM-1", "default:OBS-QM-3"): "OBS-QM-3"}


class Refused(Exception):
    """The summary was not written. ``args[0]`` is a short code."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value):
    """A finite number, or None where the recorder has no value yet."""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def frozen_hashes(research: Path, expected: dict) -> dict:
    for name, want in expected.items():
        relative = HASH_FILES.get(name)
        if relative is None or not (research / relative).is_file():
            raise Refused(f"RULE_FILE_MISSING:{name}")
        if sha256(research / relative) != want:
            raise Refused(f"RULE_HASH_MISMATCH:{name}")
    return dict(expected)


def x2(research: Path, strategy_id: str) -> dict:
    path = research / X2_SETTLED
    if not path.is_file():
        raise Refused(f"SOURCE_MISSING:{X2_SETTLED}")
    data = json.loads(path.read_text())
    covered = data["FLOW"]["earnings_covered_through"]
    if strategy_id == "X2-FLOW":
        block = data["FLOW"]
        mine = [t for t in data["trades"] if t["flow"]]
        extra = {"sessions_not_scored": block["sessions_unscored_no_earnings_data"],
                 "decision_due": bool(block["decision_due"])}
    else:
        block = data["STRESS"]
        mine = [t for t in data["trades"] if t["stress_day"] and not t["earnings"] and t["s"] <= covered]
        extra = {"stress_sessions": block["stress_sessions"]}
    if len(mine) != block["n"]:
        raise Refused("SOURCE_INCONSISTENT:TRADE_COUNT")
    drawdown = number(block.get("book_maxdd"))
    return {"source": X2_SETTLED, "first_session": data["start"], "through_session": data["through"],
            "sessions": data["sessions"], "trades": block["n"], "trade_dates": len({t["s"] for t in mine}),
            "figures": {"mean_net_bps": number(block.get("mean_bps")), "t_session": number(block.get("t_session")),
                        "max_drawdown_pct": None if drawdown is None else round(drawdown * 100, 4), **extra}}


def qm(research: Path) -> dict:
    path = research / QM_LEDGER
    if not path.is_file():
        raise Refused(f"SOURCE_MISSING:{QM_LEDGER}")
    lines = path.read_text().splitlines()
    previous = "0" * 64
    records = []
    for line in lines:
        record = json.loads(line)
        if record["prev"] != previous:
            raise Refused("SOURCE_INCONSISTENT:LEDGER_CHAIN")
        previous = hashlib.sha256(line.encode()).hexdigest()
        records.append(record)
    mine = [r for r in records if isinstance(r.get("key"), dict) and r["key"].get("account") == QM_ACCOUNT]
    sessions = sorted((r for r in mine if r["type"] == "session"), key=lambda r: r["key"]["session"])
    if not sessions:
        raise Refused("SOURCE_EMPTY")
    peak, drawdown = QM_START_EQUITY, 0.0
    for r in sessions:
        peak = max(peak, r["data"]["equity"])
        drawdown = min(drawdown, r["data"]["equity"] / peak - 1)
    last = sessions[-1]["data"]
    count = lambda kind: sum(r["type"] == kind for r in mine)
    exits = [r for r in mine if r["type"] == "exit"]
    return {"source": QM_LEDGER, "first_session": sessions[0]["key"]["session"],
            "through_session": sessions[-1]["key"]["session"], "sessions": len(sessions),
            "trades": len(exits), "trade_dates": len({r["data"]["session"] for r in exits}),
            "figures": {"equity_usd": round(last["equity"], 2),
                        "return_pct": round((last["equity"] / QM_START_EQUITY - 1) * 100, 4),
                        "max_drawdown_pct": round(drawdown * 100, 4), "gross_usd": round(last["gross"], 2),
                        "open_positions": last["positions"], "orders": count("order"), "fills": count("fill"),
                        "missed_orders": count("missed"), "fill_rejections": count("fill_rejected")}}


def summary(research: Path, strategy_id: str, version: str, expected: dict, now: datetime) -> dict:
    hashes = frozen_hashes(research, expected)
    body = qm(research) if strategy_id == "QM-1" else x2(research, strategy_id)
    source = body.pop("source")
    return {"schema": SCHEMA, "strategy_id": strategy_id, "version": version,
            "record_name": STRATEGIES[(strategy_id, version)], "rule_hashes": hashes,
            "source": {"file": source, "sha256": sha256(research / source)}, **body,
            "generated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds")}


def inventory_hashes(path: Path = INVENTORY) -> dict:
    rows = json.loads(path.read_text())["strategies"]
    return {(r["strategy_id"], r["version"]): r["hashes"] for r in rows
            if r["admissible"] and r["execution_kind"] == "EXTERNAL_LOCAL"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--research", required=True, type=Path, help="the research folder (read only)")
    parser.add_argument("--out", required=True, type=Path, help="where to write the summaries")
    parser.add_argument("--strategy", default="all", choices=["all", *sorted({k[0] for k in STRATEGIES})])
    args = parser.parse_args(argv)
    research, out = args.research.expanduser().resolve(), args.out.expanduser().resolve()
    if out == research or research in out.parents:
        print("REFUSED OUTPUT_INSIDE_RESEARCH_FOLDER")
        return 1
    expected = inventory_hashes()
    failed = False
    for (strategy_id, version), hashes in sorted(expected.items()):
        if (strategy_id, version) not in STRATEGIES or args.strategy not in ("all", strategy_id):
            continue
        try:
            record = summary(research, strategy_id, version, hashes, datetime.now(timezone.utc))
        except Refused as error:
            failed = True
            print(f"{strategy_id}: REFUSED {error.args[0]}")
            continue
        except (KeyError, TypeError, ValueError):
            failed = True
            print(f"{strategy_id}: REFUSED SOURCE_UNREADABLE")
            continue
        out.mkdir(parents=True, exist_ok=True)
        target = out / f"{strategy_id}_{record['through_session']}.json"
        target.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
        print(f"{strategy_id}: WRITTEN {target.name} sessions={record['sessions']} trades={record['trades']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
