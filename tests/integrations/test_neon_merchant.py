"""The Merchant door's SQL against the real Neon database (#41). Opt-in; needs migration 0003:

uv run alembic upgrade head
uv run python scripts/seed_platforms.py
TILLHAND_NEON_TESTS=1 uv run pytest -m neon

Each test registers its own assistant profile under a URL unique to the run, and deletes what it made.
"""

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tests.support.profiles import PLATFORM_PROFILE
from tillhand.core.config import get_settings
from tillhand.core.constants import TILLHAND_SITE
from tillhand.core.errors import Unauthorized, UnknownProfile
from tillhand.integrations.neon.merchant import NeonMerchantStore, NeonPlatformStore
from tillhand.integrations.neon.pool import Pool, create_pool
from tillhand.models.domain import MerchantDoorHeaders, PreApprovedPlatform
from tillhand.services.merchant_door import MerchantDoor, issue_key
from tillhand.services.profiles import registry_entries

live = pytest.mark.skipif(os.environ.get("TILLHAND_NEON_TESTS") != "1", reason="set TILLHAND_NEON_TESTS=1")
pytestmark = [pytest.mark.anyio, pytest.mark.neon, live]


@pytest.fixture
async def pool() -> AsyncIterator[Pool]:
    pool = await create_pool(get_settings(), max_size=4)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def assistant(pool: Pool) -> AsyncIterator[str]:
    """A pre-approved assistant profile unique to this test, removed afterwards with its keys."""
    url = f"https://tests.example/{uuid.uuid4()}/assistant.json"
    entry = PreApprovedPlatform.model_validate(
        {"profile_url": url, "note": "test", "profile": PLATFORM_PROFILE}
    )
    await NeonPlatformStore(pool).upsert([(entry, json.dumps(PLATFORM_PROFILE))])
    try:
        yield url
    finally:
        async with pool.acquire() as conn:
            await conn.execute("delete from merchant_api_keys where profile_url = $1", url)
            await conn.execute("delete from platforms where profile_url = $1", url)


@pytest.fixture
async def customer_refs(pool: Pool) -> AsyncIterator[str]:
    """A prefix for the Merchant's Customer ids in this test; its Customers are removed afterwards."""
    prefix = f"test-{uuid.uuid4()}-"
    yield prefix
    async with pool.acquire() as conn:
        await conn.execute("delete from customers where merchant_customer_id like $1", f"{prefix}%")


async def test_the_seeded_registry_loads_and_validates(pool: Pool) -> None:
    loaded = {p.profile_url: p for p in await NeonPlatformStore(pool).platforms()}

    for entry, _ in registry_entries(await asyncio.to_thread(Path("data/platforms.json").read_bytes)):
        assert loaded[entry.profile_url].profile == entry.profile
    assert f"{TILLHAND_SITE}/profiles/harness.json" in loaded


async def test_a_key_is_stored_only_as_a_hash(pool: Pool, assistant: str) -> None:
    key, stored = await issue_key(NeonMerchantStore(pool), label="test", profile_url=assistant)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select m::text as everything from merchant_api_keys m where id = $1", stored.id
        )
    assert row is not None
    assert key not in row["everything"]
    assert key[len(stored.prefix) :] not in row["everything"]  # not even the secret part


async def test_issuing_a_key_makes_its_profile_key_bound(pool: Pool, assistant: str) -> None:
    platforms = NeonPlatformStore(pool)
    assert not {p.profile_url: p for p in await platforms.platforms()}[assistant].key_bound

    await issue_key(NeonMerchantStore(pool), label="test", profile_url=assistant)
    entry = PreApprovedPlatform.model_validate(
        {"profile_url": assistant, "note": "re-seeded", "profile": PLATFORM_PROFILE}
    )
    await platforms.upsert([(entry, entry.profile.model_dump_json())])  # re-seeding never clears the flag

    assert {p.profile_url: p for p in await platforms.platforms()}[assistant].key_bound


async def test_key_binding_is_read_fresh_from_the_table(pool: Pool, assistant: str) -> None:
    """The public door asks this on every call, so a first key takes effect with no restart."""
    platforms = NeonPlatformStore(pool)
    assert not await platforms.requires_key(assistant)

    await issue_key(NeonMerchantStore(pool), label="first key", profile_url=assistant)

    assert await platforms.requires_key(assistant)
    assert not await platforms.requires_key(f"{TILLHAND_SITE}/profiles/harness.json")
    assert not await platforms.requires_key("https://never-registered.example/profile.json")


async def test_a_key_needs_a_pre_registered_profile(pool: Pool) -> None:
    with pytest.raises(UnknownProfile):
        await issue_key(
            NeonMerchantStore(pool), label="x", profile_url=f"https://tests.example/{uuid.uuid4()}"
        )


async def test_keys_authenticate_side_by_side_until_revoked(pool: Pool, assistant: str) -> None:
    store = NeonMerchantStore(pool)
    door = MerchantDoor(store)
    old, old_stored = await issue_key(store, label="old", profile_url=assistant)
    new, new_stored = await issue_key(store, label="new", profile_url=assistant)

    assert (
        await door.authenticate(MerchantDoorHeaders(api_key=old, customer_ref=None, has_customer_token=False))
    ).key_id == old_stored.id
    assert (
        await door.authenticate(MerchantDoorHeaders(api_key=new, customer_ref=None, has_customer_token=False))
    ).key_id == new_stored.id

    assert await store.revoke_key(old_stored.id)
    with pytest.raises(Unauthorized):
        await door.authenticate(MerchantDoorHeaders(api_key=old, customer_ref=None, has_customer_token=False))
    assert (
        await door.authenticate(MerchantDoorHeaders(api_key=new, customer_ref=None, has_customer_token=False))
    ).profile_url == assistant
    assert not await store.revoke_key(old_stored.id)


async def test_a_customer_id_always_resolves_to_the_same_customer(pool: Pool, customer_refs: str) -> None:
    store = NeonMerchantStore(pool)
    ref = f"{customer_refs}shop_123"

    first = await store.customer_id(ref)
    again = await store.customer_id(ref)
    racing = await asyncio.gather(*(store.customer_id(f"{customer_refs}shop_new") for _ in range(4)))
    other = await store.customer_id(f"{customer_refs}shop_456")

    assert first == again != other
    assert len(set(racing)) == 1  # a concurrent first sight still makes one Customer
    assert first.version == 4
