"""Our Extensions pass the namespace check Platforms apply, and follow UCP's naming convention."""

import pytest

from tillhand.core.constants import EXTENSION_AUTHORITY, EXTENSIONS, TILLHAND_SITE, Extension
from tillhand.utils.namespace import schema_authority_matches


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


def test_the_namespace_is_the_tillhand_sites_host_reversed() -> None:
    assert TILLHAND_SITE == "https://tillhand.vercel.app"
    assert EXTENSION_AUTHORITY == "app.vercel.tillhand"
