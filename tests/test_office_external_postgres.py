"""Real PostgreSQL checks for mirrored local records, on the research test schema.

The summaries here are made up. The inventory rows they point at are the checked-in ones, unchanged.
"""
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from stock_machine.office import config, external, inventory, status
from tests.test_office_research_postgres import pg, sql  # noqa: F401

QM = ("QM-1", "default:OBS-QM-3")


def hashes(key=QM):
    return next(r["hashes"] for r in inventory.read() if (r["strategy_id"], r["version"]) == key)


def summary(key=QM, through="2026-11-11", source="b" * 64, generated="2026-11-11T22:05:00+00:00", **changes):
    record = {"schema": external.SCHEMA, "strategy_id": key[0], "version": key[1], "record_name": "OBS-QM-3",
              "rule_hashes": hashes(key), "source": {"file": "records/ledger.jsonl", "sha256": source},
              "first_session": "2026-11-02", "through_session": through, "sessions": 8, "trades": 0,
              "trade_dates": 0, "figures": {"equity_usd": 100000.0, "return_pct": 0.0, "open_positions": 0},
              "generated_at": generated}
    record.update(changes)
    return record


@pytest.fixture
def loaded(pg):
    inventory.load()
    return pg


def stored(pg):
    return sql(pg, "SELECT count(*) FROM office_external_records")[0][0]


def refused(payload):
    with pytest.raises(external.ExternalRecordError) as caught:
        external.import_record(payload, uploaded_by="admin")
    return caught.value.code


def test_a_summary_is_stored_once_and_a_repeated_upload_adds_nothing(loaded):
    first = external.import_record(summary(), uploaded_by="admin")
    assert first == {"status": "CREATED", "strategy_id": "QM-1", "version": "default:OBS-QM-3",
                     "through_session": "2026-11-11", "simulated": True, "broker_fill": False}
    # exported again later from the same source file: the same record
    again = external.import_record(summary(generated="2026-11-12T09:00:00+00:00"), uploaded_by="admin")
    assert again["status"] == "PRESENT" and stored(loaded) == 1
    row = sql(loaded, "SELECT sessions, trades, figures, simulated, broker_fill, uploaded_by, rule_hashes "
                      "FROM office_external_records")[0]
    assert row == (8, 0, {"equity_usd": 100000.0, "return_pct": 0.0, "open_positions": 0}, True, False, "admin",
                   hashes())


def test_the_same_source_cannot_be_stored_again_with_different_figures(loaded):
    external.import_record(summary(), uploaded_by="admin")
    kinder = summary(figures={"equity_usd": 125000.0, "return_pct": 25.0, "open_positions": 0})
    assert refused(kinder) == "RECORD_ALREADY_STORED_WITH_DIFFERENT_CONTENT" and stored(loaded) == 1


def test_a_changed_rule_hash_is_refused_in_code_and_in_the_database(loaded):
    changed = {**hashes(), "OBS-QM-3 spec": "c" * 64}
    assert refused(summary(rule_hashes=changed)) == "RULE_HASH_MISMATCH"
    assert refused(summary(rule_hashes={"OBS-QM-3 spec": hashes()["OBS-QM-3 spec"]})) == "RULE_HASH_MISMATCH"
    assert stored(loaded) == 0
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        insert(loaded, rule_hashes=changed)
    assert stored(loaded) == 0


def insert(pg, key=QM, **changes):
    row = {"record_id": uuid4(), "strategy_id": key[0], "version": key[1], "record_name": "OBS-QM-3",
           "first_session": "2026-11-02", "through_session": "2026-11-11", "sessions": 8, "trades": 0,
           "trade_dates": 0, "figures": {}, "rule_hashes": hashes(key) if key == QM else {"x": "a" * 64},
           "source_file": "records/ledger.jsonl", "source_sha256": "b" * 64, "payload_sha256": "d" * 64,
           "generated_at": "2026-11-11T22:05:00+00:00", "uploaded_by": "admin"}
    row.update(changes)
    values = [Jsonb(v) if isinstance(v, dict) else v for v in row.values()]
    sql(pg, f"INSERT INTO office_external_records({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})", values)


def test_only_a_strategy_kept_on_the_owners_machine_can_be_mirrored(loaded):
    native = ("fair-value-convergence.v1:sector_timed_120", "forward.v1")
    assert refused(summary(native)) == "STRATEGY_NOT_MIRRORED"
    assert refused(summary(strategy_id="EXT-FADE", version="v1", rule_hashes={"x": "a" * 64})) == "STRATEGY_NOT_MIRRORED"
    assert refused(summary(strategy_id="NOT-A-STRATEGY", version="v1")) == "STRATEGY_NOT_IN_INVENTORY"
    for key in (native, ("EXT-FADE", "v1")):                              # the database refuses them too
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            insert(loaded, key, rule_hashes=hashes(key) or {"x": "a" * 64})
    assert stored(loaded) == 0


def test_nothing_is_stored_while_the_office_is_off(loaded, monkeypatch):
    monkeypatch.setenv("OFFICE_ENABLED", "false")
    with pytest.raises(config.OfficeDisabled):
        external.import_record(summary(), uploaded_by="admin")
    assert stored(loaded) == 0


def test_a_stored_summary_cannot_be_changed_removed_or_marked_as_a_broker_fill(loaded):
    external.import_record(summary(), uploaded_by="admin")
    for statement in ("UPDATE office_external_records SET trades = 99",
                      "UPDATE office_external_records SET figures = '{\"return_pct\": 50}'",
                      "DELETE FROM office_external_records", "TRUNCATE office_external_records"):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            sql(loaded, statement)
    for changes in ({"broker_fill": True}, {"simulated": False}, {"trade_dates": 3},
                    {"first_session": "2026-12-01"}):
        with pytest.raises(psycopg.errors.CheckViolation):
            insert(loaded, source_sha256="e" * 64, **changes)
    assert stored(loaded) == 1


def test_an_upload_admits_nothing_and_creates_no_deployment_order_or_fill(loaded):
    before = sql(loaded, "SELECT (SELECT count(*) FROM office_strategy_versions), "
                         "(SELECT max(created_at) FROM office_strategy_versions)")
    external.import_record(summary(), uploaded_by="admin")
    for table in ("office_admission_decisions", "office_deployments", "office_books", "office_opportunities",
                  "office_orders", "office_fills", "office_positions", "office_marks", "office_tasks"):
        assert sql(loaded, f"SELECT count(*) FROM {table}")[0][0] == 0, table
    assert sql(loaded, "SELECT (SELECT count(*) FROM office_strategy_versions), "
                       "(SELECT max(created_at) FROM office_strategy_versions)") == before


def test_the_status_shows_the_newest_summary_of_each_strategy_as_a_simulation_kept_elsewhere(loaded):
    assert status.summary()["mirrored_records"] == []
    external.import_record(summary(), uploaded_by="admin")
    external.import_record(summary(through="2026-11-12", source="f" * 64, sessions=9, trades=2, trade_dates=1),
                           uploaded_by="admin")
    shown = status.summary()["mirrored_records"]
    assert len(shown) == 1 and shown[0]["through_session"] == "2026-11-12"
    assert (shown[0]["sessions"], shown[0]["trades"], shown[0]["trade_dates"], shown[0]["uploads"]) == (9, 2, 1, 2)
    assert shown[0]["kept"] == "ON_OWNER_MACHINE" and shown[0]["simulated"] is True
    assert shown[0]["broker_fill"] is False
