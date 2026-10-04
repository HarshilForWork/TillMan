"""Per-step deadlines: every slow step gets its own time budget and, when it overruns, a name.

A step that runs out of time raises `StepTimeout(step)`. The service catches it and returns
`timeout_error(step)`, so the caller learns which dependency was slow instead of waiting on a hang.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

NEON_QUERY_SECONDS = 5.0
"""A catalog query is a few round trips of ~85 ms from India, plus a possible Neon cold start (~1 s)."""

NEON_SYNC_SECONDS = 30.0
"""A whole catalog sync (seed script only): a handful of batched statements in one transaction."""

EMBED_QUERY_SECONDS = 3.0
EMBED_PASSAGES_SECONDS = 30.0
"""Only the seed script embeds passages, in batches of up to 96, so it gets far longer than a search."""

PROFILE_FETCH_SECONDS = 3.0
"""A Platform's profile: DNS, TLS and a body of at most 128 KiB. A profile host slower than this is
`profile_unreachable`, named as `profile.fetch`. It runs before, not inside, the tool's own deadline."""


TOOL_SECONDS = 10.0
"""A whole tool call. Covers a search's embedding (3 s) and query (5 s) budgets, plus some slack."""


class StepTimeout(Exception):
    def __init__(self, step: str) -> None:
        super().__init__(f"{step} ran out of time")
        self.step = step


@asynccontextmanager
async def deadline(step: str, seconds: float) -> AsyncIterator[None]:
    try:
        async with asyncio.timeout(seconds):
            yield
    except TimeoutError as exc:
        raise StepTimeout(step) from exc
