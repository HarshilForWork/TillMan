"""`get_suggestions`, TillHand's Suggestions Extension (`app.vercel.tillhand.shopping.suggestions`)."""

from collections.abc import Callable
from typing import Any

from tillhand.api.mcp.server import UcpTool
from tillhand.models.ucp import (
    DEFAULT_SUGGESTIONS,
    MAX_SUGGESTION_SOURCES,
    MAX_SUGGESTIONS,
    GetSuggestionsArguments,
)
from tillhand.services.suggestions import SuggestionService

GET_SUGGESTIONS = (
    f"Suggest Products to offer alongside the given ones (up to {MAX_SUGGESTION_SOURCES} product_ids): what "
    "the Merchant pairs with them, then similar Products from other categories. Returns up to `limit` "
    f"(default {DEFAULT_SUGGESTIONS}, at most {MAX_SUGGESTIONS}) catalog Products, best first, each with a "
    "`reason`. Explain each Suggestion only in the terms of its reason: kind 'bundle' means the Merchant "
    "pairs it with the Product in `from`; kind 'similar' means it resembles that Product, so never present "
    "it as curated or recommended by the Merchant. Never claim expert or medical endorsement. Unknown or "
    "discontinued ids are skipped with a warning. cart_id is not supported yet."
)


def suggestion_tools(suggestions: Callable[[], SuggestionService]) -> list[UcpTool[Any]]:
    return [
        UcpTool(
            "get_suggestions",
            GET_SUGGESTIONS,
            GetSuggestionsArguments,
            lambda arguments: suggestions().get_suggestions(arguments),
        )
    ]
