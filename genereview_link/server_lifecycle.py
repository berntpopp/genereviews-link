"""Application lifecycle helpers for the GeneReview Link server."""

import hashlib
import json
import os
import shutil
import tarfile as tf_mod
from pathlib import Path
from typing import IO

import asyncpg
from fastapi import FastAPI

from genereview_link.api.client_manager import get_client_manager, shutdown_clients
from genereview_link.config import settings
from genereview_link.logging_config import get_logger
from genereview_link.services.service_manager import get_service_manager, shutdown_services

logger = get_logger("server.manager")


def _sha256_stream(fh: IO[bytes]) -> str:
    """Return the SHA-256 digest for an open binary stream."""
    h = hashlib.sha256()
    for chunk in iter(lambda: fh.read(65536), b""):
        h.update(chunk)
    return h.hexdigest()


def _bundle_bootstrap_paths(work_dir: Path) -> tuple[Path, Path]:
    """Return bundle tarball and extraction paths under the writable work dir."""
    return work_dir / "bundle.tar.gz", work_dir / "bundle_extract"


async def _bootstrap() -> None:
    """Bootstrap the corpus before the pool is opened for request serving.

    Three modes:
    1. BUNDLE_URL set -> download + verify + pg_restore bundle.
    2. BUILD_LOCAL=true -> run full local ingest pipeline.
    3. Neither -> assume an external Postgres already has a corpus (or it's empty).

    In all cases, if an active corpus version already exists the function
    returns immediately (hot-path / already-populated).
    """
    from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
    from genereview_link.db.pool import create_pool
    from genereview_link.ingest.github_release import (
        download_with_integrity,
        fetch_sibling_sha256,
        pg_restore,
        resolve_latest,
    )

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
            return  # MODE 1 hot path / already-populated

        bundle_url = settings.BUNDLE_URL
        if bundle_url == "latest":
            bundle_url = await resolve_latest(settings.GITHUB_REPO)
        if bundle_url:
            logger.info("downloading corpus bundle", url=bundle_url)
            sha = await fetch_sibling_sha256(bundle_url)
            work_dir = Path(settings.BUNDLE_BOOTSTRAP_DIR)
            tmp, extract_dir = _bundle_bootstrap_paths(work_dir)
            staging_dir = work_dir / "bundle_extract.tmp"
            shutil.rmtree(extract_dir, ignore_errors=True)
            shutil.rmtree(staging_dir, ignore_errors=True)
            work_dir.mkdir(parents=True, exist_ok=True)
            await download_with_integrity(bundle_url, tmp, expected_sha256=sha)
            staging_dir.mkdir(parents=True, exist_ok=True)
            with tf_mod.open(tmp, "r:gz") as tar:
                manifest_member = tar.getmember("manifest.json")
                manifest_file = tar.extractfile(manifest_member)
                if manifest_file is None:
                    raise RuntimeError("manifest.json is not a file")
                manifest = json.loads(manifest_file.read())
                checksum_entries = manifest["checksums"]
                expected_names = ["manifest.json", *checksum_entries.keys()]
                expected_members = set(expected_names)

                seen: set[str] = set()
                for member in tar.getmembers():
                    if member.name in seen:
                        raise RuntimeError(f"duplicate tar member: {member.name}")
                    seen.add(member.name)
                    if member.name not in expected_members:
                        raise RuntimeError(f"unexpected tar member: {member.name}")

                for relpath, expected in checksum_entries.items():
                    member = tar.getmember(relpath)
                    member_file = tar.extractfile(member)
                    if member_file is None:
                        raise RuntimeError(f"manifest member is not a file: {relpath}")
                    actual = _sha256_stream(member_file)
                    if actual != expected:
                        raise RuntimeError(f"manifest checksum mismatch on {relpath}")

                for name in expected_names:
                    tar.extract(tar.getmember(name), path=str(staging_dir), filter="data")
            shutil.rmtree(extract_dir, ignore_errors=True)
            staging_dir.rename(extract_dir)
            await pg_restore(
                extract_dir / "corpus.dump",
                database_url=settings.DATABASE_URL,
                jobs=os.cpu_count() or 2,
            )
            logger.info("corpus bundle restored")
            return

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

        # MODE 3: external Postgres - assume corpus already present (or empty)
        logger.warning(
            "no BUNDLE_URL or BUILD_LOCAL set and no active corpus; "
            "/passages/search will return 503 until corpus is loaded"
        )
    except asyncpg.PostgresError as exc:
        logger.warning("bootstrap failed; server will start without corpus", error=str(exc))
    finally:
        if "staging_dir" in locals():
            shutil.rmtree(staging_dir, ignore_errors=True)
        await pool.close()


async def _initialize_state(app: FastAPI) -> None:
    """Initialize shared application state for request serving."""
    logger.info(
        "Starting GeneReview Link Server",
        version="2.0.0",
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
    from genereview_link.corpus.tokenizer import BGE_DIM, BGE_MODEL_NAME

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
