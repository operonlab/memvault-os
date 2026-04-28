"""Alembic environment for memvault-os (async / asyncpg).

DATABASE_URL is read from the environment and overrides alembic.ini.
target_metadata aggregates Base from src.shared.models plus the memvault
ORM modules (models.py, kg_models.py) and the audit_stub.

Until Worker A lands `apps/api/src/memvault/` and `apps/api/src/shared/`,
the imports are wrapped in try/except so this file is at least importable
for `alembic upgrade head` will only succeed once Base.metadata is populated.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

db_url = os.getenv("DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)


# ---------------------------------------------------------------------------
# Model imports — populate Base.metadata
# ---------------------------------------------------------------------------
# NOTE: pending Worker A. `src.shared.models` and `src.memvault.*` are not
# yet present in this repo. We try/except so syntax errors in env.py don't
# block alembic CLI introspection (`alembic current` / config validation).
target_metadata = None
try:
    from src.shared.models import Base  # type: ignore  # noqa: F401

    # Side-effect imports to register tables on Base.metadata.
    from src.memvault import kg_models  # type: ignore  # noqa: F401
    from src.memvault import models  # type: ignore  # noqa: F401
    from src.audit_stub import AuditLog  # type: ignore  # noqa: F401

    target_metadata = Base.metadata
except ImportError:
    # Pending Worker A — leave target_metadata = None.
    # Manual baseline migrations (op.create_table) still work.
    target_metadata = None


def _include_object(object, name, type_, reflected, compare_to):
    """Restrict autogen to memvault + audit schemas (defensive)."""
    if type_ == "schema":
        return name in {"memvault"}
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL to stdout."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=_include_object,
        version_table_schema="memvault",
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    from sqlalchemy import text

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=_include_object,
        version_table_schema="memvault",
    )
    # WHY all DDL inside begin_transaction(): SQLAlchemy 2.x sync `Connection`
    # auto-begins a transaction on the first execute() call. If we run
    # `CREATE SCHEMA` BEFORE `begin_transaction()`, that auto-begun tx (T1)
    # is already open, so alembic's begin_transaction becomes a no-op (it
    # sees the connection is already in-transaction). When the async
    # `async with connectable.connect()` releases the connection,
    # SQLAlchemy rolls back any pending transaction → all migration DDL
    # is silently rolled back. The fix: do everything inside the
    # `begin_transaction()` block which alembic owns and commits on exit.
    with context.begin_transaction():
        # Ensure schema exists before running migrations (CI / fresh installs
        # skip infra/postgres/init.sql which would otherwise pre-create it).
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS memvault"))
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an async engine."""
    section = config.get_section(config.config_ini_section, {}) or {}
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
