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


def test_repository_does_not_set_search_path_per_query() -> None:
    repository_path = Path(__file__).parents[2] / "genereview_link/retrieval/repository.py"

    assert "set search_path" not in repository_path.read_text(encoding="utf-8")
