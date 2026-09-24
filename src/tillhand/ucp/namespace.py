"""UCP's namespace authority check (overview, "Authority Binding" → "Derivation algorithm").

A Platform applies this to every capability a business declares and silently drops the capability
when it fails, so we run the same check on our own Extensions in tests.
"""

import ipaddress
from urllib.parse import urlsplit


def schema_authority_matches(name: str, schema_url: str) -> bool:
    """True when `schema_url`'s host, labels reversed, equals `name` or is a label-aligned prefix of it."""
    try:
        parts = urlsplit(schema_url)
        hostname = parts.hostname
    except ValueError:
        return False
    if parts.scheme != "https" or "@" in parts.netloc or not hostname:
        return False

    host = hostname.rstrip(".")
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return False

    labels = host.split(".")
    if len(labels) < 2 or not all(labels):
        return False

    authority = ".".join(reversed(labels))
    return name == authority or name.startswith(f"{authority}.")
