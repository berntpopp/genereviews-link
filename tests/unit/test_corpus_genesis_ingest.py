"""The genesis path: a first corpus build with no prior release to chain from.

The chain has to start somewhere. Before `--genesis` existed, every mutating
ingest demanded a prior manifest/seal pair built under the *current* scheme, so
the first build under that scheme was unreachable by construction (#147). These
tests pin both halves of the fix: genesis produces a provable capture without a
prior, and the absence of a prior without `--genesis` is still refused.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import pytest

from genereview_link.corpus.pipeline import _require_offline_source_set
from genereview_link.corpus.semantic_identity import compute_content_identity
from genereview_link.corpus.source_capture import (
    SourceCaptureError,
    archive_content_identities,
    load_offline_capture,
)
from genereview_link.corpus.source_fetch import prior_artifact_from
from genereview_link.corpus.source_snapshot import SourceSnapshotError, admit_offline_source

LISTING_ROW = b"ca/84/gene_NBK1116.tar.gz,GeneReviews,NCBI,1993,NBK1116,2026-08-31 02:41:04\n"


def _identity(path: Path) -> dict[str, object]:
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
    }


def _write_side_data(side_dir: Path) -> None:
    (side_dir / "GRtitle_shortname_NBKid.txt").write_text(
        "nine\tChapter nine\tNBK9999\t1\nnine-b\tChapter nine b\tNBK9998\t2\n"
    )
    (side_dir / "NBKid_shortname_genesymbol.txt").write_text(
        "NBK9999\tnine\tGENE9\nNBK9998\tnine-b\tGENE8\n"
    )
    (side_dir / "NBKid_shortname_OMIM.txt").write_text(
        "NBK9999\tnine\t100009\nNBK9998\tnine-b\t100008\n"
    )


def _fixture_corpus(tmp_path: Path) -> tuple[Path, Path]:
    """One tiny NBK9999-style archive plus its three side-data files."""
    root = tmp_path / "retained"
    root.mkdir()
    member = tmp_path / "NBK9999.nxml"
    member.write_text("<article>genesis fixture</article>")
    archive = root / "gene_NBK1116.tar.gz"
    with tarfile.open(archive, "w:gz") as retained:
        retained.add(member, arcname="NBK9999/NBK9999.nxml")
    _write_side_data(root)
    (root / "file_list.csv").write_bytes(LISTING_ROW)
    return archive, root


def _genesis_capture(archive: Path, root: Path) -> dict[str, object]:
    members_sha256, expanded_sha256 = archive_content_identities(archive)
    return {
        "format": "genereviews-offline-source-v1",
        "genesis": True,
        "listing": {
            "url": "https://ftp.ncbi.nlm.nih.gov/pub/litarch/file_list.csv",
            "raw_sha256": hashlib.sha256(LISTING_ROW).hexdigest(),
            "raw_size_bytes": len(LISTING_ROW),
            "captured_at": "2026-08-31T03:00:00Z",
            "integrity_class": "https-captured-untrusted",
            "relpath": "ca/84/gene_NBK1116.tar.gz",
            "last_updated": "2026-08-31 02:41:04",
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
        "chapter_ids": ["NBK9998", "NBK9999"],
        "prior_artifact": None,
    }


def _write_capture(root: Path, capture: dict[str, object]) -> Path:
    metadata = root / "source-capture.json"
    metadata.write_text(json.dumps(capture, sort_keys=True))
    return metadata


def test_genesis_capture_loads_without_any_prior_pair(tmp_path: Path) -> None:
    archive, root = _fixture_corpus(tmp_path)
    capture = _genesis_capture(archive, root)

    loaded = load_offline_capture(
        _write_capture(root, capture),
        archive=archive,
        side_data_dir=root,
        prior_manifest=None,
        prior_seal_manifest=None,
    )

    assert loaded["genesis"] is True
    assert loaded["prior_artifact"] is None
    assert loaded["chapter_ids"] == ["NBK9998", "NBK9999"]


def test_genesis_capture_refuses_a_prior_it_claims_not_to_have(tmp_path: Path) -> None:
    archive, root = _fixture_corpus(tmp_path)
    metadata = _write_capture(root, _genesis_capture(archive, root))
    stray = root / "prior-manifest.json"
    stray.write_text("{}")

    with pytest.raises(SourceCaptureError, match="must not name or carry a prior artifact"):
        load_offline_capture(
            metadata,
            archive=archive,
            side_data_dir=root,
            prior_manifest=stray,
            prior_seal_manifest=stray,
        )


def test_capture_without_genesis_still_requires_a_prior_pair(tmp_path: Path) -> None:
    archive, root = _fixture_corpus(tmp_path)
    capture = _genesis_capture(archive, root)
    del capture["genesis"]

    with pytest.raises(SourceCaptureError, match="requires a retained prior manifest pair"):
        load_offline_capture(
            _write_capture(root, capture),
            archive=archive,
            side_data_dir=root,
            prior_manifest=None,
            prior_seal_manifest=None,
        )


def test_genesis_flag_must_be_a_literal_boolean(tmp_path: Path) -> None:
    archive, root = _fixture_corpus(tmp_path)
    capture = _genesis_capture(archive, root)
    capture["genesis"] = "true"

    with pytest.raises(SourceCaptureError, match="genesis flag must be a literal boolean"):
        load_offline_capture(
            _write_capture(root, capture),
            archive=archive,
            side_data_dir=root,
            prior_manifest=None,
            prior_seal_manifest=None,
        )


def test_genesis_admission_copies_the_inventory_minus_the_prior_pair(tmp_path: Path) -> None:
    archive, root = _fixture_corpus(tmp_path)
    _write_capture(root, _genesis_capture(archive, root))

    with admit_offline_source(
        archive=archive,
        side_data_dir=root,
        source_metadata=root / "source-capture.json",
        prior_manifest=None,
        prior_seal_manifest=None,
    ) as admitted:
        names = {path.name for path in admitted.root.iterdir()}
        assert admitted.prior_manifest is None
        assert admitted.prior_seal_manifest is None

    assert names == {
        "source-capture.json",
        "file_list.csv",
        "gene_NBK1116.tar.gz",
        "GRtitle_shortname_NBKid.txt",
        "NBKid_shortname_genesymbol.txt",
        "NBKid_shortname_OMIM.txt",
    }


def test_admission_refuses_half_a_prior_pair(tmp_path: Path) -> None:
    archive, root = _fixture_corpus(tmp_path)
    _write_capture(root, _genesis_capture(archive, root))

    with (
        pytest.raises(SourceSnapshotError, match="requires both prior files"),
        admit_offline_source(
            archive=archive,
            side_data_dir=root,
            source_metadata=root / "source-capture.json",
            prior_manifest=root / "prior-manifest.json",
            prior_seal_manifest=None,
        ),
    ):
        pass


def test_ingest_without_genesis_or_a_prior_keeps_todays_refusal() -> None:
    with pytest.raises(ValueError, match="mutating ingest requires the complete retained"):
        _require_offline_source_set(
            archive=None,
            side_data_dir=None,
            source_metadata=None,
            prior_manifest=None,
            prior_seal_manifest=None,
            genesis=False,
        )


def test_ingest_with_a_partial_offline_set_is_still_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="offline ingest requires archive"):
        _require_offline_source_set(
            archive=tmp_path / "a",
            side_data_dir=tmp_path,
            source_metadata=tmp_path / "m",
            prior_manifest=None,
            prior_seal_manifest=None,
            genesis=False,
        )


def test_genesis_ingest_refuses_a_prior_pair(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not be given a prior manifest pair"):
        _require_offline_source_set(
            archive=tmp_path / "a",
            side_data_dir=tmp_path,
            source_metadata=tmp_path / "m",
            prior_manifest=tmp_path / "p",
            prior_seal_manifest=tmp_path / "s",
            genesis=True,
        )


def test_genesis_ingest_still_requires_the_retained_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="genesis ingest requires archive"):
        _require_offline_source_set(
            archive=tmp_path / "a",
            side_data_dir=None,
            source_metadata=tmp_path / "m",
            prior_manifest=None,
            prior_seal_manifest=None,
            genesis=True,
        )


def _rows(chapter_ids: list[str]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    chapters = [{"nbk_id": nbk, "title": f"Chapter {nbk}"} for nbk in chapter_ids]
    passages = [{"nbk_id": nbk, "passage_id": f"{nbk}:1", "text": "body"} for nbk in chapter_ids]
    return chapters, passages


def test_genesis_content_identity_records_a_null_prior(tmp_path: Path) -> None:
    archive, root = _fixture_corpus(tmp_path)
    capture = _genesis_capture(archive, root)
    chapters, passages = _rows(["NBK9998", "NBK9999"])

    identity = compute_content_identity(
        chapters=chapters,
        passages=passages,
        side_mapping_ids={"NBK9998", "NBK9999"},
        source_capture=capture,
    )

    delta = identity["delta_from_prior"]
    assert isinstance(delta, dict)
    assert delta["genesis"] is True
    assert delta["object_id"] is None
    assert delta["prior_chapter_count"] is None
    assert delta["added"] == ["NBK9998", "NBK9999"]
    assert delta["removed"] == [] and delta["changed"] == []
    assert identity["chapter_count"] == 2


def test_content_identity_still_needs_a_prior_when_not_genesis(tmp_path: Path) -> None:
    archive, root = _fixture_corpus(tmp_path)
    capture = _genesis_capture(archive, root)
    del capture["genesis"]
    chapters, passages = _rows(["NBK9998", "NBK9999"])

    with pytest.raises(ValueError, match="lacks prior per-chapter identity"):
        compute_content_identity(
            chapters=chapters,
            passages=passages,
            side_mapping_ids={"NBK9998", "NBK9999"},
            source_capture=capture,
        )


def _seal_bytes(release_id: str, manifest_bytes: bytes) -> bytes:
    return (
        json.dumps(
            {
                "format": "genereviews-local-handoff-v1",
                "corpus_release_id": release_id,
                "genesis": True,
                "prior": None,
                "files": [
                    {
                        "name": "manifest.json",
                        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                        "size": len(manifest_bytes),
                        "mode": 0o400,
                    }
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def test_a_chained_capture_verifies_against_a_genesis_release(tmp_path: Path) -> None:
    """The point of genesis: the *second* build must chain off the first."""
    archive, root = _fixture_corpus(tmp_path)
    genesis_capture = _genesis_capture(archive, root)
    chapters, passages = _rows(["NBK9998", "NBK9999"])
    genesis_identity = compute_content_identity(
        chapters=chapters,
        passages=passages,
        side_mapping_ids={"NBK9998", "NBK9999"},
        source_capture=genesis_capture,
    )
    manifest_bytes = json.dumps(
        {
            "manifest_version": "3",
            "corpus_release_id": "2026-08-31-r1",
            "app_git_sha": "1" * 40,
            "content_identity": genesis_identity,
        },
        sort_keys=True,
    ).encode()
    prior_manifest = root / "prior-manifest.json"
    prior_manifest.write_bytes(manifest_bytes)
    prior_seal = root / "prior-seal-manifest.json"
    prior_seal.write_bytes(_seal_bytes("2026-08-31-r1", manifest_bytes))

    chained = dict(genesis_capture)
    del chained["genesis"]
    chained["prior_artifact"] = prior_artifact_from(prior_manifest, prior_seal)

    loaded = load_offline_capture(
        _write_capture(root, chained),
        archive=archive,
        side_data_dir=root,
        prior_manifest=prior_manifest,
        prior_seal_manifest=prior_seal,
    )

    prior = loaded["prior_artifact"]
    assert isinstance(prior, dict)
    assert prior["corpus_release_id"] == "2026-08-31-r1"
    assert prior["chapter_count"] == 2
    assert prior["object_id"] == hashlib.sha256(prior_seal.read_bytes()).hexdigest()
