"""The interface the UCP catalog tools call. The catalog data layer implements it over Neon."""

from typing import Protocol

from .models.catalog import (
    GetProductArguments,
    GetProductResponse,
    LookupCatalogArguments,
    LookupResponse,
    SearchCatalogArguments,
    SearchResponse,
)
from .models.common import ErrorResponse


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
