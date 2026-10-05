"""The cross-owner isolation fixture (#42, #11 decision 9): another owner must learn nothing about your data.

Owner A creates something (a Cart, say). Then each stranger calls every owner-scoped tool twice: once
with A's id, once with an id that never existed. The two answers must be **byte-identical**, and must be
the tool's rule (#11 decision 7):
- `not_found`: a normal result whose message code is `not_found` (Carts, and Orders with a Customer token);
- `identity_required`: the JSON-RPC `-32000` error asking for a Customer token (Orders without one).
Afterwards A reads the thing again and must find it unchanged, so a stranger's update or cancel did nothing.

The strangers cover both halves of an Owner: Customer B and a guest on A's own Platform, then a guest
and Customer A on another Platform (the same Customer through another Platform is another Owner). A
Customer is named by the test-only `meta["test-customer"]`, read by `identify_with_test_customer`; the
Merchant door (#41) is the first real door that identifies one.

**Adding a tool** (the cart, checkout, order and refund tickets each do this): describe how Owner A creates
the resource and how each of its tools is called, as a `Resource` in `RESOURCES` (or in that ticket's
test module). `test_every_owner_scoped_tool_is_in_the_isolation_fixture` fails until every tool labelled
`owner_scoped` is probed here.
"""

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from mcp import Client
from mcp.shared.exceptions import MCPError

from tests.support.profiles import PLATFORM_PROFILE, PLATFORM_URL
from tillhand.models.domain import Owner
from tillhand.models.ucp import PlatformProfile, RequestMeta

OTHER_PLATFORM_URL = "https://other-platform.example/profiles/agent.json"

BOTH_PLATFORMS = {
    PLATFORM_URL: PlatformProfile.model_validate(PLATFORM_PROFILE),
    OTHER_PLATFORM_URL: PlatformProfile.model_validate(PLATFORM_PROFILE),
}
"""Pre-approve both Platforms, so neither is fetched."""

TEST_CUSTOMER = "test-customer"


def meta(platform: str, customer: str | None = None) -> dict[str, Any]:
    named = {"ucp-agent": {"profile": platform}}
    return named if customer is None else {**named, TEST_CUSTOMER: customer}


def identify_with_test_customer(request_meta: RequestMeta) -> Owner:
    """The test door: the Platform, plus the Customer named in `meta["test-customer"]`, if any."""
    customer = (request_meta.model_extra or {}).get(TEST_CUSTOMER)
    platform = request_meta.ucp_agent.profile
    return Owner(platform=platform, customer=customer if isinstance(customer, str) else None)


OWNER_A = meta(PLATFORM_URL, "cust_a")

STRANGERS = {
    "Customer B on the same Platform": meta(PLATFORM_URL, "cust_b"),
    "a guest on the same Platform": meta(PLATFORM_URL),
    "a guest on another Platform": meta(OTHER_PLATFORM_URL),
    "Customer A on another Platform": meta(OTHER_PLATFORM_URL, "cust_a"),
}

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


async def outcome(client: Client, probe: Probe, caller: dict[str, Any], id: str) -> str:
    """Everything the caller receives, as one canonical string: the result's text, or the error."""
    call_meta = {**caller, "idempotency-key": str(uuid.uuid4())} if probe.idempotent else caller
    try:
        result = await client.call_tool(probe.tool, {"meta": call_meta, **probe.arguments(id)})
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


async def leaks(client: Client, resource: Resource) -> list[str]:
    """Every way a stranger could tell Owner A's resource from one that never existed; empty is safe."""
    found: list[str] = []
    id = await resource.create(client, OWNER_A)
    before = await outcome(client, resource.read, OWNER_A, id)
    for stranger, caller in STRANGERS.items():
        for probe in resource.probes:
            theirs = await outcome(client, probe, caller, id)
            never_existed = await outcome(client, probe, caller, str(uuid.uuid4()))
            if theirs != never_existed:
                found.append(f"{probe.tool}: {stranger} gets {theirs} for A's id, {never_existed} otherwise")
            elif not follows_rule(theirs, probe.rule):
                found.append(f"{probe.tool}: {stranger} gets {theirs}, not {probe.rule}")
    after = await outcome(client, resource.read, OWNER_A, id)
    if after != before:
        found.append(f"{resource.name}: the strangers changed A's data: {before} became {after}")
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
