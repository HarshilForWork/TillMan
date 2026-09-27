"""Loading a catalog file into the database, and embedding the Products whose text changed.

The order is the connection rule in practice: write the catalog (DB only), read which embeddings are
stale (DB only), embed them (network, no connection held), then write the vectors back (DB only).
"""

import hashlib
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from tillhand.models.db import CatalogSyncResult, EmbeddingState, ProductEmbedding
from tillhand.models.domain import Catalog, Product


class CatalogWriter(Protocol):
    async def sync_catalog(self, catalog: Catalog) -> CatalogSyncResult: ...

    async def embedding_states(self) -> list[EmbeddingState]: ...

    async def store_embeddings(self, embeddings: list[ProductEmbedding]) -> None: ...


class PassageEmbedder(Protocol):
    @property
    def model(self) -> str: ...

    async def embed_passages(self, texts: list[str]) -> list[list[float]]: ...


class SyncReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog: CatalogSyncResult
    embedded: list[str]
    """Products (re-)embedded this run, because they were new or their embedded text changed."""

    stale: list[str]
    """Products with no embedding at all, when embedding was skipped."""


def embedding_source(model: str, product: Product) -> str:
    """What decides whether a stored vector is current: the model and the exact text embedded."""
    return hashlib.sha256(f"{model}\n{product.embedding_text()}".encode()).hexdigest()


async def sync_catalog(
    catalog: Catalog, writer: CatalogWriter, embedder: PassageEmbedder | None
) -> SyncReport:
    """Make the database match `catalog`, then embed new or changed Products.

    With no embedder, nothing is embedded, and the Products with no embedding at all are reported.
    """
    result = await writer.sync_catalog(catalog)
    current = {state.id: state.embedding_source for state in await writer.embedding_states()}
    if embedder is None:
        missing = [p.id for p in catalog.products if current.get(p.id) is None]
        return SyncReport(catalog=result, embedded=[], stale=missing)
    stale = [p for p in catalog.products if current.get(p.id) != embedding_source(embedder.model, p)]
    if stale:
        vectors = await embedder.embed_passages([p.embedding_text() for p in stale])
        await writer.store_embeddings(
            [
                ProductEmbedding(id=p.id, vector=vector, source=embedding_source(embedder.model, p))
                for p, vector in zip(stale, vectors, strict=True)
            ]
        )
    return SyncReport(catalog=result, embedded=[p.id for p in stale], stale=[])
