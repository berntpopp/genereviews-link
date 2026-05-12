"""Tests for query intent detection used by retrieval reranking."""

from __future__ import annotations

import pytest

from genereview_link.retrieval.rerank import detect_query_intents


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("What treatment options are available?", ["management"]),
        ("How is diagnosis confirmed?", ["diagnosis"]),
        ("Autosomal inheritance and penetrance", ["genetics"]),
    ],
)
def test_detect_query_intents_single_intent(query: str, expected: list[str]) -> None:
    assert detect_query_intents(query) == expected


def test_detect_query_intents_stacks_multiple_intents_sorted() -> None:
    query = "Diagnostic criteria and treatment for autosomal disease"

    assert detect_query_intents(query) == ["diagnosis", "genetics", "management"]


def test_detect_query_intents_returns_empty_list_without_matches() -> None:
    assert detect_query_intents("foo bar baz") == []
