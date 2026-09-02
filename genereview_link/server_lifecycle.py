"""Application lifecycle helpers for the GeneReview Link server."""

from typing import Any

import asyncpg
from fastapi import FastAPI

from genereview_link.api.client_manager import get_client_manager, shutdown_clients
from genereview_link.config import settings
from genereview_link.logging_config import get_logger
from genereview_link.runtime_data_identity import (
    RuntimeDataIdentityError,
    configured_data_identity,
    observed_data_identity,
    release_identity_payload,
)
from genereview_link.services.service_manager import get_service_manager, shutdown_services

logger = get_logger("server.manager")


async def _bootstrap() -> None:
    """Bring the live schema up to date; never load corpus data.

    The serving application has NO restore path. Corpus data enters PostgreSQL only via
    the no-egress `genereview-corpus-restore` init sidecar (`genereview-link corpus
    restore`), which verifies an immutable, digest-pinned, data-only artifact and loads it
    atomically as an unprivileged role. Downloading and restoring a bundle from inside the
    request-serving process would give it exactly the egress and the database rights the
    restored-database contract exists to deny it.

    One mode remains here: the corpus is already present (restored by the init sidecar),
    or the database is empty and `/passages/search` degrades to 503 until it is loaded.
    `BUILD_LOCAL=true` is inert -- ingest has no boot-time live-fetch path; a corpus is
    built by a maintainer with `snapshot` + `ingest` (docs/data.md).
    """
    from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
    from genereview_link.db.pool import create_pool

    pool = await create_pool()
    try:
        applied = await apply_control_migrations(pool)
        if applied:
            logger.info("applied control migrations", versions=applied)

        active = await pool.fetchval(
            "select 1 from public.genereview_corpus_version where is_active"
        )
        if active:
            # Ensure the live schema has any data-migration columns added since
            # the last ingest (e.g. primary_gene_symbols / #43). Data migrations
            # only reach the live 'genereview' schema via a full re-ingest +
            # atomic_swap, so deploying code that SELECTs a new column against a
            # corpus ingested before that migration would otherwise break every
            # search query (UndefinedColumnError). apply_data_migrations is
            # idempotent and keyed by public.schema_migrations: atomic_swap
            # records genereview:0001..NNNN, so only unapplied versions run here
            # (just the new column, added with its default). The ranker boost
            # stays inactive until a re-ingest populates the values.
            applied_data = await apply_data_migrations(pool, schema="genereview")
            if applied_data:
                logger.info("applied data migrations to live schema", versions=applied_data)
            logger.info("active corpus found; skipping bootstrap")
            return  # hot path / already-populated by the restore sidecar

        if settings.BUILD_LOCAL:
            # BUILD_LOCAL named a boot-time live ingest that no longer exists:
            # ingest consumes only a retained offline source set, so this branch
            # could only ever raise. Say so plainly and degrade the same way an
            # empty database does, rather than crashing startup on a ValueError
            # whose text says nothing about the flag that caused it.
            logger.error(
                "BUILD_LOCAL=true is inert: ingest has no boot-time live-fetch path. "
                "Build a corpus with `genereview-link snapshot` then "
                "`genereview-link ingest --genesis` (see docs/data.md), or let the "
                "genereview-corpus-restore sidecar restore a reviewed artifact"
            )

        # No active corpus: the restore sidecar has not run (or the database is external
        # and empty). The server still starts and serves definitions; corpus-backed routes
        # degrade to 503 rather than answering from an empty database.
        logger.warning(
            "no active corpus; /passages/search will return 503 until the "
            "genereview-corpus-restore sidecar loads the reviewed corpus artifact"
        )
    except asyncpg.PostgresError as exc:
        logger.warning("bootstrap failed; server will start without corpus", error=str(exc))
    finally:
        await pool.close()


async def _initialize_state(app: FastAPI) -> None:
    """Initialize shared application state for request serving."""
    from genereview_link import __version__

    logger.info(
        "Starting GeneReview Link Server",
        version=__version__,
        environment=settings.ENVIRONMENT,
    )

    # --- Corpus bootstrap (bundle / build-local / external) ---
    if settings.DATABASE_URL:
        await _bootstrap()

    client_manager = await get_client_manager()
    service_manager = await get_service_manager()
    await client_manager.get_client()  # Initialize client
    await service_manager.get_service()  # Initialize service
    logger.info("Client and Service managers initialized.")

    # --- Postgres pool + repository (graceful degradation when DATABASE_URL is empty) ---
    pool = None
    if settings.DATABASE_URL:
        try:
            from genereview_link.db.pool import create_pool
            from genereview_link.retrieval.repository import GeneReviewRepository

            # Use the shared pool factory so the pgvector codec gets
            # registered on every connection - required for dense vector
            # queries (e.g. /passages/search?rerank=rrf).
            pool = await create_pool()
            app.state.pool = pool
            app.state.repository = GeneReviewRepository(pool)
            logger.info("Postgres pool and repository initialised.")
        except Exception as exc:
            logger.warning("Failed to create Postgres pool; /passages/* will 503.", error=str(exc))
            app.state.pool = None
            app.state.repository = None
    else:
        logger.info("DATABASE_URL not set; skipping Postgres pool (repository unavailable).")
        app.state.pool = None
        app.state.repository = None

    # --- Active corpus version (cached for _meta.corpus_version and /health.corpus) ---
    app.state.corpus_version = None
    # corpus_data_as_of: ingest_finished_at of the active corpus, restored verbatim from
    # the release bundle (see genereview_link/corpus/freshness.py). Exposed on /health so
    # a frozen corpus is a visible fact instead of a silent "healthy" (#145).
    app.state.corpus_data_as_of = None
    if app.state.repository is not None:
        try:
            cv = await app.state.repository.active_corpus_version()
            app.state.corpus_version = cv.version if cv is not None else None
            app.state.corpus_data_as_of = (
                cv.ingest_finished_at.isoformat()
                if cv is not None and cv.ingest_finished_at is not None
                else None
            )
            logger.info(
                "Active corpus version cached on app.state",
                corpus_version=app.state.corpus_version,
                corpus_data_as_of=app.state.corpus_data_as_of,
            )
        except Exception as exc:
            logger.warning(
                "Failed to read active corpus version; _meta will omit it.",
                error=str(exc),
            )

    # --- Runtime data identity (cached for /health.release_identity) ---
    # The fleet controller can only activate a NEW data release for a service that proves,
    # at runtime, which reviewed release it is serving. `expected` is what this deployment
    # is configured for; `actual` is re-derived from the restore's own record plus the rows
    # actually present. Absence is reported, never guessed.
    app.state.release_identity = release_identity_payload(None, None)
    app.state.data_available = False
    if app.state.pool is not None:
        expected: dict[str, str] | None = None
        actual: dict[str, str] | None = None
        try:
            expected = configured_data_identity(settings)
        except RuntimeDataIdentityError as exc:
            logger.warning("no configured data release identity", error=str(exc))
        try:
            actual = await observed_data_identity(app.state.pool)
        except (RuntimeDataIdentityError, asyncpg.PostgresError) as exc:
            logger.warning("no runtime data identity for the restored corpus", error=str(exc))
        app.state.release_identity = release_identity_payload(expected, actual)
        app.state.data_available = expected is not None and actual == expected
        logger.info(
            "runtime data identity resolved",
            data_available=app.state.data_available,
            expected=expected,
            actual=actual,
        )

    # --- Gene symbol index (cached for fuzzy alias suggestions) ---
    app.state.gene_index = None
    if app.state.pool is not None:
        try:
            from genereview_link.services.gene_index import load_gene_index

            app.state.gene_index = await load_gene_index(app.state.pool)
            logger.info(
                "loaded gene_index",
                count=len(app.state.gene_index.symbols),
            )
        except Exception as exc:
            logger.warning("gene_index load failed", error=str(exc))

    # --- Embedding provider (fail-closed; see retrieval/provider_policy.py) ---
    await _initialize_embeddings(app)

    # --- Release watcher scheduler ---
    if settings.AUTO_PULL_RELEASES:
        raise RuntimeError(
            "AUTO_PULL_RELEASES is not implemented and never was: the branch behind it was "
            "a bare `pass`, so it silently did nothing. The serving process has no restore "
            "path by design (#97). Unset it, and set RELEASE_WATCHER_ENABLED=true to record "
            "corpus staleness into public.genereview_refresh_log instead."
        )
    app.state.scheduler = None
    if settings.RELEASE_WATCHER_ENABLED and pool is not None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from genereview_link.ingest.scheduler import check_for_new_release

        app.state.scheduler = AsyncIOScheduler()
        app.state.scheduler.add_job(check_for_new_release, "cron", minute=17, args=[pool])
        app.state.scheduler.start()
        logger.info("Corpus staleness watcher started (fires at :17 each hour).")


async def _initialize_embeddings(app: FastAPI) -> None:
    """Select the dense embedding provider and record what actually got loaded.

    Raises:
        EmbeddingPolicyError: a stub provider is configured for production without an
            explicit opt-in, or the loaded model disagrees with the corpus's.
    """
    from genereview_link.retrieval.provider_policy import (
        assert_corpus_model_agreement,
        build_embedding_provider,
        provider_is_real,
    )

    provider, kind = await build_embedding_provider(settings)
    real = provider_is_real(provider)
    await _assert_corpus_model(app, provider, assert_corpus_model_agreement)

    app.state.embedder = provider
    app.state.embedding_provider_kind = kind
    app.state.embedding_provider_real = real
    # A stub's query vector is uncorrelated with the stored corpus vectors, so fusing it
    # displaces correct lexical hits instead of improving them. Disable the dense path
    # rather than serve a ranking that is worse than no ranking.
    app.state.dense_ranking_enabled = real
    # Report the model that is actually loaded. Reporting the reference model while a
    # stub answers queries is misinformation, not a cosmetic defect.
    app.state.dense_model_id = provider.model_name
    app.state.embedding_dim = provider.dim

    if real:
        logger.info("dense embedding provider loaded", model=provider.model_name, kind=kind)
    else:
        logger.error(
            "DEGRADED: stub embedding provider active; dense ranking is disabled and "
            "search falls back to lexical ranking",
            model=provider.model_name,
            kind=kind,
            environment=settings.ENVIRONMENT,
        )


async def _assert_corpus_model(app: FastAPI, provider: object, assertion: Any) -> None:
    """Refuse to serve when the corpus was embedded with a different model."""
    pool = getattr(app.state, "pool", None)
    corpus_model: str | None = None
    if pool is not None:
        try:
            corpus_model = await pool.fetchval(
                "select model_name from public.genereview_active_embedding where id = 1"
            )
        except asyncpg.PostgresError as exc:
            # An unreadable identity is not evidence of agreement, but it is also not
            # evidence of mismatch; the corpus-version gate already covers an absent
            # corpus. Say so rather than inventing either verdict.
            logger.warning("could not read the corpus embedding identity", error=str(exc))
            return
    assertion(provider, corpus_model)


async def _teardown_state(app: FastAPI) -> None:
    """Tear down shared application state after request serving."""
    logger.info("Shutting down GeneReview Link Server...")
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("Release watcher scheduler stopped.")
    await shutdown_services()
    await shutdown_clients()
    pool = getattr(app.state, "pool", None)
    if pool is not None:
        await pool.close()
        logger.info("Postgres pool closed.")
    logger.info("Shutdown complete.")
