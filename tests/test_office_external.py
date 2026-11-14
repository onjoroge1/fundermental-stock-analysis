"""Mirrored local records: what a summary may hold, what the export script writes, and who may upload one.

Every research folder here is a made-up one under a temporary directory. No real record is read.
"""
import copy
import hashlib
import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stock_machine.admin_panel import api, security, store
from stock_machine.office import config, external, inventory

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://testserver"
H = {"Origin": ORIGIN, "X-CSRF-Token": "fixture-csrf"}
NOW = datetime(2026, 11, 13, 22, 0, tzinfo=timezone.utc)
spec = importlib.util.spec_from_file_location("office_export_local_record",
                                              ROOT / "scripts/office_export_local_record.py")
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def good(**changes):
    record = {"schema": external.SCHEMA, "strategy_id": "QM-1", "version": "default:OBS-QM-3",
              "record_name": "OBS-QM-3", "rule_hashes": {"SPEC.md": "a" * 64},
              "source": {"file": "records/ledger.jsonl", "sha256": "b" * 64},
              "first_session": "2026-11-02", "through_session": "2026-11-11", "sessions": 8, "trades": 3,
              "trade_dates": 2, "figures": {"equity_usd": 100250.5, "return_pct": 0.2505, "open_positions": 4,
                                            "t_session": None},
              "generated_at": "2026-11-11T22:05:00+00:00"}
    record.update(changes)
    return record


def code(payload):
    with pytest.raises(external.ExternalRecordError) as caught:
        external.validate(payload)
    return caught.value.code


def test_a_well_formed_summary_is_accepted_unchanged():
    assert external.validate(good()) == good()


@pytest.mark.parametrize("changes, expected", [
    ({"bars": [[1, 2, 3]]}, "RECORD_FIELD_NOT_ALLOWED"),
    ({"note": "buy more"}, "RECORD_FIELD_NOT_ALLOWED"),
    ({"schema": "office.external_record.v0"}, "RECORD_SCHEMA_UNKNOWN"),
    ({"strategy_id": "QM 1; DROP"}, "RECORD_STRATEGY_INVALID"),
    ({"record_name": "<b>x</b>"}, "RECORD_NAME_INVALID"),
    ({"rule_hashes": {}}, "RECORD_RULE_HASHES_INVALID"),
    ({"rule_hashes": {"SPEC.md": "abc"}}, "RECORD_RULE_HASHES_INVALID"),
    ({"source": {"file": "../secrets.env", "sha256": "b" * 64}}, "RECORD_SOURCE_INVALID"),
    ({"source": {"file": "a.json", "sha256": "b" * 64, "content": "x"}}, "RECORD_SOURCE_INVALID"),
    ({"first_session": "2026-11-12"}, "RECORD_DATE_INVALID"),
    ({"generated_at": "2026-11-11T22:05:00"}, "RECORD_DATE_INVALID"),
    ({"trades": -1}, "RECORD_COUNT_INVALID"),
    ({"trades": True}, "RECORD_COUNT_INVALID"),
    ({"trade_dates": 9}, "RECORD_COUNT_INVALID"),
    ({"figures": {"last_price": 101.2}}, "RECORD_FIGURE_NOT_ALLOWED"),
    ({"figures": {"tickers": ["ACME"]}}, "RECORD_FIGURE_NOT_ALLOWED"),
    ({"figures": {"equity_usd": "100000"}}, "RECORD_FIGURES_INVALID"),
    ({"figures": {"equity_usd": float("nan")}}, "RECORD_FIGURES_INVALID"),
    ({"figures": {"open_positions": 1.5}}, "RECORD_FIGURES_INVALID"),
    ({"figures": {"decision_due": 1}}, "RECORD_FIGURES_INVALID"),
    ({"rule_hashes": {f"file-{i}": "a" * 64 for i in range(200)}}, "RECORD_TOO_LARGE"),
])
def test_a_summary_with_anything_else_in_it_is_refused(changes, expected):
    assert code(good(**changes)) == expected


def test_a_summary_missing_a_field_or_not_an_object_is_refused():
    record = good()
    del record["source"]
    assert code(record) == "RECORD_FIELD_MISSING" and code([good()]) == "RECORD_NOT_AN_OBJECT"


def test_no_figure_can_carry_text_and_no_field_is_a_price_or_a_ticker():
    assert set(external.FIGURES.values()) == {"number", "count", "flag"}
    names = " ".join((*external.TOP, *external.FIGURES)).lower()
    for word in ("price", "ticker", "symbol", "bar", "quote", "open", "close", "high", "low", "volume"):
        assert not re.search(rf"(^|[ _]){word}($|[ _])", names.replace("open_positions", "positions")), word


# ------------------------------------------------------------------------------------ the export script

def write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return hashlib.sha256(text.encode()).hexdigest()


def research_folder(tmp_path, names):
    """A made-up research folder holding the named frozen files. Returns it with their hashes."""
    root = tmp_path / "research"
    return root, {name: write(root / export.HASH_FILES[name], f"frozen rules: {name}\n") for name in names}


X2_NAMES = ("X2-FLOW_SPEC_v1.md", "X2_SHADOW_SPEC_v1.2.md", "x2_shadow.py")
QM_NAMES = ("discovery/exp_qm1/SPEC.md", "OBS-QM-3 spec")


def settled(root):
    trade = lambda s, flow, stress, earnings=False: {
        "tk": "ACME", "s": s, "side": 1, "ext": 2.4, "net_bps": 31.5, "exit": "close", "earnings": earnings,
        "spy_gap_pct": 0.41, "flow": flow, "stress_day": stress}
    data = {"start": "2026-11-09", "through": "2026-11-13", "sessions": 5, "prices_dir": "ibkr_2026-11-13",
            "trades": [trade("2026-11-09", True, False), trade("2026-11-09", True, True),
                       trade("2026-11-10", False, True), trade("2026-11-12", False, True, earnings=True)],
            "FLOW": {"earnings_covered_through": "2026-11-13", "sessions_unscored_no_earnings_data": 0, "n": 2,
                     "mean_bps": 31.5, "t_session": None, "book_maxdd": -0.0123, "decision_due": False},
            "STRESS": {"n": 2, "mean_bps": 12.25, "t_session": 0.8, "book_maxdd": None, "stress_sessions": 3}}
    write(root / export.X2_SETTLED, json.dumps(data))
    return data


def ledger(root, equities=(100000.0, 101000.0, 99990.0), *, break_chain=False):
    lines, previous = [], "0" * 64
    def add(kind, key, data):
        nonlocal previous
        line = json.dumps({"seq": len(lines), "prev": previous, "type": kind, "key": key, "data": data},
                          sort_keys=True)
        lines.append(line)
        previous = hashlib.sha256(line.encode()).hexdigest()
    add("run", {"run": "r1"}, {"code_verified": ["/somewhere/recorder.py"]})
    for day, equity in enumerate(equities, start=2):
        session = f"2026-11-{day:02d}"
        for account in ("D-1x", "EW-1x"):
            add("session", {"account": account, "session": session},
                {"equity": equity if account == "D-1x" else 100000.0, "cash": 5000.0, "gross": 95000.0,
                 "positions": 4, "used": [["SPY", session, "c", 752.1]]})
    add("order", {"account": "D-1x", "ticker": "ACME"}, {"session": "2026-11-02"})
    add("fill", {"account": "D-1x", "ticker": "ACME"}, {"session": "2026-11-02", "price_nominal": 187.31})
    add("exit", {"account": "D-1x", "ticker": "ACME", "lot": 1}, {"session": "2026-11-04", "price_nominal": 190.02})
    add("exit", {"account": "EW-1x", "ticker": "ACME", "lot": 1}, {"session": "2026-11-04"})
    if break_chain:
        lines[2] = lines[2].replace("100000.0", "100001.0")
    write(root / export.QM_LEDGER, "\n".join(lines) + "\n")


def test_the_flow_and_stress_summaries_hold_counts_and_net_figures_and_nothing_else(tmp_path):
    root, hashes = research_folder(tmp_path, X2_NAMES)
    settled(root)
    flow = export.summary(root, "X2-FLOW", "v1", hashes, NOW)
    stress = export.summary(root, "X2-STRESS", "v1", hashes, NOW)
    assert external.validate(flow) == flow and external.validate(stress) == stress
    assert (flow["sessions"], flow["trades"], flow["trade_dates"]) == (5, 2, 1)
    assert flow["figures"] == {"mean_net_bps": 31.5, "t_session": None, "max_drawdown_pct": -1.23,
                               "sessions_not_scored": 0, "decision_due": False}
    assert (stress["trades"], stress["trade_dates"]) == (2, 2)          # the earnings-day trade is not counted
    assert stress["figures"] == {"mean_net_bps": 12.25, "t_session": 0.8, "max_drawdown_pct": None,
                                 "stress_sessions": 3}
    assert flow["rule_hashes"] == hashes and flow["first_session"] == "2026-11-09"
    assert flow["source"] == {"file": export.X2_SETTLED,
                              "sha256": hashlib.sha256((root / export.X2_SETTLED).read_bytes()).hexdigest()}
    for text in (json.dumps(flow), json.dumps(stress)):
        assert "ACME" not in text and "ibkr" not in text and "spy_gap" not in text and str(tmp_path) not in text


def test_the_qm_summary_comes_from_the_primary_account_and_carries_no_price(tmp_path):
    root, hashes = research_folder(tmp_path, QM_NAMES)
    ledger(root)
    out = export.summary(root, "QM-1", "default:OBS-QM-3", hashes, NOW)
    assert external.validate(out) == out
    assert (out["first_session"], out["through_session"], out["sessions"]) == ("2026-11-02", "2026-11-04", 3)
    assert (out["trades"], out["trade_dates"]) == (1, 1)               # the other account's exit is not counted
    assert out["figures"] == {"equity_usd": 99990.0, "return_pct": -0.01, "max_drawdown_pct": -1.0,
                              "gross_usd": 95000.0, "open_positions": 4, "orders": 1, "fills": 1,
                              "missed_orders": 0, "fill_rejections": 0}
    text = json.dumps(out)
    assert "752.1" not in text and "187.31" not in text and "ACME" not in text and "SPY" not in text


def refusal(*args):
    with pytest.raises(export.Refused) as caught:
        export.summary(*args, NOW)
    return caught.value.args[0]


def test_a_changed_or_missing_rule_file_stops_the_export(tmp_path):
    root, hashes = research_folder(tmp_path, X2_NAMES)
    settled(root)
    (root / export.HASH_FILES["x2_shadow.py"]).write_text("frozen rules, edited\n")
    assert refusal(root, "X2-FLOW", "v1", hashes) == "RULE_HASH_MISMATCH:x2_shadow.py"
    (root / export.HASH_FILES["x2_shadow.py"]).unlink()
    assert refusal(root, "X2-FLOW", "v1", hashes) == "RULE_FILE_MISSING:x2_shadow.py"
    assert refusal(root, "X2-FLOW", "v1", {"renamed.md": "a" * 64}) == "RULE_FILE_MISSING:renamed.md"


def test_a_missing_empty_or_inconsistent_source_is_refused_not_guessed(tmp_path):
    root, hashes = research_folder(tmp_path, (*X2_NAMES, *QM_NAMES))
    x2, qm = {k: hashes[k] for k in X2_NAMES}, {k: hashes[k] for k in QM_NAMES}
    assert refusal(root, "X2-FLOW", "v1", x2) == "SOURCE_MISSING:" + export.X2_SETTLED
    assert refusal(root, "QM-1", "default:OBS-QM-3", qm) == "SOURCE_MISSING:" + export.QM_LEDGER
    data = settled(root)
    data["FLOW"]["n"] = 7
    write(root / export.X2_SETTLED, json.dumps(data))
    assert refusal(root, "X2-FLOW", "v1", x2) == "SOURCE_INCONSISTENT:TRADE_COUNT"
    ledger(root, break_chain=True)
    assert refusal(root, "QM-1", "default:OBS-QM-3", qm) == "SOURCE_INCONSISTENT:LEDGER_CHAIN"
    ledger(root, equities=())
    assert refusal(root, "QM-1", "default:OBS-QM-3", qm) == "SOURCE_EMPTY"


def test_the_script_writes_only_into_the_output_folder_and_reports_each_refusal(tmp_path, monkeypatch, capsys):
    root, hashes = research_folder(tmp_path, (*X2_NAMES, *QM_NAMES))
    ledger(root)
    monkeypatch.setattr(export, "inventory_hashes", lambda: {
        ("X2-FLOW", "v1"): {k: hashes[k] for k in X2_NAMES},
        ("QM-1", "default:OBS-QM-3"): {k: hashes[k] for k in QM_NAMES}})
    before = sorted(str(p) for p in root.rglob("*"))
    out = tmp_path / "uploads"
    assert export.main(["--research", str(root), "--out", str(out)]) == 1      # FLOW has no settled record yet
    printed = capsys.readouterr().out.splitlines()
    assert printed == ["QM-1: WRITTEN QM-1_2026-11-04.json sessions=3 trades=1",
                       "X2-FLOW: REFUSED SOURCE_MISSING:" + export.X2_SETTLED]
    assert [p.name for p in out.iterdir()] == ["QM-1_2026-11-04.json"]
    assert external.validate(json.loads((out / "QM-1_2026-11-04.json").read_text()))
    assert sorted(str(p) for p in root.rglob("*")) == before                    # the research folder is untouched
    assert export.main(["--research", str(root), "--out", str(root / "exports")]) == 1
    assert capsys.readouterr().out.strip() == "REFUSED OUTPUT_INSIDE_RESEARCH_FOLDER"
    assert not (root / "exports").exists()


def test_the_script_has_no_network_code_and_needs_only_the_standard_library():
    source = (ROOT / "scripts/office_export_local_record.py").read_text()
    for word in ("urllib", "requests", "httpx", "socket", "http.client", "subprocess", "os.environ", "getenv",
                 "stock_machine import", "psycopg"):
        assert word not in source, word
    imports = set(re.findall(r"^(?:from|import) ([a-z_]+)", source, flags=re.M))
    assert imports == {"__future__", "argparse", "hashlib", "json", "math", "sys", "datetime", "pathlib"}


def test_the_script_covers_exactly_the_strategies_the_inventory_keeps_on_the_owners_machine():
    kept_local = {(r["strategy_id"], r["version"]): r["hashes"] for r in inventory.read()
                  if r["admissible"] and r["execution_kind"] == "EXTERNAL_LOCAL"}
    assert export.inventory_hashes() == kept_local and set(kept_local) == set(export.STRATEGIES)
    assert {name for hashes in kept_local.values() for name in hashes} == set(export.HASH_FILES)
    assert export.SCHEMA == external.SCHEMA


# ------------------------------------------------------------------------------------ the upload route

@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app, base_url=ORIGIN)


def signed_in(monkeypatch):
    monkeypatch.setattr(store, "session", lambda *a, **kw: {"username": "admin", "must_change_password": False})


def test_an_upload_without_the_owners_session_stores_nothing(client, monkeypatch):
    monkeypatch.setattr(store, "session",
                        lambda *a, **kw: (_ for _ in ()).throw(security.PanelError("LOGIN_REQUIRED", 401)))
    monkeypatch.setattr(external, "import_record", lambda *a, **kw: pytest.fail("stored without a session"))
    reply = client.post("/api/operator/office/external-records", headers=H, json=good())
    assert reply.status_code == 401 and reply.json() == {"error_code": "LOGIN_REQUIRED"}
    assert client.post("/api/operator/office/external-records", json=good()).status_code == 403   # no origin


def test_the_owner_uploads_a_summary_and_refusals_come_back_as_codes(client, monkeypatch):
    signed_in(monkeypatch)
    seen = []
    monkeypatch.setattr(external, "import_record",
                        lambda data, uploaded_by: seen.append((data, uploaded_by)) or {"status": "CREATED"})
    reply = client.post("/api/operator/office/external-records", headers=H, json=good())
    assert reply.status_code == 200 and reply.json() == {"status": "CREATED"} and seen == [(good(), "admin")]
    extra = client.post("/api/operator/office/external-records", headers=H, json=good(bars=[1, 2]))
    assert extra.status_code == 400 and extra.json() == {"error_code": "INVALID_REQUEST"} and len(seen) == 1

    def mismatch(data, uploaded_by):
        raise external.ExternalRecordError("RULE_HASH_MISMATCH")
    monkeypatch.setattr(external, "import_record", mismatch)
    reply = client.post("/api/operator/office/external-records", headers=H, json=good())
    assert reply.status_code == 422 and reply.json() == {"error_code": "RULE_HASH_MISMATCH"}


def test_an_upload_is_refused_while_the_office_is_off(client, monkeypatch):
    signed_in(monkeypatch)
    monkeypatch.delenv("OFFICE_ENABLED", raising=False)
    monkeypatch.setattr(external.tasks, "connect", lambda: pytest.fail("the database was opened while off"))
    reply = client.post("/api/operator/office/external-records", headers=H, json=good())
    assert reply.status_code == 409 and reply.json() == {"error_code": "OFFICE_DISABLED"}
    assert config.enabled() is False


def test_the_module_adds_summaries_and_touches_no_strategy_deployment_order_or_fill():
    import inspect
    source = inspect.getsource(external)
    written = set(re.findall(r"INSERT INTO (\w+)", source))
    assert written == {"office_external_records"}
    assert not re.search(r"\b(UPDATE|DELETE|TRUNCATE|ALTER|DROP)\b", source)
    for name in ("office_deployments", "office_orders", "office_fills", "office_admission", "paper", "admission"):
        assert name not in source.replace("not broker", ""), name


def test_the_console_card_says_these_are_simulations_and_shows_no_raw_html():
    page, script = (ROOT / "webui/admin.html").read_text(), (ROOT / "webui/admin.js").read_text()
    assert 'id="office-record-files"' in page and "not broker fills" in page and "no prices" in page
    assert "api('/office/external-records','POST'" in script and "innerHTML" not in script
