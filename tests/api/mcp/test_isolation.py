"""The cross-owner isolation fixture (`tests/support/isolation.py`), over the real MCP transport (#42).

Two halves:
- **Every owner-scoped tool** is in the fixture, and no stranger can tell its owner's data from data that
  never existed.
- **The fixture itself is checked** on dummy tools: a correct one passes under each rule, and a leaky one,
  which answers "forbidden" where it should say "not found", is caught.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from mcp import Client
from mcp.shared.exceptions import MCPError

from tests.support.app import HOST, app, services
from tests.support.isolation import (
    BOTH_PLATFORMS,
    CALLERS,
    OWNERS,
    RESOURCES,
    Caller,
    OpenClient,
    Probe,
    Resource,
    leaks,
)
from tests.support.mcp import MODES, connect, running
from tillhand.api.mcp import TOOL_ACCESS, OwnedTool, Tool
from tillhand.core.constants import UCP_VERSION
from tillhand.core.errors import business_error
from tillhand.models.domain import Owner
from tillhand.models.ucp import Closed, ErrorResponse, Open, RequestMeta, UcpResponseMeta

pytestmark = pytest.mark.anyio


@pytest.fixture(params=MODES)
def mode(request: pytest.FixtureRequest) -> str:
    return request.param


def test_every_owner_scoped_tool_is_in_the_isolation_fixture() -> None:
    owner_scoped = {name for name, access in TOOL_ACCESS.items() if access == "owner_scoped"}
    probed = {probe.tool for resource in RESOURCES for probe in resource.probes}
    probed |= {resource.create_tool for resource in RESOURCES}

    assert probed == owner_scoped


@asynccontextmanager
async def serving(served: FastAPI, mode: str) -> AsyncIterator[OpenClient]:
    """Run the app, and hand out one client per caller: each through its own door, with its own headers."""

    def open_client(caller: Caller) -> Any:
        return connect(served, host=HOST, mode=mode, path=caller.path, headers=caller.headers)

    async with running(served):
        yield open_client


@pytest.mark.parametrize("resource", RESOURCES, ids=lambda r: r.name)
async def test_no_stranger_can_tell_an_owners_data_from_none(mode: str, resource: Resource) -> None:
    async with serving(app(services(pre_approved=BOTH_PLATFORMS)), mode) as open_client:
        assert await leaks(open_client, resource) == []


# -- The fixture, checked on dummy tools -----------------------------------------------------------


class DummyArguments(Open):
    meta: RequestMeta
    id: str | None = None


class DummyThing(Closed):
    ucp: UcpResponseMeta
    id: str


DUMMY_ACCESS = {
    "dummy_create": "owner_scoped",
    "dummy_get": "owner_scoped",
    "dummy_get_leaky": "owner_scoped",
    "dummy_get_order": "owner_scoped",
}


def dummy_tools(owners: dict[str, Owner]) -> list[Tool]:
    """A thing owned by whoever created it, and three ways of reading it back."""

    def not_found() -> ErrorResponse:
        return business_error(code="not_found", content="Not found.", severity="unrecoverable")

    def thing(id: str) -> DummyThing:
        return DummyThing(ucp=UcpResponseMeta(version=UCP_VERSION), id=id)

    async def create(arguments: DummyArguments, owner: Owner) -> DummyThing:
        id = str(uuid.uuid4())
        owners[id] = owner
        return thing(id)

    async def get(arguments: DummyArguments, owner: Owner) -> DummyThing | ErrorResponse:
        id = arguments.id or ""
        return thing(id) if owners.get(id) == owner else not_found()

    async def get_leaky(arguments: DummyArguments, owner: Owner) -> DummyThing | ErrorResponse:
        id = arguments.id or ""
        if id in owners and owners[id] != owner:
            return business_error(code="forbidden", content="Not yours.", severity="unrecoverable")
        return await get(arguments, owner)

    async def get_order(arguments: DummyArguments, owner: Owner) -> DummyThing:
        """#11 decision 7's other rule: no Customer token, so anything not yours asks for one."""
        id = arguments.id or ""
        if owners.get(id) == owner:
            return thing(id)
        raise MCPError(code=-32000, message="Identity required", data={"code": "identity_required"})

    return [
        OwnedTool("dummy_create", "Create a thing.", DummyArguments, create),
        OwnedTool("dummy_get", "Get a thing.", DummyArguments, get),
        OwnedTool("dummy_get_leaky", "Get a thing, leakily.", DummyArguments, get_leaky),
        OwnedTool("dummy_get_order", "Get a thing, Order-style.", DummyArguments, get_order),
    ]


async def create_dummy(client: Client, owner_meta: dict[str, Any]) -> str:
    result = await client.call_tool("dummy_create", {"meta": owner_meta})
    assert isinstance(result.structured_content, dict)
    return result.structured_content["id"]


def dummy_probe(tool: str, rule: Any = "not_found") -> Probe:
    return Probe(tool, rule, lambda id: {"id": id})


def dummy_resource(*probes: Probe) -> Resource:
    return Resource("dummy thing", "dummy_create", create_dummy, dummy_probe("dummy_get"), list(probes))


def dummy_app() -> FastAPI:
    owners: dict[str, Owner] = {}

    def tools(_: Callable[[], Any]) -> list[Tool]:
        return dummy_tools(owners)

    return app(services(pre_approved=BOTH_PLATFORMS), tools=tools, access=DUMMY_ACCESS)


async def test_a_tool_that_hides_other_owners_data_passes(mode: str) -> None:
    async with serving(dummy_app(), mode) as open_client:
        assert await leaks(open_client, dummy_resource(dummy_probe("dummy_get"))) == []


async def test_an_order_style_identity_required_tool_passes(mode: str) -> None:
    async with serving(dummy_app(), mode) as open_client:
        probe = dummy_probe("dummy_get_order", "identity_required")
        assert await leaks(open_client, dummy_resource(probe)) == []


async def test_a_tool_that_says_forbidden_instead_of_not_found_is_caught(mode: str) -> None:
    async with serving(dummy_app(), mode) as open_client:
        found = await leaks(open_client, dummy_resource(dummy_probe("dummy_get_leaky")))

    assert len(found) == len(OWNERS) * (len(CALLERS) - 1)  # every stranger, for every owner
    assert all(f.startswith("dummy_get_leaky:") and "forbidden" in f for f in found)


async def test_customers_through_one_assistant_are_different_owners(mode: str) -> None:
    customer_a = CALLERS["Customer A through the demo assistant"]
    customer_b = CALLERS["Customer B through the demo assistant"]
    async with serving(dummy_app(), mode) as open_client:
        async with open_client(customer_a) as client:
            id = await create_dummy(client, customer_a.meta)
            mine = await client.call_tool("dummy_get", {"meta": {}, "id": id})
        async with open_client(customer_b) as client:
            other = await client.call_tool("dummy_get", {"meta": {}, "id": id})

    assert isinstance(mine.structured_content, dict) and mine.structured_content["id"] == id
    assert isinstance(other.structured_content, dict)
    assert other.structured_content["messages"][0]["code"] == "not_found"
