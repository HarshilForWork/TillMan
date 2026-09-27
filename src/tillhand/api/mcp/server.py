"""The MCP server: UCP tools on the low-level `mcp` Server.

UCP's MCP binding has its own result rules, which the SDK's `MCPServer` tool decorator would break:
- the UCP response goes in `structuredContent` as is, with no `{"result": ...}` wrapper;
- a business "no" is a normal result carrying `ucp.status: "error"`, never `isError`;
- a malformed call is a JSON-RPC error (`-32602`), not an `isError` result.

So each tool is a plain async function over a UCP arguments model (ADR-0001), and this module does
the one generic job: validate the arguments, run the function under a deadline, and put its UCP
response on the wire.
"""

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import mcp_types as types
from mcp.server import Server, ServerRequestContext
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel, ValidationError

from tillhand.core.deadlines import StepTimeout, deadline
from tillhand.core.errors import RequestTooLarge, timeout_error
from tillhand.models.ucp import ucp_dump

logger = logging.getLogger(__name__)

ArgumentsT = TypeVar("ArgumentsT", bound=BaseModel)


@dataclass(frozen=True)
class UcpTool(Generic[ArgumentsT]):
    name: str
    description: str
    arguments: type[ArgumentsT]
    run: Callable[[ArgumentsT], Awaitable[BaseModel]]

    def definition(self) -> types.Tool:
        return types.Tool(
            name=self.name,
            description=self.description,
            input_schema=self.arguments.model_json_schema(by_alias=True),
        )


def build_mcp_server(tools: Sequence[UcpTool[Any]], *, tool_seconds: float) -> Server:
    by_name = {tool.name: tool for tool in tools}

    async def list_tools(
        ctx: ServerRequestContext, params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[tool.definition() for tool in tools])

    async def call_tool(
        ctx: ServerRequestContext, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        tool = by_name.get(params.name)
        if tool is None:
            raise MCPError(code=types.INVALID_PARAMS, message=f"Unknown tool: {params.name}")
        try:
            return await _call(tool, params.arguments or {}, tool_seconds)
        except MCPError:
            raise
        except Exception as exc:
            # The SDK's legacy path would send the exception's own text to the client, and that text
            # can carry anything (a connection string, a query). It stays in our log.
            logger.exception("Tool %s crashed", tool.name)
            raise MCPError(code=types.INTERNAL_ERROR, message=f"Internal error in {tool.name}") from exc

    return Server("tillhand", on_list_tools=list_tools, on_call_tool=call_tool)


async def _call(tool: UcpTool[Any], raw: dict[str, Any], seconds: float) -> types.CallToolResult:
    try:
        arguments = tool.arguments.model_validate(raw)
    except ValidationError as exc:
        raise MCPError(
            code=types.INVALID_PARAMS, message=f"Invalid arguments for {tool.name}", data=_fields(exc)
        ) from exc
    try:
        async with deadline(tool.name, seconds):
            result = await tool.run(arguments)
    except StepTimeout as exc:
        result = timeout_error(exc.step)
    except RequestTooLarge as exc:
        raise MCPError(code=types.INVALID_PARAMS, message=str(exc)) from exc
    return _ucp_result(result)


def _ucp_result(response: BaseModel) -> types.CallToolResult:
    """UCP puts the response in `structuredContent`; the text block repeats it for older clients."""
    structured = ucp_dump(response)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(structured))], structured_content=structured
    )


def _fields(exc: ValidationError) -> list[dict[str, str]]:
    """Where the arguments went wrong, without echoing the caller's values back."""
    return [
        {"path": ".".join(str(p) for p in error["loc"]), "problem": error["msg"]} for error in exc.errors()
    ]
