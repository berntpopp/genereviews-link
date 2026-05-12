"""Regression tests: chunker must preserve original text casing and punctuation.

Earlier versions used decode_tokens() to recover text from token windows.
BGE's uncased WordPiece tokenizer lowercased all tokens and inserted spaces
around punctuation, so "Lynch syndrome (CRC)" became "lynch syndrome ( crc )".
These tests guard against that regression.
"""

from __future__ import annotations

import pytest

from genereview_link.corpus.chunking import chunk_section_text

# Use a small max_tokens so multi-window logic is exercised even on short inputs.
_SMALL_MAX = 10
_SMALL_OVERLAP = 2


@pytest.mark.slow
def test_chunker_preserves_proper_case() -> None:
    """Proper-noun casing must survive the token-window round-trip."""
    # Repeat enough to force multiple windows with _SMALL_MAX.
    sentence = "Lynch syndrome (CRC) is caused by MLH1 mutations. "
    text = sentence * 6
    chunks = chunk_section_text(text, max_tokens=_SMALL_MAX, overlap_tokens=_SMALL_OVERLAP)
    assert len(chunks) >= 2, "expected multi-window chunking at max_tokens=10"
    joined = " ".join(c.text for c in chunks)
    assert "Lynch" in joined, "proper-noun casing lost: 'Lynch' not found"
    assert "MLH1" in joined, "gene-name casing lost: 'MLH1' not found"
    assert "Lynch syndrome" in joined, "capitalized 'Lynch syndrome' lost during chunking"
    assert "lynch syndrome" not in joined.replace("Lynch", "X"), (
        "lowercased form 'lynch syndrome' leaked into chunk text"
    )


@pytest.mark.slow
def test_chunker_preserves_punctuation_spacing() -> None:
    """Parentheses and hyphens must not acquire spurious spaces."""
    sentence = "Levels of low-density lipoprotein cholesterol (LDL-C) are elevated. "
    text = sentence * 6
    chunks = chunk_section_text(text, max_tokens=_SMALL_MAX, overlap_tokens=_SMALL_OVERLAP)
    assert len(chunks) >= 2, "expected multi-window chunking at max_tokens=10"
    joined = " ".join(c.text for c in chunks)
    assert "( LDL - C )" not in joined, "spurious spaces around parenthesised acronym detected"
    assert "low - density" not in joined, "spurious spaces inside hyphenated compound detected"
    # At least one chunk must contain the intact compound or acronym.
    assert "low-density" in joined or "(LDL-C)" in joined, (
        "neither 'low-density' nor '(LDL-C)' survived chunking"
    )


@pytest.mark.slow
def test_chunker_single_window_returns_original_text() -> None:
    """Fast-path (single window) must return the original string verbatim."""
    text = "Short text that fits in one window."
    chunks = chunk_section_text(text, max_tokens=510, overlap_tokens=50)
    assert len(chunks) == 1
    assert chunks[0].text == text


@pytest.mark.slow
def test_chunk_text_is_substring_of_original() -> None:
    """Every chunk's text must be a contiguous substring of the original."""
    sentence = "Hereditary breast and ovarian cancer (HBOC) is linked to BRCA1/BRCA2 variants. "
    text = sentence * 6
    chunks = chunk_section_text(text, max_tokens=_SMALL_MAX, overlap_tokens=_SMALL_OVERLAP)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.text in text, (
            f"chunk text is not a substring of original:\n  chunk: {chunk.text!r}"
        )
