"""The cart SQL against the real Neon database (#50). Opt-in; needs migration 0002 and the skincare seed:

uv run alembic upgrade head
uv run python scripts/seed_catalog.py data/seeds/skincare.json
TILLHAND_NEON_TESTS=1 uv run pytest -m neon

Every test owns its rows through a Platform URL unique to the run, and deletes them afterwards.
"""

import os
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from tillhand.core.config import get_settings
from tillhand.core.errors import IdempotencyConflict
from tillhand.integrations.neon.carts import NeonCartStore
from tillhand.integrations.neon.pool import Pool, create_pool
from tillhand.models.db import IdempotencyKey, NewCartLine, StoredCart
from tillhand.models.domain import Owner

live = pytest.mark.skipif(os.environ.get("TILLHAND_NEON_TESTS") != "1", reason="set TILLHAND_NEON_TESTS=1")
pytestmark = [pytest.mark.anyio, pytest.mark.neon, live]

WEEK = timedelta(days=7)
NIACINAMIDE_30 = "var_niacinamide_serum_30"  # ₹599.00, 21 in stock
SUNSCREEN_50 = "var_spf50_gel_sunscreen_50"  # ₹449.00, 50 in stock


@pytest.fixture
async def pool() -> AsyncIterator[Pool]:
    pool = await create_pool(get_settings(), max_size=2)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def platform(pool: Pool) -> AsyncIterator[str]:
    url = f"https://tests.example/{uuid.uuid4()}/agent.json"
    yield url
    async with pool.acquire() as conn:
        await conn.execute("delete from carts where owner_platform = $1", url)
        await conn.execute("delete from idempotency_keys where owner_platform = $1", url)


@pytest.fixture
def store(pool: Pool) -> NeonCartStore:
    return NeonCartStore(pool)


def line(variant_id: str, quantity: int) -> NewCartLine:
    return NewCartLine(variant_id=variant_id, quantity=quantity)


def key(request_hash: str = "h1") -> IdempotencyKey:
    return IdempotencyKey(key=uuid.uuid4(), operation="cancel_cart", request_hash=request_hash)


def described(cart: StoredCart | None) -> str:
    return "none" if cart is None else f"{cart.id}:{[(li.variant_id, li.quantity) for li in cart.lines]}"


def lines_of(cart: StoredCart) -> list[tuple[str, int, int | None]]:
    return [(li.variant_id, li.quantity, li.variant.price if li.variant else None) for li in cart.lines]


async def test_variants_are_found_by_id_or_sku_with_live_price_and_stock(store: NeonCartStore) -> None:
    found = {v.id: v for v in await store.find_variants([NIACINAMIDE_30, "FW-SUN-GEL-50", "var_nope"])}

    assert set(found) == {NIACINAMIDE_30, SUNSCREEN_50}
    niacinamide = found[NIACINAMIDE_30]
    assert (niacinamide.title, niacinamide.price, niacinamide.stock, niacinamide.currency) == (
        "Niacinamide 10% Serum (30 ml)",
        59900,
        21,
        "INR",
    )
    assert await store.catalog_currency() == "INR"


async def test_a_created_cart_reads_back_in_order_at_live_prices(store: NeonCartStore, platform: str) -> None:
    owner = Owner(platform=platform)
    created = await store.create_cart(
        owner, currency="INR", lines=[line(SUNSCREEN_50, 1), line(NIACINAMIDE_30, 2)], lifetime=WEEK
    )

    assert created.id.version == 4  # random, never sequential
    assert lines_of(created) == [(SUNSCREEN_50, 1, 44900), (NIACINAMIDE_30, 2, 59900)]
    assert await store.get_cart(owner, created.id) == created


async def test_only_the_owner_finds_a_cart(store: NeonCartStore, platform: str) -> None:
    customer_a = Owner(platform=platform, customer="cust_a")
    created = await store.create_cart(
        customer_a, currency="INR", lines=[line(NIACINAMIDE_30, 1)], lifetime=WEEK
    )

    for stranger in (
        Owner(platform=platform, customer="cust_b"),
        Owner(platform=platform),
        Owner(platform=f"{platform}.other", customer="cust_a"),
    ):
        assert await store.get_cart(stranger, created.id) is None
        assert await store.replace_cart(stranger, created.id, lines=[], lifetime=WEEK) is None
        rendered = await store.cancel_cart(stranger, created.id, key=key(), render=described)
        assert rendered == "none"
    assert await store.get_cart(customer_a, created.id) == created


async def test_replace_swaps_every_line_and_pushes_the_expiry(store: NeonCartStore, platform: str) -> None:
    owner = Owner(platform=platform)
    created = await store.create_cart(owner, currency="INR", lines=[line(NIACINAMIDE_30, 2)], lifetime=WEEK)

    replaced = await store.replace_cart(
        owner, created.id, lines=[line(SUNSCREEN_50, 3)], lifetime=timedelta(days=8)
    )

    assert replaced is not None
    assert lines_of(replaced) == [(SUNSCREEN_50, 3, 44900)]
    assert replaced.expires_at - created.expires_at > timedelta(hours=23)
    assert await store.get_cart(owner, created.id) == replaced


async def test_an_expired_cart_is_not_found(store: NeonCartStore, pool: Pool, platform: str) -> None:
    owner = Owner(platform=platform)
    created = await store.create_cart(owner, currency="INR", lines=[line(NIACINAMIDE_30, 1)], lifetime=WEEK)
    async with pool.acquire() as conn:  # arrange: move its expiry into the past
        await conn.execute(
            "update carts set expires_at = now() - interval '1 second' where id = $1", created.id
        )

    assert await store.get_cart(owner, created.id) is None
    assert await store.replace_cart(owner, created.id, lines=[], lifetime=WEEK) is None


async def test_a_line_whose_variant_left_the_catalog_comes_back_without_one(
    store: NeonCartStore, platform: str
) -> None:
    owner = Owner(platform=platform)
    created = await store.create_cart(owner, currency="INR", lines=[line("var_gone", 1)], lifetime=WEEK)

    assert lines_of(created) == [("var_gone", 1, None)]


async def test_cancel_deletes_the_cart_and_replays_its_answer(store: NeonCartStore, platform: str) -> None:
    owner = Owner(platform=platform)
    created = await store.create_cart(owner, currency="INR", lines=[line(NIACINAMIDE_30, 2)], lifetime=WEEK)
    first_key = key()

    first = await store.cancel_cart(owner, created.id, key=first_key, render=described)
    replayed = await store.cancel_cart(owner, created.id, key=first_key, render=described)
    fresh = await store.cancel_cart(owner, created.id, key=key(), render=described)

    assert first == replayed == described(created)
    assert fresh == "none"
    assert await store.get_cart(owner, created.id) is None
    with pytest.raises(IdempotencyConflict):
        await store.cancel_cart(
            owner, None, key=first_key.model_copy(update={"request_hash": "h2"}), render=described
        )


async def test_a_failed_cancel_leaves_neither_the_effect_nor_the_key(
    store: NeonCartStore, platform: str
) -> None:
    """The key and the cancel share one transaction: when rendering fails, both roll back."""
    owner = Owner(platform=platform)
    created = await store.create_cart(owner, currency="INR", lines=[line(NIACINAMIDE_30, 1)], lifetime=WEEK)
    the_key = key()

    def broken(cart: StoredCart | None) -> str:
        raise RuntimeError("render failed")

    with pytest.raises(RuntimeError):
        await store.cancel_cart(owner, created.id, key=the_key, render=broken)

    assert await store.get_cart(owner, created.id) == created
    assert await store.cancel_cart(owner, created.id, key=the_key, render=described) == described(created)


async def test_keys_are_per_caller(store: NeonCartStore, platform: str) -> None:
    owner = Owner(platform=platform)
    created = await store.create_cart(owner, currency="INR", lines=[line(NIACINAMIDE_30, 1)], lifetime=WEEK)
    shared = key()
    await store.cancel_cart(
        Owner(platform=platform, customer="cust_b"), created.id, key=shared, render=described
    )

    assert await store.cancel_cart(owner, created.id, key=shared, render=described) == described(created)


async def test_a_key_older_than_48_hours_is_forgotten(
    store: NeonCartStore, pool: Pool, platform: str
) -> None:
    owner = Owner(platform=platform)
    old = key()
    await store.cancel_cart(owner, None, key=old, render=lambda _: "first")
    async with pool.acquire() as conn:  # arrange: age the key past the window
        await conn.execute(
            "update idempotency_keys set created_at = now() - interval '49 hours' where key = $1", old.key
        )

    assert await store.cancel_cart(owner, None, key=old, render=lambda _: "second") == "second"
    assert await store.cancel_cart(owner, None, key=old, render=lambda _: "third") == "second"
