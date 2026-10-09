"""The MCP door: UCP tools under their UCP names, served over streamable HTTP."""

from tillhand.api.mcp.access import TOOL_ACCESS, Access
from tillhand.api.mcp.carts import cart_tools
from tillhand.api.mcp.catalog import catalog_tools
from tillhand.api.mcp.doors import DoorRules, MerchantDoorRules, PublicDoorRules
from tillhand.api.mcp.guards import MerchantDoorGuard, PublicDoorGuard
from tillhand.api.mcp.server import (
    OwnedTool,
    Tool,
    UcpTool,
    build_mcp_server,
    check_access,
)
from tillhand.api.mcp.suggestions import suggestion_tools

__all__ = [
    "TOOL_ACCESS",
    "Access",
    "DoorRules",
    "MerchantDoorGuard",
    "MerchantDoorRules",
    "OwnedTool",
    "PublicDoorGuard",
    "PublicDoorRules",
    "Tool",
    "UcpTool",
    "build_mcp_server",
    "cart_tools",
    "catalog_tools",
    "check_access",
    "suggestion_tools",
]
