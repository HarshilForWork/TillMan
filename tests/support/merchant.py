"""An in-memory fake of the Merchant-door store (keys and Customers), with the SQL's semantics.

`ASSISTANT_URL` is a Merchant assistant's profile: the test app pre-approves it as key-bound, so it works
only on the Merchant door, with a key. The real SQL is checked against Neon by the opt-in `neon` tests.
"""

import uuid
from datetime import UTC, datetime

from tests.support.profiles import PLATFORM_PROFILE
from tillhand.core.errors import UnknownProfile
from tillhand.models.db import MerchantKey
from tillhand.models.ucp import PlatformProfile
from tillhand.services.merchant_door import key_hash

ASSISTANT_URL = "https://demo-merchant.example/profiles/assistant.json"
OTHER_ASSISTANT_URL = "https://other-merchant.example/profiles/assistant.json"


class FakeMerchantStore:
    def __init__(self, *, platforms: set[str]) -> None:
        self.platforms = set(platforms)
        self.keys: dict[uuid.UUID, tuple[str, MerchantKey]] = {}
        self.customers: dict[str, uuid.UUID] = {}

    def stored_values(self) -> list[str]:
        """Every string the store holds, to check that no plaintext key is among them."""
        values: list[str] = []
        for stored_hash, key in self.keys.values():
            values += [stored_hash, *(str(v) for v in key.model_dump().values() if v is not None)]
        return values + list(self.customers)

    async def add_key(self, *, key_hash: str, prefix: str, label: str, profile_url: str) -> MerchantKey:
        if profile_url not in self.platforms:
            raise UnknownProfile(profile_url)
        key = MerchantKey(
            id=uuid.uuid4(),
            prefix=prefix,
            label=label,
            profile_url=profile_url,
            created_at=datetime.now(UTC),
            revoked_at=None,
        )
        self.keys[key.id] = (key_hash, key)
        return key

    async def revoke_key(self, id: uuid.UUID) -> bool:
        found = self.keys.get(id)
        if found is None or found[1].revoked_at is not None:
            return False
        self.keys[id] = (found[0], found[1].model_copy(update={"revoked_at": datetime.now(UTC)}))
        return True

    async def active_key(self, key_hash: str) -> MerchantKey | None:
        return next(
            (key for stored, key in self.keys.values() if stored == key_hash and key.revoked_at is None), None
        )

    async def customer_id(self, merchant_customer_id: str) -> uuid.UUID:
        return self.customers.setdefault(merchant_customer_id, uuid.uuid4())


ASSISTANTS = {
    ASSISTANT_URL: PlatformProfile.model_validate(PLATFORM_PROFILE),
    OTHER_ASSISTANT_URL: PlatformProfile.model_validate(PLATFORM_PROFILE),
}
"""The two Merchant assistants' profiles, pre-approved and key-bound in the test app."""

KEY_A = "thk_test-key-demo-assistant-0000000000001"
KEY_A_ROTATED = "thk_test-key-demo-assistant-0000000000002"
"""A second active key for the same assistant, as during a rotation."""
KEY_OTHER = "thk_test-key-other-assistant-000000000001"

TEST_KEYS = {KEY_A: ASSISTANT_URL, KEY_A_ROTATED: ASSISTANT_URL, KEY_OTHER: OTHER_ASSISTANT_URL}


def merchant_store() -> FakeMerchantStore:
    """Both assistants pre-registered, and the three test keys active."""
    store = FakeMerchantStore(platforms={ASSISTANT_URL, OTHER_ASSISTANT_URL})
    for key, profile_url in TEST_KEYS.items():
        stored = MerchantKey(
            id=uuid.uuid4(),
            prefix=key[:12],
            label=f"test key for {profile_url}",
            profile_url=profile_url,
            created_at=datetime.now(UTC),
            revoked_at=None,
        )
        store.keys[stored.id] = (key_hash(key), stored)
    return store


def key_id(store: FakeMerchantStore, key: str) -> uuid.UUID:
    return next(id for id, (stored, _) in store.keys.items() if stored == key_hash(key))


def merchant_headers(key: str | None, customer: str | None = None) -> dict[str, str]:
    """The Merchant door's headers: the key, and the Merchant's own id for the Customer, if any."""
    headers = {} if key is None else {"TillHand-Api-Key": key}
    return headers if customer is None else {**headers, "TillHand-Customer": customer}
