"""The model artifact fails closed, and the release watcher stops being a silent no-op."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from genereview_link.db.model_seed import ModelSeedError, materialize_model
from genereview_link.ingest.scheduler import (
    DECISION_CURRENT,
    DECISION_NO_CORPUS,
    DECISION_STALE,
    DECISION_UNAVAILABLE,
    release_tag_of,
)
from genereview_link.retrieval.model_identity import BGE_RUNTIME_FILES
from genereview_link.retrieval.onnx_embeddings import ModelArtifactError, verify_model_dir


def _seed_with(tmp_path: Path, contents: dict[str, bytes]) -> Path:
    seed = tmp_path / "seed"
    seed.mkdir()
    for name, payload in contents.items():
        (seed / name).write_bytes(payload)
    return seed


# --- model materialisation --------------------------------------------------------


def test_a_missing_model_seed_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ModelSeedError, match="not present"):
        materialize_model(tmp_path / "absent", tmp_path / "out")


def test_an_incomplete_model_seed_fails_closed(tmp_path: Path) -> None:
    seed = _seed_with(tmp_path, {"tokenizer.json": b"{}"})
    with pytest.raises(ModelSeedError, match=r"missing a regular model\.onnx"):
        materialize_model(seed, tmp_path / "out")


def test_a_substituted_model_never_reaches_the_volume(tmp_path: Path) -> None:
    """The decisive property: wrong bytes are refused BEFORE anything is written."""
    seed = _seed_with(tmp_path, dict.fromkeys(BGE_RUNTIME_FILES, b"not the reviewed model"))
    destination = tmp_path / "out"
    with pytest.raises(ModelSeedError, match="does not match the reviewed model identity"):
        materialize_model(seed, destination)
    assert not (destination / "model.onnx").exists()


def test_the_reviewed_model_is_staged_and_is_idempotent(tmp_path: Path) -> None:
    """Use synthetic members pinned to their own digests, so no 127 MiB download is needed."""
    payloads = {name: f"reviewed-{name}".encode() for name in BGE_RUNTIME_FILES}
    pinned = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
    seed = _seed_with(tmp_path, payloads)
    destination = tmp_path / "out"

    with patch.dict("genereview_link.db.model_seed.BGE_RUNTIME_FILES", pinned, clear=True):
        first = materialize_model(seed, destination)
        for name, data in payloads.items():
            assert (destination / name).read_bytes() == data
        assert first == pinned

        # Second run must not need the seed at all: already-correct bytes are left alone.
        second = materialize_model(tmp_path / "absent", destination)
        assert second == pinned


def test_the_serving_side_refuses_a_tampered_volume(tmp_path: Path) -> None:
    """Verified twice: the sidecar writes it, the server re-proves it before loading."""
    model_dir = _seed_with(tmp_path, dict.fromkeys(BGE_RUNTIME_FILES, b"tampered"))
    with pytest.raises(ModelArtifactError, match="does not match the reviewed model"):
        verify_model_dir(model_dir)


def test_the_serving_side_refuses_an_absent_model_dir(tmp_path: Path) -> None:
    with pytest.raises(ModelArtifactError, match="not present"):
        verify_model_dir(tmp_path / "absent")


# --- release watcher --------------------------------------------------------------


def test_release_tag_is_extracted_from_an_asset_url() -> None:
    url = (
        "https://github.com/berntpopp/genereviews-link/releases/download/"
        "corpus-data-2026-07-13-r1/corpus-bundle.tar.gz"
    )
    assert release_tag_of(url) == "corpus-data-2026-07-13-r1"
    assert release_tag_of("https://example.test/not-a-release") is None


class _FakeConn:
    """Minimal asyncpg connection double that captures the recorded row."""

    def __init__(self, active: str | None) -> None:
        self.active = active
        self.recorded: list[tuple[str, dict[str, Any]]] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "pg_try_advisory_lock" in sql:
            return True
        if "genereview_corpus_version" in sql:
            return self.active
        return None

    async def execute(self, sql: str, *args: Any) -> None:
        import json

        assert "genereview_refresh_log" in sql
        self.recorded.append((args[0], json.loads(args[2])))


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        conn = self._conn

        class _Ctx:
            async def __aenter__(self) -> _FakeConn:
                return conn

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _Ctx()


_ASSET = (
    "https://github.com/berntpopp/genereviews-link/releases/download/"
    "corpus-data-2026-09-01-r1/corpus-bundle.tar.gz"
)


async def _run_watcher(active: str | None, pinned: str, resolver: Any) -> _FakeConn:
    from genereview_link.config import settings
    from genereview_link.ingest import scheduler

    conn = _FakeConn(active)
    with (
        patch.object(settings, "CORPUS_RELEASE_TAG", pinned),
        patch.object(scheduler, "resolve_latest", resolver),
    ):
        await scheduler.check_for_new_release(_FakePool(conn))  # type: ignore[arg-type]
    return conn


@pytest.mark.asyncio
async def test_the_watcher_records_a_stale_corpus_instead_of_doing_nothing() -> None:
    """genereview_refresh_log had zero rows because nothing ever wrote to it."""

    async def resolver(_repo: str) -> str:
        return _ASSET

    conn = await _run_watcher("2026-05-12-r1", "corpus-data-2026-07-13-r1", resolver)

    assert len(conn.recorded) == 1, "the watcher must record every observation"
    decision, detail = conn.recorded[0]
    assert decision == DECISION_STALE
    assert detail["latest_release_tag"] == "corpus-data-2026-09-01-r1"
    assert detail["pinned_release_tag"] == "corpus-data-2026-07-13-r1"


@pytest.mark.asyncio
async def test_the_watcher_records_a_current_corpus() -> None:
    async def resolver(_repo: str) -> str:
        return _ASSET

    conn = await _run_watcher("2026-09-01-r1", "corpus-data-2026-09-01-r1", resolver)
    assert conn.recorded[0][0] == DECISION_CURRENT


@pytest.mark.asyncio
async def test_the_watcher_records_an_absent_corpus() -> None:
    async def resolver(_repo: str) -> str:
        return _ASSET

    conn = await _run_watcher(None, "corpus-data-2026-09-01-r1", resolver)
    assert conn.recorded[0][0] == DECISION_NO_CORPUS


@pytest.mark.asyncio
async def test_an_unreachable_upstream_is_recorded_not_swallowed() -> None:
    """ "No new releases" and "cannot see releases" must be distinguishable."""

    async def resolver(_repo: str) -> str:
        raise RuntimeError("github unreachable")

    conn = await _run_watcher("2026-05-12-r1", "corpus-data-2026-07-13-r1", resolver)
    assert conn.recorded[0][0] == DECISION_UNAVAILABLE


@pytest.mark.asyncio
async def test_auto_pull_releases_is_refused_rather_than_ignored() -> None:
    """A setting that silently does nothing is the bug; a loud refusal is the fix."""
    from fastapi import FastAPI

    from genereview_link.config import settings
    from genereview_link.server_lifecycle import _initialize_state

    with (
        patch.object(settings, "AUTO_PULL_RELEASES", True),
        patch.object(settings, "DATABASE_URL", ""),
        pytest.raises(RuntimeError, match="AUTO_PULL_RELEASES is not implemented"),
    ):
        await _initialize_state(FastAPI())
