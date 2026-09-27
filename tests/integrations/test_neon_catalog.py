"""The catalog SQL against the real Neon database, seeded with the skincare catalog. Opt-in and read-only:

uv run alembic upgrade head
uv run python scripts/seed_catalog.py data/seeds/skincare.json
TILLHAND_NEON_TESTS=1 uv run pytest -m neon
"""

import os
from collections.abc import AsyncIterator

import pytest

from tests.support.catalog import META, FakeEmbedder, seed
from tests.support.ucp_spec import schema_errors
from tillhand.core.config import get_settings
from tillhand.integrations.neon.catalog import NeonCatalogStore, vector_literal
from tillhand.integrations.neon.pool import create_pool
from tillhand.models.domain import category_key
from tillhand.models.ucp import (
    GetProductArguments,
    GetProductRequest,
    GetProductResponse,
    LookupCatalogArguments,
    LookupRequest,
    LookupResponse,
    SearchCatalogArguments,
    SearchFilters,
    SearchRequest,
    SearchResponse,
    ucp_dump,
)
from tillhand.services.catalog import StoreCatalogService

live = pytest.mark.skipif(os.environ.get("TILLHAND_NEON_TESTS") != "1", reason="set TILLHAND_NEON_TESTS=1")
pytestmark = [pytest.mark.anyio, pytest.mark.neon, live]


@pytest.fixture
async def store() -> AsyncIterator[NeonCatalogStore]:
    pool = await create_pool(get_settings(), max_size=2)
    try:
        yield NeonCatalogStore(pool)
    finally:
        await pool.close()


async def test_the_database_holds_the_skincare_seed_as_written(store: NeonCatalogStore) -> None:
    for expected in seed("skincare").products:
        assert await store.get_product(expected.id) == expected


async def test_browsing_filters_by_category_prefix_and_skips_discontinued(store: NeonCatalogStore) -> None:
    moisturisers = await store.search_products(
        query_vector=None,
        category_keys=[category_key("skincare > moisturiser")],
        price_min=None,
        price_max=None,
        limit=10,
        offset=0,
    )
    assert [p.id for p in moisturisers] == ["prod_oil_free_gel_moisturiser", "prod_barrier_repair_cream"]


async def test_a_price_filter_keeps_products_with_any_variant_in_range(store: NeonCatalogStore) -> None:
    found = await store.search_products(
        query_vector=None, category_keys=None, price_min=100000, price_max=None, limit=10, offset=0
    )
    assert [p.id for p in found] == ["prod_retinol_serum"]


async def test_similarity_search_runs_with_a_query_vector(store: NeonCatalogStore) -> None:
    found = await store.search_products(
        query_vector=[0.01] * 1024,
        category_keys=[category_key("Skincare > Sunscreen")],
        price_min=None,
        price_max=None,
        limit=10,
        offset=0,
    )
    assert {p.id for p in found} == {"prod_spf50_gel_sunscreen", "prod_mineral_sunscreen"}


async def test_lookup_resolves_handles_skus_and_variant_ids(store: NeonCatalogStore) -> None:
    resolved = await store.lookup_products(
        ["rose-hydrating-toner", "FW-SER-NIA-50", "var_rose_toner_200", "nope"]
    )
    matches = {p.product.id: sorted((m.input, m.variant_id) for m in p.matches) for p in resolved}
    assert matches == {
        "prod_rose_toner": [("rose-hydrating-toner", None), ("var_rose_toner_200", "var_rose_toner_200")],
        "prod_niacinamide_serum": [("FW-SER-NIA-50", "var_niacinamide_serum_50")],
    }


async def test_the_three_tools_answer_valid_ucp_over_the_real_database(store: NeonCatalogStore) -> None:
    service = StoreCatalogService(store, FakeEmbedder())
    search = await service.search_catalog(
        SearchCatalogArguments(
            meta=META, catalog=SearchRequest(filters=SearchFilters(categories=["Skincare"]))
        )
    )
    assert isinstance(search, SearchResponse) and len(search.products) == 10
    assert schema_errors(ucp_dump(search), "shopping/catalog_search", "search_response") == []

    lookup = await service.lookup_catalog(
        LookupCatalogArguments(meta=META, catalog=LookupRequest(ids=["prod_overnight_recovery_cream"]))
    )
    assert isinstance(lookup, LookupResponse)
    assert schema_errors(ucp_dump(lookup), "shopping/catalog_lookup", "lookup_response") == []

    detail = await service.get_product(
        GetProductArguments(meta=META, catalog=GetProductRequest(id="var_vitamin_c_serum_30"))
    )
    assert isinstance(detail, GetProductResponse)
    assert [v.id for v in detail.product.variants] == ["var_vitamin_c_serum_30"]
    assert schema_errors(ucp_dump(detail), "shopping/catalog_lookup", "get_product_response") == []


def test_vectors_are_sent_in_pgvector_text_form() -> None:
    assert vector_literal([1, 0.5, -2.25]) == "[1.0,0.5,-2.25]"
