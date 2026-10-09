"""Building UCP error responses for business outcomes.

A "no" from the business (out of stock, not found, a step that ran out of time) is a normal tool
result carrying `ucp.status: "error"` and `messages[]`. Only protocol failures (a bad profile, a
malformed call) are JSON-RPC errors, and those don't go through here.
"""

from tillhand.core.constants import UCP_VERSION
from tillhand.models.ucp import (
    ErrorResponse,
    MessageError,
    ProfileErrorCode,
    ProfileErrorData,
    ProtocolErrorData,
    Severity,
    UcpResponseMeta,
)

UPSTREAM_TIMEOUT = "upstream_timeout"

UCP_DISCOVERY_FAILED = -32001
"""The JSON-RPC code for every profile failure (overview, "Error Codes"). `error.data.code` says which."""


class RequestTooLarge(ValueError):
    """A request over one of our batch limits. Not a business outcome: the MCP layer maps it to
    JSON-RPC `-32602` (Invalid params), as catalog/mcp.md requires for oversized lookups."""


UCP_PROTOCOL_ERROR = -32000
"""The JSON-RPC code for UCP's other protocol errors, such as 409 and 503 (overview, "Error Codes")."""

RETRY_AFTER_SECONDS = 5


class IdempotencyConflict(Exception):
    """An idempotency key reused with a different request: refused, not replayed (#30 decision 11).

    A protocol failure, not a business "no": JSON-RPC `-32000` (REST would say 409), with
    `error.data.code` `idempotency_key_reused`. The caller must use a fresh key for a new request."""

    message = "Idempotency key reused with a different request"

    def data(self) -> ProtocolErrorData:
        return ProtocolErrorData(code="idempotency_key_reused")


class ServiceUnavailable(Exception):
    """A store a write depends on can't be reached, so the write is refused rather than risked: it fails
    closed (#30 decision 11). JSON-RPC `-32000` (REST would say 503), with `error.data.retry_after`."""

    message = "Service unavailable"

    def __init__(self, step: str) -> None:
        super().__init__(f"{step} is unavailable")
        self.step = step

    def data(self) -> ProtocolErrorData:
        return ProtocolErrorData(code="service_unavailable", retry_after=RETRY_AFTER_SECONDS)


class UnknownProfile(ValueError):
    """A Merchant API key was asked for a profile that isn't pre-registered in the `platforms` table."""


class Unauthorized(Exception):
    """The Merchant door's key is missing, wrong or revoked: the same answer for all three, so trying keys
    reveals nothing (#11 decision 3). HTTP 401 with JSON-RPC `-32000` "Unauthorized", as UCP's example."""

    message = "Unauthorized"


class ProfileError(Exception):
    """The Platform's profile can't be used: a protocol failure, not a business "no".

    The MCP layer answers it with JSON-RPC `-32001` and `ProfileErrorData` (overview, "Error Codes"):
    `invalid_profile_url` (missing, malformed, not https, or resolving to a special-use address),
    `profile_unreachable` (the fetch failed, timed out, redirected or answered non-2xx) or
    `profile_malformed` (not JSON, too large, or not a valid Platform profile).
    """

    def __init__(self, code: ProfileErrorCode, content: str) -> None:
        super().__init__(f"{code}: {content}")
        self.code: ProfileErrorCode = code
        self.content = content

    def data(self) -> ProfileErrorData:
        return ProfileErrorData(code=self.code, content=self.content)


def business_error(
    *,
    code: str,
    content: str,
    severity: Severity,
    path: str | None = None,
    continue_url: str | None = None,
) -> ErrorResponse:
    return ErrorResponse(
        ucp=UcpResponseMeta(version=UCP_VERSION, status="error"),
        messages=[MessageError(code=code, content=content, severity=severity, path=path)],
        continue_url=continue_url,
    )


def timeout_error(step: str) -> ErrorResponse:
    """The deadline rule's failure: say which step overran, and that trying again may work."""
    return business_error(
        code=UPSTREAM_TIMEOUT,
        content=f"Timed out at {step}. Try again.",
        severity="recoverable",
    )
