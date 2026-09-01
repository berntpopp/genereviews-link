"""Executable regressions for the second corpus publication security review."""

from __future__ import annotations

import hashlib
import json
import sys
import tarfile
import types
from collections.abc import Callable
from pathlib import Path

import pytest

from genereview_link.corpus import computation_provenance, evaluation
from genereview_link.corpus.dispatch_identity import DispatchIdentityError, verify_acceptance
from genereview_link.corpus.evaluation import (
    EVALUATION_SUITE,
    EvaluationRejectedError,
    assert_evaluation_accepted,
)
from genereview_link.corpus.evaluation_contract import EVALUATION_SUITE_SHA256
from genereview_link.corpus.pg_client import (
    PG18_IMAGE,
    PgClientError,
    assert_client_server_match,
    build_pg_client_command,
)
from genereview_link.corpus.release_selection import ReleaseSlot, select_release_id
from genereview_link.corpus.semantic_identity import compute_content_identity
from genereview_link.corpus.source_capture import (
    SourceCaptureError,
    archive_content_identities,
    load_offline_capture,
)
from genereview_link.db.restore import ArchivePolicyError, validate_restore_endpoint
from genereview_link.retrieval.model_identity import BGE_MODEL_FILES


def test_computation_provenance_captures_full_runtime_at_compute_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = tmp_path / "model"
    (model / "nested").mkdir(parents=True)
    (model / "config.json").write_bytes(b"reviewed config")
    (model / "nested" / "weights.bin").write_bytes(b"reviewed weights")
    model_files = {
        path.relative_to(model).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in model.rglob("*")
        if path.is_file()
    }

    def show_config() -> None:
        print("fixture-blas=single-threaded")

    class FakeNumpy(types.ModuleType):
        show_config: Callable[[], None]

    numpy = FakeNumpy("numpy")
    numpy.show_config = show_config

    class FakeTorch(types.ModuleType):
        __version__: str
        version: object
        backends: object
        cuda: object

    torch = FakeTorch("torch")
    torch.__version__ = "2.13.0+cpu"
    torch.version = types.SimpleNamespace(cuda=None)
    torch.backends = types.SimpleNamespace(cudnn=types.SimpleNamespace(version=lambda: None))
    torch.cuda = types.SimpleNamespace(
        is_available=lambda: False, get_device_name=lambda _index: "none"
    )

    def snapshot_download(*_args: object, **_kwargs: object) -> str:
        return str(model)

    class FakeHub(types.ModuleType):
        snapshot_download: Callable[..., str]

    hub = FakeHub("huggingface_hub")
    hub.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "numpy", numpy)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setattr(computation_provenance, "BGE_MODEL_FILES", model_files)

    class Distribution:
        def __init__(self) -> None:
            self.metadata = {"Name": "fixture-runtime"}
            self.version = "1.0"

    versions = {
        "sentence-transformers": "5.1.0",
        "transformers": "4.55.2",
        "hatchling": "1.27.0",
    }
    monkeypatch.setattr(
        computation_provenance.importlib.metadata,
        "distributions",
        lambda: [Distribution()],
    )
    monkeypatch.setattr(
        computation_provenance.importlib.metadata,
        "version",
        lambda name: versions[name],
    )

    provenance = computation_provenance.collect_computation_provenance(app_git_sha="a" * 40)

    assert provenance["schema"] == "genereviews-computation-v2"
    assert provenance["source"]["app_git_sha"] == "a" * 40
    assert provenance["environment"]["installed_distributions_sha256"]
    assert provenance["environment"]["uv_version"].startswith("uv ")
    assert provenance["environment"]["os"]
    assert provenance["environment"]["kernel"]
    assert provenance["environment"]["libc"]
    assert provenance["environment"]["cpu"]
    assert provenance["environment"]["blas"]
    assert provenance["environment"]["build_backend"]
    assert provenance["environment"]["torch"] == "2.13.0+cpu"
    assert provenance["environment"]["installed_distributions"] == ["fixture-runtime==1.0"]
    assert provenance["database"]["client_major"] == "18"
    assert provenance["model"]["files"] == model_files


def test_pg18_client_is_digest_pinned_and_rejects_host_major_mismatch(tmp_path: Path) -> None:
    command = build_pg_client_command("pg_dump", ["--version"], mounts=(tmp_path,), network="host")

    assert command[0:2] == ["docker", "run"]
    assert PG18_IMAGE in command
    assert command[-2:] == ["pg_dump", "--version"]
    with pytest.raises(PgClientError, match=r"major 16.*server major 18"):
        assert_client_server_match("pg_dump (PostgreSQL) 16.10", "180006")
    assert_client_server_match("pg_dump (PostgreSQL) 18.6", "180006")


def _capture_metadata(archive: Path, side_dir: Path) -> dict[str, object]:
    def identity(path: Path) -> dict[str, object]:
        return {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size,
        }

    side_names = (
        "GRtitle_shortname_NBKid.txt",
        "NBKid_shortname_genesymbol.txt",
        "NBKid_shortname_OMIM.txt",
    )
    members_sha256, expanded_sha256 = archive_content_identities(archive)
    listing = b"ca/84/gene_NBK1116.tar.gz,GeneReviews,NCBI,1993,NBK1116,2026-08-31 02:41:04\n"
    (archive.parent / "file_list.csv").write_bytes(listing)
    return {
        "format": "genereviews-offline-source-v1",
        "listing": {
            "url": "https://ftp.ncbi.nlm.nih.gov/pub/litarch/file_list.csv",
            "raw_sha256": hashlib.sha256(listing).hexdigest(),
            "raw_size_bytes": len(listing),
            "captured_at": "2026-08-31T03:00:00Z",
            "integrity_class": "https-captured-untrusted",
            "relpath": "ca/84/gene_NBK1116.tar.gz",
            "last_updated": "2026-08-31 02:41:04",
        },
        "archive": {
            "url": "https://ftp.ncbi.nlm.nih.gov/pub/litarch/ca/84/gene_NBK1116.tar.gz",
            **identity(archive),
            "members_sha256": members_sha256,
            "expanded_sha256": expanded_sha256,
        },
        "side_data": {
            name: {
                "url": f"https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews/{name}",
                **identity(side_dir / name),
            }
            for name in side_names
        },
        "chapter_ids": ["NBK1", "NBK2"],
        "prior_artifact": {
            "object_id": "d" * 64,
            "chapter_ids": ["NBK1"],
            "chapter_count": 1,
            "chapter_digests": {"NBK1": "e" * 64},
            "chapters_sha256": "f" * 64,
            "passages_sha256": "0" * 64,
        },
    }


def _write_side_data(side_dir: Path) -> None:
    (side_dir / "GRtitle_shortname_NBKid.txt").write_text(
        "one\tChapter one\tNBK1\t1\ntwo\tChapter two\tNBK2\t2\n"
    )
    (side_dir / "NBKid_shortname_genesymbol.txt").write_text("NBK1\tone\tGENE1\nNBK2\ttwo\tGENE2\n")
    (side_dir / "NBKid_shortname_OMIM.txt").write_text("NBK1\tone\t100001\nNBK2\ttwo\t100002\n")


def _write_prior_manifest(tmp_path: Path, capture: dict[str, object]) -> tuple[Path, Path]:
    prior = capture["prior_artifact"]
    assert isinstance(prior, dict)
    manifest = tmp_path / "prior-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_version": "3",
                "corpus_release_id": "2026-08-01-r1",
                "app_git_sha": "1" * 40,
                "content_identity": {
                    key: prior[key]
                    for key in (
                        "chapter_ids",
                        "chapter_count",
                        "chapter_digests",
                        "chapters_sha256",
                        "passages_sha256",
                    )
                },
            },
            sort_keys=True,
        )
    )
    prior["manifest_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    prior["corpus_release_id"] = "2026-08-01-r1"
    prior["app_git_sha"] = "1" * 40
    seal = tmp_path / "prior-seal-manifest.json"
    seal.write_text(
        json.dumps(
            {
                "format": "genereviews-local-handoff-v1",
                "corpus_release_id": prior["corpus_release_id"],
                "files": [
                    {
                        "name": "manifest.json",
                        "sha256": prior["manifest_sha256"],
                        "size": manifest.stat().st_size,
                        "mode": 0o400,
                    }
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    prior["object_id"] = hashlib.sha256(seal.read_bytes()).hexdigest()
    return manifest, seal


@pytest.mark.parametrize(
    "raw",
    (
        b'{"format":"genereviews-offline-source-v1","format":"shadow"}',
        b'{"format":"genereviews-offline-source-v1","value":NaN}',
        b"[" * 10_000 + b"]" * 10_000,
    ),
    ids=("duplicate", "nonfinite", "deep"),
)
def test_offline_capture_maps_strict_metadata_json_errors(tmp_path: Path, raw: bytes) -> None:
    metadata = tmp_path / "source-capture.json"
    metadata.write_bytes(raw)

    with pytest.raises(SourceCaptureError, match="source capture metadata is not valid JSON"):
        load_offline_capture(
            metadata,
            archive=tmp_path / "archive",
            side_data_dir=tmp_path,
            prior_manifest=tmp_path / "prior-manifest",
            prior_seal_manifest=tmp_path / "prior-seal",
        )


@pytest.mark.parametrize("target", ("manifest", "seal"))
@pytest.mark.parametrize(
    "raw",
    (
        b'{"format":"first","format":"shadow"}',
        b'{"value":Infinity}',
        b"[" * 10_000 + b"]" * 10_000,
    ),
    ids=("duplicate", "nonfinite", "deep"),
)
def test_offline_capture_maps_strict_prior_json_errors(
    tmp_path: Path, target: str, raw: bytes
) -> None:
    archive = tmp_path / "gene_NBK1116.tar.gz"
    member = tmp_path / "NBK1.nxml"
    member.write_text("<article>retained</article>")
    with tarfile.open(archive, "w:gz") as retained:
        retained.add(member, arcname="NBK1/NBK1.nxml")
    side_dir = tmp_path / "side"
    side_dir.mkdir()
    _write_side_data(side_dir)
    capture = _capture_metadata(archive, side_dir)
    prior_manifest, prior_seal = _write_prior_manifest(tmp_path, capture)
    prior = capture["prior_artifact"]
    assert isinstance(prior, dict)
    if target == "manifest":
        prior_manifest.write_bytes(raw)
        prior["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
        expected = "prior manifest is not valid JSON"
    else:
        prior_seal.write_bytes(raw)
        prior["object_id"] = hashlib.sha256(raw).hexdigest()
        expected = "prior seal manifest is not valid JSON"
    metadata = tmp_path / "source-capture.json"
    metadata.write_text(json.dumps(capture))

    with pytest.raises(SourceCaptureError, match=expected):
        load_offline_capture(
            metadata,
            archive=archive,
            side_data_dir=side_dir,
            prior_manifest=prior_manifest,
            prior_seal_manifest=prior_seal,
        )


def test_offline_capture_binds_exact_retained_files_and_is_not_deleted(tmp_path: Path) -> None:
    archive = tmp_path / "gene_NBK1116.tar.gz"
    member = tmp_path / "NBK1.nxml"
    member.write_text("<article>retained</article>")
    with tarfile.open(archive, "w:gz") as retained:
        retained.add(member, arcname="NBK1/NBK1.nxml")
    side_dir = tmp_path / "side"
    side_dir.mkdir()
    _write_side_data(side_dir)
    metadata = tmp_path / "source-capture.json"
    capture_metadata = _capture_metadata(archive, side_dir)
    prior_manifest, prior_seal = _write_prior_manifest(tmp_path, capture_metadata)
    metadata.write_text(json.dumps(capture_metadata))

    capture = load_offline_capture(
        metadata,
        archive=archive,
        side_data_dir=side_dir,
        prior_manifest=prior_manifest,
        prior_seal_manifest=prior_seal,
    )

    assert capture["chapter_ids"] == ["NBK1", "NBK2"]
    assert archive.is_file()
    assert all(path.is_file() for path in side_dir.iterdir())
    archive.write_bytes(b"tampered archive")
    with pytest.raises(SourceCaptureError, match=r"archive (size|digest)"):
        load_offline_capture(
            metadata,
            archive=archive,
            side_data_dir=side_dir,
            prior_manifest=prior_manifest,
            prior_seal_manifest=prior_seal,
        )


def test_offline_capture_rejects_fabricated_prior_tuple(tmp_path: Path) -> None:
    archive = tmp_path / "gene_NBK1116.tar.gz"
    member = tmp_path / "NBK1.nxml"
    member.write_text("<article>retained</article>")
    with tarfile.open(archive, "w:gz") as retained:
        retained.add(member, arcname="NBK1/NBK1.nxml")
    side_dir = tmp_path / "side"
    side_dir.mkdir()
    _write_side_data(side_dir)
    capture = _capture_metadata(archive, side_dir)
    prior_manifest, prior_seal = _write_prior_manifest(tmp_path, capture)
    prior = capture["prior_artifact"]
    assert isinstance(prior, dict)
    prior["chapters_sha256"] = "9" * 64
    metadata = tmp_path / "source-capture.json"
    metadata.write_text(json.dumps(capture))

    with pytest.raises(SourceCaptureError, match="prior manifest"):
        load_offline_capture(
            metadata,
            archive=archive,
            side_data_dir=side_dir,
            prior_manifest=prior_manifest,
            prior_seal_manifest=prior_seal,
        )

    prior["chapters_sha256"] = "f" * 64
    metadata.write_text(json.dumps(capture))
    unsafe_prior = tmp_path / "unsafe-prior-manifest.json"
    unsafe_prior.symlink_to(prior_manifest)
    with pytest.raises(SourceCaptureError, match="unsafe"):
        load_offline_capture(
            metadata,
            archive=archive,
            side_data_dir=side_dir,
            prior_manifest=unsafe_prior,
            prior_seal_manifest=prior_seal,
        )

    prior["object_id"] = "0" * 64
    metadata.write_text(json.dumps(capture))
    with pytest.raises(SourceCaptureError, match="object ID"):
        load_offline_capture(
            metadata,
            archive=archive,
            side_data_dir=side_dir,
            prior_manifest=prior_manifest,
            prior_seal_manifest=prior_seal,
        )


def test_offline_capture_rejects_chapter_ids_not_derived_from_retained_mapping(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "gene_NBK1116.tar.gz"
    member = tmp_path / "NBK1.nxml"
    member.write_text("<article>retained</article>")
    with tarfile.open(archive, "w:gz") as retained:
        retained.add(member, arcname="NBK1/NBK1.nxml")
    side_dir = tmp_path / "side"
    side_dir.mkdir()
    _write_side_data(side_dir)
    capture = _capture_metadata(archive, side_dir)
    prior_manifest, prior_seal = _write_prior_manifest(tmp_path, capture)
    capture["chapter_ids"] = ["NBK1", "NBK3"]
    metadata = tmp_path / "source-capture.json"
    metadata.write_text(json.dumps(capture))

    with pytest.raises(SourceCaptureError, match="authoritative side mapping"):
        load_offline_capture(
            metadata,
            archive=archive,
            side_data_dir=side_dir,
            prior_manifest=prior_manifest,
            prior_seal_manifest=prior_seal,
        )


def test_archive_identity_accepts_safe_directory_members(tmp_path: Path) -> None:
    archive = tmp_path / "gene_NBK1116.tar.gz"
    member = tmp_path / "NBK1.nxml"
    member.write_text("<article>retained</article>")
    with tarfile.open(archive, "w:gz") as retained:
        directory = tarfile.TarInfo("NBK1")
        directory.type = tarfile.DIRTYPE
        retained.addfile(directory)
        retained.add(member, arcname="NBK1/NBK1.nxml")

    members_sha256, expanded_sha256 = archive_content_identities(archive)

    assert len(members_sha256) == 64
    assert len(expanded_sha256) == 64


def test_offline_capture_rejects_noncanonical_upstream_urls(tmp_path: Path) -> None:
    archive = tmp_path / "gene_NBK1116.tar.gz"
    member = tmp_path / "NBK1.nxml"
    member.write_text("<article>retained</article>")
    with tarfile.open(archive, "w:gz") as retained:
        retained.add(member, arcname="NBK1/NBK1.nxml")
    side_dir = tmp_path / "side"
    side_dir.mkdir()
    _write_side_data(side_dir)
    capture = _capture_metadata(archive, side_dir)
    prior_manifest, prior_seal = _write_prior_manifest(tmp_path, capture)
    capture["listing"]["url"] = "https://attacker.example/file_list.csv"  # type: ignore[index]
    metadata = tmp_path / "source-capture.json"
    metadata.write_text(json.dumps(capture))

    with pytest.raises(SourceCaptureError, match="canonical"):
        load_offline_capture(
            metadata,
            archive=archive,
            side_data_dir=side_dir,
            prior_manifest=prior_manifest,
            prior_seal_manifest=prior_seal,
        )


def test_offline_capture_derives_listing_fields_from_exact_retained_csv(tmp_path: Path) -> None:
    archive = tmp_path / "gene_NBK1116.tar.gz"
    member = tmp_path / "NBK1.nxml"
    member.write_text("<article>retained</article>")
    with tarfile.open(archive, "w:gz") as retained:
        retained.add(member, arcname="NBK1/NBK1.nxml")
    side_dir = tmp_path / "side"
    side_dir.mkdir()
    _write_side_data(side_dir)
    capture = _capture_metadata(archive, side_dir)
    prior_manifest, prior_seal = _write_prior_manifest(tmp_path, capture)
    listing = capture["listing"]
    assert isinstance(listing, dict)
    listing["last_updated"] = "2026-09-01 02:41:04"
    metadata = tmp_path / "source-capture.json"
    metadata.write_text(json.dumps(capture))

    with pytest.raises(SourceCaptureError, match=r"derived from retained file_list\.csv"):
        load_offline_capture(
            metadata,
            archive=archive,
            side_data_dir=side_dir,
            prior_manifest=prior_manifest,
            prior_seal_manifest=prior_seal,
        )


@pytest.mark.parametrize(
    "metrics",
    [
        {"mrr_at_10": 0.0, "section_precision_at_5": 0.0, "queries_run": 5},
        {"mrr_at_10": 0.2618, "section_precision_at_5": 0.4, "queries_run": 5},
        {"mrr_at_10": 0.3, "section_precision_at_5": 0.39, "queries_run": 5},
        {"mrr_at_10": 0.3, "section_precision_at_5": 0.4, "queries_run": 4},
    ],
)
def test_evaluation_fails_closed_below_reviewed_acceptance(metrics: dict[str, float]) -> None:
    with pytest.raises(EvaluationRejectedError):
        assert_evaluation_accepted(metrics, expected_queries=5, covered_queries=5)


def test_evaluation_accepts_established_floor_with_full_coverage() -> None:
    assert_evaluation_accepted(
        {"mrr_at_10": 0.2619, "section_precision_at_5": 0.4, "queries_run": 5},
        expected_queries=5,
        covered_queries=5,
    )


def test_reviewed_evaluation_suite_is_packaged_with_exact_digest() -> None:
    assert EVALUATION_SUITE.is_file()
    assert hashlib.sha256(EVALUATION_SUITE.read_bytes()).hexdigest() == EVALUATION_SUITE_SHA256
    assert "genereview_link" in EVALUATION_SUITE.parts


@pytest.mark.asyncio
async def test_evaluation_refuses_to_run_a_tampered_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tampered = tmp_path / "queries.jsonl"
    tampered.write_text(
        '{"query":"attacker","expected_chapter":"NBK1","expected_section":"summary"}\n'
    )
    monkeypatch.setattr(evaluation, "EVALUATION_SUITE", tampered)

    with pytest.raises(EvaluationRejectedError, match="suite bytes"):
        await evaluation.evaluate_connection(object())


def test_restore_endpoint_must_be_same_database_and_exact_restricted_role() -> None:
    owner = "postgresql://owner:secret@db.internal:5432/genereview"
    valid = "postgresql://genereview_restore:restore@db.internal:5432/genereview"

    validate_restore_endpoint(owner, valid, role="genereview_restore")
    with pytest.raises(ArchivePolicyError, match="username"):
        validate_restore_endpoint(
            owner, valid.replace("genereview_restore", "owner"), role="genereview_restore"
        )
    with pytest.raises(ArchivePolicyError, match="same database endpoint"):
        validate_restore_endpoint(
            owner, valid.replace("db.internal", "other"), role="genereview_restore"
        )
    with pytest.raises(ArchivePolicyError, match="plain PostgreSQL URL"):
        validate_restore_endpoint(
            owner,
            valid + "?host=attacker.internal",
            role="genereview_restore",
        )
    with pytest.raises(ArchivePolicyError, match="plain PostgreSQL URL"):
        validate_restore_endpoint(
            owner,
            valid.replace("postgresql://", "https://"),
            role="genereview_restore",
        )


def test_release_selection_treats_every_remote_release_or_tag_as_occupied() -> None:
    slots = [
        ReleaseSlot(1, release="different", tag=True, immutable=True),
        ReleaseSlot(3, release="exact", tag=True, immutable=True),
        ReleaseSlot(4, release="different", tag=False, immutable=False),
        ReleaseSlot(6, release=None, tag=True, immutable=False),
    ]

    assert select_release_id("2026-08-31", slots) == ("2026-08-31-r2", False)
    without_exact = [slot for slot in slots if slot.release != "exact"]
    assert select_release_id("2026-08-31", without_exact) == ("2026-08-31-r2", False)


def test_dispatch_acceptance_rejects_stale_or_wrong_run() -> None:
    expected = {
        "release_id": 17,
        "target_commit": "a" * 40,
        "nonce": "b" * 64,
        "dispatch_time": "2026-09-01T12:00:00Z",
    }
    accepted = {
        **expected,
        "run_id": 99,
        "run_started_at": "2026-09-01T12:00:01Z",
        "head_sha": "a" * 40,
        "source_ref": "refs/tags/corpus-data-2026-08-31-r1",
        "status": "passed",
    }
    verify_acceptance(accepted, expected=expected)
    stale = {**accepted, "run_started_at": "2026-09-01T11:59:59Z"}
    with pytest.raises(DispatchIdentityError, match="predates"):
        verify_acceptance(stale, expected=expected)
    wrong = {**accepted, "head_sha": "c" * 40}
    with pytest.raises(DispatchIdentityError, match="head SHA"):
        verify_acceptance(wrong, expected=expected)


def test_model_identity_binds_model_tokenizer_and_config_files() -> None:
    assert "model.safetensors" in BGE_MODEL_FILES
    assert "tokenizer.json" in BGE_MODEL_FILES
    assert "tokenizer_config.json" in BGE_MODEL_FILES
    assert "config.json" in BGE_MODEL_FILES


def test_content_identity_is_logical_and_reports_all_prior_chapter_deltas() -> None:
    capture = {
        "chapter_ids": ["NBK1", "NBK2"],
        "archive": {"members_sha256": "a" * 64, "expanded_sha256": "b" * 64},
        "prior_artifact": {
            "object_id": "c" * 64,
            "chapter_ids": ["NBK1", "NBK3"],
            "chapter_count": 2,
            "chapter_digests": {"NBK1": "d" * 64, "NBK3": "e" * 64},
            "chapters_sha256": "f" * 64,
            "passages_sha256": "0" * 64,
        },
    }
    chapters = [
        {"nbk_id": "NBK2", "title": "two"},
        {"nbk_id": "NBK1", "title": "one"},
    ]
    passages = [
        {"nbk_id": "NBK2", "passage_id": "p2", "text": "beta"},
        {"nbk_id": "NBK1", "passage_id": "p1", "text": "alpha"},
    ]

    identity = compute_content_identity(
        chapters=chapters,
        passages=passages,
        side_mapping_ids={"NBK1", "NBK2"},
        source_capture=capture,
    )

    assert identity["chapter_ids"] == ["NBK1", "NBK2"]
    assert identity["delta_from_prior"]["added"] == ["NBK2"]
    assert identity["delta_from_prior"]["removed"] == ["NBK3"]
    assert identity["delta_from_prior"]["changed"] == ["NBK1"]
    assert identity["source_archive"] == {
        "members_sha256": "a" * 64,
        "expanded_sha256": "b" * 64,
    }
