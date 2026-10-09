"""Issue a Merchant API key for the Merchant door (#41). The key is printed once and never stored.

    uv run python scripts/issue_key.py --profile https://tillhand.vercel.app/profiles/demo-assistant.json \
        --label "Demo assistant, website"
    uv run python scripts/issue_key.py --list

The profile must already be a pre-approved Platform (`scripts/seed_platforms.py`), loaded by the running app.
Issuing its first key makes it key-bound: from that moment it works only on the Merchant door, with a key,
and the public door refuses it on the very next call (no restart). To rotate, issue a new key, switch the
assistant to it, then revoke the old one (`scripts/revoke_key.py`); both work in between.
"""

import argparse
import asyncio

from tillhand.core.config import get_settings
from tillhand.core.errors import UnknownProfile
from tillhand.integrations.neon.merchant import NeonMerchantStore
from tillhand.integrations.neon.pool import create_pool
from tillhand.services.merchant_door import issue_key


async def main(*, profile: str | None, label: str | None, list_only: bool) -> None:
    pool = await create_pool(get_settings(), max_size=2)
    try:
        store = NeonMerchantStore(pool)
        if list_only:
            for key in await store.list_keys():
                state = f"revoked {key.revoked_at:%Y-%m-%d %H:%M}" if key.revoked_at else "active"
                print(f"{key.id}  {key.prefix}…  {state:<22}  {key.profile_url}  {key.label}")
            return
        if profile is None or label is None:
            raise SystemExit("--profile and --label are required to issue a key")
        try:
            key, stored = await issue_key(store, label=label, profile_url=profile)
        except UnknownProfile:
            raise SystemExit(f"{profile} isn't a pre-approved Platform: seed it first") from None
    finally:
        await pool.close()
    print(f"key id:  {stored.id}")
    print(f"profile: {stored.profile_url}")
    print(f"key:     {key}")
    print("Store the key now: it is shown only this once. Send it as the TillHand-Api-Key header.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--profile", help="the pre-approved profile URL the key acts as")
    parser.add_argument("--label", help="who or what holds the key, for the list")
    parser.add_argument("--list", action="store_true", help="list every key (prefixes only) and exit")
    args = parser.parse_args()
    asyncio.run(main(profile=args.profile, label=args.label, list_only=args.list))
