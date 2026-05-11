"""Targeted unit tests to lift project coverage above the 70% floor.

These exercise lightweight surfaces that were previously uncovered:
client_manager (rate-limiter + ClientManager + health_check), service
errors, the github_release module, corpus/archive parsing, the MCP
prompts registry, and a couple of CLI command bodies (with the heavy
boundaries mocked).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from genereview_link.api import client_manager as cm
from genereview_link.api.client_manager import (
    DistributedRateLimiter,
    get_client_manager,
    get_managed_client,
    shutdown_clients,
)
from genereview_link.cli import app as cli_app
from genereview_link.corpus.archive import ArchiveListing, parse_file_list_row
from genereview_link.mcp import prompts as prompts_mod
from genereview_link.services.errors import NotYetIndexedError

runner = CliRunner()


# ---- DistributedRateLimiter -------------------------------------------------


class TestDistributedRateLimiter:
    @pytest.mark.asyncio
    async def test_local_no_wait_when_first_call(self) -> None:
        rl = DistributedRateLimiter(requests_per_second=1000.0)
        # First call should not block; just record timestamp.
        await rl.wait_if_needed()
        assert rl._local_last_request > 0.0

    @pytest.mark.asyncio
    async def test_local_sleeps_when_called_back_to_back(self) -> None:
        rl = DistributedRateLimiter(requests_per_second=1000.0)
        await rl.wait_if_needed()
        # Second call should still complete; duration measured by the side effect.
        await rl.wait_if_needed()

    def test_read_shared_state_returns_zero_without_path(self) -> None:
        rl = DistributedRateLimiter(requests_per_second=10.0)
        assert rl._read_shared_state() == 0.0

    def test_read_shared_state_returns_zero_when_missing(self, tmp_path: Path) -> None:
        rl = DistributedRateLimiter(
            requests_per_second=10.0,
            shared_state_file=str(tmp_path / "missing.txt"),
        )
        assert rl._read_shared_state() == 0.0

    def test_read_shared_state_round_trip(self, tmp_path: Path) -> None:
        state = tmp_path / "state.txt"
        rl = DistributedRateLimiter(requests_per_second=10.0, shared_state_file=str(state))
        rl._write_shared_state(123.456)
        assert rl._read_shared_state() == 123.456

    def test_write_shared_state_no_op_without_path(self) -> None:
        rl = DistributedRateLimiter(requests_per_second=10.0)
        # Should not raise.
        rl._write_shared_state(1.0)

    def test_read_shared_state_recovers_from_garbage(self, tmp_path: Path) -> None:
        state = tmp_path / "garbage.txt"
        state.write_text("not a number")
        rl = DistributedRateLimiter(requests_per_second=10.0, shared_state_file=str(state))
        assert rl._read_shared_state() == 0.0

    @pytest.mark.asyncio
    async def test_distributed_path_uses_shared_state(self, tmp_path: Path) -> None:
        state = tmp_path / "state.txt"
        rl = DistributedRateLimiter(requests_per_second=1000.0, shared_state_file=str(state))
        await rl.wait_if_needed()
        # State file should now contain a positive timestamp.
        assert float(state.read_text().strip()) > 0.0


# ---- ClientManager ----------------------------------------------------------


class TestClientManager:
    @pytest.mark.asyncio
    async def test_get_client_manager_returns_singleton(self) -> None:
        m1 = await get_client_manager()
        m2 = await get_client_manager()
        assert m1 is m2

    @pytest.mark.asyncio
    async def test_get_client_creates_client_lazily(self, mocker: MockerFixture) -> None:
        # Reset any cached client so we hit the lazy-init branch.
        manager = await get_client_manager()
        manager._client = None  # type: ignore[assignment]

        fake_client = MagicMock()
        fake_client.close = AsyncMock()
        mocker.patch(
            "genereview_link.api.client_manager.EutilsClient",
            return_value=fake_client,
        )
        client = await manager.get_client()
        assert client is fake_client
        # Subsequent call returns the same instance without re-creating.
        client2 = await manager.get_client()
        assert client2 is fake_client

    @pytest.mark.asyncio
    async def test_get_client_context_yields_then_keeps_open(self, mocker: MockerFixture) -> None:
        manager = await get_client_manager()
        manager._client = None  # type: ignore[assignment]
        fake_client = MagicMock()
        fake_client.close = AsyncMock()
        mocker.patch(
            "genereview_link.api.client_manager.EutilsClient",
            return_value=fake_client,
        )

        async with manager.get_client_context() as client:
            assert client is fake_client
        # Context exit must NOT close the client (the manager owns lifecycle).
        fake_client.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_close_resets_internal_client(self, mocker: MockerFixture) -> None:
        manager = await get_client_manager()
        manager._client = None  # type: ignore[assignment]
        fake_client = MagicMock()
        fake_client.close = AsyncMock()
        mocker.patch(
            "genereview_link.api.client_manager.EutilsClient",
            return_value=fake_client,
        )
        await manager.get_client()
        await manager.close()
        fake_client.close.assert_awaited_once()
        assert manager._client is None  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_close_is_safe_when_no_client(self) -> None:
        manager = await get_client_manager()
        manager._client = None  # type: ignore[assignment]
        # Should not raise.
        await manager.close()

    @pytest.mark.asyncio
    async def test_health_check_no_test_connection(self, mocker: MockerFixture) -> None:
        manager = await get_client_manager()
        manager._client = None  # type: ignore[assignment]
        fake_client = MagicMock()
        fake_client.close = AsyncMock()
        fake_client.client = MagicMock()
        fake_client.rate_limit_delay = 0.34
        fake_client.base_url = "https://eutils.example/"
        mocker.patch(
            "genereview_link.api.client_manager.EutilsClient",
            return_value=fake_client,
        )
        result = await manager.health_check(test_connection=False)
        assert result["status"] == "ready"
        assert "rate_limit_delay" in result
        assert "base_url" in result

    @pytest.mark.asyncio
    async def test_health_check_with_test_connection_success(self, mocker: MockerFixture) -> None:
        manager = await get_client_manager()
        manager._client = None  # type: ignore[assignment]
        fake_client = MagicMock()
        fake_client.close = AsyncMock()
        fake_client.rate_limit_delay = 0.34
        fake_client.base_url = "https://eutils.example/"
        # http client surface for the einfo probe
        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_client.client = MagicMock()
        fake_client.client.get = AsyncMock(return_value=fake_response)
        mocker.patch(
            "genereview_link.api.client_manager.EutilsClient",
            return_value=fake_client,
        )
        result = await manager.health_check(test_connection=True)
        assert result["status"] == "healthy"
        assert result["connection_tested"] is True
        assert "response_time_ms" in result

    @pytest.mark.asyncio
    async def test_health_check_returns_degraded_on_error(self, mocker: MockerFixture) -> None:
        manager = await get_client_manager()
        manager._client = None  # type: ignore[assignment]
        fake_client = MagicMock()
        fake_client.close = AsyncMock()
        fake_client.client = MagicMock()
        fake_client.rate_limit_delay = 0.34
        fake_client.base_url = "https://eutils.example/"
        fake_client.client.get = AsyncMock(side_effect=RuntimeError("boom"))
        mocker.patch(
            "genereview_link.api.client_manager.EutilsClient",
            return_value=fake_client,
        )
        result = await manager.health_check(test_connection=True)
        assert result["status"] == "degraded"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_managed_client_yields_eutils_client(self, mocker: MockerFixture) -> None:
        manager = await get_client_manager()
        manager._client = None  # type: ignore[assignment]
        fake_client = MagicMock()
        fake_client.close = AsyncMock()
        mocker.patch(
            "genereview_link.api.client_manager.EutilsClient",
            return_value=fake_client,
        )
        gen = get_managed_client()
        client = await gen.__anext__()
        assert client is fake_client
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    @pytest.mark.asyncio
    async def test_shutdown_clients_idempotent(self) -> None:
        # Should not raise even if no manager has been initialised.
        await shutdown_clients()
        await shutdown_clients()
        # Re-initialise so the rest of the suite continues to work.
        cm._client_manager = None  # type: ignore[assignment]
        await get_client_manager()


# ---- services/errors --------------------------------------------------------


class TestNotYetIndexedError:
    def test_carries_attributes(self) -> None:
        err = NotYetIndexedError(
            gene_symbol="BRCA1",
            nbk_id="NBK1247",
            pubmed_id="12345",
            corpus_version="2026-04-01",
        )
        assert err.gene_symbol == "BRCA1"
        assert err.nbk_id == "NBK1247"
        assert err.pubmed_id == "12345"
        assert err.corpus_version == "2026-04-01"
        assert str(err) == "not_yet_indexed"

    def test_defaults_are_none(self) -> None:
        err = NotYetIndexedError()
        assert err.gene_symbol is None
        assert err.nbk_id is None
        assert err.pubmed_id is None
        assert err.corpus_version is None


# ---- corpus/archive ---------------------------------------------------------


class TestParseFileListRow:
    def test_returns_listing_for_matching_nbk(self) -> None:
        row = (
            "books/NBK1116/genereviews.NBK1116.tar.gz,"
            "GeneReviews,University of Washington,1993,NBK1116,2026-04-01"
        )
        out = parse_file_list_row(row)
        assert isinstance(out, ArchiveListing)
        assert out.nbk_id == "NBK1116"
        assert out.relpath.endswith(".tar.gz")
        assert out.title == "GeneReviews"
        assert out.publisher == "University of Washington"
        assert out.initial_year == "1993"
        assert out.last_updated == "2026-04-01"

    def test_returns_none_for_non_matching_nbk(self) -> None:
        row = "x,y,z,1999,NBK9999,2020-01-01"
        assert parse_file_list_row(row, nbk_filter="NBK1116") is None

    def test_returns_none_for_short_row(self) -> None:
        assert parse_file_list_row("a,b,c") is None

    def test_returns_none_for_empty_row(self) -> None:
        assert parse_file_list_row("") is None


# ---- mcp/prompts ------------------------------------------------------------


class TestFindInSection:
    def test_renders_management_query(self) -> None:
        out = prompts_mod.find_in_section("BRCA1", "management")
        assert "BRCA1" in out
        assert "management" in out
        assert "search_passages" in out
        assert "_meta.attribution" in out

    def test_renders_diagnosis_query(self) -> None:
        out = prompts_mod.find_in_section("TP53", "diagnosis")
        assert "TP53" in out
        assert "diagnosis" in out

    def test_replaces_underscore_with_space_in_section_name(self) -> None:
        out = prompts_mod.find_in_section("BRCA1", "genetic_counseling")
        # Human-readable form is used in the prose body.
        assert "genetic counseling" in out
        # Canonical key (with underscore) is used inside sections=[...].
        assert "sections=['genetic_counseling']" in out

    def test_register_prompts_invokes_mcp_prompt(self) -> None:
        mcp = MagicMock()
        decorator = MagicMock(side_effect=lambda fn: fn)
        mcp.prompt.return_value = decorator
        prompts_mod.register_prompts(mcp)
        mcp.prompt.assert_called_once_with(name="find_in_section")
        decorator.assert_called_once()


# ---- CLI command bodies (mocked boundaries) --------------------------------


class TestCliDbReset:
    def test_db_reset_without_yes_exits_with_code_1(self) -> None:
        result = runner.invoke(cli_app, ["db", "reset"])
        assert result.exit_code == 1
        assert "Refusing" in result.stdout


class TestCliIngest:
    def test_ingest_dry_run_aborts_with_code_2(self, mocker: MockerFixture) -> None:
        # Patch create_pool so the inner async function reaches the dry-run guard.
        fake_pool = MagicMock()
        fake_pool.close = AsyncMock()
        mocker.patch(
            "genereview_link.db.pool.create_pool",
            AsyncMock(return_value=fake_pool),
        )
        result = runner.invoke(cli_app, ["ingest", "--dry-run"])
        assert result.exit_code == 2


class TestCliEmbed:
    def test_embed_fake_provider_runs(self, mocker: MockerFixture) -> None:
        fake_pool = MagicMock()
        fake_pool.close = AsyncMock()
        mocker.patch(
            "genereview_link.db.pool.create_pool",
            AsyncMock(return_value=fake_pool),
        )
        mocker.patch(
            "genereview_link.ingest.orchestrator.backfill_embeddings",
            AsyncMock(return_value=42),
        )
        mocker.patch(
            "genereview_link.ingest.orchestrator.build_hnsw_index",
            AsyncMock(return_value=None),
        )
        result = runner.invoke(cli_app, ["embed", "--fake"])
        assert result.exit_code == 0
        assert "embedded 42 passages" in result.stdout
        assert "HNSW index built" in result.stdout


class TestCliBundleBuild:
    def test_bundle_build_without_active_corpus_aborts(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        fake_pool = MagicMock()
        fake_pool.close = AsyncMock()
        # No active corpus row → the command should typer.Exit(1).
        fake_pool.fetchrow = AsyncMock(return_value=None)
        mocker.patch(
            "genereview_link.db.pool.create_pool",
            AsyncMock(return_value=fake_pool),
        )
        result = runner.invoke(
            cli_app, ["bundle", "build", "--output", str(tmp_path / "out.tar.gz")]
        )
        assert result.exit_code == 1
        assert "no active corpus" in result.stdout


# ---- ingest/github_release --------------------------------------------------


class TestResolveLatest:
    @pytest.mark.asyncio
    async def test_resolve_latest_picks_corpus_asset(self, mocker: MockerFixture) -> None:
        from genereview_link.ingest import github_release as gh

        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.json = MagicMock(
            return_value={
                "assets": [
                    {"name": "checksums.txt", "browser_download_url": "https://x/c"},
                    {
                        "name": "genereview-corpus-2026-04-01.tar.gz",
                        "browser_download_url": "https://x/genereview-corpus.tar.gz",
                    },
                ]
            }
        )

        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        mocker.patch.object(gh.httpx, "AsyncClient", return_value=fake_client)

        url = await gh.resolve_latest("owner/repo")
        assert url == "https://x/genereview-corpus.tar.gz"

    @pytest.mark.asyncio
    async def test_resolve_latest_raises_when_no_corpus_asset(self, mocker: MockerFixture) -> None:
        from genereview_link.ingest import github_release as gh

        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.json = MagicMock(
            return_value={"assets": [{"name": "other.zip", "browser_download_url": "x"}]}
        )

        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        mocker.patch.object(gh.httpx, "AsyncClient", return_value=fake_client)

        with pytest.raises(RuntimeError, match="no corpus bundle"):
            await gh.resolve_latest("owner/repo")

    @pytest.mark.asyncio
    async def test_fetch_sibling_sha256_returns_hex(self, mocker: MockerFixture) -> None:
        from genereview_link.ingest import github_release as gh

        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.text = "deadbeef  bundle.tar.gz\n"

        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        mocker.patch.object(gh.httpx, "AsyncClient", return_value=fake_client)

        digest = await gh.fetch_sibling_sha256("https://x/bundle.tar.gz")
        assert digest == "deadbeef"

    def test_pg_restore_invokes_subprocess(self, mocker: MockerFixture) -> None:
        from genereview_link.ingest import github_release as gh

        run_mock = mocker.patch.object(gh.subprocess, "run")
        # pg_restore is not async; call it directly.
        import asyncio

        asyncio.run(
            gh.pg_restore(
                Path("/tmp/dump"),  # noqa: S108
                database_url="postgresql://x",
                jobs=4,
            )
        )
        run_mock.assert_called_once()
        cmd = run_mock.call_args[0][0]
        assert cmd[0] == "pg_restore"
        assert "-j" in cmd
        assert "4" in cmd


# ---- db/pool ----------------------------------------------------------------


class TestCreatePool:
    @pytest.mark.asyncio
    async def test_create_pool_raises_without_database_url(self, mocker: MockerFixture) -> None:
        from genereview_link import config as cfg
        from genereview_link.db import pool as pool_mod

        new_settings = cfg.Settings(DATABASE_URL="")
        mocker.patch.object(pool_mod.config, "settings", new_settings)
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            await pool_mod.create_pool()

    @pytest.mark.asyncio
    async def test_init_conn_swallows_value_error(self) -> None:
        from genereview_link.db import pool as pool_mod

        # _init_conn must tolerate environments where the pgvector extension
        # is not installed (raises ValueError from register_vector).
        with patch(
            "genereview_link.db.pool.pgvector.asyncpg.register_vector",
            new=AsyncMock(side_effect=ValueError("vector not installed")),
        ):
            # No exception should bubble out.
            await pool_mod._init_conn(MagicMock())


# ---- corpus/sidedata smoke test (raise coverage on simple branches) --------


class TestSidedataSmoke:
    def test_sidedata_constructs_from_empty_data(self) -> None:
        # Smoke test that exercises construction paths without I/O.
        from genereview_link.corpus.sidedata import SideData

        sd = SideData(
            short_name_by_nbk={"NBK1247": "brca1"},
            gene_symbols={"NBK1247": ("BRCA1",)},
            omim_ids={"NBK1247": ("113705",)},
        )
        assert sd.short_name_by_nbk["NBK1247"] == "brca1"
        assert sd.gene_symbols["NBK1247"] == ("BRCA1",)
        assert sd.omim_ids["NBK1247"] == ("113705",)


# ---- bundle/manifest light coverage ----------------------------------------


class TestBundleManifestModel:
    def test_manifest_carries_corpus_version(self) -> None:
        from dataclasses import asdict

        from genereview_link.corpus.bundle import BundleManifest

        m = BundleManifest(
            corpus_version="2026-04-01",
            chapter_count=842,
        )
        data: dict[str, Any] = asdict(m)
        assert data["corpus_version"] == "2026-04-01"
        assert data["chapter_count"] == 842
        assert data["manifest_version"] == "1"
        assert data["bundle_format"] == "tar.gz"


# ---- Repository (asyncpg-mocked) -------------------------------------------


class _FakeAcquireCM:
    """Mimic pool.acquire(timeout=...)'s async context manager."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *args: Any) -> None:
        return None


def _fake_pool(conn: Any) -> Any:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakeAcquireCM(conn))
    return pool


class TestRepository:
    @pytest.mark.asyncio
    async def test_active_corpus_version_returns_row(self) -> None:
        from genereview_link.retrieval.repository import GeneReviewRepository

        conn = MagicMock()
        conn.fetchrow = AsyncMock(
            return_value={
                "version": "2026-04-01",
                "is_active": True,
                "ingest_status": "complete",
                "ingest_finished_at": None,
                "chapter_count": 842,
            }
        )
        repo = GeneReviewRepository(_fake_pool(conn))
        cv = await repo.active_corpus_version()
        assert cv is not None
        assert cv.version == "2026-04-01"
        assert cv.chapter_count == 842

    @pytest.mark.asyncio
    async def test_active_corpus_version_returns_none(self) -> None:
        from genereview_link.retrieval.repository import GeneReviewRepository

        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value=None)
        repo = GeneReviewRepository(_fake_pool(conn))
        assert await repo.active_corpus_version() is None

    @pytest.mark.asyncio
    async def test_active_embedding_table_returns_default_when_unset(self) -> None:
        from genereview_link.retrieval.repository import GeneReviewRepository

        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value=None)
        repo = GeneReviewRepository(_fake_pool(conn))
        assert await repo.active_embedding_table() == "genereview_embeddings_bge384"

    @pytest.mark.asyncio
    async def test_active_embedding_table_returns_configured(self) -> None:
        from genereview_link.retrieval.repository import GeneReviewRepository

        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value={"table_name": "custom_emb_table"})
        repo = GeneReviewRepository(_fake_pool(conn))
        assert await repo.active_embedding_table() == "custom_emb_table"

    @pytest.mark.asyncio
    async def test_get_passage_returns_none_when_missing(self) -> None:
        from genereview_link.retrieval.repository import GeneReviewRepository

        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)
        repo = GeneReviewRepository(_fake_pool(conn))
        assert await repo.get_passage("NBK1247:0001") is None

    @pytest.mark.asyncio
    async def test_get_passage_returns_row(self) -> None:
        from genereview_link.retrieval.repository import GeneReviewRepository

        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(
            return_value={
                "nbk_id": "NBK1247",
                "passage_id": "NBK1247:0001",
                "chapter_section": "summary",
                "heading_path": "Summary",
                "section_level": 1,
                "chunk_index": 1,
                "text": "passage text",
                "chapter_title": "BRCA1",
                "chapter_last_updated": None,
                "gene_symbols": ["BRCA1"],
            }
        )
        repo = GeneReviewRepository(_fake_pool(conn))
        row = await repo.get_passage("NBK1247:0001")
        assert row is not None
        assert row.passage_id == "NBK1247:0001"
        assert row.gene_symbols == ("BRCA1",)
        assert row.chapter_title == "BRCA1"

    @pytest.mark.asyncio
    async def test_get_section_maps_rows(self) -> None:
        from genereview_link.retrieval.repository import GeneReviewRepository

        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetch = AsyncMock(
            return_value=[
                {
                    "nbk_id": "NBK1",
                    "passage_id": "NBK1:0001",
                    "chapter_section": "management",
                    "heading_path": "Management",
                    "section_level": 1,
                    "chunk_index": 0,
                    "text": "first",
                    "chapter_title": "Chap",
                    "chapter_last_updated": None,
                    "gene_symbols": ["BRCA1"],
                },
                {
                    "nbk_id": "NBK1",
                    "passage_id": "NBK1:0002",
                    "chapter_section": "management",
                    "heading_path": "Management",
                    "section_level": 1,
                    "chunk_index": 1,
                    "text": "second",
                    "chapter_title": "Chap",
                    "chapter_last_updated": None,
                    "gene_symbols": [],
                },
            ]
        )
        repo = GeneReviewRepository(_fake_pool(conn))
        rows = await repo.get_section("NBK1", "management")
        assert len(rows) == 2
        assert rows[0].passage_id == "NBK1:0001"
        assert rows[1].passage_id == "NBK1:0002"
        assert rows[1].gene_symbols == ()

    @pytest.mark.asyncio
    async def test_get_chapter_by_gene_returns_none(self) -> None:
        from genereview_link.retrieval.repository import GeneReviewRepository

        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)
        repo = GeneReviewRepository(_fake_pool(conn))
        assert await repo.get_chapter_by_gene("BRCA1") is None

    @pytest.mark.asyncio
    async def test_get_chapter_by_nbk_round_trip(self) -> None:
        from genereview_link.retrieval.repository import GeneReviewRepository

        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(
            return_value={
                "nbk_id": "NBK1247",
                "short_name": "brca1",
                "title": "BRCA1",
                "pubmed_id": "12345",
                "gene_symbols": ["BRCA1"],
                "omim_ids": ["113705"],
                "authors": "Petrucelli et al",
                "initial_pub_date": None,
                "last_updated_date": None,
            }
        )
        repo = GeneReviewRepository(_fake_pool(conn))
        ch = await repo.get_chapter_by_nbk("NBK1247")
        assert ch is not None
        assert ch.short_name == "brca1"
        assert ch.gene_symbols == ("BRCA1",)
        assert ch.omim_ids == ("113705",)

    @pytest.mark.asyncio
    async def test_get_chapter_by_pmid_round_trip(self) -> None:
        from genereview_link.retrieval.repository import GeneReviewRepository

        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(
            return_value={
                "nbk_id": "NBK1247",
                "short_name": "brca1",
                "title": "BRCA1",
                "pubmed_id": "12345",
                "gene_symbols": ["BRCA1"],
                "omim_ids": [],
                "authors": None,
                "initial_pub_date": None,
                "last_updated_date": None,
            }
        )
        repo = GeneReviewRepository(_fake_pool(conn))
        ch = await repo.get_chapter_by_pmid("12345")
        assert ch is not None
        assert ch.pubmed_id == "12345"

    @pytest.mark.asyncio
    async def test_dense_scores_for_passages_empty_short_circuits(self) -> None:
        from genereview_link.retrieval.repository import GeneReviewRepository

        conn = MagicMock()
        repo = GeneReviewRepository(_fake_pool(conn))
        out = await repo.dense_scores_for_passages(
            [0.0] * 4, [], model_table="genereview_embeddings_bge384"
        )
        assert out == {}

    @pytest.mark.asyncio
    async def test_dense_scores_for_passages_returns_map(self) -> None:
        from genereview_link.retrieval.repository import GeneReviewRepository

        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetch = AsyncMock(
            return_value=[
                {"passage_id": "NBK1:0001", "score": 0.9},
                {"passage_id": "NBK1:0002", "score": 0.7},
            ]
        )
        repo = GeneReviewRepository(_fake_pool(conn))
        out = await repo.dense_scores_for_passages(
            [0.1] * 4,
            [("NBK1", "NBK1:0001"), ("NBK1", "NBK1:0002")],
            model_table="genereview_embeddings_bge384",
        )
        assert out == {"NBK1:0001": 0.9, "NBK1:0002": 0.7}

    @pytest.mark.asyncio
    async def test_search_passages_maps_rows(self) -> None:
        from genereview_link.retrieval.repository import GeneReviewRepository

        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetch = AsyncMock(
            return_value=[
                {
                    "nbk_id": "NBK1",
                    "passage_id": "NBK1:0001",
                    "chapter_section": "management",
                    "heading_path": "Management",
                    "section_level": 1,
                    "chunk_index": 0,
                    "text": "passage text",
                    "gene_symbols": ["BRCA1"],
                    "chapter_title": "Chap",
                    "chapter_last_updated": None,
                    "phrase_rank": 0.5,
                    "strict_rank": 0.4,
                    "recall_rank": 0.3,
                    "recall_overlap_count": 1,
                    "lexical_rank": 0.7,
                    "snippet": "**bold** snippet",
                },
            ]
        )
        repo = GeneReviewRepository(_fake_pool(conn))
        rows = await repo.search_passages(
            "BRCA1 management",
            sections=["management"],
            limit=5,
            brief=True,
        )
        assert len(rows) == 1
        assert rows[0].passage.passage_id == "NBK1:0001"
        assert rows[0].snippet == "**bold** snippet"
        assert rows[0].lexical_rank == 0.7
        assert rows[0].passage.gene_symbols == ("BRCA1",)


# ---- Debug route (gated) ---------------------------------------------------


class TestDebugRoute:
    @pytest.mark.asyncio
    async def test_debug_disabled_returns_404(self, mocker: MockerFixture) -> None:
        """When DEBUG_RANKING_ENABLED=False the /debug/ranking route 404s."""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from genereview_link.api.routes import debug as debug_routes

        # Default settings have DEBUG_RANKING_ENABLED=False.
        app = FastAPI()
        app.include_router(debug_routes.router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.get("/debug/ranking", params={"q": "BRCA1"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_debug_enabled_returns_payload(self, mocker: MockerFixture) -> None:
        """When DEBUG_RANKING_ENABLED=True the route returns ranking payload."""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from genereview_link.api.routes import debug as debug_routes
        from genereview_link.api.routes.passages import (
            get_embedding_provider,
            get_repository,
        )
        from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
        from genereview_link.retrieval.repository import (
            LexicalPassageRow,
            PassageRow,
        )

        # Flip the gate on for this test.
        mocker.patch.object(debug_routes.settings, "DEBUG_RANKING_ENABLED", True)

        repo = MagicMock()
        repo.search_passages = AsyncMock(
            return_value=[
                LexicalPassageRow(
                    passage=PassageRow(
                        nbk_id="NBK1",
                        passage_id="NBK1:0001",
                        chapter_section="summary",
                        heading_path="Summary",
                        section_level=1,
                        chunk_index=0,
                        text="text",
                        chapter_title="Chap",
                        chapter_last_updated=None,
                        gene_symbols=("BRCA1",),
                    ),
                    phrase_rank=0.5,
                    strict_rank=0.4,
                    recall_rank=0.3,
                    recall_overlap_count=1,
                    lexical_rank=0.6,
                )
            ]
        )
        repo.active_embedding_table = AsyncMock(return_value="t")
        repo.dense_scores_for_passages = AsyncMock(return_value={"NBK1:0001": 0.85})

        embedder = FakeEmbeddingProvider(dim=384)

        app = FastAPI()
        app.include_router(debug_routes.router)
        app.dependency_overrides[get_repository] = lambda: repo
        app.dependency_overrides[get_embedding_provider] = lambda: embedder

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.get("/debug/ranking", params={"q": "BRCA1", "limit": 5})
        assert resp.status_code == 200
        body = resp.json()
        assert "diagnostics" in body
        assert "passages" in body
        assert body["query"] == "BRCA1"


# ---- CLI db migrate (mocked) -----------------------------------------------


class TestCliDbMigrate:
    def test_db_migrate_reports_no_op(self, mocker: MockerFixture) -> None:
        fake_pool = MagicMock()
        fake_pool.close = AsyncMock()
        mocker.patch(
            "genereview_link.db.pool.create_pool",
            AsyncMock(return_value=fake_pool),
        )
        mocker.patch(
            "genereview_link.db.migrate.apply_control_migrations",
            AsyncMock(return_value=[]),
        )
        mocker.patch(
            "genereview_link.db.migrate.apply_data_migrations",
            AsyncMock(return_value=[]),
        )
        result = runner.invoke(cli_app, ["db", "migrate"])
        assert result.exit_code == 0
        assert "nothing to apply" in result.stdout

    def test_db_migrate_reports_applied_versions(self, mocker: MockerFixture) -> None:
        fake_pool = MagicMock()
        fake_pool.close = AsyncMock()
        mocker.patch(
            "genereview_link.db.pool.create_pool",
            AsyncMock(return_value=fake_pool),
        )
        mocker.patch(
            "genereview_link.db.migrate.apply_control_migrations",
            AsyncMock(return_value=["001_init"]),
        )
        mocker.patch(
            "genereview_link.db.migrate.apply_data_migrations",
            AsyncMock(return_value=["genereview:001_chapters"]),
        )
        result = runner.invoke(cli_app, ["db", "migrate"])
        assert result.exit_code == 0
        assert "control: 001_init" in result.stdout
        assert "data: genereview:001_chapters" in result.stdout


# ---- server_manager utility endpoints --------------------------------------


class TestServerManagerSmoke:
    def test_root_endpoint_responds(self) -> None:
        from fastapi.testclient import TestClient

        from genereview_link.config import ServerConfig
        from genereview_link.server_manager import UnifiedServerManager

        config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
        mgr = UnifiedServerManager()
        # create_fastapi_app is sync and does NOT trigger lifespan.
        app = mgr.create_fastapi_app(config)
        # TestClient sidesteps lifespan when used as a plain non-context call.
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert "message" in body

    def test_metrics_endpoint_responds(self) -> None:
        from fastapi.testclient import TestClient

        from genereview_link.config import ServerConfig
        from genereview_link.server_manager import UnifiedServerManager

        config = ServerConfig(transport="http", log_level="WARNING", enable_docs=False)
        mgr = UnifiedServerManager()
        app = mgr.create_fastapi_app(config)
        client = TestClient(app)
        resp = client.get("/metrics")
        # The route exists regardless of metrics middleware; just confirm
        # the endpoint is reachable.
        assert resp.status_code == 200


# ---- db/migrate _list_sql --------------------------------------------------


class TestMigrateListSql:
    def test_list_sql_returns_sorted_pairs(self) -> None:
        from genereview_link.db import migrate as migrate_mod
        from genereview_link.db.migrations import control as control_pkg

        pairs = migrate_mod._list_sql(control_pkg)
        # There is at least one control migration in the package.
        assert isinstance(pairs, list)
        # If any present, each entry is (version, sql_text) and versions sorted.
        if pairs:
            assert all(isinstance(v, str) and isinstance(s, str) for v, s in pairs)
            versions = [v for v, _ in pairs]
            assert versions == sorted(versions)
            assert all(not v.endswith(".sql") for v in versions)


# ---- corpus/chunking (tokenizer mocked) ------------------------------------


class TestChunking:
    def test_chunk_section_text_empty_returns_empty(self, mocker: MockerFixture) -> None:
        # Mock the tokenizer so the test does not download HF model weights.
        mocker.patch(
            "genereview_link.corpus.chunking.encode_to_token_ids",
            side_effect=lambda t: list(range(len(t.split()))),
        )
        mocker.patch(
            "genereview_link.corpus.chunking.decode_tokens",
            side_effect=lambda ids: " ".join(f"w{i}" for i in ids),
        )
        from genereview_link.corpus.chunking import chunk_section_text

        assert chunk_section_text("") == []
        assert chunk_section_text("   ") == []

    def test_chunk_section_text_short_text_one_chunk(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "genereview_link.corpus.chunking.encode_to_token_ids",
            return_value=list(range(10)),
        )
        from genereview_link.corpus.chunking import chunk_section_text

        chunks = chunk_section_text("a b c d e f g h i j", max_tokens=20)
        assert len(chunks) == 1
        assert chunks[0].chunk_index == 0
        assert chunks[0].token_count == 10

    def test_chunk_section_text_splits_into_overlapping_windows(
        self, mocker: MockerFixture
    ) -> None:
        mocker.patch(
            "genereview_link.corpus.chunking.encode_to_token_ids",
            return_value=list(range(100)),
        )
        mocker.patch(
            "genereview_link.corpus.chunking.decode_tokens",
            side_effect=lambda ids: " ".join(str(i) for i in ids),
        )
        from genereview_link.corpus.chunking import chunk_section_text

        chunks = chunk_section_text("ignored", max_tokens=40, overlap_tokens=10)
        assert len(chunks) >= 2
        assert chunks[0].chunk_index == 0
        # Strides of (max_tokens - overlap) = 30 cover the 100 token range.
        assert chunks[-1].chunk_index >= 1
        assert chunks[0].token_count == 40

    def test_chunk_section_text_invalid_overlap_raises(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "genereview_link.corpus.chunking.encode_to_token_ids",
            return_value=list(range(50)),
        )
        from genereview_link.corpus.chunking import chunk_section_text

        with pytest.raises(ValueError, match="overlap_tokens"):
            chunk_section_text("ignored", max_tokens=10, overlap_tokens=10)


# ---- corpus/archive fetch + download (httpx-mocked) ------------------------


class TestArchiveFetch:
    @pytest.mark.asyncio
    async def test_fetch_listing_finds_matching_row(self, mocker: MockerFixture) -> None:
        from genereview_link.corpus import archive

        csv_text = (
            "books/NBK1116/genereviews.NBK1116.tar.gz,GeneReviews,UW,1993,NBK1116,2026-04-01\n"
            "other.tar.gz,Other,Pub,2000,NBK9999,2024-01-01\n"
        )
        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.text = csv_text

        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        mocker.patch.object(archive.httpx, "AsyncClient", return_value=fake_client)

        listing = await archive.fetch_listing()
        assert listing.nbk_id == "NBK1116"
        assert listing.last_updated == "2026-04-01"

    @pytest.mark.asyncio
    async def test_fetch_listing_raises_when_missing(self, mocker: MockerFixture) -> None:
        from genereview_link.corpus import archive

        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.text = "other.tar.gz,Other,Pub,2000,NBK9999,2024-01-01\n"

        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        mocker.patch.object(archive.httpx, "AsyncClient", return_value=fake_client)

        with pytest.raises(RuntimeError, match="not found"):
            await archive.fetch_listing(nbk_id="NBK1116")


# ---- ingest/github_release.download_with_integrity -------------------------


class TestDownloadWithIntegrity:
    @pytest.mark.asyncio
    async def test_download_writes_file_and_validates_sha(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        import hashlib

        from genereview_link.ingest import github_release as gh

        payload = b"hello world"
        expected = hashlib.sha256(payload).hexdigest()

        async def _aiter_bytes(_size: int):
            yield payload

        stream_resp = MagicMock()
        stream_resp.raise_for_status = MagicMock()
        stream_resp.aiter_bytes = _aiter_bytes

        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_resp)
        stream_cm.__aexit__ = AsyncMock(return_value=False)

        fake_client = MagicMock()
        fake_client.stream = MagicMock(return_value=stream_cm)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        mocker.patch.object(gh.httpx, "AsyncClient", return_value=fake_client)

        dest = tmp_path / "bundle.tar.gz"
        await gh.download_with_integrity(
            "https://example.com/bundle.tar.gz", dest, expected_sha256=expected
        )
        assert dest.read_bytes() == payload

    @pytest.mark.asyncio
    async def test_download_raises_on_sha_mismatch(
        self, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        from genereview_link.ingest import github_release as gh

        async def _aiter_bytes(_size: int):
            yield b"actual"

        stream_resp = MagicMock()
        stream_resp.raise_for_status = MagicMock()
        stream_resp.aiter_bytes = _aiter_bytes

        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_resp)
        stream_cm.__aexit__ = AsyncMock(return_value=False)

        fake_client = MagicMock()
        fake_client.stream = MagicMock(return_value=stream_cm)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        mocker.patch.object(gh.httpx, "AsyncClient", return_value=fake_client)

        dest = tmp_path / "bundle.tar.gz"
        with pytest.raises(RuntimeError, match="sha256 mismatch"):
            await gh.download_with_integrity(
                "https://example.com/bundle.tar.gz",
                dest,
                expected_sha256="0" * 64,
            )
        # File must be cleaned up after a mismatch.
        assert not dest.exists()


# ---- CLI db_reset (mocked --yes path) --------------------------------------


class TestCliDbResetWithYes:
    def test_db_reset_yes_runs_drop_and_apply(self, mocker: MockerFixture) -> None:
        # Mock the asyncpg pool + acquire context manager + connection methods.
        fake_conn = MagicMock()
        fake_conn.execute = AsyncMock()
        fake_conn.fetch = AsyncMock(return_value=[])

        class _AcquireCM:
            async def __aenter__(self) -> Any:
                return fake_conn

            async def __aexit__(self, *args: Any) -> None:
                return None

        fake_pool = MagicMock()
        fake_pool.close = AsyncMock()
        fake_pool.acquire = MagicMock(return_value=_AcquireCM())

        mocker.patch(
            "genereview_link.db.pool.create_pool",
            AsyncMock(return_value=fake_pool),
        )
        mocker.patch(
            "genereview_link.db.migrate.apply_control_migrations",
            AsyncMock(return_value=[]),
        )
        mocker.patch(
            "genereview_link.db.migrate.apply_data_migrations",
            AsyncMock(return_value=[]),
        )
        result = runner.invoke(cli_app, ["db", "reset", "--yes"])
        assert result.exit_code == 0
        assert "reset complete" in result.stdout
