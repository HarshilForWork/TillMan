"""A real MCP client over streamable HTTP, talking to our app in-process through `httpx2.ASGITransport`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from fastapi import FastAPI
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from tests.support.profiles import PLATFORM_URL

MODES = ["legacy", "2026-07-28"]
"""The two protocol eras every tool test runs in: the legacy `initialize` handshake, and 2026-07-28."""

AGENT = {"ucp-agent": {"profile": PLATFORM_URL}}
"""`meta` as a Platform sends it on the public UCP door. The test app pre-approves this profile."""

HARNESS = {"ucp-agent": {"profile": "https://tillhand.vercel.app/profiles/harness.json"}}
"""`meta` as our own Harness sends it: the production app pre-approves it in `data/platforms.json`."""


MERCHANT_PATH = "/merchant/mcp"
"""The Merchant door: every request needs `TillHand-Api-Key`."""


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[None]:
    """Run the app's lifespan, so clients can `connect` to it (several at once, each with its own headers)."""
    async with app.router.lifespan_context(app):
        yield


@asynccontextmanager
async def connect(
    app: FastAPI, *, host: str, mode: str, path: str = "/mcp", headers: dict[str, str] | None = None
) -> AsyncIterator[Client]:
    """A client of a running app, on `path`, sending `headers` with every request: `mode` is "legacy" (the
    initialize handshake) or a modern protocol version such as "2026-07-28"."""
    transport = httpx2.ASGITransport(app=app)
    async with (
        httpx2.AsyncClient(transport=transport, base_url=f"http://{host}", headers=headers or {}) as http,
        Client(streamable_http_client(f"http://{host}{path}", http_client=http), mode=mode) as client,
    ):
        yield client


@asynccontextmanager
async def serve(
    app: FastAPI, *, host: str, mode: str, path: str = "/mcp", headers: dict[str, str] | None = None
) -> AsyncIterator[Client]:
    """Run the app's lifespan and connect one client."""
    async with running(app), connect(app, host=host, mode=mode, path=path, headers=headers) as client:
        yield client


async def call(
    client: Client, tool: str, catalog: dict[str, Any], *, meta: dict[str, Any] = AGENT
) -> dict[str, Any]:
    """A tool call that must succeed at the protocol level; returns its `structuredContent`."""
    result = await client.call_tool(tool, {"meta": meta, "catalog": catalog})
    assert not result.is_error
    assert isinstance(result.structured_content, dict)
    return result.structured_content
