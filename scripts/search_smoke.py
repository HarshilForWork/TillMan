"""Run natural-language searches through the real catalog service (Pinecone + Neon) and print the top hits.

    uv run python scripts/search_smoke.py "serum for oily skin" "oily skin ke liye serum"
    uv run python scripts/search_smoke.py          # the default query set

A hand check that search returns sane results; the eval suite, not this, is the regression test.
"""

import asyncio
import sys
import time

import httpx2

from tillhand.core.config import get_settings
from tillhand.integrations.neon.catalog import NeonCatalogStore
from tillhand.integrations.neon.pool import create_pool
from tillhand.integrations.pinecone import PineconeEmbedder
from tillhand.models.ucp import ErrorResponse, RequestMeta, SearchCatalogArguments, SearchRequest
from tillhand.services.catalog import StoreCatalogService

DEFAULT_QUERIES = [
    "serum for oily skin",
    "oily skin ke liye serum",
    "something to fade dark spots",
    "sunscreen that doesn't leave a white cast",
    "gentle face wash for dry sensitive skin",
    "remove makeup",
    "anti-ageing night treatment",
    "moisturiser for humid weather",
]


async def main(queries: list[str]) -> None:
    settings = get_settings()
    pool = await create_pool(settings, max_size=2)
    meta = RequestMeta.model_validate({"ucp-agent": {"profile": "https://example.com/smoke-test-profile"}})
    try:
        async with httpx2.AsyncClient(timeout=10.0) as client:
            embedder = PineconeEmbedder(
                client,
                api_key=settings.pinecone_api_key,
                model=settings.embedding_model,
                dimension=settings.embedding_dimension,
            )
            service = StoreCatalogService(NeonCatalogStore(pool), embedder)
            for query in queries:
                started = time.perf_counter()
                result = await service.search_catalog(
                    SearchCatalogArguments(meta=meta, catalog=SearchRequest(query=query))
                )
                elapsed = (time.perf_counter() - started) * 1000
                print(f"\n{query!r}  ({elapsed:.0f} ms)")
                if isinstance(result, ErrorResponse):
                    print(f"  error: {[m.content for m in result.messages]}")
                    continue
                for product in result.products[:3]:
                    available = sum(
                        1 for v in product.variants if v.availability and v.availability.available
                    )
                    print(f"  {product.title}  [{available}/{len(product.variants)} variants available]")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or DEFAULT_QUERIES))
