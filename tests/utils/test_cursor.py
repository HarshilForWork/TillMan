import pytest

from tillhand.utils.cursor import decode_offset, encode_offset


@pytest.mark.parametrize("offset", [0, 10, 12345])
def test_an_issued_cursor_decodes_to_its_offset(offset: int) -> None:
    assert decode_offset(encode_offset(offset)) == offset


@pytest.mark.parametrize("cursor", ["", "not base64 !", "MTA", encode_offset(3) + "x", "bzotMQ"])
def test_a_cursor_we_did_not_issue_decodes_to_none(cursor: str) -> None:
    assert decode_offset(cursor) is None
