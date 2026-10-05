"""All cart SQL (#50), under the owned-data rules in `integrations.neon` (#42).

A Cart is two tables: `carts` (the owner, currency and expiry) and `cart_lines` (Variant and quantity).
No price is ever stored: every read joins `variants` and `products`, so a Cart is always at today's price.
A read is one round trip, bounded by the Cart's line limit.

Every write runs in one short transaction of DB statements only. `cancel_cart` also writes its
idempotency key and response in that same transaction (#30 decision 11), so a crash can't leave the
cancel without its key, or the key without the cancel.

A connection failure is `ServiceUnavailable`: the caller is refused (fails closed) rather than left with a
half-known outcome. A query that runs out of time is `StepTimeout`, named after the step.
"""

import uuid
from collections.abc import Callable, Sequence
from datetime import timedelta

import asyncpg

from tillhand.core.errors import IdempotencyConflict
from tillhand.integrations.neon.pool import Connection, Pool, connection
from tillhand.models.db import CartVariant, IdempotencyKey, NewCartLine, StoredCart, StoredCartLine
from tillhand.models.domain import Owner
from tillhand.models.ucp import MAX_CART_LINES

IDEMPOTENCY_HOURS = 48
SWEEP_BATCH = 100
"""How many expired idempotency keys one write deletes, at most."""

_VARIANT_COLUMNS = """
    v.id, v.sku, p.title as product_title, p.status as product_status, v.option_values, p.currency,
    v.price, v.stock
"""

_FIND_VARIANTS = f"""
select {_VARIANT_COLUMNS}
from variants v join products p on p.id = v.product_id
where v.id = any($1::text[]) or v.sku = any($1::text[])
limit {2 * MAX_CART_LINES}
"""
"""A request names at most MAX_CART_LINES refs, each matching at most an id and a SKU."""

# $1 the Cart, $2/$3 the Owner. One row per line in position order; a Cart with no lines comes back as
# one row with null line columns. A line whose Variant has gone has null Variant columns.
_GET_CART = f"""
select c.id as cart_id, c.currency as cart_currency, c.expires_at,
       l.position, l.variant_id as line_variant_id, l.quantity,
       {_VARIANT_COLUMNS}
from carts c
left join cart_lines l on l.cart_id = c.id
left join variants v on v.id = l.variant_id
left join products p on p.id = v.product_id
where c.id = $1
  and c.owner_platform = $2 and c.owner_customer is not distinct from $3
  and c.expires_at > now()
order by l.position
"""

_INSERT_LINES = """
insert into cart_lines (cart_id, position, variant_id, quantity)
select $1, t.ord - 1, t.variant_id, t.quantity
from unnest($2::text[], $3::integer[]) with ordinality as t(variant_id, quantity, ord)
"""

_STORED_KEY = f"""
select request_hash, response from idempotency_keys
where key = $1 and owner_platform = $2 and owner_customer is not distinct from $3 and operation = $4
  and created_at > now() - interval '{IDEMPOTENCY_HOURS} hours'
"""

# Replaces a key only once it has aged out of the window. A fresh row held by someone else (a concurrent
# retry with the same key) updates nothing, so no row comes back, and the caller replays theirs instead.
_STORE_KEY = f"""
insert into idempotency_keys (key, owner_platform, owner_customer, operation, request_hash, response)
values ($1, $2, $3, $4, $5, $6)
on conflict (key, owner_platform, owner_customer, operation) do update
    set request_hash = excluded.request_hash, response = excluded.response, created_at = now()
    where idempotency_keys.created_at <= now() - interval '{IDEMPOTENCY_HOURS} hours'
returning 1
"""

_SWEEP_KEYS = f"""
delete from idempotency_keys where ctid in (
    select ctid from idempotency_keys
    where created_at <= now() - interval '{IDEMPOTENCY_HOURS} hours'
    limit {SWEEP_BATCH}
)
"""


class _Raced(Exception):
    """A concurrent call with the same key stored its response first: roll back, then replay theirs."""


def _owner(owner: Owner) -> tuple[str, str | None]:
    """The two owner columns every owned table has, `owner_platform` and `owner_customer`."""
    return owner.platform, owner.customer


def _variant(row: asyncpg.Record) -> CartVariant:
    return CartVariant(
        id=row["id"],
        sku=row["sku"],
        product_title=row["product_title"],
        product_status=row["product_status"],
        option_values=list(row["option_values"]),
        currency=row["currency"],
        price=row["price"],
        stock=row["stock"],
    )


def _stored(rows: Sequence[asyncpg.Record]) -> StoredCart | None:
    if not rows:
        return None
    first = rows[0]
    return StoredCart(
        id=first["cart_id"],
        currency=first["cart_currency"],
        expires_at=first["expires_at"],
        lines=[
            StoredCartLine(
                position=row["position"],
                variant_id=row["line_variant_id"],
                quantity=row["quantity"],
                variant=None if row["id"] is None else _variant(row),
            )
            for row in rows
            if row["position"] is not None
        ],
    )


class NeonCartStore:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def find_variants(self, refs: list[str]) -> list[CartVariant]:
        """Public catalog data, so no Owner: the Variants whose id or SKU is in `refs`."""
        async with connection(self._pool, "neon.find_variants") as conn:
            rows = await conn.fetch(_FIND_VARIANTS, refs)
        return [_variant(row) for row in rows]

    async def catalog_currency(self) -> str | None:
        """The catalog's one currency (each Merchant's catalog has one), for a Cart created empty."""
        async with connection(self._pool, "neon.catalog_currency") as conn:
            return await conn.fetchval("select currency from products order by position limit 1")

    async def create_cart(
        self, owner: Owner, *, currency: str, lines: list[NewCartLine], lifetime: timedelta
    ) -> StoredCart:
        async with connection(self._pool, "neon.create_cart") as conn, conn.transaction():
            id = await conn.fetchval(
                "insert into carts (owner_platform, owner_customer, currency, expires_at) "
                "values ($1, $2, $3, now() + $4::interval) returning id",
                *_owner(owner),
                currency,
                lifetime,
            )
            await _insert_lines(conn, id, lines)
            stored = _stored(await conn.fetch(_GET_CART, id, *_owner(owner)))
        if stored is None:
            raise RuntimeError(f"Cart {id} was created in this transaction but doesn't read back")
        return stored

    async def get_cart(self, owner: Owner, id: uuid.UUID) -> StoredCart | None:
        async with connection(self._pool, "neon.get_cart") as conn:
            return _stored(await conn.fetch(_GET_CART, id, *_owner(owner)))

    async def replace_cart(
        self, owner: Owner, id: uuid.UUID, *, lines: list[NewCartLine], lifetime: timedelta
    ) -> StoredCart | None:
        async with connection(self._pool, "neon.replace_cart") as conn, conn.transaction():
            # The UPDATE locks the Cart's row until commit, so two concurrent replaces of one Cart take
            # turns instead of interleaving their lines.
            found = await conn.fetchval(
                "update carts set expires_at = now() + $4::interval, updated_at = now() "
                "where id = $1 and owner_platform = $2 and owner_customer is not distinct from $3 "
                "and expires_at > now() returning id",
                id,
                *_owner(owner),
                lifetime,
            )
            if found is None:
                return None
            await conn.execute("delete from cart_lines where cart_id = $1", id)
            await _insert_lines(conn, id, lines)
            return _stored(await conn.fetch(_GET_CART, id, *_owner(owner)))

    async def cancel_cart(
        self,
        owner: Owner,
        id: uuid.UUID | None,
        *,
        key: IdempotencyKey,
        render: Callable[[StoredCart | None], str],
    ) -> str:
        try:
            return await self._cancel_once(owner, id, key, render)
        except _Raced:
            # A concurrent call with this key committed first; its stored response is now the answer.
            return await self._cancel_once(owner, id, key, render)

    async def _cancel_once(
        self,
        owner: Owner,
        id: uuid.UUID | None,
        key: IdempotencyKey,
        render: Callable[[StoredCart | None], str],
    ) -> str:
        async with connection(self._pool, "neon.cancel_cart") as conn, conn.transaction():
            earlier = await conn.fetchrow(_STORED_KEY, key.key, *_owner(owner), key.operation)
            if earlier is not None:
                if earlier["request_hash"] != key.request_hash:
                    raise IdempotencyConflict
                return earlier["response"]
            stored = None
            if id is not None:
                stored = _stored(await conn.fetch(_GET_CART, id, *_owner(owner)))
                if stored is not None:
                    await conn.execute("delete from carts where id = $1", id)
            response = render(stored)  # CPU only: no I/O happens while the transaction is open
            stored_key = await conn.fetchval(
                _STORE_KEY, key.key, *_owner(owner), key.operation, key.request_hash, response
            )
            if stored_key is None:
                raise _Raced
            await conn.execute(_SWEEP_KEYS)
        return response


async def _insert_lines(conn: Connection, cart_id: uuid.UUID, lines: list[NewCartLine]) -> None:
    if lines:
        await conn.execute(
            _INSERT_LINES, cart_id, [line.variant_id for line in lines], [line.quantity for line in lines]
        )
