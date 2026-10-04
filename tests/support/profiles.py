"""Platform profiles for tests: a valid one for `platform.example`, and a registry that pre-approves it.

`tests.support.mcp.AGENT` sends `PLATFORM_URL`; the test app pre-approves it, so no test fetches it.
"""

from typing import Any

import anyio

from tillhand.core.constants import UCP_VERSION
from tillhand.core.errors import ProfileError
from tillhand.models.ucp import PlatformProfile

PLATFORM_URL = "https://platform.example/profiles/agent.json"

PLATFORM_PROFILE: dict[str, Any] = {
    "ucp": {
        "version": UCP_VERSION,
        "services": {
            "dev.ucp.shopping": [
                {
                    "version": UCP_VERSION,
                    "spec": f"https://ucp.dev/{UCP_VERSION}/specification/overview",
                    "transport": "mcp",
                    "schema": f"https://ucp.dev/{UCP_VERSION}/services/shopping/mcp.openrpc.json",
                }
            ]
        },
        "capabilities": {
            "dev.ucp.shopping.catalog.search": [
                {
                    "version": UCP_VERSION,
                    "spec": f"https://ucp.dev/{UCP_VERSION}/specification/shopping/catalog/search",
                    "schema": f"https://ucp.dev/{UCP_VERSION}/schemas/shopping/catalog_search.json",
                }
            ]
        },
        "payment_handlers": {},
    }
}

PRE_APPROVED = {PLATFORM_URL: PlatformProfile.model_validate(PLATFORM_PROFILE)}


class FakeProfileFetcher:
    """Serves `profiles` by URL; anything else, or `fail`, raises. `delay` makes every fetch slow."""

    def __init__(
        self,
        profiles: dict[str, PlatformProfile] | None = None,
        *,
        fail: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.profiles = profiles or {}
        self.fail = fail
        self.delay = delay
        self.fetched: list[str] = []

    async def fetch(self, url: str) -> PlatformProfile:
        self.fetched.append(url)
        if self.delay:
            await anyio.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        if url not in self.profiles:
            raise ProfileError("profile_unreachable", "the profile URL answered HTTP 404")
        return self.profiles[url]
