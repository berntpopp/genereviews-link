"""Regressions for final ingest and publication review findings."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import genereview_link.corpus.pipeline as pipeline
from genereview_link.corpus.source_identity import SIDEDATA_FILES

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_offline_ingest_parses_only_admitted_private_source_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "gene_NBK1116.tar.gz"
    archive.write_bytes(b"owned archive")
    side_data = tmp_path / "side"
    side_data.mkdir()
    for name in SIDEDATA_FILES:
        (side_data / name).write_bytes(f"owned {name}".encode())
    metadata = tmp_path / "source-capture.json"
    listing = tmp_path / "file_list.csv"
    prior = tmp_path / "prior-manifest.json"
    for path in (metadata, listing, prior):
        path.write_bytes(b"fixture")

    def validate(*_args: object, **kwargs: object) -> dict[str, object]:
        admitted_archive = kwargs["archive"]
        admitted_side = kwargs["side_data_dir"]
        assert isinstance(admitted_archive, Path) and isinstance(admitted_side, Path)
        assert admitted_archive.read_bytes() == b"owned archive"
        for name in SIDEDATA_FILES:
            assert (admitted_side / name).read_bytes() == f"owned {name}".encode()
        archive.write_bytes(b"foreign archive")
        for name in SIDEDATA_FILES:
            (side_data / name).write_bytes(f"foreign {name}".encode())
        return {
            "listing": {"relpath": "gene_NBK1116.tar.gz", "last_updated": "2026-09-01"},
            "archive": {"sha256": "a" * 64},
            "side_data": {name: {} for name in SIDEDATA_FILES},
        }

    async def ingest(_pool: object, **kwargs: object) -> str:
        admitted_archive = kwargs["tarball"]
        admitted_side = kwargs["sidedata_dir"]
        assert isinstance(admitted_archive, Path) and isinstance(admitted_side, Path)
        assert admitted_archive != archive and admitted_side != side_data
        assert admitted_archive.read_bytes() == b"owned archive"
        for name in SIDEDATA_FILES:
            assert (admitted_side / name).read_bytes() == f"owned {name}".encode()
        return "admitted"

    monkeypatch.setattr(pipeline, "load_offline_capture", validate)
    monkeypatch.setattr(pipeline, "_ingest_files", ingest)

    result = await pipeline._run_full_ingest_locked(
        object(),  # type: ignore[arg-type]
        archive=archive,
        side_data_dir=side_data,
        source_metadata=metadata,
        prior_manifest=prior,
    )

    assert result == "admitted"


def test_verifier_is_main_only_and_holds_no_privileged_permission() -> None:
    """The only remaining corpus workflow reads; it never signs, attests or publishes.

    Publication now happens on the maintainer's workstation with ``gh release create``,
    so nothing in CI needs ``id-token``, ``attestations`` or write access to contents.
    """
    verifier = yaml.safe_load((ROOT / ".github/workflows/verify-corpus-bundle.yml").read_text())
    verify = verifier["jobs"]["verify"]

    assert verify["if"] == "${{ github.ref == 'refs/heads/main' }}"
    assert verify["steps"][0]["uses"].startswith("actions/checkout@")
    assert verifier["permissions"] == {"contents": "read"}
    assert "id-token" not in verifier["permissions"]
    assert "attestations" not in verifier["permissions"]
    assert "permissions" not in verify
