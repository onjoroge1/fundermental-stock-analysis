"""The office's summary of the unified strategy ledger: what it may hold, and that it stays reference material."""
import copy
import importlib.util
import inspect
import json
import re
from pathlib import Path

import pytest

from stock_machine.office import inventory, roles, tools, unified

ROOT = Path(__file__).resolve().parents[1]
RAW = json.loads(unified.PATH.read_text())
SCRIPT = ROOT / "scripts/office_export_unified_summary.py"


def exporter():
    spec = importlib.util.spec_from_file_location("office_export_unified_summary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def broken(change):
    data = copy.deepcopy(RAW)
    change(data)
    with pytest.raises(unified.UnifiedSummaryError) as error:
        unified.validate(data)
    return error.value.code


def test_the_checked_in_summary_is_valid_and_says_what_the_research_record_says():
    data = unified.read()
    assert data["counts"] == {"minute_rules": 258, "daily_rules": 58}
    assert all(r["parity"]["verdict"] == "PASS" for r in data["rules"])
    net = data["minute_net_after_costs"]
    assert net["rules_with_rows"] == 253 and net["min_trades"] == 100
    assert net["2016-23"] == {"rules": 249, "net_positive": 7} and net["2024-26"] == {"rules": 232, "net_positive": 3}
    assert net["net_positive_in_both"] == ["X2", "X2-FLOW", "X2-STRESS"]


def test_the_summary_holds_no_path_no_user_name_and_no_field_beyond_the_fixed_list():
    text = unified.PATH.read_text()
    assert "/Users" not in text and "/home" not in text and "\\\\" not in text and ".pkl" not in text
    assert not re.search(r"\d{4}-\d{2}-\d{2}T", text)                       # no timestamp
    for row in RAW["rules"]:
        assert set(row) == set(unified.RULE)
        assert all(set(p) == set(unified.PERIOD) for p in row["periods"])


@pytest.mark.parametrize("change, code", [
    (lambda d: d.update(extra=1), "SUMMARY_FIELDS_INVALID"),
    (lambda d: d["rules"][0].update(ticker="AAA"), "RULE_FIELDS_INVALID"),
    (lambda d: d["rules"][0]["periods"][0].update(price=1.0), "PERIOD_FIELDS_INVALID"),
    (lambda d: d["rules"][0].update(rule="see /Users/someone/file.py"), "RULE_TEXT_INVALID"),
    (lambda d: d["rules"][0].update(rule="x" * 601), "RULE_TEXT_INVALID"),
    (lambda d: d["rules"][0]["parity"].update(verdict="FAIL"), "RULE_PARITY_INVALID"),
    (lambda d: d["rules"][0]["flags"].append("PROMISING"), "RULE_FLAG_UNKNOWN"),
    (lambda d: d["rules"][0]["periods"][0].update(mean_net_bps=float("nan")), "PERIOD_FIGURE_INVALID"),
    (lambda d: d["rules"][0]["periods"][0].update(trades=-1), "PERIOD_COUNT_INVALID"),
    (lambda d: d["rules"].append(copy.deepcopy(d["rules"][0])), "RULE_ID_REPEATED"),
    (lambda d: d["rules"][1].update(subset_of="NOT-A-RULE"), "RULE_SUBSET_INVALID"),
    (lambda d: d["counts"].update(minute_rules=1), "SUMMARY_COUNTS_INVALID"),
    (lambda d: d["sources"].update({"../x": "0" * 64}), "SUMMARY_SOURCES_INVALID"),
    (lambda d: d["minute_net_after_costs"]["net_positive_in_both"].append("NOT-A-RULE"), "SUMMARY_NET_INVALID"),
])
def test_a_summary_with_anything_unknown_or_malformed_is_refused(change, code):
    assert broken(change) == code


def test_a_rule_whose_original_reads_later_candles_is_marked_not_usable():
    flagged = {r["strategy_id"] for r in RAW["rules"] if "ORIGINAL_RULE_USES_LATER_CANDLES" in r["flags"]}
    assert flagged == {f"R8.C.{side}.X{exit_}" for side in "LS" for exit_ in range(4)}
    assert unified.overview()["not_usable"] == sorted(flagged)
    assert "not usable" in unified.rule("R8.C.L.X0")["flag_notes"][0]


def test_every_reading_says_it_is_neither_a_candidate_nor_new_evidence():
    assert unified.overview()["new_evidence"] is False and unified.overview()["candidates"] is False
    entry = unified.rule("X2-FLOW")
    assert entry["candidate"] is False and entry["new_evidence"] is False and entry["subset_of"] == "X2"
    assert any("one bet" in note for note in entry["notes"]) and any("54" in note for note in entry["notes"])
    assert unified.rule("NOT-A-RULE") is None
    fade = unified.family("Opening-gap fade")
    assert {"EXT-FADE.v1", "X2", "X2-FLOW", "X2-STRESS"} <= {r["strategy_id"] for r in fade}
    assert all("rule" not in r for r in fade)


def test_only_roles_that_may_read_strategy_evidence_can_read_it():
    assert tools.unified_ledger("reviewer")["minute_rules"] == 258
    assert tools.unified_ledger("qa", "UVM-1.RV20.RET20.H5")["bars"] == "daily"
    with pytest.raises(roles.ToolNotAllowed):
        tools.unified_ledger("scout")
    with pytest.raises(roles.ToolNotAllowed):
        tools.unified_ledger("nobody", "X2")


def test_the_ledger_adds_no_candidate_and_cannot_block_one():
    rows = inventory.read()
    by_id = {r["strategy_id"]: r for r in rows}
    for name in ("UNIFIED-LEDGER", "UPOOL-1"):
        assert by_id[name]["admissible"] is False and by_id[name]["execution_kind"] == "NONE" and by_id[name]["hashes"] == {}
    assert by_id["UPOOL-1"]["original_status"] == "Closed, FAIL at the development gate"
    candidate_hashes = {h for r in rows if r["admissible"] for h in r["hashes"].values()}
    assert not candidate_hashes & set(RAW["sources"].values())
    # The module reads one file: it imports nothing that reaches a database, the network or another office module.
    imports = set(re.findall(r"^(?:import|from) ([a-z_.]+)", inspect.getsource(unified), re.M))
    assert imports == {"__future__", "json", "math", "re", "functools", "pathlib"}


def test_the_exporter_is_standard_library_only_reads_small_result_files_and_refuses_a_bad_source(tmp_path):
    text = SCRIPT.read_text()
    imports = set(re.findall(r"^(?:import|from) ([a-z_]+)", text, re.M))
    assert imports <= {"__future__", "argparse", "csv", "hashlib", "json", "math", "re", "sys", "pathlib"}
    for word in ("ledger_2016", "ledger_daily", ".pkl", "socket", "urllib", "http", "subprocess"):
        assert word not in text
    module = exporter()
    assert module.SCHEMA == unified.SCHEMA and set(module.SOURCES) == set(RAW["sources"])
    assert {module.LOOK_AHEAD, module.NO_ROWS, module.PICKS_ONLY} == set(unified.FLAGS)
    assert module.main(["--research", str(tmp_path), "--out", str(tmp_path / "out.json")]) == 1      # inside the research folder
    out = tmp_path.parent / f"{tmp_path.name}-out.json"
    assert module.main(["--research", str(tmp_path), "--out", str(out)]) == 1 and not out.exists()   # no source files


def test_the_exporter_builds_a_valid_summary_from_small_files_and_refuses_a_rule_that_did_not_reproduce(tmp_path):
    module = exporter()
    work = tmp_path / module.FOLDER
    work.mkdir(parents=True)
    meta = {"family": "Fixture", "status": "Closed", "subset_of": None, "rule": "A fixture rule (two facts; some_dir/work/chosen.pkl).", "adapter": "a",
            "original": {"/somewhere/private.py": "0" * 64}, "recorded": "/somewhere/trades.pkl"}
    (work / "registry.json").write_text(json.dumps({"FIX.A": meta, "FIX.B": {**meta, "subset_of": "FIX.A"}}))
    (work / "registry_daily.json").write_text(json.dumps({"FIX.D": {**meta, "span": "2016-01..2023-12"}}))
    (work / "summary_unified.csv").write_text(
        "strategy_id,span,orders,unfilled,short_restricted,trades,dates,hedged_bps,t_hedged,net_primary_bps,t_net,net_base_bps,net_stress_bps,cost_primary_bps\n"
        "FIX.A,2016-23,300,0,0,300,200,5.126,1.5,2.004,0.6,3,-4.5,3\nFIX.A,2024-26,120,0,0,120,90,4,1,1.5,0.4,2,-5,3\n"
        "FIX.B,2016-23,50,0,0,50,40,9,1,7,0.9,8,1,2\n")
    (work / "summary_daily.csv").write_text("strategy_id,trades,dates,first,last,gross_bps,hedged_bps,net_primary_bps\nFIX.D,58,56,2016-03-24,2023-07-29,13.1,17.2,1.39\n")
    parity = "strategy_id,recorded,mine,matched,verdict\nFIX.A,420,420,420,PASS\nFIX.B,50,50,50,PASS\n"
    (work / "parity.csv").write_text(parity)
    (work / "parity_daily.csv").write_text("strategy_id,recorded,mine,matched,verdict\nFIX.D,58,58,58,PASS\n")
    summary = unified.validate(module.build(tmp_path))
    text = json.dumps(summary)
    assert "/somewhere" not in text and "2016-03-24" not in text and "adapter" not in text
    assert summary["counts"] == {"minute_rules": 2, "daily_rules": 1}
    assert summary["rules"][0]["rule"] == "A fixture rule (two facts)."               # the file reference is dropped
    assert summary["rules"][0]["periods"][0]["mean_hedged_bps"] == 5.13
    net = summary["minute_net_after_costs"]                    # FIX.B has under 100 trades: it is not counted
    assert net["2016-23"] == {"rules": 1, "net_positive": 1} and net["net_positive_in_both"] == ["FIX.A"]
    (work / "parity.csv").write_text(parity.replace("FIX.B,50,50,50,PASS", "FIX.B,50,50,49,FAIL"))
    with pytest.raises(module.Refused) as error:
        module.build(tmp_path)
    assert error.value.args[0] == "PARITY_NOT_PASSED"
