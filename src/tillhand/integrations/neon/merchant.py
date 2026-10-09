"""The Merchant door's SQL (#41): pre-approved Platforms, Merchant API keys and Customers.

None of this is owned data (it belongs to the Merchant's deployment, not to a Customer or Platform), so it
takes no `Owner`. Every request on the Merchant door makes one key lookup, and one Customer lookup when it
names a Customer; both are single round trips through a unique index.
"""

import uuid

import asyncpg

from tillhand.core.errors import UnknownProfile
from tillhand.integrations.neon.pool import Pool, connection
from tillhand.models.db import MerchantKey, RegisteredPlatform
from tillhand.models.domain import PreApprovedPlatform
from tillhand.models.ucp import PlatformProfile

_KEY_COLUMNS = "id, prefix, label, profile_url, created_at, revoked_at"

# Find the Customer the Merchant knows by $1, or make one. A concurrent first sight of the same id makes
# the insert do nothing, and the final select finds the row the other request made.
_CUSTOMER = """
with made as (
    insert into customers (merchant_customer_id) values ($1)
    on conflict (merchant_customer_id) do nothing
    returning id
)
select id from made
union all
select id from customers where merchant_customer_id = $1
limit 1
"""


def _key(row: asyncpg.Record) -> MerchantKey:
    return MerchantKey(**dict(row))


class NeonMerchantStore:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def add_key(self, *, key_hash: str, prefix: str, label: str, profile_url: str) -> MerchantKey:
        """Store a new key's hash for a pre-registered profile, and mark that profile key-bound."""
        async with connection(self._pool, "neon.add_key") as conn, conn.transaction():
            bound = await conn.fetchval(
                "update platforms set key_bound = true, updated_at = now() "
                "where profile_url = $1 returning 1",
                profile_url,
            )
            if bound is None:
                raise UnknownProfile(profile_url)
            row = await conn.fetchrow(
                "insert into merchant_api_keys (key_hash, prefix, label, profile_url) "
                f"values ($1, $2, $3, $4) returning {_KEY_COLUMNS}",
                key_hash,
                prefix,
                label,
                profile_url,
            )
        if row is None:
            raise RuntimeError("INSERT ... RETURNING returned no row")
        return _key(row)

    async def revoke_key(self, id: uuid.UUID) -> bool:
        async with connection(self._pool, "neon.revoke_key") as conn:
            revoked = await conn.fetchval(
                "update merchant_api_keys set revoked_at = now() "
                "where id = $1 and revoked_at is null returning id",
                id,
            )
        return revoked is not None

    async def active_key(self, key_hash: str) -> MerchantKey | None:
        async with connection(self._pool, "neon.active_key") as conn:
            row = await conn.fetchrow(
                f"select {_KEY_COLUMNS} from merchant_api_keys where key_hash = $1 and revoked_at is null",
                key_hash,
            )
        return None if row is None else _key(row)

    async def list_keys(self) -> list[MerchantKey]:
        """Every key, newest first, for the admin scripts. Bounded: a Merchant has a handful."""
        async with connection(self._pool, "neon.list_keys") as conn:
            rows = await conn.fetch(
                f"select {_KEY_COLUMNS} from merchant_api_keys order by created_at desc limit 100"
            )
        return [_key(row) for row in rows]

    async def customer_id(self, merchant_customer_id: str) -> uuid.UUID:
        async with connection(self._pool, "neon.customer_id") as conn:
            id = await conn.fetchval(_CUSTOMER, merchant_customer_id)
        if id is None:
            raise RuntimeError(f"no Customer row for {merchant_customer_id!r} after making one")
        return id


class NeonPlatformStore:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def platforms(self) -> list[RegisteredPlatform]:
        """Every pre-approved Platform, its profile re-validated against UCP's schema as it is read.

        A row that no longer validates raises, so the app refuses to start rather than serving it."""
        async with connection(self._pool, "neon.platforms") as conn:
            rows = await conn.fetch(
                "select profile_url, note, profile, key_bound from platforms order by profile_url limit 1000"
            )
        return [
            RegisteredPlatform(
                profile_url=row["profile_url"],
                note=row["note"],
                profile=PlatformProfile.model_validate_json(row["profile"]),
                key_bound=row["key_bound"],
            )
            for row in rows
        ]

    async def requires_key(self, url: str) -> bool:
        """Asked on every public-door call: one lookup through the primary key."""
        async with connection(self._pool, "neon.key_bound") as conn:
            bound = await conn.fetchval("select key_bound from platforms where profile_url = $1", url)
        return bool(bound)

    async def upsert(self, platforms: list[tuple[PreApprovedPlatform, str]]) -> int:
        """Add or update pre-approved Platforms: each with the profile document's own JSON text. A Platform's
        `key_bound` flag is left as it is: only issuing a key sets it."""
        async with connection(self._pool, "neon.upsert_platforms") as conn, conn.transaction():
            await conn.executemany(
                """
                insert into platforms (profile_url, note, profile) values ($1, $2, $3)
                on conflict (profile_url) do update
                    set note = excluded.note, profile = excluded.profile, updated_at = now()
                """,
                [(p.profile_url, p.note, document) for p, document in platforms],
            )
        return len(platforms)
