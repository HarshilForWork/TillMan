"""Revoke a Merchant API key (#41). It stops working on its very next request; nothing needs restarting.

    uv run python scripts/revoke_key.py 3f0d3fd1-4d45-4cb5-b3a5-69e5237d0d6b

Find the key's id with `uv run python scripts/issue_key.py --list`.
"""

import argparse
import asyncio
import uuid

from tillhand.core.config import get_settings
from tillhand.integrations.neon.merchant import NeonMerchantStore
from tillhand.integrations.neon.pool import create_pool


async def main(id: uuid.UUID) -> None:
    pool = await create_pool(get_settings(), max_size=2)
    try:
        revoked = await NeonMerchantStore(pool).revoke_key(id)
    finally:
        await pool.close()
    print(f"revoked {id}" if revoked else f"no active key {id}: it doesn't exist, or was already revoked")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("id", type=uuid.UUID, help="the key's id (not the key)")
    asyncio.run(main(parser.parse_args().id))
