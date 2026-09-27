"""The UCP catalog tools, under their UCP names, each wrapping one `CatalogService` method."""

from collections.abc import Callable
from typing import Any

from tillhand.api.mcp.server import UcpTool
from tillhand.models.ucp import GetProductArguments, LookupCatalogArguments, SearchCatalogArguments
from tillhand.services.catalog import MAX_LOOKUP_IDS, CatalogService

SEARCH_CATALOG = (
    "Search the Merchant's catalog in natural language, optionally filtered by category and price. "
    "Returns matching Products with their Variants, prices and availability; page with the cursor."
)
LOOKUP_CATALOG = (
    "Look up Products by identifier: a Product id or handle, or a Variant id or SKU "
    f"(at most {MAX_LOOKUP_IDS} at once). Unknown identifiers are skipped and reported as not_found."
)
GET_PRODUCT = (
    "Get one Product in full: every Option with which values are available, and the Variants that "
    "match the selected Options. Pass a Variant id to select that exact Variant."
)


def catalog_tools(catalog: Callable[[], CatalogService]) -> list[UcpTool[Any]]:
    return [
        UcpTool(
            "search_catalog",
            SEARCH_CATALOG,
            SearchCatalogArguments,
            lambda arguments: catalog().search_catalog(arguments),
        ),
        UcpTool(
            "lookup_catalog",
            LOOKUP_CATALOG,
            LookupCatalogArguments,
            lambda arguments: catalog().lookup_catalog(arguments),
        ),
        UcpTool(
            "get_product",
            GET_PRODUCT,
            GetProductArguments,
            lambda arguments: catalog().get_product(arguments),
        ),
    ]
