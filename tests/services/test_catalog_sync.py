import pytest

from tests.support.catalog import FakeCatalogWriter, FakeEmbedder, seed
from tillhand.models.domain import Catalog
from tillhand.services.catalog_sync import embedding_source, sync_catalog

pytestmark = pytest.mark.anyio


async def test_the_first_sync_embeds_every_product_as_a_passage() -> None:
    writer, embedder = FakeCatalogWriter(), FakeEmbedder()
    catalog = seed("skincare")
    report = await sync_catalog(catalog, writer, embedder)
    assert report.embedded == [p.id for p in catalog.products]
    assert embedder.passages == [p.embedding_text() for p in catalog.products]
    assert all(len(e.vector) == 1024 for e in writer.stored)


async def test_a_re_sync_embeds_only_what_changed() -> None:
    writer, embedder = FakeCatalogWriter(), FakeEmbedder()
    catalog = seed("skincare")
    await sync_catalog(catalog, writer, embedder)

    edited = catalog.model_dump()
    edited["products"][0]["description"] += " Now with extra zinc."
    edited["products"][1]["variants"][0]["price"] += 1000  # price is not embedded
    report = await sync_catalog(Catalog.model_validate(edited), writer, embedder)
    assert report.embedded == [catalog.products[0].id]


async def test_without_an_embedder_nothing_is_embedded_and_the_gap_is_reported() -> None:
    writer = FakeCatalogWriter()
    catalog = seed("coffee")
    report = await sync_catalog(catalog, writer, None)
    assert report.embedded == [] and writer.stored == []
    assert report.stale == [p.id for p in catalog.products]


def test_a_new_model_makes_every_stored_vector_stale() -> None:
    product = seed("coffee").products[0]
    assert embedding_source("llama-text-embed-v2", product) != embedding_source("another-model", product)
