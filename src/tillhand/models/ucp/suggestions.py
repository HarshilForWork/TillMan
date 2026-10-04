"""`get_suggestions`, the `app.vercel.tillhand.shopping.suggestions` Extension (#18, #46, ADR-0006).

UCP has no cross-sell capability, so this one is ours. It extends the catalog capabilities, so it follows
the catalog tools' shape: arguments are `{meta, catalog}`, and each Suggestion is the same UCP
Product the catalog tools return, plus a `reason` the agent explains it by.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .catalog import Product, RequestMeta
from .common import Attribution, Closed, Context, Message, Open, Signals, UcpResponseMeta

DEFAULT_SUGGESTIONS = 3
MAX_SUGGESTIONS = 10
MAX_SUGGESTION_SOURCES = 10

ProductId = Annotated[str, Field(min_length=1)]


class GetSuggestionsRequest(Open):
    """Suggestions for some Products (`product_ids`), or for a Cart (`cart_id`, not supported yet: #47)."""

    product_ids: Annotated[list[ProductId], Field(min_length=1, max_length=MAX_SUGGESTION_SOURCES)] | None = (
        None
    )
    cart_id: Annotated[str, Field(min_length=1)] | None = None
    limit: Annotated[int, Field(ge=1, le=MAX_SUGGESTIONS)] = DEFAULT_SUGGESTIONS
    context: Context | None = None
    signals: Signals | None = None
    attribution: Attribution | None = None

    @model_validator(mode="after")
    def _products_or_cart(self) -> Self:
        if (self.product_ids is None) == (self.cart_id is None):
            raise ValueError("pass either product_ids or cart_id, not both")
        return self


class GetSuggestionsArguments(Open):
    meta: RequestMeta
    catalog: GetSuggestionsRequest


SuggestionKind = Literal["bundle", "similar"]


class SuggestionReason(Closed):
    """Why this Product is suggested, as facts the agent repeats rather than reasons it invents.

    - `bundle`: the Merchant pairs it with `from`.
    - `similar`: it resembles `from` and is in another category. Never present it as curated.
    - `personalised`: what about the Customer changed it; empty until memory lands (#29).
    """

    kind: SuggestionKind
    from_: str = Field(alias="from")
    personalised: list[str] = Field(default_factory=list)


class Suggestion(Product):
    reason: SuggestionReason


class SuggestionsResponse(Closed):
    ucp: UcpResponseMeta
    suggestions: list[Suggestion]
    """Best first; empty, with a message saying so, when nothing qualifies."""
    messages: list[Message] | None = None
