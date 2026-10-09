"""The Merchant door over real HTTP (#41): `/merchant/mcp`, where every request carries `TillHand-Api-Key`.

The key rules are tested on the service (tests/services/test_merchant_door.py). This checks the door:
- the key's profile reaches the tools in `meta`, whatever the caller wrote there;
- a missing, wrong or revoked key gets the identical HTTP 401, as UCP's JSON-RPC "Unauthorized";
- `TillHand-Customer` makes the Customer an Owner, and survives key rotation;
- a Merchant assistant's profile is refused on the public door (owner's decision).
That one Customer can't reach another's Cart is the isolation fixture's job (test_isolation.py).
"""

import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx2
import pytest
from fastapi import FastAPI
from mcp.shared.exceptions import MCPError

from tests.support.app import HOST, app, services
from tests.support.mcp import AGENT, MERCHANT_PATH, MODES, connect, running
from tests.support.merchant import (
    ASSISTANT_URL,
    KEY_A,
    KEY_A_ROTATED,
    FakeMerchantStore,
    key_id,
    merchant_headers,
    merchant_store,
)
from tests.support.profiles import PLATFORM_URL, FakeProfileFetcher
from tests.support.ucp_spec import schema_errors
from tillhand.api.mcp import TOOL_ACCESS, Tool, UcpTool
from tillhand.core.constants import UCP_VERSION
from tillhand.main import Services, service_tools
from tillhand.models.ucp import Closed, Open, RequestMeta, UcpResponseMeta
from tillhand.services.merchant_door import MerchantDoor, issue_key

pytestmark = pytest.mark.anyio

UNAUTHORIZED = {"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": "Unauthorized"}}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
LINE = {"item": {"id": "var_niacinamide_serum_30"}, "quantity": 2}


@pytest.fixture(params=MODES)
def mode(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def store() -> FakeMerchantStore:
    return merchant_store()


class EchoArguments(Open):
    meta: RequestMeta


class Echo(Closed):
    ucp: UcpResponseMeta
    profile: str


async def _echo(arguments: EchoArguments) -> Echo:
    return Echo(ucp=UcpResponseMeta(version=UCP_VERSION), profile=arguments.meta.ucp_agent.profile)


def _with_echo(served: Callable[[], Services]) -> list[Tool]:
    return [*service_tools(served), UcpTool("echo_meta", "Echo meta.", EchoArguments, _echo)]


_ECHO_ACCESS = {**TOOL_ACCESS, "echo_meta": "public"}


def door_app(store: FakeMerchantStore) -> FastAPI:
    """The test app, plus `echo_meta`: a public tool that answers with the profile its `meta` named."""
    return app(services(merchant=MerchantDoor(store)), tools=_with_echo, access=_ECHO_ACCESS)


@asynccontextmanager
async def raw(served: FastAPI) -> AsyncIterator[httpx2.AsyncClient]:
    """Plain HTTP, to see the exact status and bytes an MCP client would only turn into an exception."""
    async with (
        running(served),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=served), base_url=f"http://{HOST}") as http,
    ):
        yield http


async def post(
    http: httpx2.AsyncClient, headers: dict[str, str], path: str = MERCHANT_PATH
) -> httpx2.Response:
    accept = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    return await http.post(path, content=json.dumps(LIST_TOOLS), headers={**accept, **headers})


async def test_a_valid_key_reaches_the_tools_with_its_profile_in_meta(
    store: FakeMerchantStore, mode: str
) -> None:
    served = door_app(store)
    async with (
        running(served),
        connect(served, host=HOST, mode=mode, path=MERCHANT_PATH, headers=merchant_headers(KEY_A)) as client,
    ):
        result = await client.call_tool("echo_meta", {"meta": AGENT})  # names another profile: overwritten
        bare = await client.call_tool("echo_meta", {})  # no meta at all: the door fills it

    assert isinstance(result.structured_content, dict) and isinstance(bare.structured_content, dict)
    assert result.structured_content["profile"] == ASSISTANT_URL
    assert bare.structured_content["profile"] == ASSISTANT_URL


async def test_a_missing_wrong_or_revoked_key_gets_the_same_401(store: FakeMerchantStore) -> None:
    await store.revoke_key(key_id(store, KEY_A_ROTATED))
    async with raw(door_app(store)) as http:
        answers = [
            await post(http, merchant_headers(None)),
            await post(http, merchant_headers("thk_not-a-real-key")),
            await post(http, merchant_headers(KEY_A_ROTATED)),
        ]

    assert {r.status_code for r in answers} == {401}
    assert len({r.content for r in answers}) == 1  # byte-identical
    assert answers[0].json() == UNAUTHORIZED
    assert schema_errors(UNAUTHORIZED, "transports/jsonrpc", "error_response") == []


async def test_a_revoked_key_fails_on_its_very_next_request(store: FakeMerchantStore) -> None:
    async with raw(door_app(store)) as http:
        before = await post(http, merchant_headers(KEY_A))
        await store.revoke_key(key_id(store, KEY_A))
        after = await post(http, merchant_headers(KEY_A))

    assert before.status_code == 200
    assert after.status_code == 401


async def test_two_keys_work_side_by_side_and_a_rotation_keeps_the_customers_cart(
    store: FakeMerchantStore, mode: str
) -> None:
    served = door_app(store)
    async with running(served):
        async with connect(
            served, host=HOST, mode=mode, path=MERCHANT_PATH, headers=merchant_headers(KEY_A, "shop_123")
        ) as old:
            created = await old.call_tool("create_cart", {"cart": {"line_items": [LINE]}})
        async with connect(
            served,
            host=HOST,
            mode=mode,
            path=MERCHANT_PATH,
            headers=merchant_headers(KEY_A_ROTATED, "shop_123"),
        ) as new:
            assert isinstance(created.structured_content, dict)
            found = await new.call_tool("get_cart", {"id": created.structured_content["id"]})

    assert isinstance(found.structured_content, dict)
    assert found.structured_content["id"] == created.structured_content["id"]
    assert len(store.customers) == 1  # the same TillHand-Customer, the same Customer


async def test_a_merchant_assistants_profile_is_refused_on_the_public_door(mode: str) -> None:
    served = door_app(merchant_store())
    async with running(served), connect(served, host=HOST, mode=mode) as client:
        with pytest.raises(MCPError) as raised:
            await client.call_tool("echo_meta", {"meta": {"ucp-agent": {"profile": ASSISTANT_URL}}})

    assert raised.value.error.code == -32000
    assert raised.value.error.data == {"code": "merchant_key_required"}


async def test_merchant_headers_on_the_public_door_are_refused(store: FakeMerchantStore) -> None:
    """`TillHand-Customer` is accepted only with a valid key, on the Merchant door."""
    async with raw(door_app(store)) as http:
        answer = await post(http, merchant_headers(KEY_A, "shop_123"), path="/mcp")

    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == -32600


@pytest.mark.parametrize(
    "headers",
    [
        {**merchant_headers(KEY_A, "shop_123"), "Authorization": "Bearer a-customer-token"},
        merchant_headers(KEY_A, "x" * 300),
    ],
    ids=["with a Customer token", "too long"],
)
async def test_a_bad_customer_header_is_a_400(store: FakeMerchantStore, headers: dict[str, str]) -> None:
    async with raw(door_app(store)) as http:
        answer = await post(http, headers)

    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == -32600
    assert store.customers == {}


async def test_every_merchant_door_request_logs_its_key(
    store: FakeMerchantStore, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="tillhand.api.mcp.guards")
    async with raw(door_app(store)) as http:
        await post(http, merchant_headers(KEY_A))

    assert str(key_id(store, KEY_A)) in caplog.text
    assert KEY_A not in caplog.text  # never the key itself


class FlippableBinding:
    """A key-binding check the test can change while the app runs, as issuing a first key does in Neon."""

    def __init__(self) -> None:
        self.bound: set[str] = set()

    async def requires_key(self, url: str) -> bool:
        return url in self.bound


async def test_a_profile_bound_while_running_is_refused_on_the_public_door_at_once(mode: str) -> None:
    """Owner's decision: key-binding is checked afresh on every public call, so there's no restart gap."""
    binding = FlippableBinding()
    served = app(
        services(key_binding=binding), tools=_with_echo, access={**TOOL_ACCESS, "echo_meta": "public"}
    )
    meta = {"ucp-agent": {"profile": PLATFORM_URL}}
    async with running(served), connect(served, host=HOST, mode=mode) as client:
        before = await client.call_tool("echo_meta", {"meta": meta})
        binding.bound.add(PLATFORM_URL)  # its first Merchant API key was just issued
        with pytest.raises(MCPError) as raised:
            await client.call_tool("echo_meta", {"meta": meta})

    assert isinstance(before.structured_content, dict)
    assert raised.value.error.data == {"code": "merchant_key_required"}


async def test_a_key_whose_profile_isnt_loaded_is_refused_without_a_fetch(mode: str) -> None:
    """A key issued for a Platform seeded after startup: its profile is never fetched, the call is refused."""
    store = merchant_store()
    unloaded = "https://later.example/profiles/assistant.json"
    store.platforms.add(unloaded)
    key, _ = await issue_key(store, label="seeded later", profile_url=unloaded)
    fetcher = FakeProfileFetcher()
    served = app(
        services(merchant=MerchantDoor(store), fetcher=fetcher), tools=_with_echo, access=_ECHO_ACCESS
    )
    async with (
        running(served),
        connect(served, host=HOST, mode=mode, path=MERCHANT_PATH, headers=merchant_headers(key)) as client,
    ):
        with pytest.raises(MCPError) as raised:
            await client.call_tool("echo_meta", {})

    assert raised.value.error.code == -32000
    assert raised.value.error.data["code"] == "profile_not_loaded"
    assert fetcher.fetched == []
