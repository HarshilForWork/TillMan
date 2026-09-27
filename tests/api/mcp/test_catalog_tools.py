"""The UCP catalog tools, called by a real MCP client over streamable HTTP.

The app runs in-process behind `httpx2.ASGITransport`, so every call crosses the real HTTP transport
and the MCP runner, over a catalog service backed by the in-memory fakes. Every test runs twice: once
with a client using the legacy `initialize` handshake, and once with a `2026-07-28` client.
"""

import json
from contextlib import AbstractAsyncContextManager, nullcontext
from typing import Any

import anyio
import httpx2
import pytest
from fastapi import FastAPI
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import INTERNAL_ERROR, INVALID_PARAMS

from tests.support.catalog import FakeCatalogStore, FakeEmbedder, seed
from tests.support.mcp import AGENT, MODES, call, serve
from tests.support.ucp_spec import schema_errors
from tillhand.core.errors import timeout_error
from tillhand.main import create_app
from tillhand.models.ucp import (
    ErrorResponse,
    GetProductArguments,
    GetProductResponse,
    LookupCatalogArguments,
    LookupResponse,
    SearchCatalogArguments,
    SearchResponse,
    ucp_dump,
)
from tillhand.services.catalog import MAX_LOOKUP_IDS, CatalogService, StoreCatalogService

pytestmark = pytest.mark.anyio

HOST = "testserver"


@pytest.fixture(params=MODES)
def mode(request: pytest.FixtureRequest) -> str:
    return request.param


def catalog_service(merchant: str = "skincare") -> CatalogService:
    return StoreCatalogService(FakeCatalogStore(seed(merchant)), FakeEmbedder())


def app(service: CatalogService | None = None, **options: Any) -> FastAPI:
    catalog = service or catalog_service()
    return create_app(open_catalog=lambda: nullcontext(catalog), allowed_hosts=[HOST], **options)


def connect(
    mode: str, service: CatalogService | None = None, **options: Any
) -> AbstractAsyncContextManager[Client]:
    return serve(app(service, **options), host=HOST, mode=mode)


async def test_the_catalog_tools_are_listed_under_their_ucp_names(mode: str) -> None:
    async with connect(mode) as client:
        listed = await client.list_tools()

    tools = {tool.name: tool for tool in listed.tools}
    assert set(tools) == {"search_catalog", "lookup_catalog", "get_product"}
    for tool in tools.values():
        assert tool.description
        assert set(tool.input_schema["required"]) == {"meta", "catalog"}
        # No output schema: a client would check a business "no" (an ErrorResponse) against it.
        assert tool.output_schema is None


@pytest.mark.parametrize(
    ("merchant", "query"), [("skincare", "serum for oily skin"), ("coffee", "dark roast")]
)
async def test_search_catalog_returns_ucp_search_results_for_each_merchant(
    mode: str, merchant: str, query: str
) -> None:
    async with connect(mode, catalog_service(merchant)) as client:
        found = await call(client, "search_catalog", {"query": query})

    assert schema_errors(found, "shopping/catalog_search", "search_response") == []
    assert found["products"]


@pytest.mark.parametrize(
    ("merchant", "product_id", "unavailable_id"),
    [
        ("skincare", "prod_vitamin_c_serum", "var_vitamin_c_serum_30"),
        ("coffee", "prod_attikan_light", "var_attikan_wb_500"),
    ],
)
async def test_unavailable_variants_are_marked_unavailable(
    mode: str, merchant: str, product_id: str, unavailable_id: str
) -> None:
    async with connect(mode, catalog_service(merchant)) as client:
        looked_up = await call(client, "lookup_catalog", {"ids": [unavailable_id]})
        detail = await call(client, "get_product", {"id": unavailable_id})

    assert schema_errors(looked_up, "shopping/catalog_lookup", "lookup_response") == []
    assert schema_errors(detail, "shopping/catalog_lookup", "get_product_response") == []
    ((variant,),) = [p["variants"] for p in looked_up["products"] if p["id"] == product_id]
    assert variant["id"] == unavailable_id
    assert variant["availability"]["available"] is False
    assert [v["availability"]["available"] for v in detail["product"]["variants"]] == [False]


async def test_a_discontinued_product_is_left_out_of_search_but_fetchable_by_id(mode: str) -> None:
    discontinued = "prod_overnight_recovery_cream"
    async with connect(mode) as client:
        found = await call(client, "search_catalog", {"filters": {"categories": ["Skincare > Moisturiser"]}})
        detail = await call(client, "get_product", {"id": discontinued})
        looked_up = await call(client, "lookup_catalog", {"ids": [discontinued]})

    assert found["products"]
    assert discontinued not in [p["id"] for p in found["products"]]
    assert detail["product"]["id"] == discontinued
    assert [p["id"] for p in looked_up["products"]] == [discontinued]


async def test_a_business_no_is_a_normal_result_with_ucp_error_status(mode: str) -> None:
    async with connect(mode) as client:
        result = await client.call_tool("get_product", {"meta": AGENT, "catalog": {"id": "prod_nope"}})

    assert not result.is_error
    assert isinstance(result.structured_content, dict)
    assert schema_errors(result.structured_content, "common/types/error_response") == []
    assert result.structured_content["ucp"]["status"] == "error"
    assert [m["code"] for m in result.structured_content["messages"]] == ["not_found"]


async def test_a_lookup_over_the_batch_limit_is_invalid_params(mode: str) -> None:
    ids = [f"prod_{i}" for i in range(MAX_LOOKUP_IDS + 1)]
    async with connect(mode) as client:
        with pytest.raises(MCPError) as refused:
            await client.call_tool("lookup_catalog", {"meta": AGENT, "catalog": {"ids": ids}})

    assert refused.value.error.code == INVALID_PARAMS


async def test_a_call_without_the_ucp_agent_profile_is_invalid_params(mode: str) -> None:
    async with connect(mode) as client:
        with pytest.raises(MCPError) as refused:
            await client.call_tool("search_catalog", {"meta": {}, "catalog": {"query": "serum"}})

    assert refused.value.error.code == INVALID_PARAMS
    assert "meta.ucp-agent" in json.dumps(refused.value.error.data)


class CrashingCatalog:
    async def search_catalog(self, arguments: SearchCatalogArguments) -> SearchResponse | ErrorResponse:
        raise RuntimeError("password=hunter2 at db.internal:5432")

    async def lookup_catalog(self, arguments: LookupCatalogArguments) -> LookupResponse | ErrorResponse:
        raise NotImplementedError

    async def get_product(self, arguments: GetProductArguments) -> GetProductResponse | ErrorResponse:
        raise NotImplementedError


async def test_a_crash_is_an_internal_error_that_reveals_nothing(mode: str) -> None:
    async with connect(mode, CrashingCatalog()) as client:
        with pytest.raises(MCPError) as crashed:
            await client.call_tool("search_catalog", {"meta": AGENT, "catalog": {"query": "serum"}})

    assert crashed.value.error.code == INTERNAL_ERROR
    assert "hunter2" not in repr(crashed.value.error)


class UnserializableResponse(ErrorResponse):
    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("password=hunter2 in a field that would not serialize")


class UnserializableCatalog(CrashingCatalog):
    async def search_catalog(self, arguments: SearchCatalogArguments) -> SearchResponse | ErrorResponse:
        return UnserializableResponse.model_validate(ucp_dump(timeout_error("search_catalog")))


async def test_a_response_that_cannot_be_put_on_the_wire_reveals_nothing(mode: str) -> None:
    async with connect(mode, UnserializableCatalog()) as client:
        with pytest.raises(MCPError) as crashed:
            await client.call_tool("search_catalog", {"meta": AGENT, "catalog": {"query": "serum"}})

    assert crashed.value.error.code == INTERNAL_ERROR
    assert "hunter2" not in repr(crashed.value.error)


class HangingCatalog(CrashingCatalog):
    async def search_catalog(self, arguments: SearchCatalogArguments) -> SearchResponse | ErrorResponse:
        await anyio.sleep(30)
        raise AssertionError("the deadline should have ended this call")


async def test_a_tool_that_overruns_its_deadline_says_which_tool_timed_out(mode: str) -> None:
    async with connect(mode, HangingCatalog(), tool_seconds=0.2) as client:
        result = await call(client, "search_catalog", {"query": "serum"})

    assert schema_errors(result, "common/types/error_response") == []
    assert result["ucp"]["status"] == "error"
    ((message,),) = [result["messages"]]
    assert message["code"] == "upstream_timeout"
    assert "search_catalog" in message["content"]


async def test_a_request_for_an_unknown_host_is_refused() -> None:
    served = app()
    async with (
        served.router.lifespan_context(served),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=served), base_url="http://rebound.example"
        ) as http,
    ):
        response = await http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"accept": "application/json, text/event-stream"},
        )

    assert response.status_code == 421


async def test_a_request_from_a_browser_page_is_refused() -> None:
    """Platforms call from their servers, which send no Origin. A browser page never reaches the tools."""
    served = app()
    async with (
        served.router.lifespan_context(served),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=served), base_url=f"http://{HOST}") as http,
    ):
        response = await http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"accept": "application/json, text/event-stream", "origin": "https://page.example"},
        )

    assert response.status_code == 403


class RoundRobin(httpx2.AsyncBaseTransport):
    """Two replicas behind a load balancer with no sticky sessions: requests alternate between them."""

    def __init__(self, *replicas: httpx2.AsyncBaseTransport) -> None:
        self.replicas = replicas
        self.served = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        replica = self.replicas[self.served % len(self.replicas)]
        self.served += 1
        return await replica.handle_async_request(request)


async def test_a_legacy_client_is_served_by_any_replica() -> None:
    first, second = app(), app()
    balancer = RoundRobin(httpx2.ASGITransport(app=first), httpx2.ASGITransport(app=second))
    async with (
        first.router.lifespan_context(first),
        second.router.lifespan_context(second),
        httpx2.AsyncClient(transport=balancer, base_url=f"http://{HOST}") as http,
        Client(streamable_http_client(f"http://{HOST}/mcp", http_client=http), mode="legacy") as client,
    ):
        for _ in range(3):
            await call(client, "get_product", {"id": "prod_rose_toner"})

    assert balancer.served >= 4  # the handshake and the calls landed on both replicas
