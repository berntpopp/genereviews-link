"""Regressions for the exact-head corpus publication follow-up review."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from genereview_link.corpus.readiness import ReadinessError, require_release_readiness
from genereview_link.corpus.rights_notice import RightsNoticeError, load_rights_notice
from genereview_link.db.direct_seed import DirectSeedError, extract_direct_seed
from genereview_link.db.restore import ArchivePolicyError, extract_bundle

ROOT = Path(__file__).resolve().parents[2]


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


def _deep_json() -> bytes:
    value = b"[" * 10_000 + b"]" * 10_000 + b"\n"
    assert len(value) == 20_001
    return value


def test_deep_json_maps_to_rights_notice_domain_error(tmp_path: Path) -> None:
    notice = tmp_path / "RIGHTS.json"
    notice.write_bytes(_deep_json())
    with pytest.raises(RightsNoticeError):
        load_rights_notice(notice)


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
def test_every_surviving_metadata_parser_rejects_duplicate_or_deep_json(
    tmp_path: Path, raw: bytes
) -> None:
    """Every parser that reads attacker-reachable metadata is strict, not stdlib ``json``.

    The locator parsers this once covered are gone with the sealed-handoff scheme; the
    parsers that remain -- the bundle's own metadata loader, the offline source capture
    loader, and the committed rights notice -- must still refuse the same inputs with a
    domain error rather than a ``RecursionError`` or a silent last-key-wins parse.
    """
    from genereview_link.corpus.bundle_integrity import BundleIntegrityError, _load_json
    from genereview_link.corpus.source_capture import SourceCaptureError, load_offline_capture

    bundle_metadata = tmp_path / "manifest.json"
    bundle_metadata.write_bytes(raw)
    with pytest.raises(BundleIntegrityError, match="invalid JSON"):
        _load_json(bundle_metadata)

    capture = tmp_path / "source-capture.json"
    capture.write_bytes(raw)
    with pytest.raises(SourceCaptureError, match="not valid JSON"):
        load_offline_capture(
            capture,
            archive=tmp_path / "archive",
            side_data_dir=tmp_path,
            prior_manifest=tmp_path / "prior-manifest.json",
        )

    notice = tmp_path / "RIGHTS.json"
    notice.write_bytes(raw)
    with pytest.raises(RightsNoticeError, match="not valid JSON"):
        load_rights_notice(notice)
