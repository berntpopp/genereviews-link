"""Tests for chunking."""

from __future__ import annotations

import pytest

from genereview_link.corpus.chunking import chunk_section_text


@pytest.mark.slow
def test_short_section_yields_one_chunk() -> None:
    text = "Short summary of the disease and its inheritance pattern."
    chunks = chunk_section_text(text, max_tokens=510, overlap_tokens=50)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].chunk_index == 0


@pytest.mark.slow
def test_long_section_splits_with_overlap() -> None:
    text = ". ".join(f"Sentence number {i} discussing pathogenic variants" for i in range(200))
    chunks = chunk_section_text(text, max_tokens=510, overlap_tokens=50)
    assert len(chunks) >= 2
    # adjacent chunks must overlap
    a = chunks[0].text
    b = chunks[1].text
    # the last ~50 tokens of a should appear at start of b
    assert any(word in a and word in b for word in b.split()[:20])


@pytest.mark.slow
def test_chunks_index_is_sequential() -> None:
    text = ". ".join(f"Word {i}" for i in range(2000))
    chunks = chunk_section_text(text, max_tokens=510, overlap_tokens=50)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
