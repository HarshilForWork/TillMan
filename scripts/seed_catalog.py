"""Load a canonical JSON catalog file into this deployment's database, and embed what changed.

    uv run python scripts/seed_catalog.py data/seeds/skincare.json
    uv run python scripts/seed_catalog.py data/seeds/skincare.json --no-embed

Run `uv run alembic upgrade head` first. Safe to re-run: unchanged Products keep their embeddings,
and Products missing from the file are marked discontinued, never deleted.
"""

import argparse
import asyncio
from pathlib import Path

import httpx2

from tillhand.core.config import get_settings
from tillhand.integrations.neon.catalog import NeonCatalogStore
from tillhand.integrations.neon.pool import create_pool
from tillhand.integrations.pinecone import PineconeEmbedder
from tillhand.models.domain import Catalog
from tillhand.services.catalog_sync import sync_catalog


async def main(path: Path, *, embed: bool) -> None:
    catalog = Catalog.model_validate_json(await asyncio.to_thread(path.read_bytes))
    counts = f"{len(catalog.products)} products, {sum(len(p.variants) for p in catalog.products)} variants"
    print(f"{catalog.merchant}: {counts}, {len(catalog.bundles)} bundles")
    settings = get_settings()
    pool = await create_pool(settings, max_size=2)
    try:
        async with httpx2.AsyncClient(timeout=20.0) as client:
            embedder = (
                PineconeEmbedder(
                    client,
                    api_key=settings.pinecone_api_key,
                    model=settings.embedding_model,
                    dimension=settings.embedding_dimension,
                )
                if embed
                else None
            )
            report = await sync_catalog(catalog, NeonCatalogStore(pool), embedder)
    finally:
        await pool.close()
    print(f"synced: {report.catalog.model_dump()}")
    print(f"embedded {len(report.embedded)} products")
    if report.stale:
        print(f"no embedding yet (embedding skipped): {len(report.stale)} products")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("catalog", type=Path, help="a canonical JSON catalog file")
    parser.add_argument("--no-embed", action="store_true", help="sync the catalog without calling Pinecone")
    args = parser.parse_args()
    asyncio.run(main(args.catalog, embed=not args.no_embed))
