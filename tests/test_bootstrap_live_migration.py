"""Bootstrap applies data migrations to the live schema on the hot path.

Regression guard for #43 deploy safety: a data migration (e.g.
0006_primary_gene_symbols) only reaches the live ``genereview`` schema via a
full re-ingest + atomic_swap. When an active corpus already exists, _bootstrap
returns early and never re-runs ingest, so code that SELECTs the new column
would break every search query (UndefinedColumnError) until a re-ingest.

_bootstrap must therefore idempotently bring the live schema up to date with
data migrations before returning on the active-corpus hot path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import genereview_link.corpus.pipeline as pipeline
import genereview_link.db.migrate as migrate
import genereview_link.db.pool as pool_mod
from genereview_link.config import settings
from genereview_link.server_lifecycle import _bootstrap

pytestmark = pytest.mark.asyncio


async def test_bootstrap_applies_data_migrations_to_live_schema_when_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Active corpus -> data migrations run against 'genereview' before return."""
    mock_pool = AsyncMock()
    # fetchval(... is_active) returns truthy => active corpus / hot path.
    mock_pool.fetchval = AsyncMock(return_value=1)
    mock_pool.close = AsyncMock()

    create_pool = AsyncMock(return_value=mock_pool)
    apply_control_migrations = AsyncMock(return_value=[])
    apply_data_migrations = AsyncMock(return_value=["genereview:0006"])

    monkeypatch.setattr(pool_mod, "create_pool", create_pool)
    monkeypatch.setattr(migrate, "apply_control_migrations", apply_control_migrations)
    monkeypatch.setattr(migrate, "apply_data_migrations", apply_data_migrations)
    # No bundle / build-local work should be reached on the active hot path.
    monkeypatch.setattr(settings, "BUNDLE_URL", "")
    monkeypatch.setattr(settings, "BUILD_LOCAL", False)
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://example/test")

    await _bootstrap()

    # The data migrations must be applied to the live schema on the hot path,
    # and must target the literal 'genereview' schema (per atomic_swap).
    apply_data_migrations.assert_awaited_once_with(mock_pool, schema="genereview")
    # The pool is always closed in the finally block.
    mock_pool.close.assert_awaited_once()


async def test_bootstrap_skips_data_migrations_when_no_active_corpus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh deploy (no active corpus) must NOT run apply_data_migrations here.

    That path builds 'genereview' fresh via bundle restore / ingest which
    already includes the latest data migration, so re-running it on the live
    schema here would be redundant. With BUNDLE_URL/BUILD_LOCAL both unset, the
    function falls through to the MODE 3 warning and returns.
    """
    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=None)  # no active corpus
    mock_pool.close = AsyncMock()

    create_pool = AsyncMock(return_value=mock_pool)
    apply_control_migrations = AsyncMock(return_value=[])
    apply_data_migrations = AsyncMock(return_value=[])

    monkeypatch.setattr(pool_mod, "create_pool", create_pool)
    monkeypatch.setattr(migrate, "apply_control_migrations", apply_control_migrations)
    monkeypatch.setattr(migrate, "apply_data_migrations", apply_data_migrations)
    monkeypatch.setattr(settings, "BUNDLE_URL", "")
    monkeypatch.setattr(settings, "BUILD_LOCAL", False)
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://example/test")

    await _bootstrap()

    apply_data_migrations.assert_not_awaited()
    mock_pool.close.assert_awaited_once()


async def test_bootstrap_says_build_local_is_inert_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUILD_LOCAL named a boot-time live ingest that ingest no longer has.

    It used to reach ``run_full_ingest(pool)`` with no source set at all, which
    raised a ValueError whose text never mentioned the flag -- and which the
    bootstrap's ``asyncpg.PostgresError`` handler did not catch, so the server
    died on startup. It must now say so plainly and degrade like an empty
    database, pointing at the maintainer flow that does work.
    """
    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=None)  # no active corpus
    mock_pool.close = AsyncMock()
    monkeypatch.setattr(pool_mod, "create_pool", AsyncMock(return_value=mock_pool))
    monkeypatch.setattr(migrate, "apply_control_migrations", AsyncMock(return_value=[]))
    apply_data_migrations = AsyncMock(return_value=[])
    monkeypatch.setattr(migrate, "apply_data_migrations", apply_data_migrations)
    monkeypatch.setattr(settings, "BUILD_LOCAL", True)
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://example/test")

    ingest = AsyncMock()
    monkeypatch.setattr(pipeline, "run_full_ingest", ingest)

    await _bootstrap()

    ingest.assert_not_awaited()
    apply_data_migrations.assert_not_awaited()
    mock_pool.close.assert_awaited_once()
