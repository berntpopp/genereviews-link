"""Local sealed corpus handoff tests; no release service is involved."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import genereview_link.corpus.handoff as handoff
from genereview_link.corpus.bundle import BundleManifest, write_data_only_bundle
from genereview_link.corpus.handoff import (
    HandoffError,
    prepare_publish_handoff,
    seal_handoff,
    verify_data_only_bundle,
    verify_handoff,
    verify_rights_record,
)


def _source(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    (work / "corpus.dump").write_bytes(b"PGDMP-data")
    return write_data_only_bundle(
        work_dir=work,
        output=tmp_path / "source",
        manifest=BundleManifest(
            corpus_release_id="2026-08-30-r1",
            tarball_source_sha256="a" * 64,
            embedding={
                "model_name": "BAAI/bge-small-en-v1.5",
                "dimension": 384,
                "distance_metric": "cosine",
                "active_table": "genereview_embeddings_bge384",
                "count": 0,
                "expected_count": 0,
            },
            validation={"status": "passed", "smoke_queries": []},
        ),
    )


def _verified_manifest(release_id: str = "2026-08-30-r1") -> BundleManifest:
    return BundleManifest(
        corpus_release_id=release_id,
        tarball_source_sha256="a" * 64,
        embedding={
            "model_name": "BAAI/bge-small-en-v1.5",
            "dimension": 384,
            "distance_metric": "cosine",
            "active_table": "genereview_embeddings_bge384",
            "count": 0,
            "expected_count": 0,
        },
        validation={"status": "passed", "smoke_queries": []},
    )


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
        "source_sha256": "a" * 64,
        "artifact_sha256": hashlib.sha256(
            sealed.path.joinpath("corpus.dump").read_bytes()
        ).hexdigest(),
        "corpus_release_id": "2026-08-30-r1",
    }
    rights = tmp_path / "rights.json"
    rights.write_text(json.dumps(record))
    assert verify_rights_record(rights, sealed.object_id)["decision"] == "affirmative"
    record["decision"] = "pending"
    rights.write_text(json.dumps(record))
    with pytest.raises(HandoffError, match="affirmative"):
        verify_rights_record(rights, sealed.object_id)


def test_handoff_root_must_be_owner_only(tmp_path: Path) -> None:
    root = tmp_path / "handoffs"
    root.mkdir(mode=0o755)
    root.chmod(0o755)

    with pytest.raises(HandoffError, match="owner-only"):
        seal_handoff(_source(tmp_path), root)


def test_handoff_root_must_be_owned_by_the_invoking_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "handoffs"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(handoff.os, "geteuid", lambda: root.stat().st_uid + 1)

    with pytest.raises(HandoffError, match="owner-only"):
        seal_handoff(_source(tmp_path), root)


def test_capped_read_rejects_a_file_replaced_by_a_symlink_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim = tmp_path / "manifest.json"
    replacement = tmp_path / "replacement.json"
    victim.write_text('{"safe":true}')
    replacement.write_text('{"attacker":true}')
    original_open = handoff.os.open

    def swap_then_open(path: Path, flags: int, *args: object, **kwargs: object) -> int:
        if Path(path) == victim and not victim.is_symlink():
            victim.unlink()
            victim.symlink_to(replacement)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(handoff.os, "open", swap_then_open)
    with pytest.raises(HandoffError, match="unsafe"):
        handoff._read_capped(victim)


def test_digest_rejects_a_file_replaced_by_a_symlink_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim = tmp_path / "corpus.dump"
    replacement = tmp_path / "replacement.dump"
    victim.write_bytes(b"safe")
    replacement.write_bytes(b"attacker")
    original_open = handoff.os.open

    def swap_then_open(path: Path, flags: int, *args: object, **kwargs: object) -> int:
        if Path(path) == victim and not victim.is_symlink():
            victim.unlink()
            victim.symlink_to(replacement)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(handoff.os, "open", swap_then_open)
    with pytest.raises(HandoffError, match="unsafe"):
        handoff._sha256(victim)


def test_handoff_rejects_a_file_mode_changed_before_the_digest_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sealed = seal_handoff(_source(tmp_path), tmp_path / "handoffs")
    victim = sealed.path / "corpus.dump"
    original_open = handoff.os.open

    def relax_then_open(path: Path, flags: int, *args: object, **kwargs: object) -> int:
        if Path(path) == victim:
            victim.chmod(0o644)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(handoff.os, "open", relax_then_open)
    with pytest.raises(HandoffError, match="mode 0400"):
        verify_handoff(tmp_path / "handoffs", sealed.object_id)


def test_rights_record_must_bind_source_and_artifact_identity(tmp_path: Path) -> None:
    sealed = seal_handoff(_source(tmp_path), tmp_path / "handoffs")
    incomplete = {
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
    rights.write_text(json.dumps(incomplete))

    with pytest.raises(HandoffError, match="complete required"):
        verify_rights_record(rights, sealed.object_id)


def test_publisher_rejects_a_rights_record_for_a_different_source(tmp_path: Path) -> None:
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
        "source_sha256": "b" * 64,
        "artifact_sha256": hashlib.sha256(
            sealed.path.joinpath("corpus.dump").read_bytes()
        ).hexdigest(),
        "corpus_release_id": "2026-08-30-r1",
    }
    rights = tmp_path / "rights.json"
    rights.write_text(json.dumps(record))

    with pytest.raises(HandoffError, match="source_sha256"):
        prepare_publish_handoff(tmp_path / "handoffs", sealed.object_id, rights)


def test_data_only_verifier_rejects_extra_file(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "corpus.dump").write_bytes(b"PGDMP-data")
    source = write_data_only_bundle(
        work_dir=work,
        output=tmp_path / "source",
        manifest=_verified_manifest(),
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
        manifest=_verified_manifest("not-a-release"),
    )
    with pytest.raises(HandoffError, match="corpus_release_id"):
        verify_data_only_bundle(source)
