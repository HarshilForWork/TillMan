"""Project-wide constants: the UCP version we conform to, and TillHand's Extensions.

Extension names are the TillHand site's host with its labels reversed, then `{service}.{capability}`
(UCP's naming convention). Platforms silently drop any Extension whose schema is served from a host
that doesn't match its name, so the namespace is derived from the site's URL rather than written
out a second time, and every Extension's schema lives under that site.
"""

from dataclasses import dataclass

from tillhand.utils.namespace import url_authority

UCP_VERSION = "2026-08-25"
"""The UCP version we conform to. The vendored spec under `vendor/ucp/v{UCP_VERSION}/` must match."""

CATALOG_SEARCH = "dev.ucp.shopping.catalog.search"
CATALOG_LOOKUP = "dev.ucp.shopping.catalog.lookup"
"""The UCP catalog capabilities we implement: `search_catalog`, and `lookup_catalog` with `get_product`."""

CART = "dev.ucp.shopping.cart"
"""`create_cart`, `get_cart`, `update_cart` and `cancel_cart` (#50)."""

TILLHAND_SITE = "https://tillhand.vercel.app"
EXTENSIONS_VERSION = "2026-09-24"

_authority = url_authority(TILLHAND_SITE)
if _authority is None:
    raise RuntimeError(f"TILLHAND_SITE {TILLHAND_SITE!r} cannot serve UCP schemas")
EXTENSION_AUTHORITY: str = _authority


@dataclass(frozen=True)
class Extension:
    name: str
    extends: str | tuple[str, ...]
    schema_url: str
    version: str = EXTENSIONS_VERSION


def _shopping_extension(capability: str, *, extends: str | tuple[str, ...]) -> Extension:
    return Extension(
        name=f"{EXTENSION_AUTHORITY}.shopping.{capability}",
        extends=extends,
        schema_url=f"{TILLHAND_SITE}/schemas/shopping/{capability}.json",
    )


REFUND_REQUEST = _shopping_extension("refund_request", extends="dev.ucp.shopping.order")
"""Lets an agent *ask* for a refund. Never executes one: a human refunds in the Razorpay dashboard."""

SUGGESTIONS = _shopping_extension(
    "suggestions", extends=("dev.ucp.shopping.catalog.search", "dev.ucp.shopping.catalog.lookup")
)
"""Suggestions: a Bundle first, similarity when a Product has no curated partners.

Both catalog capabilities are parents. UCP has no `dev.ucp.shopping.catalog` capability, and negotiation
prunes an Extension none of whose parents survive, so it is kept whenever a Platform negotiates either."""

SUGGESTION_SIMILARITY_FLOOR = 0.48
"""The cosine similarity a similar Product needs to be suggested at all (#46). A deployment can override
it (`SUGGESTION_SIMILARITY_FLOOR`), since it depends on the catalog and the embedding model.

Tuned on the skincare seed with `llama-text-embed-v2` (`scripts/suggestions_smoke.py`). Its cross-category
similarities run from 0.29 to 0.68. Every pair at 0.48 or above agrees on skin type or purpose (the gel
moisturiser and gel sunscreen at 0.65, the ceramide cleanser and barrier cream at 0.68). The first mismatches
sit just below: a sensitive-skin sunscreen and an oily-skin moisturiser (0.477), a dry-skin toner and the same
moisturiser (0.472). At 0.48 every Product but one keeps at least one similar complement; the retinol serum's
best is 0.473, so it relies on its Bundle."""

EXTENSIONS: tuple[Extension, ...] = (REFUND_REQUEST, SUGGESTIONS)
