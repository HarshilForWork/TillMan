"""UCP Cart capability (`shopping/cart.json`, `types/line_item.json`, `types/item.json`, `totals.json`).

The MCP binding (`cart/mcp.md`, `services/shopping/mcp.openrpc.json`): the tool arguments are `{meta, cart}`
to create, `{meta, id}` to get or cancel, and `{meta, id, cart}` to update. The `cart` payload never carries
an id. `cancel_cart` also requires `meta["idempotency-key"]`, a UUID.

Requests are `Open` (Platforms may send fields we don't use, such as a line's `totals` from an earlier
response); what we return is `Closed`. Our limits are stricter than the spec's: at most `MAX_CART_LINES`
lines and `MAX_LINE_QUANTITY` of each, so an oversized request is invalid params (`-32602`).
"""

import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import AfterValidator, Field, model_validator

from .catalog import QuantityUnit, RequestMeta, UnitPrice
from .common import (
    MAX_SAFE_INTEGER,
    Actions,
    Amount,
    Attribution,
    Closed,
    Context,
    CurrencyCode,
    Link,
    Message,
    Open,
    Policy,
    Signals,
    UcpResponseMeta,
    Url,
)

MAX_CART_LINES = 50
MAX_LINE_QUANTITY = 99

SignedAmount = Annotated[int, Field(ge=-MAX_SAFE_INTEGER, le=MAX_SAFE_INTEGER, strict=True)]


class TotalLine(Closed):
    display_text: str
    amount: SignedAmount


class Total(Closed):
    """One entry of a cost breakdown (`common/types/total.json`), e.g. the subtotal."""

    type: str
    amount: SignedAmount
    display_text: str | None = None
    lines: list[TotalLine] | None = None


def _one_subtotal_and_one_total(totals: list[Total]) -> list[Total]:
    kinds = [t.type for t in totals]
    if kinds.count("subtotal") != 1 or kinds.count("total") != 1:
        raise ValueError("totals need exactly one subtotal and one total")
    return totals


Totals = Annotated[list[Total], AfterValidator(_one_subtotal_and_one_total)]


class ItemRequest(Open):
    """What a line asks for: the Variant, by its id (or SKU)."""

    id: Annotated[str, Field(min_length=1)]
    quantity_unit: QuantityUnit | None = None


class Item(Closed):
    id: str
    title: str
    price: Amount
    """Per unit, in minor units, at the live price."""
    quantity_unit: QuantityUnit | None = None
    unit_price: UnitPrice | None = None
    image_url: Url | None = None


class LineItemRequest(Open):
    id: str | None = None
    item: ItemRequest
    quantity: Annotated[int, Field(ge=1, le=MAX_LINE_QUANTITY)]
    parent_id: str | None = None


class LineItem(Closed):
    id: str
    item: Item
    quantity: Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)]
    totals: Totals
    parent_id: str | None = None


class Buyer(Open):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone_number: str | None = None


class CartRequest(Open):
    """The `cart` of `create_cart` and `update_cart`: the whole Cart, since an update replaces it."""

    line_items: Annotated[list[LineItemRequest], Field(max_length=MAX_CART_LINES)]
    context: Context | None = None
    signals: Signals | None = None
    attribution: Attribution | None = None
    buyer: Buyer | None = None

    @model_validator(mode="after")
    def _no_id(self) -> Self:
        if "id" in (self.model_extra or {}):
            raise ValueError("the cart payload carries no id: name the Cart with the top-level id")
        return self


class Cart(Closed):
    ucp: UcpResponseMeta
    id: str
    line_items: list[LineItem]
    currency: CurrencyCode
    totals: Totals
    context: Context | None = None
    signals: Signals | None = None
    attribution: Attribution | None = None
    buyer: Buyer | None = None
    actions: Actions | None = None
    messages: list[Message] | None = None
    links: list[Link] | None = None
    policies: list[Policy] | None = None
    continue_url: Url | None = None
    expires_at: datetime | None = None


# -- MCP tool arguments --------------------------------------------------------------------------


class IdempotentRequestMeta(RequestMeta):
    """`meta` for a tool that requires `idempotency-key` (a UUID), such as `cancel_cart`."""

    @model_validator(mode="after")
    def _key_is_a_uuid(self) -> Self:
        if self.idempotency_key is None:
            raise ValueError("meta.idempotency-key is required")
        try:
            uuid.UUID(self.idempotency_key)
        except ValueError:
            raise ValueError("meta.idempotency-key must be a UUID") from None
        return self

    @property
    def key(self) -> uuid.UUID:
        """`idempotency-key`, which the validator has checked is present and a UUID."""
        return uuid.UUID(self.idempotency_key or "")


CartId = Annotated[str, Field(min_length=1)]


class CreateCartArguments(Open):
    meta: RequestMeta
    cart: CartRequest


class GetCartArguments(Open):
    meta: RequestMeta
    id: CartId


class UpdateCartArguments(Open):
    meta: RequestMeta
    id: CartId
    cart: CartRequest


class CancelCartArguments(Open):
    meta: IdempotentRequestMeta
    id: CartId
