"""The namespace authority check from UCP's overview (Authority Binding, derivation algorithm).

A Platform silently drops any capability whose schema host fails this check, so ours must pass it.
"""

import pytest

from tillhand.ucp import EXTENSION_AUTHORITY, EXTENSIONS, TILLHAND_SITE, Extension, schema_authority_matches

from .spec import authority_table


# The spec's own table, read from the vendored overview rather than copied here.
@pytest.mark.parametrize(("name", "host", "accepted"), authority_table())
def test_spec_authority_table(name: str, host: str, accepted: bool) -> None:
    assert schema_authority_matches(name, f"https://{host}/schemas/x.json") is accepted


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://example.com/x.json", "not https"),
        ("https://user:pass@example.com/x.json", "userinfo"),
        ("https://example.com@evil.example/x.json", "userinfo hides the real host"),
        ("https://203.0.113.10/x.json", "IP literal"),
        ("https://localhost/x.json", "single label"),
        ("not a url", "unparseable"),
    ],
)
def test_invalid_schema_urls_never_match(url: str, reason: str) -> None:
    assert schema_authority_matches("com.example.pay", url) is False, reason


def test_host_is_normalised_before_matching() -> None:
    assert schema_authority_matches("com.example.pay", "https://EXAMPLE.com.:8443/x.json") is True


@pytest.mark.parametrize("extension", EXTENSIONS, ids=lambda e: e.name)
def test_our_extensions_pass_the_check_platforms_apply(extension: Extension) -> None:
    assert schema_authority_matches(extension.name, extension.schema_url)


@pytest.mark.parametrize("extension", EXTENSIONS, ids=lambda e: e.name)
def test_our_extensions_follow_the_naming_convention(extension: Extension) -> None:
    # {reverse-domain}.{service}.{capability}, and it attaches to a UCP parent capability.
    assert extension.name.startswith(f"{EXTENSION_AUTHORITY}.")
    service, _, capability = extension.name.removeprefix(f"{EXTENSION_AUTHORITY}.").partition(".")
    assert service == "shopping" and capability and "." not in capability
    assert extension.extends.startswith("dev.ucp.shopping.")


def test_spec_table_was_found() -> None:
    assert len(authority_table()) == 9


def test_the_namespace_is_the_tillhand_sites_host_reversed() -> None:
    assert TILLHAND_SITE == "https://tillhand.vercel.app"
    assert EXTENSION_AUTHORITY == "app.vercel.tillhand"
