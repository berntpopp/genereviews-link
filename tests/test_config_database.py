"""Tests for DATABASE_URL configuration."""

from __future__ import annotations

import os
from unittest.mock import patch

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
    assert settings.DATABASE_POOL_MAX_SIZE == 10
