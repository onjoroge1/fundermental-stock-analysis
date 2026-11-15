"""Checks on the office that need no database: it is off by default, it cannot reach a broker, its migration
only adds tables, the migration can only be started by hand, and frozen evaluation code is untouched."""
import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from stock_machine import db
from stock_machine.office import config

ROOT = Path(__file__).resolve().parents[1]
OFFICE = ROOT / "stock_machine" / "office"
# Paper and simulation only: the office may not import a broker client, the IBKR bridge or feed, or the desk.
FORBIDDEN = ("ibapi", "ib_insync", "ib_async", "stock_machine.ibkr_bridge", "stock_machine.market_data.ibkr_tws",
             "stock_machine.market_data.ibkr_quotes", "desk", "stock_machine_desk", "alpaca", "ccxt")
FORBIDDEN_NAMES = {"ibkr_bridge", "ibkr_tws", "ibkr_quotes", "placeOrder", "cancelOrder", "reqGlobalCancel"}


def office_violations(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            modules, names = [a.name for a in node.names], []
        elif isinstance(node, ast.ImportFrom):
            # resolve "from .. import x" / "from ..market_data import y" to absolute module names
            base = "stock_machine" if node.level >= 2 else "stock_machine.office" if node.level == 1 else ""
            module = ".".join(p for p in (base, node.module) if p)
            modules, names = [module] + [f"{module}.{a.name}" for a in node.names], [a.name for a in node.names]
        elif isinstance(node, ast.Attribute | ast.Name):
            modules, names = [], [node.attr if isinstance(node, ast.Attribute) else node.id]
        else:
            continue
        for module in modules:
            if any(module == f or module.startswith(f + ".") for f in FORBIDDEN):
                found.append(f"{path.name}:{node.lineno}: imports {module}")
        found += [f"{path.name}:{node.lineno}: {n}" for n in names if n in FORBIDDEN_NAMES]
    return found


def test_the_office_cannot_reach_a_broker_or_the_ibkr_feed():
    files = sorted(OFFICE.rglob("*.py"))
    assert files, "office package not found"
    assert [v for f in files for v in office_violations(f)] == []


def test_the_broker_check_catches_planted_imports(tmp_path):
    cases = ("from .. import ibkr_bridge\n", "from ..market_data import ibkr_tws\n", "import ibapi.client\n",
             "from stock_machine.ibkr_bridge import app\n", "def go(app):\n    app.placeOrder(1)\n",
             "import desk.engine\n")
    for n, code in enumerate(cases):
        path = tmp_path / f"case{n}.py"
        path.write_text(code)
        assert office_violations(path), code
    ok = tmp_path / "ok.py"
    ok.write_text('"""Never calls placeOrder."""\nfrom .. import db\nfrom . import config\n')
    assert office_violations(ok) == []


def test_everything_is_off_unless_the_exact_word_true_is_set(monkeypatch):
    for name in ("OFFICE_ENABLED", "OFFICE_PAPER_ENABLED", "OFFICE_MODEL_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    assert not config.enabled() and not config.paper_enabled() and config.provider() == "none"
    with pytest.raises(config.OfficeDisabled):
        config.require_enabled()
    for value in ("", "1", "yes", "True", "TRUE", "on", " false "):
        monkeypatch.setenv("OFFICE_ENABLED", value)
        assert not config.enabled(), value
    monkeypatch.setenv("OFFICE_PAPER_ENABLED", "true")
    monkeypatch.setenv("OFFICE_ENABLED", "false")
    assert not config.paper_enabled()            # the paper book cannot be on while the office is off
    monkeypatch.setenv("OFFICE_ENABLED", "true")
    assert config.enabled() and config.paper_enabled()


def test_no_checked_in_file_turns_the_office_on():
    hits = []
    for path in [ROOT / "vercel.json", ROOT / ".env.example", *sorted((ROOT / ".github/workflows").glob("*.yml"))]:
        if path.exists():
            text = path.read_text()
            hits += [path.name for flag in ("OFFICE_ENABLED", "OFFICE_PAPER_ENABLED") if flag in text]
    assert hits == []


def test_retry_delays_grow_and_are_capped():
    assert [config.retry_delay_seconds(n) for n in (0, 1, 2, 3)] == [60, 60, 120, 240]
    assert config.retry_delay_seconds(50) == config.RETRY_CAP_SECONDS == 3600


def _connection_with_versions(versions):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [(v,) for v in versions]
    return conn


def test_existing_code_accepts_the_database_before_and_after_the_office_migration():
    assert db.REQUIRED_SCHEMA_VERSION == config.OFFICE_SCHEMA_VERSION == "0035_office_qa"
    assert db.COMPATIBLE_SCHEMA_VERSIONS == {"0028_fair_value_forward", "0029_office_tasks",
                                             "0030_office_budget", "0031_office_research",
                                             "0032_office_strategies", "0033_office_paper",
                                             "0034_office_external", "0035_office_qa"}
    for version in sorted(db.COMPATIBLE_SCHEMA_VERSIONS):
        conn = _connection_with_versions([version])
        db.init_schema(conn)
        conn.commit.assert_called_once()
    for versions in ([], ["0027_agent_outcome_checks"], ["0036_not_yet"],
                     ["0028_fair_value_forward", "0029_office_tasks"]):
        with pytest.raises(RuntimeError, match="Database migration required"):
            db.init_schema(_connection_with_versions(versions))


def test_the_migration_only_adds_office_objects():
    from tests.test_db_integrity import migration_sql
    statements = migration_sql("0028_fair_value_forward:0029_office_tasks")
    body = statements.split("-- Running upgrade", 1)[1]
    assert body.index("SET LOCAL lock_timeout") < body.index("CREATE TABLE")
    created = [line.split()[2] for line in body.splitlines() if line.startswith("CREATE TABLE")]
    assert created == ["office_agent_definitions", "office_tasks", "office_task_dependencies", "office_runs"]
    upper = body.upper()
    assert "ALTER TABLE" not in upper and "DROP " not in upper and "DELETE FROM" not in upper
    # the only write outside office_ objects is Alembic recording the new revision
    writes = [line for line in body.splitlines() if line.startswith(("UPDATE ", "INSERT "))]
    assert writes == ["UPDATE alembic_version SET version_num='0029_office_tasks' "
                      "WHERE alembic_version.version_num = '0028_fair_value_forward';"]
    for line in body.splitlines():
        if line.startswith(("CREATE INDEX", "CREATE TRIGGER", "CREATE FUNCTION")):
            assert "office_" in line, line


@pytest.mark.parametrize("number", ["0029", "0030", "0031", "0032", "0033", "0034", "0035"])
def test_the_production_migration_can_only_be_started_by_hand(number):
    text = (ROOT / f".github/workflows/office-migrate-{number}.yml").read_text()
    trigger = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "workflow_dispatch:" in trigger
    assert not any(word in trigger for word in ("push:", "schedule:", "pull_request", "workflow_run"))
    assert f"inputs.confirm == 'MIGRATE-{number}'" in text and "github.ref == 'refs/heads/main'" in text
    assert "contents: read" in text and "OFFICE_ENABLED" not in text


def test_the_budget_migration_only_adds_one_office_table():
    from tests.test_db_integrity import migration_sql
    body = migration_sql("0029_office_tasks:0030_office_budget").split("-- Running upgrade", 1)[1]
    created = [line.split()[2] for line in body.splitlines() if line.startswith("CREATE TABLE")]
    assert created == ["office_budget_reservations"]
    upper = body.upper()
    assert "ALTER TABLE" not in upper and "DROP " not in upper and "DELETE FROM" not in upper
    assert [line for line in body.splitlines() if line.startswith(("UPDATE ", "INSERT "))] == [
        "UPDATE alembic_version SET version_num='0030_office_budget' "
        "WHERE alembic_version.version_num = '0029_office_tasks';"]


def test_the_research_migration_only_adds_four_office_tables():
    from tests.test_db_integrity import migration_sql
    body = migration_sql("0030_office_budget:0031_office_research").split("-- Running upgrade", 1)[1]
    created = [line.split()[2] for line in body.splitlines() if line.startswith("CREATE TABLE")]
    assert created == ["office_events", "office_memos", "office_reviews", "office_briefings"]
    upper = body.upper()
    assert "ALTER TABLE" not in upper and "DROP " not in upper and "DELETE FROM" not in upper
    assert [line for line in body.splitlines() if line.startswith(("UPDATE ", "INSERT "))] == [
        "UPDATE alembic_version SET version_num='0031_office_research' "
        "WHERE alembic_version.version_num = '0030_office_budget';"]
    for line in body.splitlines():
        if line.startswith(("CREATE INDEX", "CREATE TRIGGER", "CREATE FUNCTION")):
            assert "office_" in line, line


def test_the_strategy_migration_only_adds_four_office_tables_and_has_no_live_state():
    from tests.test_db_integrity import migration_sql
    body = migration_sql("0031_office_research:0032_office_strategies").split("-- Running upgrade", 1)[1]
    created = [line.split()[2] for line in body.splitlines() if line.startswith("CREATE TABLE")]
    assert created == ["office_strategy_versions", "office_books", "office_admission_decisions",
                       "office_deployments"]
    upper = body.upper()
    assert "ALTER TABLE" not in upper and "DROP " not in upper and "DELETE FROM" not in upper
    assert "'LIVE'" not in upper and "BROKER" not in upper
    assert [line for line in body.splitlines() if line.startswith(("UPDATE ", "INSERT "))] == [
        "UPDATE alembic_version SET version_num='0032_office_strategies' "
        "WHERE alembic_version.version_num = '0031_office_research';"]
    for line in body.splitlines():
        if line.startswith(("CREATE INDEX", "CREATE UNIQUE INDEX", "CREATE TRIGGER", "CREATE FUNCTION")):
            assert "office_" in line, line


def test_the_paper_migration_only_adds_five_office_tables_and_cannot_hold_a_broker_fill():
    from tests.test_db_integrity import migration_sql
    body = migration_sql("0032_office_strategies:0033_office_paper").split("-- Running upgrade", 1)[1]
    created = [line.split()[2] for line in body.splitlines() if line.startswith("CREATE TABLE")]
    assert created == ["office_opportunities", "office_orders", "office_fills", "office_positions", "office_marks"]
    upper = body.upper()
    assert "ALTER TABLE" not in upper and "DROP " not in upper and "DELETE FROM" not in upper
    assert "CHECK(NOT BROKER_FILL)" in upper and upper.count("CHECK(SIMULATED)") == 2
    assert [line for line in body.splitlines() if line.startswith(("UPDATE ", "INSERT "))] == [
        "UPDATE alembic_version SET version_num='0033_office_paper' "
        "WHERE alembic_version.version_num = '0032_office_strategies';"]
    for line in body.splitlines():
        if line.startswith(("CREATE INDEX", "CREATE UNIQUE INDEX", "CREATE TRIGGER", "CREATE FUNCTION")):
            assert "office_" in line, line


def test_the_mirror_migration_only_adds_one_office_table_and_cannot_hold_a_broker_fill():
    from tests.test_db_integrity import migration_sql
    body = migration_sql("0033_office_paper:0034_office_external").split("-- Running upgrade", 1)[1]
    created = [line.split()[2] for line in body.splitlines() if line.startswith("CREATE TABLE")]
    assert created == ["office_external_records"]
    upper = body.upper()
    assert "ALTER TABLE" not in upper and "DROP " not in upper and "DELETE FROM" not in upper
    assert "CHECK(NOT BROKER_FILL)" in upper and "CHECK(SIMULATED)" in upper
    assert [line for line in body.splitlines() if line.startswith(("UPDATE ", "INSERT "))] == [
        "UPDATE alembic_version SET version_num='0034_office_external' "
        "WHERE alembic_version.version_num = '0033_office_paper';"]
    for line in body.splitlines():
        if line.startswith(("CREATE INDEX", "CREATE UNIQUE INDEX", "CREATE TRIGGER", "CREATE FUNCTION")):
            assert "office_" in line, line


def test_the_qa_migration_only_adds_three_office_tables_and_no_qualified_or_live_state():
    from tests.test_db_integrity import migration_sql
    body = migration_sql("0034_office_external:0035_office_qa").split("-- Running upgrade", 1)[1]
    created = [line.split()[2] for line in body.splitlines() if line.startswith("CREATE TABLE")]
    assert created == ["office_evaluations", "office_proposals", "office_deployment_events"]
    upper = body.upper()
    assert "ALTER TABLE" not in upper and "DROP " not in upper and "DELETE FROM" not in upper
    assert "'LIVE'" not in upper and "BROKER" not in upper and "QUALIFIED_PAPER" not in upper
    assert "CHECK(SIMULATED)" in upper
    assert [line for line in body.splitlines() if line.startswith(("UPDATE ", "INSERT "))] == [
        "UPDATE alembic_version SET version_num='0035_office_qa' "
        "WHERE alembic_version.version_num = '0034_office_external';"]
    for line in body.splitlines():
        if line.startswith(("CREATE INDEX", "CREATE UNIQUE INDEX", "CREATE TRIGGER", "CREATE FUNCTION")):
            assert "office_" in line, line


def test_this_change_leaves_frozen_evaluation_code_and_its_registered_hashes_alone():
    from stock_machine.agent_intelligence import fair_value_forward
    from tests.test_fair_value_forward import PINNED_PROTOCOL_SHA256
    assert fair_value_forward.PROTOCOL_SHA256 == PINNED_PROTOCOL_SHA256
    assert "db.py" not in fair_value_forward.DECISION_SOURCES
    assert not any("office" in str(source) for source in fair_value_forward.DECISION_SOURCES)
