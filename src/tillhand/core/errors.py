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
    Severity,
    UcpResponseMeta,
)

UPSTREAM_TIMEOUT = "upstream_timeout"

UCP_DISCOVERY_FAILED = -32001
"""The JSON-RPC code for every profile failure (overview, "Error Codes"). `error.data.code` says which."""


class RequestTooLarge(ValueError):
    """A request over one of our batch limits. Not a business outcome: the MCP layer maps it to
    JSON-RPC `-32602` (Invalid params), as catalog/mcp.md requires for oversized lookups."""


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
