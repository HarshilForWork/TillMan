"""The pre-approved Platform registry file (`data/platforms.json`): Platforms served without a fetch."""

from tillhand.models.ucp import Closed, PlatformProfile, Url


class PreApprovedPlatform(Closed):
    profile_url: Url
    note: str
    """Who this is and why it is trusted, for whoever reads the file next."""
    profile: PlatformProfile


class PlatformsFile(Closed):
    platforms: list[PreApprovedPlatform]
