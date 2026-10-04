"""Fetching a Platform's UCP profile from the URL it sends (`meta.ucp-agent.profile`).

This is the one outbound request whose target a caller chooses, so it is an SSRF surface: without
care, "fetch my profile from http://169.254.169.254/..." would make our server read its own cloud
metadata, or probe the private network it sits in. The rules, from UCP's "Fetching" section:
- `https` only, with no credentials in the URL;
- every address the host resolves to must be public: no private, loopback, link-local (including the
  metadata address), shared, multicast or reserved ones;
- the connection goes to the address that passed the check, so a second DNS answer can't swap it for
  a private one (DNS rebinding), while TLS still verifies the certificate for the real host name;
- no redirects are followed (UCP: profile endpoints MUST NOT redirect);
- the body is capped at `MAX_PROFILE_BYTES` and must be JSON;
- DNS, connect and read all run under one `profile.fetch` deadline.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx2
from pydantic import ValidationError

from tillhand.core.deadlines import PROFILE_FETCH_SECONDS, StepTimeout, deadline
from tillhand.core.errors import ProfileError
from tillhand.models.ucp import PlatformProfile

STEP = "profile.fetch"

MAX_PROFILE_BYTES = 128 * 1024
"""UCP: verifiers SHOULD bound the body, and no lower than 128 KiB (documented profiles are under 5 KiB)."""

Resolver = Callable[[str, int], Awaitable[list[str]]]
"""Host and port to the IP addresses it resolves to. Raises `OSError` when it doesn't resolve."""


async def resolve_host(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def is_public_address(address: str) -> bool:
    """A globally routable unicast address. IPv6 forms that embed an IPv4 address are judged by it too."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if embedded is not None and not is_public_address(str(embedded)):
            return False
    return ip.is_global and not ip.is_multicast


def _invalid(content: str) -> ProfileError:
    return ProfileError("invalid_profile_url", content)


class HttpProfileFetcher:
    """Fetches and validates profiles through the process's shared HTTP client."""

    def __init__(
        self,
        client: httpx2.AsyncClient,
        *,
        resolve: Resolver = resolve_host,
        seconds: float = PROFILE_FETCH_SECONDS,
        max_bytes: int = MAX_PROFILE_BYTES,
    ) -> None:
        self._client = client
        self._resolve = resolve
        self._seconds = seconds
        self._max_bytes = max_bytes

    async def fetch(self, url: str) -> PlatformProfile:
        """The validated profile, or `ProfileError`. Raises `StepTimeout("profile.fetch")` when slow."""
        host, port = _target(url)
        async with deadline(STEP, self._seconds):
            address = await self._public_address(host, port)
            body = await self._get(url, host=host, port=port, address=address)
        try:
            return PlatformProfile.model_validate_json(body)
        except ValidationError as exc:
            where = ", ".join(sorted({".".join(map(str, e["loc"])) or "<root>" for e in exc.errors()})[:3])
            raise ProfileError("profile_malformed", f"not a valid UCP Platform profile (at {where})") from exc

    async def _public_address(self, host: str, port: int) -> str:
        try:
            ipaddress.ip_address(host)
            addresses = [host]
        except ValueError:
            try:
                addresses = await self._resolve(host, port)
            except OSError as exc:
                raise _invalid(f"the profile host {host} does not resolve") from exc
        if not addresses:
            raise _invalid(f"the profile host {host} does not resolve")
        # Every address must pass: a client may connect to any of them.
        if not all(is_public_address(a) for a in addresses):
            raise _invalid("the profile URL resolves to a special-use address")
        return addresses[0]

    async def _get(self, url: str, *, host: str, port: int, address: str) -> bytes:
        pinned = httpx2.URL(url).copy_with(host=address)
        try:
            async with self._client.stream(
                "GET",
                pinned,
                headers={"host": host if port == 443 else f"{host}:{port}", "accept": "application/json"},
                extensions={"sni_hostname": host},
                follow_redirects=False,
            ) as response:
                if not response.is_success:
                    extra = " (redirects are not followed)" if response.is_redirect else ""
                    raise ProfileError(
                        "profile_unreachable", f"the profile URL answered HTTP {response.status_code}{extra}"
                    )
                media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if media_type != "application/json" and not media_type.endswith("+json"):
                    raise ProfileError(
                        "profile_malformed", f"the profile is served as {media_type!r}, not JSON"
                    )
                declared = response.headers.get("content-length")
                if declared is not None and declared.isdigit() and int(declared) > self._max_bytes:
                    raise self._too_large()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > self._max_bytes:
                        raise self._too_large()
                return bytes(body)
        except httpx2.TimeoutException as exc:
            # The client's own timeout can fire before our deadline; either way the step overran.
            raise StepTimeout(STEP) from exc
        except httpx2.HTTPError as exc:
            raise ProfileError(
                "profile_unreachable", f"could not fetch the profile: {type(exc).__name__}"
            ) from exc

    def _too_large(self) -> ProfileError:
        return ProfileError("profile_malformed", f"the profile is larger than {self._max_bytes} bytes")


def _target(url: str) -> tuple[str, int]:
    """The host and port of a plain `https` URL, or `invalid_profile_url`."""
    try:
        parts = urlsplit(url)
        port = parts.port or 443
    except ValueError as exc:
        raise _invalid("the profile URL is malformed") from exc
    if parts.scheme != "https":
        raise _invalid("the profile URL must use https")
    if not parts.hostname:
        raise _invalid("the profile URL has no host")
    if parts.username is not None or parts.password is not None:
        raise _invalid("the profile URL must not carry credentials")
    return parts.hostname, port
