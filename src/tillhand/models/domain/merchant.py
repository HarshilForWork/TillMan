"""Who is calling on the Merchant door (#41): the Merchant assistant, proven by its key, and the Customer
it vouches for, if any."""

import uuid

from pydantic import BaseModel, ConfigDict

from .owner import Owner


class MerchantDoorHeaders(BaseModel):
    """The Merchant door's request headers, read once at the HTTP boundary (#41)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: str | None
    """`TillHand-Api-Key`. Never logged, never stored."""
    customer_ref: str | None
    """`TillHand-Customer`: the Merchant's own id for the Customer."""
    has_customer_token: bool
    """Whether an `Authorization` header (a Customer token, #44) came too."""


class MerchantCaller(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key_id: uuid.UUID
    """Logged on every call, so each request can be traced to the key that made it."""
    profile_url: str
    """The key's pre-registered profile. It fills `meta.ucp-agent.profile`, hiding which door was used."""
    customer: uuid.UUID | None
    """The Customer named by `TillHand-Customer`, resolved to our own id; `None` for a guest."""

    def owner(self) -> Owner:
        """The key's profile plus the Customer (owner's decision), so rotating keys keeps every Cart."""
        return Owner(
            platform=self.profile_url, customer=None if self.customer is None else str(self.customer)
        )
