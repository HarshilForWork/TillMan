"""The Cart rules, on the service over a fake store (#50). Every expected value comes from the skincare seed.

The rules (owner's decisions, 5 Oct 2026):
- A Cart holds Variants and quantities; every read prices them live and totals them.
- An unknown id is dropped with a `not_found` message. A known item is kept as asked: out of stock or
  discontinued is an `item_unavailable` error and counts 0 in the totals; more than the stock is an
  `insufficient_stock` warning. A create where nothing is known makes no Cart.
- A Cart expires 7 days after its last change; reads don't extend it.
- Someone else's Cart, an expired one and one that never existed are all the same `not_found`.
- `cancel_cart` returns the Cart's last state, then it's gone. Its idempotency key replays the same answer.
"""

import json
import uuid
from datetime import timedelta
from typing import Any

import pytest

from tests.support.carts import FakeCartStore
from tests.support.catalog import seed
from tillhand.core.constants import CART, UCP_VERSION
from tillhand.core.errors import IdempotencyConflict, RequestTooLarge
from tillhand.models.domain import Owner
from tillhand.models.ucp import (
    CancelCartArguments,
    Cart,
    CreateCartArguments,
    ErrorResponse,
    GetCartArguments,
    UpdateCartArguments,
    ucp_dump,
)
from tillhand.services.carts import StoreCartService

pytestmark = pytest.mark.anyio

HARNESS = Owner(platform="https://tillhand.vercel.app/profiles/harness.json")
STRANGER = Owner(platform="https://platform.example/profiles/agent.json")
META = {"ucp-agent": {"profile": HARNESS.platform}}
NOT_FOUND = {
    "type": "error",
    "code": "not_found",
    "content": "Cart not found or has expired",
    "severity": "unrecoverable",
}

NIACINAMIDE_30 = "var_niacinamide_serum_30"  # ₹599.00, 21 in stock
SUNSCREEN_50 = "var_spf50_gel_sunscreen_50"  # ₹449.00, 50 in stock
NIACINAMIDE_50 = "var_niacinamide_serum_50"  # ₹849.00, 6 in stock
TONER_200 = "var_glycolic_toner_200"  # ₹749.00, out of stock
OVERNIGHT_50 = "var_overnight_recovery_cream_50"  # ₹749.00, discontinued Product


@pytest.fixture
def store() -> FakeCartStore:
    return FakeCartStore(seed("skincare"))


@pytest.fixture
def service(store: FakeCartStore) -> StoreCartService:
    return StoreCartService(store, lifetime=timedelta(days=7))


def lines(*items: tuple[str, int]) -> list[dict[str, Any]]:
    return [{"item": {"id": id}, "quantity": quantity} for id, quantity in items]


async def create(service: StoreCartService, *items: tuple[str, int], owner: Owner = HARNESS) -> Any:
    arguments = CreateCartArguments.model_validate({"meta": META, "cart": {"line_items": lines(*items)}})
    return ucp_dump(await service.create_cart(arguments, owner))


async def get(service: StoreCartService, id: str, owner: Owner = HARNESS) -> Any:
    return ucp_dump(await service.get_cart(GetCartArguments.model_validate({"meta": META, "id": id}), owner))


async def update(service: StoreCartService, id: str, *items: tuple[str, int], owner: Owner = HARNESS) -> Any:
    arguments = UpdateCartArguments.model_validate(
        {"meta": META, "id": id, "cart": {"line_items": lines(*items)}}
    )
    return ucp_dump(await service.update_cart(arguments, owner))


async def cancel(service: StoreCartService, id: str, key: str, owner: Owner = HARNESS) -> Any:
    arguments = CancelCartArguments.model_validate({"meta": {**META, "idempotency-key": key}, "id": id})
    return ucp_dump(await service.cancel_cart(arguments, owner))


def summary(cart: dict[str, Any]) -> list[tuple[str, str, int, int, int]]:
    """Each line as (Variant id, title, unit price, quantity, line total)."""
    return [
        (
            li["item"]["id"],
            li["item"]["title"],
            li["item"]["price"],
            li["quantity"],
            li["totals"][-1]["amount"],
        )
        for li in cart["line_items"]
    ]


def codes(cart: dict[str, Any]) -> list[tuple[str, str, str | None]]:
    return [(m["type"], m["code"], m.get("path")) for m in cart.get("messages", [])]


# -- Create and read, at live prices ---------------------------------------------------------------


async def test_a_new_cart_prices_each_line_and_totals_them(service: StoreCartService) -> None:
    cart = await create(service, (NIACINAMIDE_30, 2), (SUNSCREEN_50, 1))

    assert summary(cart) == [
        (NIACINAMIDE_30, "Niacinamide 10% Serum (30 ml)", 59900, 2, 119800),
        (SUNSCREEN_50, "SPF 50 PA++++ Gel Sunscreen (50 g)", 44900, 1, 44900),
    ]
    assert cart["totals"] == [{"type": "subtotal", "amount": 164700}, {"type": "total", "amount": 164700}]
    assert cart["currency"] == "INR"
    assert cart["ucp"] == {
        "version": UCP_VERSION,
        "status": "success",
        "capabilities": {CART: [{"version": UCP_VERSION}]},
    }
    assert [li["id"] for li in cart["line_items"]] == ["li_1", "li_2"]
    assert "messages" not in cart
    assert str(uuid.UUID(cart["id"])) == cart["id"]
    Cart.model_validate(cart)


async def test_get_cart_returns_what_was_created(service: StoreCartService) -> None:
    created = await create(service, (NIACINAMIDE_30, 2))

    assert await get(service, created["id"]) == created


async def test_a_price_change_shows_on_the_next_read(service: StoreCartService, store: FakeCartStore) -> None:
    created = await create(service, (NIACINAMIDE_30, 2))
    store.variants[NIACINAMIDE_30] = store.variants[NIACINAMIDE_30].model_copy(update={"price": 54900})

    cart = await get(service, created["id"])

    assert summary(cart) == [(NIACINAMIDE_30, "Niacinamide 10% Serum (30 ml)", 54900, 2, 109800)]
    assert cart["totals"][-1] == {"type": "total", "amount": 109800}


async def test_a_sku_names_its_variant(service: StoreCartService) -> None:
    cart = await create(service, ("FW-SER-NIA-30", 1))

    assert summary(cart) == [(NIACINAMIDE_30, "Niacinamide 10% Serum (30 ml)", 59900, 1, 59900)]


# -- Items the Cart can't sell ---------------------------------------------------------------------


async def test_unknown_ids_are_dropped_and_known_items_kept_as_asked(service: StoreCartService) -> None:
    cart = await create(service, (TONER_200, 1), (NIACINAMIDE_50, 10), (OVERNIGHT_50, 1), ("var_nope", 1))

    assert summary(cart) == [
        (TONER_200, "Glycolic Acid Exfoliating Toner (200 ml)", 74900, 1, 0),
        (NIACINAMIDE_50, "Niacinamide 10% Serum (50 ml)", 84900, 10, 849000),
        (OVERNIGHT_50, "Overnight Recovery Cream (50 g)", 74900, 1, 0),
    ]
    assert cart["totals"][-1] == {"type": "total", "amount": 849000}
    assert codes(cart) == [
        ("error", "item_unavailable", "$.line_items[0]"),
        ("warning", "insufficient_stock", "$.line_items[1]"),
        ("error", "item_unavailable", "$.line_items[2]"),
        ("error", "not_found", None),
    ]
    contents = [m["content"] for m in cart["messages"]]
    assert "out of stock" in contents[0]
    assert "Only 6" in contents[1]
    assert "discontinued" in contents[2]
    assert "var_nope" in contents[3]


async def test_unknown_ids_are_reported_only_by_the_write_that_dropped_them(
    service: StoreCartService,
) -> None:
    created = await create(service, (NIACINAMIDE_30, 1), ("var_nope", 1))

    assert "messages" not in await get(service, created["id"])


async def test_a_create_where_nothing_is_known_makes_no_cart(
    service: StoreCartService, store: FakeCartStore
) -> None:
    result = await create(service, ("var_nope", 1), ("var_also_nope", 2))

    ErrorResponse.model_validate(result)
    assert [m["code"] for m in result["messages"]] == ["not_found", "not_found"]
    assert store.carts == {}


async def test_an_empty_cart_can_be_created(service: StoreCartService) -> None:
    cart = await create(service)

    assert cart["line_items"] == []
    assert cart["totals"] == [{"type": "subtotal", "amount": 0}, {"type": "total", "amount": 0}]
    assert cart["currency"] == "INR"


async def test_the_same_variant_twice_becomes_one_line(service: StoreCartService) -> None:
    cart = await create(service, (NIACINAMIDE_30, 2), ("FW-SER-NIA-30", 3))

    assert summary(cart) == [(NIACINAMIDE_30, "Niacinamide 10% Serum (30 ml)", 59900, 5, 299500)]
    assert codes(cart) == [("info", "merged", "$.line_items[0]")]


async def test_merged_quantities_over_the_limit_are_invalid(service: StoreCartService) -> None:
    with pytest.raises(RequestTooLarge):
        await create(service, (NIACINAMIDE_30, 60), (NIACINAMIDE_30, 40))


# -- Update and expiry -----------------------------------------------------------------------------


async def test_update_replaces_every_line(service: StoreCartService) -> None:
    created = await create(service, (NIACINAMIDE_30, 2))

    updated = await update(service, created["id"], (SUNSCREEN_50, 3))

    assert updated["id"] == created["id"]
    assert summary(updated) == [(SUNSCREEN_50, "SPF 50 PA++++ Gel Sunscreen (50 g)", 44900, 3, 134700)]
    assert await get(service, created["id"]) == updated


async def test_an_update_where_nothing_is_known_keeps_the_cart(service: StoreCartService) -> None:
    """Owner's decision: a typo can't wipe a Customer's Cart."""
    created = await create(service, (NIACINAMIDE_30, 2))

    refused = await update(service, created["id"], ("var_nope", 1))

    ErrorResponse.model_validate(refused)
    assert [m["code"] for m in refused["messages"]] == ["not_found"]
    assert "var_nope" in refused["messages"][0]["content"]
    assert await get(service, created["id"]) == created


async def test_an_update_to_no_lines_empties_the_cart(service: StoreCartService) -> None:
    created = await create(service, (NIACINAMIDE_30, 2))

    emptied = await update(service, created["id"])

    assert emptied["line_items"] == []
    assert emptied["totals"][-1] == {"type": "total", "amount": 0}


async def test_an_empty_catalog_is_a_reason_not_a_crash(
    service: StoreCartService, store: FakeCartStore
) -> None:
    store.currency = None

    result = await create(service)

    ErrorResponse.model_validate(result)
    assert [m["code"] for m in result["messages"]] == ["catalog_empty"]


async def test_a_cart_expires_seven_days_after_its_last_change(
    service: StoreCartService, store: FakeCartStore
) -> None:
    created = await create(service, (NIACINAMIDE_30, 1))
    assert created["expires_at"] == "2026-10-12T10:00:00Z"

    store.now += timedelta(days=3)
    updated = await update(service, created["id"], (NIACINAMIDE_30, 2))
    assert updated["expires_at"] == "2026-10-15T10:00:00Z"

    store.now += timedelta(days=6)
    assert (await get(service, created["id"]))["expires_at"] == "2026-10-15T10:00:00Z"  # reads don't extend

    store.now += timedelta(days=1)
    assert (await get(service, created["id"]))["messages"] == [NOT_FOUND]


# -- Someone else's, or nothing at all -------------------------------------------------------------


async def test_someone_elses_cart_is_not_found_like_one_that_never_existed(service: StoreCartService) -> None:
    created = await create(service, (NIACINAMIDE_30, 2))

    theirs = await get(service, created["id"], owner=STRANGER)
    never = await get(service, str(uuid.uuid4()), owner=STRANGER)
    not_a_uuid = await get(service, "cart_abc123", owner=STRANGER)

    assert (
        theirs
        == never
        == not_a_uuid
        == {
            "ucp": {
                "version": UCP_VERSION,
                "status": "error",
                "capabilities": {CART: [{"version": UCP_VERSION}]},
            },
            "messages": [NOT_FOUND],
        }
    )
    assert await update(service, created["id"], (SUNSCREEN_50, 1), owner=STRANGER) == theirs
    assert summary(await get(service, created["id"])) == summary(created)


# -- Cancel, idempotently --------------------------------------------------------------------------


async def test_cancel_returns_the_last_state_then_the_cart_is_gone(service: StoreCartService) -> None:
    created = await create(service, (NIACINAMIDE_30, 2))

    cancelled = await cancel(service, created["id"], str(uuid.uuid4()))

    assert cancelled == created
    assert (await get(service, created["id"]))["messages"] == [NOT_FOUND]
    assert (await cancel(service, created["id"], str(uuid.uuid4())))["messages"] == [NOT_FOUND]


async def test_a_retried_cancel_with_the_same_key_gets_the_same_answer(service: StoreCartService) -> None:
    created = await create(service, (NIACINAMIDE_30, 2))
    key = str(uuid.uuid4())

    first = await cancel(service, created["id"], key)
    retried = await cancel(service, created["id"], key)

    assert json.dumps(retried) == json.dumps(first)
    assert summary(retried) == summary(created)


async def test_the_same_key_for_a_different_cart_is_refused(service: StoreCartService) -> None:
    one = await create(service, (NIACINAMIDE_30, 1))
    other = await create(service, (SUNSCREEN_50, 1))
    key = str(uuid.uuid4())
    await cancel(service, one["id"], key)

    with pytest.raises(IdempotencyConflict):
        await cancel(service, other["id"], key)
    assert summary(await get(service, other["id"])) == summary(other)


async def test_an_idempotency_key_lasts_48_hours(service: StoreCartService, store: FakeCartStore) -> None:
    created = await create(service, (NIACINAMIDE_30, 1))
    key = str(uuid.uuid4())
    first = await cancel(service, created["id"], key)

    store.now += timedelta(hours=47)
    assert await cancel(service, created["id"], key) == first

    store.now += timedelta(hours=2)
    assert (await cancel(service, created["id"], key))["messages"] == [NOT_FOUND]


async def test_keys_are_per_caller(service: StoreCartService) -> None:
    created = await create(service, (NIACINAMIDE_30, 1))
    key = str(uuid.uuid4())
    await cancel(service, str(uuid.uuid4()), key, owner=STRANGER)

    assert summary(await cancel(service, created["id"], key)) == summary(created)
