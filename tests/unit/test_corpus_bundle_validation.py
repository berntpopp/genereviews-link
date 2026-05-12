"""Tests for bundle validation result helpers."""

from __future__ import annotations

from genereview_link.corpus.bundle_validation import BundleValidationResult


def test_validation_result_passes_when_no_errors() -> None:
    result = BundleValidationResult(errors=[], warnings=["passage count close to threshold"])

    assert result.ok is True
    assert result.as_manifest()["status"] == "passed"
    assert result.as_manifest()["warnings"] == ["passage count close to threshold"]


def test_validation_result_fails_when_errors_exist() -> None:
    result = BundleValidationResult(errors=["embeddings incomplete"], warnings=[])

    assert result.ok is False
    assert result.as_manifest()["status"] == "failed"
    assert result.as_manifest()["errors"] == ["embeddings incomplete"]
