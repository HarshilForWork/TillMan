"""UCP common types (`source/schemas/common/`, `source/schemas/ucp.json`).

Two model bases, on purpose:
- `Closed` for what we produce: an unknown field is a bug in our code and must fail loudly.
- `Open` for what Platforms send us: UCP requests are extensible, and a field we don't model is
  kept, not rejected.

Serialise every outgoing payload with `ucp_dump`.
"""

from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

MAX_SAFE_INTEGER = 9_007_199_254_740_991

Amount = Annotated[int, Field(ge=0, le=MAX_SAFE_INTEGER, strict=True)]
"""Integer minor units (paise for INR); `common/types/amount.json`."""

CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
UcpVersion = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
ReverseDomainName = Annotated[
    str, Field(pattern=r"^[a-z](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9_-]*[a-z0-9_])?)+$")
]
"""A UCP identifier like `dev.ucp.shopping.catalog` (`common/types/reverse_domain_name.json`)."""


def _absolute_uri(value: str) -> str:
    parts = urlsplit(value)
    if not parts.scheme or not (parts.netloc or parts.scheme in ("mailto", "urn", "tel")):
        raise ValueError(f"{value!r} is not an absolute URI")
    return value


Url = Annotated[str, AfterValidator(_absolute_uri)]
"""A `format: uri` string, kept exactly as written (no normalisation, so payloads round-trip)."""

Severity = Literal["recoverable", "requires_buyer_input", "requires_buyer_review", "unrecoverable"]
ContentType = Literal["plain", "markdown"]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Open(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class OpenObject(Open):
    """An object UCP defines as free-form (`"type": "object"` with no fixed properties), e.g. `metadata`.

    Parsed as a model, never passed around as a raw dict; its fields are whatever the sender put there.
    """


def ucp_dump(model: BaseModel) -> dict[str, Any]:
    """The wire form of a model: JSON types, UCP field names, and unset optionals left out."""
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


class Price(Closed):
    amount: Amount
    currency: CurrencyCode


class PriceRange(Closed):
    min: Price
    max: Price


class Description(Closed):
    plain: str | None = None
    html: str | None = None
    markdown: str | None = None

    @model_validator(mode="after")
    def _at_least_one_format(self) -> Self:
        if self.plain is None and self.html is None and self.markdown is None:
            raise ValueError("a description needs at least one of plain, html or markdown")
        return self


class Media(Closed):
    type: str
    url: Url
    alt_text: str | None = None
    width: Annotated[int, Field(ge=1)] | None = None
    height: Annotated[int, Field(ge=1)] | None = None


class Link(Closed):
    type: str
    url: Url
    title: str | None = None


class PaginationRequest(Open):
    cursor: str | None = None
    limit: Annotated[int, Field(ge=1)] | None = None


class PaginationResponse(Closed):
    cursor: str | None = None
    has_next_page: bool
    total_count: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def _next_page_needs_cursor(self) -> Self:
        if self.has_next_page and self.cursor is None:
            raise ValueError("has_next_page is true, so a cursor is required")
        return self


class Context(Open):
    """The Customer's context as a Platform sends it (`common/types/context.json`).

    Open, since Platforms may add their own keys. `currency` stays a plain string because UCP types
    it as one here; a Price's currency is the strict ISO 4217 code.
    """

    intent: str | None = None
    language: str | None = None
    currency: str | None = None
    location: str | None = None


class Signals(Open):
    """Platform-observed signals (`common/types/signals.json`); keys are reverse-domain names."""

    buyer_ip: str | None = Field(default=None, alias="dev.ucp.buyer_ip")
    user_agent: str | None = Field(default=None, alias="dev.ucp.user_agent")


Attribution = dict[str, str]
"""Attribution parameters (`shopping/types/attribution.json`): string keys to string values."""


class Policy(Open):
    """A business policy (`common/types/policy.json`), e.g. returns. UCP allows extra fields here."""

    type: ReverseDomainName
    description: Description
    applies_to: list[str] | None = None
    url: Url | None = None


class ActionInstance(Open):
    id: Annotated[str, Field(min_length=1)]
    config: OpenObject | None = None


Actions = dict[str, Annotated[list[ActionInstance], Field(min_length=1)]]
"""Actions the buyer can take, keyed by reverse-domain name (`common/types/actions.json`)."""


# -- Messages ------------------------------------------------------------------------------------


class MessageError(Closed):
    type: Literal["error"] = "error"
    code: str
    content: str
    severity: Severity
    path: str | None = None
    content_type: ContentType | None = None


class MessageWarning(Closed):
    type: Literal["warning"] = "warning"
    code: str
    content: str
    path: str | None = None
    content_type: ContentType | None = None
    presentation: str | None = None
    image_url: Url | None = None
    url: Url | None = None


class MessageInfo(Closed):
    type: Literal["info"] = "info"
    content: str
    code: str | None = None
    path: str | None = None
    content_type: ContentType | None = None


Message = Annotated[MessageError | MessageWarning | MessageInfo, Field(discriminator="type")]


# -- The `ucp` envelope on every response --------------------------------------------------------


class Entity(Open):
    """What every capability, service and payment handler shares (`ucp.json#/$defs/entity`)."""

    version: UcpVersion
    spec: Url | None = None
    schema_: Url | None = Field(default=None, alias="schema")
    id: str | None = None
    config: OpenObject | None = None


class CapabilityEntry(Entity):
    extends: ReverseDomainName | Annotated[list[ReverseDomainName], Field(min_length=1)] | None = None


class ServiceEntry(Entity):
    transport: Literal["rest", "mcp", "a2a", "embedded"]
    endpoint: Url | None = None


class PaymentHandlerEntry(Entity):
    """A payment handler's entry. Its instruments belong to the payment-handler work, so they stay open."""

    available_instruments: Annotated[list[OpenObject], Field(min_length=1)] | None = None

    @model_validator(mode="after")
    def _id_is_required(self) -> Self:
        if self.id is None:
            raise ValueError("a payment handler entry needs an id")
        return self


class UcpResponseMeta(Open):
    version: UcpVersion
    status: Literal["success", "error"] = "success"
    capabilities: dict[ReverseDomainName, list[CapabilityEntry]] | None = None
    services: dict[ReverseDomainName, list[ServiceEntry]] | None = None
    payment_handlers: dict[ReverseDomainName, list[PaymentHandlerEntry]] | None = None
    map_order: dict[str, list[str]] | None = None


class ErrorResponse(Closed):
    """A business outcome that is a "no" (`common/types/error_response.json`).

    Returned as a normal tool result, never as a JSON-RPC error or MCP `isError`.
    """

    ucp: UcpResponseMeta
    messages: Annotated[list[Message], Field(min_length=1)]
    continue_url: Url | None = None

    @model_validator(mode="after")
    def _status_is_error(self) -> Self:
        if self.ucp.status != "error":
            raise ValueError("an error response carries ucp.status 'error'")
        return self
