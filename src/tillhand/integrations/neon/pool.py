"""The process's one asyncpg pool over Neon's pooled endpoint, created at startup and reused.

asyncpg's prepared-statement cache stays on: behind Neon's pooler it was probed with 120 concurrent
tasks over 4 connections (cached statements and explicit `prepare`) with no errors (#12).
"""

from typing import TypeAlias

import asyncpg

from tillhand.core.config import Settings
from tillhand.core.deadlines import NEON_QUERY_SECONDS

Pool: TypeAlias = "asyncpg.Pool[asyncpg.Record]"
"""Generic only in the type stubs; asyncpg's runtime class takes no parameters."""


async def create_pool(settings: Settings, *, max_size: int = 10) -> Pool:
    return await asyncpg.create_pool(
        settings.database_url.get_secret_value(),
        min_size=1,
        max_size=max_size,
        command_timeout=NEON_QUERY_SECONDS,
    )
