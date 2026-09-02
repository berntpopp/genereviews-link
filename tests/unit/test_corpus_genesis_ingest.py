"""The genesis path: a first corpus build with no prior release to chain from.

The chain has to start somewhere. Before `--genesis` existed, every mutating
ingest demanded a prior manifest built under the *current* scheme, so the first
build under that scheme was unreachable by construction (#147). These tests pin
both halves of the fix: genesis produces a provable capture without a prior, and
the absence of a prior without `--genesis` is still refused.

The chained half has the same unreachability hazard, so it is pinned here too:
a chained build must be provable from the previous release's published
`manifest.json` and nothing else, because that is the only prior byte-stream a
published release still carries.
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
        )


def test_capture_without_genesis_still_requires_a_prior_manifest(tmp_path: Path) -> None:
    archive, root = _fixture_corpus(tmp_path)
    capture = _genesis_capture(archive, root)
    del capture["genesis"]

    with pytest.raises(SourceCaptureError, match="requires a retained prior manifest"):
        load_offline_capture(
            _write_capture(root, capture),
            archive=archive,
            side_data_dir=root,
            prior_manifest=None,
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
        )


def test_genesis_admission_copies_the_inventory_minus_the_prior_manifest(tmp_path: Path) -> None:
    archive, root = _fixture_corpus(tmp_path)
    _write_capture(root, _genesis_capture(archive, root))

    with admit_offline_source(
        archive=archive,
        side_data_dir=root,
        source_metadata=root / "source-capture.json",
        prior_manifest=None,
    ) as admitted:
        names = {path.name for path in admitted.root.iterdir()}
        assert admitted.prior_manifest is None

    assert names == {
        "source-capture.json",
        "file_list.csv",
        "gene_NBK1116.tar.gz",
        "GRtitle_shortname_NBKid.txt",
        "NBKid_shortname_genesymbol.txt",
        "NBKid_shortname_OMIM.txt",
    }


def test_chained_admission_copies_the_prior_manifest_too(tmp_path: Path) -> None:
    """The chained inventory is the genesis one plus exactly `prior-manifest.json`."""
    archive, root = _fixture_corpus(tmp_path)
    _write_capture(root, _genesis_capture(archive, root))
    prior_manifest = root / "prior-manifest.json"
    prior_manifest.write_bytes(b'{"manifest_version":"3"}\n')

    with admit_offline_source(
        archive=archive,
        side_data_dir=root,
        source_metadata=root / "source-capture.json",
        prior_manifest=prior_manifest,
    ) as admitted:
        names = {path.name for path in admitted.root.iterdir()}
        assert admitted.prior_manifest is not None
        assert admitted.prior_manifest.read_bytes() == prior_manifest.read_bytes()

    assert names == {
        "source-capture.json",
        "file_list.csv",
        "prior-manifest.json",
        "gene_NBK1116.tar.gz",
        "GRtitle_shortname_NBKid.txt",
        "NBKid_shortname_genesymbol.txt",
        "NBKid_shortname_OMIM.txt",
    }


def test_admission_refuses_a_prior_manifest_that_is_not_there(tmp_path: Path) -> None:
    """A named-but-absent prior is a hard refusal, never a silent genesis."""
    archive, root = _fixture_corpus(tmp_path)
    _write_capture(root, _genesis_capture(archive, root))

    with (
        pytest.raises(SourceSnapshotError),
        admit_offline_source(
            archive=archive,
            side_data_dir=root,
            source_metadata=root / "source-capture.json",
            prior_manifest=root / "prior-manifest.json",
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
            genesis=False,
        )


def test_ingest_with_a_partial_offline_set_is_still_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="offline ingest requires archive"):
        _require_offline_source_set(
            archive=tmp_path / "a",
            side_data_dir=tmp_path,
            source_metadata=tmp_path / "m",
            prior_manifest=None,
            genesis=False,
        )


def test_genesis_ingest_refuses_a_prior_manifest(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not be given a prior manifest"):
        _require_offline_source_set(
            archive=tmp_path / "a",
            side_data_dir=tmp_path,
            source_metadata=tmp_path / "m",
            prior_manifest=tmp_path / "p",
            genesis=True,
        )


def test_genesis_ingest_still_requires_the_retained_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="genesis ingest requires archive"):
        _require_offline_source_set(
            archive=tmp_path / "a",
            side_data_dir=None,
            source_metadata=tmp_path / "m",
            prior_manifest=None,
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
    assert delta["prior_manifest_sha256"] is None
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


def _prior_release_manifest(root: Path, identity: dict[str, object]) -> Path:
    """Exactly what a published release carries: `manifest.json`, nothing beside it."""
    manifest_bytes = json.dumps(
        {
            "manifest_version": "3",
            "corpus_release_id": "2026-08-31-r1",
            "app_git_sha": "1" * 40,
            "content_identity": identity,
        },
        sort_keys=True,
    ).encode()
    prior_manifest = root / "prior-manifest.json"
    prior_manifest.write_bytes(manifest_bytes)
    return prior_manifest


def _chained_capture(root: Path, capture: dict[str, object], prior_manifest: Path) -> dict:
    chained = dict(capture)
    chained.pop("genesis", None)
    chained["prior_artifact"] = prior_artifact_from(prior_manifest)
    return chained


def test_a_chained_capture_verifies_against_a_genesis_release(tmp_path: Path) -> None:
    """The point of genesis: the *second* build must chain off the first.

    And it must do so from the previous release's published `manifest.json`
    alone -- that is the only prior byte-stream a release still publishes, so
    demanding anything beside it would make the chained path unreachable exactly
    the way the genesis path once was (#147, #149).
    """
    archive, root = _fixture_corpus(tmp_path)
    genesis_capture = _genesis_capture(archive, root)
    chapters, passages = _rows(["NBK9998", "NBK9999"])
    genesis_identity = compute_content_identity(
        chapters=chapters,
        passages=passages,
        side_mapping_ids={"NBK9998", "NBK9999"},
        source_capture=genesis_capture,
    )
    prior_manifest = _prior_release_manifest(root, genesis_identity)
    chained = _chained_capture(root, genesis_capture, prior_manifest)

    loaded = load_offline_capture(
        _write_capture(root, chained),
        archive=archive,
        side_data_dir=root,
        prior_manifest=prior_manifest,
    )

    prior = loaded["prior_artifact"]
    assert isinstance(prior, dict)
    assert set(prior) == {
        "manifest_sha256",
        "corpus_release_id",
        "app_git_sha",
        "chapter_ids",
        "chapter_count",
        "chapter_digests",
        "chapters_sha256",
        "passages_sha256",
    }
    assert prior["corpus_release_id"] == "2026-08-31-r1"
    assert prior["chapter_count"] == 2
    assert prior["manifest_sha256"] == hashlib.sha256(prior_manifest.read_bytes()).hexdigest()


def test_a_chained_capture_is_bound_to_the_exact_prior_manifest_bytes(tmp_path: Path) -> None:
    """`manifest_sha256` is the whole binding now, so it has to actually bind."""
    archive, root = _fixture_corpus(tmp_path)
    genesis_capture = _genesis_capture(archive, root)
    chapters, passages = _rows(["NBK9998", "NBK9999"])
    genesis_identity = compute_content_identity(
        chapters=chapters,
        passages=passages,
        side_mapping_ids={"NBK9998", "NBK9999"},
        source_capture=genesis_capture,
    )
    prior_manifest = _prior_release_manifest(root, genesis_identity)
    chained = _chained_capture(root, genesis_capture, prior_manifest)
    prior_manifest.write_bytes(prior_manifest.read_bytes() + b" ")

    with pytest.raises(SourceCaptureError, match="digest does not match retained bytes"):
        load_offline_capture(
            _write_capture(root, chained),
            archive=archive,
            side_data_dir=root,
            prior_manifest=prior_manifest,
        )


def test_a_chained_capture_cannot_overclaim_the_priors_logical_identity(tmp_path: Path) -> None:
    """Every logical field is re-read from the prior manifest, not taken on trust."""
    archive, root = _fixture_corpus(tmp_path)
    genesis_capture = _genesis_capture(archive, root)
    chapters, passages = _rows(["NBK9998", "NBK9999"])
    genesis_identity = compute_content_identity(
        chapters=chapters,
        passages=passages,
        side_mapping_ids={"NBK9998", "NBK9999"},
        source_capture=genesis_capture,
    )
    prior_manifest = _prior_release_manifest(root, genesis_identity)
    chained = _chained_capture(root, genesis_capture, prior_manifest)
    prior = chained["prior_artifact"]
    assert isinstance(prior, dict)
    prior["chapters_sha256"] = "d" * 64

    with pytest.raises(SourceCaptureError, match="does not prove the claimed logical identity"):
        load_offline_capture(
            _write_capture(root, chained),
            archive=archive,
            side_data_dir=root,
            prior_manifest=prior_manifest,
        )


def test_a_chained_capture_still_refuses_a_genesis_shaped_prior_claim(tmp_path: Path) -> None:
    """Genesis and chained stay mutually exclusive from both directions."""
    archive, root = _fixture_corpus(tmp_path)
    genesis_capture = _genesis_capture(archive, root)
    chapters, passages = _rows(["NBK9998", "NBK9999"])
    genesis_identity = compute_content_identity(
        chapters=chapters,
        passages=passages,
        side_mapping_ids={"NBK9998", "NBK9999"},
        source_capture=genesis_capture,
    )
    prior_manifest = _prior_release_manifest(root, genesis_identity)
    still_genesis = dict(genesis_capture)
    still_genesis["prior_artifact"] = prior_artifact_from(prior_manifest)

    with pytest.raises(SourceCaptureError, match="must not name or carry a prior artifact"):
        load_offline_capture(
            _write_capture(root, still_genesis),
            archive=archive,
            side_data_dir=root,
            prior_manifest=prior_manifest,
        )
