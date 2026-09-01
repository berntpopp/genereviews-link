"""Regressions for the exact-head corpus publication follow-up review."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from genereview_link.corpus.handoff_locator import HandoffLocatorError, load_handoff_locator
from genereview_link.corpus.readiness import ReadinessError, require_release_readiness
from genereview_link.corpus.release_promotion import (
    PromotionStateError,
    assert_prepatch,
    freeze_release,
)
from genereview_link.corpus.rights import RightsError, verify_rights_record
from genereview_link.db.direct_seed import DirectSeedError, extract_direct_seed
from genereview_link.db.restore import ArchivePolicyError, extract_bundle

ROOT = Path(__file__).resolve().parents[2]


def _release() -> dict[str, object]:
    names = (
        "corpus.dump",
        "manifest.json",
        "SHA256SUMS",
        "rights-record.json",
        "rights-evidence.json",
        "terms-snapshot.html",
        "seal-manifest.json",
        "publisher-tool.whl",
    )
    return {
        "id": 17,
        "tag_name": "corpus-data-2026-08-30-r1",
        "target_commitish": "a" * 40,
        "draft": True,
        "immutable": False,
        "assets": [
            {"id": index, "name": name, "size": index, "digest": "sha256:" + "b" * 64}
            for index, name in enumerate(names, 1)
        ],
    }


def _active_ruleset() -> dict[str, object]:
    return {
        "id": 41,
        "target": "tag",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["refs/tags/corpus-data-*"], "exclude": []}},
        "rules": [{"type": "deletion"}, {"type": "update"}],
    }


def test_build_retains_exact_attested_handoff_and_materialization_identity() -> None:
    workflow = (ROOT / ".github/workflows/corpus-data-release.yml").read_text()
    build = workflow.split("  build:", 1)[1].split("  publish:", 1)[0]

    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in build
    assert "retention-days: 90" in build
    assert "handoff-materialization.json" in build
    assert "sealed-handoff-${{ steps.seal.outputs.object_id }}" in build
    attest = build.split("Attest the exact evaluated build output", 1)[1]
    assert "handoff-materialization.json" in attest
    for field in ("object_id", "build_revision", "source_ref", "subjects"):
        assert field in build
    materialization = build.split("Stage the exact sealed evaluated build output", 1)[1].split(
        "Attest the exact evaluated build output", 1
    )[0]
    assert "handle.read(1024 * 1024)" in materialization
    assert "path.read_bytes()" not in materialization


@pytest.mark.asyncio
async def test_active_direct_readiness_must_match_current_configured_release_tuple() -> None:
    stored = {
        "release_tag": "corpus-data-2026-08-30-r1",
        "artifact_digest": "sha256:" + "a" * 64,
        "manifest_digest": "sha256:" + "b" * 64,
        "checksums_digest": "sha256:" + "c" * 64,
        "schema_version": "3",
        "counts": {},
        "migrations": [],
        "indexes": [],
        "source_digest": "sha256:" + "d" * 64,
        "query_result_sha256": "e" * 64,
        "restore_count": 1,
        "restore_mode": "data-only",
        "operation_order": [
            "migrations",
            "data-only-restore",
            "counts",
            "hnsw",
            "source-digest",
            "semantic-query",
            "readiness-marker",
        ],
        "ready": True,
        "readiness_marker": "verified-v1",
    }

    class Pool:
        async def fetchval(self, _query: str) -> str:
            return json.dumps(stored)

    with pytest.raises(ReadinessError, match="configured direct release"):
        await require_release_readiness(
            Pool(),
            release_tag="corpus-data-2026-08-31-r1",
            artifact_digest="sha256:" + "f" * 64,
            manifest_digest="sha256:" + "1" * 64,
            checksums_digest="sha256:" + "2" * 64,
        )


def _direct_seed(
    root: Path, *, manifest: bytes = b'{"corpus_release_id":"x"}\n'
) -> tuple[str, str, str]:
    root.mkdir()
    dump = b"PGDMP-data"
    dump_digest = hashlib.sha256(dump).hexdigest()
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    sums = f"{dump_digest}  corpus.dump\n{manifest_digest}  manifest.json\n".encode()
    (root / "corpus.dump").write_bytes(dump)
    (root / "manifest.json").write_bytes(manifest)
    (root / "SHA256SUMS").write_bytes(sums)
    return dump_digest, manifest_digest, hashlib.sha256(sums).hexdigest()


def test_direct_seed_keyboard_interrupt_rolls_back_every_admitted_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = tmp_path / "seed"
    anchors = _direct_seed(seed)
    destination = tmp_path / "restore"
    real_link = os.link
    calls = 0

    def interrupt_after_link(*args: object, **kwargs: object) -> None:
        nonlocal calls
        real_link(*args, **kwargs)
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(os, "link", interrupt_after_link)
    with pytest.raises(KeyboardInterrupt):
        extract_direct_seed(
            seed,
            destination,
            expected_dump_sha256=anchors[0],
            expected_manifest_sha256=anchors[1],
            expected_checksums_sha256=anchors[2],
        )
    assert not any(destination.iterdir())

    monkeypatch.setattr(os, "link", real_link)
    assert extract_direct_seed(
        seed,
        destination,
        expected_dump_sha256=anchors[0],
        expected_manifest_sha256=anchors[1],
        expected_checksums_sha256=anchors[2],
    ).dump.is_file()


def test_prepatch_rejects_tag_mutation_and_unprotected_tag_ruleset() -> None:
    frozen = freeze_release(
        _release(),
        etag='"verified"',
        tag="corpus-data-2026-08-30-r1",
        target_commit="a" * 40,
        tag_object_sha="c" * 40,
    )
    assert_prepatch(
        frozen,
        conditional_status=304,
        tag_object_sha="c" * 40,
        ruleset=_active_ruleset(),
    )
    with pytest.raises(PromotionStateError, match="tag"):
        assert_prepatch(
            frozen,
            conditional_status=304,
            tag_object_sha="d" * 40,
            ruleset=_active_ruleset(),
        )
    unprotected = _active_ruleset()
    unprotected["bypass_actors"] = [{"actor_type": "RepositoryRole", "actor_id": 5}]
    with pytest.raises(PromotionStateError, match="ruleset"):
        assert_prepatch(
            frozen,
            conditional_status=304,
            tag_object_sha="c" * 40,
            ruleset=unprotected,
        )
    incomplete = _active_ruleset()
    incomplete["rules"] = [{"type": "deletion"}]
    with pytest.raises(PromotionStateError, match="ruleset"):
        assert_prepatch(
            frozen,
            conditional_status=304,
            tag_object_sha="c" * 40,
            ruleset=incomplete,
        )


def _deep_json() -> bytes:
    value = b"[" * 10_000 + b"]" * 10_000 + b"\n"
    assert len(value) == 20_001
    return value


def test_deep_json_maps_to_handoff_locator_domain_error() -> None:
    with pytest.raises(HandoffLocatorError):
        load_handoff_locator(
            _deep_json(),
            allowed_repositories={"owner/repo"},
            expected_object_id="a" * 64,
        )


def test_deep_json_maps_to_rights_domain_error(tmp_path: Path) -> None:
    rights = tmp_path / "rights-record.json"
    rights.write_bytes(_deep_json())
    with pytest.raises(RightsError):
        verify_rights_record(rights, "a" * 64)


def test_deep_json_maps_to_direct_seed_domain_error(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    anchors = _direct_seed(seed, manifest=_deep_json())
    with pytest.raises(DirectSeedError):
        extract_direct_seed(
            seed,
            tmp_path / "restore",
            expected_dump_sha256=anchors[0],
            expected_manifest_sha256=anchors[1],
            expected_checksums_sha256=anchors[2],
        )


def test_deep_json_maps_to_legacy_bundle_domain_error(tmp_path: Path) -> None:
    archive = tmp_path / "bundle.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        manifest = _deep_json()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        bundle.addfile(info, io.BytesIO(manifest))
    with pytest.raises(ArchivePolicyError):
        extract_bundle(
            archive,
            tmp_path / "restore",
            expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        )


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', _deep_json()], ids=("duplicate", "deep"))
def test_all_locator_parsers_reject_duplicate_or_deep_json_with_domain_errors(raw: bytes) -> None:
    from genereview_link.corpus.rights_locator import RightsLocatorError, load_rights_locator
    from genereview_link.corpus.source_locator import SourceLocatorError, load_source_locator

    with pytest.raises(RightsLocatorError):
        load_rights_locator(raw, allowed_repositories={"owner/repo"})
    with pytest.raises(SourceLocatorError):
        load_source_locator(raw, allowed_repositories={"owner/repo"})
