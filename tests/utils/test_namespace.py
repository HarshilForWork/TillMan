"""The namespace authority check from UCP's overview (Authority Binding, derivation algorithm).

A Platform silently drops any capability whose schema host fails this check; see tests/core for ours.
"""

import pytest

from tests.support.ucp_spec import authority_table
from tillhand.utils.namespace import schema_authority_matches, url_authority


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


def test_spec_table_was_found() -> None:
    assert len(authority_table()) == 9


def test_url_authority_reverses_the_host() -> None:
    assert url_authority("https://tillhand.vercel.app/schemas/x.json") == "app.vercel.tillhand"
    assert url_authority("http://tillhand.vercel.app/x.json") is None
