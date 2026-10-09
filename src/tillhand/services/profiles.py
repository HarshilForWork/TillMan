"""Which Platform is calling: resolving the profile URL every request names (`meta.ucp-agent.profile`).

UCP says the business MUST fetch and validate that profile, and SHOULD keep a registry of
pre-approved Platforms that are served without fetching (overview, "Fetching"). So resolution is:
1. **Pre-approved?** Use the registered profile. Nothing leaves the process.
2. **Otherwise,** fetch it and validate it (`integrations.profile_fetch`).

There is no cache yet: an unknown Platform's profile is fetched on every request. That is conformant
("fetch unless cached"), and caching it waits for measurements (#38).

The registry is also the seam for the Merchant door (#41). That door authenticates the Merchant
assistant with a Merchant API key, fills `meta.ucp-agent.profile` with the key's pre-registered profile
URL, and that URL is found here, so its requests never fetch. The registry lives in Neon's `platforms`
table (#41), loaded once at startup; `data/platforms.json` is only the seed it's loaded from
(`scripts/seed_platforms.py`), validated by `load_pre_approved` before it's written.
"""

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Protocol

import anyio

from tillhand.core.deadlines import StepTimeout
from tillhand.core.errors import ProfileError
from tillhand.models.db import RegisteredPlatform
from tillhand.models.domain import PlatformsFile, PreApprovedPlatform
from tillhand.models.ucp import PlatformProfile


class ProfileFetcher(Protocol):
    async def fetch(self, url: str) -> PlatformProfile: ...


class PreApprovedProfiles(Protocol):
    async def get(self, url: str) -> PlatformProfile | None: ...


class KeyBinding(Protocol):
    async def requires_key(self, url: str) -> bool:
        """A Merchant assistant's profile, bound to a Merchant API key: usable only on the Merchant door,
        with its key (#41). Asked afresh on every public-door call (owner's decision), so a profile whose
        first key was just issued is refused at once, with no restart."""
        ...


class StaticPreApproved:
    """A registry fixed at startup. In tests it also answers `KeyBinding` from `key_bound`."""

    def __init__(self, profiles: Mapping[str, PlatformProfile], *, key_bound: Iterable[str] = ()) -> None:
        self._profiles = dict(profiles)
        self._key_bound = frozenset(key_bound)

    async def get(self, url: str) -> PlatformProfile | None:
        return self._profiles.get(url)

    async def requires_key(self, url: str) -> bool:
        return url in self._key_bound


class ProfileResolver:
    def __init__(
        self, pre_approved: PreApprovedProfiles, fetcher: ProfileFetcher, *, key_binding: KeyBinding
    ) -> None:
        self._pre_approved = pre_approved
        self._fetcher = fetcher
        self._key_binding = key_binding

    async def requires_key(self, url: str) -> bool:
        return await self._key_binding.requires_key(url)

    async def is_pre_approved(self, url: str) -> bool:
        return await self._pre_approved.get(url) is not None

    async def resolve(self, url: str) -> PlatformProfile:
        """The Platform's validated profile, or `ProfileError` saying why it can't be used."""
        if (known := await self._pre_approved.get(url)) is not None:
            return known
        try:
            return await self._fetcher.fetch(url)
        except StepTimeout as exc:
            raise ProfileError("profile_unreachable", f"Timed out at {exc.step}. Try again.") from exc


def registry_from(platforms: Iterable[RegisteredPlatform]) -> StaticPreApproved:
    """The registry the app serves, from the `platforms` table (#41): loaded once at startup."""
    rows = list(platforms)
    return StaticPreApproved(
        {p.profile_url: p.profile for p in rows}, key_bound=[p.profile_url for p in rows if p.key_bound]
    )


def registry_entries(raw: bytes) -> list[tuple[PreApprovedPlatform, str]]:
    """A registry file's entries, validated in full, each with its profile document as JSON text: the same
    content as the file (keys, values and their order), only re-spaced (owner's decision). A bad entry or a
    repeated URL raises rather than being skipped."""
    document = json.loads(raw)
    parsed = PlatformsFile.model_validate(document)
    urls = [p.profile_url for p in parsed.platforms]
    if len(set(urls)) != len(urls):
        raise ValueError("the registry lists a profile URL more than once")
    texts = [json.dumps(entry["profile"], ensure_ascii=False) for entry in document["platforms"]]
    return list(zip(parsed.platforms, texts, strict=True))


async def load_pre_approved(path: Path) -> StaticPreApproved:
    """A registry file as a registry, validated in full (the seed script's check, and the tests')."""
    entries = registry_entries(await anyio.Path(path).read_bytes())
    return StaticPreApproved({p.profile_url: p.profile for p, _ in entries})
