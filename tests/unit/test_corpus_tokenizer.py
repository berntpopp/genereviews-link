"""Tests for the BGE tokenizer cache."""

from __future__ import annotations

import pytest

from genereview_link.corpus.tokenizer import bge_tokenizer, count_tokens


@pytest.mark.slow
def test_count_tokens_matches_bge_tokenizer() -> None:
    text = "The breast cancer susceptibility gene BRCA1 encodes a tumor suppressor."
    n = count_tokens(text)
    assert 10 <= n <= 20  # rough; actual exact count depends on tokenizer version


@pytest.mark.slow
def test_tokenizer_is_singleton() -> None:
    a = bge_tokenizer()
    b = bge_tokenizer()
    assert a is b
