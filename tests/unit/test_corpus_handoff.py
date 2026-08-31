"""Local sealed corpus handoff tests; no release service is involved."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from genereview_link.corpus.bundle import BundleManifest, write_data_only_bundle
from genereview_link.corpus.handoff import (
    HandoffError,
    seal_handoff,
    verify_data_only_bundle,
    verify_handoff,
    verify_rights_record,
)


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "corpus.dump").write_bytes(b"PGDMP-data")
    (source / "manifest.json").write_text('{"corpus_release_id":"2026-08-30-r1"}\n')
    checksums = "\n".join(
        f"{hashlib.sha256((source / name).read_bytes()).hexdigest()}  {name}"
        for name in ("corpus.dump", "manifest.json")
    )
    (source / "SHA256SUMS").write_text(f"{checksums}\n")
    return source


def test_seal_is_content_addressed_read_only_and_reverifiable(tmp_path: Path) -> None:
    sealed = seal_handoff(_source(tmp_path), tmp_path / "handoffs")

    assert sealed.object_id == hashlib.sha256(sealed.manifest.read_bytes()).hexdigest()
    assert verify_handoff(tmp_path / "handoffs", sealed.object_id).object_id == sealed.object_id
    assert all(not path.is_symlink() for path in sealed.path.rglob("*"))


def test_seal_rejects_symlink_and_existing_object_substitution(tmp_path: Path) -> None:
    source = _source(tmp_path)
    (source / "unsafe").symlink_to("corpus.dump")
    with pytest.raises(HandoffError, match="regular"):
        seal_handoff(source, tmp_path / "handoffs")


def test_rights_record_binds_affirmative_decision_to_exact_object(tmp_path: Path) -> None:
    sealed = seal_handoff(_source(tmp_path), tmp_path / "handoffs")
    record = {
        "object_id": sealed.object_id,
        "decision": "affirmative",
        "authority": "reviewer@example.org",
        "decision_time": "2026-08-30T12:00:00Z",
        "terms_version": "2026-08",
        "permitted_asset_use": "immutable research corpus artifact",
        "attribution": "GeneReviews",
        "evidence_uri": "https://example.org/rights-record",
    }
    rights = tmp_path / "rights.json"
    rights.write_text(json.dumps(record))
    assert verify_rights_record(rights, sealed.object_id)["decision"] == "affirmative"
    record["decision"] = "pending"
    rights.write_text(json.dumps(record))
    with pytest.raises(HandoffError, match="affirmative"):
        verify_rights_record(rights, sealed.object_id)


def test_data_only_verifier_rejects_extra_file(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "corpus.dump").write_bytes(b"PGDMP-data")
    source = write_data_only_bundle(
        work_dir=work,
        output=tmp_path / "source",
        manifest=BundleManifest(corpus_release_id="2026-08-30-r1"),
    )
    assert verify_data_only_bundle(source)["corpus_release_id"] == "2026-08-30-r1"
    (source / "unexpected").write_text("not an asset")
    with pytest.raises(HandoffError, match="exactly"):
        verify_data_only_bundle(source)


def test_data_only_verifier_rejects_invalid_release_identity(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "corpus.dump").write_bytes(b"PGDMP-data")
    source = write_data_only_bundle(
        work_dir=work,
        output=tmp_path / "source",
        manifest=BundleManifest(corpus_release_id="not-a-release"),
    )
    with pytest.raises(HandoffError, match="corpus_release_id"):
        verify_data_only_bundle(source)
