"""The process's one asyncpg pool over Neon's pooled endpoint, created at startup and reused.

asyncpg's prepared-statement cache stays on: behind Neon's pooler it was probed with 120 concurrent
tasks over 4 connections (cached statements and explicit `prepare`) with no errors (#12).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TypeAlias

import asyncpg

from tillhand.core.config import Settings
from tillhand.core.deadlines import NEON_QUERY_SECONDS, deadline
from tillhand.core.errors import ServiceUnavailable

Pool: TypeAlias = "asyncpg.Pool[asyncpg.Record]"
"""Generic only in the type stubs; asyncpg's runtime class takes no parameters."""

Connection: TypeAlias = "asyncpg.pool.PoolConnectionProxy[asyncpg.Record]"

_UNREACHABLE = (
    OSError,
    asyncpg.InterfaceError,
    asyncpg.PostgresConnectionError,
    asyncpg.CannotConnectNowError,
)


@asynccontextmanager
async def connection(pool: Pool, step: str, seconds: float = NEON_QUERY_SECONDS) -> AsyncIterator[Connection]:
    """A pooled connection for one step: under its deadline (`StepTimeout`), and `ServiceUnavailable` if
    Neon can't be reached, so a caller is refused cleanly instead of crashing. Used by the cart SQL; the
    catalog SQL still spells out `deadline` + `acquire` and can move onto it."""
    try:
        async with deadline(step, seconds), pool.acquire() as conn:
            yield conn
    except _UNREACHABLE as exc:
        raise ServiceUnavailable(step) from exc


async def create_pool(settings: Settings, *, max_size: int = 10) -> Pool:
    return await asyncpg.create_pool(
        settings.database_url.get_secret_value(),
        min_size=1,
        max_size=max_size,
        command_timeout=NEON_QUERY_SECONDS,
    )
