"""The app factory: FastAPI, with the MCP server's streamable-HTTP endpoint at `/mcp`."""

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

import httpx2
from fastapi import FastAPI
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route

from tillhand.api.mcp import (
    TOOL_ACCESS,
    Access,
    DoorRules,
    MerchantDoorGuard,
    MerchantDoorRules,
    PublicDoorGuard,
    PublicDoorRules,
    Tool,
    build_mcp_server,
    cart_tools,
    catalog_tools,
    suggestion_tools,
)
from tillhand.api.routes import health_router
from tillhand.core.config import Settings, get_settings
from tillhand.core.deadlines import TOOL_SECONDS
from tillhand.core.logs import configure_logging
from tillhand.integrations.neon.carts import NeonCartStore
from tillhand.integrations.neon.catalog import NeonCatalogStore
from tillhand.integrations.neon.merchant import NeonMerchantStore, NeonPlatformStore
from tillhand.integrations.neon.pool import create_pool
from tillhand.integrations.pinecone import PineconeEmbedder
from tillhand.integrations.profile_fetch import HttpProfileFetcher
from tillhand.services.carts import CartService, StoreCartService
from tillhand.services.catalog import CatalogService, StoreCatalogService
from tillhand.services.merchant_door import MerchantDoor
from tillhand.services.profiles import ProfileResolver, registry_from
from tillhand.services.suggestions import StoreSuggestionService, SuggestionService

HTTP_TIMEOUT_SECONDS = 10.0
"""A backstop for the shared HTTP client; each call also runs under its own, shorter step deadline."""


@dataclass(frozen=True)
class Services:
    """What the doors call, made once at startup over the process's one DB pool and one HTTP client."""

    catalog: CatalogService
    suggestions: SuggestionService
    carts: CartService
    profiles: ProfileResolver
    merchant: MerchantDoor
    """The Merchant door's key and Customer checks (#41)."""


def service_tools(services: Callable[[], Services]) -> list[Tool]:
    """Every tool the server serves, each labelled in `api.mcp.access.TOOL_ACCESS`."""
    return [
        *catalog_tools(lambda: services().catalog),
        *suggestion_tools(lambda: services().suggestions),
        *cart_tools(lambda: services().carts),
    ]


def create_app(
    *,
    open_services: Callable[[], AbstractAsyncContextManager[Services]],
    allowed_hosts: list[str],
    tool_seconds: float = TOOL_SECONDS,
    tools: Callable[[Callable[[], Services]], list[Tool]] = service_tools,
    access: Mapping[str, Access] = TOOL_ACCESS,
) -> FastAPI:
    """`open_services` makes the services at startup and closes what they hold at shutdown.

    Two doors onto the same tools (#41): the public door at `/mcp`, and the Merchant door at
    `/merchant/mcp`, where every request needs a Merchant API key. `tools` and `access` let tests add tools.
    """

    def services() -> Services:
        return app.state.services  # `app` is made below; this only runs once it is serving

    def door(rules: DoorRules) -> StreamableHTTPSessionManager:
        return StreamableHTTPSessionManager(
            app=build_mcp_server(
                tools(services),
                profiles=lambda: services().profiles,
                tool_seconds=tool_seconds,
                door=rules,
                access=access,
            ),
            # Stateless: no MCP session lives in this process, so any replica can answer any request,
            # including from clients on the legacy handshake (Railway has no sticky sessions).
            stateless=True,
            json_response=True,
            # DNS-rebinding protection: a Host not on the list gets 421, and any browser Origin gets 403.
            # Platforms call from their servers and send no Origin, so no Origin is allowed.
            security_settings=TransportSecuritySettings(allowed_hosts=allowed_hosts),
        )

    public, merchant = door(PublicDoorRules()), door(MerchantDoorRules())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_services() as opened, public.run(), merchant.run():
            app.state.services = opened
            yield

    app = FastAPI(title="TillHand", lifespan=lifespan)
    app.include_router(health_router)
    app.router.routes.append(Route("/mcp", endpoint=PublicDoorGuard(StreamableHTTPASGIApp(public))))
    app.router.routes.append(
        Route(
            "/merchant/mcp",
            endpoint=MerchantDoorGuard(StreamableHTTPASGIApp(merchant), lambda: services().merchant),
        )
    )
    return app


def production_app() -> FastAPI:
    """The deployed app, over Neon and Pinecone: `uvicorn tillhand.main:production_app --factory`."""
    configure_logging()
    settings = get_settings()
    return create_app(open_services=lambda: _open_services(settings), allowed_hosts=settings.allowed_hosts)


@asynccontextmanager
async def _open_services(settings: Settings) -> AsyncIterator[Services]:
    """The process's one DB pool and one HTTP client, opened at startup and closed at shutdown."""
    pool = await create_pool(settings)
    try:
        platform_store = NeonPlatformStore(pool)
        pre_approved = registry_from(await platform_store.platforms())
        async with httpx2.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            embedder = PineconeEmbedder(
                client,
                api_key=settings.pinecone_api_key,
                model=settings.embedding_model,
                dimension=settings.embedding_dimension,
            )
            store = NeonCatalogStore(pool)
            yield Services(
                catalog=StoreCatalogService(store, embedder),
                suggestions=StoreSuggestionService(store, floor=settings.suggestion_similarity_floor),
                carts=StoreCartService(
                    NeonCartStore(pool), lifetime=timedelta(days=settings.cart_lifetime_days)
                ),
                profiles=ProfileResolver(
                    pre_approved, HttpProfileFetcher(client), key_binding=platform_store
                ),
                merchant=MerchantDoor(NeonMerchantStore(pool)),
            )
    finally:
        await pool.close()
