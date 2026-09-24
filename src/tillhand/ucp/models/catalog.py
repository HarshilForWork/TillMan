"""UCP catalog capability (`shopping/catalog_search.json`, `shopping/catalog_lookup.json`, `shopping/types/`).

Requests are `Open` (Platforms may send fields we don't use); everything we return is `Closed`.
The tool arguments wrap the request under `catalog`, next to the `meta` every MCP call carries.
"""

from typing import Annotated, Self

from pydantic import Field, model_validator

from .common import (
    MAX_SAFE_INTEGER,
    Actions,
    Amount,
    Attribution,
    Closed,
    Context,
    CurrencyCode,
    Description,
    Link,
    Media,
    Message,
    Open,
    OpenObject,
    PaginationRequest,
    PaginationResponse,
    Policy,
    Price,
    PriceRange,
    Signals,
    UcpResponseMeta,
    Url,
)

# -- Units of sale (`common/types/unit.json`, `measure.json`, `quantity_unit.json`) --------------

EACH = "C62"
"""UN/CEFACT Rec 20 code for "one/each", UCP's default sale basis."""


class Unit(Closed):
    unit: str
    display_text: str
    scale: Annotated[int, Field(ge=0, le=15)] | None = None

    @model_validator(mode="after")
    def _each_has_no_scale(self) -> Self:
        if self.unit == EACH and self.scale not in (None, 0):
            raise ValueError("the 'each' unit (C62) is always scale 0")
        return self


class QuantityUnit(Unit):
    increment: Annotated[int, Field(ge=1)] | None = None


class Measure(Unit):
    value: Annotated[int, Field(ge=-MAX_SAFE_INTEGER, le=MAX_SAFE_INTEGER)]


class UnitPrice(Closed):
    """A shelf-style comparison price, e.g. ₹12 per 10 ml (`shopping/types/unit_price.json`)."""

    amount: Amount
    currency: CurrencyCode
    measure: Measure
    reference: Measure

    @model_validator(mode="after")
    def _positive_measures(self) -> Self:
        if self.measure.value < 1 or self.reference.value < 1:
            raise ValueError("unit price measure and reference values are at least 1")
        return self


# -- Product building blocks ---------------------------------------------------------------------


class Category(Closed):
    value: str
    taxonomy: str | None = None


class Availability(Closed):
    available: bool | None = None
    status: str | None = None


class SelectedOption(Closed):
    name: str
    label: str
    id: str | None = None


class OptionValue(Closed):
    label: str
    id: str | None = None


class DetailOptionValue(OptionValue):
    available: bool | None = None
    exists: bool | None = None


class ProductOption(Closed):
    name: str
    values: Annotated[list[OptionValue], Field(min_length=1)]


class DetailProductOption(Closed):
    name: str
    values: Annotated[list[DetailOptionValue], Field(min_length=1)]


class Rating(Closed):
    value: Annotated[float, Field(ge=0)]
    scale_max: Annotated[float, Field(ge=1)]
    scale_min: Annotated[float, Field(ge=0)] | None = None
    count: Annotated[int, Field(ge=0)] | None = None


class Barcode(Closed):
    type: str
    value: str


class Seller(Closed):
    name: str | None = None
    links: list[Link] | None = None


class Variant(Closed):
    id: str
    title: str
    description: Description
    price: Price
    sku: str | None = None
    barcodes: list[Barcode] | None = None
    handle: str | None = None
    url: Url | None = None
    categories: list[Category] | None = None
    quantity_unit: QuantityUnit | None = None
    list_price: Price | None = None
    unit_price: UnitPrice | None = None
    availability: Availability | None = None
    options: list[SelectedOption] | None = None
    media: list[Media] | None = None
    rating: Rating | None = None
    tags: list[str] | None = None
    seller: Seller | None = None
    metadata: OpenObject | None = None


class _ProductFields(Closed):
    id: str
    title: str
    description: Description
    price_range: PriceRange
    handle: str | None = None
    url: Url | None = None
    categories: list[Category] | None = None
    list_price_range: PriceRange | None = None
    media: list[Media] | None = None
    rating: Rating | None = None
    tags: list[str] | None = None
    metadata: OpenObject | None = None


class Product(_ProductFields):
    options: list[ProductOption] | None = None
    variants: Annotated[list[Variant], Field(min_length=1)]


class InputCorrelation(Closed):
    id: str
    match: str | None = None


class LookupVariant(Variant):
    """A Variant in a lookup result, saying which requested ids it answers."""

    inputs: Annotated[list[InputCorrelation], Field(min_length=1)]


class LookupProduct(_ProductFields):
    options: list[ProductOption] | None = None
    variants: Annotated[list[LookupVariant], Field(min_length=1)]


class DetailProduct(_ProductFields):
    """A Product as `get_product` returns it: options carry per-value availability."""

    options: list[DetailProductOption] | None = None
    variants: Annotated[list[Variant], Field(min_length=1)]
    selected: list[SelectedOption] | None = None


# -- Requests ------------------------------------------------------------------------------------


class PriceFilter(Closed):
    min: Amount | None = None
    max: Amount | None = None


class SearchFilters(Open):
    """The core UCP filters. Open: a business may accept custom filter keys."""

    categories: list[str] | None = None
    price: PriceFilter | None = None


class _CatalogRequest(Open):
    """Fields every catalog request may carry."""

    filters: SearchFilters | None = None
    context: Context | None = None
    signals: Signals | None = None
    attribution: Attribution | None = None


class SearchRequest(_CatalogRequest):
    query: str | None = None
    pagination: PaginationRequest | None = None


class LookupRequest(_CatalogRequest):
    ids: Annotated[list[str], Field(min_length=1)]


class GetProductRequest(_CatalogRequest):
    id: str
    selected: list[SelectedOption] | None = None
    preferences: list[str] | None = None


# -- Responses -----------------------------------------------------------------------------------


class _CatalogResponse(Closed):
    ucp: UcpResponseMeta
    actions: Actions | None = None
    messages: list[Message] | None = None
    policies: list[Policy] | None = None


class SearchResponse(_CatalogResponse):
    products: list[Product]
    pagination: PaginationResponse | None = None


class LookupResponse(_CatalogResponse):
    products: list[LookupProduct]


class GetProductResponse(_CatalogResponse):
    product: DetailProduct


# -- MCP tool arguments --------------------------------------------------------------------------


class UcpAgent(Open):
    profile: str


class RequestMeta(Open):
    """`params.arguments.meta` (`transports/mcp_tool_call.json`). The catalog binding requires `ucp-agent`.

    Both doors hand the tools this same shape. The public UCP door takes `ucp-agent` from the
    Platform's request. The Merchant door authenticates the Merchant assistant with a Merchant API
    key, and the server fills in that key's pre-registered profile before the tool is called, so the
    tools never see which door a call came through.
    """

    ucp_agent: UcpAgent = Field(alias="ucp-agent")
    idempotency_key: str | None = Field(default=None, alias="idempotency-key")


class SearchCatalogArguments(Open):
    meta: RequestMeta
    catalog: SearchRequest


class LookupCatalogArguments(Open):
    meta: RequestMeta
    catalog: LookupRequest


class GetProductArguments(Open):
    meta: RequestMeta
    catalog: GetProductRequest
