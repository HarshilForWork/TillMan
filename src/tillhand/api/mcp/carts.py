"""The UCP cart tools (`dev.ucp.shopping.cart`) under their UCP names, each wrapping a `CartService` method.

All four are owner-scoped: a Cart belongs to whoever created it (#42), and anyone else gets `not_found`.
"""

from collections.abc import Callable

from tillhand.api.mcp.server import OwnedTool, Tool
from tillhand.models.ucp import (
    MAX_CART_LINES,
    MAX_LINE_QUANTITY,
    CancelCartArguments,
    CreateCartArguments,
    GetCartArguments,
    UpdateCartArguments,
)
from tillhand.services.carts import CartService

_LINES = (
    f"Each line names a Variant by id (or SKU) and a quantity from 1 to {MAX_LINE_QUANTITY}; at most "
    f"{MAX_CART_LINES} lines. Prices are live: every response re-prices the Cart from the catalog. "
    "Unknown ids are left out with a not_found message; out-of-stock or discontinued lines stay, marked "
    "item_unavailable and counted as 0; more than the stock gets an insufficient_stock warning."
)
CREATE_CART = (
    f"Create a Cart for the Customer from line items. {_LINES} "
    "The Cart expires at expires_at, which each change pushes further out."
)
GET_CART = (
    "Get a Cart by id, at today's prices and stock. An unknown, expired or cancelled Cart is not_found."
)
UPDATE_CART = f"Replace a Cart's line items: send the whole Cart, as the update replaces every line. {_LINES}"
CANCEL_CART = (
    "Cancel a Cart: returns its last state, after which it is not_found. Requires meta.idempotency-key "
    "(a UUID); retrying with the same key returns the same response."
)


def cart_tools(carts: Callable[[], CartService]) -> list[Tool]:
    return [
        OwnedTool(
            "create_cart",
            CREATE_CART,
            CreateCartArguments,
            lambda arguments, owner: carts().create_cart(arguments, owner),
        ),
        OwnedTool(
            "get_cart",
            GET_CART,
            GetCartArguments,
            lambda arguments, owner: carts().get_cart(arguments, owner),
        ),
        OwnedTool(
            "update_cart",
            UPDATE_CART,
            UpdateCartArguments,
            lambda arguments, owner: carts().update_cart(arguments, owner),
        ),
        OwnedTool(
            "cancel_cart",
            CANCEL_CART,
            CancelCartArguments,
            lambda arguments, owner: carts().cancel_cart(arguments, owner),
        ),
    ]
