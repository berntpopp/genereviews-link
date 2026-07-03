"""Unit coverage for genereview_link.mcp.envelope.build_error_envelope's
error_code/retryable classification, independent of the full FastMCP wrapper."""

from __future__ import annotations

from genereview_link.mcp.envelope import build_error_envelope


def test_known_code_maps_to_closed_enum_and_uses_recovery_hint() -> None:
    detail = {
        "code": "chapter_not_found",
        "message": "chapter 'NBK999' not in corpus",
        "recovery_hint": "check the NBK ID",
        "field_errors": [],
        "next_commands": [{"tool": "search_passages", "arguments": {}}],
    }

    envelope = build_error_envelope(
        "get_chapter_metadata",
        status_code=404,
        detail=detail,
        fallback_message="unused",
        request_id="r1",
        elapsed_ms=2.0,
    )

    assert envelope["success"] is False
    assert envelope["error_code"] == "not_found"
    assert envelope["retryable"] is False
    assert envelope["message"] == "chapter 'NBK999' not in corpus"
    assert envelope["recovery_action"] == "check the NBK ID"
    assert envelope["_meta"]["next_commands"] == [{"tool": "search_passages", "arguments": {}}]
    assert envelope["_meta"]["unsafe_for_clinical_use"] is True


def test_upstream_ncbi_unavailable_is_retryable() -> None:
    detail = {
        "code": "upstream_ncbi_unavailable",
        "message": "NCBI was unavailable while attempting to search GeneReviews.",
        "recovery_hint": "Retry later or use indexed corpus tools such as search_passages.",
        "field_errors": [],
        "next_commands": [],
    }

    envelope = build_error_envelope(
        "search_genereviews",
        status_code=502,
        detail=detail,
        fallback_message="unused",
        request_id="r2",
        elapsed_ms=3.0,
    )

    assert envelope["error_code"] == "upstream_unavailable"
    assert envelope["retryable"] is True


def test_unknown_code_falls_back_to_status_bucket() -> None:
    envelope = build_error_envelope(
        "get_table",
        status_code=429,
        detail=None,
        fallback_message="Too many requests",
        request_id="r3",
        elapsed_ms=1.0,
    )

    assert envelope["error_code"] == "rate_limited"
    assert envelope["retryable"] is True
    assert envelope["message"] == "Too many requests"
    # No field_errors were supplied -> the key must be omitted, not null-padded.
    assert "field_errors" not in envelope


def test_unmodeled_5xx_without_detail_is_upstream_unavailable() -> None:
    envelope = build_error_envelope(
        "get_fulltext",
        status_code=500,
        detail=None,
        fallback_message="ValueError: boom",
        request_id="r4",
        elapsed_ms=1.0,
    )

    # 500 has no explicit bucket entry; falls through the >=500 default.
    assert envelope["error_code"] == "upstream_unavailable"
    assert envelope["retryable"] is True
    assert envelope["message"] == "ValueError: boom"


def test_field_errors_are_preserved_when_present() -> None:
    detail = {
        "code": "gene_not_indexed",
        "message": "gene symbol 'ZZZ1' is not indexed in the corpus",
        "recovery_hint": "use the canonical HGNC symbol",
        "field_errors": [{"field": "gene", "reason": "symbol not found in the indexed corpus"}],
        "next_commands": [],
    }

    envelope = build_error_envelope(
        "search_passages",
        status_code=400,
        detail=detail,
        fallback_message="unused",
        request_id="r5",
        elapsed_ms=1.0,
    )

    assert envelope["field_errors"] == [
        {"field": "gene", "reason": "symbol not found in the indexed corpus"}
    ]
