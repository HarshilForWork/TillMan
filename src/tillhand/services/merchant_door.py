"""The Merchant door's rules: Merchant API keys and Customer identification (#41, #11 decisions 3 and 5).

The Merchant door (`/merchant/mcp`) is how the Merchant's own assistant reaches the tools. Unlike a
Platform on the public door, whose profile URL is only a claim, it proves who it is with a secret key:
- **A key** is `thk_` plus 32 random bytes, shown once when issued. Only its SHA-256 hash is stored, with a
  short visible prefix for telling keys apart, so a leaked database holds no usable key. A fast hash is
  right here: the key is long and random, so there is nothing to guess (passwords need a slow hash).
- **Each key acts as exactly one pre-registered profile.** Two keys may be active at once, so a Merchant
  can rotate without downtime. Revoking is immediate: every request looks the key up afresh.
- **A missing, wrong or revoked key** is the same `Unauthorized`, so trying keys reveals nothing.
- **`TillHand-Customer`** carries the Merchant's own id for a Customer. Only after the key is valid, it
  resolves to one Customer row (made the first time). It is never accepted with a Customer token: the
  Merchant vouches, or the Customer signs in, but not both (#11 decision 5).
"""

import hashlib
import secrets
import uuid
from typing import Protocol

from tillhand.core.errors import Unauthorized
from tillhand.models.db import MerchantKey
from tillhand.models.domain import MerchantCaller, MerchantDoorHeaders

KEY_PREFIX = "thk_"
VISIBLE_PREFIX_LENGTH = 12
MAX_CUSTOMER_REF = 255


class CustomerHeaderError(ValueError):
    """`TillHand-Customer` is malformed, or came with a Customer token. A malformed request, not a
    failed sign-in: the key was valid."""


class MerchantKeyStore(Protocol):
    """Keys and Customers (`integrations.neon.merchant.NeonMerchantStore`; a fake in tests)."""

    async def add_key(self, *, key_hash: str, prefix: str, label: str, profile_url: str) -> MerchantKey:
        """`UnknownProfile` unless `profile_url` is pre-registered."""
        ...

    async def revoke_key(self, id: uuid.UUID) -> bool:
        """`True` if an active key was revoked now."""
        ...

    async def active_key(self, key_hash: str) -> MerchantKey | None: ...

    async def customer_id(self, merchant_customer_id: str) -> uuid.UUID:
        """The Customer the Merchant knows by this id, made on first sight."""
        ...


def new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def issue_key(store: MerchantKeyStore, *, label: str, profile_url: str) -> tuple[str, MerchantKey]:
    """A new key for `profile_url`: the key itself, to show once, and what is stored about it."""
    key = new_key()
    stored = await store.add_key(
        key_hash=key_hash(key), prefix=key[:VISIBLE_PREFIX_LENGTH], label=label, profile_url=profile_url
    )
    return key, stored


def _customer_ref(raw: str) -> str:
    ref = raw.strip()
    if not ref or len(ref) > MAX_CUSTOMER_REF or not ref.isprintable():
        raise CustomerHeaderError(f"TillHand-Customer must be 1 to {MAX_CUSTOMER_REF} printable characters")
    return ref


class MerchantDoor:
    def __init__(self, store: MerchantKeyStore) -> None:
        self._store = store

    async def authenticate(self, headers: MerchantDoorHeaders) -> MerchantCaller:
        """The caller a request's headers prove, or `Unauthorized`. The key is checked first, so nothing
        about the Customer is looked at, or created, for a request without a valid key."""
        key = await self._store.active_key(key_hash(headers.api_key)) if headers.api_key else None
        if key is None:
            raise Unauthorized
        customer = None
        if (customer_ref := headers.customer_ref) is not None:
            if headers.has_customer_token:
                raise CustomerHeaderError("send TillHand-Customer or a Customer token, never both")
            customer = await self._store.customer_id(_customer_ref(customer_ref))
        return MerchantCaller(key_id=key.id, profile_url=key.profile_url, customer=customer)
