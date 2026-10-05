"""The app over in-memory fakes, as the MCP and route tests build it."""

from contextlib import nullcontext
from datetime import timedelta
from typing import Any

from fastapi import FastAPI

from tests.support.carts import FakeCartStore
from tests.support.catalog import FakeCatalogStore, FakeEmbedder, FakeSuggestionStore, seed
from tests.support.isolation import identify_with_test_customer
from tests.support.profiles import PRE_APPROVED, FakeProfileFetcher
from tillhand.main import Services, create_app
from tillhand.models.ucp import PlatformProfile
from tillhand.services.carts import CartService, StoreCartService
from tillhand.services.catalog import CatalogService, StoreCatalogService
from tillhand.services.profiles import ProfileFetcher, ProfileResolver, StaticPreApproved
from tillhand.services.suggestions import StoreSuggestionService, SuggestionService

HOST = "testserver"


def catalog_service(merchant: str = "skincare") -> CatalogService:
    return StoreCatalogService(FakeCatalogStore(seed(merchant)), FakeEmbedder())


def services(
    catalog: CatalogService | None = None,
    *,
    suggestions: SuggestionService | None = None,
    carts: CartService | None = None,
    fetcher: ProfileFetcher | None = None,
    pre_approved: dict[str, PlatformProfile] | None = None,
) -> Services:
    """By default `platform.example` is pre-approved, and any other profile URL is a 404."""
    return Services(
        catalog=catalog or catalog_service(),
        suggestions=suggestions or StoreSuggestionService(FakeSuggestionStore(seed("skincare"), {})),
        carts=carts or StoreCartService(FakeCartStore(seed("skincare")), lifetime=timedelta(days=7)),
        profiles=ProfileResolver(
            StaticPreApproved(PRE_APPROVED if pre_approved is None else pre_approved),
            fetcher or FakeProfileFetcher(),
        ),
    )


def app(served: Services | None = None, **options: Any) -> FastAPI:
    """Callers of owner-scoped tools are identified by the test door: `meta["test-customer"]` names a
    Customer (`tests.support.isolation`)."""
    built = served or services()
    options.setdefault("identify", identify_with_test_customer)
    return create_app(open_services=lambda: nullcontext(built), allowed_hosts=[HOST], **options)
