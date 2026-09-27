"""All catalog SQL. Every method takes a connection, runs its statements, and gives it back.

Each Product comes back as one JSON document built in SQL (its Options and Variants included), parsed
straight into the domain `Product`. That keeps a page of results to one round trip, which matters at
~85 ms per round trip from India, and it is still bounded: the outer query has a LIMIT, and the
per-Product subqueries hit the `variants (product_id, position)` and `product_options` primary-key indexes.

Nothing here embeds text or calls out; callers embed first, then call in (never hold a connection
across an embedding call).
"""

from pydantic import TypeAdapter

from tillhand.core.deadlines import NEON_QUERY_SECONDS, NEON_SYNC_SECONDS, deadline
from tillhand.integrations.neon.pool import Pool
from tillhand.models.db import CatalogSyncResult, EmbeddingState, IdMatch, ProductEmbedding, ResolvedProduct
from tillhand.models.domain import Catalog, Product, category_prefixes

_PRODUCT_JSON = """
jsonb_build_object(
    'id', p.id,
    'handle', p.handle,
    'title', p.title,
    'description', p.description,
    'status', p.status,
    'currency', p.currency,
    'categories', p.categories,
    'tags', p.tags,
    'options', coalesce((
        select jsonb_agg(jsonb_build_object('name', o.name, 'values', o."values") order by o.position)
        from product_options o where o.product_id = p.id
    ), '[]'::jsonb),
    'variants', (
        select jsonb_agg(jsonb_build_object(
            'id', v.id,
            'sku', v.sku,
            'options', coalesce((
                select jsonb_object_agg(o.name, v.option_values[o.position + 1])
                from product_options o where o.product_id = p.id
            ), '{}'::jsonb),
            'price', v.price,
            'list_price', v.list_price,
            'stock', v.stock
        ) order by v.position)
        from variants v where v.product_id = p.id
    )
)::text
"""

# $1 category keys (null: no filter), $2/$3 price bounds in minor units (null: unbounded).
# Discontinued Products are never searched (#8). A price filter keeps a Product when any Variant is
# in range; dropping the out-of-range Variants themselves is the service's job.
_SEARCH_WHERE = """
where p.status = 'active'
  and ($1::text[] is null or p.category_prefixes && $1::text[])
  and (($2::bigint is null and $3::bigint is null) or exists (
      select 1 from variants v
      where v.product_id = p.id
        and ($2::bigint is null or v.price >= $2::bigint)
        and ($3::bigint is null or v.price <= $3::bigint)
  ))
"""

_SEARCH_BY_SIMILARITY = f"""
select {_PRODUCT_JSON} as product
from products p
{_SEARCH_WHERE}
order by p.embedding <=> $4::text::vector
limit $5 offset $6
"""

_BROWSE = f"""
select {_PRODUCT_JSON} as product
from products p
{_SEARCH_WHERE}
order by p.position
limit $4 offset $5
"""

# Lookup resolves product ids and handles to the Product, and variant ids and SKUs to the Variant.
_LOOKUP = f"""
with wanted as (select distinct unnest($1::text[]) as input),
matches as (
    select w.input, p.id as product_id, null::text as variant_id
    from wanted w join products p on p.id = w.input or p.handle = w.input
    union all
    select w.input, v.product_id, v.id
    from wanted w join variants v on v.id = w.input or v.sku = w.input
)
select {_PRODUCT_JSON} as product,
       jsonb_agg(jsonb_build_object('input', m.input, 'variant_id', m.variant_id) order by m.input)::text
           as matches
from matches m join products p on p.id = m.product_id
group by p.id
"""

_GET = f"""
select {_PRODUCT_JSON} as product
from products p
where p.id = $1 or p.id = (select v.product_id from variants v where v.id = $1)
"""

_MATCHES = TypeAdapter(list[IdMatch])


def vector_literal(vector: list[float]) -> str:
    """pgvector's text form. Sent as text and cast in SQL, so asyncpg needs no codec for `vector`."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


class NeonCatalogStore:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def search_products(
        self,
        *,
        query_vector: list[float] | None,
        category_keys: list[str] | None,
        price_min: int | None,
        price_max: int | None,
        limit: int,
        offset: int,
    ) -> list[Product]:
        """Active Products passing the filters: nearest first given a query vector, else in catalog order."""
        filters = (category_keys, price_min, price_max)
        async with deadline("neon.search_products", NEON_QUERY_SECONDS), self._pool.acquire() as conn:
            if query_vector is None:
                rows = await conn.fetch(_BROWSE, *filters, limit, offset)
            else:
                rows = await conn.fetch(
                    _SEARCH_BY_SIMILARITY, *filters, vector_literal(query_vector), limit, offset
                )
        return [Product.model_validate_json(row["product"]) for row in rows]

    async def lookup_products(self, ids: list[str]) -> list[ResolvedProduct]:
        """Products for any of `ids` (product id, handle, variant id or SKU); unknown ids are just absent."""
        async with deadline("neon.lookup_products", NEON_QUERY_SECONDS), self._pool.acquire() as conn:
            rows = await conn.fetch(_LOOKUP, ids)
        return [
            ResolvedProduct(
                product=Product.model_validate_json(row["product"]),
                matches=_MATCHES.validate_json(row["matches"]),
            )
            for row in rows
        ]

    async def get_product(self, id: str) -> Product | None:
        """The Product with this id, or owning the Variant with this id. Discontinued ones included."""
        async with deadline("neon.get_product", NEON_QUERY_SECONDS), self._pool.acquire() as conn:
            row = await conn.fetchrow(_GET, id)
        return None if row is None else Product.model_validate_json(row["product"])

    async def sync_catalog(self, catalog: Catalog) -> CatalogSyncResult:
        """Make the database match a catalog file, leaving embeddings of unchanged Products in place.

        One transaction, on purpose: a half-applied catalog (Variants without their Options, Bundles
        pointing at nothing) must never be visible. It holds only DB statements, batched with
        `executemany`, and runs from the seed script, not in the request path.
        """
        ids = [p.id for p in catalog.products]
        # The pool's command_timeout is sized for request-path queries; each batch here gets the
        # sync's own budget instead, or the outer deadline could never be reached.
        t = NEON_SYNC_SECONDS
        async with deadline("neon.sync_catalog", NEON_SYNC_SECONDS), self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(
                    """
                    insert into products (id, handle, title, description, status, currency, categories,
                                          category_prefixes, tags, position)
                    values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    on conflict (id) do update set
                        handle = excluded.handle, title = excluded.title,
                        description = excluded.description, status = excluded.status,
                        currency = excluded.currency, categories = excluded.categories,
                        category_prefixes = excluded.category_prefixes, tags = excluded.tags,
                        position = excluded.position, updated_at = now()
                    """,
                    [
                        (
                            p.id,
                            p.handle,
                            p.title,
                            p.description,
                            p.status,
                            p.currency,
                            p.categories,
                            category_prefixes(p.categories),
                            p.tags,
                            position,
                        )
                        for position, p in enumerate(catalog.products)
                    ],
                    timeout=t,
                )
                missing = await conn.fetch(
                    """
                    update products set status = 'discontinued', updated_at = now()
                    where not (id = any($1::text[])) and status <> 'discontinued'
                    returning id
                    """,
                    ids,
                    timeout=t,
                )
                await conn.execute(
                    "delete from product_options where product_id = any($1::text[])", ids, timeout=t
                )
                await conn.executemany(
                    'insert into product_options (product_id, position, name, "values") '
                    "values ($1, $2, $3, $4)",
                    [
                        (p.id, position, option.name, option.values)
                        for p in catalog.products
                        for position, option in enumerate(p.options)
                    ],
                    timeout=t,
                )
                variant_ids = [v.id for p in catalog.products for v in p.variants]
                await conn.execute(
                    "delete from variants where product_id = any($1::text[]) and not (id = any($2::text[]))",
                    ids,
                    variant_ids,
                    timeout=t,
                )
                await conn.executemany(
                    """
                    insert into variants (id, product_id, position, sku, option_values, price,
                                          list_price, stock)
                    values ($1, $2, $3, $4, $5, $6, $7, $8)
                    on conflict (id) do update set
                        product_id = excluded.product_id, position = excluded.position,
                        sku = excluded.sku, option_values = excluded.option_values,
                        price = excluded.price, list_price = excluded.list_price, stock = excluded.stock
                    """,
                    [
                        (v.id, p.id, position, v.sku, p.option_values(v), v.price, v.list_price, v.stock)
                        for p in catalog.products
                        for position, v in enumerate(p.variants)
                    ],
                    timeout=t,
                )
                await conn.execute("delete from bundles", timeout=t)
                await conn.executemany(
                    "insert into bundles (source_id, target_id, weight) values ($1, $2, $3)",
                    [(b.source, b.target, b.weight) for b in catalog.bundles],
                    timeout=t,
                )
        return CatalogSyncResult(
            products=len(catalog.products),
            variants=len(variant_ids),
            bundles=len(catalog.bundles),
            discontinued_missing=sorted(row["id"] for row in missing),
        )

    async def embedding_states(self) -> list[EmbeddingState]:
        async with deadline("neon.embedding_states", NEON_QUERY_SECONDS), self._pool.acquire() as conn:
            rows = await conn.fetch("select id, embedding_source from products order by position")
        return [EmbeddingState(id=row["id"], embedding_source=row["embedding_source"]) for row in rows]

    async def store_embeddings(self, embeddings: list[ProductEmbedding]) -> None:
        async with deadline("neon.store_embeddings", NEON_QUERY_SECONDS), self._pool.acquire() as conn:
            await conn.executemany(
                "update products set embedding = $2::text::vector, embedding_source = $3 where id = $1",
                [(e.id, vector_literal(e.vector), e.source) for e in embeddings],
            )
