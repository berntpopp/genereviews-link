"""Structural untrusted-text fencing contracts."""

from __future__ import annotations

import hashlib

import pytest

from genereview_link.mcp.untrusted_content import (
    UntrustedTextLimitError,
    enforce_untrusted_text_limits,
    fence_untrusted_text,
)


def test_fence_normalizes_and_removes_forbidden_controls() -> None:
    raw = "Cafe\u0301\x00\u200b\u202e\nBRCA1"
    fenced = fence_untrusted_text(raw, source="genereviews", record_id="NBK1116:0042")

    assert fenced.kind == "untrusted_text"
    assert fenced.text == "Caf\u00e9\nBRCA1"
    assert fenced.raw_sha256 == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert fenced.provenance.source == "genereviews"
    assert fenced.provenance.record_id == "NBK1116:0042"


def test_fence_preserves_tabs_newlines_and_scientific_symbols() -> None:
    raw = "p.Gly12Asp\t\u0394G = \u22121.2 kcal/mol\r\n"
    assert fence_untrusted_text(raw, source="genereviews", record_id="NBK1116:0042").text == raw


def test_limits_reject_oversized_object() -> None:
    big = fence_untrusted_text("x" * 10, source="genereviews", record_id="NBK1116:0042")
    with pytest.raises(UntrustedTextLimitError):
        enforce_untrusted_text_limits([big], max_text_bytes=5)


def test_guard_maps_limit_breach_to_typed_response_too_large() -> None:
    """A limit breach surfaces as a typed 413 response_too_large, not internal_error."""
    from genereview_link.api.errors import StructuredHTTPException
    from genereview_link.api.untrusted_limits import guard_untrusted_limits
    from genereview_link.mcp.envelope import _ERROR_CODE_MAP

    obj = fence_untrusted_text("x" * 10, source="genereviews", record_id="NBK1116:0042")
    with pytest.raises(StructuredHTTPException) as exc_info:
        guard_untrusted_limits([obj, obj], max_objects=1)
    assert exc_info.value.status_code == 413
    assert exc_info.value.detail["code"] == "response_too_large"
    # And the envelope classifies it as a typed invalid_input, never internal_error.
    assert _ERROR_CODE_MAP["response_too_large"] == ("invalid_input", False)


def test_guard_allows_large_but_bounded_object_count() -> None:
    """The generous default ceiling does not trip on a legitimately wide result."""
    from genereview_link.api.untrusted_limits import guard_untrusted_limits

    objs = [
        fence_untrusted_text("cell", source="genereviews", record_id=f"NBK1:table:t1#r{i}")
        for i in range(500)
    ]
    guard_untrusted_limits(objs)  # must not raise (500 < 10000 ceiling)


def test_collect_untrusted_json_reconstructs_fenced_dicts() -> None:
    """The batch collector finds already-serialized fenced dict nodes (search_passages_batch)."""
    from genereview_link.api.untrusted_limits import collect_untrusted_json

    fenced = fence_untrusted_text(
        "hostile", source="genereviews", record_id="NBK1:0001"
    ).model_dump(mode="json")
    # Shape mirrors search_passages_batch: list[list[hit-dict]] with nested fenced fields.
    payload = [[{"passage_id": "NBK1:0001", "text": fenced, "snippet": None}]]
    found = collect_untrusted_json(payload)
    assert len(found) == 1
    assert found[0].text == "hostile"
    assert found[0].kind == "untrusted_text"
