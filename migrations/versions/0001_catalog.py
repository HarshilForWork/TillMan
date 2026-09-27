"""The catalog (#8, #12): Products, their Options and Variants, Bundles, and one embedding per Product.

Revision ID: 0001
Revises:
Create Date: 2026-09-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("create extension if not exists vector")

    op.execute(
        """
        create table products (
            id text primary key,
            handle text not null unique,
            title text not null,
            description text not null,
            status text not null default 'active' check (status in ('active', 'discontinued')),
            currency char(3) not null check (currency ~ '^[A-Z]{3}$'),
            categories text[] not null check (cardinality(categories) > 0),
            -- Every whole-segment prefix of every category path, lower-cased: 'Skincare > Serum' gives
            -- 'skincare' and 'skincare > serum'. The categories filter is an overlap (&&) on this.
            category_prefixes text[] not null,
            tags text[] not null default '{}',
            -- The Merchant's catalog order, used when browsing without a query.
            position integer not null,
            -- llama-text-embed-v2 at 1024 dimensions (#27), input_type 'passage'.
            embedding vector(1024),
            -- A hash of the model and the embedded text; the seed script re-embeds when it changes.
            embedding_source text,
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now()
        )
        """
    )
    op.execute("create index products_category_prefixes on products using gin (category_prefixes)")
    op.execute("create index products_embedding on products using hnsw (embedding vector_cosine_ops)")
    op.execute("create index products_active_position on products (position) where status = 'active'")

    op.execute(
        """
        create table product_options (
            product_id text not null references products (id) on delete cascade,
            position smallint not null,
            name text not null,
            "values" text[] not null check (cardinality("values") > 0),
            primary key (product_id, position),
            unique (product_id, name)
        )
        """
    )

    op.execute(
        """
        create table variants (
            id text primary key,
            product_id text not null references products (id) on delete cascade,
            position smallint not null,
            sku text unique,
            -- One value per Option, in product_options.position order.
            option_values text[] not null default '{}',
            price bigint not null check (price >= 0),
            list_price bigint check (list_price >= price),
            -- Null means untracked: always available.
            stock integer check (stock >= 0),
            -- Deferred, so a re-seed can reorder a Product's Variants inside one transaction.
            unique (product_id, position) deferrable initially deferred,
            unique (product_id, option_values) deferrable initially deferred
        )
        """
    )

    op.execute(
        """
        create table bundles (
            source_id text not null references products (id) on delete cascade,
            target_id text not null references products (id) on delete cascade,
            weight real not null check (weight > 0 and weight <= 1),
            primary key (source_id, target_id),
            check (source_id <> target_id)
        )
        """
    )

    # A filtered HNSW scan can return fewer rows than LIMIT asks for; iterative scans (pgvector 0.8)
    # keep scanning until enough rows pass the filter. Set on the database, because a session-level
    # SET doesn't survive Neon's transaction pooler.
    op.execute(
        """
        do $$ begin
            execute format('alter database %I set hnsw.iterative_scan = strict_order', current_database());
        end $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        do $$ begin
            execute format('alter database %I reset hnsw.iterative_scan', current_database());
        end $$
        """
    )
    op.execute("drop table bundles")
    op.execute("drop table variants")
    op.execute("drop table product_options")
    op.execute("drop table products")
