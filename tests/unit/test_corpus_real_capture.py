"""What the offline capture path does with the *real* upstream bytes.

Both fixes here were found by running the pipeline against live NCBI data for
the first time (#147). Each one refused a perfectly valid capture, and each was
invisible to the existing tests because their fixtures were cleaner than reality:
a hand-written listing is ASCII, and a hand-written side-data identity was built
in the stored shape rather than the capture shape.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest

from genereview_link.corpus.archive import decodable_listing_rows, listing_from_bytes
from genereview_link.corpus.pipeline import (
    _database_side_data_identity,
    record_corpus_version_start,
)
from genereview_link.corpus.source_capture import (
    SourceCaptureError,
    archive_content_identities,
    load_offline_capture,
)

GENEREVIEWS_ROW = b"ca/84/gene_NBK1116.tar.gz,GeneReviews,NCBI,1993,NBK1116,2026-09-01 02:42:06"
# Real rows from https://ftp.ncbi.nlm.nih.gov/pub/litarch/file_list.csv: NCBI's
# global index carries latin-1 bytes in unrelated titles.
LATIN1_ROW = (
    b"26/5f/who375925spa_NBK623781.tar.gz,Opciones terap\xe9uticas contra el "
    b"\xc9bola,World Health Organization,2023,NBK623781,2026-08-01 00:00:00"
)


def test_a_latin1_row_elsewhere_in_the_index_does_not_hide_genereviews() -> None:
    body = b"\n".join([LATIN1_ROW, GENEREVIEWS_ROW, LATIN1_ROW]) + b"\n"

    rows = decodable_listing_rows(body)

    assert rows == [GENEREVIEWS_ROW.decode(), ""]
    assert listing_from_bytes(body).relpath == "ca/84/gene_NBK1116.tar.gz"


def test_carriage_returns_are_stripped_from_retained_rows() -> None:
    assert decodable_listing_rows(GENEREVIEWS_ROW + b"\r\n") == [GENEREVIEWS_ROW.decode(), ""]


def test_an_undecodable_genereviews_row_still_fails_closed() -> None:
    """Skipping bad rows must not become a way to smuggle one past validation."""
    body = GENEREVIEWS_ROW.replace(b"GeneReviews", b"Gene\xe9Reviews") + b"\n"

    assert decodable_listing_rows(body) == [""]
    with pytest.raises(RuntimeError, match="NBK1116 not found"):
        listing_from_bytes(body)


def _identity(path: Path) -> dict[str, object]:
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
    }


def _real_shaped_capture(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    root = tmp_path / "retained"
    root.mkdir()
    payload = b"<article>NBK9999</article>"
    archive = root / "gene_NBK1116.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        info = tarfile.TarInfo("NBK9999/NBK9999.nxml")
        info.size = len(payload)
        handle.addfile(info, io.BytesIO(payload))
    (root / "GRtitle_shortname_NBKid.txt").write_text("nine\tChapter nine\tNBK9999\t1\n")
    (root / "NBKid_shortname_genesymbol.txt").write_text("NBK9999\tnine\tGENE9\n")
    (root / "NBKid_shortname_OMIM.txt").write_text("NBK9999\tnine\t100009\n")
    # A real index: the GeneReviews row alongside rows that are not UTF-8.
    listing = b"\n".join([LATIN1_ROW, GENEREVIEWS_ROW]) + b"\n"
    (root / "file_list.csv").write_bytes(listing)
    members_sha256, expanded_sha256 = archive_content_identities(archive)
    capture: dict[str, Any] = {
        "format": "genereviews-offline-source-v1",
        "genesis": True,
        "listing": {
            "url": "https://ftp.ncbi.nlm.nih.gov/pub/litarch/file_list.csv",
            "raw_sha256": hashlib.sha256(listing).hexdigest(),
            "raw_size_bytes": len(listing),
            "captured_at": "2026-09-02T00:00:00Z",
            "integrity_class": "https-captured-untrusted",
            "relpath": "ca/84/gene_NBK1116.tar.gz",
            "last_updated": "2026-09-01 02:42:06",
        },
        "archive": {
            "url": "https://ftp.ncbi.nlm.nih.gov/pub/litarch/ca/84/gene_NBK1116.tar.gz",
            **_identity(archive),
            "members_sha256": members_sha256,
            "expanded_sha256": expanded_sha256,
        },
        "side_data": {
            name: {
                "url": f"https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews/{name}",
                **_identity(root / name),
            }
            for name in (
                "GRtitle_shortname_NBKid.txt",
                "NBKid_shortname_genesymbol.txt",
                "NBKid_shortname_OMIM.txt",
            )
        },
        "chapter_ids": ["NBK9999"],
        "prior_artifact": None,
    }
    metadata = root / "source-capture.json"
    metadata.write_text(json.dumps(capture, sort_keys=True))
    return archive, root, capture


def test_a_capture_over_the_real_index_shape_loads(tmp_path: Path) -> None:
    archive, root, capture = _real_shaped_capture(tmp_path)

    loaded = load_offline_capture(
        root / "source-capture.json",
        archive=archive,
        side_data_dir=root,
        prior_manifest=None,
    )

    assert loaded == capture


def test_a_listing_digest_mismatch_is_still_refused(tmp_path: Path) -> None:
    archive, root, capture = _real_shaped_capture(tmp_path)
    capture["listing"]["raw_sha256"] = "0" * 64
    (root / "source-capture.json").write_text(json.dumps(capture, sort_keys=True))

    with pytest.raises(SourceCaptureError, match=r"file_list\.csv bytes do not match"):
        load_offline_capture(
            root / "source-capture.json",
            archive=archive,
            side_data_dir=root,
            prior_manifest=None,
        )


def test_two_genereviews_rows_are_still_refused(tmp_path: Path) -> None:
    archive, root, capture = _real_shaped_capture(tmp_path)
    listing = b"\n".join([GENEREVIEWS_ROW, LATIN1_ROW, GENEREVIEWS_ROW]) + b"\n"
    (root / "file_list.csv").write_bytes(listing)
    capture["listing"]["raw_sha256"] = hashlib.sha256(listing).hexdigest()
    capture["listing"]["raw_size_bytes"] = len(listing)
    (root / "source-capture.json").write_text(json.dumps(capture, sort_keys=True))

    with pytest.raises(SourceCaptureError, match="exactly one canonical NBK1116 row"):
        load_offline_capture(
            root / "source-capture.json",
            archive=archive,
            side_data_dir=root,
            prior_manifest=None,
        )


def test_capture_side_data_is_projected_onto_the_stored_identity() -> None:
    """A capture entry carries `url`; the stored identity is digest and size only."""
    capture_side_data = {
        "GRtitle_shortname_NBKid.txt": {
            "url": "https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews/GRtitle_shortname_NBKid.txt",
            "sha256": "a" * 64,
            "size_bytes": 10,
        }
    }

    assert _database_side_data_identity(capture_side_data) == {
        "GRtitle_shortname_NBKid.txt": {"sha256": "a" * 64, "size_bytes": 10}
    }


def test_side_data_without_a_digest_identity_is_refused() -> None:
    with pytest.raises(ValueError, match="lacks its digest identity"):
        _database_side_data_identity({"GRtitle_shortname_NBKid.txt": {"url": "https://x/y"}})


async def test_recording_a_version_accepts_a_capture_shaped_side_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression itself: stage 0 refused every real capture before writing a row."""
    from genereview_link.corpus.archive import ArchiveListing

    recorded: dict[str, object] = {}

    class _Connection:
        async def execute(self, *args: object) -> None:
            recorded.setdefault("executed", [])  # type: ignore[arg-type]

        async def fetchval(self, *args: object) -> None:
            return None

        def transaction(self) -> Any:
            connection = self

            class _Transaction:
                async def __aenter__(self) -> Any:
                    return connection

                async def __aexit__(self, *args: object) -> None:
                    return None

            return _Transaction()

    class _Pool:
        def acquire(self) -> Any:
            class _Acquire:
                async def __aenter__(self) -> Any:
                    return _Connection()

                async def __aexit__(self, *args: object) -> None:
                    return None

            return _Acquire()

    async def _locked(conn: object, **kwargs: object) -> str:
        recorded["side_data"] = kwargs["exact_side_data"]
        return "2026-09-01"

    monkeypatch.setattr(
        "genereview_link.corpus.pipeline._record_corpus_version_start_locked", _locked
    )
    listing = ArchiveListing(
        relpath="ca/84/gene_NBK1116.tar.gz",
        title="GeneReviews",
        publisher="NCBI",
        initial_year="1993",
        nbk_id="NBK1116",
        last_updated="2026-09-01 02:42:06",
    )
    side_data = {
        name: {
            "url": f"https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews/{name}",
            "sha256": digest * 64,
            "size_bytes": 10,
        }
        for name, digest in (
            ("GRtitle_shortname_NBKid.txt", "a"),
            ("NBKid_shortname_genesymbol.txt", "b"),
            ("NBKid_shortname_OMIM.txt", "c"),
        )
    }

    version = await record_corpus_version_start(
        _Pool(),  # type: ignore[arg-type]
        listing=listing,
        tarball_sha256="d" * 64,
        size=636758863,
        side_data=side_data,
    )

    assert version == "2026-09-01"
    assert recorded["side_data"] == {
        name: {"sha256": entry["sha256"], "size_bytes": 10} for name, entry in side_data.items()
    }
