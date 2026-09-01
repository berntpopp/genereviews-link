"""Regressions for the final GeneReviews publication/deployment review."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import io
import json
import tarfile
import textwrap
from pathlib import Path

import pytest

import genereview_link.corpus.handoff_locator as handoff_locator
import genereview_link.corpus.pipeline as corpus_pipeline
import genereview_link.corpus.rights_locator as rights_locator
from genereview_link import download_guard
from genereview_link.corpus.readiness import ReadinessError, build_readiness_payload
from genereview_link.corpus.rights import (
    RIGHTS_APPROVAL_KIND,
    RIGHTS_AUTHORITY,
    RIGHTS_FIELDS,
    RIGHTS_PERMITTED_ASSET_USE,
    RIGHTS_TERMS_SOURCE_URI,
)
from genereview_link.corpus.source_locator import SOURCE_ASSETS
from genereview_link.db.restore import ArchivePolicyError, extract_bundle, seed_identity_mode

ROOT = Path(__file__).resolve().parents[2]


def _literal_string_sets(source: str) -> set[frozenset[str]]:
    body = textwrap.dedent(source.split("<<'PY'\n", 1)[1])
    python = body.rsplit("\nPY", 1)[0]
    return {
        frozenset(element.value for element in node.elts)
        for node in ast.walk(ast.parse(python))
        if isinstance(node, ast.Set)
        and all(
            isinstance(element, ast.Constant) and isinstance(element.value, str)
            for element in node.elts
        )
    }


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


def test_privileged_publisher_verifies_build_attestations_before_wheel_or_rights() -> None:
    workflow = (ROOT / ".github/workflows/corpus-data-release.yml").read_text()
    publish = workflow.split("  publish:", 1)[1]
    attest = publish.index("gh attestation verify")
    extract = publish.index("zipfile.ZipFile")
    rights = publish.index("GENEREVIEWS_RIGHTS_LOCATOR")

    assert attest < extract and attest < rights
    assert "Attest exact sealed publication inputs" not in publish
    build = workflow.split("  build:", 1)[1].split("  publish:", 1)[0]
    for name in (
        "corpus.dump",
        "manifest.json",
        "SHA256SUMS",
        "seal-manifest.json",
        "publisher-tool.whl",
    ):
        assert name in build.split("Attest the exact evaluated build output", 1)[1]


def test_workflows_bind_rights_to_seal_and_never_follow_unchecked_handoff_redirects() -> None:
    publisher = (ROOT / ".github/workflows/corpus-data-release.yml").read_text()
    verifier = (ROOT / ".github/workflows/verify-corpus-bundle.yml").read_text()
    fetch = publisher.split("name: Fetch durable digest-addressed sealed handoff", 1)[1].split(
        "- name:", 1
    )[0]

    assert "curl" not in fetch or "--location" not in fetch
    assert frozenset(
        {
            "github.com",
            "release-assets.githubusercontent.com",
            "objects.githubusercontent.com",
            "github-releases.githubusercontent.com",
        }
    ) in _literal_string_sets(fetch)
    assert "sealed_values" in verifier
    assert "artifact_sha256" in verifier and "source_sha256" in verifier


def test_source_locator_includes_exact_retained_listing_bytes() -> None:
    assert "file_list.csv" in SOURCE_ASSETS
    assert len(SOURCE_ASSETS) == 8


def test_rights_contract_is_explicit_owner_determination_not_upstream_approval() -> None:
    rights = (ROOT / "genereview_link/corpus/rights.py").read_text()

    assert {"approval_kind", "upstream_approval", "terms_source_uri"} <= RIGHTS_FIELDS
    assert RIGHTS_AUTHORITY == "Bernt Popp / repository owner"
    assert RIGHTS_APPROVAL_KIND == "repository-owner redistribution determination"
    assert "upstream_approval" in rights and " is not False" in rights
    assert RIGHTS_PERMITTED_ASSET_USE == (
        "immutable GeneReviews research corpus artifact for noncommercial research purposes "
        "only; no further modifications"
    )
    assert RIGHTS_TERMS_SOURCE_URI == "https://www.genereviews.org/"


def test_existing_draft_tag_state_is_checked_before_missing_asset_upload() -> None:
    transaction = (ROOT / "genereview_link/corpus/release_transaction.py").read_text()
    workflow = (ROOT / ".github/workflows/corpus-data-release.yml").read_text()
    gate = workflow.split("name: Four-state immutable publication gate", 1)[1]

    assert "verify_existing_tag_state" in transaction
    assert "verify_existing_tag_state" in gate
    assert gate.index("verify_existing_tag_state") < gate.index("data-binary")


def test_release_selection_uses_exhaustive_release_and_ref_union() -> None:
    workflow = (ROOT / ".github/workflows/corpus-data-release.yml").read_text()
    build = workflow.split("  build:", 1)[1].split("  publish:", 1)[0]

    assert "release_selection import" in build
    assert "release-tags.txt" in build and "tag-refs.json" in build
    assert "matching-refs/tags/corpus-data-" in build


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
    paths = [tmp_path / str(index) for index in range(5)]
    await asyncio.gather(
        corpus_pipeline.run_full_ingest(
            Pool(),  # type: ignore[arg-type]
            archive=paths[0],
            side_data_dir=paths[1],
            source_metadata=paths[2],
            prior_manifest=paths[3],
            prior_seal_manifest=paths[4],
        ),
        corpus_pipeline.run_full_ingest(
            Pool(),  # type: ignore[arg-type]
            archive=paths[0],
            side_data_dir=paths[1],
            source_metadata=paths[2],
            prior_manifest=paths[3],
            prior_seal_manifest=paths[4],
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


def test_sync_locators_enforce_monotonic_end_to_end_deadlines() -> None:
    for path in (
        ROOT / "genereview_link/corpus/handoff_locator.py",
        ROOT / "genereview_link/corpus/rights_locator.py",
    ):
        source = path.read_text()
        assert "monotonic" in source
        assert "deadline" in source


class _Response:
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int) -> bytes:
        return b"x"


class _Opener:
    def open(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()


def test_rights_locator_deadline_is_total_and_removes_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = [
        {
            "name": name,
            "url": f"https://api.github.com/repos/owner/repo/releases/assets/{index}",
            "sha256": hashlib.sha256(b"x").hexdigest(),
            "size_bytes": 1,
        }
        for index, name in enumerate(sorted(rights_locator.RIGHTS_ASSET_NAMES), 1)
    ]
    destination = tmp_path / "rights"
    destination.mkdir()
    ticks = iter((0.0, rights_locator.RIGHTS_TRANSFER_DEADLINE_SECONDS + 1))
    monkeypatch.setattr(rights_locator, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(rights_locator, "build_opener", lambda *_args: _Opener())

    with pytest.raises(rights_locator.RightsLocatorError, match="monotonic deadline"):
        rights_locator.fetch_rights_assets(
            json.dumps({"format": "genereviews-rights-locator-v1", "assets": assets}).encode(),
            allowed_repositories={"owner/repo"},
            destination=destination,
            token="token",  # noqa: S106 - non-secret test fixture
        )
    assert not any(destination.iterdir())


def test_handoff_locator_deadline_is_total_and_removes_partial_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = [
        "SHA256SUMS",
        "corpus.dump",
        "genereview_link-5.1.6-py3-none-any.whl",
        "manifest.json",
        "seal-manifest.json",
    ]
    assets = [
        {
            "name": name,
            "url": f"https://api.github.com/repos/owner/repo/releases/assets/{index}",
            "sha256": hashlib.sha256(b"x").hexdigest(),
            "size_bytes": 1,
        }
        for index, name in enumerate(names, 1)
    ]
    destination = tmp_path / "handoff"
    destination.mkdir(mode=0o700)
    ticks = iter((0.0, handoff_locator.HANDOFF_TRANSFER_DEADLINE_SECONDS + 1))
    monkeypatch.setattr(handoff_locator, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(handoff_locator, "build_opener", lambda *_args: _Opener())
    object_id = "a" * 64
    locator = {
        "format": "genereviews-handoff-locator-v1",
        "object_id": object_id,
        "build_revision": "b" * 40,
        "assets": assets,
    }

    with pytest.raises(handoff_locator.HandoffLocatorError, match="monotonic deadline"):
        handoff_locator.fetch_handoff(
            json.dumps(locator).encode(),
            allowed_repositories={"owner/repo"},
            destination_root=destination,
            token="token",  # noqa: S106 - non-secret test fixture
            expected_object_id=object_id,
            expected_build_revision="b" * 40,
        )
    assert not any(destination.iterdir())


def test_ingest_provenance_uses_the_exact_reviewed_runtime_contract() -> None:
    verifier = (ROOT / "genereview_link/corpus/bundle_verifier.py").read_text()
    assert 'startswith("pgvector/pgvector' not in verifier
    assert "PG18_IMAGE" in verifier
    assert "ingest_provenance" in verifier and "validate_computation_provenance" in verifier


def test_release_download_cleanup_catches_cancellation() -> None:
    source = (ROOT / "genereview_link/corpus/release_assets.py").read_text()
    download = source.split("async def download_release_assets", 1)[1]
    assert "except BaseException" in download


def test_docs_describe_source_only_work_and_truthful_unadopted_runtime_contract() -> None:
    changelog = (ROOT / "docs/CHANGELOG.md").read_text()
    deployment = (ROOT / "docs/deployment.md").read_text()

    assert "No corpus publication is enabled" not in changelog
    assert "No publication was performed" in changelog
    assert "definitions.contract" in deployment
    assert "data_identity_contract" in deployment
    assert "unadopted" in deployment
