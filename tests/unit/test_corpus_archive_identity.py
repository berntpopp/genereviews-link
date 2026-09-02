"""`archive_content_identities` must be exact *and* finish.

Hashing members in sorted name order over a `tarfile` opened as `r:gz` makes
every backwards seek re-inflate the gzip stream from byte zero. On the real
~636 MB GeneReviews archive (2925 members whose archive order is not sorted
order) that read 386 MB/s for over an hour without finishing — indistinguishable
from a hang. These tests pin the two things that matter: the digests are exactly
what the sorted-order definition says they are, and the work is done in one pass.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from genereview_link.corpus.source_capture import (
    MAX_MEMBERS,
    SourceCaptureError,
    archive_content_identities,
)

MEMBERS = {
    # Deliberately written in an order that is not sorted order, so a
    # sorted-order reader must seek backwards.
    "z/last.nxml": b"<article>zeta</article>",
    "a/first.nxml": b"<article>alpha</article>",
    "m/middle.nxml": b"<article>mu</article>",
    "b/second.nxml": b"<article>beta</article>",
}


def _archive(tmp_path: Path, members: dict[str, bytes]) -> Path:
    path = tmp_path / "gene_NBK1116.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return path


def _reference(members: dict[str, bytes]) -> tuple[str, str]:
    """The definition, written out plainly: sorted name order, name\\0bytes\\0."""
    inventory = [
        {
            "name": name,
            "type": "file",
            "size_bytes": len(members[name]),
            "sha256": hashlib.sha256(members[name]).hexdigest(),
        }
        for name in sorted(members)
    ]
    expanded = hashlib.sha256()
    for name in sorted(members):
        expanded.update(name.encode())
        expanded.update(b"\0")
        expanded.update(members[name])
        expanded.update(b"\0")
    canonical = (json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return hashlib.sha256(canonical).hexdigest(), expanded.hexdigest()


def test_identities_match_the_sorted_order_definition(tmp_path: Path) -> None:
    assert archive_content_identities(_archive(tmp_path, MEMBERS)) == _reference(MEMBERS)


def test_the_gzip_stream_is_inflated_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the fix: one inflation, then seekable random access."""
    from genereview_link.corpus import source_capture

    path = _archive(tmp_path, MEMBERS)
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
    # The fixture must actually exercise the pathological case: hashing in sorted
    # order over the archive's own order requires seeking backwards.
    assert names != sorted(names)

    inflations = 0
    original = source_capture._decompress_bounded

    def _counting(source: object, target: object) -> None:
        nonlocal inflations
        inflations += 1
        original(source, target)  # type: ignore[arg-type]

    monkeypatch.setattr(source_capture, "_decompress_bounded", _counting)

    assert archive_content_identities(path) == _reference(MEMBERS)
    assert inflations == 1


def test_a_non_gzip_archive_is_still_refused(tmp_path: Path) -> None:
    path = tmp_path / "gene_NBK1116.tar.gz"
    path.write_bytes(b"not a gzip stream at all")

    with pytest.raises(SourceCaptureError, match="not a readable gzip tar capture"):
        archive_content_identities(path)


def test_a_truncated_gzip_stream_is_still_refused(tmp_path: Path) -> None:
    path = _archive(tmp_path, MEMBERS)
    path.write_bytes(path.read_bytes()[: -len(path.read_bytes()) // 2])

    with pytest.raises(SourceCaptureError, match="not a readable gzip tar capture"):
        archive_content_identities(path)


def test_an_empty_archive_is_still_refused(tmp_path: Path) -> None:
    path = tmp_path / "gene_NBK1116.tar.gz"
    with tarfile.open(path, "w:gz"):
        pass

    with pytest.raises(SourceCaptureError, match="member count is outside the reviewed bound"):
        archive_content_identities(path)


def test_a_directory_only_archive_is_still_refused(tmp_path: Path) -> None:
    path = tmp_path / "gene_NBK1116.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("dir")
        info.type = tarfile.DIRTYPE
        archive.addfile(info)

    with pytest.raises(SourceCaptureError, match="no regular source members"):
        archive_content_identities(path)


def test_an_unsafe_member_name_is_still_refused(tmp_path: Path) -> None:
    with pytest.raises(SourceCaptureError, match="unsafe or duplicate member"):
        archive_content_identities(_archive(tmp_path, {"../escape.nxml": b"<x/>"}))


def test_the_member_ceiling_is_still_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("genereview_link.corpus.source_capture.MAX_MEMBERS", 2)
    assert MAX_MEMBERS == 10_000

    with pytest.raises(SourceCaptureError, match="member count is outside the reviewed bound"):
        archive_content_identities(_archive(tmp_path, MEMBERS))


def test_the_expanded_byte_ceiling_is_still_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("genereview_link.corpus.source_capture.MAX_EXPANDED_BYTES", 4)

    with pytest.raises(SourceCaptureError, match="expanded data exceeds the reviewed bound"):
        archive_content_identities(_archive(tmp_path, MEMBERS))


def test_the_decompression_ceiling_is_enforced_before_the_tar_is_parsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A zip bomb must die during inflation, not after it has filled the disk."""
    monkeypatch.setattr("genereview_link.corpus.source_capture.MAX_EXPANDED_BYTES", 0)
    monkeypatch.setattr("genereview_link.corpus.source_capture._TAR_OVERHEAD_BYTES", 0)

    with pytest.raises(SourceCaptureError, match="expanded data exceeds the reviewed bound"):
        archive_content_identities(_archive(tmp_path, MEMBERS))
