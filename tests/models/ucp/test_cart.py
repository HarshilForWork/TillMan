"""The UCP Cart models against the vendored spec: its cart pages' examples, scaffolds and `cart.json` (#50).

The spec is the source of truth. Requests must accept what the spec's examples send; responses must accept
the spec's examples and produce only what `shopping/cart.json` accepts. The request examples aren't
checked against `cart.json` itself, because that schema describes the response: UCP derives request
schemas from its `ucp_request` annotations when it builds its docs.
"""

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from tests.support.ucp_spec import SpecExample, cart_doc_examples, scaffold, schema_errors
from tillhand.models.ucp import (
    MAX_CART_LINES,
    MAX_LINE_QUANTITY,
    CancelCartArguments,
    Cart,
    CartRequest,
    CreateCartArguments,
    ErrorResponse,
    GetCartArguments,
    UpdateCartArguments,
    ucp_dump,
)

CONTRACTS: dict[tuple[str, str, str], type[BaseModel]] = {
    ("shopping/cart", "create", "request"): CartRequest,
    ("shopping/cart", "read", "response"): Cart,
    ("common/types/error_response", "read", "response"): ErrorResponse,
}

EXAMPLES = [
    e for e in cart_doc_examples() if (e.schema, e.op, e.direction) in CONTRACTS and e.definition is None
]

TOOL_CALLS = [e for e in cart_doc_examples() if e.schema == "transports/mcp_tool_call"]

ARGUMENTS: dict[str, type[BaseModel]] = {
    "create_cart": CreateCartArguments,
    "get_cart": GetCartArguments,
    "update_cart": UpdateCartArguments,
    "cancel_cart": CancelCartArguments,
}

META = {"ucp-agent": {"profile": "https://platform.example/profiles/agent.json"}}
KEY = "660e8400-e29b-41d4-a716-446655440001"


def as_parsed(model: BaseModel) -> Any:
    return model.model_dump(mode="json", by_alias=True, exclude_unset=True)


def test_the_cart_pages_yield_examples_for_every_contract() -> None:
    assert {(e.schema, e.op, e.direction) for e in EXAMPLES} == set(CONTRACTS)
    assert {e.payload["params"]["name"] for e in TOOL_CALLS} == {"get_cart", "update_cart", "cancel_cart"}


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda e: e.label)
def test_spec_example_round_trips_through_our_model(example: SpecExample) -> None:
    model = CONTRACTS[(example.schema, example.op, example.direction)]
    if example.direction == "response":
        assert schema_errors(example.payload, example.schema) == [], "harness: spec example invalid"

    parsed = model.model_validate(example.payload)

    assert as_parsed(parsed) == example.payload


@pytest.mark.parametrize("example", TOOL_CALLS, ids=lambda e: e.payload["params"]["name"])
def test_the_spec_tool_calls_parse_as_our_arguments(example: SpecExample) -> None:
    params = example.payload["params"]

    parsed = ARGUMENTS[params["name"]].model_validate(params["arguments"])

    assert as_parsed(parsed) == params["arguments"]


def test_the_scaffolds_parse_and_our_cart_passes_the_schema() -> None:
    CartRequest.model_validate(scaffold("shopping_cart_request_create"))
    CartRequest.model_validate(scaffold("shopping_cart_request_update"))
    cart = Cart.model_validate(scaffold("shopping_cart_response"))

    assert schema_errors(ucp_dump(cart), "shopping/cart") == []


def test_cancel_cart_requires_an_idempotency_key_that_is_a_uuid() -> None:
    CancelCartArguments.model_validate({"meta": {**META, "idempotency-key": KEY}, "id": "c"})

    with pytest.raises(ValidationError, match="idempotency-key"):
        CancelCartArguments.model_validate({"meta": META, "id": "c"})
    with pytest.raises(ValidationError, match="UUID"):
        CancelCartArguments.model_validate({"meta": {**META, "idempotency-key": "retry-1"}, "id": "c"})


def test_a_cart_in_a_request_carries_no_id() -> None:
    """MCP binding: the target is the top-level `id`, and the `cart` payload MUST NOT contain one."""
    with pytest.raises(ValidationError, match="id"):
        CartRequest.model_validate({"id": "cart_1", "line_items": []})


def test_a_cart_request_is_bounded_in_lines_and_quantity() -> None:
    line = {"item": {"id": "var_x"}, "quantity": MAX_LINE_QUANTITY}
    CartRequest.model_validate({"line_items": [line] * MAX_CART_LINES})

    with pytest.raises(ValidationError):
        CartRequest.model_validate({"line_items": [line] * (MAX_CART_LINES + 1)})
    with pytest.raises(ValidationError):
        CartRequest.model_validate({"line_items": [{**line, "quantity": MAX_LINE_QUANTITY + 1}]})
    with pytest.raises(ValidationError):
        CartRequest.model_validate({"line_items": [{**line, "quantity": 0}]})


def test_cart_totals_need_exactly_one_subtotal_and_one_total() -> None:
    cart = scaffold("shopping_cart_response")

    with pytest.raises(ValidationError, match="subtotal"):
        Cart.model_validate({**cart, "totals": [{"type": "total", "amount": 1000}]})
