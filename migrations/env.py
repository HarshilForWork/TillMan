"""Alembic environment: migrations run over asyncpg, like the app, against DATABASE_URL.

Migrations are hand-written SQL (`op.execute`); there are no SQLAlchemy models to autogenerate from.
SQLAlchemy is only Alembic's plumbing here, and the app never imports it.

Neon's connection string goes to asyncpg unchanged (through `async_creator`), because SQLAlchemy's
URL parsing would turn `sslmode` and `channel_binding` into keyword arguments asyncpg doesn't take.
The pooled endpoint is fine: each migration run is one transaction, which the pooler keeps on one
server connection (verified on Neon, #12).
"""

import asyncio
from logging.config import fileConfig

import asyncpg
from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from tillhand.core.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL without connecting."""
    context.configure(dialect_name="postgresql", literal_binds=True, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    dsn = get_settings().database_url.get_secret_value()
    engine = create_async_engine(
        "postgresql+asyncpg://",
        async_creator=lambda: asyncpg.connect(dsn),
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
