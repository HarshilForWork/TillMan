"""The production app, wired to the real Neon database and Pinecone, answering over HTTP. Opt-in:

uv run alembic upgrade head
uv run python scripts/seed_catalog.py data/seeds/skincare.json
TILLHAND_NEON_TESTS=1 uv run pytest -m neon
"""

import os
import uuid
from typing import Any

import pytest
from mcp import Client

from tests.support.mcp import HARNESS, MODES, call, serve
from tests.support.ucp_spec import schema_errors
from tillhand.main import production_app

live = pytest.mark.skipif(os.environ.get("TILLHAND_NEON_TESTS") != "1", reason="set TILLHAND_NEON_TESTS=1")
pytestmark = [pytest.mark.anyio, pytest.mark.neon, live]

DISCONTINUED = "prod_overnight_recovery_cream"


@pytest.mark.parametrize("mode", MODES)
async def test_the_production_app_serves_the_seeded_catalog(mode: str) -> None:
    async with serve(production_app(), host="localhost:8000", mode=mode) as client:
        found = await call(client, "search_catalog", {"query": "night cream for dry skin"}, meta=HARNESS)
        detail = await call(client, "get_product", {"id": DISCONTINUED}, meta=HARNESS)

    assert schema_errors(found, "shopping/catalog_search", "search_response") == []
    assert found["products"]
    assert DISCONTINUED not in [p["id"] for p in found["products"]]
    assert schema_errors(detail, "shopping/catalog_lookup", "get_product_response") == []
    assert detail["product"]["id"] == DISCONTINUED


@pytest.mark.parametrize("mode", MODES)
async def test_the_production_app_suggests_with_reasons_for_the_harness(mode: str) -> None:
    async with serve(production_app(), host="localhost:8000", mode=mode) as client:
        result = await call(
            client, "get_suggestions", {"product_ids": ["prod_salicylic_gel_cleanser"]}, meta=HARNESS
        )

    assert [(s["id"], s["reason"]["kind"]) for s in result["suggestions"]] == [
        ("prod_niacinamide_serum", "bundle"),
        ("prod_oil_free_gel_moisturiser", "bundle"),
        ("prod_glycolic_toner", "similar"),
    ]
    for suggestion in result["suggestions"]:
        product = {k: v for k, v in suggestion.items() if k != "reason"}
        assert schema_errors(product, "shopping/types/product") == []


@pytest.mark.parametrize("mode", MODES)
async def test_the_production_app_keeps_a_harness_cart_in_neon(mode: str) -> None:
    line = {"item": {"id": "var_niacinamide_serum_30"}, "quantity": 2}
    async with serve(production_app(), host="localhost:8000", mode=mode) as client:
        created = await call_cart(client, "create_cart", {"cart": {"line_items": [line]}})
        got = await call_cart(client, "get_cart", {"id": created["id"]})
        cancelled = await call_cart(client, "cancel_cart", {"id": created["id"]}, key=str(uuid.uuid4()))
        gone = await call_cart(client, "get_cart", {"id": created["id"]})

    assert schema_errors(created, "shopping/cart") == []
    assert created["totals"][-1] == {"type": "total", "amount": 119800}
    assert got == created == cancelled
    assert [m["code"] for m in gone["messages"]] == ["not_found"]


async def call_cart(client: Client, tool: str, arguments: dict[str, Any], *, key: str | None = None) -> Any:
    meta = HARNESS if key is None else {**HARNESS, "idempotency-key": key}
    result = await client.call_tool(tool, {"meta": meta, **arguments})
    assert not result.is_error and isinstance(result.structured_content, dict)
    return result.structured_content
