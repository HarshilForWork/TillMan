"""The MCP door: UCP tools under their UCP names, served over streamable HTTP."""

from tillhand.api.mcp.catalog import catalog_tools
from tillhand.api.mcp.server import UcpTool, build_mcp_server

__all__ = ["UcpTool", "build_mcp_server", "catalog_tools"]
