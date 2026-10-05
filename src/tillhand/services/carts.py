"""The Cart: what `create_cart`, `get_cart`, `update_cart` and `cancel_cart` call (#50, #30 decision 2).

A Cart holds Variants and quantities, never prices: every response prices it live from the catalog, so the
Storefront and an agent can't disagree. Each line is checked on every read too (owner's decisions, 5 Oct):
- **Unknown ids** (no Variant with that id or SKU) are dropped, with a `not_found` message on the write that
  dropped them. A create where nothing is known makes no Cart and answers an error.
- **Known items are kept as asked.** Out of stock or discontinued is an `item_unavailable` error on the
  line, which counts 0 in the totals; more than the stock is an `insufficient_stock` warning. Nothing is
  silently clamped, so a restock brings the line back by itself.
- **The same Variant twice** in one request becomes one line, with an info message.

A Cart belongs to its Owner (#42). Someone else's Cart, an expired one, a cancelled one and an id that
never existed all get the same `not_found`, which never echoes the id, so a stranger learns nothing.

`cancel_cart` requires an idempotency key (#30 decision 11). The store records the key and the response in
the same transaction as the cancel, so a retry with the key replays the first answer, byte for byte.
"""

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Literal, Protocol

from pydantic import TypeAdapter

from tillhand.core.constants import CART, UCP_VERSION
from tillhand.core.deadlines import StepTimeout
from tillhand.core.errors import RequestTooLarge, business_error, timeout_error
from tillhand.models.db import CartVariant, IdempotencyKey, NewCartLine, StoredCart
from tillhand.models.domain import Owner
from tillhand.models.ucp import (
    MAX_LINE_QUANTITY,
    CancelCartArguments,
    CapabilityEntry,
    Cart,
    CartRequest,
    CreateCartArguments,
    ErrorResponse,
    GetCartArguments,
    Item,
    LineItem,
    Message,
    MessageError,
    MessageInfo,
    MessageWarning,
    Total,
    UcpResponseMeta,
    UpdateCartArguments,
    ucp_dump,
)


class CartStore(Protocol):
    """The cart queries (`integrations.neon.carts.NeonCartStore`; a fake in tests). Every function that
    touches a Cart takes its Owner, and finds nothing for anyone else."""

    async def find_variants(self, refs: list[str]) -> list[CartVariant]:
        """The Variants whose id or SKU is in `refs`, live; unknown refs are just absent."""
        ...

    async def catalog_currency(self) -> str | None:
        """The catalog's one currency, for a Cart created empty; `None` if the catalog is empty."""
        ...

    async def create_cart(
        self, owner: Owner, *, currency: str, lines: list[NewCartLine], lifetime: timedelta
    ) -> StoredCart: ...

    async def get_cart(self, owner: Owner, id: uuid.UUID) -> StoredCart | None:
        """The owner's unexpired Cart, its lines joined to the live catalog."""
        ...

    async def replace_cart(
        self, owner: Owner, id: uuid.UUID, *, lines: list[NewCartLine], lifetime: timedelta
    ) -> StoredCart | None:
        """Replace every line and push the expiry out, or `None` if the owner has no such live Cart."""
        ...

    async def cancel_cart(
        self,
        owner: Owner,
        id: uuid.UUID | None,
        *,
        key: IdempotencyKey,
        render: Callable[[StoredCart | None], str],
    ) -> str:
        """In one transaction: replay the key's stored response if it has one (`IdempotencyConflict` if
        it was for a different request), else delete the Cart, `render` its last state (`None`: there
        was none), and store that response under the key. Returns the response."""
        ...


class CartService(Protocol):
    """The four UCP cart tools, by their UCP names. The caller is the Cart's Owner."""

    async def create_cart(self, arguments: CreateCartArguments, owner: Owner) -> Cart | ErrorResponse: ...

    async def get_cart(self, arguments: GetCartArguments, owner: Owner) -> Cart | ErrorResponse: ...

    async def update_cart(self, arguments: UpdateCartArguments, owner: Owner) -> Cart | ErrorResponse: ...

    async def cancel_cart(self, arguments: CancelCartArguments, owner: Owner) -> Cart | ErrorResponse: ...


_RESPONSE: TypeAdapter[Cart | ErrorResponse] = TypeAdapter(Cart | ErrorResponse)


def _not_found() -> ErrorResponse:
    # The same words whoever asks and whatever the id: it must not reveal whether the Cart exists.
    return ErrorResponse(
        ucp=_meta(status="error"),
        messages=[
            MessageError(code="not_found", content="Cart not found or has expired", severity="unrecoverable")
        ],
    )


def _cart_id(raw: str) -> uuid.UUID | None:
    """Cart ids are UUIDs; anything else names no Cart, and gets the same `not_found` as a missing one."""
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


class ResolvedLines:
    """A request's lines resolved against the catalog: merged by Variant, unknown ids dropped."""

    def __init__(self, request: CartRequest, found: list[CartVariant]) -> None:
        by_ref = {ref: v for v in found for ref in (v.id, v.sku) if ref is not None}
        quantities: dict[str, int] = {}
        self.variants: dict[str, CartVariant] = {}
        self.unknown: list[str] = []
        self.merged: set[str] = set()
        """The Variants listed more than once."""
        for line in request.line_items:
            variant = by_ref.get(line.item.id)
            if variant is None:
                self.unknown.append(line.item.id)
                continue
            if variant.id in quantities:
                self.merged.add(variant.id)
            quantities[variant.id] = quantities.get(variant.id, 0) + line.quantity
            self.variants[variant.id] = variant
        if over := [id for id, quantity in quantities.items() if quantity > MAX_LINE_QUANTITY]:
            raise RequestTooLarge(f"at most {MAX_LINE_QUANTITY} of each Variant per Cart: {', '.join(over)}")
        self.lines = [NewCartLine(variant_id=id, quantity=quantity) for id, quantity in quantities.items()]

    @property
    def nothing_known(self) -> bool:
        """Lines were asked for, and none of them names a Variant."""
        return bool(self.unknown) and not self.lines

    def unknown_messages(self) -> list[Message]:
        return [
            MessageError(
                code="not_found",
                content=f"No Variant has the id or SKU {ref}; it was left out.",
                severity="recoverable",
            )
            for ref in self.unknown
        ]

    def refused(self) -> ErrorResponse:
        """The answer when nothing asked for is known: no Cart is made, and none is changed."""
        return ErrorResponse(ucp=_meta(status="error"), messages=self.unknown_messages())


def _meta(*, status: Literal["success", "error"] = "success") -> UcpResponseMeta:
    return UcpResponseMeta(
        version=UCP_VERSION, status=status, capabilities={CART: [CapabilityEntry(version=UCP_VERSION)]}
    )


def _line_message(variant: CartVariant, quantity: int, path: str) -> Message | None:
    """Why this line can't be bought as asked, if it can't."""
    if not variant.available:
        reason = "discontinued" if variant.product_status == "discontinued" else "out of stock"
        return MessageError(
            code="item_unavailable",
            content=f"{variant.title} is {reason}.",
            severity="recoverable",
            path=path,
        )
    if variant.stock is not None and variant.stock < quantity:
        return MessageWarning(
            code="insufficient_stock",
            content=f"Only {variant.stock} of {variant.title} available.",
            path=path,
        )
    return None


def _priced(stored: StoredCart, written: ResolvedLines | None = None) -> Cart:
    """The Cart at today's prices and stock, with a message for every line that can't be bought as asked.

    `written` is the request this response answers, if any: its merged and unknown lines are reported too.
    """
    line_items: list[LineItem] = []
    merged: list[Message] = []
    messages: list[Message] = []
    for line in stored.lines:
        variant = line.variant
        if variant is None:
            messages.append(
                MessageError(
                    code="not_found",
                    content=f"{line.variant_id} is no longer sold; update the Cart to remove it.",
                    severity="recoverable",
                )
            )
            continue
        path = f"$.line_items[{len(line_items)}]"
        if written is not None and variant.id in written.merged:
            merged.append(
                MessageInfo(
                    code="merged",
                    content="The same Variant was listed twice; its quantities were added.",
                    path=path,
                )
            )
        if (message := _line_message(variant, line.quantity, path)) is not None:
            messages.append(message)
        amount = variant.price * line.quantity if variant.available else 0
        line_items.append(
            LineItem(
                id=f"li_{len(line_items) + 1}",
                item=Item(id=variant.id, title=variant.title, price=variant.price),
                quantity=line.quantity,
                totals=_totals(amount),
            )
        )
    unknown = written.unknown_messages() if written is not None else []
    return Cart(
        ucp=_meta(),
        id=str(stored.id),
        line_items=line_items,
        currency=stored.currency,
        totals=_totals(sum(li.totals[-1].amount for li in line_items)),
        messages=[*merged, *messages, *unknown] or None,
        expires_at=stored.expires_at,
    )


def _totals(amount: int) -> list[Total]:
    return [Total(type="subtotal", amount=amount), Total(type="total", amount=amount)]


def _request_hash(arguments: CancelCartArguments) -> str:
    """What the request asked, minus `meta`: the same key may only ever repeat the same request."""
    body = arguments.model_dump(mode="json", by_alias=True, exclude={"meta"})
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


class StoreCartService:
    """`CartService` over a `CartStore`. `lifetime` is how long a Cart lives after its last change."""

    def __init__(self, store: CartStore, *, lifetime: timedelta) -> None:
        self._store = store
        self._lifetime = lifetime

    async def _resolve(self, request: CartRequest) -> ResolvedLines:
        refs = list(dict.fromkeys(line.item.id for line in request.line_items))
        return ResolvedLines(request, await self._store.find_variants(refs) if refs else [])

    async def create_cart(self, arguments: CreateCartArguments, owner: Owner) -> Cart | ErrorResponse:
        try:
            resolved = await self._resolve(arguments.cart)
            if resolved.nothing_known:
                return resolved.refused()
            variants = list(resolved.variants.values())
            currency = variants[0].currency if variants else await self._store.catalog_currency()
            if currency is None:
                return business_error(
                    code="catalog_empty",
                    content="The Merchant's catalog is empty, so no Cart can be made.",
                    severity="unrecoverable",
                )
            stored = await self._store.create_cart(
                owner, currency=currency, lines=resolved.lines, lifetime=self._lifetime
            )
        except StepTimeout as exc:
            return timeout_error(exc.step)
        return _priced(stored, resolved)

    async def get_cart(self, arguments: GetCartArguments, owner: Owner) -> Cart | ErrorResponse:
        id = _cart_id(arguments.id)
        if id is None:
            return _not_found()
        try:
            stored = await self._store.get_cart(owner, id)
        except StepTimeout as exc:
            return timeout_error(exc.step)
        return _not_found() if stored is None else _priced(stored)

    async def update_cart(self, arguments: UpdateCartArguments, owner: Owner) -> Cart | ErrorResponse:
        id = _cart_id(arguments.id)
        if id is None:
            return _not_found()
        try:
            resolved = await self._resolve(arguments.cart)
            if resolved.nothing_known:
                # Owner's decision: a request naming only unknown ids is refused, and the Cart is kept.
                # It never touches the store, so it answers the same whoever owns the id.
                return resolved.refused()
            stored = await self._store.replace_cart(owner, id, lines=resolved.lines, lifetime=self._lifetime)
        except StepTimeout as exc:
            return timeout_error(exc.step)
        return _not_found() if stored is None else _priced(stored, resolved)

    async def cancel_cart(self, arguments: CancelCartArguments, owner: Owner) -> Cart | ErrorResponse:
        key = IdempotencyKey(
            key=arguments.meta.key, operation="cancel_cart", request_hash=_request_hash(arguments)
        )

        def render(stored: StoredCart | None) -> str:
            return json.dumps(ucp_dump(_not_found() if stored is None else _priced(stored)))

        try:
            text = await self._store.cancel_cart(owner, _cart_id(arguments.id), key=key, render=render)
        except StepTimeout as exc:
            return timeout_error(exc.step)
        return _RESPONSE.validate_json(text)
