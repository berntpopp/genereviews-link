# GeneReview Link Server

A unified Python server providing both REST API and MCP interfaces for searching, fetching, and scraping NCBI GeneReviews data with enhanced performance, observability, and reliability.

## Features

- **Dual Interface**: REST API and Model Context Protocol (MCP) support
- **Comprehensive Data**: Search, abstracts, links, and full-text content with hierarchical section extraction
- **Intelligent Caching**: Async LRU caching with configurable TTL and size limits
- **Rate Limiting**: NCBI-compliant request throttling with distributed coordination
- **Robust Scraping**: Enhanced HTML parsing with browser-like headers and retry logic
- **Structured Logging**: JSON-formatted logs with correlation IDs and performance metrics
- **Production Ready**: Comprehensive error handling, health checks, and monitoring

## Quick Start

### Installation

```bash
# Install with development dependencies
uv sync

# Create environment configuration
cp .env.example .env
# Edit .env and add your NCBI_API_KEY (optional but recommended)
```

For production Docker or Nginx Proxy Manager deployments, use `.env.docker`:

```bash
cp .env.docker.example .env.docker
# Edit .env.docker before deploying.
```

### Running the Server

The server can be run in different modes depending on your needs.

#### Unified Mode (REST API + MCP over HTTP) - Recommended for Web Deployments
```bash
uv run genereview-link serve --transport unified
# Or in production:
uv run uvicorn server:app --host 0.0.0.0 --port 8000
```
- REST API available at `http://localhost:8000`  
- MCP tools available at `http://localhost:8000/mcp`

#### STDIO Mode (MCP only) - For Local AI Assistants
```bash
uv run genereview-link serve --transport stdio
# Or for backwards compatibility:
uv run python mcp_server.py
```

#### HTTP-Only Mode (REST API only)
```bash
uv run genereview-link serve --transport http
```

#### Development Mode
```bash
uv run genereview-link serve --dev --transport unified
```

The REST API provides:
- Interactive docs at `/docs`
- OpenAPI schema at `/openapi.json`
- Health check at `/health`

## API Endpoints

### Core Endpoints

- **`GET /genereview/{gene_symbol}`** - Convenience orchestration (lean by default; opt in to fulltext)
  - Query params: `include_abstract`, `include_links`, `include_fulltext` (default `false`), `max_chars` (default `16000`, `0` disables the cap), `fresh`
- **`GET /search/{gene_symbol}`** - Search for GeneReviews by gene symbol
  - Query params: `retmax` (max results, default 20)
- **`GET /abstract/{pmid}`** - Get abstract and metadata for PubMed articles
- **`GET /links/{pmid}`** - Get all available links (Bookshelf, PMC, external)
- **`GET /fulltext/{nbk_id}`** - Get comprehensive scraped content with hierarchical sections
  - Query params: `sections` (optional, comma-separated section keys for selective retrieval; fuzzy substring matching, e.g. `summary` matches both `summary` and `clinical_summary`)
- **`GET /health`** - System health check with optional connection testing

### Example Usage

```bash
# Lean BRCA1 envelope: abstract + links only (no scraped fulltext by default)
curl "http://localhost:8000/genereview/BRCA1"

# Opt into fulltext; the default 16000-char cap truncates large chapters and
# stamps _meta.truncated + next_commands -> get_chapter_section. Pass
# max_chars=0 to disable the cap.
curl "http://localhost:8000/genereview/BRCA1?include_fulltext=true"
curl "http://localhost:8000/genereview/BRCA1?include_fulltext=true&max_chars=0"

# Search for TP53-related GeneReviews
curl "http://localhost:8000/search/TP53?retmax=5"

# Get abstract for a specific PubMed ID
curl "http://localhost:8000/abstract/20301552"

# Get links for a specific PubMed ID
curl "http://localhost:8000/links/20301552"

# Get full text content from NCBI Bookshelf
curl "http://localhost:8000/fulltext/NBK1246"

# Get only the summary and diagnosis sections (fuzzy substring match)
curl "http://localhost:8000/fulltext/NBK1246?sections=summary,diagnosis"

# Health check with connection testing
curl "http://localhost:8000/health?test_connection=true"
```

## Configuration

Configure via environment variables or `.env` file:

### Core Settings
```bash
NCBI_API_KEY=your_api_key_here          # Optional, increases rate limits to 10/sec
EUTILS_BASE_URL=https://eutils.ncbi.nlm.nih.gov/entrez/eutils  # NCBI E-utils base URL
```

### Performance & Caching
```bash
CACHE_SIZE=512                          # LRU cache size (default: 512)
CACHE_TTL_HOURS=24                     # Cache TTL in hours (default: 24)
RATE_LIMIT_STATE_FILE=/path/to/state   # Multi-worker rate limiting (optional)
```

### Logging & Monitoring
```bash
LOG_LEVEL=INFO                         # Logging level (default: INFO)
LOG_JSON=false                         # JSON logging for production (default: false)
ENVIRONMENT=development                # Environment name for logging context
```

### Server Configuration
```bash
CORS_ORIGINS=*                         # CORS allowed origins (default: *)
MCP_ALLOWED_HOSTS='["localhost","127.0.0.1","::1"]'
MCP_ALLOWED_ORIGINS='[]'
```

Host and Origin validation is strict for every HTTP route. Add reverse-proxy
hostnames to `MCP_ALLOWED_HOSTS` as exact JSON strings; wildcards are rejected.
Browser deployments must add exact origins to both `MCP_ALLOWED_ORIGINS` and
`CORS_ORIGINS`, because transport validation and browser CORS are independent.

## Development

### Code Quality

```bash
# Linting and formatting
make lint                       # Ruff lint
make format                     # Ruff format

# Type checking
make typecheck                  # Strict mypy

# Testing
make test                       # Fast test suite
make test-unit                  # Unit tests only
make test-integration           # Integration tests
make test-cov                   # Coverage with threshold
uv run pytest tests/test_specific.py -q
uv run pytest -k "test_name" -q

# Full local gate before handoff
make ci-local
```

### Project Structure

```
genereview_link/
├── api/
│   ├── routes/              # FastAPI route handlers
│   │   ├── search.py        # Search endpoint
│   │   ├── abstract.py      # Abstract endpoint
│   │   ├── links.py         # Links endpoint
│   │   ├── fulltext.py      # Full text endpoint
│   │   └── genereview.py    # Comprehensive endpoint
│   ├── eutils_client.py     # NCBI E-utilities client with enhanced scraping
│   └── client_manager.py    # Singleton client lifecycle management
├── models/
│   └── genereview_models.py # Pydantic data models and validation
├── services/
│   ├── genereview_service.py    # Business logic with caching
│   └── service_manager.py       # Service lifecycle management
├── config.py                # Configuration management with Pydantic
└── logging_config.py        # Structured logging with observability

# Additional files:
server.py                    # REST API server (FastAPI)
mcp_server.py               # MCP server for AI integration
```

## MCP Integration

The server supports the Model Context Protocol for seamless AI integration with Claude and other MCP-compatible clients.

### Gateway namespace and identity

This server follows the **GeneFoundry Tool-Naming Standard v1**. Leaf tools are
exposed **unprefixed** (e.g. `get_abstract`, `search_passages`); the
[`genefoundry-router`](https://github.com/berntpopp/genefoundry-router) gateway
adds the namespace at mount time.

- **`serverInfo.name`:** `GeneReview Link Tool`
- **Canonical gateway namespace token:** `genereviews`
- **At the gateway, tools surface as:** `genereviews_<tool>` (e.g.
  `genereviews_get_abstract`, `genereviews_search_passages`).

### Configuration

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "genereview-link": {
      "command": "uv",
      "args": [
        "run",
        "genereview-link",
        "serve",
        "--transport",
        "stdio"
      ],
      "env": {
        "NCBI_API_KEY": "your_api_key_here"
      }
    }
  }
}
```

For a local hosted MCP server running on port 8765:

```bash
claude mcp add --transport http genereview-link http://127.0.0.1:8765/mcp
```

### Available MCP Tools

- **`search_genereviews`** - Search for GeneReviews by gene symbol
- **`search_passages`** - Corpus-backed passage search with modes and filters
- **`get_chapter_metadata`** - Chapter outline, section counts, and table IDs
- **`get_chapter_section`** - Retrieve passages for a chapter section
- **`get_passage`** - Retrieve one passage with optional neighbor context
- **`get_passages_batch`** - Fetch up to 20 passages by ID
- **`get_table`** - Fetch a structured GeneReviews table
- **`get_abstract`** - Fetch PubMed abstract and metadata
- **`get_links`** - Get all available links for a publication
- **`get_fulltext`** - Scrape comprehensive content from NCBI Bookshelf
- **`get_genereview_summary`** - Convenience summary workflow
- **`get_license`** - License and attribution text

Static references are also available as MCP resources, including
`genereview://usage` and `genereview://license`.

### Recent Fixes

- **Resolved MCP JSON parsing errors** - Fixed stdout contamination that caused
  "Unexpected non-whitespace character" errors.
- **Clean protocol communication** - Logs are routed to stderr, leaving stdout
  for JSON protocol in stdio mode.
- **Phase 1 correctness/performance** - Cache TTLs are applied, STDIO now runs
  the same lifecycle state as HTTP, Postgres search path is configured at pool
  creation, bundle extraction is hardened, and asyncpg pool defaults are
  production-tuned.

## Technical Details

### Architecture Highlights

- **Singleton Pattern**: Efficient client and service lifecycle management
- **Async Design**: Full async/await support with proper connection pooling
- **Distributed Rate Limiting**: Multi-worker coordination via shared state files
- **Comprehensive Caching**: Both service-level and client-level caching with LRU eviction

### Rate Limiting
- **With API Key**: 10 requests/second (0.11s delay)
- **Without API Key**: 3 requests/second (0.34s delay)
- **Web Scraping**: 3x longer delays with exponential backoff retry
- **Multi-worker Support**: File-based coordination for production deployments

### Caching Strategy
- **Async LRU Cache**: Service methods use `@alru_cache` decorator
- **Configurable Parameters**: Size (default: 512) and TTL (default: 24h)
- **Dynamic Application**: Cache decorators applied at runtime for flexibility
- **Memory Efficient**: Automatic eviction based on size and time limits

### Enhanced Scraping
- **Hierarchical Extraction**: Maintains document structure with nested sections
- **Robust Parsing**: Multiple fallback strategies for content extraction
- **Browser Simulation**: Realistic headers and retry patterns
- **Error Recovery**: Graceful degradation when specific sections fail

### Observability
- **Structured Logging**: JSON-formatted logs with correlation IDs
- **Performance Metrics**: Request timing, cache hit rates, and error tracking
- **Health Monitoring**: Comprehensive health checks with connectivity testing
- **Request Tracing**: Full request lifecycle tracking with unique identifiers

### Error Handling
- **Custom Exceptions**: `DataNotFoundError` for missing resources with descriptive messages
- **Graceful Retries**: 403/429 handling with exponential backoff
- **Fallback Mechanisms**: Multiple strategies for data retrieval
- **Comprehensive Logging**: Detailed error context for debugging and monitoring

## Security

See [`SECURITY.md`](SECURITY.md) for the vulnerability-reporting process and the
required repository settings. In particular, an operator must enable **secret
scanning and push protection** (a GitHub repo setting, not code) via the
`gh api` command documented there. CodeQL and Dependabot are already wired into
CI.

When restoring from a release bundle (Mode 1), set `EXPECTED_BUNDLE_SHA256` to
an independently-trusted, out-of-band SHA-256 of the bundle so authenticity is
verified against a committed anchor rather than a checksum fetched from the same
(redirected) download host.

## License

This project is licensed under the MIT License.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests and linting
5. Submit a pull request

## Deployment modes

GeneReview Link supports three corpus-loading modes, selected at startup via environment variables.

### Mode 1 — Restore from release bundle (`BUNDLE_URL`)

The fastest way to get a populated corpus. Set `BUNDLE_URL` to a `.tar.gz` asset URL from the
GitHub Releases page, or to the special value `latest` to auto-resolve the newest release after
a release bundle has been published:

```bash
BUNDLE_URL=latest DATABASE_URL=postgresql://... genereview-link serve
```

On first boot the server downloads and integrity-verifies the bundle, restores the Postgres dump,
and starts serving immediately. Subsequent boots detect the active corpus and skip the restore.

This is the recommended Docker production mode. The bundle is built locally by the maintainer on
CUDA, published as a GitHub Release asset, and consumed by Docker at startup. Maintainers build and
publish a new bundle with:

```bash
make cuda-check
RELEASE_ID=2026-05-12-r1 make bundle-publish-local
```

Set `GITHUB_REPO=owner/repo` (default `berntpopp/genereviews-link`) when hosting your own releases.
Leave `BUNDLE_URL` empty when no release bundle exists; the server will boot, but corpus-backed
passage search returns 503 until a corpus is loaded.

### Mode 2 — Build corpus locally (`BUILD_LOCAL=true`)

Runs the full ingest pipeline (download from NCBI, parse, embed) on first boot:

```bash
BUILD_LOCAL=true DATABASE_URL=postgresql://... genereview-link serve
```

Expect 15-30 minutes on first boot. Requires an NCBI API key for reliable rate limits
(`NCBI_API_KEY=...`). Subsequent boots are instant.

### Mode 3 — External Postgres (no env vars)

Point `DATABASE_URL` at a pre-populated database (e.g. managed RDS / Cloud SQL). The server
assumes the corpus already exists and starts immediately:

```bash
DATABASE_URL=postgresql://user:pass@managed-host/genereview genereview-link serve
```

If the schema is empty, `/passages/search` returns 503 until the corpus is loaded externally.

### Memory budget and worker tuning

| Component | Approximate RAM |
|---|---|
| Python + FastAPI baseline | ~150 MB |
| BGE-small-en-v1.5 model (`GENEREVIEW_EAGER_LOAD_BGE=true`) | ~130 MB |
| asyncpg pool (20 connections, default max) | ~100 MB |
| Postgres shared_buffers (self-hosted) | ~1 GB |
| **Total recommended** | **3 GB** |

The bundled production Docker/Nginx Proxy Manager stack uses the unified CLI
server so both REST and `/mcp` are exposed by the same process:

```bash
genereview-link serve --transport unified --host 0.0.0.0 --port 8000
```

`docker/gunicorn_conf.py` remains available for custom Gunicorn deployments, but
the bundled production compose files do not use `GUNICORN_WORKERS`.

Use `GENEREVIEW_EAGER_LOAD_BGE=true` only when semantic passage search is required;
leave it `false` (default) for API-key-only or lite deployments.

## Support

For issues and questions, please use the GitHub issue tracker.
