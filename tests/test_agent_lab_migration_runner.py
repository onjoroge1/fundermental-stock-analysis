"""External Alembic transaction and error-redaction regression tests."""
import os
from pathlib import Path
from uuid import uuid4

import pytest

from scripts.agent_lab_migration import apply_on_connection, error_code


@pytest.mark.parametrize("message,code", [
    ("unsupported startup parameter: lock_timeout secret=SENSITIVE", "DB_UNSUPPORTED_STARTUP_PARAMETER"),
    ("password authentication failed secret=SENSITIVE", "DB_AUTHENTICATION_FAILED"),
    ("connection refused secret=SENSITIVE", "DB_NETWORK_UNAVAILABLE"),
    ("unclassified secret=SENSITIVE", "DB_OPERATION_FAILED"),
])
def test_connection_diagnostics_never_return_raw_errors(message, code):
    assert error_code(RuntimeError(message)) == code


@pytest.fixture
def migration_db():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not configured")
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from alembic import command
    from alembic.config import Config
    url = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, poolclass=NullPool)
    schema = "test_agent_release_" + uuid4().hex
    with engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        cfg = Config()
        cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0018_consensus_archives")
    try:
        yield engine, schema
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()


def test_migration_and_audit_checks_commit_together(migration_db):
    engine, schema = migration_db
    with engine.begin() as conn:
        conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        result = apply_on_connection(conn)
        assert result["after"] == "0019_agent_lab" and result["audit_triggers"] == 10
        assert conn.exec_driver_sql("SHOW lock_timeout").scalar_one() == "10s"
    with engine.begin() as conn:
        conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0019_agent_lab"
        assert apply_on_connection(conn)["before"] == ["0019_agent_lab"]


def test_outer_failure_rolls_back_schema_and_revision(migration_db):
    engine, schema = migration_db
    with pytest.raises(RuntimeError, match="test failure"):
        with engine.begin() as conn:
            conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            apply_on_connection(conn)
            raise RuntimeError("test failure before commit")
    with engine.begin() as conn:
        conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0018_consensus_archives"
        assert conn.exec_driver_sql("SELECT to_regclass('agent_lab_decisions')").scalar_one() is None
