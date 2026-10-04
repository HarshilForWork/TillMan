"""Fetching a Platform's profile: the one outbound request a caller's input decides, so an SSRF surface.

The HTTP side is `httpx2.MockTransport` and DNS is a dictionary, so nothing here leaves the process.
"""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import anyio
import httpx2
import pytest

from tests.support.profiles import PLATFORM_PROFILE
from tillhand.core.deadlines import StepTimeout
from tillhand.core.errors import ProfileError
from tillhand.integrations.profile_fetch import MAX_PROFILE_BYTES, HttpProfileFetcher

pytestmark = pytest.mark.anyio

URL = "https://platform.example/profiles/agent.json"
PUBLIC_IP = "93.184.215.14"
JSON_HEADERS = {"content-type": "application/json", "cache-control": "public, max-age=300"}

Handler = Callable[[httpx2.Request], Awaitable[httpx2.Response]]


def dns(table: dict[str, list[str]]) -> Callable[[str, int], Awaitable[list[str]]]:
    async def resolve(host: str, port: int) -> list[str]:
        if host not in table:
            raise OSError(f"no such host: {host}")
        return table[host]

    return resolve


class Recorder:
    def __init__(self, respond: Handler) -> None:
        self.respond = respond
        self.requests: list[httpx2.Request] = []

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return await self.respond(request)


async def serves_profile(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, headers=JSON_HEADERS, json=PLATFORM_PROFILE)


async def fetch(
    url: str = URL,
    respond: Handler = serves_profile,
    table: dict[str, list[str]] | None = None,
    seconds: float = 2.0,
) -> tuple[Any, Recorder]:
    recorder = Recorder(respond)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(recorder)) as client:
        fetcher = HttpProfileFetcher(
            client,
            resolve=dns({"platform.example": [PUBLIC_IP]} if table is None else table),
            seconds=seconds,
        )
        return await fetcher.fetch(url), recorder


async def refused(
    url: str = URL, respond: Handler = serves_profile, **kwargs: Any
) -> tuple[ProfileError, Recorder]:
    recorder = Recorder(respond)
    table = kwargs.pop("table", {"platform.example": [PUBLIC_IP]})
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(recorder)) as client:
        fetcher = HttpProfileFetcher(client, resolve=dns(table), **kwargs)
        with pytest.raises(ProfileError) as caught:
            await fetcher.fetch(url)
    return caught.value, recorder


async def test_a_valid_profile_is_fetched_from_the_address_that_was_checked() -> None:
    profile, recorder = await fetch()

    assert profile.ucp.version == PLATFORM_PROFILE["ucp"]["version"]
    (request,) = recorder.requests
    # Pinned: the connection goes to the IP that passed the check, so DNS can't change its answer
    # in between (rebinding). TLS still verifies the certificate for the real host name.
    assert request.url.host == PUBLIC_IP
    assert request.headers["host"] == "platform.example"
    assert request.extensions["sni_hostname"] == "platform.example"


@pytest.mark.parametrize(
    "url",
    [
        "http://platform.example/profiles/agent.json",
        "ftp://platform.example/agent.json",
        "platform.example/agent.json",
        "https:///agent.json",
        "https://user:secret@platform.example/agent.json",
        "https://platform.example:99999/agent.json",
        "",
    ],
)
async def test_a_url_that_is_not_plain_https_is_an_invalid_profile_url(url: str) -> None:
    error, recorder = await refused(url)

    assert error.code == "invalid_profile_url"
    assert recorder.requests == []


@pytest.mark.parametrize(
    ("host", "addresses"),
    [
        ("private.example", ["10.0.0.5"]),
        ("loopback.example", ["127.0.0.1"]),
        ("metadata.example", ["169.254.169.254"]),
        ("shared.example", ["100.64.0.1"]),
        ("v6-loopback.example", ["::1"]),
        ("mapped.example", ["::ffff:127.0.0.1"]),
        ("v6-private.example", ["fd00::1"]),
        ("multicast.example", ["224.0.0.1"]),
        ("one-bad-apple.example", [PUBLIC_IP, "192.168.1.10"]),
    ],
)
async def test_a_host_resolving_to_a_special_use_address_is_refused(host: str, addresses: list[str]) -> None:
    error, recorder = await refused(f"https://{host}/agent.json", table={host: addresses})

    assert error.code == "invalid_profile_url"
    assert recorder.requests == []


@pytest.mark.parametrize("literal", ["127.0.0.1", "169.254.169.254", "10.1.2.3", "[::1]"])
async def test_an_ip_literal_in_a_special_use_range_is_refused(literal: str) -> None:
    error, recorder = await refused(f"https://{literal}/agent.json", table={})

    assert error.code == "invalid_profile_url"
    assert recorder.requests == []


async def test_a_host_that_does_not_resolve_is_an_invalid_profile_url() -> None:
    error, recorder = await refused("https://nowhere.example/agent.json")

    assert error.code == "invalid_profile_url"
    assert recorder.requests == []


async def test_a_redirect_is_never_followed_even_to_a_private_address() -> None:
    async def redirects(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    error, recorder = await refused(respond=redirects)

    assert error.code == "profile_unreachable"
    assert len(recorder.requests) == 1


@pytest.mark.parametrize("status", [404, 500, 503])
async def test_a_non_2xx_answer_is_profile_unreachable(status: int) -> None:
    async def fails(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status)

    error, _ = await refused(respond=fails)

    assert error.code == "profile_unreachable"


async def test_a_connection_failure_is_profile_unreachable() -> None:
    async def cannot_connect(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    error, _ = await refused(respond=cannot_connect)

    assert error.code == "profile_unreachable"


async def test_an_oversized_body_is_refused_without_reading_it_all() -> None:
    chunk = b" " * 65_536
    sent: list[int] = []

    async def endless() -> AsyncIterator[bytes]:
        for _ in range(1_000):  # 64 MB if read to the end
            sent.append(len(chunk))
            yield chunk

    async def huge(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"content-type": "application/json"}, content=endless())

    error, _ = await refused(respond=huge)

    assert error.code == "profile_malformed"
    assert sum(sent) <= MAX_PROFILE_BYTES + 2 * len(chunk)


async def test_a_declared_length_over_the_cap_is_refused_before_reading() -> None:
    async def declares_huge(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, headers={"content-type": "application/json", "content-length": str(10 * MAX_PROFILE_BYTES)}
        )

    error, _ = await refused(respond=declares_huge)

    assert error.code == "profile_malformed"


@pytest.mark.parametrize("content_type", ["text/html", "text/plain", ""])
async def test_a_non_json_content_type_is_profile_malformed(content_type: str) -> None:
    async def html(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, headers={"content-type": content_type}, content=json.dumps(PLATFORM_PROFILE)
        )

    error, _ = await refused(respond=html)

    assert error.code == "profile_malformed"


async def test_a_json_suffix_content_type_is_accepted() -> None:
    async def problem_json(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "application/ucp+json; charset=utf-8"},
            content=json.dumps(PLATFORM_PROFILE),
        )

    profile, _ = await fetch(respond=problem_json)

    assert profile.ucp.version == PLATFORM_PROFILE["ucp"]["version"]


@pytest.mark.parametrize("body", [b"not json", b'{"ucp": {"version": "2026-08-25"}}', b"[]"])
async def test_a_body_that_is_not_a_platform_profile_is_profile_malformed(body: bytes) -> None:
    async def serves(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"content-type": "application/json"}, content=body)

    error, _ = await refused(respond=serves)

    assert error.code == "profile_malformed"


async def test_a_slow_profile_host_is_a_named_timeout_not_a_hang() -> None:
    async def slow(request: httpx2.Request) -> httpx2.Response:
        await anyio.sleep(30)
        raise AssertionError("the deadline should have ended this fetch")

    recorder = Recorder(slow)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(recorder)) as client:
        fetcher = HttpProfileFetcher(client, resolve=dns({"platform.example": [PUBLIC_IP]}), seconds=0.2)
        with pytest.raises(StepTimeout) as caught:
            await fetcher.fetch(URL)

    assert caught.value.step == "profile.fetch"


async def test_a_public_ipv6_host_is_fetched_on_a_non_default_port() -> None:
    public_v6 = "2606:2800:220:1:248:1893:25c8:1946"
    _, recorder = await fetch(
        "https://platform.example:8443/profiles/agent.json", table={"platform.example": [public_v6]}
    )

    (request,) = recorder.requests
    assert request.url.host == public_v6
    assert request.url.port == 8443
    assert request.headers["host"] == "platform.example:8443"
