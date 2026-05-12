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
# Keep BUNDLE_URL=latest to restore the newest promoted corpus release, or pin a release URL.
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

## Environment variables

See `.env.example` for local development and `.env.docker.example` for
production Docker/NPM deployments. Notable:

- `NCBI_API_KEY` — strongly recommended for the higher NCBI rate limit.
- `GENEREVIEW_LINK_PORT` — default 8000.
- `NPM_NETWORK_NAME` — external Docker network used by Nginx Proxy Manager.
- `BUNDLE_URL` — set to `latest` or a pinned GitHub Release asset URL to bootstrap a
  populated corpus without local ingest/backfill.

### Corpus Bundle Restore

Production Docker should restore a precomputed GitHub Release bundle:

```bash
BUNDLE_URL=latest
```

For reproducibility, pin the release asset URL instead of using `latest`.
Docker does not run ingest/backfill unless `BUILD_LOCAL=true` is explicitly set.

The production compose stack runs `genereview-link serve --transport unified`,
which preserves both REST and `/mcp` over HTTP. `docker/gunicorn_conf.py` remains
available for custom deployments, but the bundled production/NPM compose stack
does not use Gunicorn worker environment variables.
