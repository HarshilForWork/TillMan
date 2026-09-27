"""The catalog service against in-memory fakes: UCP semantics, availability, and failure outcomes.

Every successful response is also validated against the vendored UCP schemas.
"""

from typing import Any

import pytest

from tests.support.catalog import (
    EMBED_REFUSED,
    EMBED_TIMEOUT,
    META,
    FakeCatalogStore,
    FakeEmbedder,
    TimingOutStore,
    seed,
)
from tests.support.ucp_spec import schema_errors
from tillhand.core.errors import RequestTooLarge
from tillhand.models.ucp import (
    DetailProduct,
    ErrorResponse,
    GetProductArguments,
    GetProductRequest,
    GetProductResponse,
    LookupCatalogArguments,
    LookupRequest,
    LookupResponse,
    PaginationRequest,
    PriceFilter,
    SearchCatalogArguments,
    SearchFilters,
    SearchRequest,
    SearchResponse,
    SelectedOption,
    ucp_dump,
)
from tillhand.services.catalog import MAX_LOOKUP_IDS, MAX_OFFSET, CatalogService, StoreCatalogService
from tillhand.utils.cursor import encode_offset

pytestmark = pytest.mark.anyio


def service(catalog: str = "skincare", *, embedder: FakeEmbedder | None = None) -> StoreCatalogService:
    return StoreCatalogService(FakeCatalogStore(seed(catalog)), embedder or FakeEmbedder())


async def search(svc: CatalogService, **request: Any) -> SearchResponse:
    result = await svc.search_catalog(SearchCatalogArguments(meta=META, catalog=SearchRequest(**request)))
    assert isinstance(result, SearchResponse), result
    assert schema_errors(ucp_dump(result), "shopping/catalog_search", "search_response") == []
    return result


async def lookup(svc: CatalogService, *ids: str, **request: Any) -> LookupResponse:
    result = await svc.lookup_catalog(
        LookupCatalogArguments(meta=META, catalog=LookupRequest(ids=list(ids), **request))
    )
    assert isinstance(result, LookupResponse), result
    assert schema_errors(ucp_dump(result), "shopping/catalog_lookup", "lookup_response") == []
    return result


async def get(svc: CatalogService, id: str, **request: Any) -> DetailProduct:
    result = await svc.get_product(
        GetProductArguments(meta=META, catalog=GetProductRequest(id=id, **request))
    )
    assert isinstance(result, GetProductResponse), result
    assert schema_errors(ucp_dump(result), "shopping/catalog_lookup", "get_product_response") == []
    return result.product


def error_codes(result: object) -> list[str]:
    assert isinstance(result, ErrorResponse), result
    assert schema_errors(ucp_dump(result), "common/types/error_response") == []
    return [m.code or "" for m in result.messages]


# -- search_catalog --------------------------------------------------------------------------------


async def test_the_query_is_embedded_before_the_store_is_asked() -> None:
    store, embedder = FakeCatalogStore(seed("skincare")), FakeEmbedder()
    await search(StoreCatalogService(store, embedder), query="  serum for oily skin ")
    assert embedder.queries == ["serum for oily skin"]
    assert store.search_calls[0]["query_vector"] is not None


async def test_browsing_by_category_needs_no_embedding() -> None:
    embedder = FakeEmbedder()
    result = await search(service(embedder=embedder), filters=SearchFilters(categories=["skincare > serum"]))
    assert embedder.queries == []
    assert {p.id for p in result.products} == {
        "prod_niacinamide_serum",
        "prod_vitamin_c_serum",
        "prod_hyaluronic_serum",
        "prod_retinol_serum",
    }


async def test_a_search_with_no_query_and_no_filters_is_refused() -> None:
    result = await service().search_catalog(
        SearchCatalogArguments(meta=META, catalog=SearchRequest(query="  "))
    )
    assert error_codes(result) == ["invalid_request"]


async def test_discontinued_products_are_never_searched() -> None:
    result = await search(
        service(), filters=SearchFilters(categories=["Skincare"]), pagination=PaginationRequest(limit=50)
    )
    assert "prod_overnight_recovery_cream" not in {p.id for p in result.products}


async def test_unavailable_variants_are_returned_marked_and_after_the_available_ones() -> None:
    result = await search(service(), filters=SearchFilters(categories=["Skincare > Serum"]))
    vitamin_c = next(p for p in result.products if p.id == "prod_vitamin_c_serum")
    assert [(v.id, v.availability and v.availability.status) for v in vitamin_c.variants] == [
        ("var_vitamin_c_serum_15", "in_stock"),
        ("var_vitamin_c_serum_30", "out_of_stock"),
    ]
    retinol = next(p for p in result.products if p.id == "prod_retinol_serum")
    assert all(v.availability and v.availability.available is False for v in retinol.variants)


async def test_a_price_filter_drops_variants_outside_it() -> None:
    price = PriceFilter(min=30000, max=60000)
    result = await search(service(), filters=SearchFilters(categories=["Skincare > Serum"], price=price))
    niacinamide = next(p for p in result.products if p.id == "prod_niacinamide_serum")
    assert [v.id for v in niacinamide.variants] == ["var_niacinamide_serum_15", "var_niacinamide_serum_30"]
    assert niacinamide.price_range.max.amount == 59900
    assert all(30000 <= v.price.amount <= 60000 for p in result.products for v in p.variants)


async def test_pages_follow_one_another_through_the_cursor() -> None:
    svc = service()
    browse = SearchFilters(categories=["Skincare"])
    first = await search(svc, filters=browse, pagination=PaginationRequest(limit=5))
    assert first.pagination and first.pagination.has_next_page and first.pagination.cursor
    second = await search(
        svc, filters=browse, pagination=PaginationRequest(limit=5, cursor=first.pagination.cursor)
    )
    assert not {p.id for p in first.products} & {p.id for p in second.products}
    last = await search(svc, filters=browse, pagination=PaginationRequest(limit=50))
    assert last.pagination and not last.pagination.has_next_page and last.pagination.cursor is None


async def test_page_size_defaults_to_ten_and_is_capped() -> None:
    store = FakeCatalogStore(seed("skincare"))
    svc = StoreCatalogService(store, FakeEmbedder())
    browse = SearchFilters(categories=["Skincare"])
    assert len((await search(svc, filters=browse)).products) == 10
    await search(svc, filters=browse, pagination=PaginationRequest(limit=10_000))
    assert store.search_calls[-1]["limit"] == 51  # 50 plus one, to learn whether a next page exists


@pytest.mark.parametrize("cursor", ["garbage", encode_offset(MAX_OFFSET + 1)])
async def test_a_foreign_or_out_of_range_cursor_is_refused(cursor: str) -> None:
    request = SearchRequest(query="serum", pagination=PaginationRequest(cursor=cursor))
    result = await service().search_catalog(SearchCatalogArguments(meta=META, catalog=request))
    assert error_codes(result) == ["invalid_cursor"]


async def test_an_embedding_timeout_names_the_step_and_never_reaches_the_database() -> None:
    store = FakeCatalogStore(seed("skincare"))
    svc = StoreCatalogService(store, FakeEmbedder(fail=EMBED_TIMEOUT))
    result = await svc.search_catalog(SearchCatalogArguments(meta=META, catalog=SearchRequest(query="serum")))
    assert error_codes(result) == ["upstream_timeout"]
    assert isinstance(result, ErrorResponse)
    assert "pinecone.embed_query" in result.messages[0].content
    assert store.search_calls == []


async def test_a_refused_embedding_is_search_unavailable_not_an_exception() -> None:
    svc = service(embedder=FakeEmbedder(fail=EMBED_REFUSED))
    result = await svc.search_catalog(SearchCatalogArguments(meta=META, catalog=SearchRequest(query="serum")))
    assert error_codes(result) == ["search_unavailable"]


# -- lookup_catalog --------------------------------------------------------------------------------


async def test_lookup_resolves_product_ids_to_the_featured_variant_and_variant_ids_exactly() -> None:
    result = await lookup(service(), "prod_vitamin_c_serum", "var_niacinamide_serum_50", "FW-CLN-SAL-200")
    by_id = {p.id: p for p in result.products}
    (vitamin_c,) = by_id["prod_vitamin_c_serum"].variants
    assert (vitamin_c.id, [(i.id, i.match) for i in vitamin_c.inputs]) == (
        "var_vitamin_c_serum_15",
        [("prod_vitamin_c_serum", "featured")],
    )
    (niacinamide,) = by_id["prod_niacinamide_serum"].variants
    assert [(i.id, i.match) for i in niacinamide.inputs] == [("var_niacinamide_serum_50", "exact")]
    (cleanser,) = by_id["prod_salicylic_gel_cleanser"].variants
    assert cleanser.id == "var_salicylic_gel_cleanser_200"
    assert result.messages is None


async def test_lookup_returns_a_product_once_however_many_ids_name_it() -> None:
    result = await lookup(
        service(), "prod_rose_toner", "rose-hydrating-toner", "var_rose_toner_100", "prod_rose_toner"
    )
    (toner,) = result.products
    (variant,) = toner.variants
    assert sorted((i.id, i.match) for i in variant.inputs) == [
        ("prod_rose_toner", "featured"),
        ("rose-hydrating-toner", "featured"),
        ("var_rose_toner_100", "exact"),
    ]


async def test_unknown_ids_are_fewer_products_and_an_informational_message() -> None:
    result = await lookup(service(), "prod_rose_toner", "prod_nope")
    assert [p.id for p in result.products] == ["prod_rose_toner"]
    assert result.messages is not None
    assert [(m.type, m.code, m.content) for m in result.messages] == [("info", "not_found", "prod_nope")]


async def test_a_discontinued_product_can_still_be_looked_up_and_reports_why_it_is_unavailable() -> None:
    result = await lookup(service(), "prod_overnight_recovery_cream")
    (variant,) = result.products[0].variants
    assert variant.availability is not None
    assert (variant.availability.available, variant.availability.status) == (False, "discontinued")


async def test_lookup_filters_apply_after_resolution() -> None:
    result = await lookup(
        service(),
        "var_niacinamide_serum_50",
        "prod_rose_toner",
        filters=SearchFilters(price=PriceFilter(max=40000)),
    )
    assert [p.id for p in result.products] == ["prod_rose_toner"]
    assert result.messages is None, "filtered out is not the same as not found"


async def test_too_many_ids_is_invalid_params() -> None:
    ids = [f"prod_{n}" for n in range(MAX_LOOKUP_IDS + 1)]
    with pytest.raises(RequestTooLarge):
        await service().lookup_catalog(LookupCatalogArguments(meta=META, catalog=LookupRequest(ids=ids)))


# -- get_product -----------------------------------------------------------------------------------


async def test_an_unknown_id_is_an_unrecoverable_not_found() -> None:
    result = await service().get_product(
        GetProductArguments(meta=META, catalog=GetProductRequest(id="prod_nope"))
    )
    assert error_codes(result) == ["not_found"]
    assert isinstance(result, ErrorResponse) and result.messages[0].type == "error"
    assert result.messages[0].severity == "unrecoverable"


async def test_a_product_id_returns_the_featured_variant_and_its_selection() -> None:
    detail = await get(service(), "prod_glycolic_toner")
    assert [v.id for v in detail.variants] == ["var_glycolic_toner_100"]
    assert [(s.name, s.label) for s in detail.selected or []] == [("Size", "100 ml")]
    assert detail.options is not None
    sizes = {v.label: (v.exists, v.available) for v in detail.options[0].values}
    assert sizes == {"100 ml": (True, True), "200 ml": (True, False)}


async def test_a_variant_id_fixes_the_selection_and_ignores_selected() -> None:
    detail = await get(
        service("coffee"),
        "var_ghat_dark_fp_250",
        selected=[SelectedOption(name="Grind", label="Espresso")],
    )
    assert [v.id for v in detail.variants] == ["var_ghat_dark_fp_250"]
    assert [(s.name, s.label) for s in detail.selected or []] == [
        ("Grind", "French Press"),
        ("Size", "250 g"),
    ]


async def test_a_partial_selection_returns_every_matching_variant_with_the_available_first() -> None:
    detail = await get(
        service("coffee"),
        "prod_ghat_estate_dark",
        selected=[SelectedOption(name="Grind", label="French Press")],
    )
    assert [v.id for v in detail.variants] == ["var_ghat_dark_fp_250", "var_ghat_dark_fp_500"]
    assert detail.options is not None
    sizes = {v.label: (v.exists, v.available) for v in detail.options[1].values}
    assert sizes == {"250 g": (True, True), "500 g": (True, False), "1 kg": (False, False)}


async def test_an_impossible_selection_is_relaxed_from_the_end_of_preferences() -> None:
    selected = [SelectedOption(name="Grind", label="Espresso"), SelectedOption(name="Size", label="1 kg")]
    kept_grind = await get(
        service("coffee"), "prod_ghat_estate_dark", selected=selected, preferences=["Grind", "Size"]
    )
    assert [(s.name, s.label) for s in kept_grind.selected or []] == [("Grind", "Espresso")]
    kept_size = await get(
        service("coffee"), "prod_ghat_estate_dark", selected=selected, preferences=["Size", "Grind"]
    )
    assert [(s.name, s.label) for s in kept_size.selected or []] == [("Size", "1 kg")]
    assert [v.id for v in kept_size.variants] == ["var_ghat_dark_wb_1000"]


async def test_a_product_without_options_needs_no_selection() -> None:
    detail = await get(service("coffee"), "prod_french_press")
    assert detail.options is None and detail.selected is None
    assert detail.list_price_range is not None and detail.list_price_range.max.amount == 149900


async def test_a_discontinued_product_is_still_fetchable_by_id() -> None:
    detail = await get(service(), "prod_overnight_recovery_cream")
    assert all(v.availability and v.availability.status == "discontinued" for v in detail.variants)


async def test_a_database_timeout_names_the_step() -> None:
    svc = StoreCatalogService(TimingOutStore(seed("skincare")), FakeEmbedder())
    result = await svc.get_product(
        GetProductArguments(meta=META, catalog=GetProductRequest(id="prod_rose_toner"))
    )
    assert error_codes(result) == ["upstream_timeout"]
    assert isinstance(result, ErrorResponse) and "neon.get_product" in result.messages[0].content
