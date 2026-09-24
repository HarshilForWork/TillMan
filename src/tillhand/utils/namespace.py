"""UCP's namespace authority rules (overview, "Authority Binding" → "Derivation algorithm").

A Platform applies `schema_authority_matches` to every capability a business declares and silently
drops the capability when it fails, so we run the same check on our own Extensions in tests.
"""

import ipaddress
from urllib.parse import urlsplit


def url_authority(url: str) -> str | None:
    """The reverse-domain authority a schema URL proves (`ucp.dev` → `dev.ucp`), or None if it proves none.

    Following the spec: the URL must be https with no userinfo, and its host must be a registered
    name of at least two labels (no IP literals, no `localhost`), lowercased, without a trailing dot.
    """
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        return None
    if parts.scheme != "https" or "@" in parts.netloc or not hostname:
        return None

    host = hostname.rstrip(".")
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None

    labels = host.split(".")
    if len(labels) < 2 or not all(labels):
        return None
    return ".".join(reversed(labels))


def schema_authority_matches(name: str, schema_url: str) -> bool:
    """True when the URL's authority equals `name` or is a label-aligned prefix of it."""
    authority = url_authority(schema_url)
    return authority is not None and (name == authority or name.startswith(f"{authority}."))
