"""The Merchant door (#41): pre-approved Platforms move into Neon, Merchant API keys, and Customers.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        create table platforms (
            -- A pre-approved Platform (#37), served without fetching its profile. Seeded from
            -- data/platforms.json by scripts/seed_platforms.py.
            profile_url text primary key check (profile_url ~ '^https://'),
            note text not null,
            -- The Platform's UCP profile document, kept verbatim (owner's decision, #41). It's someone else's
            -- document: the app validates it against UCP's schema when it loads it, and nothing queries
            -- inside it.
            profile text not null,
            -- A Merchant assistant's profile, bound to its Merchant API keys: usable only on the Merchant
            -- door, with a key. Set when its first key is issued, and never cleared.
            key_bound boolean not null default false,
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now()
        )
        """
    )

    op.execute(
        """
        create table merchant_api_keys (
            id uuid primary key default gen_random_uuid(),
            -- SHA-256 of the key, hex. The key itself is shown once and never stored (#11 decision 3).
            key_hash text not null unique check (key_hash ~ '^[0-9a-f]{64}$'),
            -- The key's first characters, so a person can tell keys apart in a list.
            prefix text not null check (prefix like 'thk\\_%'),
            label text not null check (label <> ''),
            -- The one pre-registered profile this key acts as.
            profile_url text not null references platforms (profile_url),
            created_at timestamptz not null default now(),
            -- Revoking is immediate: every request looks its key up afresh.
            revoked_at timestamptz
        )
        """
    )

    op.execute(
        """
        create table customers (
            -- Random, never sequential (#11). This id is the Customer half of every Owner.
            id uuid primary key default gen_random_uuid(),
            -- The Merchant's own id for this Customer, sent as TillHand-Customer on the Merchant door.
            -- Null for a Customer known only through identity linking, which adds its own key (#44).
            merchant_customer_id text unique
                check (char_length(merchant_customer_id) between 1 and 255),
            created_at timestamptz not null default now()
        )
        """
    )


def downgrade() -> None:
    op.execute("drop table customers")
    op.execute("drop table merchant_api_keys")
    op.execute("drop table platforms")
