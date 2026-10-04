"""The Platform profile model accepts what UCP's `profile.json#/$defs/platform_schema` accepts, and refuses
what it refuses. The schema is the source of truth for every case: each one is checked against it first."""

import copy
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from tests.support.ucp_spec import overview_examples, schema_errors
from tillhand.models.ucp import PlatformProfile

PLATFORM_EXAMPLES = [
    e for e in overview_examples() if e.schema == "profile" and e.definition == "platform_schema"
]


def spec_profile() -> dict[str, Any]:
    (example,) = PLATFORM_EXAMPLES
    return copy.deepcopy(example.payload)


def test_the_spec_platform_profile_round_trips() -> None:
    payload = spec_profile()
    assert schema_errors(payload, "profile", "platform_schema") == [], "harness: spec example invalid"

    parsed = PlatformProfile.model_validate(payload)

    assert parsed.model_dump(mode="json", by_alias=True, exclude_unset=True) == payload


def _drop(path: str) -> Callable[[dict[str, Any]], None]:
    def edit(doc: dict[str, Any]) -> None:
        *parents, last = path.split("/")
        node: Any = doc
        for key in parents:
            node = node[int(key)] if key.isdigit() else node[key]
        del node[last]

    return edit


def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def edit(doc: dict[str, Any]) -> None:
        *parents, last = path.split("/")
        node: Any = doc
        for key in parents:
            node = node[int(key)] if key.isdigit() else node[key]
        node[int(last) if last.isdigit() else last] = value

    return edit


SERVICE = "ucp/services/dev.ucp.shopping/0"
CAPABILITY = "ucp/capabilities/dev.ucp.shopping.checkout/0"
HANDLER = "ucp/payment_handlers/com.google.pay/0"
KEY = "keys/0"

REFUSED = {
    "no ucp": _drop("ucp"),
    "no ucp.version": _drop("ucp/version"),
    "a malformed ucp.version": _set("ucp/version", "26-08-25"),
    "no services": _drop("ucp/services"),
    "no payment_handlers": _drop("ucp/payment_handlers"),
    "a service without spec": _drop(f"{SERVICE}/spec"),
    "an rest service without schema": _drop(f"{SERVICE}/schema"),
    "an unknown transport": _set(f"{SERVICE}/transport", "carrier-pigeon"),
    "a service name that isn't reverse-domain": _set("ucp/services/Shopping", []),
    "a capability without schema": _drop(f"{CAPABILITY}/schema"),
    "a capability without spec": _drop(f"{CAPABILITY}/spec"),
    "a payment handler without id": _drop(f"{HANDLER}/id"),
    "a payment handler without schema": _drop(f"{HANDLER}/schema"),
    "an instrument without type": _drop(
        "ucp/payment_handlers/dev.shopify.shop_pay/0/available_instruments/0/type"
    ),
    "a key without kid": _drop(f"{KEY}/kid"),
    "an EC key without y": _drop(f"{KEY}/y"),
    "a P-256 key claiming ES384": _set(f"{KEY}/alg", "ES384"),
    "a private key member": _set(f"{KEY}/d", "c2VjcmV0"),
}


@pytest.mark.parametrize("edit", REFUSED.values(), ids=REFUSED.keys())
def test_a_profile_the_schema_refuses_is_refused(edit: Callable[[dict[str, Any]], None]) -> None:
    payload = spec_profile()
    edit(payload)
    assert schema_errors(payload, "profile", "platform_schema") != [], "harness: the schema accepts this"

    with pytest.raises(ValidationError):
        PlatformProfile.model_validate(payload)


ACCEPTED = {
    "an a2a service without schema": _set(
        "ucp/services/dev.ucp.shopping/0",
        {"version": "2026-08-25", "spec": "https://a.example/s", "transport": "a2a"},
    ),
    "no capabilities": _drop("ucp/capabilities"),
    "no keys": _drop("keys"),
    "an unknown key type": _set(KEY, {"kid": "k1", "kty": "RSA", "n": "AQAB", "e": "AQAB"}),
    "an OKP key": _set(
        KEY, {"kid": "k2", "kty": "OKP", "crv": "Ed25519", "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"}
    ),
    "an unknown top-level member": _set("x-platform-note", "hello"),
}


@pytest.mark.parametrize("edit", ACCEPTED.values(), ids=ACCEPTED.keys())
def test_a_profile_the_schema_accepts_is_accepted(edit: Callable[[dict[str, Any]], None]) -> None:
    payload = spec_profile()
    edit(payload)
    assert schema_errors(payload, "profile", "platform_schema") == [], "harness: the schema refuses this"

    PlatformProfile.model_validate(payload)
