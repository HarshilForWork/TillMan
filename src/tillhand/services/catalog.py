"""The catalog service: what the UCP catalog tools call. Implemented over Neon by the catalog data layer."""

from collections.abc import Sequence
from typing import Protocol

from tillhand.core.constants import CATALOG_LOOKUP, CATALOG_SEARCH, UCP_VERSION
from tillhand.core.deadlines import StepTimeout
from tillhand.core.errors import RequestTooLarge, business_error, timeout_error
from tillhand.integrations.pinecone import EmbeddingError
from tillhand.models.db import ResolvedProduct
from tillhand.models.domain import Product, Variant, category_key, category_prefixes
from tillhand.models.ucp import (
    CapabilityEntry,
    ErrorResponse,
    GetProductArguments,
    GetProductResponse,
    InputCorrelation,
    LookupCatalogArguments,
    LookupProduct,
    LookupResponse,
    MessageInfo,
    PaginationResponse,
    SearchCatalogArguments,
    SearchFilters,
    SearchResponse,
    SelectedOption,
    UcpResponseMeta,
)
from tillhand.services import catalog_mapper
from tillhand.utils.cursor import decode_offset, encode_offset

DEFAULT_PAGE_SIZE = 10
"""UCP's recommended default."""

MAX_PAGE_SIZE = 50
MAX_OFFSET = 10_000
"""Deeper than any catalog we serve; a cursor past it was not one we issued."""

MAX_LOOKUP_IDS = 50
"""UCP asks for at least 10; over this, `lookup_catalog` is refused as invalid params."""


class CatalogService(Protocol):
    """The three UCP catalog tools, by their UCP names.

    What counts as a "no" differs per tool (catalog/mcp.md):
    - `search_catalog`: nothing matching is an empty `products` list, not an error.
    - `lookup_catalog` MUST succeed for unknown ids: they just produce fewer products, optionally
      with an informational `not_found` message.
    - `get_product`: an unknown id is an `ErrorResponse` (`not_found`).

    An `ErrorResponse` is also how any tool reports a step that ran out of time (`timeout_error`).
    """

    async def search_catalog(self, arguments: SearchCatalogArguments) -> SearchResponse | ErrorResponse: ...

    async def lookup_catalog(self, arguments: LookupCatalogArguments) -> LookupResponse | ErrorResponse: ...

    async def get_product(self, arguments: GetProductArguments) -> GetProductResponse | ErrorResponse: ...


class CatalogStore(Protocol):
    """The catalog queries (`integrations.neon.catalog.NeonCatalogStore`; a fake in tests)."""

    async def search_products(
        self,
        *,
        query_vector: list[float] | None,
        category_keys: list[str] | None,
        price_min: int | None,
        price_max: int | None,
        limit: int,
        offset: int,
    ) -> list[Product]: ...

    async def lookup_products(self, ids: list[str]) -> list[ResolvedProduct]: ...

    async def get_product(self, id: str) -> Product | None: ...


class QueryEmbedder(Protocol):
    async def embed_query(self, text: str) -> list[float]: ...


class _Filters:
    """The core UCP filters, normalised once: category keys for the product, a price range for Variants."""

    def __init__(self, filters: SearchFilters | None) -> None:
        categories = [c for c in (filters.categories or []) if c.strip()] if filters else []
        self.category_keys = sorted({category_key(c) for c in categories}) or None
        price = filters.price if filters else None
        self.price_min = price.min if price else None
        self.price_max = price.max if price else None

    @property
    def empty(self) -> bool:
        return self.category_keys is None and self.price_min is None and self.price_max is None

    def admits_product(self, product: Product) -> bool:
        return self.category_keys is None or not set(self.category_keys).isdisjoint(
            category_prefixes(product.categories)
        )

    def admits_variant(self, variant: Variant) -> bool:
        return (self.price_min is None or variant.price >= self.price_min) and (
            self.price_max is None or variant.price <= self.price_max
        )


def _meta(capability: str) -> UcpResponseMeta:
    return UcpResponseMeta(
        version=UCP_VERSION, capabilities={capability: [CapabilityEntry(version=UCP_VERSION)]}
    )


def _featured_first(product: Product, variants: Sequence[Variant]) -> list[Variant]:
    """Available Variants first, each group in the Merchant's order, so the first one is the featured one."""
    return sorted(variants, key=lambda v: not product.is_available(v))


def _matching(product: Product, selection: dict[str, str]) -> list[Variant]:
    return [
        v for v in product.variants if all(v.options.get(name) == label for name, label in selection.items())
    ]


def relax_selection(
    product: Product, selected: Sequence[SelectedOption], preferences: Sequence[str] | None
) -> dict[str, str]:
    """The effective selection: the request's, minus whatever must go for some Variant to match.

    Selections of Options the Product doesn't have are ignored. Relaxation drops selections missing
    from `preferences` first (latest-selected first), then `preferences` from the end, as UCP says.
    """
    names = {option.name for option in product.options}
    effective = {s.name: s.label for s in selected if s.name in names}
    ranked = [name for name in (preferences or []) if name in effective]
    drop_order = [name for name in reversed(effective) if name not in ranked] + ranked[::-1]
    while effective and not _matching(product, effective):
        del effective[drop_order.pop(0)]
    return effective


class StoreCatalogService:
    """`CatalogService` over a `CatalogStore`, with query embedding done before any DB work."""

    def __init__(self, store: CatalogStore, embedder: QueryEmbedder) -> None:
        self._store = store
        self._embedder = embedder

    async def search_catalog(self, arguments: SearchCatalogArguments) -> SearchResponse | ErrorResponse:
        request = arguments.catalog
        query = (request.query or "").strip() or None
        filters = _Filters(request.filters)
        if query is None and filters.empty:
            return business_error(
                code="invalid_request",
                content="A search needs a query, a categories filter or a price filter.",
                severity="recoverable",
            )
        pagination = request.pagination
        limit = min(pagination.limit or DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE) if pagination else DEFAULT_PAGE_SIZE
        offset = 0
        if pagination and pagination.cursor is not None:
            decoded = decode_offset(pagination.cursor)
            if decoded is None or decoded > MAX_OFFSET:
                return business_error(
                    code="invalid_cursor",
                    content="That cursor was not issued by this search. Start again without one.",
                    severity="recoverable",
                    path="$.catalog.pagination.cursor",
                )
            offset = decoded
        try:
            # Embed first: the DB connection is only taken once the slow network call is done.
            vector = None if query is None else await self._embedder.embed_query(query)
            found = await self._store.search_products(
                query_vector=vector,
                category_keys=filters.category_keys,
                price_min=filters.price_min,
                price_max=filters.price_max,
                limit=limit + 1,
                offset=offset,
            )
        except StepTimeout as exc:
            return timeout_error(exc.step)
        except EmbeddingError:
            return business_error(
                code="search_unavailable",
                content="Search is unavailable right now: the query could not be processed. "
                "Try again, or browse with a categories filter.",
                severity="recoverable",
            )
        page = found[:limit]
        has_next = len(found) > limit
        products = []
        for product in page:
            variants = [v for v in product.variants if filters.admits_variant(v)]
            if variants:
                products.append(catalog_mapper.search_product(product, _featured_first(product, variants)))
        return SearchResponse(
            ucp=_meta(CATALOG_SEARCH),
            products=products,
            pagination=PaginationResponse(
                has_next_page=has_next, cursor=encode_offset(offset + limit) if has_next else None
            ),
        )

    async def lookup_catalog(self, arguments: LookupCatalogArguments) -> LookupResponse | ErrorResponse:
        request = arguments.catalog
        ids = list(dict.fromkeys(request.ids))  # UCP: duplicates MUST be deduplicated
        if len(ids) > MAX_LOOKUP_IDS:
            raise RequestTooLarge(f"lookup_catalog accepts at most {MAX_LOOKUP_IDS} ids, got {len(ids)}")
        try:
            resolved = await self._store.lookup_products(ids)
        except StepTimeout as exc:
            return timeout_error(exc.step)
        filters = _Filters(request.filters)
        found: set[str] = set()
        products: list[LookupProduct] = []
        for item in resolved:
            found.update(match.input for match in item.matches)
            if product := _lookup_product(item, filters):
                products.append(product)
        not_found = [id for id in ids if id not in found]
        return LookupResponse(
            ucp=_meta(CATALOG_LOOKUP),
            products=products,
            messages=[MessageInfo(code="not_found", content=id) for id in not_found] or None,
        )

    async def get_product(self, arguments: GetProductArguments) -> GetProductResponse | ErrorResponse:
        request = arguments.catalog
        try:
            product = await self._store.get_product(request.id)
        except StepTimeout as exc:
            return timeout_error(exc.step)
        if product is None:
            return _product_not_found(request.id)
        filters = _Filters(request.filters)
        requested = next((v for v in product.variants if v.id == request.id), None)
        if requested is not None:
            # A Variant id fully determines the selection; `selected` is ignored (UCP).
            selection = dict(requested.options)
            variants = [requested]
        elif request.selected:
            selection = relax_selection(product, request.selected, request.preferences)
            variants = _featured_first(product, _matching(product, selection))
        else:
            featured = product.featured_variant()
            selection = dict(featured.options)
            variants = [featured]
        variants = [v for v in variants if filters.admits_variant(v)]
        if not variants or not filters.admits_product(product):
            return _product_not_found(request.id, reason="nothing matches the filters")
        return GetProductResponse(
            ucp=_meta(CATALOG_LOOKUP),
            product=catalog_mapper.detail_product(product, variants, selection),
        )


def _lookup_product(item: ResolvedProduct, filters: _Filters) -> LookupProduct | None:
    """A resolved Product with one Variant per identifier: the named one, or the featured one.

    Filters apply after resolution: a Variant outside the price filter is left out, and a Product
    left with none is excluded (catalog/lookup.md).
    """
    product = item.product
    if not filters.admits_product(product):
        return None
    admitted = [v for v in product.variants if filters.admits_variant(v)]
    if not admitted:
        return None
    featured = _featured_first(product, admitted)[0]
    inputs: dict[str, list[InputCorrelation]] = {}
    for match in item.matches:
        if match.variant_id is None:
            inputs.setdefault(featured.id, []).append(InputCorrelation(id=match.input, match="featured"))
        elif any(v.id == match.variant_id for v in admitted):
            inputs.setdefault(match.variant_id, []).append(InputCorrelation(id=match.input, match="exact"))
    return catalog_mapper.lookup_product(product, inputs) if inputs else None


def _product_not_found(id: str, *, reason: str | None = None) -> ErrorResponse:
    detail = f" ({reason})" if reason else ""
    # `unrecoverable`: UCP says agents MUST NOT retry the same id.
    return business_error(
        code="not_found", content=f"Product not found: {id}{detail}", severity="unrecoverable"
    )
