"""Our catalog model (#8): Products with Options, Variants, stock, status and Bundles.

It is ours, not UCP's wire format: it holds what UCP has no slot for (stock counts, Bundles, status),
and the UCP mapper turns it into wire models at the MCP boundary. `Catalog` is also the canonical
JSON catalog file a Merchant onboards with, so a seed file is validated by exactly these rules.

Nothing here may assume a vertical: Options have any names, and categories are the Merchant's own paths.
"""

from collections import Counter
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from tillhand.models.ucp import Amount, CurrencyCode

CATEGORY_SEPARATOR = " > "
"""Categories are paths in the Merchant's taxonomy, e.g. `Skincare > Serum` (UCP has no tree)."""

Id = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
Handle = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=128)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CategoryPath = Annotated[
    str, StringConstraints(pattern=r"^[^>\s](?:[^>]*[^>\s])?(?: > [^>\s](?:[^>]*[^>\s])?)*$", max_length=256)
]
"""Whole segments joined by ` > `, none of them empty or padded."""

ProductStatus = Literal["active", "discontinued"]


def category_key(value: str) -> str:
    """How a category path is compared: case-insensitive, with ` > ` separators normalised."""
    return CATEGORY_SEPARATOR.join(" ".join(part.split()) for part in value.lower().split(">"))


def category_prefixes(paths: list[str]) -> list[str]:
    """Every whole-segment prefix of every path, as keys (#8 decision 12).

    A categories filter matches a Product when any filter value's key is among these, so `Skincare`
    matches `Skincare > Serum`, but `Skin` does not.
    """
    keys: set[str] = set()
    for path in paths:
        segments = category_key(path).split(CATEGORY_SEPARATOR)
        keys.update(CATEGORY_SEPARATOR.join(segments[:end]) for end in range(1, len(segments) + 1))
    return sorted(keys)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _duplicates(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


class Option(DomainModel):
    """A dimension the Product's Variants differ along, with its allowed values, in display order."""

    name: Text
    values: Annotated[list[Text], Field(min_length=1)]

    @model_validator(mode="after")
    def _unique_values(self) -> Self:
        if dupes := _duplicates(self.values):
            raise ValueError(f"option {self.name!r} repeats values {dupes}")
        return self


class Variant(DomainModel):
    """One chosen value for each of the Product's Options, with its own price and optional stock."""

    id: Id
    sku: Text | None = None
    options: dict[str, str] = Field(default_factory=dict)
    """Option name to chosen value, one entry per Option of the Product."""

    price: Amount
    """Minor units, GST included (#8: there is no tax calculation)."""

    list_price: Amount | None = None
    """The struck-through price; never below `price`."""

    stock: Annotated[int, Field(ge=0)] | None = None
    """`None` means untracked: always available. A tracked Variant at 0 is out of stock."""

    @model_validator(mode="after")
    def _list_price_not_below_price(self) -> Self:
        if self.list_price is not None and self.list_price < self.price:
            raise ValueError(f"variant {self.id}: list_price {self.list_price} is below price {self.price}")
        return self

    @property
    def untracked(self) -> bool:
        return self.stock is None


class Product(DomainModel):
    id: Id
    handle: Handle
    title: Text
    description: Text
    """Plain text. It is embedded for search, so it describes the Product rather than sells it."""

    status: ProductStatus = "active"
    currency: CurrencyCode
    categories: Annotated[list[CategoryPath], Field(min_length=1)]
    tags: list[Text] = Field(default_factory=list)
    options: list[Option] = Field(default_factory=list)
    variants: Annotated[list[Variant], Field(min_length=1)]
    """In the Merchant's display order; the first available one is the featured Variant."""

    @model_validator(mode="after")
    def _variants_match_options(self) -> Self:
        names = [option.name for option in self.options]
        if dupes := _duplicates(names):
            raise ValueError(f"product {self.id} repeats options {dupes}")
        allowed = {option.name: set(option.values) for option in self.options}
        combinations: list[str] = []
        for variant in self.variants:
            if set(variant.options) != set(allowed):
                chosen = sorted(variant.options)
                raise ValueError(
                    f"variant {variant.id} must choose exactly the options {names}, got {chosen}"
                )
            for name, value in variant.options.items():
                if value not in allowed[name]:
                    raise ValueError(f"variant {variant.id}: {value!r} is not a value of option {name!r}")
            combinations.append(" / ".join(variant.options[name] for name in names))
        if dupes := _duplicates(combinations):
            raise ValueError(f"product {self.id} has more than one variant for {dupes}")
        if dupes := _duplicates([variant.id for variant in self.variants]):
            raise ValueError(f"product {self.id} repeats variant ids {dupes}")
        return self

    @property
    def discontinued(self) -> bool:
        return self.status == "discontinued"

    def is_available(self, variant: Variant) -> bool:
        """Purchasable now. A discontinued Product's Variants are all unavailable (#8)."""
        return not self.discontinued and (variant.stock is None or variant.stock > 0)

    def option_values(self, variant: Variant) -> list[str]:
        """The Variant's chosen values in the Product's Option order."""
        return [variant.options[option.name] for option in self.options]

    def variant_title(self, variant: Variant) -> str:
        return " / ".join(self.option_values(variant)) or self.title

    def featured_variant(self) -> Variant:
        """The first available Variant in display order, or the first one when none is available."""
        return next((v for v in self.variants if self.is_available(v)), self.variants[0])

    def embedding_text(self) -> str:
        """The one document embedded per Product (#8): title, description, category paths and tags.

        Variants are left out on purpose: they differ in size, grind or shade, not in meaning.
        """
        lines = [self.title, self.description, f"Categories: {'; '.join(self.categories)}"]
        if self.tags:
            lines.append(f"Tags: {', '.join(self.tags)}")
        return "\n".join(lines)


class Bundle(DomainModel):
    """A curated, directed pairing: buying `source` suggests `target`. Product to Product, never Variant."""

    source: Id
    target: Id
    weight: Annotated[float, Field(gt=0, le=1)] = 1.0
    """Higher is suggested first."""

    @model_validator(mode="after")
    def _not_self(self) -> Self:
        if self.source == self.target:
            raise ValueError(f"bundle {self.source} -> {self.target} pairs a product with itself")
        return self


class Catalog(DomainModel):
    """The canonical JSON catalog file: one Merchant's whole catalog, in one currency.

    `currency` is written once, at the top; each Product inherits it.
    """

    merchant: Text
    currency: CurrencyCode
    products: Annotated[list[Product], Field(min_length=1)]
    bundles: list[Bundle] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _products_inherit_currency(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("products"), list):
            currency = data.get("currency")
            products: list[Any] = []
            for product in data["products"]:
                if isinstance(product, dict) and "currency" not in product:
                    product = {**product, "currency": currency}
                products.append(product)
            data = {**data, "products": products}
        return data

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        problems: list[str] = []
        ids = [p.id for p in self.products]
        variants = [v for p in self.products for v in p.variants]
        checks = {
            "product ids": ids,
            "handles": [p.handle for p in self.products],
            "variant ids": [v.id for v in variants],
            "skus": [v.sku for v in variants if v.sku is not None],
            "bundles": [f"{b.source} -> {b.target}" for b in self.bundles],
        }
        for label, values in checks.items():
            if dupes := _duplicates(values):
                problems.append(f"repeated {label}: {dupes}")
        if clash := sorted(set(ids) & {v.id for v in variants}):
            problems.append(f"ids used for both a product and a variant: {clash}")
        if other := sorted({p.currency for p in self.products} - {self.currency}):
            problems.append(f"products priced in {other}, but the catalog is in {self.currency}")
        known = set(ids)
        if unknown := sorted({end for b in self.bundles for end in (b.source, b.target)} - known):
            problems.append(f"bundles name unknown products: {unknown}")
        if problems:
            raise ValueError("; ".join(problems))
        return self
