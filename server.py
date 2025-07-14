#!/usr/bin/env python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from genereview_link.api.routes.genereview import router as genereview_router
from genereview_link.api.routes.search import router as search_router
from genereview_link.api.routes.abstract import router as abstract_router
from genereview_link.api.routes.links import router as links_router
from genereview_link.api.routes.fulltext import router as fulltext_router
from genereview_link.config import settings
from genereview_link.api.client_manager import shutdown_clients, get_client_manager
from genereview_link.services.service_manager import shutdown_services
from genereview_link.logging_config import configure_structlog, get_logger
from genereview_link.middleware.logging_middleware import RequestLoggingMiddleware

# Configure structured logging
configure_structlog()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for proper startup/shutdown."""
    logger.info(
        "Starting GeneReview Link Server",
        version="2.0.0",
        environment=settings.ENVIRONMENT,
    )

    # Startup: Initialize managers without testing connection
    client_manager = await get_client_manager()
    health = await client_manager.health_check(test_connection=False)
    logger.info(
        "Client manager initialized", status=health["status"], client_health=health
    )

    try:
        yield
    finally:
        # Shutdown: Clean up resources
        logger.info("Shutting down GeneReview Link Server")
        await shutdown_services()
        await shutdown_clients()
        logger.info("Shutdown complete")


app = FastAPI(
    title="GeneReview Link Server",
    description="A comprehensive API for searching, fetching, and scraping NCBI GeneReviews data.",
    version="2.0.0",
    lifespan=lifespan,
)

# Add logging middleware first (so it wraps everything)
app.add_middleware(RequestLoggingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include all routers
app.include_router(search_router)
app.include_router(abstract_router)
app.include_router(links_router)
app.include_router(fulltext_router)
app.include_router(genereview_router)


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "Welcome to the GeneReview Link Server!",
        "version": "2.0.0",
        "endpoints": {
            "search": "GET /search/{gene_symbol} - Search for GeneReviews by gene symbol",
            "abstract": "GET /abstract/{pubmed_id} - Get abstract and metadata for a PubMed ID",
            "links": "GET /links/{pubmed_id} - Get all available links for a PubMed ID",
            "fulltext": "GET /fulltext/{nbk_id} - Get comprehensive scraped content from NCBI Bookshelf",
            "genereview": "GET /genereview/{gene_symbol} - Complete workflow with all data",
            "docs": "GET /docs - Interactive API documentation",
            "health": "GET /health?test_connection=false - System health check",
        },
    }


@app.get("/health", tags=["Health"])
async def health_check(test_connection: bool = False):
    """
    System health check endpoint.

    - test_connection=false (default): Quick check without network requests
    - test_connection=true: Full check including NCBI API connectivity
    """
    client_manager = await get_client_manager()
    health = await client_manager.health_check(test_connection=test_connection)

    overall_status = "healthy"
    if health["status"] in ["degraded", "unhealthy"]:
        overall_status = "degraded"
    elif health["status"] == "ready" and not test_connection:
        overall_status = "ready"

    return {
        "status": overall_status,
        "version": "2.0.0",
        "client_health": health,
        "features": {
            "singleton_clients": True,
            "distributed_rate_limiting": bool(settings.RATE_LIMIT_STATE_FILE),
            "comprehensive_caching": True,
            "api_key_configured": bool(settings.NCBI_API_KEY),
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
