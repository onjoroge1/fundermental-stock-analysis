"""Alembic environment for Stock Machine's Postgres schema."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from stock_machine.config import DATABASE_URL

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _sqlalchemy_url(url: str) -> str:
    """Select the installed psycopg v3 driver for common Postgres URLs."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


if DATABASE_URL:
    config.set_main_option("sqlalchemy.url", _sqlalchemy_url(DATABASE_URL))


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    if not url or url == "postgresql+psycopg://":
        raise RuntimeError("DATABASE_URL is required for database migrations")
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url")
    if not url or url == "postgresql+psycopg://":
        raise RuntimeError("DATABASE_URL is required for database migrations")
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
