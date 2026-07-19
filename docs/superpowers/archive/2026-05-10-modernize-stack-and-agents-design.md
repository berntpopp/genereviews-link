# Modernize Stack and Agents Files — Design Spec

- **Date:** 2026-05-10
> Historical record

- **Repo:** genereviews-link
- **Reference:** pubtator-link (sibling repo at `../pubtator-link`)
- **Status:** Approved by user, ready for plan generation

## Goal

Bring genereviews-link to parity with pubtator-link's stack, build system, and
agents-tooling conventions. The two repos solve closely related problems (REST
+ MCP servers wrapping NCBI APIs) and should share one operational shape so
that one developer can move between them without context-switching toolchains.

This is a coordinated modernization, not a behavior change. The HTTP surface,
MCP tool surface, and service-layer semantics stay the same. Internals,
tooling, and process change.

## Non-goals

- No new endpoints or MCP tools.
- No changes to NCBI scraping logic, rate-limiting strategy, or cache shape.
- No database introduction (pubtator has one; we do not).
- No reorganization of the `genereview_link/` package layout beyond removing
  the custom logging middleware.

## Decisions (locked in during brainstorm)

| Area | Decision |
| --- | --- |
| Python minimum | 3.12 |
| Build backend | hatchling (replaces setuptools) |
| Package manager | uv with `uv.lock` (replaces pip + editable install) |
| Make targets | Mirror pubtator's Makefile |
| Linter | Ruff only (drop black, drop isort) |
| Type checker | mypy strict |
| Coverage threshold | 70 (pragmatic; pubtator is at 80) |
| Docker | Full stack (Dockerfile + dev/prod/npm compose) |
| CI | Full port (ci, security, docker, container-security, release, dependabot, PR template) |
| Agents files | AGENTS.md + slim CLAUDE.md split |
| docs/superpowers | Full scaffold (plans, specs, prompts, archive) |
| .claude/skills | Port ci-failure-triage, fastapi-route-change, mcp-tool-change, release-readiness; add ncbi-scraper-change; skip database-migration |
| Dep versions | Match pubtator pins exactly |
| New deps | defusedxml, asgi-correlation-id, prometheus-client, typer, rich, gunicorn |
| Correlation IDs | Replace custom middleware with asgi-correlation-id |
| Metrics | Add prometheus-client `/metrics` endpoint |
| CLI library | Switch argparse → Typer |
| FastMCP | Attempt v3.x first; fall back to v2.10.x if API surface diverges too far |
| XML parsing | Switch `xml.etree.ElementTree` → `defusedxml.ElementTree` (drop-in) |
| Legacy `mcp_server.py` | Keep (backwards compat); add `genereview-link-mcp` console script |

## Architecture changes

### Build and dependencies

- `pyproject.toml` rewritten with hatchling backend, PEP 735
  `[dependency-groups]`, `[project.scripts]` for `genereview-link` and
  `genereview-link-mcp`, ruff config with rule set
  `E,W,F,I,N,UP,B,C4,S,T20,SIM,RUF`, `line-length = 100`, strict mypy,
  pubtator-shaped `[[tool.mypy.overrides]]` blocks.
- Delete `mypy.ini` (config moves into `pyproject.toml`).
- Add `uv.lock` via `uv lock`.
- Add `Makefile` with targets: `install lock upgrade sync format format-check
  lint lint-ci lint-fix typecheck typecheck-fast typecheck-stop typecheck-fresh
  test test-fast test-unit test-integration test-cov test-all check ci-local
  precommit clean dev mcp-serve mcp-serve-http docker-build docker-up
  docker-down docker-logs`.
- pytest config drops automatic `--cov` from `addopts`; coverage runs only
  under `make test-cov`. Add `slow` and `integration` markers.

### Security and observability

- `genereview_link/api/eutils_client.py`: replace `from xml.etree import
  ElementTree as ET` with `from defusedxml import ElementTree as ET`. No
  call-site changes.
- Delete `genereview_link/middleware/logging_middleware.py` and the
  `middleware/` package if empty after.
- In `server_manager.py`, install `asgi_correlation_id.CorrelationIdMiddleware`
  with `header_name = settings.CORRELATION_ID_HEADER` (default
  `"X-Request-ID"`).
- In `logging_config.py`, add a structlog processor that reads
  `asgi_correlation_id.correlation_id` and injects it as `correlation_id`
  into each log record. Preserve current JSON log shape.
- Add `prometheus_client.make_asgi_app()` mounted at `/metrics`. Add
  request-count `Counter` and request-latency `Histogram` registered against
  the default registry. Skip for stdio transport.

### Config

`genereview_link/config.py` adds:

- `ENABLE_METRICS: bool = True`
- `CORRELATION_ID_HEADER: str = "X-Request-ID"`

All other settings unchanged.

### CLI

`genereview_link/cli.py` rewritten with Typer:

- One command, `serve`, with same flags as today: `--transport`, `--host`,
  `--port`, `--dev`, `--mcp-path`, `--disable-docs`, `--log-level`.
- `create_config_from_args()` becomes `build_config(**kwargs) -> ServerConfig`.
- `genereview-link` console script wired to the Typer app via
  `[project.scripts]`.
- `tests/test_cli.py` rewritten with `typer.testing.CliRunner`. Behavioral
  assertions stay the same (transport routing, host/port, dev mode flag).

### FastMCP 3 audit

- First task in Phase D runs a spike: install fastmcp 3.x in an isolated
  environment, import `FastMCP`, confirm `http_app(path=...)` and tool
  registration surface against current usage in `server_manager.py`.
- If the surface is compatible (or trivially adaptable), commit the
  `fastmcp >=3.2` pin.
- If not, pin `fastmcp >=2.10,<3.0` and document the deviation in this spec.

### Docker

`docker/` directory with:

- `Dockerfile` — multi-stage: uv build stage (resolves and exports wheel),
  slim runtime stage (`python:3.12-slim`) running `gunicorn -k
  uvicorn.workers.UvicornWorker server:app -c docker/gunicorn_conf.py`.
- `gunicorn_conf.py` — `workers = int(os.environ.get("WEB_CONCURRENCY",
  "2"))`, structlog-compatible log config.
- `docker-compose.yml` — base service.
- `docker-compose.dev.yml` — bind mount + `--reload` overlay.
- `docker-compose.prod.yml` — read-only FS, resource limits.
- `docker-compose.npm.yml` — Nginx Proxy Manager labels overlay.
- `docker/README.md` — usage notes for each compose combination.

### CI

`.github/workflows/`:

- `ci.yml` — matrix of py3.12 and py3.13 on ubuntu-latest, runs
  `make ci-local`.
- `security.yml` — weekly `uv pip audit`, ruff `S`-rule summary, scheduled
  cron.
- `docker.yml` — buildx build + smoke test on PRs touching `docker/**` or
  `Dockerfile`.
- `container-security.yml` — Trivy scan against built image, weekly cron + on
  docker.yml runs.
- `release.yml` — on tag push (`v*`), build wheel via `uv build`, publish to
  TestPyPI by default (flip to PyPI when ready).

Plus:

- `.github/dependabot.yml` — weekly checks for `pip` and `github-actions`.
- `.github/pull_request_template.md` — adapted from pubtator.

### Agents files

- New `AGENTS.md` at repo root — shared instructions: project description,
  source-of-truth rules, working rules, `make` command list, coding standards,
  testing notes. GeneReviews-specific additions: NCBI rate-limit awareness,
  scraper fragility caveats, no destructive cache ops on the public MCP.
- `CLAUDE.md` shrunk to:
  ```markdown
  # CLAUDE.md

  @AGENTS.md

  Claude Code entrypoint only:

  - Use `AGENTS.md` for shared repository instructions.
  - Keep Claude-specific additions here short and tool-specific.
  - Prefer `make ci-local` before final handoff.
  ```
  All current architecture content moves into `AGENTS.md`.

### docs/superpowers scaffold

```
docs/superpowers/
├── README.md        (one-paragraph index)
├── plans/
├── specs/           (this file lives here)
├── prompts/
└── archive/
```

### .claude/skills

Ported from pubtator-link (adapted to GeneReviews context):

- `ci-failure-triage/` — diagnostic flow for `make ci-local` failures.
- `fastapi-route-change/` — checklist when modifying a route.
- `mcp-tool-change/` — checklist when adding/changing an MCP tool.
- `release-readiness/` — pre-tag checklist.

New repo-specific:

- `ncbi-scraper-change/` — checklist when changing `eutils_client.py`
  scraping logic: rate-limit re-verification, BS4 selector regression check,
  refresh fixtures under `tests/fixtures/`.

Skipped (no fit): `database-migration` (no DB in this repo).

## Verification (definition of done)

- `make ci-local` green: format-check, lint-ci, typecheck-fast (strict), full
  test suite.
- `make docker-build && make docker-up` produces a healthy `/health` and
  `/metrics`.
- Manual stdio MCP smoke test: `python -m genereview_link.cli serve
  --transport stdio` round-trips at least one tool invocation.
- Unified-mode request smoke test: same correlation ID appears in (a) the
  `X-Request-ID` response header and (b) the corresponding structlog JSON
  line.
- Coverage report ≥ 70.

## Phased execution outline

Each phase ends with `make ci-local` green.

- **Phase A — Build/tooling base.** New `pyproject.toml`, `uv.lock`,
  `Makefile`, ruff/mypy config, remove `black`/`isort` and `mypy.ini`. Verify
  `make install` + `make lint` pass before touching code.
- **Phase B — Security + observability swaps.** `defusedxml` import,
  `asgi-correlation-id` + `prometheus-client` wiring, delete custom
  middleware.
- **Phase C — CLI to Typer.** Rewrite `cli.py`, console scripts, tests.
- **Phase D — FastMCP 3 spike + upgrade.** Audit, adapt `server_manager.py`,
  lock pin decision.
- **Phase E — Agents files + docs scaffold.** `AGENTS.md`, slim `CLAUDE.md`,
  `docs/superpowers/`, `.claude/skills/`.
- **Phase F — Docker.** Dockerfile, gunicorn conf, compose files.
- **Phase G — CI.** Port workflows, dependabot, PR template.
- **Phase H — Coverage + final verification.** Raise threshold to 70, run
  golden gate.

## Risks

- **FastMCP 2 → 3 API divergence.** Spike in Phase D before committing.
  Documented fallback: pin `fastmcp >=2.10,<3.0`.
- **CLI rewrite test brittleness.** Write Typer-based tests before deleting
  argparse-era tests.
- **Coverage threshold jump 59 → 70.** Real possibility some current tests
  don't reach 70. Mitigation: run `make test-cov` after migration and address
  the largest gaps; if still under 70, lower threshold to current floor and
  open a follow-up to raise it.
- **Log shape change from middleware swap.** Add a regression test that an
  injected `X-Request-ID` propagates to the structlog record.

## Out of scope (deferred follow-ups)

- Raising coverage from 70 → 80.
- Adding embeddings or RAG-style enrichment (pubtator has these, we do not
  need them).
- Database introduction.
- Repository monorepo merge with pubtator-link.
