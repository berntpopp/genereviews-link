"""Regressions for final ingest and publication transaction review findings."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

import genereview_link.corpus.handoff as handoff
import genereview_link.corpus.pipeline as pipeline
import genereview_link.corpus.release_assets as release_assets
import genereview_link.corpus.rights as rights_module
from genereview_link.corpus.handoff import HandoffError, SealedHandoff
from genereview_link.corpus.release_assets import ReleaseAssetError
from genereview_link.corpus.rights import (
    RIGHTS_ATTRIBUTION,
    RIGHTS_AUTHORITY,
    RIGHTS_AUTHORIZATION_URI,
    RIGHTS_PERMITTED_ASSET_USE,
    RightsError,
)
from genereview_link.corpus.source_identity import SIDEDATA_FILES

ROOT = Path(__file__).resolve().parents[2]
TERMS = (
    "<html>GeneReviews® ©1993-2026 University of Washington, Seattle; "
    "https://www.genereviews.org; noncommercial research purposes only; copyright notice; "
    "Usage Disclaimer; no further modifications.</html>\n"
).encode()


@pytest.mark.asyncio
async def test_offline_ingest_parses_only_admitted_private_source_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "gene_NBK1116.tar.gz"
    archive.write_bytes(b"owned archive")
    side_data = tmp_path / "side"
    side_data.mkdir()
    for name in SIDEDATA_FILES:
        (side_data / name).write_bytes(f"owned {name}".encode())
    metadata = tmp_path / "source-capture.json"
    prior = tmp_path / "prior-manifest.json"
    prior_seal = tmp_path / "prior-seal-manifest.json"
    for path in (metadata, prior, prior_seal):
        path.write_bytes(b"fixture")

    def validate(*_args: object, **kwargs: object) -> dict[str, object]:
        admitted_archive = kwargs["archive"]
        admitted_side = kwargs["side_data_dir"]
        assert isinstance(admitted_archive, Path) and isinstance(admitted_side, Path)
        assert admitted_archive.read_bytes() == b"owned archive"
        for name in SIDEDATA_FILES:
            assert (admitted_side / name).read_bytes() == f"owned {name}".encode()
        archive.write_bytes(b"foreign archive")
        for name in SIDEDATA_FILES:
            (side_data / name).write_bytes(f"foreign {name}".encode())
        return {
            "listing": {"relpath": "gene_NBK1116.tar.gz", "last_updated": "2026-09-01"},
            "archive": {"sha256": "a" * 64},
            "side_data": {name: {} for name in SIDEDATA_FILES},
        }

    async def ingest(_pool: object, **kwargs: object) -> str:
        admitted_archive = kwargs["tarball"]
        admitted_side = kwargs["sidedata_dir"]
        assert isinstance(admitted_archive, Path) and isinstance(admitted_side, Path)
        assert admitted_archive != archive and admitted_side != side_data
        assert admitted_archive.read_bytes() == b"owned archive"
        for name in SIDEDATA_FILES:
            assert (admitted_side / name).read_bytes() == f"owned {name}".encode()
        return "admitted"

    monkeypatch.setattr(pipeline, "load_offline_capture", validate)
    monkeypatch.setattr(pipeline, "_ingest_files", ingest)

    result = await pipeline._run_full_ingest_locked(
        object(),  # type: ignore[arg-type]
        archive=archive,
        side_data_dir=side_data,
        source_metadata=metadata,
        prior_manifest=prior,
        prior_seal_manifest=prior_seal,
    )

    assert result == "admitted"


def test_secret_bearing_jobs_are_main_only_before_checkout_or_secret_access() -> None:
    release = yaml.safe_load((ROOT / ".github/workflows/corpus-data-release.yml").read_text())
    verifier = yaml.safe_load((ROOT / ".github/workflows/verify-corpus-bundle.yml").read_text())
    build = release["jobs"]["build"]
    verify = verifier["jobs"]["verify"]

    assert "github.ref == 'refs/heads/main'" in build["if"]
    assert verify["if"] == "${{ github.ref == 'refs/heads/main' }}"
    assert "GENEREVIEWS_SOURCE_LOCATOR" not in build.get("env", {})
    assert "GENEREVIEWS_SOURCE_LOCATOR" not in verify.get("env", {})
    assert build["steps"][0]["uses"].startswith("actions/checkout@")
    assert verify["steps"][0]["uses"].startswith("actions/checkout@")


def test_annotated_tag_is_created_only_at_final_serialized_transaction_point() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/corpus-data-release.yml").read_text())
    gate = next(
        step["run"]
        for step in workflow["jobs"]["publish"]["steps"]
        if step.get("name") == "Four-state immutable publication gate"
    )

    object_create = gate.index('--method POST "repos/$GH_REPO/git/tags"')
    ref_create = gate.index('--method POST "repos/$GH_REPO/git/refs"')
    acceptance = gate.index("dispatch_verifier prepublication")
    transaction = gate.index("ensure_exact_tag", acceptance)
    patch = gate.index('--method PATCH "repos/$GH_REPO/releases/$release_id"')
    first_upload = gate.index("data-binary")
    initial_state = gate.index("read_tag_state")

    assert initial_state < first_upload
    assert object_create < ref_create
    assert acceptance < transaction < patch
    assert "verify_existing_tag_state" in gate
    assert "tag race resolved to the exact annotated object" in gate


def _rights_record(
    root: Path,
    *,
    decision_time: str,
    terms_version: str,
    bind_terms_in_evidence: bool,
) -> Path:
    terms = root / "terms-snapshot.html"
    terms.write_bytes(TERMS)
    terms_sha256 = hashlib.sha256(TERMS).hexdigest()
    record: dict[str, object] = {
        "artifact_sha256": "2" * 64,
        "object_id": "1" * 64,
        "decision": "affirmative",
        "approval_kind": "repository-owner redistribution determination",
        "upstream_approval": False,
        "responsible_reviewer": "reviewer@example.org",
        "rights_authority": RIGHTS_AUTHORITY,
        "authorization_uri": RIGHTS_AUTHORIZATION_URI,
        "decision_time": decision_time,
        "terms_uri": "bundle:terms-snapshot.html",
        "terms_sha256": terms_sha256,
        "terms_version": terms_version,
        "terms_source_uri": "https://www.genereviews.org/",
        "permitted_asset_use": RIGHTS_PERMITTED_ASSET_USE,
        "attribution": RIGHTS_ATTRIBUTION,
        "evidence_uri": "bundle:rights-evidence.json",
        "evidence_sha256": "",
        "source_sha256": "3" * 64,
        "corpus_release_id": "2026-09-01-r1",
    }
    evidence_fields = {
        "approval_kind",
        "upstream_approval",
        "rights_authority",
        "responsible_reviewer",
        "authorization_uri",
        "decision_time",
        "terms_source_uri",
        "permitted_asset_use",
        "attribution",
        "object_id",
        "source_sha256",
        "artifact_sha256",
        "corpus_release_id",
    }
    if bind_terms_in_evidence:
        evidence_fields |= {"terms_version", "terms_sha256"}
    evidence = {
        "format": "genereviews-owner-rights-evidence-v1",
        **{name: record[name] for name in evidence_fields},
    }
    evidence_path = root / "rights-evidence.json"
    evidence_path.write_text(json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n")
    record["evidence_sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    record["rights_record_sha256"] = hashlib.sha256(
        (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    rights = root / "rights-record.json"
    rights.write_text(json.dumps(record))
    return rights


def test_rights_record_rejects_pre_authorization_decision(tmp_path: Path) -> None:
    rights = _rights_record(
        tmp_path,
        decision_time="2026-08-30T12:00:00Z",
        terms_version="2026-08",
        bind_terms_in_evidence=False,
    )
    with pytest.raises(RightsError, match="2026-09-01 owner authorization"):
        rights_module.verify_rights_record(rights, "1" * 64)


def test_rights_record_requires_exact_reviewed_terms_version(tmp_path: Path) -> None:
    rights = _rights_record(
        tmp_path,
        decision_time="2026-09-01T00:00:00Z",
        terms_version="floating-current",
        bind_terms_in_evidence=False,
    )
    with pytest.raises(RightsError, match="terms version"):
        rights_module.verify_rights_record(rights, "1" * 64)


def test_rights_evidence_binds_exact_reviewed_terms_snapshot(tmp_path: Path) -> None:
    rights = _rights_record(
        tmp_path,
        decision_time="2026-09-01T00:00:00Z",
        terms_version="2026-09-01",
        bind_terms_in_evidence=True,
    )
    record = rights_module.verify_rights_record(rights, "1" * 64)
    assert record["terms_version"] == "2026-09-01"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    (
        b'{"id":1,"id":2}',
        b'{"id":NaN}',
        b"[" * 10_000 + b"]" * 10_000,
    ),
    ids=("duplicate", "nonfinite", "deep"),
)
async def test_release_metadata_uses_strict_bounded_domain_parser(
    raw: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def payload(*_args: object, **_kwargs: object) -> bytes:
        return raw

    monkeypatch.setattr(release_assets.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr(release_assets, "read_capped", payload)

    with pytest.raises(ReleaseAssetError, match="strict bounded JSON"):
        await release_assets._release_assets("owner/repo", "tag", "")


def test_publisher_gate_rejects_duplicate_seal_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = (
        b'{"source_sha256":"'
        + b"a" * 64
        + b'","source_sha256":"'
        + b"b" * 64
        + b'","artifact_sha256":"'
        + b"c" * 64
        + b'","corpus_release_id":"2026-09-01-r1"}'
    )
    manifest = tmp_path / "seal-manifest.json"
    manifest.write_bytes(raw)
    sealed = SealedHandoff(hashlib.sha256(raw).hexdigest(), tmp_path, manifest)
    monkeypatch.setattr(rights_module, "verify_rights_record", lambda *_args, **_kwargs: {})

    with pytest.raises(HandoffError, match="strict bounded JSON"):
        handoff.verify_rights_record(tmp_path / "rights.json", sealed.object_id, sealed=sealed)


def test_publisher_gate_reopens_and_rebinds_seal_manifest_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = json.dumps(
        {
            "source_sha256": "a" * 64,
            "artifact_sha256": "b" * 64,
            "corpus_release_id": "2026-09-01-r1",
        }
    ).encode()
    manifest = tmp_path / "seal-manifest.json"
    manifest.write_bytes(original)
    sealed = SealedHandoff(hashlib.sha256(original).hexdigest(), tmp_path, manifest)
    manifest.write_text(
        json.dumps(
            {
                "source_sha256": "c" * 64,
                "artifact_sha256": "d" * 64,
                "corpus_release_id": "2026-09-01-r2",
            }
        )
    )
    monkeypatch.setattr(rights_module, "verify_rights_record", lambda *_args, **_kwargs: {})

    with pytest.raises(HandoffError, match="sealed manifest identity changed"):
        handoff.verify_rights_record(tmp_path / "rights.json", sealed.object_id, sealed=sealed)
