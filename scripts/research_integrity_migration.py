"""Additive 0020 release with one transaction and verified audit protections."""
from pathlib import Path

TARGET = "0020_research_integrity"


def apply_on_connection(connection):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    if not connection.in_transaction():
        raise RuntimeError("MIGRATION_REQUIRES_TRANSACTION")
    connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
    connection.exec_driver_sql("SET LOCAL statement_timeout = '120s'")
    if not connection.exec_driver_sql("SELECT pg_try_advisory_xact_lock(hashtextextended('research-integrity-release-0020',0))").scalar_one():
        raise RuntimeError("ANOTHER_RELEASE_HOLDS_DATABASE_LOCK")
    before = set(connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalars())
    if before not in ({"0019_agent_lab"}, {TARGET}):
        raise RuntimeError("UNEXPECTED_DATABASE_REVISION")
    if before != {TARGET}:
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
        config.attributes["connection"] = connection
        command.upgrade(config, TARGET)
    after = set(connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalars())
    if after != {TARGET}:
        raise RuntimeError("SCHEMA_VERIFICATION_FAILED")
    from scripts.agent_lab_migration import TABLES
    for table in (*TABLES, "research_evidence_records"):
        count = connection.execute(text("SELECT count(*) FROM pg_trigger WHERE tgrelid=CAST(:table AS regclass) AND NOT tgisinternal AND tgenabled='O'"), {"table": table}).scalar_one()
        if count != 2:
            raise RuntimeError("AUDIT_PROTECTION_MISSING")
    return {"before": sorted(before), "after": TARGET, "audit_triggers": 12, "transaction_scoped_lock": True}


def migrate():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from stock_machine.config import DATABASE_URL
    from stock_machine.db import REQUIRED_SCHEMA_VERSION
    if REQUIRED_SCHEMA_VERSION != TARGET or not DATABASE_URL:
        raise RuntimeError("SCHEMA_OR_DATABASE_CONFIGURATION_INVALID")
    url = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1).replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 20})
    try:
        with engine.begin() as connection:
            result = apply_on_connection(connection)
        return result
    finally:
        engine.dispose()
