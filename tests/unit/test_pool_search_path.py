from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from genereview_link import config as config_mod
from genereview_link.db import pool as pool_mod


@pytest.mark.asyncio
async def test_create_pool_configures_search_path_server_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    sentinel = object()

    async def fake_create_pool(**kwargs: Any) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(config_mod.settings, "DATABASE_URL", "postgresql://test/test")
    monkeypatch.setattr(pool_mod.asyncpg, "create_pool", fake_create_pool)

    result = await pool_mod.create_pool()

    assert result is sentinel
    assert captured["server_settings"] == {"search_path": "genereview, public"}


@pytest.mark.asyncio
async def test_create_pool_passes_tuning_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    sentinel = object()

    async def fake_create_pool(**kwargs: Any) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    monkeypatch.setenv("DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S", "120.0")
    monkeypatch.setenv("DATABASE_COMMAND_TIMEOUT_S", "30.0")
    monkeypatch.setenv("DATABASE_STATEMENT_CACHE_SIZE", "0")
    monkeypatch.setattr(config_mod, "settings", config_mod.Settings())
    monkeypatch.setattr(pool_mod.asyncpg, "create_pool", fake_create_pool)

    result = await pool_mod.create_pool()

    assert result is sentinel
    assert {
        "max_inactive_connection_lifetime": captured["max_inactive_connection_lifetime"],
        "command_timeout": captured["command_timeout"],
        "statement_cache_size": captured["statement_cache_size"],
    } == {
        "max_inactive_connection_lifetime": 120.0,
        "command_timeout": 30.0,
        "statement_cache_size": 0,
    }


def test_default_pool_max_size_is_20(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_POOL_MAX_SIZE", raising=False)

    settings = config_mod.Settings(_env_file=None)

    assert settings.DATABASE_POOL_MAX_SIZE == 20


def test_repository_does_not_set_search_path_per_query() -> None:
    repository_path = Path(__file__).parents[2] / "genereview_link/retrieval/repository.py"

    assert "set search_path" not in repository_path.read_text(encoding="utf-8")
