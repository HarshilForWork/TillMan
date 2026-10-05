"""What the cart queries return: a Cart's lines joined to the live catalog, and the idempotency key a write
carries (#50)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from tillhand.models.db.catalog import Row
from tillhand.models.domain import ProductStatus


class CartVariant(Row):
    """A Variant as a Cart sees it, read live from the catalog: today's price, stock and status."""

    id: str
    sku: str | None
    product_title: str
    product_status: ProductStatus
    option_values: list[str]
    """The Variant's chosen values in the Product's Option order; empty for a Product with no Options."""
    currency: str
    price: int
    stock: int | None
    """`None` is untracked: always available."""

    @property
    def available(self) -> bool:
        return self.product_status == "active" and (self.stock is None or self.stock > 0)

    @property
    def title(self) -> str:
        options = " / ".join(self.option_values)
        return f"{self.product_title} ({options})" if options else self.product_title


class NewCartLine(Row):
    variant_id: str
    quantity: int = Field(ge=1)


class StoredCartLine(Row):
    position: int
    variant_id: str
    quantity: int
    variant: CartVariant | None
    """`None` when the Variant has since left the catalog (a re-seed may delete Variants)."""


class StoredCart(Row):
    id: uuid.UUID
    currency: str
    lines: list[StoredCartLine]
    """In position order."""
    expires_at: datetime


CartOperation = Literal["cancel_cart"]


class IdempotencyKey(Row):
    """A write's `meta["idempotency-key"]`, scoped to its caller (the Owner) and operation (#30, D13)."""

    key: uuid.UUID
    operation: CartOperation
    request_hash: str
    """A hash of the request, minus `meta`: the same key with a different request is refused."""
