"""Opaque pagination cursors. UCP cursors are opaque strings; ours encode an offset, and nothing else."""

import base64
import binascii

_PREFIX = "o:"


def encode_offset(offset: int) -> str:
    return base64.urlsafe_b64encode(f"{_PREFIX}{offset}".encode()).decode().rstrip("=")


def decode_offset(cursor: str) -> int | None:
    """The offset a cursor from `encode_offset` holds, or `None` for anything we didn't issue."""
    try:
        text = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    digits = text.removeprefix(_PREFIX)
    if text == digits or not digits.isdigit() or not digits.isascii():
        return None
    return int(digits)
