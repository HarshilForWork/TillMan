"""The seed catalogs, and in-memory fakes of the catalog store and embedders.

`FakeCatalogStore` answers with the same semantics as the SQL in `integrations/neon/catalog.py`
(filters, discontinued handling, id resolution), minus similarity: it keeps catalog order and records
the vector it was given. The real SQL is checked against Neon by the opt-in `neon` tests.
"""

from functools import cache
from pathlib import Path

from tillhand.core.deadlines import StepTimeout
from tillhand.integrations.pinecone import EmbeddingError
from tillhand.models.db import CatalogSyncResult, EmbeddingState, IdMatch, ProductEmbedding, ResolvedProduct
from tillhand.models.domain import Catalog, Product, category_prefixes
from tillhand.models.ucp import RequestMeta

SEEDS = Path(__file__).resolve().parents[2] / "data" / "seeds"
META = RequestMeta.model_validate({"ucp-agent": {"profile": "https://platform.example/profiles/agent.json"}})


@cache
def seed(name: str) -> Catalog:
    return Catalog.model_validate_json((SEEDS / f"{name}.json").read_bytes())


def product(catalog: Catalog, id: str) -> Product:
    return next(p for p in catalog.products if p.id == id)


class FakeCatalogStore:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.search_calls: list[dict[str, object]] = []

    async def search_products(
        self,
        *,
        query_vector: list[float] | None,
        category_keys: list[str] | None,
        price_min: int | None,
        price_max: int | None,
        limit: int,
        offset: int,
    ) -> list[Product]:
        self.search_calls.append({"query_vector": query_vector, "limit": limit, "offset": offset})

        def admitted(p: Product) -> bool:
            in_category = category_keys is None or bool(
                set(category_keys) & set(category_prefixes(p.categories))
            )
            in_price = any(
                (price_min is None or v.price >= price_min) and (price_max is None or v.price <= price_max)
                for v in p.variants
            )
            return not p.discontinued and in_category and in_price

        return [p for p in self.catalog.products if admitted(p)][offset : offset + limit]

    async def lookup_products(self, ids: list[str]) -> list[ResolvedProduct]:
        matches: dict[str, list[IdMatch]] = {}
        for input in dict.fromkeys(ids):
            for p in self.catalog.products:
                if input in (p.id, p.handle):
                    matches.setdefault(p.id, []).append(IdMatch(input=input, variant_id=None))
                for v in p.variants:
                    if input in (v.id, v.sku):
                        matches.setdefault(p.id, []).append(IdMatch(input=input, variant_id=v.id))
        return [
            ResolvedProduct(product=product(self.catalog, id), matches=sorted(found, key=lambda m: m.input))
            for id, found in matches.items()
        ]

    async def get_product(self, id: str) -> Product | None:
        return next(
            (p for p in self.catalog.products if id == p.id or id in {v.id for v in p.variants}), None
        )


class TimingOutStore(FakeCatalogStore):
    async def get_product(self, id: str) -> Product | None:
        raise StepTimeout("neon.get_product")


class FakeEmbedder:
    """Deterministic vectors; `fail` makes it raise instead, like Pinecone timing out or refusing."""

    model = "llama-text-embed-v2"

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.fail = fail
        self.queries: list[str] = []
        self.passages: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        if self.fail is not None:
            raise self.fail
        self.queries.append(text)
        return [float(len(text))] * 1024

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if self.fail is not None:
            raise self.fail
        self.passages.extend(texts)
        return [[float(i)] * 1024 for i, _ in enumerate(texts)]


EMBED_TIMEOUT = StepTimeout("pinecone.embed_query")
EMBED_REFUSED = EmbeddingError("Pinecone answered HTTP 401")


class FakeCatalogWriter:
    def __init__(self) -> None:
        self.sources: dict[str, str | None] = {}
        self.stored: list[ProductEmbedding] = []

    async def sync_catalog(self, catalog: Catalog) -> CatalogSyncResult:
        for p in catalog.products:
            self.sources.setdefault(p.id, None)
        return CatalogSyncResult(
            products=len(catalog.products),
            variants=sum(len(p.variants) for p in catalog.products),
            bundles=len(catalog.bundles),
            discontinued_missing=[],
        )

    async def embedding_states(self) -> list[EmbeddingState]:
        return [EmbeddingState(id=id, embedding_source=source) for id, source in self.sources.items()]

    async def store_embeddings(self, embeddings: list[ProductEmbedding]) -> None:
        self.stored.extend(embeddings)
        for e in embeddings:
            self.sources[e.id] = e.source
