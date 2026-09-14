"""Atomic migration 0019 using the configured database, never a rewritten DSN."""
from pathlib import Path

TARGET = "0019_agent_lab"
TABLES = ("agent_lab_policies", "agent_lab_evidence", "agent_lab_decisions", "agent_lab_events", "agent_lab_report_outbox")


def error_code(exc):
    """Classify in memory; return only a fixed code, never the raw exception."""
    message = str(getattr(exc, "orig", exc)).lower()
    for phrases, code in (
        (("unsupported startup parameter",), "DB_UNSUPPORTED_STARTUP_PARAMETER"),
        (("password authentication failed", "authentication failed", "invalid password"), "DB_AUTHENTICATION_FAILED"),
        (("could not translate host name", "name or service not known", "connection refused", "network is unreachable"), "DB_NETWORK_UNAVAILABLE"),
        (("lock timeout",), "DB_LOCK_TIMEOUT"),
        (("statement timeout",), "DB_STATEMENT_TIMEOUT"),
        (("ssl", "certificate verify failed"), "DB_TLS_ERROR"),
    ):
        if any(phrase in message for phrase in phrases):
            return code
    return "DB_OPERATION_FAILED"


def apply_on_connection(connection):
    """Caller must own a transaction; no session state across pool checkouts."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    if not connection.in_transaction():
        raise RuntimeError("MIGRATION_REQUIRES_TRANSACTION")
    connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
    connection.exec_driver_sql("SET LOCAL statement_timeout = '120s'")
    locked = connection.exec_driver_sql("SELECT pg_try_advisory_xact_lock(hashtextextended('agent-lab-release-0019',0))").scalar_one()
    if not locked:
        raise RuntimeError("ANOTHER_RELEASE_HOLDS_DATABASE_LOCK")
    versions = set(connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalars())
    if versions not in ({"0018_consensus_archives"}, {TARGET}):
        raise RuntimeError("UNEXPECTED_DATABASE_REVISION")
    if versions != {TARGET}:
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
        config.attributes["connection"] = connection
        command.upgrade(config, TARGET)
    after = set(connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalars())
    if after != {TARGET}:
        raise RuntimeError("SCHEMA_VERIFICATION_FAILED")
    for table in TABLES:
        exists = connection.execute(text("SELECT to_regclass(:table)"), {"table": table}).scalar_one()
        triggers = connection.execute(text("SELECT count(*) FROM pg_trigger WHERE tgrelid=CAST(:table AS regclass) AND NOT tgisinternal AND tgenabled='O'"), {"table": table}).scalar_one()
        if exists is None or triggers != 2:
            raise RuntimeError("JOURNAL_AUDIT_PROTECTION_MISSING")
    return {"before": sorted(versions), "after": TARGET, "verified_tables": list(TABLES), "audit_triggers": 10, "transaction_scoped_lock": True}


def migrate():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from stock_machine import db
    from stock_machine.config import DATABASE_URL

    if db.REQUIRED_SCHEMA_VERSION != TARGET:
        raise RuntimeError("SCHEMA_DECLARATION_MISMATCH")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL_NOT_CONFIGURED")
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgres://")
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 20})
    try:
        with engine.begin() as connection:
            result = apply_on_connection(connection)
        return result  # returned only after the outer transaction commits
    finally:
        engine.dispose()
