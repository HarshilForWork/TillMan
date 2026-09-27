import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Async tests run on asyncio only: the app does, and asyncpg has no trio support."""
    return "asyncio"
