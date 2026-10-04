"""Choosing Suggestions (#46, ADR-0006): Bundle partners first, then similar Products from other categories.

Over the skincare seed with an in-memory store. The store's similarity comes from a table written here,
so every expected order below follows from that table and the seed's Bundles, not from the code.
"""

from typing import Any

import pytest

from tests.support.catalog import META, FakeSuggestionStore, TimingOutSuggestionStore, seed
from tillhand.models.domain import Bundle, Catalog, category_key
from tillhand.models.ucp import (
    ErrorResponse,
    GetSuggestionsArguments,
    GetSuggestionsRequest,
    SuggestionsResponse,
)
from tillhand.services.suggestions import StoreSuggestionService

pytestmark = pytest.mark.anyio

FLOOR = 0.5

SALICYLIC_CLEANSER = "prod_salicylic_gel_cleanser"
CERAMIDE_CLEANSER = "prod_ceramide_cream_cleanser"
NIACINAMIDE_SERUM = "prod_niacinamide_serum"
HYALURONIC_SERUM = "prod_hyaluronic_serum"
RETINOL_SERUM = "prod_retinol_serum"  # every Variant at stock 0
OIL_FREE_MOISTURISER = "prod_oil_free_gel_moisturiser"
BARRIER_CREAM = "prod_barrier_repair_cream"  # untracked: always available
DISCONTINUED_CREAM = "prod_overnight_recovery_cream"
ROSE_TONER = "prod_rose_toner"
GLYCOLIC_TONER = "prod_glycolic_toner"
SPF50 = "prod_spf50_gel_sunscreen"
MINERAL_SUNSCREEN = "prod_mineral_sunscreen"  # one Variant in stock, one at 0

SKINCARE = seed("skincare")


def with_bundles(*bundles: tuple[str, str, float]) -> Catalog:
    return SKINCARE.model_copy(
        update={"bundles": [Bundle(source=s, target=t, weight=w) for s, t, w in bundles]}
    )


def service(
    catalog: Catalog = SKINCARE, similarity: dict[tuple[str, str], float] | None = None
) -> tuple[StoreSuggestionService, FakeSuggestionStore]:
    store = FakeSuggestionStore(catalog, similarity or {})
    return StoreSuggestionService(store, floor=FLOOR), store


async def suggest(
    svc: StoreSuggestionService, *ids: str, limit: int | None = None, **request: Any
) -> SuggestionsResponse:
    fields: dict[str, Any] = {"product_ids": list(ids), **request}
    if limit is not None:
        fields["limit"] = limit
    result = await svc.get_suggestions(
        GetSuggestionsArguments(meta=META, catalog=GetSuggestionsRequest.model_validate(fields))
    )
    assert isinstance(result, SuggestionsResponse), result
    return result


def picked(result: SuggestionsResponse) -> list[tuple[str, str, str]]:
    return [(s.id, s.reason.kind, s.reason.from_) for s in result.suggestions]


async def test_bundle_partners_come_in_order_of_weight() -> None:
    # The seed: salicylic cleanser -> niacinamide serum (0.9), -> oil-free moisturiser (0.8).
    svc, _ = service()

    result = await suggest(svc, SALICYLIC_CLEANSER, limit=2)

    assert picked(result) == [
        (NIACINAMIDE_SERUM, "bundle", SALICYLIC_CLEANSER),
        (OIL_FREE_MOISTURISER, "bundle", SALICYLIC_CLEANSER),
    ]


async def test_equal_weights_go_to_the_partner_more_sources_point_to() -> None:
    catalog = with_bundles(
        (SALICYLIC_CLEANSER, SPF50, 0.7),
        (SALICYLIC_CLEANSER, BARRIER_CREAM, 0.7),
        (ROSE_TONER, BARRIER_CREAM, 0.7),
    )
    svc, _ = service(catalog)

    result = await suggest(svc, SALICYLIC_CLEANSER, ROSE_TONER, limit=2)

    assert [s.id for s in result.suggestions] == [BARRIER_CREAM, SPF50]


async def test_a_partner_of_several_sources_is_credited_to_the_strongest_pairing() -> None:
    catalog = with_bundles((SALICYLIC_CLEANSER, SPF50, 0.6), (ROSE_TONER, SPF50, 0.9))
    svc, _ = service(catalog)

    result = await suggest(svc, SALICYLIC_CLEANSER, ROSE_TONER, limit=1)

    assert picked(result) == [(SPF50, "bundle", ROSE_TONER)]


async def test_similarity_fills_in_only_when_bundles_run_short() -> None:
    similarity = {(SALICYLIC_CLEANSER, ROSE_TONER): 0.8}
    svc, store = service(similarity=similarity)

    enough = await suggest(svc, SALICYLIC_CLEANSER, limit=2)
    assert store.similar_calls == []
    assert [s.reason.kind for s in enough.suggestions] == ["bundle", "bundle"]

    short = await suggest(svc, SALICYLIC_CLEANSER, limit=3)
    assert picked(short)[2] == (ROSE_TONER, "similar", SALICYLIC_CLEANSER)
    assert len(store.similar_calls) == 1


async def test_similar_products_come_only_from_other_categories() -> None:
    similarity = {
        (SALICYLIC_CLEANSER, CERAMIDE_CLEANSER): 0.95,  # a substitute: another cleanser
        (SALICYLIC_CLEANSER, ROSE_TONER): 0.7,
        (SALICYLIC_CLEANSER, SPF50): 0.6,
    }
    svc, _ = service(with_bundles(), similarity)

    result = await suggest(svc, SALICYLIC_CLEANSER, limit=5)

    assert picked(result) == [
        (ROSE_TONER, "similar", SALICYLIC_CLEANSER),
        (SPF50, "similar", SALICYLIC_CLEANSER),
    ]
    source_categories = {category_key(c) for c in SKINCARE.products[0].categories}
    for suggestion in result.suggestions:
        assert not {category_key(c.value) for c in suggestion.categories or []} & source_categories


async def test_with_several_sources_each_excludes_only_its_own_category() -> None:
    """Owner's decision (#46): the moisturiser may bring a cleanser though a cleanser is also asked about."""
    micellar = "prod_micellar_water"
    similarity = {
        (OIL_FREE_MOISTURISER, SALICYLIC_CLEANSER): 0.6,  # a cleanser, similar to the moisturiser: allowed
        (micellar, CERAMIDE_CLEANSER): 0.9,  # a cleanser, similar to the cleanser: excluded
        (micellar, BARRIER_CREAM): 0.7,  # a moisturiser, similar to the cleanser: allowed
    }
    svc, _ = service(with_bundles(), similarity)

    result = await suggest(svc, OIL_FREE_MOISTURISER, micellar, limit=5)

    assert picked(result) == [
        (BARRIER_CREAM, "similar", micellar),
        (SALICYLIC_CLEANSER, "similar", OIL_FREE_MOISTURISER),
    ]


async def test_a_product_in_one_sources_category_is_credited_to_another_source() -> None:
    similarity = {
        (SALICYLIC_CLEANSER, CERAMIDE_CLEANSER): 0.9,
        (OIL_FREE_MOISTURISER, CERAMIDE_CLEANSER): 0.6,
    }
    svc, _ = service(with_bundles(), similarity)

    result = await suggest(svc, SALICYLIC_CLEANSER, OIL_FREE_MOISTURISER, limit=5)

    assert picked(result) == [(CERAMIDE_CLEANSER, "similar", OIL_FREE_MOISTURISER)]


async def test_similarity_below_the_floor_is_never_suggested() -> None:
    svc, _ = service(with_bundles(), {(SALICYLIC_CLEANSER, ROSE_TONER): FLOOR - 0.01})

    result = await suggest(svc, SALICYLIC_CLEANSER)

    assert result.suggestions == []


async def test_out_of_stock_is_left_out_and_untracked_and_partly_stocked_are_kept() -> None:
    catalog = with_bundles(
        (SALICYLIC_CLEANSER, RETINOL_SERUM, 1.0),
        (SALICYLIC_CLEANSER, BARRIER_CREAM, 0.6),
        (SALICYLIC_CLEANSER, MINERAL_SUNSCREEN, 0.5),
    )
    svc, _ = service(catalog, {(SALICYLIC_CLEANSER, RETINOL_SERUM): 0.99})

    result = await suggest(svc, SALICYLIC_CLEANSER, limit=5)

    assert [s.id for s in result.suggestions] == [BARRIER_CREAM, MINERAL_SUNSCREEN]


async def test_a_discontinued_product_is_never_suggested() -> None:
    catalog = with_bundles((SALICYLIC_CLEANSER, DISCONTINUED_CREAM, 1.0))
    svc, _ = service(catalog, {(SALICYLIC_CLEANSER, DISCONTINUED_CREAM): 0.99})

    result = await suggest(svc, SALICYLIC_CLEANSER, limit=5)

    assert DISCONTINUED_CREAM not in [s.id for s in result.suggestions]


async def test_a_source_is_never_suggested_back() -> None:
    # The seed pairs salicylic cleanser -> niacinamide serum; both are asked about here.
    svc, _ = service(similarity={(NIACINAMIDE_SERUM, SALICYLIC_CLEANSER): 0.9})

    result = await suggest(svc, SALICYLIC_CLEANSER, NIACINAMIDE_SERUM, limit=10)

    assert not {SALICYLIC_CLEANSER, NIACINAMIDE_SERUM} & {s.id for s in result.suggestions}


async def test_an_unknown_or_discontinued_id_is_a_warning_and_the_rest_still_answer() -> None:
    svc, _ = service()

    result = await suggest(svc, "prod_nope", DISCONTINUED_CREAM, SALICYLIC_CLEANSER, limit=1)

    assert picked(result) == [(NIACINAMIDE_SERUM, "bundle", SALICYLIC_CLEANSER)]
    warnings = [(m.type, m.code, m.path) for m in result.messages or []]
    assert warnings == [
        ("warning", "not_found", "$.catalog.product_ids[0]"),
        ("warning", "discontinued", "$.catalog.product_ids[1]"),
    ]


async def test_nothing_qualifying_is_an_empty_list_with_a_message() -> None:
    svc, _ = service(with_bundles())

    result = await suggest(svc, "prod_nope")

    assert result.suggestions == []
    assert [m.code for m in result.messages or []] == ["not_found", "no_suggestions"]


@pytest.mark.parametrize("limit", [1, 2, 4])
async def test_the_limit_is_respected(limit: int) -> None:
    similarity = {(SALICYLIC_CLEANSER, p): 0.9 for p in (ROSE_TONER, GLYCOLIC_TONER, SPF50, BARRIER_CREAM)}
    svc, _ = service(similarity=similarity)

    result = await suggest(svc, SALICYLIC_CLEANSER, limit=limit)

    assert len(result.suggestions) == limit


async def test_the_default_limit_is_three() -> None:
    similarity = {(SALICYLIC_CLEANSER, p): 0.9 for p in (ROSE_TONER, GLYCOLIC_TONER, SPF50, BARRIER_CREAM)}
    svc, _ = service(similarity=similarity)

    result = await suggest(svc, SALICYLIC_CLEANSER)

    assert len(result.suggestions) == 3


async def test_the_same_input_always_gives_the_same_suggestions_ties_broken_by_id() -> None:
    similarity = {(SALICYLIC_CLEANSER, p): 0.8 for p in (SPF50, ROSE_TONER, GLYCOLIC_TONER)}
    svc, _ = service(with_bundles(), similarity)

    first = await suggest(svc, SALICYLIC_CLEANSER, limit=3)
    second = await suggest(svc, SALICYLIC_CLEANSER, limit=3)

    assert first == second
    assert [s.id for s in first.suggestions] == sorted([SPF50, ROSE_TONER, GLYCOLIC_TONER])


async def test_every_suggestion_says_why_and_nothing_is_personalised_yet() -> None:
    svc, _ = service(similarity={(SALICYLIC_CLEANSER, ROSE_TONER): 0.8})

    result = await suggest(svc, SALICYLIC_CLEANSER, limit=3)

    for suggestion in result.suggestions:
        assert suggestion.reason.kind in ("bundle", "similar")
        assert suggestion.reason.from_ == SALICYLIC_CLEANSER
        assert suggestion.reason.personalised == []


async def test_a_cart_is_not_supported_yet() -> None:
    svc, store = service()

    result = await svc.get_suggestions(
        GetSuggestionsArguments(meta=META, catalog=GetSuggestionsRequest(cart_id="cart_123"))
    )

    assert isinstance(result, ErrorResponse)
    assert [m.code for m in result.messages] == ["not_supported"]
    assert store.source_calls == []


async def test_a_slow_database_is_a_named_timeout() -> None:
    svc = StoreSuggestionService(TimingOutSuggestionStore(SKINCARE, {}), floor=FLOOR)

    result = await svc.get_suggestions(
        GetSuggestionsArguments(meta=META, catalog=GetSuggestionsRequest(product_ids=[SALICYLIC_CLEANSER]))
    )

    assert isinstance(result, ErrorResponse)
    assert result.messages[0].code == "upstream_timeout"
    assert "neon.suggestion_sources" in result.messages[0].content
