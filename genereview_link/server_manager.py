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
from genereview_link.config import ServerConfig, settings
from genereview_link.logging_config import get_logger
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
        client_manager = await get_client_manager()
        service_manager = await get_service_manager()
        await client_manager.get_client()  # Initialize client
        await service_manager.get_service()  # Initialize service
        logger.info("Client and Service managers initialized.")
        yield
        logger.info("Shutting down GeneReview Link Server...")
        await shutdown_services()
        await shutdown_clients()
        logger.info("Shutdown complete.")

    async def create_fastapi_app(self, config: ServerConfig) -> FastAPI:
        """Create the core FastAPI application."""
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

        app.include_router(search.router)
        app.include_router(abstract.router)
        app.include_router(links.router)
        app.include_router(fulltext.router)
        app.include_router(genereview.router)
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
        }

        mcp_route_maps = [
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
        """Start the server in unified mode (REST API + MCP over HTTP)."""
        self._current_transport = "unified"
        logger.info(f"Starting unified server on {config.host}:{config.port}")
        self.app = await self.create_fastapi_app(config)
        self.mcp = await self.create_mcp_server(self.app, config)
        self.app.mount(config.mcp_path, self.mcp.http_app())
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
        self.app = await self.create_fastapi_app(config)
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
        self.app = await self.create_fastapi_app(config)

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
