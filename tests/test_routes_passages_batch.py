"""Tests for POST /passages/batch route (Task 8 — Spec F1)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from genereview_link.api.routes import passages as passages_routes
from genereview_link.retrieval.repository import PassageRow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    *,
    passage_id: str = "NBK1247:0022",
    nbk_id: str = "NBK1247",
    chapter_section: str = "management",
    heading_path: str = "Management > Other",
    section_level: int = 2,
    chunk_index: int = 22,
    text: str = "risk-reducing surgery text",
    chapter_title: str = "BRCA1- and BRCA2-Associated HBOC",
    chapter_last_updated: date = date(2025, 12, 1),
    gene_symbols: tuple[str, ...] = ("BRCA1", "BRCA2"),
) -> PassageRow:
    return PassageRow(
        nbk_id=nbk_id,
        passage_id=passage_id,
        chapter_section=chapter_section,
        heading_path=heading_path,
        section_level=section_level,
        chunk_index=chunk_index,
        text=text,
        chapter_title=chapter_title,
        chapter_last_updated=chapter_last_updated,
        gene_symbols=gene_symbols,
    )


def _build_app(rows: dict[str, PassageRow | None]) -> FastAPI:
    """Build a minimal FastAPI app whose fake repo serves a dict of passage_id -> row."""
    app = FastAPI()
    app.include_router(passages_routes.router)

    # Build a fake connection whose _fetch_passage_row honours the lookup dict.
    fake_conn = MagicMock()
    fake_conn.execute = AsyncMock()

    async def _fetch_passage_row(conn: Any, passage_id: str) -> PassageRow | None:
        return rows.get(passage_id)

    # Build a fake repo that provides _acquire as an async context manager
    # returning fake_conn, and _fetch_passage_row as a coroutine.
    repo = MagicMock(spec_set=["_acquire", "_fetch_passage_row"])
    repo._fetch_passage_row = _fetch_passage_row  # plain async function

    @asynccontextmanager
    async def _acquire():  # type: ignore[override]
        yield fake_conn

    repo._acquire = _acquire

    app.state.repository = repo
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_200_returns_all_found() -> None:
    """Happy path: all requested ids are found and returned in order."""
    row_a = _make_row(passage_id="NBK1247:0001", chunk_index=1, text="passage one")
    row_b = _make_row(passage_id="NBK1247:0002", chunk_index=2, text="passage two")
    app = _build_app({"NBK1247:0001": row_a, "NBK1247:0002": row_b})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/passages/batch", json={"ids": ["NBK1247:0001", "NBK1247:0002"]})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "passages" in body
    assert "missing_ids" in body
    assert "_meta" in body
    assert body["missing_ids"] == []
    assert len(body["passages"]) == 2
    # Order must match request order
    assert body["passages"][0]["passage_id"] == "NBK1247:0001"
    assert body["passages"][1]["passage_id"] == "NBK1247:0002"
    # Spot-check fields
    assert body["passages"][0]["text"] == "passage one"
    assert body["passages"][0]["char_count"] == len("passage one")
    assert body["passages"][0]["gene_symbols"] == ["BRCA1", "BRCA2"]


@pytest.mark.asyncio
async def test_batch_200_with_partial_misses() -> None:
    """Partial misses: found passages returned, missing ids listed."""
    row_a = _make_row(passage_id="NBK1247:0001", chunk_index=1, text="found passage")
    # NBK1247:0099 is not in the store
    app = _build_app({"NBK1247:0001": row_a, "NBK1247:0099": None})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/passages/batch",
            json={"ids": ["NBK1247:0001", "NBK1247:0099"]},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["passages"]) == 1
    assert body["passages"][0]["passage_id"] == "NBK1247:0001"
    assert body["missing_ids"] == ["NBK1247:0099"]


@pytest.mark.asyncio
async def test_batch_422_on_empty_ids_list() -> None:
    """Empty ids list is rejected with 422 (Pydantic min_length=1 constraint)."""
    app = _build_app({})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/passages/batch", json={"ids": []})

    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_batch_422_on_invalid_id_format() -> None:
    """Ids that do not match ^NBK\\d+:\\d{4}$ are rejected with 422."""
    app = _build_app({})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/passages/batch", json={"ids": ["not-a-valid-id"]})

    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_batch_413_on_oversize() -> None:
    """More than 20 ids returns 413 with code='batch_size_exceeded'."""
    # 21 syntactically valid ids — none need to exist in the store
    ids = [f"NBK1247:{i:04d}" for i in range(21)]
    app = _build_app({})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/passages/batch", json={"ids": ids})

    assert resp.status_code == 413, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "batch_size_exceeded"
    assert "recovery_hint" in detail
    assert "next_commands" in detail
    cmds = detail["next_commands"]
    assert len(cmds) == 1
    assert cmds[0]["tool"] == "get_passages_batch"
    # next_commands carries the first 20 ids
    assert len(cmds[0]["arguments"]["ids"]) == 20


@pytest.mark.asyncio
async def test_batch_200_order_preserved_for_all_found() -> None:
    """Response passages appear in the same order as the request ids."""
    rows = {
        f"NBK1247:{i:04d}": _make_row(passage_id=f"NBK1247:{i:04d}", chunk_index=i)
        for i in range(5)
    }
    ids = [f"NBK1247:{i:04d}" for i in range(4, -1, -1)]  # reverse order: 4,3,2,1,0
    app = _build_app(rows)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/passages/batch", json={"ids": ids})

    assert resp.status_code == 200, resp.text
    returned_ids = [p["passage_id"] for p in resp.json()["passages"]]
    assert returned_ids == ids


@pytest.mark.asyncio
async def test_batch_200_meta_carries_attribution() -> None:
    """_meta.attribution is always present in the batch response."""
    row = _make_row(passage_id="NBK1247:0001", chunk_index=1)
    app = _build_app({"NBK1247:0001": row})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/passages/batch", json={"ids": ["NBK1247:0001"]})

    assert resp.status_code == 200, resp.text
    meta = resp.json()["_meta"]
    assert "attribution" in meta
    assert meta["attribution"].startswith("GeneReviews")


@pytest.mark.asyncio
async def test_batch_200_corpus_version_from_app_state() -> None:
    """_meta.corpus_version is wired through from app.state.corpus_version."""
    row = _make_row(passage_id="NBK1247:0001", chunk_index=1)
    app = _build_app({"NBK1247:0001": row})
    app.state.corpus_version = "2026-01-15"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/passages/batch", json={"ids": ["NBK1247:0001"]})

    assert resp.status_code == 200, resp.text
    assert resp.json()["_meta"]["corpus_version"] == "2026-01-15"


# ---------------------------------------------------------------------------
# heading_path_array opt-in tests (Task 11 — Spec H1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_heading_path_array_absent_by_default() -> None:
    """heading_path_array is absent from batch results unless include=['heading_path_array']."""
    row = _make_row(passage_id="NBK1247:0001", chunk_index=1, heading_path="A > B > C")
    app = _build_app({"NBK1247:0001": row})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/passages/batch", json={"ids": ["NBK1247:0001"]})

    assert resp.status_code == 200
    assert resp.json()["passages"][0].get("heading_path_array") is None


@pytest.mark.asyncio
async def test_batch_heading_path_array_opt_in() -> None:
    """include=['heading_path_array'] splits heading_path on ' > ' for each passage."""
    row = _make_row(passage_id="NBK1247:0001", chunk_index=1, heading_path="A > B > C")
    app = _build_app({"NBK1247:0001": row})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/passages/batch",
            json={"ids": ["NBK1247:0001"], "include": ["heading_path_array"]},
        )

    assert resp.status_code == 200
    assert resp.json()["passages"][0]["heading_path_array"] == ["A", "B", "C"]
