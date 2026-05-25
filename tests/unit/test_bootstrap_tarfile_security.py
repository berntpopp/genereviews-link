"""Security checks for corpus bootstrap tarball extraction."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from genereview_link.config import settings
from genereview_link.server_lifecycle import _bootstrap

pytestmark = pytest.mark.asyncio


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_bundle(
    path: Path,
    files: list[tuple[str, bytes]],
    *,
    checksums: dict[str, str] | None = None,
) -> Path:
    if checksums is None:
        checksums = {name: _sha256_bytes(data) for name, data in files}

    manifest = {
        "manifest_version": "1",
        "bundle_format": "tar.gz",
        "checksums": checksums,
    }
    manifest_bytes = json.dumps(manifest).encode()

    with tarfile.open(path, "w:gz") as tar:
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(manifest_bytes)
        tar.addfile(manifest_info, io.BytesIO(manifest_bytes))

        for name, data in files:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    return path


def _bundle_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _extract_dir(tmp_path: Path) -> Path:
    return tmp_path / "bootstrap" / "bundle_extract"


def _assert_no_restore(pg_restore: AsyncMock) -> None:
    pg_restore.assert_not_awaited()


def _patch_bootstrap_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bundle: Path,
) -> AsyncMock:
    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.close = AsyncMock()

    create_pool = AsyncMock(return_value=mock_pool)
    apply_control_migrations = AsyncMock(return_value=[])
    fetch_sibling_sha256 = AsyncMock(return_value=_bundle_sha256(bundle))

    async def download_with_integrity(
        _url: str,
        dest: Path,
        *,
        expected_sha256: str,
    ) -> None:
        del expected_sha256
        shutil.copyfile(bundle, dest)

    pg_restore = AsyncMock()

    import genereview_link.db.migrate as migrate
    import genereview_link.db.pool as pool
    import genereview_link.ingest.github_release as github_release

    monkeypatch.setattr(pool, "create_pool", create_pool)
    monkeypatch.setattr(migrate, "apply_control_migrations", apply_control_migrations)
    monkeypatch.setattr(github_release, "fetch_sibling_sha256", fetch_sibling_sha256)
    monkeypatch.setattr(github_release, "download_with_integrity", download_with_integrity)
    monkeypatch.setattr(github_release, "pg_restore", pg_restore)
    monkeypatch.setattr(settings, "BUNDLE_URL", "https://example.test/bundle.tar.gz")
    monkeypatch.setattr(settings, "BUNDLE_BOOTSTRAP_DIR", str(tmp_path / "bootstrap"))
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://example/test")
    monkeypatch.setattr(settings, "BUILD_LOCAL", False)

    return pg_restore


async def test_bootstrap_rejects_forged_manifest_before_extracting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(
        tmp_path / "bundle.tar.gz",
        [("corpus.dump", b"real dump")],
        checksums={"corpus.dump": _sha256_bytes(b"forged dump")},
    )

    pg_restore = _patch_bootstrap_bundle(monkeypatch, tmp_path, bundle)

    with pytest.raises(RuntimeError, match="manifest checksum mismatch"):
        await _bootstrap()

    assert not (_extract_dir(tmp_path) / "corpus.dump").exists()
    _assert_no_restore(pg_restore)


async def test_bootstrap_rejects_unexpected_member(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    corpus = b"real dump"
    bundle = _write_bundle(
        tmp_path / "bundle.tar.gz",
        [("corpus.dump", corpus), ("evil.txt", b"extra")],
        checksums={"corpus.dump": _sha256_bytes(corpus)},
    )

    pg_restore = _patch_bootstrap_bundle(monkeypatch, tmp_path, bundle)

    with pytest.raises(RuntimeError, match="unexpected tar member"):
        await _bootstrap()

    assert not (_extract_dir(tmp_path) / "corpus.dump").exists()
    _assert_no_restore(pg_restore)


async def test_bootstrap_rejects_duplicate_member(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    corpus = b"real dump"
    bundle = _write_bundle(
        tmp_path / "bundle.tar.gz",
        [("corpus.dump", corpus), ("corpus.dump", corpus)],
        checksums={"corpus.dump": _sha256_bytes(corpus)},
    )

    pg_restore = _patch_bootstrap_bundle(monkeypatch, tmp_path, bundle)

    with pytest.raises(RuntimeError, match="duplicate tar member"):
        await _bootstrap()

    assert not (_extract_dir(tmp_path) / "corpus.dump").exists()
    _assert_no_restore(pg_restore)


async def test_bootstrap_filter_data_blocks_listed_unsafe_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    corpus = b"real dump"
    unsafe_name = "../evil.txt"
    unsafe_path = tmp_path / "bootstrap" / "evil.txt"
    unsafe_data = b"unsafe"
    bundle = _write_bundle(
        tmp_path / "bundle.tar.gz",
        [("corpus.dump", corpus), (unsafe_name, unsafe_data)],
        checksums={
            "corpus.dump": _sha256_bytes(corpus),
            unsafe_name: _sha256_bytes(unsafe_data),
        },
    )

    pg_restore = _patch_bootstrap_bundle(monkeypatch, tmp_path, bundle)

    with pytest.raises(tarfile.FilterError):
        await _bootstrap()

    assert not unsafe_path.exists()
    _assert_no_restore(pg_restore)


async def test_bootstrap_handles_cpu_count_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    corpus = b"real dump"
    bundle = _write_bundle(
        tmp_path / "bundle.tar.gz",
        [("corpus.dump", corpus)],
        checksums={"corpus.dump": _sha256_bytes(corpus)},
    )
    monkeypatch.setattr("os.cpu_count", lambda: None)

    pg_restore = _patch_bootstrap_bundle(monkeypatch, tmp_path, bundle)
    await _bootstrap()

    assert pg_restore.await_args.kwargs["jobs"] == 2
