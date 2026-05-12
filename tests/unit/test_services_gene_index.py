"""Unit tests for genereview_link.services.gene_index."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from genereview_link.services.gene_index import GeneIndex, load_gene_index

# ---------------------------------------------------------------------------
# GeneIndex dataclass tests
# ---------------------------------------------------------------------------


def test_gene_index_match_exact() -> None:
    idx = GeneIndex(symbols=frozenset({"BRCA1", "BRCA2", "MLH1"}))
    assert idx.is_indexed("BRCA1") is True
    assert idx.is_indexed("BRCA9") is False


def test_gene_index_close_matches_for_aliases() -> None:
    idx = GeneIndex(symbols=frozenset({"MLH1", "MSH2", "PMS2"}))
    suggestions = idx.close_matches("hMLH1", limit=3)
    assert "MLH1" in suggestions


def test_gene_index_close_matches_no_match_for_unrelated() -> None:
    idx = GeneIndex(symbols=frozenset({"BRCA1", "BRCA2", "MLH1"}))
    # A completely unrelated string should return empty list
    suggestions = idx.close_matches("ZZZZZZZZZ", limit=3, score_cutoff=70.0)
    assert suggestions == []


def test_gene_index_is_indexed_case_sensitive() -> None:
    idx = GeneIndex(symbols=frozenset({"BRCA1"}))
    assert idx.is_indexed("BRCA1") is True
    assert idx.is_indexed("brca1") is False


def test_gene_index_empty_set() -> None:
    idx = GeneIndex(symbols=frozenset())
    assert idx.is_indexed("BRCA1") is False
    assert idx.close_matches("BRCA1") == []


# ---------------------------------------------------------------------------
# load_gene_index with mock asyncpg pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_gene_index_mock_pool() -> None:
    """load_gene_index should build a GeneIndex from pool query results."""
    rows = [{"sym": "BRCA1"}, {"sym": "BRCA2"}, {"sym": "MLH1"}, {"sym": None}]

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=rows)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    idx = await load_gene_index(mock_pool)

    assert isinstance(idx, GeneIndex)
    assert idx.is_indexed("BRCA1") is True
    assert idx.is_indexed("BRCA2") is True
    assert idx.is_indexed("MLH1") is True
    # None values must be filtered out
    assert len(idx.symbols) == 3
