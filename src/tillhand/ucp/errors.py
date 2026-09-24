"""Building UCP error responses for business outcomes.

A "no" from the business (out of stock, not found, a step that ran out of time) is a normal tool
result carrying `ucp.status: "error"` and `messages[]`. Only protocol failures (a bad profile, a
malformed call) are JSON-RPC errors, and those don't go through here.
"""

from .common import ErrorResponse, MessageError, Severity, UcpResponseMeta
from .version import UCP_VERSION

UPSTREAM_TIMEOUT = "upstream_timeout"


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
