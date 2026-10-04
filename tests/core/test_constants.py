"""Our Extensions pass the namespace check Platforms apply, and follow UCP's naming convention."""

import pytest

from tests.support.ucp_spec import ucp_capability_names
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
    parents = (extension.extends,) if isinstance(extension.extends, str) else extension.extends
    assert parents and all(parent.startswith("dev.ucp.shopping.") for parent in parents)


@pytest.mark.parametrize("extension", EXTENSIONS, ids=lambda e: e.name)
def test_every_parent_is_a_capability_ucp_defines(extension: Extension) -> None:
    """Negotiation prunes an Extension none of whose parents survive, so a parent UCP doesn't define
    would silently drop the Extension for every Platform."""
    parents = (extension.extends,) if isinstance(extension.extends, str) else extension.extends
    assert set(parents) <= ucp_capability_names()


def test_the_namespace_is_the_tillhand_sites_host_reversed() -> None:
    assert TILLHAND_SITE == "https://tillhand.vercel.app"
    assert EXTENSION_AUTHORITY == "app.vercel.tillhand"
