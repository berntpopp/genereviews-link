"""Regressions for the final GeneReviews publication/deployment review."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

import genereview_link.corpus.pipeline as corpus_pipeline
from genereview_link import download_guard
from genereview_link.corpus.readiness import ReadinessError, build_readiness_payload
from genereview_link.corpus.source_assets import (
    GENESIS_SOURCE_ASSETS,
    PRIOR_ASSETS,
    SOURCE_ASSETS,
)
from genereview_link.db.restore import ArchivePolicyError, extract_bundle, seed_identity_mode

ROOT = Path(__file__).resolve().parents[2]


def _readiness_manifest() -> dict[str, object]:
    return {
        "manifest_version": "3",
        "corpus_release_id": "2026-08-31-r1",
        "corpus_version": "2026-08-31",
        "tarball_source_sha256": "1" * 64,
        "chapter_count": 2,
        "passage_count": 3,
        "embedding": {"count": 3},
        "schema_migrations": {"control": ["0007_release_readiness"], "data": ["x"]},
        "hnsw": {"index_name": "idx"},
        "evaluation": {"result_sha256": "2" * 64},
        "checksums": {"corpus.dump": "3" * 64},
    }


def test_legacy_restore_is_explicitly_compatible_but_never_controller_ready() -> None:
    cli = (ROOT / "genereview_link/cli.py").read_text()
    restore = (ROOT / "genereview_link/db/restore.py").read_text()

    assert "seed_identity_mode" in restore
    assert "legacy" in cli and "verified-v1" in cli
    assert "write_release_readiness" in cli and "manifest_version" in cli
    assert seed_identity_mode("sha256:" + "a" * 64, "", "", "") == "legacy"
    assert seed_identity_mode("", "b" * 64, "c" * 64, "d" * 64) == "direct"
    with pytest.raises(ArchivePolicyError, match="incomplete"):
        seed_identity_mode("a" * 64, "b" * 64, "", "")


def test_source_asset_set_includes_exact_retained_listing_bytes() -> None:
    """One capture is exactly seven retained files; a genesis build is those minus one.

    The prior *seal* manifest is gone with the sealed-handoff scheme: the previous
    release's ``manifest.json`` is the only artifact a chained capture proves itself
    against, so the chained inventory is the genesis one plus exactly that file.
    """
    assert "file_list.csv" in SOURCE_ASSETS
    assert len(SOURCE_ASSETS) == 7
    assert set(PRIOR_ASSETS) == {"prior-manifest.json"}
    assert GENESIS_SOURCE_ASSETS == SOURCE_ASSETS - PRIOR_ASSETS
    assert len(GENESIS_SOURCE_ASSETS) == 6


def test_ingest_uses_a_distinct_session_lock_for_the_whole_staging_lifecycle() -> None:
    locks = (ROOT / "genereview_link/db/locks.py").read_text()
    pipeline = (ROOT / "genereview_link/corpus/pipeline.py").read_text()

    assert "CORPUS_INGEST_LOCK_KEY" in locks
    assert "pg_advisory_lock" in pipeline and "pg_advisory_unlock" in pipeline
    assert "CORPUS_INGEST_LOCK_KEY" in pipeline


@pytest.mark.asyncio
async def test_concurrent_ingests_cannot_interleave_the_shared_staging_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = asyncio.Lock()
    active = 0
    peak = 0

    class Connection:
        async def execute(self, statement: str, _key: int) -> None:
            if "pg_advisory_unlock" in statement:
                lock.release()
            else:
                await lock.acquire()

    class Acquire:
        async def __aenter__(self) -> Connection:
            return Connection()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Pool:
        def acquire(self) -> Acquire:
            return Acquire()

    async def bounded_ingest(*_args: object, **_kwargs: object) -> object:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return object()

    monkeypatch.setattr(corpus_pipeline, "_run_full_ingest_locked", bounded_ingest)
    paths = [tmp_path / str(index) for index in range(4)]
    await asyncio.gather(
        corpus_pipeline.run_full_ingest(
            Pool(),  # type: ignore[arg-type]
            archive=paths[0],
            side_data_dir=paths[1],
            source_metadata=paths[2],
            prior_manifest=paths[3],
        ),
        corpus_pipeline.run_full_ingest(
            Pool(),  # type: ignore[arg-type]
            archive=paths[0],
            side_data_dir=paths[1],
            source_metadata=paths[2],
            prior_manifest=paths[3],
        ),
    )
    assert peak == 1


def test_legacy_manifest_is_bounded_before_read_and_pg_restore_output_is_bounded(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "legacy.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("manifest.json")
        info.size = 2 * 1024 * 1024
        bundle.addfile(info, io.BytesIO(b"x" * info.size))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    with pytest.raises(ArchivePolicyError, match=r"manifest\.json.*size ceiling"):
        extract_bundle(archive, tmp_path / "out", expected_sha256=digest)
    restore = (ROOT / "genereview_link/db/restore.py").read_text()
    toc = restore.split("def read_archive_entries", 1)[1].split("def assert_data_only_archive", 1)[
        0
    ]
    assert "capture_output=True" not in toc
    assert "deadline" in toc or "timeout" in toc


@pytest.mark.asyncio
async def test_download_guard_never_overwrites_existing_target_and_cleans_cancellation(
    tmp_path: Path,
) -> None:
    target = tmp_path / "asset"
    target.write_bytes(b"preexisting")
    with pytest.raises(FileExistsError):
        await download_guard.stream_to_file(
            object(),  # type: ignore[arg-type]
            "https://example.test/asset",
            target,
            max_bytes=10,
        )
    assert target.read_bytes() == b"preexisting"

    partial = tmp_path / "partial"

    class Response:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self, _size: int):  # type: ignore[no-untyped-def]
            yield b"partial"
            raise asyncio.CancelledError

    class Stream:
        async def __aenter__(self) -> Response:
            return Response()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Client:
        def stream(self, *_args: object) -> Stream:
            return Stream()

    with pytest.raises(asyncio.CancelledError):
        await download_guard.stream_to_file(
            Client(),  # type: ignore[arg-type]
            "https://example.test/asset",
            partial,
            max_bytes=10,
        )
    assert not partial.exists()


def test_readiness_digest_must_equal_manifest_corpus_dump_digest() -> None:
    manifest = _readiness_manifest()
    with pytest.raises(ReadinessError, match="artifact digest"):
        build_readiness_payload(
            manifest,
            counts={"chapters": 2, "passages": 3, "embeddings": 3},
            migrations=["control:0007_release_readiness", "data:x"],
            indexes=["idx"],
            source_digest="sha256:" + "1" * 64,
            query_result_sha256="2" * 64,
            artifact_digest="sha256:" + "4" * 64,
            manifest_digest="sha256:" + "5" * 64,
            checksums_digest="sha256:" + "6" * 64,
            release_tag="corpus-data-2026-08-30-r1",
        )


def test_prefixed_legacy_digest_is_normalized_consistently(tmp_path: Path) -> None:
    archive = tmp_path / "bundle.tar.gz"
    manifest = {
        "corpus_version": "legacy",
        "checksums": {"corpus.dump": hashlib.sha256(b"PGDMP").hexdigest()},
    }
    with tarfile.open(archive, "w:gz") as bundle:
        for name, content in (
            ("manifest.json", json.dumps(manifest).encode()),
            ("corpus.dump", b"PGDMP"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    assert (
        extract_bundle(archive, tmp_path / "out", expected_sha256=f"sha256:{digest}").corpus_version
        == "legacy"
    )


def test_ingest_provenance_uses_the_exact_reviewed_runtime_contract() -> None:
    verifier = (ROOT / "genereview_link/corpus/bundle_verifier.py").read_text()
    assert 'startswith("pgvector/pgvector' not in verifier
    assert "PG18_IMAGE" in verifier
    assert "ingest_provenance" in verifier and "validate_computation_provenance" in verifier


def test_docs_describe_the_maintainer_prebuilt_release_scheme_truthfully() -> None:
    """The documented scheme must be the one the code actually implements.

    Corpus bundles are built on the maintainer's workstation and published with
    ``gh release create``; the redistribution basis is the committed
    ``data/RIGHTS.json``. None of the machinery the docs used to describe -- the sealed
    handoff object, its locator secrets, the CI build attestation -- exists any more, so
    the docs must not still promise it.
    """
    data = (ROOT / "docs/data.md").read_text()
    agents = (ROOT / "AGENTS.md").read_text()
    readme = (ROOT / "README.md").read_text()

    for present in (
        "maintainer-prebuilt",
        "data/RIGHTS.json",
        "gh release create",
        "build_provenance",
        "rights_notice",
        "verify-corpus-bundle.yml",
    ):
        assert present in data, f"docs/data.md must document {present}"
    for absent in (
        "seal-handoff",
        "GENEREVIEWS_HANDOFF_LOCATOR",
        "gh attestation verify",
        "seal-manifest",
        "responsible_reviewer",
    ):
        assert absent not in data, f"docs/data.md still describes removed machinery: {absent}"

    assert 'build_provenance: "maintainer-prebuilt"' in agents
    assert "data/RIGHTS.json" in agents and "verify-corpus-bundle.yml" in agents
    assert "maintainer-prebuilt" in readme and "data/RIGHTS.json" in readme


def test_deployment_docs_still_describe_the_adopted_runtime_data_contract() -> None:
    deployment = (ROOT / "docs/deployment.md").read_text()

    assert "definitions.contract" in deployment
    assert "data_identity_contract" in deployment
    assert "unadopted" in deployment
