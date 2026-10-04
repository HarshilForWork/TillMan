"""The Suggestion SQL against the real Neon database, seeded with the skincare catalog. Opt-in and read-only:

uv run alembic upgrade head
uv run python scripts/seed_catalog.py data/seeds/skincare.json
TILLHAND_NEON_TESTS=1 uv run pytest -m neon
"""

import os
from collections.abc import AsyncIterator

import pytest

from tests.support.catalog import META, seed
from tillhand.core.config import get_settings
from tillhand.core.constants import SUGGESTION_SIMILARITY_FLOOR
from tillhand.integrations.neon.catalog import NeonCatalogStore
from tillhand.integrations.neon.pool import create_pool
from tillhand.models.domain import Product, category_key, category_prefixes
from tillhand.models.ucp import (
    MAX_SUGGESTIONS,
    GetSuggestionsArguments,
    GetSuggestionsRequest,
    SuggestionsResponse,
)
from tillhand.services.suggestions import StoreSuggestionService

live = pytest.mark.skipif(os.environ.get("TILLHAND_NEON_TESTS") != "1", reason="set TILLHAND_NEON_TESTS=1")
pytestmark = [pytest.mark.anyio, pytest.mark.neon, live]

SKINCARE = seed("skincare")
ACTIVE = [p for p in SKINCARE.products if not p.discontinued]


@pytest.fixture
async def store() -> AsyncIterator[NeonCatalogStore]:
    pool = await create_pool(get_settings(), max_size=2)
    try:
        yield NeonCatalogStore(pool)
    finally:
        await pool.close()


async def test_sources_come_back_with_their_bundles_strongest_first(store: NeonCatalogStore) -> None:
    ids = ["prod_salicylic_gel_cleanser", "prod_overnight_recovery_cream", "prod_nope"]

    sources = {s.id: s for s in await store.suggestion_sources(ids)}

    assert set(sources) == {"prod_salicylic_gel_cleanser", "prod_overnight_recovery_cream"}
    assert sources["prod_overnight_recovery_cream"].status == "discontinued"
    expected = sorted(
        (b for b in SKINCARE.bundles if b.source == "prod_salicylic_gel_cleanser"), key=lambda b: -b.weight
    )
    bundles = sources["prod_salicylic_gel_cleanser"].bundles
    assert [b.product.id for b in bundles] == [b.target for b in expected]
    assert [b.product for b in bundles] == [
        next(p for p in SKINCARE.products if p.id == b.target) for b in expected
    ]


@pytest.mark.parametrize("source", ACTIVE, ids=lambda p: p.id)
async def test_similar_products_follow_the_service_rules(store: NeonCatalogStore, source: Product) -> None:
    keys = sorted({category_key(c) for c in source.categories})

    near = await store.similar_products(
        sources={source.id: keys}, exclude_ids=[], floor=-1.0, limit=MAX_SUGGESTIONS
    )

    assert near, "every seeded Product has cross-category neighbours"
    assert [n.similarity for n in near] == sorted((n.similarity for n in near), reverse=True)
    for n in near:
        assert n.source_id == source.id
        assert n.product.id != source.id
        assert not n.product.discontinued
        assert any(n.product.is_available(v) for v in n.product.variants)
        assert not set(category_prefixes(n.product.categories)) & set(keys)


async def test_each_source_excludes_only_its_own_category_in_sql(store: NeonCatalogStore) -> None:
    moisturiser, micellar = "prod_oil_free_gel_moisturiser", "prod_micellar_water"
    cleanser_key = category_key("Skincare > Cleanser")

    near = await store.similar_products(
        sources={moisturiser: [category_key("Skincare > Moisturiser")], micellar: [cleanser_key]},
        exclude_ids=[],
        floor=-1.0,
        limit=MAX_SUGGESTIONS,
    )

    def cleansers_near(source: str) -> list[str]:
        return [
            n.product.id
            for n in near
            if n.source_id == source and cleanser_key in category_prefixes(n.product.categories)
        ]

    assert "prod_salicylic_gel_cleanser" in cleansers_near(moisturiser)
    assert cleansers_near(micellar) == []


async def test_the_floor_and_exclusions_are_applied_in_sql(store: NeonCatalogStore) -> None:
    source = "prod_oil_free_gel_moisturiser"
    sources = {source: [category_key("Skincare > Moisturiser")]}
    everything = await store.similar_products(
        sources=sources, exclude_ids=[], floor=-1.0, limit=MAX_SUGGESTIONS
    )
    nearest = everything[0].product.id

    floored = await store.similar_products(
        sources=sources, exclude_ids=[nearest], floor=0.45, limit=MAX_SUGGESTIONS
    )

    assert nearest not in [n.product.id for n in floored]
    assert all(n.similarity >= 0.45 for n in floored)


@pytest.mark.parametrize("source", ACTIVE, ids=lambda p: p.id)
async def test_every_products_fallback_suggestions_come_from_another_category(
    store: NeonCatalogStore, source: Product
) -> None:
    service = StoreSuggestionService(store, floor=SUGGESTION_SIMILARITY_FLOOR)
    arguments = GetSuggestionsArguments(
        meta=META, catalog=GetSuggestionsRequest(product_ids=[source.id], limit=MAX_SUGGESTIONS)
    )

    first = await service.get_suggestions(arguments)
    second = await service.get_suggestions(arguments)

    assert isinstance(first, SuggestionsResponse)
    assert first == second
    own = {category_key(c) for c in source.categories}
    for suggestion in first.suggestions:
        if suggestion.reason.kind == "similar":
            assert not {category_key(c.value) for c in suggestion.categories or []} & own
