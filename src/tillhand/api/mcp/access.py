"""The tool registry: every tool is `public` or `owner_scoped`; nothing else is served (#42, #11 decision 9).

- **public:** reads only what anyone with a valid profile may see (the catalog, Suggestions over it).
- **owner_scoped:** reads or changes data that belongs to someone, a Cart for instance. It is built as an
  `OwnedTool`, which receives the caller's `Owner`, and every one of them is in the cross-owner isolation
  fixture (`tests/support/isolation.py`): another owner asking for its id must learn nothing.

`build_mcp_server` refuses a tool missing from here, or labelled one way and built the other, so a new
tool can't ship without someone deciding which it is.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

Access = Literal["public", "owner_scoped"]

TOOL_ACCESS: Mapping[str, Access] = MappingProxyType(
    {
        "search_catalog": "public",
        "lookup_catalog": "public",
        "get_product": "public",
        # Public while it reads only the catalog. Its `cart_id` input (#47) reads a Cart, so it becomes
        # owner_scoped then, and joins the isolation fixture.
        "get_suggestions": "public",
        # A Cart belongs to whoever created it (#50).
        "create_cart": "owner_scoped",
        "get_cart": "owner_scoped",
        "update_cart": "owner_scoped",
        "cancel_cart": "owner_scoped",
    }
)
