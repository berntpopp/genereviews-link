"""Unified server manager for GeneReview Link with multiple transports."""

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from genereview_link.api.client_manager import (
    get_client_manager,
    shutdown_clients,
)
from genereview_link.api.routes import (
    abstract,
    fulltext,
    genereview,
    links,
    search,
)
from genereview_link.api.routes import chapters as chapters_routes
from genereview_link.api.routes import debug as debug_routes
from genereview_link.api.routes import license as license_routes
from genereview_link.api.routes import passages as passages_routes
from genereview_link.config import ServerConfig, settings
from genereview_link.logging_config import get_logger
from genereview_link.services.errors import NotYetIndexedError
from genereview_link.services.service_manager import (
    get_service_manager,
    shutdown_services,
)

logger = get_logger("server.manager")

REQUEST_COUNTER = Counter(
    "genereview_requests_total",
    "Total HTTP requests",
    labelnames=("method", "path", "status"),
)
REQUEST_LATENCY = Histogram(
    "genereview_request_duration_seconds",
    "HTTP request latency in seconds",
    labelnames=("method", "path"),
)


async def _bootstrap() -> None:
    """Bootstrap the corpus before the pool is opened for request serving.

    Three modes:
    1. BUNDLE_URL set → download + verify + pg_restore bundle.
    2. BUILD_LOCAL=true → run full local ingest pipeline.
    3. Neither → assume an external Postgres already has a corpus (or it's empty).

    In all cases, if an active corpus version already exists the function
    returns immediately (hot-path / already-populated).
    """
    import json
    import os
    import tarfile as tf_mod
    from pathlib import Path

    import asyncpg

    from genereview_link.corpus.bundle import sha256_file
    from genereview_link.db.migrate import apply_control_migrations
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
            logger.info("active corpus found; skipping bootstrap")
            return  # MODE 1 hot path / already-populated

        bundle_url = settings.BUNDLE_URL
        if bundle_url == "latest":
            bundle_url = await resolve_latest(settings.GITHUB_REPO)
        if bundle_url:
            logger.info("downloading corpus bundle", url=bundle_url)
            sha = await fetch_sibling_sha256(bundle_url)
            tmp = Path("/tmp") / "bundle.tar.gz"  # noqa: S108
            await download_with_integrity(bundle_url, tmp, expected_sha256=sha)
            extract_dir = Path("/tmp/bundle_extract")  # noqa: S108
            with tf_mod.open(tmp, "r:gz") as tar:
                tar.extractall(str(extract_dir))  # noqa: S202
            manifest = json.loads((extract_dir / "manifest.json").read_text())
            for relpath, expected in manifest["checksums"].items():
                actual = sha256_file(extract_dir / relpath)
                if actual != expected:
                    raise RuntimeError(f"manifest checksum mismatch on {relpath}")
            await pg_restore(
                extract_dir / "corpus.dump",
                database_url=settings.DATABASE_URL,
                jobs=os.cpu_count(),
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

        # MODE 3: external Postgres — assume corpus already present (or empty)
        logger.warning(
            "no BUNDLE_URL or BUILD_LOCAL set and no active corpus; "
            "/passages/search will return 503 until corpus is loaded"
        )
    except asyncpg.PostgresError as exc:
        logger.warning("bootstrap failed; server will start without corpus", error=str(exc))
    finally:
        await pool.close()


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        path = request.url.path
        REQUEST_LATENCY.labels(method=request.method, path=path).observe(elapsed)
        REQUEST_COUNTER.labels(
            method=request.method,
            path=path,
            status=str(response.status_code),
        ).inc()
        return response


class UnifiedServerManager:
    """Manages multiple transport protocols for the GeneReview Link server."""

    def __init__(self) -> None:
        """Initialize the unified server manager."""
        self.app: FastAPI | None = None
        self.mcp: FastMCP | None = None
        self.shutdown_event = asyncio.Event()
        self._current_transport = "unknown"

    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncGenerator[None, None]:
        """Manage application lifecycle for startup and shutdown."""
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
                # registered on every connection — required for dense vector
                # queries (e.g. /passages/search?rerank=rrf).
                pool = await create_pool()
                app.state.pool = pool
                app.state.repository = GeneReviewRepository(pool)
                logger.info("Postgres pool and repository initialised.")
            except Exception as exc:
                logger.warning(
                    "Failed to create Postgres pool; /passages/* will 503.", error=str(exc)
                )
                app.state.pool = None
                app.state.repository = None
        else:
            logger.info("DATABASE_URL not set; skipping Postgres pool (repository unavailable).")
            app.state.pool = None
            app.state.repository = None

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
            logger.info(
                "FakeEmbeddingProvider active (set GENEREVIEW_EAGER_LOAD_BGE=true for BGE)."
            )

        # --- Release watcher scheduler ---
        scheduler = None
        if settings.AUTO_PULL_RELEASES and pool is not None:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler

            from genereview_link.ingest.scheduler import check_for_new_release

            scheduler = AsyncIOScheduler()
            scheduler.add_job(check_for_new_release, "cron", minute=17, args=[pool])
            scheduler.start()
            logger.info("Release watcher scheduler started (fires at :17 each hour).")

        yield

        logger.info("Shutting down GeneReview Link Server...")
        if scheduler is not None:
            scheduler.shutdown(wait=False)
            logger.info("Release watcher scheduler stopped.")
        await shutdown_services()
        await shutdown_clients()
        if pool is not None:
            await pool.close()
            logger.info("Postgres pool closed.")
        logger.info("Shutdown complete.")

    def create_fastapi_app(self, config: ServerConfig) -> FastAPI:
        """Create the core FastAPI application.

        Synchronous so it can be invoked at module import time (e.g. by gunicorn's
        preload, by `uvicorn server:app`, or by `uvicorn --reload`) without nesting
        an event loop.
        """
        app = FastAPI(
            title="GeneReview Link Server",
            description=(
                "A comprehensive API for searching, fetching, and scraping NCBI GeneReviews data."
            ),
            version="2.0.0",
            lifespan=self.lifespan,
            docs_url="/docs" if config.enable_docs else None,
            redoc_url=None,
        )

        # asgi-correlation-id should be added last so it becomes the outermost
        # middleware and tags every response (including CORS preflight) with the
        # correlation ID. See https://github.com/snok/asgi-correlation-id
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",")],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        app.add_middleware(
            CorrelationIdMiddleware,
            header_name=settings.CORRELATION_ID_HEADER,
            update_request_header=True,
        )

        if settings.ENABLE_METRICS:
            app.add_middleware(PrometheusMiddleware)

        @app.exception_handler(NotYetIndexedError)
        async def not_yet_indexed_handler(
            _request: object, exc: NotYetIndexedError
        ) -> JSONResponse:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "not_yet_indexed",
                    "gene_symbol": exc.gene_symbol,
                    "nbk_id": exc.nbk_id,
                    "pubmed_id": exc.pubmed_id,
                    "corpus_version": exc.corpus_version,
                    "hint": "Pass ?fresh=true to fetch from NCBI live",
                },
            )

        app.include_router(search.router)
        app.include_router(abstract.router)
        app.include_router(links.router)
        app.include_router(fulltext.router)
        app.include_router(genereview.router)
        app.include_router(passages_routes.router)
        app.include_router(chapters_routes.router)
        app.include_router(debug_routes.router)
        app.include_router(license_routes.router)
        self._add_utility_endpoints(app)

        return app

    def _add_utility_endpoints(self, app: FastAPI) -> None:
        """Add utility endpoints like health checks."""

        @app.get("/", tags=["Root"])
        async def root() -> dict[str, str]:
            return {"message": "Welcome to the GeneReview Link Server!"}

        @app.get("/health", tags=["Health"])
        async def health_check(test_connection: bool = False) -> dict[str, Any]:
            client_manager = await get_client_manager()
            health = await client_manager.health_check(test_connection=test_connection)
            return {"status": "healthy", "client_health": health}

        @app.get("/metrics", tags=["Observability"], include_in_schema=False)
        async def metrics() -> Response:
            return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    async def create_mcp_server(self, app: FastAPI, config: ServerConfig) -> FastMCP:
        """Create a FastMCP server instance from the FastAPI app."""
        from fastmcp.server.providers.openapi import MCPType, RouteMap

        mcp_custom_names = {
            "get_genereview_summary": "get_genereview_summary",
            "search_genereviews": "search_genereviews",
            "get_abstract": "get_abstract",
            "get_links": "get_links",
            "get_fulltext": "get_fulltext",
            "search_passages": "search_passages",
            "get_chapter_section": "get_chapter_section",
            "get_license": "get_license",
        }

        mcp_route_maps = [
            # Exclude debug routes from MCP tool exposure
            RouteMap(pattern=r"^/debug/", mcp_type=MCPType.EXCLUDE),
            RouteMap(pattern=r"^/health$", mcp_type=MCPType.EXCLUDE),
            RouteMap(pattern=r"^/$", mcp_type=MCPType.EXCLUDE),
            RouteMap(pattern=r"^/docs$", mcp_type=MCPType.EXCLUDE),
            RouteMap(pattern=r"^/openapi.json$", mcp_type=MCPType.EXCLUDE),
        ]

        mcp = FastMCP.from_fastapi(
            app=app,
            name="GeneReview Link Tool",
            mcp_names=mcp_custom_names,
            route_maps=mcp_route_maps,
        )
        return mcp

    async def start_unified_server(self, config: ServerConfig) -> None:
        """Start the server in unified mode (REST API + MCP over HTTP).

        FastMCP's streamable-HTTP transport requires its session manager to be
        started by the parent ASGI app's lifespan. We therefore build the MCP
        app first, then attach its lifespan to the FastAPI app by chaining it
        with the FastAPI lifespan we already use for the Postgres pool, the
        embedder, and the release watcher scheduler.

        We also use ``path="/"`` on ``http_app`` so the resulting mount lives
        at ``{config.mcp_path}`` (e.g. ``/mcp``) rather than the double-prefix
        ``{config.mcp_path}/mcp``.
        """
        self._current_transport = "unified"
        logger.info(f"Starting unified server on {config.host}:{config.port}")

        # Build the FastAPI app once, then construct FastMCP against the SAME
        # app instance. FastMCP captures a reference to the FastAPI app for
        # dispatching tool calls — if we built a discovery-only app for the
        # MCP and a separate serving app for HTTP, tool calls would land on
        # the discovery app whose lifespan never ran (app.state.repository =
        # None → 503 from /passages/search and /chapters/.../sections/...).
        self.app = self.create_fastapi_app(config)
        self.mcp = await self.create_mcp_server(self.app, config)
        mcp_app = self.mcp.http_app(path="/")

        # Chain mcp_app's lifespan into the existing app's lifespan so the
        # FastMCP StreamableHTTPSessionManager starts at boot.
        original_lifespan = self.app.router.lifespan_context

        @asynccontextmanager
        async def combined_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
            async with mcp_app.lifespan(app), original_lifespan(app):
                yield

        self.app.router.lifespan_context = combined_lifespan
        # Mount under config.mcp_path so the full path is /mcp (not /mcp/mcp).
        self.app.mount(config.mcp_path, mcp_app)
        logger.info(f"MCP HTTP interface mounted at {config.mcp_path}")

        uvicorn_config = uvicorn.Config(
            app=self.app,
            host=config.host,
            port=config.port,
            log_config=None,
        )
        server = uvicorn.Server(uvicorn_config)
        await server.serve()

    async def start_stdio_server(self, config: ServerConfig) -> None:
        """Start the server in STDIO mode for MCP."""
        self._current_transport = "stdio"
        logger.info("Starting STDIO MCP server...")
        self.app = self.create_fastapi_app(config)
        # Manually initialize services since lifespan won't run
        client_manager = await get_client_manager()
        await client_manager.get_client()
        service_manager = await get_service_manager()
        await service_manager.get_service()

        self.mcp = await self.create_mcp_server(self.app, config)
        await self.mcp.run_async(transport="stdio")

    async def start_http_only_server(self, config: ServerConfig) -> None:
        """Start the server in HTTP-only mode (REST API only)."""
        self._current_transport = "http"
        logger.info(f"Starting HTTP-only server on {config.host}:{config.port}")
        self.app = self.create_fastapi_app(config)

        uvicorn_config = uvicorn.Config(
            app=self.app,
            host=config.host,
            port=config.port,
            log_config=None,
        )
        server = uvicorn.Server(uvicorn_config)
        await server.serve()

    async def start_server(self, config: ServerConfig) -> None:
        """Start the server based on the transport configuration."""
        if config.transport == "unified":
            await self.start_unified_server(config)
        elif config.transport == "stdio":
            await self.start_stdio_server(config)
        elif config.transport == "http":
            await self.start_http_only_server(config)
        else:
            raise ValueError(f"Unknown transport: {config.transport}")
