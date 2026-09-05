"""Real PostgreSQL migration/identity contracts; isolated schema per module.

CI supplies TEST_DATABASE_URL for its disposable PostgreSQL service. Without
that explicit test connection these integration tests skip.
"""
from copy import deepcopy
from io import StringIO
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from psycopg.types.json import Jsonb
import pytest

from stock_machine import db
from stock_machine.data_quality import assess_dataset

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires explicit disposable TEST_DATABASE_URL")


def migration_sql(revision):
    root = Path(__file__).resolve().parents[1]
    output = StringIO()
    cfg = Config(str(root / "alembic.ini"), output_buffer=output)
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg://unused:unused@localhost/test")
    command.upgrade(cfg, revision, sql=True)
    return output.getvalue()


@pytest.fixture(scope="module")
def conn():
    schema = "sm_test_" + uuid4().hex
    connection = psycopg.connect(os.environ["TEST_DATABASE_URL"])
    try:
        connection.execute(f'CREATE SCHEMA "{schema}"')
        connection.execute(f'SET search_path TO "{schema}"')
        connection.commit()
        connection.execute(migration_sql("0014_api_control_plane"))
        connection.execute("""INSERT INTO prediction_forecasts
            (ticker,as_of,model_version,status,payload) VALUES ('LEGACY','2026-01-02','old','OK','{"value":1}')""")
        connection.execute("""INSERT INTO alpha_probability_outcomes
            (ticker,as_of,model_version,horizon_days,target_date,prob_outperform,actual_excess_return_pct,actual_outperform)
            VALUES ('LEGACY','2026-01-02','old',5,'2026-01-09',0.5,1.0,true)""")
        connection.commit()
        connection.execute(migration_sql("0014_api_control_plane:head"))
        yield connection
    finally:
        connection.rollback()
        connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        connection.commit()
        connection.close()


def test_migration_links_legacy_outcome_to_exact_preserved_forecast(conn):
    row = conn.execute("""SELECT f.forecast_id,o.forecast_id,f.payload
        FROM prediction_forecasts f JOIN alpha_probability_outcomes o
        ON f.ticker=o.ticker WHERE f.ticker='LEGACY'""").fetchone()
    assert row[0] == row[1]
    assert row[0].startswith("legacy_")
    assert row[2] == {"value": 1}
    db.init_schema(conn)


def test_identical_forecast_reruns_are_idempotent_but_changed_inputs_append(conn):
    original = {"ticker": "IDENTITY", "as_of": "2026-09-04", "model_version": "test.v1", "status": "OK",
                "input_data_versions": {"consensus": "sha256:one"},
                "forecast_distribution": {"generated_at": "2026-09-04T21:00:00Z"}, "value": 1}
    db.save_prediction_forecast(conn, original)
    rerun = deepcopy(original)
    rerun["forecast_distribution"]["generated_at"] = "2026-09-04T22:00:00Z"
    db.save_prediction_forecast(conn, rerun)
    revised = deepcopy(original)
    revised["input_data_versions"]["consensus"] = "sha256:two"
    revised["value"] = 2
    db.save_prediction_forecast(conn, revised)
    rows = conn.execute("SELECT forecast_id,payload FROM prediction_forecasts WHERE ticker='IDENTITY'").fetchall()
    assert len(rows) == 2
    assert len({r[0] for r in rows}) == 2
    assert sorted(r[1]["value"] for r in rows) == [1, 2]
    first = next(r[1] for r in rows if r[1]["value"] == 1)
    assert first["forecast_distribution"]["generated_at"] == "2026-09-04T21:00:00Z"


def test_retrieving_unchanged_content_preserves_original_observation(conn):
    snapshot = assess_dataset("prices", [{"date": "2026-09-04", "close": 100, "volume": 10}])
    snapshot["payload"] = [{"value": "original"}]
    db.record_dataset_snapshots(conn, "RETRIEVAL", [snapshot])
    conn.execute("UPDATE dataset_snapshots SET observed_at='2020-01-01',last_checked_at='2020-01-01' WHERE ticker='RETRIEVAL'")
    conn.commit()
    db.record_dataset_snapshots(conn, "RETRIEVAL", [snapshot])
    rows = conn.execute("SELECT observed_at::date::text,last_checked_at>observed_at,payload FROM dataset_snapshots WHERE ticker='RETRIEVAL'").fetchall()
    assert rows == [("2020-01-01", True, [{"value": "original"}])]


def test_surprise_rerun_does_not_backdate_or_duplicate_observation(conn):
    row = {"date": "2025-01-20", "actual_eps": 1.1, "estimated_eps": 1.0, "surprise_pct": 10.0}
    db.upsert_surprises(conn, "VINTAGE", [row])
    db.upsert_surprises(conn, "VINTAGE", [row])
    db.upsert_surprises(conn, "VINTAGE", [{**row, "actual_eps": 1.2, "surprise_pct": 20.0}])
    records = conn.execute("SELECT event_date::text,observed_at::date::text,surprise_pct FROM earnings_surprise_vintages WHERE ticker='VINTAGE'").fetchall()
    assert len(records) == 2
    assert all(r[1] > r[0] for r in records)
    assert sorted(r[2] for r in records) == [10.0, 20.0]


def test_health_api_reads_migrated_store_and_refresh_requires_authentication(conn, monkeypatch):
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient
    from stock_machine import market_health
    from stock_machine.webapp_automation import app
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    conn.execute("INSERT INTO companies(ticker,cik) VALUES ('APIHEALTH','test')")
    conn.execute("INSERT INTO prices_daily(ticker,date,close,adj_close,volume) VALUES ('APIHEALTH','2026-09-04',100,100,10)")
    conn.commit()
    snapshot = assess_dataset("prices", [{"date": "2026-09-04", "close": 100, "volume": 10}])
    db.record_dataset_snapshots(conn, "APIHEALTH", [snapshot])
    def connect():
        c = psycopg.connect(os.environ["TEST_DATABASE_URL"])
        c.execute(f'SET search_path TO "{schema}"')
        c.commit()
        return c
    monkeypatch.setattr(db, "connect", connect)
    monkeypatch.setattr(market_health, "_now_utc", lambda: datetime(2026, 9, 5, 12, tzinfo=timezone.utc))
    client = TestClient(app)
    response = client.get("/api/v1/data-health")
    assert response.status_code == 200
    assert response.json()["current_count"] == 1
    assert client.post("/api/v1/data-refresh?tickers=APIHEALTH").status_code in (401, 503)


def test_pit_readers_filter_fiscal_basis_and_future_effective_dates(conn):
    conn.execute("""INSERT INTO consensus_snapshots(ticker,snapshot_date,period_type,forecast_period_end,eps_mean)
                    VALUES ('READER','2025-01-01','annual','2025-12-31',2),
                           ('READER','2025-01-02','quarter','2025-02-15',1)""")
    conn.execute("""INSERT INTO shares_outstanding(ticker,as_of,shares,available_at)
                    VALUES ('READER','2025-03-01',100,'2025-01-01')""")
    conn.commit()
    rows = db.fetch_consensus(conn, "READER", "2025-02-01")
    assert len(rows) == 1 and rows[0]["period_type"] == "annual"
    assert db.fetch_consensus(conn, "READER", "2025-01-01") == []
    assert db.fetch_shares(conn, "READER", "2025-02-01") == []
    assert db.fetch_periods(conn, "READER", "quarter", "2025-02-01") == []
    assert db.fetch_surprises(conn, "VINTAGE", "2025-01-25") == []
