"""The production app, wired to the real Neon database and Pinecone, answering over HTTP. Opt-in:

uv run alembic upgrade head
uv run python scripts/seed_catalog.py data/seeds/skincare.json
TILLHAND_NEON_TESTS=1 uv run pytest -m neon
"""

import json
import os
import uuid
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError

from tests.support.mcp import HARNESS, MERCHANT_PATH, MODES, call, connect, running, serve
from tests.support.merchant import merchant_headers
from tests.support.profiles import PLATFORM_PROFILE
from tests.support.ucp_spec import schema_errors
from tillhand.core.config import get_settings
from tillhand.integrations.neon.merchant import NeonMerchantStore, NeonPlatformStore
from tillhand.integrations.neon.pool import create_pool
from tillhand.main import production_app
from tillhand.models.domain import PreApprovedPlatform
from tillhand.services.merchant_door import issue_key

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


@pytest.mark.parametrize("mode", MODES)
async def test_the_production_app_serves_the_merchant_door_from_neon(mode: str) -> None:
    pool = await create_pool(get_settings(), max_size=2)
    url = f"https://tests.example/{uuid.uuid4()}/assistant.json"
    entry = PreApprovedPlatform.model_validate(
        {"profile_url": url, "note": "test", "profile": PLATFORM_PROFILE}
    )
    customer = f"test-{uuid.uuid4()}"
    try:
        await NeonPlatformStore(pool).upsert([(entry, json.dumps(PLATFORM_PROFILE))])
        key, _ = await issue_key(NeonMerchantStore(pool), label="live test", profile_url=url)
        line = {"item": {"id": "var_niacinamide_serum_30"}, "quantity": 1}
        served = production_app()  # reads the registry, now holding the test assistant, at startup
        async with running(served):
            async with connect(
                served,
                host="localhost:8000",
                mode=mode,
                path=MERCHANT_PATH,
                headers=merchant_headers(key, customer),
            ) as client:
                created = await client.call_tool("create_cart", {"cart": {"line_items": [line]}})
                assert isinstance(created.structured_content, dict)
                got = await client.call_tool("get_cart", {"id": created.structured_content["id"]})
            async with connect(served, host="localhost:8000", mode=mode) as public:
                with pytest.raises(MCPError) as refused:
                    await public.call_tool("get_cart", {"meta": {"ucp-agent": {"profile": url}}, "id": "x"})
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=served), base_url="http://localhost:8000"
            ) as http:
                wrong = await http.post(MERCHANT_PATH, json={}, headers=merchant_headers("thk_wrong"))
    finally:
        async with pool.acquire() as conn:
            await conn.execute("delete from carts where owner_platform = $1", url)
            await conn.execute("delete from merchant_api_keys where profile_url = $1", url)
            await conn.execute("delete from platforms where profile_url = $1", url)
            await conn.execute("delete from customers where merchant_customer_id = $1", customer)
        await pool.close()

    assert isinstance(got.structured_content, dict)
    assert got.structured_content["id"] == created.structured_content["id"]
    assert refused.value.error.data == {"code": "merchant_key_required"}
    assert wrong.status_code == 401
