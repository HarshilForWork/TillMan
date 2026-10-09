"""The app over in-memory fakes, as the MCP and route tests build it."""

from contextlib import nullcontext
from datetime import timedelta
from typing import Any

from fastapi import FastAPI

from tests.support.carts import FakeCartStore
from tests.support.catalog import FakeCatalogStore, FakeEmbedder, FakeSuggestionStore, seed
from tests.support.merchant import ASSISTANTS, merchant_store
from tests.support.profiles import PRE_APPROVED, FakeProfileFetcher
from tillhand.main import Services, create_app
from tillhand.models.ucp import PlatformProfile
from tillhand.services.carts import CartService, StoreCartService
from tillhand.services.catalog import CatalogService, StoreCatalogService
from tillhand.services.merchant_door import MerchantDoor
from tillhand.services.profiles import KeyBinding, ProfileFetcher, ProfileResolver, StaticPreApproved
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
    merchant: MerchantDoor | None = None,
    key_binding: KeyBinding | None = None,
) -> Services:
    """By default `platform.example` is pre-approved, and any other profile URL is a 404. The two Merchant
    assistants are pre-approved and key-bound."""
    registry = StaticPreApproved(
        {**(PRE_APPROVED if pre_approved is None else pre_approved), **ASSISTANTS}, key_bound=ASSISTANTS
    )
    return Services(
        catalog=catalog or catalog_service(),
        suggestions=suggestions or StoreSuggestionService(FakeSuggestionStore(seed("skincare"), {})),
        carts=carts or StoreCartService(FakeCartStore(seed("skincare")), lifetime=timedelta(days=7)),
        profiles=ProfileResolver(
            registry, fetcher or FakeProfileFetcher(), key_binding=key_binding or registry
        ),
        merchant=merchant or MerchantDoor(merchant_store()),
    )


def app(served: Services | None = None, **options: Any) -> FastAPI:
    built = served or services()
    return create_app(open_services=lambda: nullcontext(built), allowed_hosts=[HOST], **options)
