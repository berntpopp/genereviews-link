"""Local sealed corpus handoff tests; no release service is involved."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import genereview_link.corpus.handoff as handoff
from genereview_link.corpus.bundle import (
    BundleManifest,
    _reviewed_migration_digests,
    write_data_only_bundle,
)
from genereview_link.corpus.computation_runs import computation_run_id
from genereview_link.corpus.evaluation_contract import EVALUATION_SUITE_SHA256
from genereview_link.corpus.handoff import (
    HandoffError,
    prepare_publish_handoff,
    seal_handoff,
    verify_data_only_bundle,
    verify_handoff,
    verify_rights_record,
)
from genereview_link.corpus.pg_client import PG18_IMAGE
from genereview_link.retrieval.model_identity import BGE_MODEL_FILES

RIGHTS_ATTRIBUTION = (
    "GeneReviews® content ©1993-2026 University of Washington, Seattle; "
    "source https://www.genereviews.org; noncommercial research purposes only; "
    "comply with the copyright notice and Usage Disclaimer; no further modifications."
)
RIGHTS_AUTHORITY = "Bernt Popp / repository owner"
RIGHTS_USE = (
    "immutable GeneReviews research corpus artifact for noncommercial research purposes only; "
    "no further modifications"
)


@pytest.fixture(autouse=True)
def _valid_postgres_archive_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most handoff tests isolate sealing; archive-policy tests own pg_restore coverage."""
    monkeypatch.setattr(handoff, "_assert_local_archive", lambda _path, *, parent_fd: None)


def _source(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    (work / "corpus.dump").write_bytes(b"PGDMP-data")
    return write_data_only_bundle(
        work_dir=work,
        output=tmp_path / "source",
        manifest=_verified_manifest(),
    )


def _write_rights(path: Path, record: dict[str, object], tmp_path: Path) -> None:
    del tmp_path
    record.setdefault("approval_kind", "repository-owner redistribution determination")
    record.setdefault("upstream_approval", False)
    record.setdefault(
        "authorization_uri", "https://github.com/berntpopp/genereviews-link/issues/27"
    )
    record.setdefault("terms_source_uri", "https://www.genereviews.org/")
    evidence = path.parent / "rights-evidence.json"
    terms = path.parent / "terms-snapshot.html"
    terms.write_text(
        "<html>GeneReviews® ©1993-2026 University of Washington, Seattle; "
        "https://www.genereviews.org; noncommercial research purposes only; copyright notice; "
        "Usage Disclaimer; no further modifications.</html>\n"
    )
    record["terms_uri"] = "bundle:terms-snapshot.html"
    record["terms_sha256"] = hashlib.sha256(terms.read_bytes()).hexdigest()
    record["evidence_uri"] = "bundle:rights-evidence.json"
    evidence_fields = (
        "approval_kind",
        "upstream_approval",
        "rights_authority",
        "responsible_reviewer",
        "authorization_uri",
        "decision_time",
        "terms_version",
        "terms_sha256",
        "terms_source_uri",
        "permitted_asset_use",
        "attribution",
        "object_id",
        "source_sha256",
        "artifact_sha256",
        "corpus_release_id",
    )
    evidence.write_text(
        json.dumps(
            {
                "format": "genereviews-owner-rights-evidence-v1",
                **{name: record[name] for name in evidence_fields},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    record["evidence_sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
    unsigned = dict(record)
    record["rights_record_sha256"] = hashlib.sha256(
        (json.dumps(unsigned, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    path.write_text(json.dumps(record))


def _rewrite_rights_digest(record: dict[str, object]) -> None:
    unsigned = {key: value for key, value in record.items() if key != "rights_record_sha256"}
    record["rights_record_sha256"] = hashlib.sha256(
        (json.dumps(unsigned, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()


def _verified_manifest(release_id: str = "2026-08-30-r1") -> BundleManifest:
    per_query = [
        {
            "query_sha256": hashlib.sha256(f"query-{index}".encode()).hexdigest(),
            "expected_chapter": f"NBK{index}",
            "expected_section": "Diagnosis",
            "expected_rank": 1,
            "section_hit_at_5": index < 2,
            "results_returned": 10,
        }
        for index in range(5)
    ]
    evaluation_results = {
        "mrr_at_10": 0.2619,
        "section_precision_at_5": 0.4,
        "queries_run": 5,
        "covered_queries": 5,
        "per_query": per_query,
    }
    source_capture = {
        "format": "genereviews-offline-source-v1",
        "listing": {
            "url": "https://ftp.ncbi.nlm.nih.gov/pub/litarch/file_list.csv",
            "raw_sha256": "1" * 64,
            "raw_size_bytes": 4096,
            "captured_at": "2026-08-30T03:00:00Z",
            "integrity_class": "https-captured-untrusted",
            "relpath": "ca/84/gene_NBK1116.tar.gz",
            "last_updated": "2026-08-30 02:41:04",
        },
        "archive": {
            "url": "https://ftp.ncbi.nlm.nih.gov/pub/litarch/ca/84/gene_NBK1116.tar.gz",
            "sha256": "a" * 64,
            "size_bytes": 123,
            "members_sha256": "2" * 64,
            "expanded_sha256": "3" * 64,
        },
        "side_data": {
            "GRtitle_shortname_NBKid.txt": {
                "url": "https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews/GRtitle_shortname_NBKid.txt",
                "sha256": "b" * 64,
                "size_bytes": 10,
            },
            "NBKid_shortname_genesymbol.txt": {
                "url": "https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews/NBKid_shortname_genesymbol.txt",
                "sha256": "c" * 64,
                "size_bytes": 11,
            },
            "NBKid_shortname_OMIM.txt": {
                "url": "https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews/NBKid_shortname_OMIM.txt",
                "sha256": "d" * 64,
                "size_bytes": 12,
            },
        },
        "chapter_ids": [],
        "prior_artifact": {
            "object_id": "4" * 64,
            "chapter_ids": [],
            "chapter_count": 0,
            "chapter_digests": {},
            "chapters_sha256": "5" * 64,
            "passages_sha256": "6" * 64,
        },
    }
    content_identity = {
        "chapter_ids": [],
        "chapter_ids_sha256": "7" * 64,
        "side_mapping_ids_sha256": "7" * 64,
        "chapters_sha256": "8" * 64,
        "passages_sha256": "9" * 64,
        "chapter_digests": {},
        "source_archive": {
            "members_sha256": "2" * 64,
            "expanded_sha256": "3" * 64,
        },
        "delta_from_prior": {
            "object_id": "4" * 64,
            "prior_chapter_count": 0,
            "added": [],
            "removed": [],
            "changed": [],
            "chapters_sha256": {"prior": "5" * 64, "current": "8" * 64},
            "passages_sha256": {"prior": "6" * 64, "current": "9" * 64},
        },
    }
    installed = ["asyncpg==0.31.0", "genereview-link==5.1.5"]
    provenance = {
        "schema": "genereviews-computation-v2",
        "source": {"app_git_sha": "b" * 40, "builder_identity": "local:test"},
        "uv_lock_sha256": "f" * 64,
        "environment": {
            "installed_distributions": installed,
            "installed_distributions_sha256": hashlib.sha256(
                (json.dumps(installed, sort_keys=True, separators=(",", ":")) + "\n").encode()
            ).hexdigest(),
            "uv_version": "uv 0.8.11",
            "python": "3.12.9",
            "os": "Linux-test",
            "kernel": "6.8.0",
            "libc": "glibc 2.39",
            "cpu": "test cpu",
            "blas": "OpenBLAS",
            "device": "cpu",
            "gpu": "none",
            "cuda": "none",
            "cudnn": "none",
            "torch": "2.8.0+cpu",
            "sentence_transformers": "5.1.0",
            "transformers": "4.55.2",
            "build_backend": "hatchling==1.27.0",
        },
        "database": {
            "client_image": PG18_IMAGE,
            "client_major": "18",
            "server_version_num": "180004",
            "server_major": "18",
            "pgvector": "0.8.2",
        },
        "model": {
            "name": "BAAI/bge-small-en-v1.5",
            "revision": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
            "files": dict(BGE_MODEL_FILES),
        },
        "determinism": {
            "normalize_embeddings": True,
            "python_seed": 0,
            "numpy_seed": 0,
            "torch_seed": 0,
            "batch_size": 64,
        },
        "embedding": {
            "model_name": "BAAI/bge-small-en-v1.5",
            "model_revision": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
            "table": "genereview_embeddings_bge384",
        },
    }
    ingest_provenance = {**provenance, "source_capture": source_capture}
    embedding_run_id = computation_run_id(
        phase="embedding",
        corpus_version="2026-08-30-r3",
        expected_row_count=0,
        provenance=provenance,
    )
    ingest_run_id = computation_run_id(
        phase="ingest",
        corpus_version="2026-08-30-r3",
        expected_row_count=890,
        provenance=ingest_provenance,
    )
    manifest = BundleManifest(
        corpus_release_id=release_id,
        corpus_version="2026-08-30-r3",
        tarball_source_sha256="a" * 64,
        tarball_last_updated="2026-08-30 02:41:04",
        chapter_count=890,
        passage_count=0,
        embedding={
            "model_name": "BAAI/bge-small-en-v1.5",
            "dimension": 384,
            "distance_metric": "cosine",
            "active_table": "genereview_embeddings_bge384",
            "count": 0,
            "expected_count": 0,
        },
        postgres={"major_version": "18", "pgvector_version": "0.8.2"},
        schema_migrations={
            key: list(value) for key, value in _reviewed_migration_digests().items()
        },
        app_git_sha="b" * 40,
        app_version="5.1.5",
        genereview_link_version="5.1.5",
        hnsw={
            "index_name": "genereview_embeddings_bge384_hnsw_cosine",
            "exists": True,
        },
        source={
            "listing_relpath": "ca/84/gene_NBK1116.tar.gz",
            "last_updated": "2026-08-30 02:41:04",
            "tarball": {"sha256": "a" * 64, "size_bytes": 123},
            "side_data": {
                "GRtitle_shortname_NBKid.txt": {"sha256": "b" * 64, "size_bytes": 10},
                "NBKid_shortname_genesymbol.txt": {"sha256": "c" * 64, "size_bytes": 11},
                "NBKid_shortname_OMIM.txt": {"sha256": "d" * 64, "size_bytes": 12},
            },
        },
        source_capture=source_capture,
        content_identity=content_identity,
        validation={"status": "passed", "smoke_queries": []},
        evaluation={
            "status": "passed",
            "suite": "tests/eval/genereviews_queries.jsonl",
            "suite_sha256": EVALUATION_SUITE_SHA256,
            "model_name": "BAAI/bge-small-en-v1.5",
            "corpus_identity": {
                "corpus_version": "2026-08-30-r3",
                "source": {
                    "listing_relpath": "ca/84/gene_NBK1116.tar.gz",
                    "last_updated": "2026-08-30 02:41:04",
                    "tarball": {"sha256": "a" * 64, "size_bytes": 123},
                    "side_data": {
                        "GRtitle_shortname_NBKid.txt": {
                            "sha256": "b" * 64,
                            "size_bytes": 10,
                        },
                        "NBKid_shortname_genesymbol.txt": {
                            "sha256": "c" * 64,
                            "size_bytes": 11,
                        },
                        "NBKid_shortname_OMIM.txt": {
                            "sha256": "d" * 64,
                            "size_bytes": 12,
                        },
                    },
                },
                "chapter_count": 890,
                "passage_count": 0,
                "embedding_count": 0,
                "embedding_run_id": embedding_run_id,
                "content_identity": content_identity,
            },
            "export_snapshot": "00000003-0000001B-1",
            "dump_sha256": hashlib.sha256(b"PGDMP-data").hexdigest(),
            "results": evaluation_results,
            "result_sha256": hashlib.sha256(
                (
                    json.dumps(evaluation_results, sort_keys=True, separators=(",", ":")) + "\n"
                ).encode()
            ).hexdigest(),
        },
        computation={
            "run_id": embedding_run_id,
            "app_git_sha": "b" * 40,
            "expected_row_count": 0,
            "provenance": provenance,
            "ingest_run": {
                "run_id": ingest_run_id,
                "app_git_sha": "b" * 40,
                "expected_row_count": 890,
                "provenance": ingest_provenance,
            },
        },
    )
    return manifest


def _publisher_tool(tmp_path: Path) -> Path:
    tool = tmp_path / "publisher-tool"
    tool.mkdir(exist_ok=True)
    (tool / "genereviews_link-5.1.4-py3-none-any.whl").write_bytes(b"sealed wheel")
    return tool


def test_bundle_rejects_computation_run_id_not_derived_from_immutable_record(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "corpus.dump").write_bytes(b"PGDMP-data")
    manifest = _verified_manifest()
    manifest.computation["run_id"] = "e" * 64
    corpus_identity = manifest.evaluation["corpus_identity"]
    assert isinstance(corpus_identity, dict)
    corpus_identity["embedding_run_id"] = "e" * 64
    source = write_data_only_bundle(
        work_dir=work,
        output=tmp_path / "source",
        manifest=manifest,
    )

    with pytest.raises(HandoffError, match="computation run ID"):
        verify_data_only_bundle(source)


def test_bundle_rejects_recomputed_ingest_run_with_unreviewed_pg18_image(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "corpus.dump").write_bytes(b"PGDMP-data")
    manifest = _verified_manifest()
    ingest = manifest.computation["ingest_run"]
    assert isinstance(ingest, dict)
    ingest_provenance = json.loads(json.dumps(ingest["provenance"]))
    database = ingest_provenance["database"]
    assert isinstance(database, dict)
    database["client_image"] = PG18_IMAGE + "-attacker"
    ingest["provenance"] = ingest_provenance
    ingest["run_id"] = computation_run_id(
        phase="ingest",
        corpus_version=manifest.corpus_version,
        expected_row_count=int(ingest["expected_row_count"]),
        provenance=ingest_provenance,
    )
    source = write_data_only_bundle(
        work_dir=work,
        output=tmp_path / "source",
        manifest=manifest,
    )

    with pytest.raises(HandoffError, match="runtime computation provenance"):
        verify_data_only_bundle(source)


def test_seal_is_content_addressed_read_only_and_reverifiable(tmp_path: Path) -> None:
    tool = tmp_path / "publisher-tool"
    tool.mkdir()
    (tool / "genereviews_link-5.1.4-py3-none-any.whl").write_bytes(b"sealed wheel")
    sealed = seal_handoff(_source(tmp_path), tmp_path / "handoffs", publisher_tool=tool)

    assert sealed.object_id == hashlib.sha256(sealed.manifest.read_bytes()).hexdigest()
    assert verify_handoff(tmp_path / "handoffs", sealed.object_id).object_id == sealed.object_id
    assert all(not path.is_symlink() for path in sealed.path.rglob("*"))


def test_seal_binds_exactly_one_publisher_wheel_into_object_identity(tmp_path: Path) -> None:
    tool = tmp_path / "publisher-tool"
    tool.mkdir()
    wheel = tool / "genereviews_link-5.1.4-py3-none-any.whl"
    wheel.write_bytes(b"sealed wheel")

    sealed = seal_handoff(_source(tmp_path), tmp_path / "handoffs", publisher_tool=tool)

    assert (sealed.path / wheel.name).read_bytes() == b"sealed wheel"
    manifest = json.loads(sealed.manifest.read_text())
    entries = {entry["name"]: entry for entry in manifest["files"]}
    assert entries[wheel.name]["sha256"] == hashlib.sha256(b"sealed wheel").hexdigest()
    assert entries[wheel.name]["size"] == len(b"sealed wheel")
    assert all(entry["mode"] == 0o400 for entry in entries.values())
    assert verify_handoff(tmp_path / "handoffs", sealed.object_id).object_id == sealed.object_id


def test_seal_rejects_a_source_file_changed_during_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    original_copy = handoff._copy_regular
    changed = False

    def change_then_copy(
        source_path: Path,
        destination: Path,
        *,
        source_parent_fd: int | None = None,
        target_parent_fd: int | None = None,
    ) -> None:
        nonlocal changed
        if source_path == source / "corpus.dump" and not changed:
            changed = True
            source_path.write_bytes(b"PGDMP-attacker")
        original_copy(
            source_path,
            destination,
            source_parent_fd=source_parent_fd,
            target_parent_fd=target_parent_fd,
        )

    monkeypatch.setattr(handoff, "_copy_regular", change_then_copy)

    with pytest.raises(HandoffError, match="changed while sealing"):
        seal_handoff(source, tmp_path / "handoffs", publisher_tool=_publisher_tool(tmp_path))


def test_verify_rejects_publisher_wheel_tampering(tmp_path: Path) -> None:
    sealed = seal_handoff(
        _source(tmp_path), tmp_path / "handoffs", publisher_tool=_publisher_tool(tmp_path)
    )
    wheel_name = json.loads(sealed.manifest.read_text())["publisher_tool"]["name"]
    wheel = sealed.path / wheel_name
    wheel.chmod(0o600)
    wheel.write_bytes(b"tampered wheel")
    wheel.chmod(0o400)

    with pytest.raises(HandoffError, match="publisher wheel"):
        verify_handoff(tmp_path / "handoffs", sealed.object_id)


def test_seal_rejects_zero_or_multiple_publisher_wheels(tmp_path: Path) -> None:
    tool = tmp_path / "publisher-tool"
    tool.mkdir()
    with pytest.raises(HandoffError, match="exactly one publisher wheel"):
        seal_handoff(_source(tmp_path), tmp_path / "handoffs", publisher_tool=tool)
    (tool / "first.whl").write_bytes(b"first")
    (tool / "second.whl").write_bytes(b"second")
    with pytest.raises(HandoffError, match="exactly one publisher wheel"):
        seal_handoff(tmp_path / "source", tmp_path / "handoffs-2", publisher_tool=tool)


def test_seal_rejects_symlink_and_existing_object_substitution(tmp_path: Path) -> None:
    source = _source(tmp_path)
    (source / "unsafe").symlink_to("corpus.dump")
    with pytest.raises(HandoffError, match="regular"):
        tool = tmp_path / "publisher-tool"
        tool.mkdir()
        (tool / "tool.whl").write_bytes(b"wheel")
        seal_handoff(source, tmp_path / "handoffs", publisher_tool=tool)


def test_rights_record_binds_affirmative_decision_to_exact_object(tmp_path: Path) -> None:
    sealed = seal_handoff(
        _source(tmp_path), tmp_path / "handoffs", publisher_tool=_publisher_tool(tmp_path)
    )
    record = {
        "object_id": sealed.object_id,
        "decision": "affirmative",
        "responsible_reviewer": "reviewer@example.org",
        "rights_authority": RIGHTS_AUTHORITY,
        "decision_time": "2026-09-01T00:00:00Z",
        "terms_version": "2026-09-01",
        "permitted_asset_use": RIGHTS_USE,
        "attribution": RIGHTS_ATTRIBUTION,
        "evidence_uri": "https://example.org/rights-record",
        "source_sha256": "a" * 64,
        "artifact_sha256": hashlib.sha256(
            sealed.path.joinpath("corpus.dump").read_bytes()
        ).hexdigest(),
        "corpus_release_id": "2026-08-30-r1",
    }
    rights = tmp_path / "rights.json"
    _write_rights(rights, record, tmp_path)
    assert verify_rights_record(rights, sealed.object_id)["decision"] == "affirmative"
    record["attribution"] = "GeneReviews"
    _write_rights(rights, record, tmp_path)
    with pytest.raises(HandoffError, match="attribution"):
        verify_rights_record(rights, sealed.object_id)
    record["attribution"] = RIGHTS_ATTRIBUTION
    record["decision"] = "pending"
    _write_rights(rights, record, tmp_path)
    with pytest.raises(HandoffError, match="affirmative"):
        verify_rights_record(rights, sealed.object_id)


def test_handoff_root_must_be_owner_only(tmp_path: Path) -> None:
    root = tmp_path / "handoffs"
    root.mkdir(mode=0o755)
    root.chmod(0o755)

    with pytest.raises(HandoffError, match="owner-only"):
        seal_handoff(_source(tmp_path), root, publisher_tool=_publisher_tool(tmp_path))


def test_handoff_root_must_be_owned_by_the_invoking_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "handoffs"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(handoff.os, "geteuid", lambda: root.stat().st_uid + 1)

    with pytest.raises(HandoffError, match="owner-only"):
        seal_handoff(_source(tmp_path), root, publisher_tool=_publisher_tool(tmp_path))


def test_capped_read_rejects_a_file_replaced_by_a_symlink_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim = tmp_path / "manifest.json"
    replacement = tmp_path / "replacement.json"
    victim.write_text('{"safe":true}')
    replacement.write_text('{"attacker":true}')
    original_open = handoff.os.open

    def swap_then_open(path: Path, flags: int, *args: object, **kwargs: object) -> int:
        if (
            Path(path) == Path(victim.name)
            and kwargs.get("dir_fd") is not None
            and not victim.is_symlink()
        ):
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
        if (
            Path(path) == Path(victim.name)
            and kwargs.get("dir_fd") is not None
            and not victim.is_symlink()
        ):
            victim.unlink()
            victim.symlink_to(replacement)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(handoff.os, "open", swap_then_open)
    with pytest.raises(HandoffError, match="unsafe"):
        handoff._sha256(victim)


def test_digest_rejects_parent_directory_swap_before_openat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "parent"
    replacement = tmp_path / "replacement-parent"
    parent.mkdir()
    replacement.mkdir()
    (parent / "corpus.dump").write_bytes(b"safe")
    (replacement / "corpus.dump").write_bytes(b"attacker")
    original_open = handoff.os.open
    swapped = False

    def swap_parent(path: Path, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if Path(path) == Path("parent") and not swapped:
            swapped = True
            parent.rename(tmp_path / "original-parent")
            parent.symlink_to(replacement, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(handoff.os, "open", swap_parent)

    with pytest.raises(HandoffError, match="unsafe"):
        handoff._sha256(parent / "corpus.dump")


def test_digest_consumes_open_parent_descriptor_during_late_parent_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "parent"
    replacement = tmp_path / "replacement-parent"
    parent.mkdir()
    replacement.mkdir()
    (parent / "corpus.dump").write_bytes(b"safe")
    (replacement / "corpus.dump").write_bytes(b"attacker")
    original_open = handoff.os.open
    swapped = False

    def swap_after_parent_open(path: Path, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if Path(path) == Path("corpus.dump") and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            parent.rename(tmp_path / "original-parent")
            parent.symlink_to(replacement, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(handoff.os, "open", swap_after_parent_open)

    digest, size = handoff._sha256(parent / "corpus.dump")

    assert digest == hashlib.sha256(b"safe").hexdigest()
    assert size == len(b"safe")


def test_handoff_rejects_a_file_mode_changed_before_the_digest_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sealed = seal_handoff(
        _source(tmp_path), tmp_path / "handoffs", publisher_tool=_publisher_tool(tmp_path)
    )
    victim = sealed.path / "corpus.dump"
    original_open = handoff.os.open

    def relax_then_open(path: Path, flags: int, *args: object, **kwargs: object) -> int:
        if Path(path) == Path(victim.name) and kwargs.get("dir_fd") is not None:
            victim.chmod(0o644)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(handoff.os, "open", relax_then_open)
    with pytest.raises(HandoffError, match="mode 0400"):
        verify_handoff(tmp_path / "handoffs", sealed.object_id)


def test_rights_record_must_bind_source_and_artifact_identity(tmp_path: Path) -> None:
    sealed = seal_handoff(
        _source(tmp_path), tmp_path / "handoffs", publisher_tool=_publisher_tool(tmp_path)
    )
    incomplete = {
        "object_id": sealed.object_id,
        "decision": "affirmative",
        "responsible_reviewer": "reviewer@example.org",
        "rights_authority": RIGHTS_AUTHORITY,
        "decision_time": "2026-09-01T00:00:00Z",
        "terms_version": "2026-09-01",
        "permitted_asset_use": RIGHTS_USE,
        "attribution": RIGHTS_ATTRIBUTION,
        "evidence_uri": "https://example.org/rights-record",
    }
    rights = tmp_path / "rights.json"
    rights.write_text(json.dumps(incomplete))

    with pytest.raises(HandoffError, match="complete required"):
        verify_rights_record(rights, sealed.object_id)


def test_publisher_rejects_a_rights_record_for_a_different_source(tmp_path: Path) -> None:
    sealed = seal_handoff(
        _source(tmp_path), tmp_path / "handoffs", publisher_tool=_publisher_tool(tmp_path)
    )
    record = {
        "object_id": sealed.object_id,
        "decision": "affirmative",
        "responsible_reviewer": "reviewer@example.org",
        "rights_authority": RIGHTS_AUTHORITY,
        "decision_time": "2026-09-01T00:00:00Z",
        "terms_version": "2026-09-01",
        "permitted_asset_use": RIGHTS_USE,
        "attribution": RIGHTS_ATTRIBUTION,
        "evidence_uri": "https://example.org/rights-record",
        "source_sha256": "b" * 64,
        "artifact_sha256": hashlib.sha256(
            sealed.path.joinpath("corpus.dump").read_bytes()
        ).hexdigest(),
        "corpus_release_id": "2026-08-30-r1",
    }
    rights = tmp_path / "rights.json"
    _write_rights(rights, record, tmp_path)

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


def test_data_only_verifier_rejects_release_date_different_from_source(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "corpus.dump").write_bytes(b"PGDMP-data")
    source = write_data_only_bundle(
        work_dir=work,
        output=tmp_path / "source",
        manifest=_verified_manifest("2026-08-31-r1"),
    )

    with pytest.raises(HandoffError, match="date must match"):
        verify_data_only_bundle(source)


def test_data_only_verifier_rejects_duplicate_migration_identity(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "corpus.dump").write_bytes(b"PGDMP-data")
    manifest = _verified_manifest()
    manifest.schema_migrations["control"].append("0001_base")
    source = write_data_only_bundle(work_dir=work, output=tmp_path / "source", manifest=manifest)

    with pytest.raises(HandoffError, match="migration identity"):
        verify_data_only_bundle(source)


def test_data_only_verifier_requires_bound_evaluation_evidence(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "corpus.dump").write_bytes(b"PGDMP-data")
    manifest = _verified_manifest()
    manifest.evaluation["result_sha256"] = "0" * 64
    source = write_data_only_bundle(work_dir=work, output=tmp_path / "source", manifest=manifest)

    with pytest.raises(HandoffError, match="result digest"):
        verify_data_only_bundle(source)


def test_data_only_verifier_rejects_incomplete_release_provenance(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "corpus.dump").write_bytes(b"PGDMP-data")
    incomplete = _verified_manifest()
    incomplete.app_git_sha = ""
    source = write_data_only_bundle(
        work_dir=work,
        output=tmp_path / "source",
        manifest=incomplete,
    )

    with pytest.raises(HandoffError, match="application Git revision"):
        verify_data_only_bundle(source)


def test_sealed_handoff_preserves_installable_wheel_filename(tmp_path: Path) -> None:
    tool = tmp_path / "publisher-tool"
    tool.mkdir()
    wheel = tool / "genereviews_link-5.1.4-py3-none-any.whl"
    wheel.write_bytes(b"sealed wheel")

    sealed = seal_handoff(_source(tmp_path), tmp_path / "handoffs", publisher_tool=tool)

    assert (sealed.path / wheel.name).is_file()
    manifest = json.loads(sealed.manifest.read_text())
    assert manifest["publisher_tool"]["name"] == wheel.name
    assert wheel.name in {entry["name"] for entry in manifest["files"]}


def test_verify_handoff_rejects_nonexact_seal_manifest_mode(tmp_path: Path) -> None:
    sealed = seal_handoff(
        _source(tmp_path), tmp_path / "handoffs", publisher_tool=_publisher_tool(tmp_path)
    )
    manifest = sealed.manifest
    manifest.chmod(0o600)
    with pytest.raises(HandoffError, match="mode 0400"):
        verify_handoff(tmp_path / "handoffs", sealed.object_id)


def test_rights_record_requires_durable_evidence_and_distinct_reviewers(tmp_path: Path) -> None:
    sealed = seal_handoff(
        _source(tmp_path), tmp_path / "handoffs", publisher_tool=_publisher_tool(tmp_path)
    )
    record = {
        "object_id": sealed.object_id,
        "decision": "affirmative",
        "responsible_reviewer": "reviewer@example.org",
        "rights_authority": RIGHTS_AUTHORITY,
        "decision_time": "2026-09-01T00:00:00Z",
        "terms_version": "2026-09-01",
        "permitted_asset_use": RIGHTS_USE,
        "attribution": RIGHTS_ATTRIBUTION,
        "evidence_uri": "/var/lib/genereviews/rights-record.json",
        "source_sha256": "a" * 64,
        "artifact_sha256": hashlib.sha256(
            sealed.path.joinpath("corpus.dump").read_bytes()
        ).hexdigest(),
        "corpus_release_id": "2026-08-30-r1",
    }
    rights = tmp_path / "rights.json"
    _write_rights(rights, record, tmp_path)
    assert verify_rights_record(rights, sealed.object_id)["decision"] == "affirmative"


def test_rights_record_rejects_relative_evidence(tmp_path: Path) -> None:
    sealed = seal_handoff(
        _source(tmp_path), tmp_path / "handoffs", publisher_tool=_publisher_tool(tmp_path)
    )
    record = {
        "object_id": sealed.object_id,
        "decision": "affirmative",
        "responsible_reviewer": "reviewer@example.org",
        "rights_authority": RIGHTS_AUTHORITY,
        "decision_time": "2026-09-01T00:00:00Z",
        "terms_version": "2026-09-01",
        "permitted_asset_use": RIGHTS_USE,
        "attribution": RIGHTS_ATTRIBUTION,
        "evidence_uri": "rights-record.json",
        "source_sha256": "a" * 64,
        "artifact_sha256": hashlib.sha256(
            sealed.path.joinpath("corpus.dump").read_bytes()
        ).hexdigest(),
        "corpus_release_id": "2026-08-30-r1",
    }
    rights = tmp_path / "rights.json"
    _write_rights(rights, record, tmp_path)
    record["evidence_uri"] = "rights-record.json"
    _rewrite_rights_digest(record)
    rights.write_text(json.dumps(record))
    with pytest.raises(HandoffError, match="bundle"):
        verify_rights_record(rights, sealed.object_id)
    record["evidence_uri"] = "/var/lib/rights-record.json"
    _rewrite_rights_digest(record)
    rights.write_text(json.dumps(record))
    with pytest.raises(HandoffError, match="bundle"):
        verify_rights_record(rights, sealed.object_id)


def test_rights_record_rejects_changed_terms_snapshot(tmp_path: Path) -> None:
    sealed = seal_handoff(
        _source(tmp_path), tmp_path / "handoffs", publisher_tool=_publisher_tool(tmp_path)
    )
    record = {
        "object_id": sealed.object_id,
        "decision": "affirmative",
        "responsible_reviewer": "reviewer@example.org",
        "rights_authority": RIGHTS_AUTHORITY,
        "decision_time": "2026-09-01T00:00:00Z",
        "terms_version": "2026-09-01",
        "permitted_asset_use": RIGHTS_USE,
        "attribution": RIGHTS_ATTRIBUTION,
        "source_sha256": "a" * 64,
        "artifact_sha256": hashlib.sha256(
            sealed.path.joinpath("corpus.dump").read_bytes()
        ).hexdigest(),
        "corpus_release_id": "2026-08-30-r1",
    }
    rights = tmp_path / "rights.json"
    _write_rights(rights, record, tmp_path)
    rights.with_name("terms-snapshot.html").write_text("changed after review\n")

    with pytest.raises(HandoffError, match="terms document digest"):
        prepare_publish_handoff(tmp_path / "handoffs", sealed.object_id, rights)


def test_seal_checks_real_postgres_archive_policy_before_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[tuple[Path, int]] = []
    monkeypatch.setattr(
        handoff,
        "_assert_local_archive",
        lambda path, *, parent_fd: called.append((path, parent_fd)),
        raising=False,
    )

    source = _source(tmp_path)
    seal_handoff(source, tmp_path / "handoffs", publisher_tool=_publisher_tool(tmp_path))

    assert len(called) == 1
    assert called[0][0] == source / "corpus.dump"
    assert called[0][1] >= 0


def test_handoff_root_rejects_repository_parent_and_serving_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("GENEREVIEW_SERVING_ROOT", str(tmp_path / "serving"))
    (tmp_path / "serving").mkdir()

    with pytest.raises(HandoffError, match="repository"):
        handoff._assert_handoff_root(repository_root)
    with pytest.raises(HandoffError, match="serving"):
        handoff._assert_handoff_root(tmp_path)


def test_seal_never_overwrites_target_created_at_final_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "handoffs"

    def racing_rename(source: Path, target: Path, *, parent_fd: int | None = None) -> None:
        del parent_fd
        target.mkdir()
        raise FileExistsError(target)

    monkeypatch.setattr(handoff, "_rename_noreplace", racing_rename, raising=False)

    with pytest.raises(HandoffError, match="already exists"):
        seal_handoff(_source(tmp_path), root, publisher_tool=_publisher_tool(tmp_path))
    target = next(path for path in root.iterdir() if not path.name.startswith(".seal-"))
    assert target.is_dir()


def test_seal_anchors_original_root_and_rejects_parent_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "handoffs"
    original_copy = handoff._copy_regular
    swapped = False

    def swap_root_after_open(
        source: Path,
        destination: Path,
        *,
        source_parent_fd: int | None = None,
        target_parent_fd: int | None = None,
    ) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            root.rename(tmp_path / "anchored-root")
            (tmp_path / "attacker-root").mkdir()
            root.symlink_to(tmp_path / "attacker-root", target_is_directory=True)
        original_copy(
            source,
            destination,
            source_parent_fd=source_parent_fd,
            target_parent_fd=target_parent_fd,
        )

    monkeypatch.setattr(handoff, "_copy_regular", swap_root_after_open)

    with pytest.raises(HandoffError, match="substituted"):
        seal_handoff(_source(tmp_path), root, publisher_tool=_publisher_tool(tmp_path))

    assert not any((tmp_path / "attacker-root").iterdir())
    anchored = tmp_path / "anchored-root"
    assert len(list(anchored.iterdir())) == 1


def test_privileged_verifier_path_has_no_asyncpg_transitive_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins
    import importlib
    import sys

    real_import = builtins.__import__

    def reject_asyncpg(name: str, *args: object, **kwargs: object) -> object:
        if name == "asyncpg" or name.startswith("asyncpg."):
            raise AssertionError("stdlib-only publisher verifier imported asyncpg")
        return real_import(name, *args, **kwargs)

    for name in list(sys.modules):
        if name.startswith("genereview_link.publisher_verifier"):
            sys.modules.pop(name)
    monkeypatch.setattr(builtins, "__import__", reject_asyncpg)
    verifier = importlib.import_module("genereview_link.publisher_verifier")

    assert Path(verifier.__file__).is_file()
    assert callable(verifier.prepare_publish_handoff)
