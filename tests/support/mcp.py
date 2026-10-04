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


@asynccontextmanager
async def serve(app: FastAPI, *, host: str, mode: str) -> AsyncIterator[Client]:
    """Run the app's lifespan and connect a client: `mode` is "legacy" (the initialize handshake) or a
    modern protocol version such as "2026-07-28"."""
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=f"http://{host}") as http,
        Client(streamable_http_client(f"http://{host}/mcp", http_client=http), mode=mode) as client,
    ):
        yield client


async def call(
    client: Client, tool: str, catalog: dict[str, Any], *, meta: dict[str, Any] = AGENT
) -> dict[str, Any]:
    """A tool call that must succeed at the protocol level; returns its `structuredContent`."""
    result = await client.call_tool(tool, {"meta": meta, "catalog": catalog})
    assert not result.is_error
    assert isinstance(result.structured_content, dict)
    return result.structured_content
