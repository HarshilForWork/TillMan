"""Layer 1 of the isolation harness (#42, #11 decision 9): owned data can't be touched without an Owner.

Checked by `uv run pyright`, never run (pytest doesn't collect it). Each call below forgets the Owner, so
pyright reports it, and the `ignore` comment silences that one report. With
`reportUnnecessaryTypeIgnoreComment` on (pyproject.toml), if any of these calls ever type-checks without an
Owner, its comment becomes unnecessary and pyright fails. Every new owned-data function joins this list.
"""

import uuid
from datetime import timedelta

from tillhand.integrations.neon.carts import NeonCartStore
from tillhand.models.db import IdempotencyKey
from tillhand.services.carts import CartStore


async def _the_neon_store_needs_an_owner(store: NeonCartStore, id: uuid.UUID, key: IdempotencyKey) -> None:
    await store.create_cart(currency="INR", lines=[], lifetime=timedelta(days=7))  # pyright: ignore[reportCallIssue]
    await store.get_cart(id=id)  # pyright: ignore[reportCallIssue]
    await store.replace_cart(id=id, lines=[], lifetime=timedelta(days=7))  # pyright: ignore[reportCallIssue]
    await store.cancel_cart(id=id, key=key, render=str)  # pyright: ignore[reportCallIssue]


async def _so_does_any_cart_store(store: CartStore, id: uuid.UUID, key: IdempotencyKey) -> None:
    await store.create_cart(currency="INR", lines=[], lifetime=timedelta(days=7))  # pyright: ignore[reportCallIssue]
    await store.get_cart(id=id)  # pyright: ignore[reportCallIssue]
    await store.replace_cart(id=id, lines=[], lifetime=timedelta(days=7))  # pyright: ignore[reportCallIssue]
    await store.cancel_cart(id=id, key=key, render=str)  # pyright: ignore[reportCallIssue]
