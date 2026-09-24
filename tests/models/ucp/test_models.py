"""Behaviour of the UCP models beyond round-tripping: what they reject, and payloads we build ourselves."""

from typing import Any

import pytest
from pydantic import ValidationError

from tests.support.ucp_spec import schema_errors
from tillhand.core.constants import UCP_VERSION
from tillhand.models.ucp import (
    Availability,
    CapabilityEntry,
    Category,
    Description,
    ErrorResponse,
    Measure,
    Media,
    MessageError,
    MessageInfo,
    OptionValue,
    PaginationResponse,
    Price,
    PriceRange,
    Product,
    ProductOption,
    SearchCatalogArguments,
    SearchResponse,
    SelectedOption,
    UcpResponseMeta,
    UnitPrice,
    Variant,
    ucp_dump,
)


def inr(paise: int) -> Price:
    return Price(amount=paise, currency="INR")


def serum(*, available: bool = True) -> Product:
    """A Product with one Option and two Variants, one of them possibly out of stock."""
    return Product(
        id="prod_serum",
        title="Niacinamide Serum",
        description=Description(plain="Oil-control serum."),
        price_range=PriceRange(min=inr(49900), max=inr(89900)),
        list_price_range=PriceRange(min=inr(59900), max=inr(99900)),
        categories=[Category(value="Skincare > Serum", taxonomy="merchant")],
        tags=["skin-oily", "fragrance-free"],
        options=[ProductOption(name="Size", values=[OptionValue(label="30ml"), OptionValue(label="50ml")])],
        variants=[
            Variant(
                id="var_serum_30",
                title="30ml",
                description=Description(plain="30ml bottle."),
                price=inr(49900),
                list_price=inr(59900),
                availability=Availability(available=True),
                options=[SelectedOption(name="Size", label="30ml")],
            ),
            Variant(
                id="var_serum_50",
                title="50ml",
                description=Description(plain="50ml bottle."),
                price=inr(89900),
                availability=Availability(available=available),
                options=[SelectedOption(name="Size", label="50ml")],
            ),
        ],
    )


def test_a_product_we_build_satisfies_ucps_product_schema() -> None:
    assert schema_errors(ucp_dump(serum(available=False)), "shopping/types/product") == []


def test_a_search_response_we_build_satisfies_ucps_schema() -> None:
    response = SearchResponse(
        ucp=UcpResponseMeta(version=UCP_VERSION),
        products=[serum()],
        pagination=PaginationResponse(has_next_page=False),
    )
    assert schema_errors(ucp_dump(response), "shopping/catalog_search", "search_response") == []


@pytest.mark.parametrize(
    ("build", "why"),
    [
        (lambda: Price(amount=-1, currency="INR"), "amounts are non-negative minor units"),
        (lambda: Price(amount=2**53, currency="INR"), "amounts stay within JSON's safe integers"),
        (lambda: Price(amount=100, currency="inr"), "currency is an upper-case ISO 4217 code"),
        (lambda: Price(amount=100, currency="RUPEE"), "currency is exactly three letters"),
        (lambda: Description(), "a description needs at least one format"),
        (lambda: PaginationResponse(has_next_page=True), "a next page needs a cursor"),
        (lambda: ProductOption(name="Size", values=[]), "an Option has at least one value"),
        (
            lambda: Product.model_validate({**ucp_dump(serum()), "variants": []}),
            "a Product has at least one Variant",
        ),
        (lambda: UcpResponseMeta(version="24-09-2026"), "version is YYYY-MM-DD"),
    ],
)
def test_invalid_values_are_rejected(build: Any, why: str) -> None:
    with pytest.raises(ValidationError):
        build()


def test_products_we_build_reject_unknown_fields() -> None:
    # A typo in our own output must fail loudly rather than ship an off-spec payload.
    with pytest.raises(ValidationError):
        Product.model_validate({**ucp_dump(serum()), "prise_range": {}})


def test_requests_accept_fields_we_do_not_model() -> None:
    # UCP requests are open for extension; a Platform may send signals or attribution we ignore.
    arguments = SearchCatalogArguments.model_validate(
        {
            "meta": {"ucp-agent": {"profile": "https://platform.example/profile.json"}, "trace": "abc"},
            "catalog": {"query": "serum", "signals": {"dev.ucp.buyer_ip": "203.0.113.1"}},
        }
    )
    assert arguments.meta.ucp_agent.profile == "https://platform.example/profile.json"
    assert arguments.catalog.query == "serum"


def test_messages_are_told_apart_by_type() -> None:
    response = ErrorResponse.model_validate(
        {
            "ucp": {"version": UCP_VERSION, "status": "error"},
            "messages": [
                {"type": "error", "code": "out_of_stock", "content": "Sold out.", "severity": "recoverable"},
                {"type": "info", "content": "Restock expected next week."},
            ],
        }
    )
    assert isinstance(response.messages[0], MessageError)
    assert isinstance(response.messages[1], MessageInfo)


@pytest.mark.parametrize("url", ["not a url", "/relative/path.jpg", "cdn.example.com/x.jpg"])
def test_urls_must_be_absolute(url: str) -> None:
    with pytest.raises(ValidationError):
        Media(type="image", url=url)


def test_urls_are_kept_exactly_as_written() -> None:
    # No normalisation (e.g. an added trailing slash), or spec payloads would stop round-tripping.
    assert Media(type="image", url="https://cdn.example.com").url == "https://cdn.example.com"


def test_extends_is_never_an_empty_list() -> None:
    with pytest.raises(ValidationError):
        CapabilityEntry(version=UCP_VERSION, extends=[])


def test_signals_use_their_ucp_keys() -> None:
    arguments = SearchCatalogArguments.model_validate(
        {
            "meta": {"ucp-agent": {"profile": "https://platform.example/profile.json"}},
            "catalog": {"signals": {"dev.ucp.buyer_ip": "203.0.113.1"}},
        }
    )
    assert arguments.catalog.signals is not None
    assert arguments.catalog.signals.buyer_ip == "203.0.113.1"


def test_unit_price_measures_are_positive() -> None:
    ten_ml = Measure(unit="MLT", display_text="ml", value=10)
    UnitPrice(amount=1200, currency="INR", measure=ten_ml, reference=ten_ml)
    with pytest.raises(ValidationError):
        UnitPrice(
            amount=1200,
            currency="INR",
            measure=Measure(unit="MLT", display_text="ml", value=0),
            reference=ten_ml,
        )


def test_free_form_objects_are_parsed_not_passed_through_raw() -> None:
    variant = serum().variants[0].model_copy(update={"metadata": None})
    parsed = type(variant).model_validate({**ucp_dump(variant), "metadata": {"batch": "B-12"}})
    assert parsed.metadata is not None
    assert ucp_dump(parsed)["metadata"] == {"batch": "B-12"}
