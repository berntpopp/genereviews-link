"""Application lifecycle helpers for the GeneReview Link server."""

import asyncpg
from fastapi import FastAPI

from genereview_link.api.client_manager import get_client_manager, shutdown_clients
from genereview_link.config import settings
from genereview_link.logging_config import get_logger
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

    Two modes remain here:
    1. BUILD_LOCAL=true -> run the full local ingest pipeline (development only).
    2. Otherwise -> the corpus is already present (restored by the init sidecar), or the
       database is empty and `/passages/search` degrades to 503 until it is loaded.
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
            logger.info("BUILD_LOCAL=true; running full local ingest")
            from genereview_link.corpus.pipeline import run_full_ingest
            from genereview_link.ingest.orchestrator import backfill_embeddings, build_hnsw_index
            from genereview_link.retrieval.embeddings import SentenceTransformerEmbeddingProvider

            await run_full_ingest(pool)
            await backfill_embeddings(pool, SentenceTransformerEmbeddingProvider())
            await build_hnsw_index(pool)
            logger.info("local ingest complete")
            return

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
    logger.info(
        "Starting GeneReview Link Server",
        version="3.0.0",
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

    # --- Dense model metadata (cached for _meta under include=score_breakdown) ---
    from genereview_link.retrieval.model_identity import BGE_DIM, BGE_MODEL_NAME

    app.state.dense_model_id = BGE_MODEL_NAME
    app.state.embedding_dim = BGE_DIM

    # --- Active corpus version (cached for _meta.corpus_version) ---
    app.state.corpus_version = None
    if app.state.repository is not None:
        try:
            cv = await app.state.repository.active_corpus_version()
            app.state.corpus_version = cv.version if cv is not None else None
            logger.info(
                "Active corpus version cached on app.state",
                corpus_version=app.state.corpus_version,
            )
        except Exception as exc:
            logger.warning(
                "Failed to read active corpus version; _meta will omit it.",
                error=str(exc),
            )

    # --- primary_gene_symbols availability (issue #106 D4) ---
    # gene_role=primary/mentioned depend on primary_gene_symbols, which ships empty
    # on existing corpus installs. Probe once so the route can reject those roles
    # instead of returning a silently-empty result set.
    app.state.primary_gene_symbols_populated = False
    if app.state.repository is not None:
        try:
            app.state.primary_gene_symbols_populated = (
                await app.state.repository.primary_gene_symbols_populated()
            )
            logger.info(
                "primary_gene_symbols availability probed",
                populated=app.state.primary_gene_symbols_populated,
            )
        except Exception as exc:
            logger.warning("primary_gene_symbols probe failed", error=str(exc))

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

    # --- Embedding provider ---
    if settings.GENEREVIEW_EAGER_LOAD_BGE:
        from genereview_link.retrieval.embeddings import SentenceTransformerEmbeddingProvider

        app.state.embedder = SentenceTransformerEmbeddingProvider(
            device=settings.INGEST_EMBED_DEVICE
        )
        logger.info("BGE SentenceTransformer embedding provider loaded.")
    else:
        from genereview_link.retrieval.embeddings import FakeEmbeddingProvider

        app.state.embedder = FakeEmbeddingProvider(dim=384)
        logger.info("FakeEmbeddingProvider active (set GENEREVIEW_EAGER_LOAD_BGE=true for BGE).")

    # --- Release watcher scheduler ---
    app.state.scheduler = None
    if settings.AUTO_PULL_RELEASES and pool is not None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from genereview_link.ingest.scheduler import check_for_new_release

        app.state.scheduler = AsyncIOScheduler()
        app.state.scheduler.add_job(check_for_new_release, "cron", minute=17, args=[pool])
        app.state.scheduler.start()
        logger.info("Release watcher scheduler started (fires at :17 each hour).")


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
