"""A Platform's UCP profile (`profile.json#/$defs/platform_schema`), and the JSON-RPC error for a bad one.

A Platform names its profile URL on every request (`meta.ucp-agent.profile`), and the business MUST
fetch and validate it (overview, "Business Requirements"). These models are that validation: a
document they accept is one the vendored schema accepts, which the tests check case by case.

Everything here is `Open`: profiles are extensible, and a member we don't model is kept, not refused.
"""

from typing import Literal, Self

from pydantic import model_validator

from .common import (
    CapabilityEntry,
    Closed,
    Entity,
    Open,
    PaymentHandlerEntry,
    ReverseDomainName,
    ServiceEntry,
    UcpVersion,
    Url,
)

_PRIVATE_JWK_MEMBERS = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth", "k"})
_CURVE_ALGORITHM = {"P-256": "ES256", "P-384": "ES384", "Ed25519": "EdDSA"}


class JwkPublicKey(Open):
    """A public signing key. `kty`, `crv` and `alg` are open vocabularies: an unknown key type is fine."""

    kid: str
    kty: str
    crv: str | None = None
    x: str | None = None
    y: str | None = None
    alg: str | None = None
    use: str | None = None

    @model_validator(mode="after")
    def _well_formed(self) -> Self:
        if private := sorted(_PRIVATE_JWK_MEMBERS & set(self.model_extra or {})):
            raise ValueError(f"key {self.kid!r} carries private key material: {private}")
        if self.kty == "EC" and None in (self.crv, self.x, self.y):
            raise ValueError(f"EC key {self.kid!r} needs crv, x and y")
        if self.kty == "OKP" and None in (self.crv, self.x):
            raise ValueError(f"OKP key {self.kid!r} needs crv and x")
        expected = _CURVE_ALGORITHM.get(self.crv or "")
        if expected is not None and self.alg is not None and self.alg != expected:
            raise ValueError(f"key {self.kid!r}: curve {self.crv} pairs with {expected}, not {self.alg}")
        return self


def _require(entity: Entity, *fields: str) -> None:
    """The platform schema makes some optional `Entity` fields required; `schema_` is `schema` on the wire."""
    if missing := [f.rstrip("_") for f in fields if getattr(entity, f) is None]:
        raise ValueError(f"a platform profile entry needs {', '.join(missing)}")


class PlatformService(ServiceEntry):
    """Every transport needs `spec`; all but `a2a` also need `schema`."""

    @model_validator(mode="after")
    def _platform_fields(self) -> Self:
        _require(self, "spec", *(() if self.transport == "a2a" else ("schema_",)))
        return self


class PlatformCapability(CapabilityEntry):
    @model_validator(mode="after")
    def _platform_fields(self) -> Self:
        _require(self, "spec", "schema_")
        return self


class PlatformPaymentHandler(PaymentHandlerEntry):
    @model_validator(mode="after")
    def _platform_fields(self) -> Self:
        _require(self, "spec", "schema_")
        # `available_payment_instrument.json`: each instrument names its `type`.
        for instrument in self.available_instruments or []:
            if not isinstance((instrument.model_extra or {}).get("type"), str):
                raise ValueError(f"payment handler {self.id!r} lists an instrument without a type")
        return self


class PlatformUcp(Open):
    version: UcpVersion
    status: Literal["success", "error"] | None = None
    services: dict[ReverseDomainName, list[PlatformService]]
    capabilities: dict[ReverseDomainName, list[PlatformCapability]] | None = None
    payment_handlers: dict[ReverseDomainName, list[PlatformPaymentHandler]]
    map_order: dict[str, list[str]] | None = None


class PlatformProfile(Open):
    ucp: PlatformUcp
    keys: list[JwkPublicKey] | None = None


ProfileErrorCode = Literal["invalid_profile_url", "profile_unreachable", "profile_malformed"]


class ProfileErrorData(Closed):
    """`error.data` of the JSON-RPC `-32001` a discovery failure answers with (overview, "Error Codes")."""

    code: ProfileErrorCode
    content: str
    continue_url: Url | None = None
