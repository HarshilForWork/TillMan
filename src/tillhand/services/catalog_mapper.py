"""Our catalog model to UCP wire models: the thin mapper at the MCP boundary (#8 decision 1).

UCP exposes availability only as a boolean, never a stock count. `status` adds a machine-readable
reason, so an agent can tell "out of stock" from "discontinued" without parsing prose.
"""

from collections.abc import Sequence
from typing import Any

from tillhand.models.domain import Product
from tillhand.models.domain import Variant as DomainVariant
from tillhand.models.ucp import (
    Availability,
    Category,
    Description,
    DetailOptionValue,
    DetailProduct,
    DetailProductOption,
    InputCorrelation,
    LookupProduct,
    LookupVariant,
    OptionValue,
    Price,
    PriceRange,
    ProductOption,
    SelectedOption,
    Variant,
)
from tillhand.models.ucp import Product as UcpProduct

MERCHANT_TAXONOMY = "merchant"
"""Categories are the Merchant's own paths, not a public taxonomy such as Google's (#8 decision 13)."""


def availability(product: Product, variant: DomainVariant) -> Availability:
    if product.discontinued:
        return Availability(available=False, status="discontinued")
    if product.is_available(variant):
        return Availability(available=True, status="in_stock")
    return Availability(available=False, status="out_of_stock")


def selected_options(product: Product, variant: DomainVariant) -> list[SelectedOption]:
    return [SelectedOption(name=o.name, label=variant.options[o.name]) for o in product.options]


def variant(product: Product, v: DomainVariant) -> Variant:
    return Variant(**_variant_fields(product, v))


def _variant_fields(product: Product, v: DomainVariant) -> dict[str, Any]:
    return {
        "id": v.id,
        "title": product.variant_title(v),
        "description": Description(plain=product.description),
        "price": Price(amount=v.price, currency=product.currency),
        "list_price": None if v.list_price is None else Price(amount=v.list_price, currency=product.currency),
        "sku": v.sku,
        "availability": availability(product, v),
        "options": selected_options(product, v) or None,
    }


def _price_range(product: Product, amounts: Sequence[int]) -> PriceRange:
    return PriceRange(
        min=Price(amount=min(amounts), currency=product.currency),
        max=Price(amount=max(amounts), currency=product.currency),
    )


def _product_fields(product: Product, variants: Sequence[DomainVariant]) -> dict[str, Any]:
    """Fields every product shape shares. Price ranges cover the Variants being returned."""
    has_list_price = any(v.list_price is not None for v in variants)
    return {
        "id": product.id,
        "handle": product.handle,
        "title": product.title,
        "description": Description(plain=product.description),
        "price_range": _price_range(product, [v.price for v in variants]),
        "list_price_range": _price_range(product, [v.list_price or v.price for v in variants])
        if has_list_price
        else None,
        "categories": [Category(value=path, taxonomy=MERCHANT_TAXONOMY) for path in product.categories],
        "tags": product.tags or None,
    }


def _options(product: Product) -> list[ProductOption] | None:
    return [
        ProductOption(name=o.name, values=[OptionValue(label=value) for value in o.values])
        for o in product.options
    ] or None


def search_product(product: Product, variants: Sequence[DomainVariant]) -> UcpProduct:
    """A Product in search results, with the given Variants in the given order (featured first)."""
    return UcpProduct(
        **_product_fields(product, variants),
        options=_options(product),
        variants=[variant(product, v) for v in variants],
    )


def lookup_product(product: Product, inputs: dict[str, list[InputCorrelation]]) -> LookupProduct:
    """A Product in lookup results. `inputs` maps each returned Variant id to the ids that resolved to it."""
    variants = [v for v in product.variants if v.id in inputs]
    return LookupProduct(
        **_product_fields(product, variants),
        options=_options(product),
        variants=[LookupVariant(**_variant_fields(product, v), inputs=inputs[v.id]) for v in variants],
    )


def detail_product(
    product: Product, variants: Sequence[DomainVariant], selected: dict[str, str]
) -> DetailProduct:
    """A Product as `get_product` returns it.

    Each option value says whether a Variant exists, and whether one is available, for that value
    combined with the *other* effective selections: changing Size keeps the chosen Grind.
    """
    options: list[DetailProductOption] = []
    for option in product.options:
        others = {name: label for name, label in selected.items() if name != option.name}
        values: list[DetailOptionValue] = []
        for value in option.values:
            wanted = {**others, option.name: value}
            matching = [
                v for v in product.variants if all(v.options[n] == label for n, label in wanted.items())
            ]
            values.append(
                DetailOptionValue(
                    label=value,
                    exists=bool(matching),
                    available=any(product.is_available(v) for v in matching),
                )
            )
        options.append(DetailProductOption(name=option.name, values=values))
    return DetailProduct(
        **_product_fields(product, variants),
        options=options or None,
        variants=[variant(product, v) for v in variants],
        # UCP requires `selected` whenever the Product has Options, even if relaxation emptied it.
        selected=[
            SelectedOption(name=o.name, label=selected[o.name]) for o in product.options if o.name in selected
        ]
        if product.options
        else None,
    )
