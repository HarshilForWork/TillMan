"""Our UCP models round-trip the spec's own examples and scaffolds, and their output passes UCP's schemas."""

from typing import Any

import pytest
from pydantic import BaseModel

from tillhand.ucp import (
    ErrorResponse,
    GetProductRequest,
    GetProductResponse,
    LookupRequest,
    LookupResponse,
    SearchRequest,
    SearchResponse,
)

from .spec import SpecExample, catalog_doc_examples, scaffold, schema_errors

# (schema, op, direction) -> (our model, the schema $def it must satisfy)
CONTRACTS: dict[tuple[str, str, str], tuple[type[BaseModel], str | None]] = {
    ("shopping/catalog_search", "search", "request"): (SearchRequest, "search_request"),
    ("shopping/catalog_search", "search", "response"): (SearchResponse, "search_response"),
    ("shopping/catalog_lookup", "lookup", "request"): (LookupRequest, "lookup_request"),
    ("shopping/catalog_lookup", "lookup", "response"): (LookupResponse, "lookup_response"),
    ("shopping/catalog_lookup", "get_product", "request"): (GetProductRequest, "get_product_request"),
    ("shopping/catalog_lookup", "get_product", "response"): (GetProductResponse, "get_product_response"),
    ("common/types/error_response", "read", "response"): (ErrorResponse, None),
}

EXAMPLES = [e for e in catalog_doc_examples() if (e.schema, e.op, e.direction) in CONTRACTS]


def as_parsed(model: BaseModel) -> Any:
    """Dump exactly the fields the payload set, so a round trip can be compared field for field."""
    return model.model_dump(mode="json", by_alias=True, exclude_unset=True)


def test_the_catalog_pages_yield_examples_for_every_contract() -> None:
    covered = {(e.schema, e.op, e.direction) for e in EXAMPLES}
    assert covered == set(CONTRACTS), f"no complete spec example for: {set(CONTRACTS) - covered}"


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda e: e.label)
def test_spec_example_round_trips_through_our_model(example: SpecExample) -> None:
    model, definition = CONTRACTS[(example.schema, example.op, example.direction)]
    assert schema_errors(example.payload, example.schema, definition) == [], "harness: spec example invalid"

    parsed = model.model_validate(example.payload)

    assert as_parsed(parsed) == example.payload
    assert schema_errors(as_parsed(parsed), example.schema, definition) == []


def _scaffold_name(schema: str, op: str, direction: str) -> str:
    """UCP's scaffold file naming: `<schema path with _>_<direction>[_<op>]`.

    One exception in UCP's own tree: the common error response's scaffold is filed under `shopping_types`.
    """
    stem = {"common/types/error_response": "shopping_types_error_response"}.get(
        schema, schema.replace("/", "_")
    )
    base = f"{stem}_{direction}"
    return base if op in ("read", "search", "lookup") else f"{base}_{op}"


SCAFFOLDS = [(_scaffold_name(*key), key) for key in CONTRACTS]


@pytest.mark.parametrize(("name", "key"), SCAFFOLDS, ids=[name for name, _ in SCAFFOLDS])
def test_spec_scaffold_round_trips_through_our_model(name: str, key: tuple[str, str, str]) -> None:
    model, definition = CONTRACTS[key]
    schema = key[0]
    payload = scaffold(name)
    parsed = model.model_validate(payload)
    assert as_parsed(parsed) == payload
    assert schema_errors(as_parsed(parsed), schema, definition) == []
