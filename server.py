#!/usr/bin/env python
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from genereview_link.api.routes.genereview import router as genereview_router
from genereview_link.api.routes.search import router as search_router
from genereview_link.api.routes.abstract import router as abstract_router
from genereview_link.api.routes.links import router as links_router
from genereview_link.api.routes.fulltext import router as fulltext_router
from genereview_link.config import settings

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL))
logger = logging.getLogger(__name__)

app = FastAPI(
    title="GeneReview Link Server",
    description="A comprehensive API for searching, fetching, and scraping NCBI GeneReviews data.",
    version="2.0.0",
)

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
            "docs": "GET /docs - Interactive API documentation"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)