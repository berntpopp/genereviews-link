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
- `docker-compose.prod.yml` — read-only root FS, resource limits, gunicorn.
- `docker-compose.npm.yml` — Nginx Proxy Manager labels.

Layer overlays explicitly:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up --build
```

## Environment variables

See `.env.example` at the repo root. Notable:
- `NCBI_API_KEY` — strongly recommended for the higher NCBI rate limit.
- `GUNICORN_WORKERS` — default 2.
- `GENEREVIEW_LINK_PORT` — default 8000.
