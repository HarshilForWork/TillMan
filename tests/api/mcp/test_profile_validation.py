"""The public UCP door validates the Platform's profile before any tool runs (#37).

UCP: a business MUST fetch and validate the profile a Platform names in `meta.ucp-agent.profile`.
A profile that can't be used is a protocol failure, answered as JSON-RPC `-32001` with `error.data.code`
saying which: `invalid_profile_url`, `profile_unreachable` or `profile_malformed`.
"""

import time
from typing import Any

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError

from tests.support.app import HOST, app, services
from tests.support.mcp import AGENT, MODES, call, serve
from tests.support.profiles import PLATFORM_PROFILE, PLATFORM_URL, FakeProfileFetcher
from tests.support.ucp_spec import schema_errors
from tillhand.core.errors import UCP_DISCOVERY_FAILED, ProfileError
from tillhand.integrations.profile_fetch import HttpProfileFetcher
from tillhand.main import Services
from tillhand.models.ucp import PlatformProfile, ProfileErrorData

pytestmark = pytest.mark.anyio

OTHER_URL = "https://other-platform.example/ucp/profile.json"
OTHER_PROFILE = PlatformProfile.model_validate(PLATFORM_PROFILE)
SEARCH = {"query": "serum"}


@pytest.fixture(params=MODES)
def mode(request: pytest.FixtureRequest) -> str:
    return request.param


def connect(mode: str, served: Services) -> Any:
    return serve(app(served), host=HOST, mode=mode)


async def discovery_failure(client: Client, meta: Any) -> ProfileErrorData:
    with pytest.raises(MCPError) as refused:
        await client.call_tool("search_catalog", {"meta": meta, "catalog": SEARCH})
    error = refused.value.error
    assert error.code == UCP_DISCOVERY_FAILED
    jsonrpc = {"jsonrpc": "2.0", "id": 1, "error": error.model_dump(mode="json", exclude_none=True)}
    assert schema_errors(jsonrpc, "transports/jsonrpc", "error_response") == []
    return ProfileErrorData.model_validate(error.data)


async def test_a_pre_approved_profile_is_served_without_a_fetch(mode: str) -> None:
    fetcher = FakeProfileFetcher()
    async with connect(mode, services(fetcher=fetcher)) as client:
        found = await call(client, "search_catalog", SEARCH)

    assert found["products"]
    assert fetcher.fetched == []


async def test_an_unknown_platform_is_fetched_and_then_served(mode: str) -> None:
    fetcher = FakeProfileFetcher({OTHER_URL: OTHER_PROFILE})
    async with connect(mode, services(fetcher=fetcher)) as client:
        result = await client.call_tool(
            "search_catalog", {"meta": {"ucp-agent": {"profile": OTHER_URL}}, "catalog": SEARCH}
        )

    assert not result.is_error
    assert fetcher.fetched == [OTHER_URL]


@pytest.mark.parametrize(
    "meta",
    [{}, {"ucp-agent": {}}, {"ucp-agent": {"profile": 42}}, {"ucp-agent": "https://x.example/p.json"}, None],
    ids=["no ucp-agent", "no profile", "profile not a string", "ucp-agent not an object", "no meta"],
)
async def test_a_missing_profile_url_is_invalid_profile_url(mode: str, meta: Any) -> None:
    fetcher = FakeProfileFetcher()
    async with connect(mode, services(fetcher=fetcher)) as client:
        data = await discovery_failure(client, meta)

    assert data.code == "invalid_profile_url"
    assert fetcher.fetched == []


@pytest.mark.parametrize("code", ["invalid_profile_url", "profile_unreachable", "profile_malformed"])
async def test_each_profile_failure_is_its_own_discovery_error(mode: str, code: Any) -> None:
    fetcher = FakeProfileFetcher(fail=ProfileError(code, "what went wrong"))
    async with connect(mode, services(fetcher=fetcher)) as client:
        data = await discovery_failure(client, {"ucp-agent": {"profile": OTHER_URL}})

    assert data.code == code
    assert data.content == "what went wrong"


async def test_the_profile_is_checked_before_the_arguments(mode: str) -> None:
    """A caller with no usable profile learns nothing about our argument rules."""
    async with connect(mode, services()) as client:
        with pytest.raises(MCPError) as refused:
            await client.call_tool("search_catalog", {"meta": {}, "catalog": {"pagination": "nonsense"}})

    assert refused.value.error.code == UCP_DISCOVERY_FAILED


async def test_a_profile_on_a_private_address_is_refused_at_the_door(mode: str) -> None:
    """The real fetcher behind the door: DNS says 10.0.0.7, so nothing is ever requested."""

    async def never(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(f"requested {request.url}")

    async def internal_dns(host: str, port: int) -> list[str]:
        return ["10.0.0.7"]

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(never)) as http:
        fetcher = HttpProfileFetcher(http, resolve=internal_dns)
        async with connect(mode, services(fetcher=fetcher)) as client:
            data = await discovery_failure(
                client, {"ucp-agent": {"profile": "https://intranet.example/p.json"}}
            )

    assert data.code == "invalid_profile_url"


async def test_a_slow_profile_host_is_a_named_timeout_not_a_hang(mode: str) -> None:
    async def slow(request: httpx2.Request) -> httpx2.Response:
        await anyio.sleep(30)
        raise AssertionError("the deadline should have ended this fetch")

    async def public_dns(host: str, port: int) -> list[str]:
        return ["93.184.215.14"]

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(slow)) as http:
        fetcher = HttpProfileFetcher(http, resolve=public_dns, seconds=0.2)
        async with connect(mode, services(fetcher=fetcher)) as client:
            started = time.monotonic()
            data = await discovery_failure(client, {"ucp-agent": {"profile": OTHER_URL}})
            elapsed = time.monotonic() - started

    assert data.code == "profile_unreachable"
    assert "profile.fetch" in data.content
    assert elapsed < 5


async def test_the_test_agent_sends_the_pre_approved_profile() -> None:
    assert AGENT == {"ucp-agent": {"profile": PLATFORM_URL}}
