"""Tests for corpus bundle release metadata helpers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from genereview_link.corpus.bundle_metadata import (
    asset_name_for_release,
    collect_database_facts,
    resolve_app_git_sha,
    validate_release_id,
)


@pytest.mark.parametrize("release_id", ["2026-05-12-r1", "2026-12-31-r12"])
def test_validate_release_id_accepts_corpus_release_ids(release_id: str) -> None:
    assert validate_release_id(release_id) == release_id


@pytest.mark.parametrize(
    "release_id",
    ["corpus-2026-05-12-r1", "20260512-r1", "2026-05-12", "latest"],
)
def test_validate_release_id_rejects_invalid_values(release_id: str) -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD-rN"):
        validate_release_id(release_id)


def test_asset_name_for_release_includes_model_and_database_versions() -> None:
    assert (
        asset_name_for_release(
            "2026-05-12-r1",
            model_slug="bge-small-en-v1.5",
            postgres_major="pg18",
            pgvector_version="pgv0.8.2",
        )
        == "genereview-corpus-2026-05-12-r1-bge-small-en-v1.5-pg18-pgv0.8.2.tar.gz"
    )


@pytest.mark.asyncio
async def test_collect_database_facts_binds_complete_release_provenance() -> None:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "version": "2026-08-31-r3",
            "listing_relpath": "ca/84/gene_NBK1116.tar.gz",
            "file_list_etag": "2026-08-31 02:41:04",
            "tarball_sha256": "a" * 64,
            "tarball_size_bytes": 636_755_427,
            "sidedata_title_sha256": "b" * 64,
            "sidedata_title_size_bytes": 53_028,
            "sidedata_genes_sha256": "c" * 64,
            "sidedata_genes_size_bytes": 53_750,
            "sidedata_omim_sha256": "d" * 64,
            "sidedata_omim_size_bytes": 96_486,
            "chapter_count": 891,
        }
    )
    pool.fetch = AsyncMock(
        return_value=[
            {"namespace": "control", "version": "0001_base"},
            {"namespace": "data", "version": "genereview:0001_chapters"},
        ]
    )
    pool.fetchval = AsyncMock(side_effect=[891, 41_414, 41_414, True, True, "180004", "0.8.2"])

    facts = await collect_database_facts(pool)

    assert facts is not None
    assert facts.corpus_version == "2026-08-31-r3"
    assert facts.chapter_count == 891
    assert facts.tarball_source_sha256 == "a" * 64
    assert facts.tarball_last_updated == "2026-08-31 02:41:04"
    assert facts.source == {
        "listing_relpath": "ca/84/gene_NBK1116.tar.gz",
        "last_updated": "2026-08-31 02:41:04",
        "tarball": {"sha256": "a" * 64, "size_bytes": 636_755_427},
        "side_data": {
            "GRtitle_shortname_NBKid.txt": {"sha256": "b" * 64, "size_bytes": 53_028},
            "NBKid_shortname_genesymbol.txt": {
                "sha256": "c" * 64,
                "size_bytes": 53_750,
            },
            "NBKid_shortname_OMIM.txt": {"sha256": "d" * 64, "size_bytes": 96_486},
        },
    }
    assert facts.schema_migrations == {
        "control": ["0001_base"],
        "data": ["genereview:0001_chapters"],
    }
    assert facts.hnsw_exists is True
    assert facts.postgres == {"major_version": "18", "pgvector_version": "0.8.2"}


@pytest.mark.asyncio
async def test_collect_database_facts_rejects_unknown_migration_namespace() -> None:
    pool = MagicMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "version": "2026-08-31-r3",
            "listing_relpath": "ca/84/gene_NBK1116.tar.gz",
            "file_list_etag": "etag",
            "tarball_sha256": "a" * 64,
            "tarball_size_bytes": 1,
            "sidedata_title_sha256": "b" * 64,
            "sidedata_title_size_bytes": 1,
            "sidedata_genes_sha256": "c" * 64,
            "sidedata_genes_size_bytes": 1,
            "sidedata_omim_sha256": "d" * 64,
            "sidedata_omim_size_bytes": 1,
            "chapter_count": 890,
        }
    )
    pool.fetch = AsyncMock(return_value=[{"namespace": "attacker", "version": "x"}])
    pool.fetchval = AsyncMock(side_effect=[891, 41_414, 41_414, True, True, "180004", "0.8.2"])

    with pytest.raises(ValueError, match="migration namespace"):
        await collect_database_facts(pool)


def test_resolve_app_git_sha_accepts_exact_ci_revision() -> None:
    assert resolve_app_git_sha(env={"GITHUB_ACTIONS": "true", "GITHUB_SHA": "b" * 40}) == "b" * 40


@pytest.mark.parametrize("revision", ["main", "B" * 40])
def test_resolve_app_git_sha_rejects_invalid_ci_revision(revision: str) -> None:
    with pytest.raises(ValueError, match="Git revision"):
        resolve_app_git_sha(env={"GITHUB_ACTIONS": "true", "GITHUB_SHA": revision})


def test_resolve_app_git_sha_ignores_a_stray_non_ci_environment_revision(tmp_path: Path) -> None:
    git = shutil.which("git")
    assert git is not None
    subprocess.run([git, "init", "--quiet", str(tmp_path)], check=True)  # noqa: S603
    (tmp_path / "source.txt").write_text("reviewed\n", encoding="utf-8")
    subprocess.run(  # noqa: S603
        [git, "-C", str(tmp_path), "add", "source.txt"], check=True
    )
    subprocess.run(  # noqa: S603
        [
            git,
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "test",
        ],
        check=True,
    )
    expected = subprocess.run(  # noqa: S603
        [git, "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert resolve_app_git_sha(env={"GITHUB_SHA": "b" * 40}, repo_root=tmp_path) == expected
