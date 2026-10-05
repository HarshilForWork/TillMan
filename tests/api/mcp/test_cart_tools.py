"""The cart tools over the real MCP transport, as a Platform calls them (#50).

The Cart rules are tested on the service (tests/services/test_carts.py); this checks the door: the four
tools under their UCP names, a create → update → get round trip at live prices, `cancel_cart`'s
idempotency, and the protocol errors. That another owner gets `not_found` is the isolation fixture's job
(tests/api/mcp/test_isolation.py, which probes every cart tool).
"""

import json
import uuid
from datetime import timedelta
from typing import Any

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from tests.support.app import HOST, app, services
from tests.support.carts import FakeCartStore
from tests.support.catalog import seed
from tests.support.mcp import AGENT, MODES, serve
from tests.support.ucp_spec import schema_errors
from tillhand.core.errors import ServiceUnavailable
from tillhand.models.ucp import MAX_CART_LINES
from tillhand.services.carts import StoreCartService

pytestmark = pytest.mark.anyio

NIACINAMIDE_30 = "var_niacinamide_serum_30"  # ₹599.00
SUNSCREEN_50 = "var_spf50_gel_sunscreen_50"  # ₹449.00


@pytest.fixture(params=MODES)
def mode(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def store() -> FakeCartStore:
    return FakeCartStore(seed("skincare"))


def connect(store: FakeCartStore, mode: str) -> Any:
    carts = StoreCartService(store, lifetime=timedelta(days=7))
    return serve(app(services(carts=carts)), host=HOST, mode=mode)


def lines(*items: tuple[str, int]) -> list[dict[str, Any]]:
    return [{"item": {"id": id}, "quantity": quantity} for id, quantity in items]


async def call(client: Client, tool: str, arguments: dict[str, Any], meta: dict[str, Any] = AGENT) -> Any:
    result = await client.call_tool(tool, {"meta": meta, **arguments})
    assert not result.is_error
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def keyed(key: str | None = None) -> dict[str, Any]:
    return {**AGENT, "idempotency-key": key or str(uuid.uuid4())}


def priced(cart: dict[str, Any]) -> list[tuple[str, int, int]]:
    return [(li["item"]["id"], li["item"]["price"], li["quantity"]) for li in cart["line_items"]]


async def test_the_four_cart_tools_are_listed_under_their_ucp_names(store: FakeCartStore, mode: str) -> None:
    async with connect(store, mode) as client:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}

    required = {name: set(listed[name].input_schema["required"]) for name in listed if "cart" in name}
    assert required == {
        "create_cart": {"meta", "cart"},
        "get_cart": {"meta", "id"},
        "update_cart": {"meta", "id", "cart"},
        "cancel_cart": {"meta", "id"},
    }
    assert all(listed[name].output_schema is None for name in required)


async def test_create_update_and_get_round_trip_at_live_prices(store: FakeCartStore, mode: str) -> None:
    async with connect(store, mode) as client:
        created = await call(client, "create_cart", {"cart": {"line_items": lines((NIACINAMIDE_30, 2))}})
        updated = await call(
            client,
            "update_cart",
            {"id": created["id"], "cart": {"line_items": lines((NIACINAMIDE_30, 1), (SUNSCREEN_50, 2))}},
        )
        got = await call(client, "get_cart", {"id": created["id"]})

    assert priced(created) == [(NIACINAMIDE_30, 59900, 2)]
    assert priced(updated) == [(NIACINAMIDE_30, 59900, 1), (SUNSCREEN_50, 44900, 2)]
    assert updated["totals"][-1] == {"type": "total", "amount": 149700}
    assert got == updated
    assert schema_errors(got, "shopping/cart") == []


async def test_a_price_change_shows_on_the_next_read(store: FakeCartStore, mode: str) -> None:
    async with connect(store, mode) as client:
        created = await call(client, "create_cart", {"cart": {"line_items": lines((NIACINAMIDE_30, 2))}})
        store.variants[NIACINAMIDE_30] = store.variants[NIACINAMIDE_30].model_copy(update={"price": 54900})
        got = await call(client, "get_cart", {"id": created["id"]})

    assert priced(got) == [(NIACINAMIDE_30, 54900, 2)]
    assert got["totals"][-1] == {"type": "total", "amount": 109800}


async def test_cancel_cart_is_idempotent(store: FakeCartStore, mode: str) -> None:
    key = str(uuid.uuid4())
    async with connect(store, mode) as client:
        created = await call(client, "create_cart", {"cart": {"line_items": lines((NIACINAMIDE_30, 2))}})
        first = await client.call_tool("cancel_cart", {"meta": keyed(key), "id": created["id"]})
        retried = await client.call_tool("cancel_cart", {"meta": keyed(key), "id": created["id"]})
        after = await call(client, "get_cart", {"id": created["id"]})

    assert isinstance(first.structured_content, dict)
    assert priced(first.structured_content) == priced(created)
    assert retried.content == first.content  # the same bytes, replayed
    assert [m["code"] for m in after["messages"]] == ["not_found"]


async def test_the_same_key_for_another_request_is_a_protocol_error(store: FakeCartStore, mode: str) -> None:
    key = str(uuid.uuid4())
    async with connect(store, mode) as client:
        await call(client, "cancel_cart", {"id": str(uuid.uuid4())}, meta=keyed(key))
        with pytest.raises(MCPError) as raised:
            await client.call_tool("cancel_cart", {"meta": keyed(key), "id": str(uuid.uuid4())})

    assert raised.value.error.code == -32000
    assert raised.value.error.data == {"code": "idempotency_key_reused"}


async def test_cancel_cart_without_an_idempotency_key_is_invalid(store: FakeCartStore, mode: str) -> None:
    async with connect(store, mode) as client:
        with pytest.raises(MCPError) as raised:
            await client.call_tool("cancel_cart", {"meta": AGENT, "id": str(uuid.uuid4())})

    assert raised.value.error.code == INVALID_PARAMS
    assert "idempotency-key" in json.dumps(raised.value.error.data)


async def test_a_cart_over_the_limits_is_invalid_params(store: FakeCartStore, mode: str) -> None:
    too_many = lines(*[(NIACINAMIDE_30, 1)] * (MAX_CART_LINES + 1))
    async with connect(store, mode) as client:
        for cart in ({"line_items": too_many}, {"line_items": lines((NIACINAMIDE_30, 100))}):
            with pytest.raises(MCPError) as raised:
                await client.call_tool("create_cart", {"meta": AGENT, "cart": cart})
            assert raised.value.error.code == INVALID_PARAMS


async def test_an_unreachable_store_fails_closed(store: FakeCartStore, mode: str) -> None:
    store.fail = ServiceUnavailable("neon.cancel_cart")
    async with connect(store, mode) as client:
        with pytest.raises(MCPError) as raised:
            await client.call_tool("cancel_cart", {"meta": keyed(), "id": str(uuid.uuid4())})

    assert raised.value.error.code == -32000
    assert raised.value.error.message == "Service unavailable"
    assert raised.value.error.data == {"code": "service_unavailable", "retry_after": 5}
