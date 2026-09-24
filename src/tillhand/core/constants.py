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

TILLHAND_SITE = "https://tillhand.vercel.app"
EXTENSIONS_VERSION = "2026-09-24"

_authority = url_authority(TILLHAND_SITE)
if _authority is None:
    raise RuntimeError(f"TILLHAND_SITE {TILLHAND_SITE!r} cannot serve UCP schemas")
EXTENSION_AUTHORITY: str = _authority


@dataclass(frozen=True)
class Extension:
    name: str
    extends: str
    schema_url: str
    version: str = EXTENSIONS_VERSION


def _shopping_extension(capability: str, *, extends: str) -> Extension:
    return Extension(
        name=f"{EXTENSION_AUTHORITY}.shopping.{capability}",
        extends=extends,
        schema_url=f"{TILLHAND_SITE}/schemas/shopping/{capability}.json",
    )


REFUND_REQUEST = _shopping_extension("refund_request", extends="dev.ucp.shopping.order")
"""Lets an agent *ask* for a refund. Never executes one: a human refunds in the Razorpay dashboard."""

SUGGESTIONS = _shopping_extension("suggestions", extends="dev.ucp.shopping.catalog")
"""Suggestions: a Bundle first, similarity when a Product has no curated partners."""

EXTENSIONS: tuple[Extension, ...] = (REFUND_REQUEST, SUGGESTIONS)
