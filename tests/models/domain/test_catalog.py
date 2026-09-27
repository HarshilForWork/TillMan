"""The catalog model's rules (#8), which are also the rules a Merchant's catalog file must pass."""

from typing import Any

import pytest
from pydantic import ValidationError

from tests.support.catalog import product, seed
from tillhand.models.domain import Catalog, category_key, category_prefixes


def catalog_with(**overrides: Any) -> dict[str, Any]:
    """A minimal valid catalog file as a dict, with top-level keys replaced."""
    data: dict[str, Any] = {
        "merchant": "Example Merchant",
        "currency": "INR",
        "products": [
            {
                "id": "prod_a",
                "handle": "a",
                "title": "A",
                "description": "Product A.",
                "categories": ["Things > A"],
                "options": [{"name": "Size", "values": ["S", "M"]}],
                "variants": [
                    {"id": "var_a_s", "options": {"Size": "S"}, "price": 100},
                    {"id": "var_a_m", "options": {"Size": "M"}, "price": 200, "stock": 0},
                ],
            },
            {
                "id": "prod_b",
                "handle": "b",
                "title": "B",
                "description": "Product B.",
                "categories": ["Things > B"],
                "variants": [{"id": "var_b", "price": 300}],
            },
        ],
        "bundles": [{"source": "prod_a", "target": "prod_b"}],
    }
    data.update(overrides)
    return data


def with_variant(**fields: Any) -> dict[str, Any]:
    data = catalog_with()
    data["products"][0]["variants"][0].update(fields)
    return data


@pytest.mark.parametrize("name", ["skincare", "coffee"])
def test_both_seed_catalogs_are_valid_catalog_files(name: str) -> None:
    catalog = seed(name)
    assert catalog.currency == "INR"
    assert all(p.currency == "INR" for p in catalog.products)


def test_the_skincare_seed_carries_the_unhappy_paths() -> None:
    catalog = seed("skincare")
    variants = [v for p in catalog.products for v in p.variants]
    assert 20 <= len(variants) <= 50
    assert [p.id for p in catalog.products if p.discontinued] == ["prod_overnight_recovery_cream"]
    assert any(v.stock == 0 for v in variants), "a zero-stock Variant"
    assert any(v.untracked for v in variants), "an untracked Variant"
    retinol = product(catalog, "prod_retinol_serum")
    assert not any(retinol.is_available(v) for v in retinol.variants), "a Product with nothing in stock"


def test_the_coffee_seed_has_two_options_and_gaps_in_the_grid() -> None:
    dark = product(seed("coffee"), "prod_ghat_estate_dark")
    assert [o.name for o in dark.options] == ["Grind", "Size"]
    assert len(dark.variants) < len(dark.options[0].values) * len(dark.options[1].values)
    assert product(seed("coffee"), "prod_french_press").options == []


def test_products_inherit_the_catalog_currency() -> None:
    catalog = Catalog.model_validate(catalog_with(currency="USD"))
    assert {p.currency for p in catalog.products} == {"USD"}


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        (with_variant(options={}), "must choose exactly the options"),
        (with_variant(options={"Size": "XL"}), "is not a value of option"),
        (with_variant(options={"Size": "M"}), "more than one variant"),
        (with_variant(list_price=50), "below price"),
        (with_variant(id="var_b"), "repeated variant ids"),
        (with_variant(id="prod_b"), "both a product and a variant"),
        (with_variant(stock=-1), "greater than or equal to 0"),
        (catalog_with(bundles=[{"source": "prod_a", "target": "prod_missing"}]), "unknown products"),
        (catalog_with(bundles=[{"source": "prod_a", "target": "prod_a"}]), "with itself"),
    ],
)
def test_a_catalog_file_is_rejected_when_it_breaks_a_rule(data: dict[str, Any], problem: str) -> None:
    with pytest.raises(ValidationError, match=problem):
        Catalog.model_validate(data)


@pytest.mark.parametrize("path", ["Skincare>Serum", "Skincare >  Serum", " Skincare", "Skincare > "])
def test_category_paths_must_be_whole_segments_joined_by_the_separator(path: str) -> None:
    data = catalog_with()
    data["products"][0]["categories"] = [path]
    with pytest.raises(ValidationError):
        Catalog.model_validate(data)


def test_category_prefixes_are_whole_segments_and_case_insensitive() -> None:
    assert category_prefixes(["Coffee > Beans > Dark Roast"]) == [
        "coffee",
        "coffee > beans",
        "coffee > beans > dark roast",
    ]
    assert category_key("  coffee>BEANS ") == "coffee > beans"
    assert "cof" not in category_prefixes(["Coffee > Beans"])


def test_the_featured_variant_is_the_first_available_one() -> None:
    catalog = seed("skincare")
    assert product(catalog, "prod_niacinamide_serum").featured_variant().id == "var_niacinamide_serum_15"
    # Nothing in stock: the first Variant still stands for the Product.
    assert product(catalog, "prod_retinol_serum").featured_variant().id == "var_retinol_serum_15"


def test_a_discontinued_product_has_no_available_variant_even_with_stock() -> None:
    cream = product(seed("skincare"), "prod_overnight_recovery_cream")
    assert cream.variants[0].stock == 3
    assert not cream.is_available(cream.variants[0])


def test_the_embedded_document_is_the_product_not_its_variants() -> None:
    serum = product(seed("skincare"), "prod_niacinamide_serum")
    text = serum.embedding_text()
    assert serum.title in text and serum.description in text
    assert "Skincare > Serum" in text and "oil control" in text
    assert "30 ml" not in text


def test_a_variant_title_is_its_option_values_or_the_product_title() -> None:
    catalog = seed("coffee")
    dark = product(catalog, "prod_ghat_estate_dark")
    assert dark.variant_title(dark.variants[0]) == "Whole Bean / 250 g"
    press = product(catalog, "prod_french_press")
    assert press.variant_title(press.variants[0]) == press.title
