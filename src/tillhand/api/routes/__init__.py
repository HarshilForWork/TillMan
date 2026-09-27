"""The FastAPI endpoints that aren't MCP protocol traffic."""

from tillhand.api.routes.health import router as health_router

__all__ = ["health_router"]
