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
pip install -e ".[dev]"

# Create environment configuration
cp .env.example .env
# Edit .env and add your NCBI_API_KEY (optional but recommended)
```

### Running the Server

The server can be run in different modes depending on your needs.

#### Unified Mode (REST API + MCP over HTTP) - Recommended for Web Deployments
```bash
python server.py --transport unified
# Or in production:
uvicorn server:app --host 0.0.0.0 --port 8000
```
- REST API available at `http://localhost:8000`  
- MCP tools available at `http://localhost:8000/mcp`

#### STDIO Mode (MCP only) - For Local AI Assistants
```bash
python server.py --transport stdio
# Or for backwards compatibility:
python mcp_server.py
```

#### HTTP-Only Mode (REST API only)
```bash
python server.py --transport http
```

#### Development Mode
```bash
python server.py --dev --transport unified
```

The REST API provides:
- Interactive docs at `/docs`
- OpenAPI schema at `/openapi.json`
- Health check at `/health`

## API Endpoints

### Core Endpoints

- **`GET /genereview/{gene_symbol}`** - Complete workflow with all data (comprehensive)
  - Query params: `include_abstract`, `include_links`, `include_fulltext`
- **`GET /search/{gene_symbol}`** - Search for GeneReviews by gene symbol
  - Query params: `retmax` (max results, default 20)
- **`GET /abstract/{pubmed_id}`** - Get abstract and metadata for PubMed articles
- **`GET /links/{pubmed_id}`** - Get all available links (Bookshelf, PMC, external)
- **`GET /fulltext/{nbk_id}`** - Get comprehensive scraped content with hierarchical sections
- **`GET /health`** - System health check with optional connection testing

### Example Usage

```bash
# Get comprehensive data for BRCA1 gene (all sections)
curl "http://localhost:8000/genereview/BRCA1"

# Get BRCA1 data with specific components
curl "http://localhost:8000/genereview/BRCA1?include_abstract=true&include_links=false&include_fulltext=true"

# Search for TP53-related GeneReviews
curl "http://localhost:8000/search/TP53?retmax=5"

# Get abstract for a specific PubMed ID
curl "http://localhost:8000/abstract/20301552"

# Get links for a specific PubMed ID
curl "http://localhost:8000/links/20301552"

# Get full text content from NCBI Bookshelf
curl "http://localhost:8000/fulltext/NBK1246"

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
```

## Development

### Code Quality

```bash
# Linting and formatting
ruff check .                    # Lint code
ruff format .                   # Format code  
black .                         # Alternative formatter

# Type checking
mypy .                          # Type check entire project

# Testing
pytest                          # Run all tests
pytest tests/                   # Run specific test directory
pytest tests/test_specific.py   # Run single test file
pytest -k "test_name"           # Run specific test by name
coverage run -m pytest         # Run tests with coverage
coverage report                 # Show coverage report
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
├── middleware/
│   └── logging_middleware.py    # Request logging with correlation IDs
├── config.py                # Configuration management with Pydantic
└── logging_config.py        # Structured logging with observability

# Additional files:
server.py                    # REST API server (FastAPI)
mcp_server.py               # MCP server for AI integration
```

## MCP Integration

The server supports the Model Context Protocol for seamless AI integration with Claude and other MCP-compatible clients.

### Configuration

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "genereview-link": {
      "command": "python",
      "args": ["/path/to/mcp_server.py"],
      "env": {
        "NCBI_API_KEY": "your_api_key_here"
      }
    }
  }
}
```

### Available MCP Tools

- **`search_genereviews`** - Search for GeneReviews by gene symbol
- **`get_abstract`** - Fetch PubMed abstract and metadata
- **`get_links`** - Get all available links for a publication
- **`get_fulltext`** - Scrape comprehensive content from NCBI Bookshelf
- **`get_genereview_summary`** - Complete workflow with all data sources

### Recent Fixes

✅ **Resolved MCP JSON parsing errors** - Fixed stdout contamination that caused "Unexpected non-whitespace character" errors  
✅ **Clean protocol communication** - All logs now properly routed to stderr, leaving stdout for JSON protocol

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

## License

This project is licensed under the MIT License.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests and linting
5. Submit a pull request

## Support

For issues and questions, please use the GitHub issue tracker.