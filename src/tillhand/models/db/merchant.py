"""What the Merchant-door queries return (#41)."""

import uuid
from datetime import datetime

from tillhand.models.db.catalog import Row
from tillhand.models.ucp import PlatformProfile


class RegisteredPlatform(Row):
    """A pre-approved Platform, as the `platforms` table holds it, its profile already validated."""

    profile_url: str
    note: str
    profile: PlatformProfile
    key_bound: bool
    """A Merchant assistant's profile: usable only on the Merchant door, with one of its keys."""


class MerchantKey(Row):
    """A Merchant API key, minus its secret: the database never holds the key itself, only its hash."""

    id: uuid.UUID
    prefix: str
    """The key's first characters (`thk_` and 8 more), so a person can tell keys apart in a list."""
    label: str
    profile_url: str
    """The one pre-registered profile this key acts as (#11 decision 3)."""
    created_at: datetime
    revoked_at: datetime | None
