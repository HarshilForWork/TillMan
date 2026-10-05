"""Who a piece of owned data belongs to: the Owner (#11 decisions 4 and 9, #42).

A Cart, Checkout or Order belongs to whoever created it, and only they may use it. The server builds an
Owner for every call to an owner-scoped tool:
- **On the public UCP door,** the Platform: the profile URL the call names (`meta.ucp-agent.profile`),
  checked before any tool runs. Until signatures land (#22) that URL is a claim, not proof, so ownership
  also rests on ids being unguessable (random UUIDs; see `integrations.neon`).
- **On the Merchant door (#41),** the Merchant API key's one pre-registered profile, plus the Customer the
  Merchant vouches for.

Every data function that touches owned data takes an Owner as a required argument, so pyright rejects a
call without one. Two Owners are the same only if both fields match: a guest Cart on a Platform isn't
visible to that Platform's signed-in Customers, nor theirs to it.
"""

from pydantic import BaseModel, ConfigDict, Field


class Owner(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: str = Field(min_length=1)
    """The profile URL of the Platform (or Merchant assistant) the data was created through."""

    customer: str | None = Field(default=None, min_length=1)
    """The Customer, when the server knows one (#41, #44); `None` for a guest.

    Stored as two plain columns wherever owned data lives (`owner_platform`, `owner_customer`)."""
