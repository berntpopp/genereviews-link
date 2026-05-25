# GeneReview-Link — Senior Engineering Review

**Date:** 2026-05-25
**Commit reviewed:** `acf9279` (main)
**Reviewer:** Claude (Opus 4.7) acting as senior software / MCP engineer
**Scope:** `.understand-anything` knowledge graph (601 nodes / 969 edges / 10 layers) + targeted deep-read of the API, service, retrieval, ingest, and MCP server modules

This is a fundamentally solid system. The gaps below are mostly the "second 80%" of polish you'd expect from a pre-1.0 project that is already in the air. None of the P0s are deep architectural mistakes; they're the kind of bugs that surface only after the system meets real production traffic patterns.

Findings are prioritized P0 (bugs / correctness), P1 (high-impact perf), P2 (architecture / MCP). Each has file:line refs and a concrete fix.

---

## P0 — Bugs / Latent Correctness Issues

### 1. `CACHE_TTL_HOURS` is read but **never applied** → cache never expires

**Location:** `genereview_link/services/genereview_service.py:43-52`

```python
self.cache_ttl = timedelta(hours=settings.CACHE_TTL_HOURS)  # dead variable
self.get_genereview = alru_cache(maxsize=settings.CACHE_SIZE)(self._get_genereview_impl)
```

`async_lru==2.3.0` exposes a `ttl` kwarg — confirmed signature: `alru_cache(maxsize, *, ttl, jitter)`. Today the TTL is silently dropped: scraped GeneReview content lives in-process until LRU eviction or restart. Worse, the cache is **not invalidated on corpus version swap** (see #6), so after `atomic_swap` you can serve stale chapters for hours.

**Fix:**
```python
ttl_seconds = settings.CACHE_TTL_HOURS * 3600
self.get_genereview = alru_cache(maxsize=settings.CACHE_SIZE, ttl=ttl_seconds)(self._get_genereview_impl)
```
Drop the unused `self.cache_ttl` attribute.

---

### 2. STDIO MCP transport runs without the DB pool, embedder, or gene_index → all `/passages/*` tools 503

**Location:** `genereview_link/server_manager.py:580-592`

`start_stdio_server` manually re-creates client + service but **never executes the `lifespan` body** (lines 190-289). That body owns:
- Postgres pool + repository (the whole retrieval/RAG surface)
- `app.state.embedder`
- `app.state.gene_index`
- `app.state.corpus_version`
- the release-watcher scheduler

**Impact:** An MCP client over stdio gets a server where `search_passages`, `get_passage`, `get_chapter_metadata`, and `get_chapter_section` all return 503 because `app.state.repository is None`. This silently breaks the canonical pipeline documented in the MCP `instructions` string.

**Fix:** Extract the lifespan body into `async def _initialize_state(app: FastAPI)` and call it from both `lifespan` and `start_stdio_server`. Mirror in shutdown.

---

### 3. `SentenceTransformerEmbeddingProvider` double-load race

**Location:** `genereview_link/retrieval/embeddings.py:139-159`

```python
def _ensure_model(self):
    if self._model is not None and self._np is not None:
        return ...
    # ... loads SentenceTransformer (~130MB) ...
```

No lock. Two concurrent `embed_query` calls on first request both pass the guard, both load BGE, second one overwrites — 130MB wasted, possible OOM on small containers. The default config `GENEREVIEW_EAGER_LOAD_BGE=False` (`config.py:73`) means HTTP-only deployments hit this on first traffic burst.

**Fix:** wrap with `asyncio.Lock`:
```python
def __init__(...):
    self._load_lock = asyncio.Lock()
async def _encode(self, texts):
    async with self._load_lock:
        model, np = self._ensure_model()
    ...
```

---

### 4. `tarfile.extractall` runs **before** manifest checksum verification + no `filter="data"`

**Location:** `genereview_link/server_manager.py:118-124`

```python
with tf_mod.open(tmp, "r:gz") as tar:
    tar.extractall(str(extract_dir))  # noqa: S202
manifest = json.loads(...)
for relpath, expected in manifest["checksums"].items():
    ...
```

Two issues:
1. The whole point of the manifest is wasted — files land on disk before per-file SHAs are checked. The outer `.sha256` from `fetch_sibling_sha256` is your only real guard, and it covers the tarball blob, not contents-vs-manifest mismatch.
2. Python 3.12+ requires `filter=` to silence a `DeprecationWarning`; Python 3.14 makes it required. Without it, hostile members can write outside `extract_dir` via absolute paths / symlinks (CVE-2007-4559 family).

**Fix:**
```python
tar.extractall(str(extract_dir), filter="data")
```
Re-order: verify manifest hashes against the in-tarball file objects (`tar.extractfile(member)`) before writing to disk, or extract to a quarantine dir then move.

Bonus: `jobs=os.cpu_count()` (line 128) can be `None` on exotic systems — pass `os.cpu_count() or 2`.

---

### 5. Release watcher fires every hour but does nothing

**Location:** `genereview_link/ingest/scheduler.py:30-33`

```python
if settings.AUTO_PULL_RELEASES:
    pass  # implementation extends Task 6.3 bootstrap into a hot-swap path
```

The scheduler is started in lifespan (`server_manager.py:284-287`), takes a Postgres advisory lock, resolves the latest release URL, logs — and **does not act**. Either ship the hot-swap path or remove the scheduler. As-is, it's a footgun: operators read the log line "release watcher fired" and assume the system is auto-updating.

---

### 6. `app.state.corpus_version` and `app.state.gene_index` go stale after swap

**Location:** `genereview_link/server_manager.py:235, 253` (set once in lifespan)

After `atomic_swap` (`pipeline.py:76-132`) the new version is active, but `app.state.corpus_version` and `app.state.gene_index` are frozen at startup. Every response's `_meta.corpus_version` field is wrong, and `is_indexed("NEWGENE")` returns False for genes added by the new corpus.

**Fix:** Add a `RepositoryRefresher` invoked by the scheduler after a successful swap. Cheap: it's two repository calls.

---

### 7. `recall_tsquery` "no-match" sentinel actually matches

**Location:** `genereview_link/retrieval/lexical.py:79-81`

```python
if not terms:
    return "x:*"  # safe, matches nothing meaningful but parses
```

`x:*` is a prefix match for any token starting with `x` — matches `XIST`, `xanthoma`, etc. For a query of only stop words this returns recall hits that look spurious.

**Fix:** Return a guaranteed-no-match token (`__zzz_no_match__:*`) or upstream, when `not terms`, skip the recall branch entirely in `search_passages` SQL.

---

### 8. `EutilsClient._make_web_request` ignores 429 and `Retry-After`

**Location:** `genereview_link/api/eutils_client.py:130-169`

The retry loop special-cases 403 with exponential backoff. **429** falls into the catch-all `except Exception` branch, where the wait is `(2**attempt) * self.rate_limit_delay * 2` — far below what NCBI Bookshelf asks for under load. There is no `Retry-After` header parsing.

**Fix:**
```python
except httpx.HTTPStatusError as e:
    if e.response.status_code == 429:
        retry_after = float(e.response.headers.get("retry-after", 5))
        await asyncio.sleep(retry_after)
        continue
    if e.response.status_code == 403 and attempt < max_retries - 1:
        ...
```

---

### 9. `DistributedRateLimiter` mixes `threading.Lock` with `await asyncio.sleep` and has a TOCTOU file race

**Location:** `genereview_link/api/client_manager.py:39-95`

```python
def __init__(self, ...):
    self._lock = threading.Lock()
async def wait_if_needed(self):
    with self._lock:
        ...
        await asyncio.sleep(wait_time)  # holding threading.Lock across an await
```

Two real problems:
- The single-worker fast path holds the threading lock across `asyncio.sleep`, blocking any other task that needs the lock for the duration of the rate-limit wait. With several concurrent tool calls this serializes them needlessly — the lock should be released before the sleep, or use `asyncio.Lock`.
- The multi-worker file path reads, computes, then writes the shared file with no `fcntl.flock` — two gunicorn workers can both see "ok to fire" and burst above NCBI's limit. Also no fsync.

**Fix:** Use `asyncio.Lock` for in-process; for inter-process coordination, use Postgres advisory locks (the codebase already does this in `ingest/scheduler.py:14`) — they're transactional, atomic, and you already have the pool.

---

### 10. Bare `raise Exception(...)` swallows error class

**Location:** `genereview_link/api/eutils_client.py:113, 117, 215, 219`

```python
if e.response.status_code == 429:
    raise Exception("Rate limit exceeded...")
```

Callers cannot distinguish "rate-limited (retry later)" from any other failure. The lifespan error handlers and the orchestration layer cannot route to the right HTTP status. Define `NcbiRateLimitError`, `NcbiAccessForbiddenError` in `api/errors.py` (which already exists) and raise those.

---

## P1 — High-Impact Performance Wins

### 11. `set search_path` per query — drop one round-trip per repository call

**Location:** `genereview_link/retrieval/repository.py:342, 437, 454, 468, 488, 512, 596, 662, 746, 781, 805, 845, 863`

Every read path executes `await conn.execute("set search_path to genereview, public")` before the actual query. On a hot path (search → fanout → snippet) this is one extra network round-trip per acquired connection. **Move it into `db/pool.py:_init_conn` once per connection:**

```python
async def _init_conn(conn):
    with contextlib.suppress(ValueError):
        await pgvector.asyncpg.register_vector(conn)
    await conn.execute("set search_path to genereview, public")
```

The ingest path correctly uses `SET LOCAL search_path` inside a transaction (`pipeline.py:237`), which wins over the connection default — safe to do this.

**Expected latency reduction:** ~1-3ms per HTTP request, more on geographically distant Postgres.

---

### 12. Pool defaults are not production-tuned

**Location:** `genereview_link/db/pool.py:25-42` and `genereview_link/config.py:32-34`

`asyncpg.create_pool(dsn, min_size=2, max_size=10, init=_init_conn)` — missing:
- **`max_inactive_connection_lifetime=300`** — without it, PgBouncer/cloud-Postgres firewalls kill idle connections and the next acquire raises mid-request.
- **`command_timeout`** — a pathological query can pin a connection forever.
- **`statement_cache_size=0`** if behind PgBouncer in transaction pooling mode (asyncpg's default prepared-statement cache breaks under txn-mode pgbouncer).

Also `DATABASE_POOL_MAX_SIZE=10` is tight for unified mode (HTTP + MCP + parallel-retrieval which acquires multiple conns per request). Recommend `min=5, max=20` and document.

---

### 13. `_dense_candidates_filtered` exact-KNN bypass branch never sets `enable_seqscan` off

**Location:** `genereview_link/retrieval/repository.py:182-191`

For the single-chapter bypass branch:
```sql
order by e.embedding <=> $1::vector
limit $top_k
```
Without HNSW (since the chapter is small), Postgres may still pick a seq scan + sort over a btree-indexed `nbk_id` filter. Verify with `EXPLAIN`; if seq-scan is chosen, an index on `(nbk_id, passage_id) include (embedding)` or `SET LOCAL enable_seqscan = off` inside the txn would force the index path. Probably fine for ~200 passages/chapter; check on the long tail (the 2-3 largest chapters).

---

### 14. `embed_passages` ignores `passage_type` at the public API surface

**Location:** `genereview_link/retrieval/embeddings.py:127-128`

```python
async def embed_passages(self, texts: list[str]) -> list[list[float]]:
    return await self._encode([bge_passage_text(t) for t in texts])
```

The orchestrator pre-applies `bge_passage_text(text, passage_type=ptype)` before calling this method (`ingest/orchestrator.py:84-86`), so today it's correct **by accident** — the inner default `passage_type="narrative"` is a no-op for already-truncated table text. But any future caller (e.g. ad-hoc indexing scripts) who passes raw passages will silently lose table truncation. Fix the signature:

```python
async def embed_passages(self, texts: list[str], *, passage_types: list[str] | None = None) -> ...:
    prepared = [
        bge_passage_text(t, passage_type=pt or "narrative")
        for t, pt in zip(texts, passage_types or ["narrative"] * len(texts), strict=True)
    ]
    return await self._encode(prepared)
```

Remove the redundant pre-application in the orchestrator.

Minor: `np.asarray(vectors, dtype=float).tolist()` (line 135) casts float32 → float64 → Python floats. Just do `vectors.tolist()` (SentenceTransformer returns float32 already, asyncpg/pgvector both accept it).

---

### 15. Scraped HTML has no persistent cache

**Location:** `EutilsClient.scrape_genereview_book` / `scrape_genereview_comprehensive`

`alru_cache` is process-local — every gunicorn worker restart re-scrapes NCBI. For an MCP server where a typical interaction touches 1-3 chapters, the cache hit rate across worker reboots is near-zero.

**Recommendation:** Add `hishel` (built on httpx) as an opt-in HTTP cache pointing at a local SQLite or filesystem cache. For corpus-only deployments (the main path) this barely matters because `/passages/*` reads from Postgres, but the `/genereviews/{gene}` and `/abstract` paths benefit hugely. Gate on `settings.HTTP_CACHE_ENABLED`.

---

### 16. `EutilsClient` is 1,327 LOC — a god class hindering test isolation

**Location:** `genereview_link/api/eutils_client.py`

Mixes: rate-limited HTTP layer, JSON + XML response parsing, BeautifulSoup HTML scraping, regex-based reference parsing, and content normalization. The scraper alone has 7 fallback strategies for content discovery (lines 641-697). Split:
- `api/eutils_client.py` — pure E-utils JSON/XML.
- `scraping/bookshelf_scraper.py` — `_find_main_content`, `_extract_*`, `_clean_content`.
- `scraping/reference_parser.py` — `_extract_references`, `_parse_reference`.

This unlocks targeted fixture-based unit tests (the current scraper integration tests at `tests/fixtures/` are slow and brittle by design — splitting lets you fuzz the parser cheaply).

---

### 17. `cleanup_old` acquires one connection per drop

**Location:** `genereview_link/corpus/pipeline.py:135-153`

```python
for row in rows[retain:]:
    async with pool.acquire() as conn:
        await conn.execute(f'drop schema "{row["schema_name"]}" cascade')
```

Use one connection for the whole loop. Also: `DROP SCHEMA … CASCADE` on a large old corpus can block writers for seconds — wrap with a `lock_timeout` to fail fast if a long query is holding refs:
```python
await conn.execute("set local lock_timeout = '5s'")
```

---

## P2 — MCP / Architecture

### 18. `/metrics` is exposed as an MCP tool by default

**Location:** `genereview_link/server_manager.py:447-454`

The `route_maps` EXCLUDE list covers `/debug/`, `/health`, `/`, `/docs`, `/openapi.json` — **but not `/metrics`** (registered at `_add_utility_endpoints`, line 427). `FastMCP.from_fastapi` walks all GET routes by default; Prometheus scrape format text is then exposed as a tool an LLM might call. Adds noise to the tool list and is useless to clients.

**Fix:**
```python
mcp_route_maps = [
    RouteMap(pattern=r"^/debug/", mcp_type=MCPType.EXCLUDE),
    RouteMap(pattern=r"^/health$", mcp_type=MCPType.EXCLUDE),
    RouteMap(pattern=r"^/metrics$", mcp_type=MCPType.EXCLUDE),
    RouteMap(pattern=r"^/$", mcp_type=MCPType.EXCLUDE),
    RouteMap(pattern=r"^/docs$", mcp_type=MCPType.EXCLUDE),
    RouteMap(pattern=r"^/openapi.json$", mcp_type=MCPType.EXCLUDE),
]
```

---

### 19. `mcp_custom_names` dict is dead code

**Location:** `genereview_link/server_manager.py:435-444`

Every entry maps a name to itself — that's the FastMCP default. The whole dict is a no-op and tricks readers into thinking custom names are being applied. Delete it; document any future renames in `instructions` instead.

---

### 20. MCP `instructions` and the actual tool surface drift

**Location:** `genereview_link/server_manager.py:459-478` and route registration

The canonical pipeline in `instructions` mentions `search_passages → get_chapter_metadata → get_passage / get_chapter_section / get_table / get_passages_batch`. It does **not** mention `get_genereview_summary` (route exists at `api/routes/genereview.py:109`) or `get_abstract`, `get_links`, `get_fulltext` (these are NCBI live-scrape paths, slower and more brittle). Mention them in a "legacy live-scrape" section or hide them from MCP via `RouteMap(..., mcp_type=MCPType.EXCLUDE)` to reduce LLM confusion.

Also add MCP tool annotations (`readOnlyHint=True` is appropriate for every route here, `openWorldHint=False` for cache-only / repo-only reads).

---

### 21. Service singleton can hand out a closed httpx client after `shutdown_clients`

**Location:** `genereview_link/api/client_manager.py:169-177` + `genereview_link/services/service_manager.py:50`

`ServiceManager` stores the `EutilsClient` reference on construction (`GeneReviewService(client=client)`). If `shutdown_clients()` runs first during a partial shutdown error path, `_client` is set to `None`, but `ServiceManager._service.client` still holds the original `EutilsClient` whose `httpx.AsyncClient` has been `aclose()`-d. A subsequent re-acquire of the service would return the stale wrapper. Today this is masked because shutdown is final and unrecoverable, but it's a footgun for any future hot-restart logic.

**Fix:** In `shutdown_services()`, run before `shutdown_clients()` (it already does — see `server_manager.py:295-296`), but also null out `ServiceManager._service.client = None` defensively, or have `GeneReviewService` re-fetch from `ClientManager` on each call.

---

### 22. `register_vector` exception scope is too narrow

**Location:** `genereview_link/db/pool.py:13-22`

```python
with contextlib.suppress(ValueError):
    await pgvector.asyncpg.register_vector(conn)
```

Pre-extension state can also surface `asyncpg.exceptions.UndefinedObjectError` (not a `ValueError`). Broaden:
```python
with contextlib.suppress(ValueError, asyncpg.exceptions.UndefinedObjectError):
    ...
```

---

### 23. `passages.py` route module is 741 LOC

**Location:** `genereview_link/api/routes/passages.py`

Mixes search, single-passage, batch, neighbor-window, and section endpoints. Splitting into `routes/passages_single.py`, `routes/passages_batch.py`, `routes/sections.py` would (a) reduce review surface per change, (b) let you put route-specific helpers next to the route, and (c) make MCP `RouteMap` patterns more obvious.

---

### 24. Long-tail: scraper-reference parser is brittle

**Location:** `genereview_link/api/eutils_client.py:874-1035`

The `_extract_references` regex pipeline has two fallback strategies and is order-sensitive (line 940: "If we didn't get many references, try alternative method"). Citations get mangled by regexes like `r"(?=\b[A-Z][a-z]+\s+[A-Z]{1,2}(?:[a-z]*)?(?:,\s*[A-Z][a-z]+\s+[A-Z]{1,2})?.*?\.\s)"`. Three options, in order of effort:
- Short-term: extract PMID-only (`PMID:?\s*(\d+)`) and rely on PubTator-Link / `efetch` to resolve the full citation downstream.
- Medium: pre-parse NCBI Bookshelf NXML references (the nxml path already exists at `corpus/nxml.py`) and store them in `genereview_chapters.references_jsonb`.
- Long: replace with `refextract` or similar specialized library.

---

## P2 — Smaller Bugs / Polish

| File:line | Issue | Fix |
|---|---|---|
| `server_manager.py:128` | `jobs=os.cpu_count()` can pass `None` | `os.cpu_count() or 2` |
| `server_manager.py:214,241,258` | `except Exception` swallows tracebacks with `error=str(exc)` only | Add `exc_info=True` to `logger.warning` |
| `corpus/parallel.py:42-53` | `_iter_tarball` reads each member fully then `del data` immediately — yielding the dataclass means the bytes live in the futures queue anyway | Cap `in_flight` length is good; consider streaming `extractfile` directly to the worker (pickle cost stays) |
| `retrieval/embeddings.py:135` | `np.asarray(..., dtype=float).tolist()` → float64 round-trip | `vectors.tolist()` |
| `corpus/pipeline.py:48-58` | Same-day re-ingest version picker does sync round-trips | Single SQL with `select coalesce(max(suffix), 0) + 1` |
| `services/genereview_service.py:72,180,193,213` | Several `scraped_data.pop(...)` paths assume specific dict shapes and will `KeyError`/`TypeError` on partial failures | Use `.pop(..., None)` and validate types |
| `api/eutils_client.py:23` | `warnings.filterwarnings(...)` at module import affects every module | Use a context manager around the specific BS4 calls |
| `api/orchestration.py:30` | `live_corpus_version()` returns `f"live:{datetime.now(UTC).isoformat()}"` — high cardinality if it lands in a Prometheus label anywhere | Verify it never gets used as a label |
| `db/pool.py:39-40` | min=2/max=10 — see #12 |
| `corpus/pipeline.py:163` | `TemporaryDirectory(dir=work_dir)` — if `work_dir` is None and `/tmp` is small, this OOMs disk during corpus extract | Make `work_dir` mandatory in production via config |

---

## Suggested Sprint Plan

If I had a week with this codebase, the work order is:

1. **Day 1** — Fix STDIO transport regression (#2) and cache TTL bug (#1). Both are functional issues users will hit.
2. **Day 2** — Move `set search_path` into `_init_conn` (#11), tune pool kwargs (#12), and add the `tarfile filter="data"` (#4). Touches one file each, low risk, big quality bump.
3. **Day 3** — Add `asyncio.Lock` to the embedding lazy load (#3), fix `DistributedRateLimiter` to use `asyncio.Lock` + Postgres advisory locks (#9), tighten `EutilsClient` 429/`Retry-After` handling (#8).
4. **Day 4** — Either finish or rip out the release watcher (#5); add `corpus_version` + `gene_index` refresh on swap (#6); exclude `/metrics` from MCP (#18); drop the `mcp_custom_names` dead config (#19).
5. **Day 5** — Split `EutilsClient` (#16) and start a `bookshelf_scraper` module with property tests, so the next NCBI HTML change doesn't take down ingest.

---

## What's Already Good (Worth Preserving)

- The **9-stage atomic ingest** (`pipeline.py`) with staging schema + `atomic_swap` is exactly right for a versioned corpus. The advisory-lock pattern in `scheduler.py:18-19` is the right idiom.
- **HNSW iterative scan** + filter-aware top-K dense candidates (`repository.py:127-211`) is a sophisticated retrieval design — the bypass for single-chapter exact-KNN is a nice touch.
- **`defusedxml` everywhere XML is parsed** (`eutils_client.py:17`, `nxml.py`) — AGENTS.md guidance is enforced.
- **NFC over NFKC** for scraped content normalization (`eutils_client.py:1292-1299`) with the inline rationale — exactly the kind of comment that earns its keep.
- The **RRF + intent + role multiplier** rerank pipeline (`rerank.py`) is well-factored and easy to extend.
- The knowledge graph itself (601 nodes, 10 clean layers, zero validation issues) reflects a codebase that has been kept disciplined.

---

## Appendix — Methodology

1. Loaded `.understand-anything/knowledge-graph.json` to map the 10-layer architecture and identify critical files.
2. Targeted read of high-risk modules: `eutils_client.py` (1327 LOC), `repository.py` (915 LOC), `server_manager.py` (618 LOC), `embeddings.py`, `pool.py`, `pipeline.py`, `parallel.py`, `orchestrator.py`, `client_manager.py`, `service_manager.py`, `genereview_service.py`, `scheduler.py`, `gene_index.py`, `lexical.py`, `rerank.py`, `chunking.py`, `config.py`.
3. Verified suspected issues with `grep` + `python` introspection (e.g., confirmed `async_lru.alru_cache` supports `ttl` kwarg in v2.3.0).
4. Cross-referenced AGENTS.md rules (defusedxml usage, NCBI rate limits, uv dependency mgmt) for compliance.

Findings are issues a senior engineer would flag in a careful code review of a system about to face real production load. They are **not** evidence of poor engineering — most of them are the kind of footguns that only become visible after the code meets reality.
