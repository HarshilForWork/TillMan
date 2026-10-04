"""Print every active Product's Suggestions from the real database, and the similarities behind them.

    uv run --env-file .env python scripts/suggestions_smoke.py            # at the tuned floor
    uv run --env-file .env python scripts/suggestions_smoke.py 0.45       # at another floor

For each Product: its cross-category neighbours with their cosine similarity (no floor), then what
`get_suggestions` returns with only similarity to go on (Bundles left out), then the real answer.
This is how `SUGGESTION_SIMILARITY_FLOOR` was chosen (#46); the eval suite, not this, is the regression test.
"""

import asyncio
import sys

from tillhand.core.config import get_settings
from tillhand.core.constants import SUGGESTION_SIMILARITY_FLOOR
from tillhand.integrations.neon.catalog import NeonCatalogStore
from tillhand.integrations.neon.pool import create_pool
from tillhand.models.db import SuggestionSource
from tillhand.models.domain import category_key
from tillhand.models.ucp import (
    MAX_SUGGESTIONS,
    GetSuggestionsArguments,
    GetSuggestionsRequest,
    RequestMeta,
    SuggestionsResponse,
)
from tillhand.services.catalog import MAX_PAGE_SIZE
from tillhand.services.suggestions import StoreSuggestionService

META = RequestMeta.model_validate({"ucp-agent": {"profile": "https://example.com/smoke-test-profile"}})


class WithoutBundles(NeonCatalogStore):
    """The real store with every Bundle hidden, to see what similarity alone would suggest."""

    async def suggestion_sources(self, ids: list[str]) -> list[SuggestionSource]:
        return [s.model_copy(update={"bundles": []}) for s in await super().suggestion_sources(ids)]


async def main(floor: float) -> None:
    pool = await create_pool(get_settings(), max_size=2)
    try:
        store = NeonCatalogStore(pool)
        active = await store.search_products(
            query_vector=None,
            category_keys=None,
            price_min=None,
            price_max=None,
            limit=MAX_PAGE_SIZE,
            offset=0,
        )
        ids = [p.id for p in active]
        for source in await store.suggestion_sources(ids):
            keys = sorted({category_key(c) for c in source.categories})
            near = await store.similar_products(
                sources={source.id: keys}, exclude_ids=[], floor=-1.0, limit=20
            )
            print(f"\n{source.id}  ({', '.join(source.categories)})")
            print(
                "  cross-category similarity: "
                + ", ".join(f"{n.product.id.removeprefix('prod_')} {n.similarity:.3f}" for n in near)
            )
            for label, svc in (
                ("similar only", StoreSuggestionService(WithoutBundles(pool), floor=floor)),
                ("suggested   ", StoreSuggestionService(store, floor=floor)),
            ):
                result = await svc.get_suggestions(
                    GetSuggestionsArguments(
                        meta=META,
                        catalog=GetSuggestionsRequest(product_ids=[source.id], limit=MAX_SUGGESTIONS),
                    )
                )
                assert isinstance(result, SuggestionsResponse), result
                shown = ", ".join(
                    f"{s.id.removeprefix('prod_')} ({s.reason.kind})" for s in result.suggestions
                )
                print(f"  {label} @ {floor}: {shown or '-'}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 else SUGGESTION_SIMILARITY_FLOOR))
