# Docker

## Quick start (production-like)

```bash
make docker-build
make docker-up
curl http://localhost:8000/health
make docker-down
```

## Compose overlays

- `docker-compose.yml` — base service.
- `docker-compose.dev.yml` — adds bind mounts and uvicorn --reload.
- `docker-compose.prod.yml` — read-only root FS, resource limits, unified CLI server.
- `docker-compose.npm.yml` — Nginx Proxy Manager exposure without publishing host ports.

Layer overlays explicitly:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up --build
```

For a production Nginx Proxy Manager deployment, use `.env.docker` and layer the
production and NPM overlays:

```bash
cp .env.docker.example .env.docker
# Edit .env.docker: set POSTGRES_PASSWORD, CORS_ORIGINS, NCBI_API_KEY, and NPM_NETWORK_NAME.
# Stage the reviewed corpus release asset in CORPUS_SEED_DIR and set CORPUS_BUNDLE_SHA256
# to its published digest; both fail closed. See docs/data.md.
docker compose \
  --env-file .env.docker \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.prod.yml \
  -f docker/docker-compose.npm.yml \
  up -d --build
```

The NPM overlay attaches `genereview-link` to both the private compose network
and the external NPM network. The private network is required for the app to
resolve the `postgres` service hostname; the external network lets NPM proxy to
port 8000.

### Embedding backfill

Run as a one-off, healthcheck-disabled compose service. The service shares the
same image artifact as `genereview-link` (reuses the same build cache) and
skips the API health probe because the embed CLI does not bind port 8000:

```bash
docker compose --profile embed up genereview-link-embed
```

## Environment variables

See `.env.example` for local development and `.env.docker.example` for
production Docker/NPM deployments. Notable:

- `NCBI_API_KEY` — strongly recommended for the higher NCBI rate limit.
- `GENEREVIEW_LINK_PORT` — default 8000.
- `NPM_NETWORK_NAME` — external Docker network used by Nginx Proxy Manager.
- `CORPUS_SEED_DIR` / `CORPUS_BUNDLE_SHA256` — the staged corpus release asset and its
  published digest. Both fail closed.
- `GENEREVIEW_EMBEDDING_PROVIDER` — `bge` or `fake`. Production refuses the stub unless
  `GENEREVIEW_ALLOW_FAKE_EMBEDDINGS=true`.
- `BUNDLE_URL` — **inert**; the serving process has no restore path. Kept only for the
  release-watcher helpers.

### Corpus restore

The corpus is restored **once**, by the no-egress `genereview-corpus-restore` init sidecar,
from an artifact already staged on the host. The serving container downloads nothing.

```bash
CORPUS_SEED_DIR=/srv/genefoundry/genereviews-seed
CORPUS_BUNDLE_SHA256=<the digest published with the corpus release>
```

Docker does not run ingest/backfill unless `BUILD_LOCAL=true` is explicitly set.
See [../docs/data.md](../docs/data.md) for staging the asset and rebuilding a corpus.

The production compose stack runs `genereview-link serve --transport unified`,
which preserves both REST and `/mcp` over HTTP. `docker/gunicorn_conf.py` remains
available for custom deployments, but the bundled production/NPM compose stack
does not use Gunicorn worker environment variables.
