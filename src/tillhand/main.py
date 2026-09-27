"""The app factory: FastAPI, with the MCP server's streamable-HTTP endpoint at `/mcp`."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx2
from fastapi import FastAPI
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route

from tillhand.api.mcp import build_mcp_server, catalog_tools
from tillhand.api.routes import health_router
from tillhand.core.config import Settings, get_settings
from tillhand.core.deadlines import TOOL_SECONDS
from tillhand.integrations.neon.catalog import NeonCatalogStore
from tillhand.integrations.neon.pool import create_pool
from tillhand.integrations.pinecone import PineconeEmbedder
from tillhand.services.catalog import CatalogService, StoreCatalogService

HTTP_TIMEOUT_SECONDS = 10.0
"""A backstop for the shared HTTP client; each call also runs under its own, shorter step deadline."""


def create_app(
    *,
    open_catalog: Callable[[], AbstractAsyncContextManager[CatalogService]],
    allowed_hosts: list[str],
    tool_seconds: float = TOOL_SECONDS,
) -> FastAPI:
    """`open_catalog` makes the catalog service at startup and closes what it holds at shutdown."""

    def catalog() -> CatalogService:
        return app.state.catalog  # `app` is made below; this only runs once it is serving

    sessions = StreamableHTTPSessionManager(
        app=build_mcp_server(catalog_tools(catalog), tool_seconds=tool_seconds),
        # Stateless: no MCP session lives in this process, so any replica can answer any request,
        # including from clients on the legacy handshake (Railway has no sticky sessions).
        stateless=True,
        json_response=True,
        # DNS-rebinding protection: a Host not on the list gets 421, and any browser Origin gets 403.
        # Platforms call from their servers and send no Origin, so no Origin is allowed.
        security_settings=TransportSecuritySettings(allowed_hosts=allowed_hosts),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_catalog() as service, sessions.run():
            app.state.catalog = service
            yield

    app = FastAPI(title="TillHand", lifespan=lifespan)
    app.include_router(health_router)
    app.router.routes.append(Route("/mcp", endpoint=StreamableHTTPASGIApp(sessions)))
    return app


def production_app() -> FastAPI:
    """The deployed app, over Neon and Pinecone: `uvicorn tillhand.main:production_app --factory`."""
    settings = get_settings()
    return create_app(open_catalog=lambda: _open_catalog(settings), allowed_hosts=settings.allowed_hosts)


@asynccontextmanager
async def _open_catalog(settings: Settings) -> AsyncIterator[CatalogService]:
    """The process's one DB pool and one HTTP client, opened at startup and closed at shutdown."""
    pool = await create_pool(settings)
    try:
        async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            embedder = PineconeEmbedder(
                client,
                api_key=settings.pinecone_api_key,
                model=settings.embedding_model,
                dimension=settings.embedding_dimension,
            )
            yield StoreCatalogService(NeonCatalogStore(pool), embedder)
    finally:
        await pool.close()
