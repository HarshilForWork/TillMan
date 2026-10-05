"""An in-memory fake of the cart store, with the same semantics as the SQL in `integrations/neon/carts.py`.

Owner matching, expiry and the 48-hour idempotency window behave as the SQL does; the real SQL is checked
against Neon by the opt-in `neon` tests. Time is `now`, which tests move forward; prices and stock are
`variants`, which tests change to show that a Cart is re-priced on every read.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tillhand.core.errors import IdempotencyConflict
from tillhand.models.db import CartVariant, IdempotencyKey, NewCartLine, StoredCart, StoredCartLine
from tillhand.models.domain import Catalog, Owner

IDEMPOTENCY_WINDOW = timedelta(hours=48)


def cart_variants(catalog: Catalog) -> dict[str, CartVariant]:
    return {
        v.id: CartVariant(
            id=v.id,
            sku=v.sku,
            product_title=p.title,
            product_status=p.status,
            option_values=p.option_values(v),
            currency=p.currency,
            price=v.price,
            stock=v.stock,
        )
        for p in catalog.products
        for v in p.variants
    }


@dataclass
class _Cart:
    owner: Owner
    currency: str
    lines: list[NewCartLine]
    expires_at: datetime


@dataclass
class _Key:
    request_hash: str
    response: str
    created_at: datetime


class FakeCartStore:
    def __init__(self, catalog: Catalog) -> None:
        self.currency: str | None = catalog.currency
        self.variants = cart_variants(catalog)
        self.carts: dict[uuid.UUID, _Cart] = {}
        self.keys: dict[tuple[uuid.UUID, Owner, str], _Key] = {}
        self.now = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
        self.fail: Exception | None = None

    def _check(self) -> None:
        if self.fail is not None:
            raise self.fail

    def _stored(self, id: uuid.UUID, cart: _Cart) -> StoredCart:
        return StoredCart(
            id=id,
            currency=cart.currency,
            lines=[
                StoredCartLine(
                    position=position,
                    variant_id=line.variant_id,
                    quantity=line.quantity,
                    variant=self.variants.get(line.variant_id),
                )
                for position, line in enumerate(cart.lines)
            ],
            expires_at=cart.expires_at,
        )

    def _live(self, owner: Owner, id: uuid.UUID) -> _Cart | None:
        cart = self.carts.get(id)
        return cart if cart is not None and cart.owner == owner and cart.expires_at > self.now else None

    async def find_variants(self, refs: list[str]) -> list[CartVariant]:
        self._check()
        wanted = set(refs)
        return [v for v in self.variants.values() if v.id in wanted or v.sku in wanted]

    async def catalog_currency(self) -> str | None:
        self._check()
        return self.currency

    async def create_cart(
        self, owner: Owner, *, currency: str, lines: list[NewCartLine], lifetime: timedelta
    ) -> StoredCart:
        self._check()
        id = uuid.uuid4()
        self.carts[id] = _Cart(owner, currency, list(lines), self.now + lifetime)
        return self._stored(id, self.carts[id])

    async def get_cart(self, owner: Owner, id: uuid.UUID) -> StoredCart | None:
        self._check()
        cart = self._live(owner, id)
        return None if cart is None else self._stored(id, cart)

    async def replace_cart(
        self, owner: Owner, id: uuid.UUID, *, lines: list[NewCartLine], lifetime: timedelta
    ) -> StoredCart | None:
        self._check()
        cart = self._live(owner, id)
        if cart is None:
            return None
        cart.lines = list(lines)
        cart.expires_at = self.now + lifetime
        return self._stored(id, cart)

    async def cancel_cart(
        self,
        owner: Owner,
        id: uuid.UUID | None,
        *,
        key: IdempotencyKey,
        render: Callable[[StoredCart | None], str],
    ) -> str:
        self._check()
        slot = (key.key, owner, key.operation)
        earlier = self.keys.get(slot)
        if earlier is not None and earlier.created_at > self.now - IDEMPOTENCY_WINDOW:
            if earlier.request_hash != key.request_hash:
                raise IdempotencyConflict
            return earlier.response
        cart = None if id is None else self._live(owner, id)
        stored = None if cart is None or id is None else self._stored(id, cart)
        if id is not None and cart is not None:
            del self.carts[id]
        response = render(stored)
        self.keys[slot] = _Key(key.request_hash, response, self.now)
        return response
