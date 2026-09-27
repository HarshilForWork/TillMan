"""What catalog queries return beyond a whole `Product`: id resolution, syncing and embedding bookkeeping."""

from pydantic import BaseModel, ConfigDict

from tillhand.models.domain import Product


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IdMatch(Row):
    """One requested identifier and what it resolved to."""

    input: str
    variant_id: str | None
    """The Variant it named (a Variant id or SKU), or `None` when it named the Product (id or handle)."""


class ResolvedProduct(Row):
    product: Product
    matches: list[IdMatch]


class EmbeddingState(Row):
    id: str
    embedding_source: str | None
    """The hash of what was last embedded, or `None` when the Product has no embedding yet."""


class ProductEmbedding(Row):
    id: str
    vector: list[float]
    source: str


class CatalogSyncResult(Row):
    products: int
    variants: int
    bundles: int
    discontinued_missing: list[str]
    """Products in the database but not in the file: marked discontinued, never deleted."""
