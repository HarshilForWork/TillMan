"""Merchant API keys and Customer identification on the Merchant door, on the service over a fake store (#41).

The rules (#11 decisions 3 and 5; owner's decisions, 5 Oct 2026):
- A key is `thk_…`, shown once when issued. Only its SHA-256 hash and a short visible prefix are stored.
- Each key maps to exactly one pre-registered profile. Revoking it takes effect on the very next request.
- A missing, wrong or revoked key is the same `Unauthorized`, so a guesser learns nothing.
- `TillHand-Customer` (the Merchant's own id for a Customer) always resolves to the same Customer, and is
  never accepted together with a Customer token.
"""

import pytest

from tests.support.merchant import ASSISTANT_URL, FakeMerchantStore
from tillhand.core.errors import Unauthorized, UnknownProfile
from tillhand.models.domain import MerchantDoorHeaders
from tillhand.services.merchant_door import (
    CustomerHeaderError,
    MerchantDoor,
    issue_key,
    key_hash,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def store() -> FakeMerchantStore:
    return FakeMerchantStore(platforms={ASSISTANT_URL})


@pytest.fixture
def door(store: FakeMerchantStore) -> MerchantDoor:
    return MerchantDoor(store)


async def test_an_issued_key_is_shown_once_and_stored_only_as_a_hash(store: FakeMerchantStore) -> None:
    key, issued = await issue_key(store, label="Demo assistant", profile_url=ASSISTANT_URL)

    assert key.startswith("thk_") and len(key) >= 40
    assert issued.prefix == key[:12] and issued.label == "Demo assistant"
    assert issued.profile_url == ASSISTANT_URL and issued.revoked_at is None
    stored = store.stored_values()
    assert key_hash(key) in stored
    assert all(key not in value for value in stored)  # the plaintext key is nowhere


async def test_two_keys_are_never_the_same(store: FakeMerchantStore) -> None:
    first, _ = await issue_key(store, label="a", profile_url=ASSISTANT_URL)
    second, _ = await issue_key(store, label="b", profile_url=ASSISTANT_URL)

    assert first != second


async def test_a_key_needs_a_pre_registered_profile(store: FakeMerchantStore) -> None:
    with pytest.raises(UnknownProfile):
        await issue_key(store, label="x", profile_url="https://nobody.example/profile.json")


async def test_a_valid_key_names_its_profile_and_key(store: FakeMerchantStore, door: MerchantDoor) -> None:
    key, issued = await issue_key(store, label="Demo assistant", profile_url=ASSISTANT_URL)

    caller = await door.authenticate(
        MerchantDoorHeaders(api_key=key, customer_ref=None, has_customer_token=False)
    )

    assert (caller.key_id, caller.profile_url, caller.customer) == (issued.id, ASSISTANT_URL, None)


@pytest.mark.parametrize("api_key", [None, "", "thk_wrong", "not-even-a-key"])
async def test_a_missing_or_wrong_key_is_unauthorized(door: MerchantDoor, api_key: str | None) -> None:
    with pytest.raises(Unauthorized):
        await door.authenticate(
            MerchantDoorHeaders(api_key=api_key, customer_ref=None, has_customer_token=False)
        )


async def test_a_revoked_key_fails_on_its_very_next_request(
    store: FakeMerchantStore, door: MerchantDoor
) -> None:
    key, issued = await issue_key(store, label="old", profile_url=ASSISTANT_URL)
    await door.authenticate(MerchantDoorHeaders(api_key=key, customer_ref=None, has_customer_token=False))

    assert await store.revoke_key(issued.id)
    with pytest.raises(Unauthorized):
        await door.authenticate(MerchantDoorHeaders(api_key=key, customer_ref=None, has_customer_token=False))
    assert not await store.revoke_key(issued.id)  # already revoked


async def test_two_keys_work_side_by_side_during_rotation(
    store: FakeMerchantStore, door: MerchantDoor
) -> None:
    old, old_key = await issue_key(store, label="old", profile_url=ASSISTANT_URL)
    new, new_key = await issue_key(store, label="new", profile_url=ASSISTANT_URL)

    assert (
        await door.authenticate(MerchantDoorHeaders(api_key=old, customer_ref=None, has_customer_token=False))
    ).key_id == old_key.id
    assert (
        await door.authenticate(MerchantDoorHeaders(api_key=new, customer_ref=None, has_customer_token=False))
    ).key_id == new_key.id


async def test_the_same_customer_header_always_resolves_to_the_same_customer(
    store: FakeMerchantStore, door: MerchantDoor
) -> None:
    key, _ = await issue_key(store, label="a", profile_url=ASSISTANT_URL)
    other_key, _ = await issue_key(store, label="b", profile_url=ASSISTANT_URL)

    first = await door.authenticate(
        MerchantDoorHeaders(api_key=key, customer_ref="shop_123", has_customer_token=False)
    )
    again = await door.authenticate(
        MerchantDoorHeaders(api_key=other_key, customer_ref="shop_123", has_customer_token=False)
    )
    someone_else = await door.authenticate(
        MerchantDoorHeaders(api_key=key, customer_ref="shop_456", has_customer_token=False)
    )

    assert first.customer is not None and first.customer == again.customer
    assert someone_else.customer not in (None, first.customer)
    assert len(store.customers) == 2


async def test_a_customer_header_with_a_customer_token_is_refused(
    store: FakeMerchantStore, door: MerchantDoor
) -> None:
    key, _ = await issue_key(store, label="a", profile_url=ASSISTANT_URL)

    with pytest.raises(CustomerHeaderError, match="never both"):
        await door.authenticate(
            MerchantDoorHeaders(api_key=key, customer_ref="shop_123", has_customer_token=True)
        )


@pytest.mark.parametrize("customer_ref", ["", "   ", "x" * 256, "shop\n123"])
async def test_a_malformed_customer_header_is_refused(
    store: FakeMerchantStore, door: MerchantDoor, customer_ref: str
) -> None:
    key, _ = await issue_key(store, label="a", profile_url=ASSISTANT_URL)

    with pytest.raises(CustomerHeaderError):
        await door.authenticate(
            MerchantDoorHeaders(api_key=key, customer_ref=customer_ref, has_customer_token=False)
        )
    assert store.customers == {}


async def test_the_customer_header_is_checked_only_after_the_key(door: MerchantDoor) -> None:
    """A bad key with a Customer header is still just Unauthorized: no Customer row is made for a stranger."""
    with pytest.raises(Unauthorized):
        await door.authenticate(
            MerchantDoorHeaders(api_key="thk_wrong", customer_ref="shop_123", has_customer_token=False)
        )
