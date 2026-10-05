"""Every tool is labelled `public` or `owner_scoped` in one registry, or the server can't be built (#42).

The label says whether a tool reads data that belongs to someone (an Owner). An owner-scoped tool is also
built differently: it takes the caller's Owner, which a public tool never sees. So a tool missing from the
registry, or labelled one way and built the other, is a bug the server refuses to start with.
"""

from typing import Any

import pytest

from tests.support.app import HOST, app
from tests.support.mcp import MODES, serve
from tillhand.api.mcp import TOOL_ACCESS, OwnedTool, UcpTool, build_mcp_server
from tillhand.models.domain import Owner
from tillhand.models.ucp import ErrorResponse, SearchCatalogArguments
from tillhand.services.profiles import ProfileResolver

pytestmark = pytest.mark.anyio


async def _public(arguments: SearchCatalogArguments) -> ErrorResponse:
    raise AssertionError("never called")


async def _owned(arguments: SearchCatalogArguments, owner: Owner) -> ErrorResponse:
    raise AssertionError("never called")


def _build(tools: list[Any]) -> None:
    def no_profiles() -> ProfileResolver:
        raise AssertionError("never called")

    build_mcp_server(tools, profiles=no_profiles, tool_seconds=1)


@pytest.mark.parametrize("mode", MODES)
async def test_every_listed_tool_is_labelled_and_every_label_is_a_listed_tool(mode: str) -> None:
    async with serve(app(), host=HOST, mode=mode) as client:
        listed = await client.list_tools()

    assert {tool.name for tool in listed.tools} == set(TOOL_ACCESS)


def test_the_catalog_tools_and_get_suggestions_are_public() -> None:
    public = {name for name, access in TOOL_ACCESS.items() if access == "public"}

    assert public == {"search_catalog", "lookup_catalog", "get_product", "get_suggestions"}


def test_an_unlabelled_tool_stops_the_server_being_built() -> None:
    dummy = UcpTool("dummy_unlabelled", "A tool nobody labelled.", SearchCatalogArguments, _public)

    with pytest.raises(ValueError, match="dummy_unlabelled is not labelled"):
        _build([dummy])


def test_a_public_label_on_a_tool_that_takes_an_owner_stops_the_build() -> None:
    mislabelled = OwnedTool("search_catalog", "Owned, but labelled public.", SearchCatalogArguments, _owned)

    with pytest.raises(ValueError, match="search_catalog is labelled public"):
        _build([mislabelled])
