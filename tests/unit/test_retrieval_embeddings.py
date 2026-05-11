"""Tests for the embedding provider."""

from __future__ import annotations

import pytest

from genereview_link.retrieval.embeddings import (
    FakeEmbeddingProvider,
    bge_passage_text,
    bge_query_text,
)


def test_bge_query_prefix() -> None:
    assert bge_query_text("hello").startswith("Represent this sentence")


def test_bge_passage_text_is_identity() -> None:
    assert bge_passage_text("hello") == "hello"


@pytest.mark.asyncio
async def test_fake_provider_returns_correct_dim() -> None:
    p = FakeEmbeddingProvider(dim=384)
    v = await p.embed_query("test")
    assert len(v) == 384
    vs = await p.embed_passages(["a", "b"])
    assert all(len(x) == 384 for x in vs)
