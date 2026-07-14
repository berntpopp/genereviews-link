# Configuration

Every setting is an environment variable, read from the process environment or a `.env`
file (`genereview_link/config.py` is the single source of truth). Start from
[`.env.example`](../.env.example) for local development, or
[`.env.docker.example`](../.env.docker.example) for a production Docker / Nginx Proxy
Manager deployment.

```bash
cp .env.example .env
# or, for production Docker:
cp .env.docker.example .env.docker
```

## NCBI

| Variable | Default | Notes |
|---|---|---|
| `NCBI_API_KEY` | *(empty)* | Optional but recommended. Raises the NCBI limit from 3 req/sec (0.34 s delay) to 10 req/sec (0.11 s delay). |
| `EUTILS_BASE_URL` | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils` | NCBI E-utilities base URL. |

## Database (Postgres / pgvector)

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | *(empty)* | Corpus database. Empty ⇒ corpus-backed passage tools return 503. |
| `DATABASE_POOL_MIN_SIZE` | `2` | asyncpg pool floor. |
| `DATABASE_POOL_MAX_SIZE` | `20` | asyncpg pool ceiling; 20 gives production headroom. |
| `DATABASE_ACQUIRE_TIMEOUT_S` | `5.0` | Pool acquisition timeout. |
| `DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S` | `300.0` | How long an idle connection survives in the pool. |
| `DATABASE_COMMAND_TIMEOUT_S` | *(none)* | Per-command asyncpg timeout; `None` defers to asyncpg/Postgres defaults. |
| `DATABASE_STATEMENT_CACHE_SIZE` | `100` | asyncpg prepared-statement cache. **Set to `0` behind PgBouncer in transaction-pooling mode**, where prepared statements are unsafe across backend connection swaps. |

The Postgres `search_path` is configured at pool creation, not per query.

## Caching

| Variable | Default | Notes |
|---|---|---|
| `CACHE_SIZE` | `512` | LRU cache size. |
| `CACHE_TTL_HOURS` | `24` | Cache TTL. GeneReviews chapters do not change often. |

## Logging & observability

| Variable | Default | Notes |
|---|---|---|
| `LOG_LEVEL` | `INFO` | |
| `LOG_JSON` | `false` | Set `true` for JSON logs in production. |
| `ENVIRONMENT` | `development` | Environment name stamped into logging context. |
| `CORRELATION_ID_HEADER` | `X-Request-ID` | Correlation-ID request header. |
| `ENABLE_METRICS` | `true` | Prometheus metrics. |

## Transport & HTTP boundary

| Variable | Default | Notes |
|---|---|---|
| `MCP_TRANSPORT` | `unified` | One of `unified` \| `http` \| `stdio`. |
| `MCP_HOST` | `127.0.0.1` | |
| `MCP_PORT` | `8000` | |
| `MCP_PATH` | `/mcp` | |
| `MCP_ALLOWED_HOSTS` | `["localhost","127.0.0.1","::1"]` | JSON list of **exact** Host values. |
| `MCP_ALLOWED_ORIGINS` | `[]` | JSON list of **exact** browser Origin values. |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Comma-separated CORS response origins. |

> [!IMPORTANT]
> **Host and Origin validation is strict on every HTTP route.** Add reverse-proxy
> hostnames to `MCP_ALLOWED_HOSTS` as exact JSON strings — **wildcards are rejected**.
>
> Transport validation and browser CORS are **independent mechanisms**. A browser
> deployment must add its exact origin to **both** `MCP_ALLOWED_ORIGINS` (request
> admission) and `CORS_ORIGINS` (response headers). Setting only one of them is the
> classic misconfiguration here.

Production example (from `.env.docker.example`):

```bash
CORS_ORIGINS=https://genereviews-link.genefoundry.org
MCP_ALLOWED_HOSTS='["localhost","127.0.0.1","::1","genereviews-link.genefoundry.org"]'
MCP_ALLOWED_ORIGINS='["https://genereviews-link.genefoundry.org"]'
```

## Rate limiting

| Variable | Default | Notes |
|---|---|---|
| `RATE_LIMIT_STATE_FILE` | *(temp path)* | Shared state file for multi-worker rate-limit coordination. Set it explicitly for multi-worker production deployments. |

## Retrieval / RAG

| Variable | Default | Notes |
|---|---|---|
| `GENEREVIEW_EAGER_LOAD_BGE` | `false` | Load the BGE-small-en-v1.5 embedding model at boot (**~130 MB RAM**). Enable **only** when semantic passage search is required; when `false`, a fake embedding provider is used so the server starts fast without Postgres/GPU resources. |
| `DEBUG_RANKING_ENABLED` | `false` | Expose the `/debug/ranking` diagnostic endpoint. |

## Ingest (maintainer)

| Variable | Default |
|---|---|
| `INGEST_PARSE_WORKERS` | `8` |
| `INGEST_DB_WRITERS` | `4` |
| `INGEST_EMBED_BATCH_SIZE` | `256` |
| `INGEST_EMBED_WRITERS` | `2` |
| `INGEST_EMBED_DEVICE` | `auto` |

## Corpus loading

See [data.md](data.md) for what these mean and which combination to pick.

| Variable | Default | Notes |
|---|---|---|
| `BUNDLE_URL` | *(empty)* | `.tar.gz` release-asset URL, or `latest`. |
| `EXPECTED_BUNDLE_SHA256` | *(empty)* | **Security control.** Out-of-band, independently-trusted authenticity anchor. Empty ⇒ promotion is refused unless anchored in-repo. |
| `ALLOW_UNANCHORED_BUNDLE` | `false` | Knowingly accept transport-integrity-only bootstrap. |
| `BUNDLE_BOOTSTRAP_DIR` | `/tmp/genereview-link` | Writable download/extraction scratch. |
| `BUILD_LOCAL` | `false` | Run a full local ingest on first boot (15–30 min). |
| `GITHUB_REPO` | `berntpopp/genereviews-link` | Release resolution for `BUNDLE_URL=latest`. |
| `AUTO_PULL_RELEASES` | `false` | Start the hourly release watcher. |

### Immutable corpus artifact (production Docker)

Restored **once** by the no-egress `genereview-corpus-restore` init sidecar; the serving
app has no restore path and never downloads anything. See [deployment.md](deployment.md).

| Variable | Default | Notes |
|---|---|---|
| `CORPUS_SEED_PATH` | `/seed/corpus.tar.gz` | The reviewed bundle, mounted read-only. |
| `CORPUS_BUNDLE_SHA256` | *(empty)* | The digest committed in this repository — the trust root. Bytes are proven before the archive is opened. **Empty fails closed.** |
| `CORPUS_RESTORE_DIR` | `/var/lib/genereview/restore` | Writable scratch for archive expansion. |
| `RESTORE_DATABASE_URL` | *(empty)* | Restore-only connection, as an unprivileged role that may write the corpus tables and nothing else. |
| `RESTORE_ROLE` | `genereview_restore` | The `NOSUPERUSER` / `NOCREATEDB` / `NOCREATEROLE` role the init ensures before restoring. |
| `CORPUS_SEED_DIR` | *(compose)* | Host directory holding `corpus-bundle.tar.gz` from the release named in `container-release.json`. |
| `GENEREVIEW_LINK_IMAGE` | *(compose)* | Digest-pinned image, e.g. `ghcr.io/berntpopp/genereviews-link@sha256:…`. The prod overlay **fails closed** if unset. |
