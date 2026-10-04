"""Which Platform is calling: resolving the profile URL every request names (`meta.ucp-agent.profile`).

UCP says the business MUST fetch and validate that profile, and SHOULD keep a registry of
pre-approved Platforms that are served without fetching (overview, "Fetching"). So resolution is:
1. **Pre-approved?** Use the registered profile. Nothing leaves the process.
2. **Otherwise,** fetch it and validate it (`integrations.profile_fetch`).

There is no cache yet: an unknown Platform's profile is fetched on every request. That is conformant
("fetch unless cached"), and caching it waits for measurements (#38).

The registry is also the seam for the Merchant door (#41). That door authenticates the Merchant
assistant with a Merchant API key, fills `meta.ucp-agent.profile` with the key's pre-registered profile
URL, and that URL is found here, so its requests never fetch. Today the registry is a repo file
(`data/platforms.json`); #11 and #41 move it to Neon behind the same `PreApprovedProfiles` interface.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

import anyio

from tillhand.core.deadlines import StepTimeout
from tillhand.core.errors import ProfileError
from tillhand.models.domain import PlatformsFile
from tillhand.models.ucp import PlatformProfile


class ProfileFetcher(Protocol):
    async def fetch(self, url: str) -> PlatformProfile: ...


class PreApprovedProfiles(Protocol):
    async def get(self, url: str) -> PlatformProfile | None: ...


class StaticPreApproved:
    """A registry fixed at startup."""

    def __init__(self, profiles: Mapping[str, PlatformProfile]) -> None:
        self._profiles = dict(profiles)

    async def get(self, url: str) -> PlatformProfile | None:
        return self._profiles.get(url)


class ProfileResolver:
    def __init__(self, pre_approved: PreApprovedProfiles, fetcher: ProfileFetcher) -> None:
        self._pre_approved = pre_approved
        self._fetcher = fetcher

    async def resolve(self, url: str) -> PlatformProfile:
        """The Platform's validated profile, or `ProfileError` saying why it can't be used."""
        if (known := await self._pre_approved.get(url)) is not None:
            return known
        try:
            return await self._fetcher.fetch(url)
        except StepTimeout as exc:
            raise ProfileError("profile_unreachable", f"Timed out at {exc.step}. Try again.") from exc


async def load_pre_approved(path: Path) -> StaticPreApproved:
    """The registry file, validated in full: a bad entry stops the app starting rather than being skipped."""
    parsed = PlatformsFile.model_validate_json(await anyio.Path(path).read_bytes())
    urls = [p.profile_url for p in parsed.platforms]
    if len(set(urls)) != len(urls):
        raise ValueError(f"{path} lists a profile URL more than once")
    return StaticPreApproved({p.profile_url: p.profile for p in parsed.platforms})
