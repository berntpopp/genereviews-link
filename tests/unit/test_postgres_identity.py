from unittest.mock import AsyncMock

import pytest

from genereview_link.corpus.pg_client import (
    PG18_IMAGE,
    is_supported_pg_client_image,
)
from genereview_link.corpus.postgres_identity import (
    is_supported_pgvector_restore,
    is_supported_pgvector_runtime,
    require_database_pgvector_restore,
    require_supported_pgvector_restore,
)

OLD_CLIENT_IMAGE = (
    "pgvector/pgvector:0.8.2-pg18@"
    "sha256:42e7f6b4e1eceb02ff14e3e6bc6108bbe259abbe83879dc1845d0da1ddeb555d"
)


@pytest.mark.parametrize("image", [PG18_IMAGE, OLD_CLIENT_IMAGE])
def test_only_current_and_exact_historical_client_images_are_supported(image: str) -> None:
    assert is_supported_pg_client_image(image)


def test_unpinned_or_unreviewed_client_images_are_rejected() -> None:
    assert not is_supported_pg_client_image("pgvector/pgvector:0.8.6-pg18")
    assert not is_supported_pg_client_image("pgvector/pgvector:0.8.2-pg18@sha256:" + "0" * 64)


@pytest.mark.parametrize("version", ["0.8.2", "0.8.6"])
def test_only_reviewed_postgres_extension_runtime_versions_are_supported(version: str) -> None:
    assert is_supported_pgvector_runtime(version)


def test_cold_restore_contract_is_directional_and_closed_world() -> None:
    assert is_supported_pgvector_restore("0.8.2", "0.8.2")
    assert is_supported_pgvector_restore("0.8.2", "0.8.6")
    assert is_supported_pgvector_restore("0.8.6", "0.8.6")
    assert not is_supported_pgvector_restore("0.8.6", "0.8.2")
    assert not is_supported_pgvector_restore("0.8.2", "0.8.7")


def test_unsupported_restore_pair_raises_before_caller_can_publish_identity() -> None:
    with pytest.raises(ValueError, match=r"0\.8\.6.*0\.8\.2"):
        require_supported_pgvector_restore("0.8.6", "0.8.2")


@pytest.mark.asyncio
async def test_active_volume_guard_refuses_before_runtime_identity_is_recorded() -> None:
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value="0.8.2")

    with pytest.raises(ValueError, match=r"0\.8\.6.*0\.8\.2"):
        await require_database_pgvector_restore(pool, "0.8.6")

    pool.fetchval.assert_awaited_once_with(
        "select extversion from pg_extension where extname = 'vector'"
    )
