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

### Fleet deploy contract

The fleet controller (`strato_v6_docker_npm`) deploys `docker/docker-compose.yml` +
`docker/docker-compose.prod.yml` + `docker/docker-compose.npm.yml` layered, in that order.
`container-release.json` (`service.deployed_compose_files`, `deployed_seed_binds`,
`deployed_sidecars`) declares that exact set so the shared release workflow's
`validate-deployed-overlay` gate checks the stack actually deployed rather than the npm
overlay alone (see [AGENTS.md § Fleet Deploy Contract](../AGENTS.md#fleet-deploy-contract)
for the numeric-`user` rule and release checklist). Self-check before tagging:

```bash
# from a genefoundry-router checkout pinned at the SHA in container-release.yml
uv run python scripts/container_release.py validate-deployed-overlay \
  --config <this-repo>/container-release.json --project-dir <this-repo>
```

### Corpus restore (production)

Production does **not** use any of the three corpus-loading modes in [data.md](data.md).
The corpus is an **immutable, digest-pinned GitHub data release**, restored exactly once
into the Postgres volume by the `genereview-corpus-restore` init sidecar:

- The sidecar has **no route off the internal network** — it cannot fetch release assets
  itself. Point `CORPUS_SEED_DIR` at a host directory already holding the exact artifact
  shape named in [`container-release.json`](../container-release.json), mounted read-only
  at `/seed`.
- The current immutable pin is the legacy `corpus-bundle.tar.gz` release and uses
  `CORPUS_BUNDLE_SHA256`. After the owner-authorized exact-eight release is published and
  verified, the pin selects `asset_name: corpus.dump`; the seed then contains exactly
  `corpus.dump`, `manifest.json`, and `SHA256SUMS`, and the three corresponding digest
  variables are required. No source-only change may point production at unpublished assets.
- Both shapes verify every byte before restore. Only a manifest-v3 direct release may write the
  readiness record, which identifies the verified inner `corpus.dump` and the configured release
  tag, manifest digest, and checksums digest. Every later start must match that complete direct tuple;
  changing any configured direct asset or tag fails closed instead of accepting stale readiness.
  A legacy restore remains
  runnable but deliberately produces no controller readiness claim.
- The restore runs as `RESTORE_ROLE` (`NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`), which
  may write the corpus tables and nothing else. Reviewed in-repo migrations run as the
  owner; the untrusted artifact is loaded with the least rights that can load it.
- **The serving application has no restore path at all** and never downloads anything.
- Both the artifact and its digest fail closed. An absent seed file, and an absent,
  malformed, or **placeholder** digest (64 zeroes, 64 `f`s, the empty file's digest) are
  each refused. A placeholder is refused *by identity*, before any comparison: a checksum
  that verifies nothing while presenting itself as verification is worse than none.

### Embedding provider (production)

Semantic ranking is only meaningful when the query and the corpus were embedded by the same
model, so production runs the pinned `BAAI/bge-small-en-v1.5` weights through ONNX Runtime.
The weights are **not** image content: they are staged beside the corpus under
`CORPUS_SEED_DIR` and materialised once into the `genereview_model_data` volume by the same
no-egress init sidecar, then mounted read-only by the server and re-verified before load.

Stage them once per pin:

```bash
genereview-link model stage --output /srv/genefoundry/genereviews-seed/model
```

Production refuses to start on the stub provider unless
`GENEREVIEW_ALLOW_FAKE_EMBEDDINGS=true`; when a non-reference provider is active the dense
path is disabled, `rerank_used` reports `lexical`, `_meta.dense_model_id` reports the stub,
and `/health` reports `degraded`. See
[data.md § Embedding provider](data.md#embedding-provider).

`GENEREVIEW_LINK_IMAGE` must be a digest-pinned image
(`ghcr.io/berntpopp/genereviews-link@sha256:…`); the prod overlay fails closed if it is
unset. `container-release.json` is the machine-readable contract (`data-bound`, with the
pinned corpus release tag and digest) that the release workflow asserts against.

`definitions.contract: data-bound` requires the service definitions eventually to bind accepted
data evidence. The current legacy runtime is explicitly `data_identity_contract: unadopted`: it
can start safely, but it is not controller-deployable evidence. Only a future verified direct
`corpus.dump` pin changes that state and may produce a `verified-v1` readiness record.

## Resource budget

| Component | Approximate RAM |
|---|---|
| Python + FastAPI baseline | ~150 MB |
| BGE-small-en-v1.5 model (`GENEREVIEW_EAGER_LOAD_BGE=true`) | ~130 MB |
| asyncpg pool (20 connections, default max) | ~100 MB |
| Postgres `shared_buffers` (self-hosted) | ~1 GB |
| **Total recommended** | **3 GB** |

The production compose overlay caps the app service at 3 GB / 1.0 CPU. Leave
`GENEREVIEW_EMBEDDING_PROVIDER=fake` for API-key-only or "lite" deployments (which
disables dense ranking outright rather than degrading it); `onnx` (the production default)
runs the real model. Measured on the built image under full confinement: verify + load
310 ms, warm query 6.9 ms, ~26 MB added to the image and 127 MiB held in a volume.

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
additionally exercises upstream connectivity. It also reports the active corpus's identity
and `data_as_of`, going `degraded` once the corpus is older than `CORPUS_MAX_AGE_DAYS` — see
[data.md § Corpus freshness](data.md#corpus-freshness) for the exact payload and why it
exists (#145).
