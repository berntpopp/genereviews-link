"""Final descriptor-admission and database-provenance regressions."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import genereview_link.corpus.computation_runs as computation_runs
import genereview_link.corpus.release_assets as release_assets
import genereview_link.corpus.source_locator as source_locator
import genereview_link.download_admission as download_admission
from genereview_link import download_guard
from genereview_link.download_guard import DownloadOwnership
from genereview_link.ingest import github_release

ROOT = Path(__file__).resolve().parents[2]


class _Response:
    def __init__(self, payload: bytes, on_exit: object | None = None) -> None:
        self.headers: dict[str, str] = {}
        self.payload = payload
        self.on_exit = on_exit

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self, _size: int):  # type: ignore[no-untyped-def]
        yield self.payload


class _Stream:
    def __init__(self, response: _Response) -> None:
        self.response = response

    async def __aenter__(self) -> _Response:
        return self.response

    async def __aexit__(self, *_args: object) -> None:
        if callable(self.response.on_exit):
            self.response.on_exit()


class _Client:
    def __init__(self, payload: bytes = b"owned", on_exit: object | None = None) -> None:
        self.response = _Response(payload, on_exit)

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def stream(self, *_args: object) -> _Stream:
        return _Stream(self.response)


@pytest.mark.asyncio
async def test_ingest_run_records_exact_postgres_server_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executions: list[tuple[str, tuple[object, ...]]] = []

    class Connection:
        async def fetchrow(self, statement: str) -> dict[str, str]:
            assert "server_version_num" in statement and "pg_extension" in statement
            return {"server_version_num": "180004", "pgvector": "0.8.2"}

        async def execute(self, statement: str, *args: object) -> str:
            executions.append((statement, args))
            return "INSERT 1"

    monkeypatch.setattr(computation_runs, "resolve_app_git_sha", lambda: "a" * 40)
    monkeypatch.setattr(
        computation_runs,
        "collect_computation_provenance",
        lambda **_kwargs: {
            "schema": "genereviews-computation-v2",
            "database": {"client_image": "pg18@sha256:fixture", "client_major": "18"},
        },
    )

    await computation_runs.record_ingest_run(
        Connection(),
        corpus_version="2026-09-01",
        source_capture={"format": "fixture"},
        expected_row_count=1,
    )

    inserted = next(args for statement, args in executions if "insert into" in statement)
    provenance = json.loads(str(inserted[3]))
    assert provenance["database"] == {
        "client_image": "pg18@sha256:fixture",
        "client_major": "18",
        "server_version_num": "180004",
        "server_major": "18",
        "pgvector": "0.8.2",
    }


def test_owned_cleanup_never_unlinks_a_path_substituted_after_ownership_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "owned"
    target.write_bytes(b"owned")
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    file_fd = os.open(target.name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent_fd)
    ownership = DownloadOwnership(parent_fd=parent_fd, file_fd=file_fd, name=target.name)
    real_unlink = os.unlink
    raced = False

    def substitute_then_unlink(path: str, *args: object, dir_fd: int | None = None) -> None:
        nonlocal raced
        if path == target.name and dir_fd is not None and not raced:
            raced = True
            real_unlink(path, dir_fd=dir_fd)
            foreign_fd = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=dir_fd,
            )
            os.write(foreign_fd, b"foreign")
            os.close(foreign_fd)
        real_unlink(path, *args, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", substitute_then_unlink)
    try:
        ownership.unlink_if_owned()
        assert not raced or target.read_bytes() == b"foreign"
    finally:
        ownership.close()


def test_owned_cleanup_never_overwrites_a_second_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "owned"
    target.write_bytes(b"owned")
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    file_fd = os.open(target.name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent_fd)
    ownership = DownloadOwnership(parent_fd=parent_fd, file_fd=file_fd, name=target.name)
    target.unlink()
    target.write_bytes(b"first foreign")
    real_rename = download_admission._rename_noreplace
    calls = 0

    def substitute_again(parent: int, source: str, destination: str) -> None:
        nonlocal calls
        calls += 1
        real_rename(parent, source, destination)
        if calls == 1:
            target.write_bytes(b"second foreign")

    monkeypatch.setattr(download_admission, "_rename_noreplace", substitute_again)
    try:
        assert ownership.unlink_if_owned() is False
        assert target.read_bytes() == b"second foreign"
        assert any(
            path.read_bytes() == b"first foreign" for path in tmp_path.iterdir() if path != target
        )
    finally:
        ownership.close()


@pytest.mark.asyncio
async def test_verified_bundle_promotion_never_consumes_a_replaced_part_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"verified bundle"
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(github_release.settings, "EXPECTED_BUNDLE_SHA256", digest)
    monkeypatch.setattr(github_release, "_download_client", lambda _url: _Client(payload))
    real_replace = os.replace

    def replace_after_substitution(source: Path, destination: Path) -> None:
        Path(source).unlink()
        Path(source).write_bytes(b"foreign bundle")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", replace_after_substitution)
    target = tmp_path / "bundle.tar.gz"

    await github_release.download_with_integrity(
        "https://github.com/owner/repo/releases/download/v1/bundle.tar.gz",
        target,
        expected_sha256=digest,
    )

    assert target.read_bytes() == payload


@pytest.mark.asyncio
async def test_stream_fails_closed_if_parent_path_is_replaced_before_admission(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "downloads"
    moved = tmp_path / "moved"
    destination.mkdir()

    def replace_parent() -> None:
        destination.rename(moved)
        destination.mkdir()

    with pytest.raises(Exception, match=r"parent|destination|admission"):
        await download_guard.stream_to_file(
            _Client(on_exit=replace_parent),  # type: ignore[arg-type]
            "https://example.test/asset",
            destination / "asset",
            max_bytes=32,
        )

    assert list(destination.iterdir()) == []


def _source_locator() -> bytes:
    return json.dumps(
        {
            "format": "genereviews-source-locator-v1",
            "assets": [
                {
                    "name": name,
                    "url": f"https://api.github.com/repos/owner/source/releases/assets/{index}",
                    "sha256": hashlib.sha256(b"owned").hexdigest(),
                    "size_bytes": 5,
                }
                for index, name in enumerate(sorted(source_locator.SOURCE_ASSETS), 1)
            ],
        }
    ).encode()


async def _mutated_same_inode_stream(
    _client: object, _url: str, target: Path, **kwargs: object
) -> str:
    target.write_bytes(b"owned")
    parent_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    file_fd = os.open(target.name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent_fd)
    output = kwargs["created_ownership"]
    assert isinstance(output, list)
    output.append(DownloadOwnership(parent_fd=parent_fd, file_fd=file_fd, name=target.name))
    os.pwrite(file_fd, b"evil!", 0)
    os.fsync(file_fd)
    return hashlib.sha256(b"owned").hexdigest()


@pytest.mark.asyncio
async def test_source_admission_rehashes_the_pinned_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "source"
    destination.mkdir()
    monkeypatch.setattr(source_locator.httpx, "AsyncClient", lambda **_kwargs: _Client())
    monkeypatch.setattr(source_locator, "stream_to_file", _mutated_same_inode_stream)

    with pytest.raises(source_locator.SourceLocatorError, match=r"bytes|digest|admission"):
        await source_locator.fetch_source_assets(
            _source_locator(),
            allowed_repositories={"owner/source"},
            destination=destination,
            token="fixture-token",  # noqa: S106 - fixture only
        )


@pytest.mark.asyncio
async def test_release_admission_rehashes_the_pinned_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identities = tuple(
        release_assets.AssetIdentity(
            asset_id=index,
            name=name,
            size=5,
            digest="sha256:" + hashlib.sha256(b"owned").hexdigest(),
            url=f"https://api.github.com/repos/owner/repo/releases/assets/{index}",
        )
        for index, name in enumerate(release_assets.PUBLICATION_ASSET_NAMES, 1)
    )

    async def identity(*_args: object, **_kwargs: object) -> release_assets.ReleaseIdentity:
        return release_assets.ReleaseIdentity(17, "corpus-data-2026-09-01-r1", "a" * 40, identities)

    destination = tmp_path / "release"
    destination.mkdir()
    monkeypatch.setattr(release_assets, "_release_assets", identity)
    monkeypatch.setattr(release_assets, "stream_to_file", _mutated_same_inode_stream)

    with pytest.raises(release_assets.ReleaseAssetError, match=r"digest|admission"):
        await release_assets.download_release_assets(
            "owner/repo", "corpus-data-2026-09-01-r1", destination, release_id=17
        )


@pytest.mark.asyncio
async def test_source_closes_every_unexpected_ownership_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "source"
    destination.mkdir()
    retained: list[DownloadOwnership] = []

    async def multiple(_client: object, _url: str, target: Path, **kwargs: object) -> str:
        target.write_bytes(b"owned")
        output = kwargs["created_ownership"]
        assert isinstance(output, list)
        for _ in range(2):
            parent_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            file_fd = os.open(target.name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent_fd)
            handle = DownloadOwnership(parent_fd=parent_fd, file_fd=file_fd, name=target.name)
            output.append(handle)
            retained.append(handle)
        return hashlib.sha256(b"owned").hexdigest()

    monkeypatch.setattr(source_locator.httpx, "AsyncClient", lambda **_kwargs: _Client())
    monkeypatch.setattr(source_locator, "stream_to_file", multiple)
    try:
        with pytest.raises(source_locator.SourceLocatorError):
            await source_locator.fetch_source_assets(
                _source_locator(),
                allowed_repositories={"owner/source"},
                destination=destination,
                token="fixture-token",  # noqa: S106 - fixture only
            )
        assert retained and all(handle.closed for handle in retained)
    finally:
        for handle in retained:
            handle.close()


def test_privileged_remote_verification_uses_exact_host_bounded_downloader() -> None:
    workflow = (ROOT / ".github/workflows/corpus-data-release.yml").read_text()
    gate = workflow.split("name: Four-state immutable publication gate", 1)[1]
    remote = gate.split("verify_remote() {", 1)[1].split("immutable_releases=", 1)[0]

    assert "download_exact_https" in remote
    assert "from genereview_link.download_admission import download_exact_https" in remote
    assert "curl --fail --silent --show-error --location" not in remote


def test_privileged_exact_downloader_import_is_stdlib_only() -> None:
    result = subprocess.run(  # noqa: S603 - fixed interpreter and inline import smoke
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import sys; "
                "sys.modules['httpx'] = None; "
                f"sys.path.insert(0, {str(ROOT)!r}); "
                "from genereview_link.download_admission import download_exact_https; "
                "assert callable(download_exact_https)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_pre_attestation_handoff_bootstrap_keeps_exact_file_descriptors_open() -> None:
    workflow = (ROOT / ".github/workflows/corpus-data-release.yml").read_text()
    bootstrap = workflow.split("name: Fetch durable digest-addressed sealed handoff", 1)[1]
    bootstrap = bootstrap.split("- name: Verify trusted build provenance", 1)[0]

    assert "os.O_TMPFILE" in bootstrap
    assert "AT_EMPTY_PATH" in bootstrap
    assert "created_identity" not in bootstrap


def test_exact_sync_download_rejects_unknown_length_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response:
        chunks = iter((b"12345", b"67890", b""))

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _size: int) -> bytes:
            return next(self.chunks)

    class Opener:
        def open(self, *_args: object, **_kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr(download_admission, "build_opener", lambda *_args: Opener())
    target = tmp_path / "remote"

    with pytest.raises(download_guard.ResponseTooLargeError):
        download_admission.download_exact_https(
            "https://api.github.com/repos/owner/repo/releases/assets/1",
            target,
            allowed_initial_hosts=frozenset({"api.github.com"}),
            allowed_redirect_hosts=frozenset({"release-assets.githubusercontent.com"}),
            expected_sha256="0" * 64,
            expected_size=8,
            max_bytes=8,
        )

    assert not target.exists()


def test_exact_sync_download_rejects_redirect_outside_exact_host_allowlist() -> None:
    handler = download_admission._ExactRedirects(
        allowed_hosts=frozenset({"release-assets.githubusercontent.com"}),
        headers={"Accept": "application/octet-stream"},
    )

    with pytest.raises(download_guard.DisallowedURLError):
        handler.redirect_request(
            None,
            None,
            302,
            "Found",
            {},
            "https://release-assets.githubusercontent.com.evil.example/object",
        )


def test_exact_sync_download_strips_authorization_on_allowed_redirect() -> None:
    handler = download_admission._ExactRedirects(
        allowed_hosts=frozenset({"release-assets.githubusercontent.com"}),
        headers={
            "Accept": "application/octet-stream",
            "Authorization": "Bearer fixture-token",
        },
    )

    redirected = handler.redirect_request(
        None,
        None,
        302,
        "Found",
        {},
        "https://release-assets.githubusercontent.com/object",
    )

    assert redirected.get_header("Authorization") is None
    assert redirected.get_header("Accept") == "application/octet-stream"
