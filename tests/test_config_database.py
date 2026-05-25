"""Tests for DATABASE_URL configuration."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from genereview_link.config import Settings


def test_database_url_defaults_to_empty() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DATABASE_URL", None)
        settings = Settings()
        assert settings.DATABASE_URL == ""


def test_database_url_from_env() -> None:
    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@h:5432/db"}):
        settings = Settings()
        assert settings.DATABASE_URL == "postgresql://u:p@h:5432/db"


def test_database_pool_min_max_defaults() -> None:
    settings = Settings()
    assert settings.DATABASE_POOL_MIN_SIZE == 2
    assert settings.DATABASE_POOL_MAX_SIZE == 20


def test_new_database_tuning_fields_have_documented_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S", raising=False)
    monkeypatch.delenv("DATABASE_COMMAND_TIMEOUT_S", raising=False)
    monkeypatch.delenv("DATABASE_STATEMENT_CACHE_SIZE", raising=False)

    settings = Settings()

    assert settings.DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S == 300.0
    assert settings.DATABASE_COMMAND_TIMEOUT_S is None
    assert settings.DATABASE_STATEMENT_CACHE_SIZE == 100
