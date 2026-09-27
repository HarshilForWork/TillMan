from contextlib import nullcontext

import httpx2
import pytest

from tests.support.catalog import FakeCatalogStore, FakeEmbedder, seed
from tillhand.main import create_app
from tillhand.services.catalog import StoreCatalogService

pytestmark = pytest.mark.anyio


async def test_the_health_check_answers_while_the_app_is_up() -> None:
    catalog = StoreCatalogService(FakeCatalogStore(seed("skincare")), FakeEmbedder())
    app = create_app(open_catalog=lambda: nullcontext(catalog), allowed_hosts=["testserver"])
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as http,
    ):
        response = await http.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
