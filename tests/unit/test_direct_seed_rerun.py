"""The init sidecar re-stages the direct seed on EVERY start (E2).

`genereview-corpus-restore` is a run-once service, but `docker compose up` re-runs it on
every deploy and restart, over the same `genereview_restore_state` volume. Its second
run must therefore accept a destination that already holds the same verified seed, and
must still refuse -- never overwrite -- anything else there.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from genereview_link.db.direct_seed import DirectSeedError, extract_direct_seed


def _write_seed(root: Path, dump: bytes, manifest: bytes) -> tuple[str, str, str]:
    root.mkdir()
    dump_digest = hashlib.sha256(dump).hexdigest()
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    sums = f"{dump_digest}  corpus.dump\n{manifest_digest}  manifest.json\n".encode()
    (root / "corpus.dump").write_bytes(dump)
    (root / "manifest.json").write_bytes(manifest)
    (root / "SHA256SUMS").write_bytes(sums)
    return dump_digest, manifest_digest, hashlib.sha256(sums).hexdigest()


def _extract(seed: Path, destination: Path, anchors: tuple[str, str, str]):
    return extract_direct_seed(
        seed,
        destination,
        expected_dump_sha256=anchors[0],
        expected_manifest_sha256=anchors[1],
        expected_checksums_sha256=anchors[2],
    )


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}


def test_a_second_run_over_the_same_verified_seed_is_a_no_op(tmp_path: Path) -> None:
    seed, restore = tmp_path / "seed", tmp_path / "restore"
    anchors = _write_seed(seed, b"PGDMP-same", b'{"corpus_release_id":"a"}\n')
    first = _extract(seed, restore, anchors)
    before = _snapshot(restore)

    second = _extract(seed, restore, anchors)

    assert second.dump_sha256 == first.dump_sha256 == anchors[0]
    assert second.manifest == first.manifest == {"corpus_release_id": "a"}
    assert _snapshot(restore) == before  # same bytes, no stray staging files


def test_a_different_staged_release_is_refused_and_left_intact(tmp_path: Path) -> None:
    old_seed, new_seed, restore = tmp_path / "old", tmp_path / "new", tmp_path / "restore"
    _extract(old_seed, restore, _write_seed(old_seed, b"PGDMP-old", b'{"id":"old"}\n'))
    before = _snapshot(restore)
    new_anchors = _write_seed(new_seed, b"PGDMP-new", b'{"id":"new"}\n')

    with pytest.raises(DirectSeedError, match=r"different corpus release.*fresh"):
        _extract(new_seed, restore, new_anchors)
    assert _snapshot(restore) == before


@pytest.mark.parametrize("damage", ["truncated-dump", "missing-sums", "symlinked-manifest"])
def test_a_partial_or_corrupt_staged_seed_is_refused_and_left_intact(
    tmp_path: Path, damage: str
) -> None:
    seed, restore = tmp_path / "seed", tmp_path / "restore"
    anchors = _write_seed(seed, b"PGDMP-same", b'{"corpus_release_id":"a"}\n')
    _extract(seed, restore, anchors)
    if damage == "truncated-dump":
        (restore / "corpus.dump").write_bytes(b"PGDMP")
    elif damage == "missing-sums":
        (restore / "SHA256SUMS").unlink()
    else:
        (restore / "manifest.json").unlink()
        (restore / "manifest.json").symlink_to(seed / "manifest.json")
    before = {p.name: (p.is_symlink(), p.read_bytes()) for p in restore.iterdir()}

    with pytest.raises(DirectSeedError, match="partial or corrupt"):
        _extract(seed, restore, anchors)
    assert {p.name: (p.is_symlink(), p.read_bytes()) for p in restore.iterdir()} == before


def test_a_fifo_planted_in_the_restore_volume_is_refused_without_blocking(tmp_path: Path) -> None:
    seed, restore = tmp_path / "seed", tmp_path / "restore"
    anchors = _write_seed(seed, b"PGDMP-same", b'{"corpus_release_id":"a"}\n')
    _extract(seed, restore, anchors)
    (restore / "corpus.dump").unlink()
    os.mkfifo(restore / "corpus.dump")

    with pytest.raises(DirectSeedError, match="partial or corrupt"):
        _extract(seed, restore, anchors)
