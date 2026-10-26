"""SSE event ids accept ASCII digits only (U05 minor)."""

from __future__ import annotations

import pytest

from maivn._internal.transport.sse_client import SseParseError, parse_sse_frame

# Built from code points so the source stays ASCII: Arabic-Indic three, full-width one-two,
# and an ASCII one followed by an Arabic-Indic one.
NON_ASCII_IDS = (chr(0x0663), chr(0xFF11) + chr(0xFF12), '1' + chr(0x0661))
ASCII_POSITION = 42


@pytest.mark.parametrize('raw_id', NON_ASCII_IDS)
def test_non_ascii_decimal_digits_are_refused(raw_id: str) -> None:
    """Arabic-Indic or full-width digits are not a stream position."""
    with pytest.raises(SseParseError, match=r'^SSE id field must be a non-negative integer$'):
        parse_sse_frame(f'id: {raw_id}\nevent: final\ndata: {{}}\n\n')


def test_ascii_digits_still_parse() -> None:
    """Ordinary positions are unchanged."""
    frame = parse_sse_frame(f'id: {ASCII_POSITION}\nevent: final\ndata: {{}}\n\n')

    assert frame.position == ASCII_POSITION
