"""The two doors onto the same tools (#11, #41). A door decides who the caller is; the tools never know which
door a call came through.

- **The public door** (`/mcp`): a Platform names its profile in `meta.ucp-agent.profile`. That URL is a claim,
  checked as a profile (#37) but not proven. The caller is the Platform alone, a guest. A Merchant
  assistant's profile is refused here: it works only with its key (owner's decision).
- **The Merchant door** (`/merchant/mcp`): the Merchant assistant proved itself with its Merchant API key
  before the request reached MCP (`guards.MerchantDoorGuard`). Its key's profile replaces whatever
  `meta.ucp-agent` said, and the caller is that profile plus the Customer it vouched for.
"""

from typing import Any, Protocol

from mcp.shared.exceptions import MCPError

from tillhand.core.errors import RETRY_AFTER_SECONDS, UCP_PROTOCOL_ERROR
from tillhand.models.domain import MerchantCaller, Owner
from tillhand.models.ucp import ProtocolErrorData, RequestMeta, ucp_dump
from tillhand.services.profiles import ProfileResolver

MERCHANT_CALLER = "merchant_caller"
"""Where `MerchantDoorGuard` leaves the proven caller, in the request's state."""


class DoorRules(Protocol):
    def arguments(self, raw: dict[str, Any], request: Any) -> dict[str, Any]:
        """The tool call's arguments as the tools will see them."""
        ...

    async def admit(self, profile_url: str, profiles: ProfileResolver) -> None:
        """Refuse a profile this door won't serve, with a protocol error."""
        ...

    def owner(self, meta: RequestMeta, request: Any) -> Owner:
        """Who owns what this call creates or reads."""
        ...


class PublicDoorRules:
    def arguments(self, raw: dict[str, Any], request: Any) -> dict[str, Any]:
        return raw

    async def admit(self, profile_url: str, profiles: ProfileResolver) -> None:
        if await profiles.requires_key(profile_url):
            raise MCPError(
                code=UCP_PROTOCOL_ERROR,
                message="Unauthorized",
                data=ucp_dump(ProtocolErrorData(code="merchant_key_required")),
            )

    def owner(self, meta: RequestMeta, request: Any) -> Owner:
        return Owner(platform=meta.ucp_agent.profile)


def _caller(request: Any) -> MerchantCaller:
    caller = getattr(getattr(request, "state", None), MERCHANT_CALLER, None)
    if not isinstance(caller, MerchantCaller):
        # Unreachable through the app: the guard refuses every request without a valid key first.
        raise MCPError(code=UCP_PROTOCOL_ERROR, message="Unauthorized")
    return caller


class MerchantDoorRules:
    def arguments(self, raw: dict[str, Any], request: Any) -> dict[str, Any]:
        """The key's profile fills `meta.ucp-agent` (#9 decision 5), whatever the caller sent there."""
        sent = raw.get("meta")
        meta = dict(sent) if isinstance(sent, dict) else {}
        return {**raw, "meta": {**meta, "ucp-agent": {"profile": _caller(request).profile_url}}}

    async def admit(self, profile_url: str, profiles: ProfileResolver) -> None:
        """The key proved the profile; it must also be loaded, so it's never fetched (#37's seam). A key for a
        Platform seeded after startup is refused until the app restarts and loads it."""
        if not await profiles.is_pre_approved(profile_url):
            data = ProtocolErrorData(
                code="profile_not_loaded",
                content="This key's profile isn't loaded yet. Restart the server to load it.",
                retry_after=RETRY_AFTER_SECONDS,
            )
            raise MCPError(code=UCP_PROTOCOL_ERROR, message="Service unavailable", data=ucp_dump(data))

    def owner(self, meta: RequestMeta, request: Any) -> Owner:
        return _caller(request).owner()
