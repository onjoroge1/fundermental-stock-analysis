"""Exercise the actual 0020 release runner with commit and rollback."""
import pytest
from tests.test_agent_lab_migration_runner import migration_db
from scripts.agent_lab_migration import apply_on_connection as apply_0019
from scripts.research_integrity_migration import apply_on_connection


def test_release_0020_commits_and_is_idempotent(migration_db):
    engine, schema = migration_db
    with engine.begin() as conn:
        conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        apply_0019(conn)
    with engine.begin() as conn:
        conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        result = apply_on_connection(conn)
        assert result["after"] == "0020_research_integrity" and result["audit_triggers"] == 12
    with engine.begin() as conn:
        conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        assert apply_on_connection(conn)["before"] == ["0020_research_integrity"]


def test_release_0020_failure_preserves_0019(migration_db):
    engine, schema = migration_db
    with engine.begin() as conn:
        conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        apply_0019(conn)
    with pytest.raises(RuntimeError, match="deliberate rollback"):
        with engine.begin() as conn:
            conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            apply_on_connection(conn)
            raise RuntimeError("deliberate rollback")
    with engine.begin() as conn:
        conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0019_agent_lab"
        assert conn.exec_driver_sql("SELECT to_regclass('research_evidence_records')").scalar_one() is None
