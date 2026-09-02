"""Regressions for complete offline-source admission."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

import genereview_link.corpus.pipeline as pipeline
from genereview_link.corpus.source_assets import SOURCE_ASSETS
from genereview_link.corpus.source_identity import SIDEDATA_FILES


@pytest.mark.asyncio
async def test_offline_ingest_admits_the_whole_retained_source_set_before_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every retained source file is copied into one private root before it is parsed.

    A chained capture is exactly seven files now that the prior *seal* manifest is gone;
    the property under test is unchanged -- no parser and no ingest stage may ever read
    an ambient pathname the caller can still swap out from under it.
    """
    source = tmp_path / "source"
    source.mkdir()
    contents = {name: f"owned {name}\n".encode() for name in SOURCE_ASSETS}
    for name, body in contents.items():
        (source / name).write_bytes(body)

    archive = source / "gene_NBK1116.tar.gz"
    metadata = source / "source-capture.json"
    prior = source / "prior-manifest.json"

    def validate(admitted_metadata: Path, **kwargs: object) -> dict[str, object]:
        admitted_archive = kwargs["archive"]
        admitted_side = kwargs["side_data_dir"]
        admitted_prior = kwargs["prior_manifest"]
        assert isinstance(admitted_archive, Path)
        assert isinstance(admitted_side, Path)
        assert isinstance(admitted_prior, Path)
        admitted_root = admitted_metadata.parent
        assert admitted_root != source
        assert admitted_side == admitted_root
        assert stat.S_IMODE(admitted_root.stat().st_mode) == 0o700
        assert {path.name for path in admitted_root.iterdir()} == SOURCE_ASSETS
        admitted_paths = {
            "source-capture.json": admitted_metadata,
            "file_list.csv": admitted_metadata.with_name("file_list.csv"),
            "prior-manifest.json": admitted_prior,
            "gene_NBK1116.tar.gz": admitted_archive,
            **{name: admitted_side / name for name in SIDEDATA_FILES},
        }
        assert set(admitted_paths) == SOURCE_ASSETS
        assert all(path.parent == admitted_root for path in admitted_paths.values())
        assert all(path.read_bytes() == contents[name] for name, path in admitted_paths.items())

        # Coordinated replacement of every ambient pathname after admission cannot
        # affect either later parser reads or the capture used for provenance.
        for name in SOURCE_ASSETS:
            (source / name).write_bytes(f"foreign {name}\n".encode())
        return {
            "listing": {"relpath": "gene_NBK1116.tar.gz", "last_updated": "2026-09-01"},
            "archive": {"sha256": "a" * 64},
            "side_data": {name: {} for name in SIDEDATA_FILES},
            "admitted": True,
        }

    async def ingest(_pool: object, **kwargs: object) -> str:
        admitted_archive = kwargs["tarball"]
        admitted_side = kwargs["sidedata_dir"]
        capture = kwargs["source_capture"]
        assert isinstance(admitted_archive, Path) and isinstance(admitted_side, Path)
        assert admitted_archive.read_bytes() == contents["gene_NBK1116.tar.gz"]
        assert all((admitted_side / name).read_bytes() == contents[name] for name in SIDEDATA_FILES)
        assert capture["admitted"] is True
        return "admitted-seven"

    monkeypatch.setattr(pipeline, "load_offline_capture", validate)
    monkeypatch.setattr(pipeline, "_ingest_files", ingest)

    result = await pipeline._run_full_ingest_locked(
        object(),  # type: ignore[arg-type]
        archive=archive,
        side_data_dir=source,
        source_metadata=metadata,
        prior_manifest=prior,
    )

    assert result == "admitted-seven"
