# Deployment

How to run `genereviews-link` — transports, containers, corpus restore, and the resource
budget. Compose-overlay mechanics live in [`docker/README.md`](../docker/README.md); this
document is the operator's view and does not repeat them. Environment variables are
catalogued in [configuration.md](configuration.md); corpus loading in [data.md](data.md).

## Transports

One CLI, three transports (`--transport`, or `MCP_TRANSPORT`):

| Mode | Command | Surface |
|---|---|---|
| **unified** *(recommended for web)* | `uv run genereview-link serve --transport unified` | REST on `:8000` **and** MCP at `/mcp` |
| **stdio** *(local AI assistants)* | `uv run genereview-link serve --transport stdio` | MCP only, over stdio |
| **http** | `uv run genereview-link serve --transport http` | REST only |

Development: `uv run genereview-link serve --dev --transport unified` (or `make dev`).
`mcp_server.py` remains as a backwards-compatible stdio entry point
(`uv run python mcp_server.py`, exposed as the `genereview-link-mcp` console script).

For a self-managed process manager, `uv run uvicorn server:app --host 0.0.0.0 --port 8000`
also works.

### Registering the MCP endpoint

```bash
# hosted / local HTTP
claude mcp add --transport http genereview-link http://127.0.0.1:8000/mcp
```

Claude Desktop, over stdio:

```json
{
  "mcpServers": {
    "genereview-link": {
      "command": "uv",
      "args": ["run", "genereview-link", "serve", "--transport", "stdio"],
      "env": { "NCBI_API_KEY": "your_api_key_here" }
    }
  }
}
```

In stdio mode, **stdout is reserved for the JSON protocol** and all logs are routed to
stderr. Anything printed to stdout corrupts the protocol stream.

## Docker

```bash
make docker-build
make docker-up      # waits for the restore sidecar and a healthy app
curl http://localhost:8000/health
make docker-down
```

The production and NPM overlays run the unified CLI server, so REST and `/mcp` are served
by the same process:

```bash
genereview-link serve --transport unified --host 0.0.0.0 --port 8000
```

`docker/gunicorn_conf.py` remains available for custom Gunicorn deployments, but the
bundled production compose files do **not** use it and do not honour `GUNICORN_WORKERS`.

### Corpus restore (production)

Production does **not** use any of the three corpus-loading modes in [data.md](data.md).
The corpus is an **immutable, digest-pinned GitHub data release**, restored exactly once
into the Postgres volume by the `genereview-corpus-restore` init sidecar:

- The sidecar has **no route off the internal network** — it cannot fetch the bundle
  itself. Point `CORPUS_SEED_DIR` at a host directory already holding
  `corpus-bundle.tar.gz` from the release named in
  [`container-release.json`](../container-release.json), mounted read-only at `/seed`.
- `CORPUS_BUNDLE_SHA256` is the trust root: the bytes are verified **before** the archive
  is opened, and an empty value **fails closed**.
- The restore runs as `RESTORE_ROLE` (`NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`), which
  may write the corpus tables and nothing else. Reviewed in-repo migrations run as the
  owner; the untrusted artifact is loaded with the least rights that can load it.
- **The serving application has no restore path at all** and never downloads anything.

`GENEREVIEW_LINK_IMAGE` must be a digest-pinned image
(`ghcr.io/berntpopp/genereviews-link@sha256:…`); the prod overlay fails closed if it is
unset. `container-release.json` is the machine-readable contract (`data-bound`, with the
pinned corpus release tag and digest) that the release workflow asserts against.

## Resource budget

| Component | Approximate RAM |
|---|---|
| Python + FastAPI baseline | ~150 MB |
| BGE-small-en-v1.5 model (`GENEREVIEW_EAGER_LOAD_BGE=true`) | ~130 MB |
| asyncpg pool (20 connections, default max) | ~100 MB |
| Postgres `shared_buffers` (self-hosted) | ~1 GB |
| **Total recommended** | **3 GB** |

The production compose overlay caps the app service at 3 GB / 1.0 CPU. Leave
`GENEREVIEW_EAGER_LOAD_BGE=false` (the default) for API-key-only or "lite" deployments;
set it `true` only when semantic passage search is required.

For multi-worker deployments, set `RATE_LIMIT_STATE_FILE` so workers coordinate NCBI rate
limiting through a shared state file.

## Security posture

- The backend is **unauthenticated by design** and MUST be reachable only through the
  GeneFoundry router / reverse proxy — never published directly.
- Host and Origin allowlists are enforced on every HTTP route, and wildcards are rejected.
  See [configuration.md § Transport & HTTP boundary](configuration.md#transport--http-boundary).
- Vulnerability reporting and the operator-only repository settings (secret scanning, push
  protection) are in [`SECURITY.md`](../SECURITY.md).
- Container hardening — non-root, read-only rootfs, `cap_drop: ALL`, `no-new-privileges`,
  resource limits, digest-pinned bases — follows the fleet container-hardening standard.

## Health

`GET /health` is the liveness/readiness probe; `GET /health?test_connection=true`
additionally exercises upstream connectivity.
