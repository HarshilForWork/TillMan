"""Carts (#50) and the idempotency keys of writes (#30 decision 11), with their owners (#42).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        create table carts (
            -- Random, never sequential (#11): one id says nothing about another.
            id uuid primary key default gen_random_uuid(),
            -- The Owner (#42): the Platform's profile URL, and the Customer when one is known. A null
            -- Customer is a guest, and matches only a guest (queries use `is not distinct from`).
            owner_platform text not null check (owner_platform <> ''),
            owner_customer text check (owner_customer <> ''),
            currency char(3) not null check (currency ~ '^[A-Z]{3}$'),
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now(),
            -- Pushed out by every change (create, update), never by a read. An expired Cart is simply
            -- not found; nothing deletes it yet.
            expires_at timestamptz not null
        )
        """
    )

    op.execute(
        """
        create table cart_lines (
            cart_id uuid not null references carts (id) on delete cascade,
            position smallint not null check (position >= 0),
            -- No foreign key to variants: a re-seed may delete a Variant, and the Cart then says the
            -- line is no longer sold instead of losing it silently. Prices are never stored here: every
            -- read joins the live catalog.
            variant_id text not null,
            quantity integer not null check (quantity > 0),
            primary key (cart_id, position),
            unique (cart_id, variant_id)
        )
        """
    )

    op.execute(
        """
        create table idempotency_keys (
            -- Internal only, never shown to a caller, so a sequence is fine here.
            id bigint generated always as identity primary key,
            key uuid not null,
            -- The caller: the Owner of #42, in the same two columns as `carts`. A guest is a null Customer.
            owner_platform text not null check (owner_platform <> ''),
            owner_customer text check (owner_customer <> ''),
            operation text not null check (operation in ('cancel_cart')),
            -- A hash of the request minus its meta: the same key with another request is refused.
            request_hash text not null,
            -- The response exactly as first sent, replayed byte for byte on a retry.
            response text not null,
            created_at timestamptz not null default now(),
            -- One key per caller and operation. `nulls not distinct` (Postgres 15+) makes two guests'
            -- null Customers equal here, so a guest's key is as unique as a Customer's.
            unique nulls not distinct (key, owner_platform, owner_customer, operation)
        )
        """
    )
    # Keys live 48 hours. Each write sweeps a bounded batch of older ones, so no clean-up job is needed.
    op.execute("create index idempotency_keys_created_at on idempotency_keys (created_at)")


def downgrade() -> None:
    op.execute("drop table idempotency_keys")
    op.execute("drop table cart_lines")
    op.execute("drop table carts")
