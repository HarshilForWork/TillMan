"""Load the pre-approved Platform registry file into this deployment's `platforms` table (#37, #41).

    uv run python scripts/seed_platforms.py
    uv run python scripts/seed_platforms.py data/platforms.json

Run `uv run alembic upgrade head` first. The file is validated in full before anything is written, and each
profile document is stored with the same content as the file (keys, values and order; only re-spaced).
Safe to re-run: existing Platforms are updated, and a Merchant assistant's key-bound flag is never cleared.
The app reads the profiles at startup, so restart it to serve a new Platform. Key-binding is read afresh on
every call, so it never needs a restart.
"""

import argparse
import asyncio
from pathlib import Path

from tillhand.core.config import get_settings
from tillhand.integrations.neon.merchant import NeonPlatformStore
from tillhand.integrations.neon.pool import create_pool
from tillhand.services.profiles import registry_entries

DEFAULT_REGISTRY = Path("data/platforms.json")


async def main(path: Path) -> None:
    entries = registry_entries(await asyncio.to_thread(path.read_bytes))
    pool = await create_pool(get_settings(), max_size=2)
    try:
        count = await NeonPlatformStore(pool).upsert(entries)
    finally:
        await pool.close()
    print(f"seeded {count} pre-approved Platforms: {', '.join(p.profile_url for p, _ in entries)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("registry", type=Path, nargs="?", default=DEFAULT_REGISTRY, help="a registry file")
    asyncio.run(main(parser.parse_args().registry))
