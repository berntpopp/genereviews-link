"""Unit contract for the caller-visible error-message sanitizer.

``sanitize_message`` is the defensive backstop applied to every caller-visible
message/error/diagnostics string: it removes the fence's forbidden
control/zero-width/bidi/NUL code points and length-caps, but deliberately does
NOT rewrite ordinary prose (prose-carrying / ``str(exc)`` fields are severed to
fixed messages at their source, not sanitized here).
"""

from __future__ import annotations

from genereview_link.mcp.untrusted_content import (
    FORBIDDEN_CODEPOINTS,
    MAX_MESSAGE_CHARS,
    sanitize_message,
)


def test_strips_nul_zwj_bom_and_bidi_override() -> None:
    dirty = "boom\x00‍﻿‮"
    clean = sanitize_message(dirty)
    for bad in ("\x00", "‍", "﻿", "‮"):
        assert bad not in clean
    assert clean == "boom"


def test_preserves_ordinary_prose() -> None:
    # sanitize_message is a code-point strip, NOT a prose neutralizer.
    prose = "No GeneReviews chapter was found for gene symbol BRCA1."
    assert sanitize_message(prose) == prose


def test_preserves_tabs_and_newlines() -> None:
    # Tabs (0x09) and newlines (0x0A) are NOT in the forbidden set.
    assert sanitize_message("a\tb\nc") == "a\tb\nc"


def test_length_capped_to_max_message_chars() -> None:
    assert MAX_MESSAGE_CHARS == 280
    capped = sanitize_message("x" * 5000)
    assert len(capped) == MAX_MESSAGE_CHARS


def test_every_forbidden_codepoint_removed() -> None:
    dirty = "".join(chr(cp) for cp in FORBIDDEN_CODEPOINTS)
    assert sanitize_message(dirty) == ""
