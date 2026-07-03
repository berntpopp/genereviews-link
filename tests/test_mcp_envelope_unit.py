"""Unit coverage for genereview_link.mcp.envelope.build_success_envelope across
every registered MCP tool.

Complements tests/test_mcp_response_envelope.py (which proves the FastMCP
wrapper machinery works end to end for a couple of representative tools) with
fast, exhaustive per-tool coverage of the PRIMARY_KEY_MAP classification —
catching a misclassified tool (wrong `results`/`result` key, wrong source_key)
without needing to stand up a full FastAPI app + repository mocks for all 13
tools.
"""

from __future__ import annotations

from typing import Any

import pytest

from genereview_link.mcp.envelope import PRIMARY_KEY_MAP, build_success_envelope

# One representative raw REST JSON body per tool, using the tool's actual
# response-model field names (see genereview_link/models/genereview_models.py).
_RAW_PAYLOADS: dict[str, dict[str, Any]] = {
    "search_genereviews": {
        "count": 1,
        "retmax": 20,
        "retstart": 0,
        "ids": ["20301425"],
        "webenv": "",
        "querykey": "",
        "corpus_version": None,
        "_meta": {"attribution": "x"},
    },
    "get_genereview_summary": {
        "gene_symbol": "BRCA1",
        "pubmed_id": "20301425",
        "book_url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
        "title": "HBOC",
        "other_sections": {},
        "corpus_version": None,
        "_meta": {"attribution": "x"},
    },
    "get_abstract": {
        "pmid": "20301425",
        "title": "HBOC",
        "abstract": "text",
        "authors": [],
        "journal": "GeneReviews",
        "publication_date": "1998",
        "corpus_version": None,
        "_meta": {"attribution": "x"},
    },
    "get_fulltext": {
        "nbk_id": "NBK1247",
        "url": "https://www.ncbi.nlm.nih.gov/books/NBK1247/",
        "title": "HBOC",
        "sections": {},
        "metadata": {},
        "corpus_version": None,
        "_meta": {"attribution": "x"},
    },
    "get_links": {
        "urls": ["https://www.ncbi.nlm.nih.gov/books/NBK1247/"],
        "link_entries": None,
        "by_type": {},
        "corpus_version": None,
        "_meta": {"attribution": "x"},
    },
    "search_passages": {
        "results": [{"passage_id": "NBK1247:0001"}],
        "_meta": {"attribution": "x"},
    },
    "search_passages_batch": {
        "results": [{"query_index": 0, "q": "BRCA1", "hits": []}],
        "_meta": {"attribution": "x"},
    },
    "get_passage": {
        "passage": {"passage_id": "NBK1247:0001"},
        "neighbors_before": [],
        "neighbors_after": [],
        "has_more_before": False,
        "has_more_after": False,
        "_meta": {"attribution": "x"},
    },
    "get_passages_batch": {
        "passages": [{"passage_id": "NBK1247:0001"}],
        "missing_ids": [],
        "_meta": {"attribution": "x"},
    },
    "get_chapter_section": {
        "nbk_id": "NBK1247",
        "chapter_title": "HBOC",
        "chapter_section": "management",
        "chapter_last_updated": None,
        "passages": [{"passage_id": "NBK1247:0001"}],
        "passage_count": 1,
        "_meta": {"attribution": "x"},
    },
    "get_chapter_metadata": {
        "nbk_id": "NBK1247",
        "title": "HBOC",
        "gene_symbols": ["BRCA1"],
        "sections": [],
        "table_count": 0,
        "tables": [],
        "_meta": {"attribution": "x"},
    },
    "get_table": {
        "nbk_id": "NBK1247",
        "table_id": "management-1",
        "caption": "Surveillance",
        "section": "management",
        "header": ["Age"],
        "rows": [["18-25"]],
        "passage_id": "NBK1247:0010",
        "_meta": {"attribution": "x"},
    },
    "get_license": {
        "copyright": "© 1993-present",
        "terms_url": "https://x",
        "data_source": "NCBI Bookshelf — GeneReviews",
        "data_source_url": "https://x",
        "notes": "x",
        "license_spdx": "LicenseRef-GeneReviews",
        "attribution_text": "x",
        # No `_meta` key at all — LicenseNotice carries none.
    },
}


def test_every_registered_tool_has_a_test_payload() -> None:
    """Guard: PRIMARY_KEY_MAP and this fixture must stay in sync."""
    assert set(_RAW_PAYLOADS) == set(PRIMARY_KEY_MAP)


@pytest.mark.parametrize("tool_name", sorted(PRIMARY_KEY_MAP))
def test_build_success_envelope_banner_keys(tool_name: str) -> None:
    raw = _RAW_PAYLOADS[tool_name]
    envelope = build_success_envelope(tool_name, raw, request_id="req-1", elapsed_ms=1.0)

    assert envelope["success"] is True
    meta = envelope["_meta"]
    assert meta["tool"] == tool_name
    assert meta["request_id"] == "req-1"
    assert meta["unsafe_for_clinical_use"] is True
    assert meta["source"] == "genereviews"
    assert "capabilities_version" in meta


@pytest.mark.parametrize(
    "tool_name",
    [name for name, spec in PRIMARY_KEY_MAP.items() if spec.kind == "collection"],
)
def test_collection_tools_promote_results_to_top_level(tool_name: str) -> None:
    raw = _RAW_PAYLOADS[tool_name]
    envelope = build_success_envelope(tool_name, raw, request_id="req-1", elapsed_ms=1.0)

    assert "results" in envelope
    assert "result" not in envelope
    assert isinstance(envelope["results"], list)


@pytest.mark.parametrize(
    "tool_name",
    [name for name, spec in PRIMARY_KEY_MAP.items() if spec.kind == "single"],
)
def test_single_item_tools_nest_payload_under_result(tool_name: str) -> None:
    raw = _RAW_PAYLOADS[tool_name]
    envelope = build_success_envelope(tool_name, raw, request_id="req-1", elapsed_ms=1.0)

    assert "result" in envelope
    assert "results" not in envelope
    assert isinstance(envelope["result"], dict)
    assert "_meta" not in envelope["result"]


def test_search_genereviews_renames_ids_to_results() -> None:
    envelope = build_success_envelope(
        "search_genereviews", _RAW_PAYLOADS["search_genereviews"], request_id="r", elapsed_ms=1.0
    )
    assert envelope["results"] == ["20301425"]
    assert "ids" not in envelope
    # Sibling domain fields ride beside `results`.
    assert envelope["count"] == 1
    assert envelope["webenv"] == ""


def test_get_passages_batch_renames_passages_to_results() -> None:
    envelope = build_success_envelope(
        "get_passages_batch",
        _RAW_PAYLOADS["get_passages_batch"],
        request_id="r",
        elapsed_ms=1.0,
    )
    assert envelope["results"] == [{"passage_id": "NBK1247:0001"}]
    assert "passages" not in envelope
    assert envelope["missing_ids"] == []


def test_search_passages_results_pass_through_unrenamed() -> None:
    envelope = build_success_envelope(
        "search_passages", _RAW_PAYLOADS["search_passages"], request_id="r", elapsed_ms=1.0
    )
    assert envelope["results"] == [{"passage_id": "NBK1247:0001"}]


def test_get_license_builds_meta_when_source_has_none() -> None:
    """LicenseNotice carries no `_meta` field at all; the envelope must still build one."""
    envelope = build_success_envelope(
        "get_license", _RAW_PAYLOADS["get_license"], request_id="r", elapsed_ms=1.0
    )
    assert envelope["result"]["data_source"] == "NCBI Bookshelf — GeneReviews"
    assert envelope["_meta"]["tool"] == "get_license"
