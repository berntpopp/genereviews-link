# Architecture

`genereviews-link` is genuinely **dual-surface**: one process, one service layer, two
protocols. A FastAPI REST API and an MCP server are built from the same routes — the MCP
tool surface is derived from the FastAPI operations, so a route's `operation_id` *is* its
tool name. That is why the tool names in the README table are the `operation_id`s in
`genereview_link/api/routes/`.

## Package layout

```
genereview_link/
├── api/
│   ├── routes/              # FastAPI route handlers = the MCP tool surface
│   │   ├── search.py        #   search_genereviews
│   │   ├── passages.py      #   search_passages, get_passage, get_passages_batch
│   │   ├── search_batch.py  #   search_passages_batch
│   │   ├── chapters.py      #   get_chapter_metadata, get_chapter_section
│   │   ├── tables.py        #   get_table
│   │   ├── abstract.py      #   get_abstract
│   │   ├── links.py         #   get_links
│   │   ├── fulltext.py      #   get_fulltext
│   │   ├── genereview.py    #   get_genereview_summary
│   │   └── license.py       #   get_license
│   ├── eutils_client.py     # NCBI E-utilities client + Bookshelf scraping
│   └── client_manager.py    # Singleton client lifecycle
├── corpus/                  # Bundle restore, NXML parsing
├── db/                      # Postgres schema + control/data migrations
├── ingest/                  # Download → parse → write → swap pipeline
├── retrieval/               # Hybrid lexical + dense passage search
├── mcp/                     # Envelope, domain tags, guards, prompts, resources
├── models/                  # Pydantic models and validation
├── services/                # Business logic with caching
├── http_security.py         # Host/Origin admission
├── config.py                # Pydantic settings — the config source of truth
└── logging_config.py        # Structured logging

server.py                    # REST + MCP entry point
mcp_server.py                # stdio MCP entry point (backwards compatible)
```

## Design

- **Singleton lifecycle.** Clients and services are created once and shared
  (`client_manager.py`, `service_manager.py`). **stdio runs the same lifecycle state as
  HTTP** — there is no second, divergent startup path.
- **Async throughout**, with connection pooling. The Postgres `search_path` is set at pool
  creation, not per query, and asyncpg pool defaults are production-tuned.
- **Two-layer caching.** Service methods use `@alru_cache` (async LRU) with TTLs actually
  applied from `CACHE_SIZE` / `CACHE_TTL_HOURS`; decorators are attached at runtime so the
  cache is configurable. The client caches too. Eviction is by both size and time.
- **Distributed rate limiting.** Multi-worker coordination through a shared state file
  (`RATE_LIMIT_STATE_FILE`), so N workers still respect one NCBI budget.
- **Fail-closed HTTP boundary.** Exact Host/Origin allowlists on every route; wildcards
  rejected.

## Retrieval

The corpus is chapters split into **passages**, each with a stable `passage_id`
(`NBK1247:0042`), a section path, a `passage_role` (evidence, cross-reference, definition,
table caption, table body) and a `chapter_last_updated` date. Search is **hybrid**:
lexical + phrase + dense (BGE-small-en-v1.5 embeddings over pgvector, HNSW index), with
role and section-intent boosts. `scripts/bench_ranking.py` and `tests/eval/` guard ranking
quality; `make eval` reports MRR@10 and section-precision@5.

`GENEREVIEW_EAGER_LOAD_BGE=false` (the default) swaps in a fake embedding provider so the
server boots fast without GPU/Postgres resources — semantic search needs it set `true`.

## Scraping (NCBI Bookshelf)

There is no API for chapter bodies, so `get_fulltext` scrapes Bookshelf HTML.

- **Hierarchical extraction** preserves nested document structure rather than flattening it.
- **Multiple fallback strategies** per field; a failed section degrades gracefully instead
  of failing the request.
- **Browser-like headers**, realistic retry patterns, and exponential backoff on 403/429.
- Scraping uses **3× longer delays** than the E-utilities path.

The scraper is fragile by design (it tracks someone else's HTML). Per `AGENTS.md`: when
changing selectors, refresh the fixtures under `tests/fixtures/` and re-run the scraper
integration tests. XML parsing uses `defusedxml`, never `xml.etree.ElementTree`.

## Observability

Structured JSON logs (`LOG_JSON=true`) with correlation IDs (`CORRELATION_ID_HEADER`,
default `X-Request-ID`), request timing, cache-hit rates, and Prometheus metrics
(`ENABLE_METRICS`). Full request-lifecycle tracing by request id.

## Error handling

`DataNotFoundError` carries missing-resource conditions with descriptive context. 403/429
are retried with exponential backoff. Caller-visible MCP error messages are **fixed,
server-authored strings**: upstream error-body text and exception detail are never echoed
back, and messages are sanitized of control/zero-width/bidi/NUL code points.

## The REST surface

The same service layer, over HTTP. Interactive docs at `/docs`, OpenAPI schema at
`/openapi.json`, health at `/health`.

| Endpoint | Purpose |
|---|---|
| `GET /genereview/{gene_symbol}` | Convenience orchestration. Query: `include_abstract`, `include_links`, `include_fulltext` (default `false`), `max_chars` (default `16000`; `0` disables the cap), `fresh` |
| `GET /search/{gene_symbol}` | Search GeneReviews by gene symbol. Query: `retmax` (default 20) |
| `GET /abstract/{pmid}` | Abstract and metadata for a PubMed article |
| `GET /links/{pmid}` | All available links (Bookshelf, PMC, external) |
| `GET /fulltext/{nbk_id}` | Scraped chapter with hierarchical sections. Query: `sections` (comma-separated keys; **fuzzy substring match** — `summary` matches both `summary` and `clinical_summary`) |
| `GET /passages/search` | Corpus passage search (503 until a corpus is loaded) |
| `GET /health` | Health check. Query: `test_connection` |

`/genereview/{gene}` is **lean by default**: fulltext is opt-in. When included, the
`max_chars=16000` cap truncates large chapters, stamps `_meta.truncated`, and emits
`next_commands` pointing at `get_chapter_section`. Pass `max_chars=0` to disable the cap.

```bash
# Lean BRCA1 envelope: abstract + links only
curl "http://localhost:8000/genereview/BRCA1"

# Opt into fulltext (capped, then uncapped)
curl "http://localhost:8000/genereview/BRCA1?include_fulltext=true"
curl "http://localhost:8000/genereview/BRCA1?include_fulltext=true&max_chars=0"

curl "http://localhost:8000/search/TP53?retmax=5"
curl "http://localhost:8000/abstract/20301552"
curl "http://localhost:8000/links/20301552"
curl "http://localhost:8000/fulltext/NBK1246"

# Only the summary and diagnosis sections (fuzzy substring match)
curl "http://localhost:8000/fulltext/NBK1246?sections=summary,diagnosis"

# Health check with upstream connectivity test
curl "http://localhost:8000/health?test_connection=true"
```

## Decomposition backlog

Module size is capped at 600 lines (`make lint-loc`); oversized files are grandfathered in
`.loc-allowlist`. The active decomposition backlog (EutilsClient split, `passages.py`
split) is tracked in `.planning/2026-05-25-senior-engineering-review.md`. See `AGENTS.md`
§ File Size Discipline.
