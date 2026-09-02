"""A direct corpus seed shares `/seed` with the reviewed model seed directory.

Production binds one host directory read-only at `/seed`: the corpus release
assets live at its root and the ONNX model the init materialises lives in
`/seed/model` (`MODEL_SEED_PATH`). The direct admission rule "exactly
corpus.dump, manifest.json, SHA256SUMS" therefore has to tolerate that one
reviewed sibling directory -- and nothing else -- or a `corpus.dump` cutover
fails closed at restore on every host that also stages the model.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from genereview_link.db.direct_seed import DirectSeedError, extract_direct_seed


def _direct_seed(root: Path) -> tuple[str, str, str]:
    root.mkdir()
    manifest = b'{"corpus_release_id":"x"}\n'
    dump = b"PGDMP-data"
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


def test_the_reviewed_model_seed_directory_is_tolerated(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    anchors = _direct_seed(seed)
    (seed / "model").mkdir()
    (seed / "model" / "model.onnx").write_bytes(b"onnx")
    (seed / "model" / "tokenizer.json").write_bytes(b"{}")

    staged = _extract(seed, tmp_path / "restore", anchors)

    assert staged.dump.is_file()
    assert sorted(p.name for p in (tmp_path / "restore").iterdir()) == [
        "SHA256SUMS",
        "corpus.dump",
        "manifest.json",
    ]


def test_any_other_extra_entry_is_still_refused(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    anchors = _direct_seed(seed)
    (seed / "model").mkdir()
    (seed / "corpus-bundle.tar.gz").write_bytes(b"legacy")

    with pytest.raises(DirectSeedError, match="must contain exactly"):
        _extract(seed, tmp_path / "restore", anchors)
    assert not (tmp_path / "restore").exists()


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_a_model_entry_that_is_not_a_directory_is_refused(tmp_path: Path, kind: str) -> None:
    seed = tmp_path / "seed"
    anchors = _direct_seed(seed)
    if kind == "file":
        (seed / "model").write_bytes(b"not a directory")
    else:
        real = tmp_path / "elsewhere"
        real.mkdir()
        os.symlink(real, seed / "model")

    with pytest.raises(DirectSeedError, match="model"):
        _extract(seed, tmp_path / "restore", anchors)
    assert not (tmp_path / "restore").exists()


def test_a_missing_member_is_still_refused_with_the_model_present(
    tmp_path: Path,
) -> None:
    seed = tmp_path / "seed"
    anchors = _direct_seed(seed)
    (seed / "model").mkdir()
    (seed / "SHA256SUMS").unlink()

    with pytest.raises(DirectSeedError, match="must contain exactly"):
        _extract(seed, tmp_path / "restore", anchors)
