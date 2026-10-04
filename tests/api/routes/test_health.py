import httpx2
import pytest

from tests.support.app import app as build_app

pytestmark = pytest.mark.anyio


async def test_the_health_check_answers_while_the_app_is_up() -> None:
    app = build_app()
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as http,
    ):
        response = await http.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
