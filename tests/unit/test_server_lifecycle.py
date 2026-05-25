"""Tests for shared server lifecycle startup and teardown."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI

from genereview_link.config import ServerConfig, settings
from genereview_link.corpus.tokenizer import BGE_DIM, BGE_MODEL_NAME
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
from genereview_link.server_lifecycle import _initialize_state, _teardown_state
from genereview_link.server_manager import UnifiedServerManager


@pytest.mark.asyncio
async def test_initialize_state_with_empty_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATABASE_URL", "")
    monkeypatch.setattr(settings, "GENEREVIEW_EAGER_LOAD_BGE", False)
    monkeypatch.setattr(settings, "AUTO_PULL_RELEASES", False)

    app = FastAPI()

    await _initialize_state(app)

    assert app.state.pool is None
    assert app.state.repository is None
    assert app.state.corpus_version is None
    assert app.state.gene_index is None
    assert isinstance(app.state.embedder, FakeEmbeddingProvider)
    assert getattr(app.state, "scheduler", None) is None
    assert app.state.dense_model_id == BGE_MODEL_NAME
    assert app.state.embedding_dim == BGE_DIM

    await _teardown_state(app)


@pytest.mark.asyncio
async def test_start_stdio_server_invokes_initialize_and_teardown() -> None:
    mcp = Mock()
    mcp.run_async = AsyncMock(return_value=None)

    with (
        patch("genereview_link.server_manager._initialize_state", new_callable=AsyncMock) as init,
        patch("genereview_link.server_manager._teardown_state", new_callable=AsyncMock) as teardown,
        patch.object(
            UnifiedServerManager,
            "create_mcp_server",
            new_callable=AsyncMock,
            return_value=mcp,
        ) as create_mcp_server,
    ):
        await UnifiedServerManager().start_stdio_server(ServerConfig(transport="stdio"))

    init.assert_awaited_once()
    create_mcp_server.assert_awaited_once()
    mcp.run_async.assert_awaited_once_with(transport="stdio")
    teardown.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_stdio_server_teardown_runs_on_exception() -> None:
    mcp = Mock()
    mcp.run_async = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch("genereview_link.server_manager._initialize_state", new_callable=AsyncMock) as init,
        patch("genereview_link.server_manager._teardown_state", new_callable=AsyncMock) as teardown,
        patch.object(
            UnifiedServerManager,
            "create_mcp_server",
            new_callable=AsyncMock,
            return_value=mcp,
        ) as create_mcp_server,
        pytest.raises(RuntimeError, match="boom"),
    ):
        await UnifiedServerManager().start_stdio_server(ServerConfig(transport="stdio"))

    init.assert_awaited_once()
    create_mcp_server.assert_awaited_once()
    mcp.run_async.assert_awaited_once_with(transport="stdio")
    teardown.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_stdio_server_teardown_runs_when_initialize_fails() -> None:
    with (
        patch(
            "genereview_link.server_manager._initialize_state",
            new_callable=AsyncMock,
            side_effect=RuntimeError("init boom"),
        ) as init,
        patch("genereview_link.server_manager._teardown_state", new_callable=AsyncMock) as teardown,
        patch.object(
            UnifiedServerManager, "create_mcp_server", new_callable=AsyncMock
        ) as create_mcp,
        pytest.raises(RuntimeError, match="init boom"),
    ):
        await UnifiedServerManager().start_stdio_server(ServerConfig(transport="stdio"))

    init.assert_awaited_once()
    create_mcp.assert_not_awaited()
    teardown.assert_awaited_once()
