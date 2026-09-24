"""Business "no" outcomes are UCP error responses: normal results, never exceptions."""

from tests.support.ucp_spec import schema_errors
from tillhand.core.constants import UCP_VERSION
from tillhand.core.errors import business_error, timeout_error
from tillhand.models.ucp import ucp_dump


def test_business_error_is_a_ucp_error_response() -> None:
    error = business_error(
        code="out_of_stock",
        content="Niacinamide Serum 50ml is out of stock.",
        severity="recoverable",
        continue_url="https://shop.example/products/serum",
    )
    payload = ucp_dump(error)
    assert payload["ucp"] == {"version": UCP_VERSION, "status": "error"}
    assert payload["messages"][0]["code"] == "out_of_stock"
    assert schema_errors(payload, "common/types/error_response") == []


def test_timeout_error_names_the_step_that_overran() -> None:
    payload = ucp_dump(timeout_error("embedding.query"))
    message = payload["messages"][0]
    assert message["code"] == "upstream_timeout"
    assert message["severity"] == "recoverable"
    assert "embedding.query" in message["content"]
    assert schema_errors(payload, "common/types/error_response") == []
