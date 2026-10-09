"""The cross-owner isolation fixture (#42, #11 decision 9): another owner must learn nothing about your data.

An owner creates something (a Cart, say). Then every other caller uses every owner-scoped tool twice:
once with the owner's id, once with an id that never existed. The two answers must be **byte-identical**,
and must be the tool's rule (#11 decision 7):
- `not_found`: a normal result whose message code is `not_found` (Carts, and Orders with a Customer token);
- `identity_required`: the JSON-RPC `-32000` error asking for a Customer token (Orders without one).
Afterwards the owner reads the thing again and must find it unchanged, so a stranger's update or cancel did
nothing.

The callers are real ones, through both doors (#41): on the Merchant door, Customers A and B and a guest
through the demo assistant's key, and Customer A through another assistant's key (the same Customer through
another profile is another Owner); on the public door, two Platforms. Two of them take a turn as the owner:
Customer A through the demo assistant, and a Platform on the public door.

**Adding a tool** (the cart, checkout, order and refund tickets each do this): describe how an owner
creates the resource and how each of its tools is called, as a `Resource` in `RESOURCES`.
`test_every_owner_scoped_tool_is_in_the_isolation_fixture` fails until every tool labelled `owner_scoped`
is probed here.
"""

import json
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Literal

from mcp import Client
from mcp.shared.exceptions import MCPError

from tests.support.mcp import MERCHANT_PATH
from tests.support.merchant import KEY_A, KEY_OTHER, merchant_headers
from tests.support.profiles import PLATFORM_PROFILE, PLATFORM_URL
from tillhand.models.ucp import PlatformProfile

OTHER_PLATFORM_URL = "https://other-platform.example/profiles/agent.json"

BOTH_PLATFORMS = {
    PLATFORM_URL: PlatformProfile.model_validate(PLATFORM_PROFILE),
    OTHER_PLATFORM_URL: PlatformProfile.model_validate(PLATFORM_PROFILE),
}
"""Pre-approve both public-door Platforms, so neither is fetched."""


@dataclass(frozen=True)
class Caller:
    """Someone calling the tools: which door, the HTTP headers, and the `meta` sent with each call."""

    path: str
    headers: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    """On the Merchant door the key's profile fills `meta.ucp-agent`, so nothing needs sending."""


def _platform(url: str) -> Caller:
    return Caller("/mcp", meta={"ucp-agent": {"profile": url}})


def _through(key: str, customer: str | None = None) -> Caller:
    return Caller(MERCHANT_PATH, merchant_headers(key, customer))


CALLERS = {
    "Customer A through the demo assistant": _through(KEY_A, "cust_a"),
    "Customer B through the demo assistant": _through(KEY_A, "cust_b"),
    "a guest of the demo assistant": _through(KEY_A),
    "Customer A through another assistant": _through(KEY_OTHER, "cust_a"),
    "a Platform on the public door": _platform(PLATFORM_URL),
    "another Platform on the public door": _platform(OTHER_PLATFORM_URL),
}

OWNERS = ["Customer A through the demo assistant", "a Platform on the public door"]
"""Who takes a turn as the owner; everyone else in `CALLERS` is a stranger to them."""

OpenClient = Callable[[Caller], AbstractAsyncContextManager[Client]]

Rule = Literal["not_found", "identity_required"]


@dataclass(frozen=True)
class Probe:
    tool: str
    rule: Rule
    arguments: Callable[[str], dict[str, Any]]
    """The call's arguments, minus `meta`, for the id being probed."""
    idempotent: bool = False
    """The tool requires `meta["idempotency-key"]`: each call gets a fresh one."""


@dataclass(frozen=True)
class Resource:
    name: str
    create_tool: str
    """The owner-scoped tool that creates it; it takes no id, so it has nothing to probe."""
    create: Callable[[Client, dict[str, Any]], Awaitable[str]]
    """Creates one as the owner `meta` names, and returns its id."""
    read: Probe
    """How the owner reads it back, to show the strangers changed nothing."""
    probes: list[Probe]


async def outcome(client: Client, probe: Probe, caller: Caller, id: str) -> str:
    """Everything the caller receives, as one canonical string: the result's text, or the error."""
    meta = {**caller.meta, "idempotency-key": str(uuid.uuid4())} if probe.idempotent else caller.meta
    try:
        result = await client.call_tool(probe.tool, {"meta": meta, **probe.arguments(id)})
    except MCPError as exc:
        error = exc.error
        return json.dumps({"error": [error.code, error.message, error.data]}, sort_keys=True)
    texts = [getattr(block, "text", None) for block in result.content]
    return json.dumps({"is_error": result.is_error, "content": texts}, sort_keys=True)


def follows_rule(answer: str, rule: Rule) -> bool:
    parsed = json.loads(answer)
    if rule == "identity_required":
        error = parsed.get("error")
        return error is not None and error[0] == -32000 and "identity_required" in json.dumps(error)
    if parsed.get("is_error") is not False:
        return False
    content = json.loads(parsed["content"][0])
    return content["ucp"]["status"] == "error" and [m["code"] for m in content["messages"]] == ["not_found"]


async def leaks(open_client: OpenClient, resource: Resource) -> list[str]:
    """Every way a stranger could tell an owner's resource from one that never existed; empty is safe."""
    found: list[str] = []
    for owner_name in OWNERS:
        owner = CALLERS[owner_name]
        async with open_client(owner) as client:
            id = await resource.create(client, owner.meta)
            before = await outcome(client, resource.read, owner, id)
        for stranger, caller in CALLERS.items():
            if stranger == owner_name:
                continue
            async with open_client(caller) as client:
                for probe in resource.probes:
                    theirs = await outcome(client, probe, caller, id)
                    never_existed = await outcome(client, probe, caller, str(uuid.uuid4()))
                    if theirs != never_existed:
                        found.append(
                            f"{probe.tool}: {stranger} gets {theirs} for {owner_name}'s id, "
                            f"{never_existed} otherwise"
                        )
                    elif not follows_rule(theirs, probe.rule):
                        found.append(f"{probe.tool}: {stranger} gets {theirs}, not {probe.rule}")
        async with open_client(owner) as client:
            after = await outcome(client, resource.read, owner, id)
        if after != before:
            found.append(f"{resource.name}: strangers changed {owner_name}'s data: {before} became {after}")
    return found


async def _create_cart(client: Client, owner: dict[str, Any]) -> str:
    line = {"item": {"id": "var_niacinamide_serum_30"}, "quantity": 2}
    result = await client.call_tool("create_cart", {"meta": owner, "cart": {"line_items": [line]}})
    assert isinstance(result.structured_content, dict)
    return result.structured_content["id"]


def _by_id(id: str) -> dict[str, Any]:
    return {"id": id}


CART = Resource(
    name="Cart",
    create_tool="create_cart",
    create=_create_cart,
    read=Probe("get_cart", "not_found", _by_id),
    probes=[
        Probe("get_cart", "not_found", _by_id),
        Probe(
            "update_cart",
            "not_found",
            lambda id: {
                "id": id,
                "cart": {"line_items": [{"item": {"id": "var_hyaluronic_serum_15"}, "quantity": 1}]},
            },
        ),
        Probe("cancel_cart", "not_found", _by_id, idempotent=True),
    ],
)
"""#50. A Cart belongs to the Platform on the public door, or to the Merchant's key plus the Customer."""

RESOURCES: list[Resource] = [CART]
"""Every owner-scoped resource and its tools. Each build ticket appends its own."""
