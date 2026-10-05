"""The MCP server: UCP tools on the low-level `mcp` Server.

UCP's MCP binding has its own result rules, which the SDK's `MCPServer` tool decorator would break:
- the UCP response goes in `structuredContent` as is, with no `{"result": ...}` wrapper;
- a business "no" is a normal result carrying `ucp.status: "error"`, never `isError`;
- a malformed call is a JSON-RPC error (`-32602`), not an `isError` result.

So each tool is a plain async function over a UCP arguments model (ADR-0001), and this module does
the one generic job: resolve the caller's profile, validate the arguments, run the function under a
deadline, and put its UCP response on the wire.

The profile comes first (#37). A Platform names its profile URL in `meta.ucp-agent.profile` on every
call, and UCP says the business MUST fetch and validate it. One that can't be used is a protocol
failure: JSON-RPC `-32001` with `error.data.code` saying why, before the arguments are even looked at.
The HTTP status stays 200, as the SDK sends it for every JSON-RPC error; UCP's REST statuses
(400/424/422) are not mapped onto `/mcp` (decided in #37).

Two kinds of tool (#42). A `UcpTool` is public: it reads only what anyone may see. An `OwnedTool` reads
data that belongs to someone, so it is also handed the caller's `Owner`, which `identify` builds from the
resolved profile. Every tool is labelled one or the other in `access.TOOL_ACCESS`, and the server refuses
to be built with a tool that isn't, or whose label contradicts how it is built.
"""

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Generic, TypeAlias, TypeVar

import mcp_types as types
from mcp.server import Server, ServerRequestContext
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel, ValidationError

from tillhand.api.mcp.access import TOOL_ACCESS, Access
from tillhand.core.deadlines import StepTimeout, deadline
from tillhand.core.errors import (
    UCP_DISCOVERY_FAILED,
    UCP_PROTOCOL_ERROR,
    IdempotencyConflict,
    ProfileError,
    RequestTooLarge,
    ServiceUnavailable,
    timeout_error,
)
from tillhand.models.domain import Owner
from tillhand.models.ucp import RequestMeta, ToolCallMeta, ucp_dump
from tillhand.services.profiles import ProfileResolver

logger = logging.getLogger(__name__)

ArgumentsT = TypeVar("ArgumentsT", bound=BaseModel)

Identify: TypeAlias = Callable[[RequestMeta], Owner]
"""Who is calling an owner-scoped tool, from the call's `meta` (its profile is resolved by then)."""


def platform_owner(meta: RequestMeta) -> Owner:
    """The public door's caller: the Platform alone. The Merchant door adds the Customer (#41)."""
    return Owner(platform=meta.ucp_agent.profile)


@dataclass(frozen=True)
class UcpTool(Generic[ArgumentsT]):
    """A public tool: it reads only what anyone with a valid profile may see."""

    access: ClassVar[Access] = "public"
    name: str
    description: str
    arguments: type[ArgumentsT]
    run: Callable[[ArgumentsT], Awaitable[BaseModel]]

    def definition(self) -> types.Tool:
        return _definition(self.name, self.description, self.arguments)

    async def invoke(self, arguments: ArgumentsT, caller: Callable[[], Owner]) -> BaseModel:
        return await self.run(arguments)  # never asks who is calling


@dataclass(frozen=True)
class OwnedTool(Generic[ArgumentsT]):
    """An owner-scoped tool: it reads or changes data that belongs to the caller, given as its `Owner`."""

    access: ClassVar[Access] = "owner_scoped"
    name: str
    description: str
    arguments: type[ArgumentsT]
    run: Callable[[ArgumentsT, Owner], Awaitable[BaseModel]]

    def definition(self) -> types.Tool:
        return _definition(self.name, self.description, self.arguments)

    async def invoke(self, arguments: ArgumentsT, caller: Callable[[], Owner]) -> BaseModel:
        return await self.run(arguments, caller())


Tool: TypeAlias = "UcpTool[Any] | OwnedTool[Any]"


def _definition(name: str, description: str, arguments: type[BaseModel]) -> types.Tool:
    return types.Tool(
        name=name, description=description, input_schema=arguments.model_json_schema(by_alias=True)
    )


def check_access(tools: Sequence[Tool], access: Mapping[str, Access] = TOOL_ACCESS) -> None:
    """Every tool labelled, and built as its label says; `ValueError` naming each one that isn't."""
    problems: list[str] = []
    for tool in tools:
        label = access.get(tool.name)
        if label is None:
            problems.append(f"{tool.name} is not labelled public or owner_scoped")
        elif label != tool.access:
            problems.append(f"{tool.name} is labelled {label} but built as {tool.access}")
    if problems:
        raise ValueError("; ".join(problems))


def build_mcp_server(
    tools: Sequence[Tool],
    *,
    profiles: Callable[[], ProfileResolver],
    tool_seconds: float,
    identify: Identify = platform_owner,
    access: Mapping[str, Access] = TOOL_ACCESS,
) -> Server:
    check_access(tools, access)
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
        arguments = params.arguments or {}
        try:
            meta = _meta(arguments)
            await profiles().resolve(meta.ucp_agent.profile)
            return await _call(tool, arguments, tool_seconds, lambda: identify(meta))
        except ProfileError as exc:
            raise MCPError(
                code=UCP_DISCOVERY_FAILED, message="UCP discovery failed", data=ucp_dump(exc.data())
            ) from exc
        except MCPError:
            raise
        except Exception as exc:
            # The SDK's legacy path would send the exception's own text to the client, and that text
            # can carry anything (a connection string, a query). It stays in our log.
            logger.exception("Tool %s crashed", tool.name)
            raise MCPError(code=types.INTERNAL_ERROR, message=f"Internal error in {tool.name}") from exc

    return Server("tillhand", on_list_tools=list_tools, on_call_tool=call_tool)


def _meta(arguments: dict[str, Any]) -> RequestMeta:
    """The call's `meta`, read before the arguments are validated: no profile is `invalid_profile_url`."""
    try:
        return ToolCallMeta.model_validate(arguments).meta
    except ValidationError as exc:
        raise ProfileError("invalid_profile_url", "meta.ucp-agent.profile is missing") from exc


async def _call(
    tool: Tool, raw: dict[str, Any], seconds: float, caller: Callable[[], Owner]
) -> types.CallToolResult:
    try:
        arguments = tool.arguments.model_validate(raw)
    except ValidationError as exc:
        raise MCPError(
            code=types.INVALID_PARAMS, message=f"Invalid arguments for {tool.name}", data=_fields(exc)
        ) from exc
    try:
        async with deadline(tool.name, seconds):
            result = await tool.invoke(arguments, caller)
    except StepTimeout as exc:
        result = timeout_error(exc.step)
    except RequestTooLarge as exc:
        raise MCPError(code=types.INVALID_PARAMS, message=str(exc)) from exc
    except IdempotencyConflict as exc:
        # UCP's 409: the key was used for a different request. A new request needs a new key.
        raise MCPError(code=UCP_PROTOCOL_ERROR, message=exc.message, data=ucp_dump(exc.data())) from exc
    except ServiceUnavailable as exc:
        # UCP's 503: the write was refused rather than risked (it fails closed). Safe to retry later.
        logger.warning("%s refused: %s is unavailable", tool.name, exc.step, exc_info=exc.__cause__)
        raise MCPError(code=UCP_PROTOCOL_ERROR, message=exc.message, data=ucp_dump(exc.data())) from exc
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
