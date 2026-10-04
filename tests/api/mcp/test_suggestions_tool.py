"""`get_suggestions` over the real MCP transport, as a Platform calls it (#46).

The ranking rules are tested on the service (tests/services/test_suggestions.py); this checks the door:
the tool's contract, its UCP shapes, and that a business "no" stays a normal result.
"""

from typing import Any

import pytest
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from tests.support.app import HOST, app, services
from tests.support.catalog import FakeSuggestionStore, seed
from tests.support.mcp import AGENT, MODES, call, serve
from tests.support.ucp_spec import schema_errors
from tillhand.core.constants import SUGGESTIONS
from tillhand.core.errors import UCP_DISCOVERY_FAILED
from tillhand.models.ucp import MAX_SUGGESTION_SOURCES
from tillhand.services.suggestions import StoreSuggestionService

pytestmark = pytest.mark.anyio

CLEANSER = "prod_salicylic_gel_cleanser"
SIMILARITY = {(CLEANSER, "prod_glycolic_toner"): 0.53, (CLEANSER, "prod_ceramide_cream_cleanser"): 0.9}


@pytest.fixture(params=MODES)
def mode(request: pytest.FixtureRequest) -> str:
    return request.param


def connect(mode: str) -> Any:
    suggestions = StoreSuggestionService(FakeSuggestionStore(seed("skincare"), SIMILARITY), floor=0.48)
    return serve(app(services(suggestions=suggestions)), host=HOST, mode=mode)


async def test_get_suggestions_is_listed_and_tells_the_agent_how_to_explain(mode: str) -> None:
    async with connect(mode) as client:
        listed = await client.list_tools()

    (tool,) = [t for t in listed.tools if t.name == "get_suggestions"]
    assert set(tool.input_schema["required"]) == {"meta", "catalog"}
    assert tool.output_schema is None
    assert tool.description and "reason" in tool.description and "never" in tool.description.lower()


async def test_suggestions_are_ucp_products_each_with_its_reason(mode: str) -> None:
    async with connect(mode) as client:
        result = await call(client, "get_suggestions", {"product_ids": [CLEANSER]})

    assert result["ucp"]["capabilities"] == {SUGGESTIONS.name: [{"version": SUGGESTIONS.version}]}
    assert [(s["id"], s["reason"]) for s in result["suggestions"]] == [
        ("prod_niacinamide_serum", {"kind": "bundle", "from": CLEANSER, "personalised": []}),
        ("prod_oil_free_gel_moisturiser", {"kind": "bundle", "from": CLEANSER, "personalised": []}),
        ("prod_glycolic_toner", {"kind": "similar", "from": CLEANSER, "personalised": []}),
    ]
    for suggestion in result["suggestions"]:
        product = {k: v for k, v in suggestion.items() if k != "reason"}
        assert schema_errors(product, "shopping/types/product") == []


async def test_an_unknown_id_is_a_warning_in_a_normal_result(mode: str) -> None:
    async with connect(mode) as client:
        result = await call(client, "get_suggestions", {"product_ids": ["prod_nope"]})

    assert result["ucp"]["status"] == "success"
    assert result["suggestions"] == []
    for message in result["messages"]:
        assert schema_errors(message, "common/types/message") == []
    assert [(m["type"], m["code"]) for m in result["messages"]] == [
        ("warning", "not_found"),
        ("info", "no_suggestions"),
    ]


async def test_a_cart_is_a_business_no_for_now(mode: str) -> None:
    async with connect(mode) as client:
        result = await call(client, "get_suggestions", {"cart_id": "cart_123"})

    assert schema_errors(result, "common/types/error_response") == []
    assert [m["code"] for m in result["messages"]] == ["not_supported"]


@pytest.mark.parametrize(
    "catalog",
    [
        {"product_ids": [f"prod_{i}" for i in range(MAX_SUGGESTION_SOURCES + 1)]},
        {"product_ids": []},
        {"product_ids": [CLEANSER], "limit": 11},
        {"product_ids": [CLEANSER], "limit": 0},
        {"product_ids": [CLEANSER], "cart_id": "cart_123"},
        {},
    ],
    ids=["too many ids", "no ids", "limit over 10", "limit 0", "both inputs", "neither input"],
)
async def test_a_malformed_request_is_invalid_params(mode: str, catalog: dict[str, Any]) -> None:
    async with connect(mode) as client:
        with pytest.raises(MCPError) as refused:
            await client.call_tool("get_suggestions", {"meta": AGENT, "catalog": catalog})

    assert refused.value.error.code == INVALID_PARAMS


async def test_get_suggestions_validates_the_platform_profile_like_every_tool(mode: str) -> None:
    async with connect(mode) as client:
        with pytest.raises(MCPError) as refused:
            await client.call_tool("get_suggestions", {"meta": {}, "catalog": {"product_ids": [CLEANSER]}})

    assert refused.value.error.code == UCP_DISCOVERY_FAILED
