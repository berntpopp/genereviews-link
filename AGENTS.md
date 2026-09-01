# AGENTS.md

Shared repository instructions for agentic coding tools working in GeneReview-Link.

## Project

GeneReview-Link is a Python FastAPI and MCP server that searches, fetches,
and scrapes NCBI GeneReviews data via NCBI E-utilities and the NCBI
Bookshelf.

Primary areas:

- `genereview_link/` - Python package, FastAPI routes, services, client,
  MCP integration
- `tests/` - unit and integration tests
- `docker/` - Dockerfile and Compose deployment files
- `docs/superpowers/plans/` - implementation plans for agentic workers
- `docs/superpowers/specs/` - design specs for agentic workers
- `.claude/skills/` - repo-local Claude Code workflows for recurring tasks

## Source Of Truth

- Use this file for shared repo-wide agent guidance.
- Keep `CLAUDE.md` lean and Claude-specific; it should reference this file.
- Use repo-local `.claude/skills/` workflows when a task matches their scope.
- Prefer `Makefile` targets over ad hoc commands.
- Use `uv.lock` as the dependency lock source of truth.

## Working Rules

- Do not revert or overwrite changes you did not make unless explicitly asked.
- Keep edits scoped to the task and avoid unrelated refactors.
- Prefer existing code patterns over new abstractions.
- Put tests under `tests/`; do not create alternate test roots.
- Use ASCII unless a file already requires non-ASCII content.
- Respect NCBI rate limits. The EutilsClient already enforces 0.11s (with
  API key) or 0.34s (without) between requests. Do not bypass this.
- The NCBI Bookshelf scraper is fragile by design. When changing selectors,
  refresh fixtures under `tests/fixtures/` and re-run scraper integration
  tests.
- For MCP work, keep public hosted tools research-use scoped. No destructive
  cache operations on a public deployment.

## Commands

Required checks before claiming completion:

- `make ci-local`

Useful focused commands:

- `make install`
- `make lock`
- `make format`
- `make lint`
- `make lint-fix`
- `make lint-loc`
- `make typecheck`
- `make typecheck-fast`
- `make test`
- `make test-fast`
- `make test-unit`
- `make test-integration`
- `make test-cov`
- `make precommit`
- `make dev`
- `make mcp-serve`
- `make mcp-serve-http`
- `make docker-build`
- `make docker-up`
- `make docker-down`

## Coding Standards

- Use `uv` for dependency management; do not use direct `pip` installs.
- Use modern Python typing: `list[str]`, `dict[str, int]`, `str | None`.
- Format and lint Python with Ruff.
- Type check with mypy strict targeting Python 3.12.
- Keep FastAPI route behavior covered by route tests and service behavior
  covered by unit tests.
- All XML parsing must use `defusedxml`, never `xml.etree.ElementTree`.

## Fleet Deploy Contract

This service is deployed by an external fleet controller (`strato_v6_docker_npm`), which
renders `docker/docker-compose.npm.yml` on top of the base + prod overlays and requires an
explicit numeric `user:` on every service it deploys. `999:999` is *this image's own*
baked-in uid/gid (`docker/Dockerfile`: `groupadd --gid 999 app` / `useradd --uid 999
--gid 999`) — verify it against a changed Dockerfile rather than assuming it fleet-wide.

- **Two different compose file sets, two different contracts.**
  `docker/docker-compose.npm.yml` is what the fleet controller actually renders and
  deploys. `docker/docker-compose.yml` + `docker/docker-compose.prod.yml` are the files
  `container-release.json` (`service.compose_files`) gates in CI, and that release gate
  **forbids** a `user:` override on the application service there. The two are reconciled,
  not merged: every deployed service declares `user: "999:999"` in the NPM overlay only.
  Never move that line into the release-gated compose files.
- **Guard/projection test:** `tests/test_docker_compose_config.py` renders
  `docker/docker-compose.npm.yml` and asserts its overlay behavior (network wiring, tmpfs
  inheritance, etc.); extend it, don't duplicate it, when the overlay changes.
- **Release checklist**, enforced by this repo's own tests (see
  `tests/unit/test_version_single_source.py::test_citation_matches_current_changelog_release`):
  1. Bump `version` in `pyproject.toml`.
  2. `uv lock` — only this package's own version entry in `uv.lock` should change.
  3. Add a heading to the **root** `CHANGELOG.md` (not `docs/CHANGELOG.md`) reading exactly
     `## [x.y.z] - YYYY-MM-DD`.
  4. Update `CITATION.cff`: `version:` and `date-released:` must exactly equal the version
     and date from step 3 — the test above fails otherwise, despite the file's own
     "GENERATED, do not edit" header; this repo hand-edits it at release time regardless.
  5. Tag `vx.y.z` and push it; the container-release workflow builds, attests, and
     publishes the image.
  6. The release workflow's environment gate needs manual approval — a run can present
     more than one `pending_deployments` entry, so approve all of them rather than
     stopping after the first:
     ```bash
     rid=<run_id>
     for e in $(gh api repos/berntpopp/genereviews-link/actions/runs/$rid/pending_deployments --jq '.[].environment.id'); do
       gh api repos/berntpopp/genereviews-link/actions/runs/$rid/pending_deployments \
         -X POST -f state=approved -F "environment_ids[]=$e" -f comment='fleet release'
     done
     ```
     Poll until the run's conclusion is `success` and
     `gh release view vx.y.z --json isDraft,assets` shows `isDraft: false` with assets.
- **Corpus data bundle is separate from the application image.** A new corpus is computed
  locally on the maintainer's workstation (`make bundle-publish-local` /
  `genereview-link bundle build`), evaluated, and published as its own immutable GitHub
  data release (`corpus-data-YYYY-MM-DD-rN`) — never built on the VPS, and never fetched by
  the serving process, which has no restore path (#97; see
  `genereview_link/db/restore.py`). Two env keys in the server's `.env.docker` point a
  deployment at that published bundle: `CORPUS_RELEASE_TAG` (the exact release tag) and
  `CORPUS_BUNDLE_SHA256` (the legacy bundle digest published with it — a future
  direct/manifest-v3 release instead pins `CORPUS_DUMP_SHA256` + `CORPUS_MANIFEST_SHA256` +
  `CORPUS_CHECKSUMS_SHA256`). Both facts are also recorded in `container-release.json`
  (`data.release_tag`, `data.digest`) so CI and the fleet controller agree on which corpus
  is pinned. See [docs/deployment.md § Corpus restore](docs/deployment.md#corpus-restore-production)
  and [docs/data.md § Corpus freshness](docs/data.md#corpus-freshness).

## Postgres Connection

- `DATABASE_POOL_MIN_SIZE` and `DATABASE_POOL_MAX_SIZE` control asyncpg pool
  size. The default max is 20 for production headroom.
- `DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S` controls how long idle
  connections remain in the pool before asyncpg closes them. The default is
  300 seconds.
- `DATABASE_COMMAND_TIMEOUT_S` can set a per-command asyncpg timeout. The
  default `None` leaves timeout behavior to asyncpg/Postgres defaults.
- `DATABASE_STATEMENT_CACHE_SIZE` controls asyncpg's prepared statement cache.
  Keep the default 100 for direct Postgres connections. Set it to 0 when using
  PgBouncer in transaction-pooling mode, where prepared statements are unsafe
  across backend connection swaps.

## File Size Discipline

Hard cap: **600 lines per Python module** in `genereview_link/`, `server.py`,
and `mcp_server.py`. Enforced by `make lint-loc` (wired into `ci-local` and
pre-commit). Tests are exempt.

Why: large modules concentrate complexity, slow mypy and import cost, and
degrade LLM-assisted refactors (a single edit risks unrelated breakage).
When a file approaches 500 lines, plan its split.

How:

- New files MUST stay under 600 lines.
- Existing oversized files are grandfathered in `.loc-allowlist` with their
  current line count as the ceiling. They may shrink but not grow. Removing
  an entry after a successful split is the goal.
- Prefer cohesive splits: one module per responsibility (e.g.,
  `scraping/{bookshelf_scraper,reference_parser}.py`), not random
  partitioning to slip under the cap.
- Keep the public Protocol or facade stable across splits so call sites
  don't churn.
- If you must add to an allowlisted file as part of an unrelated fix, raise
  the ceiling explicitly in `.loc-allowlist` in the same commit and link the
  decomposition plan in the message.

The active decomposition backlog lives in
`.planning/2026-05-25-senior-engineering-review.md` (findings #16 EutilsClient
split, #23 passages.py split).

## Testing Notes

- `make test` is the fast default.
- `make test-cov` runs coverage with the 70% floor.
- `make ci-local` runs formatting, linting, type checking, and tests.
- Treat failing checks as real issues unless you have clear evidence
  otherwise.
- Scraper integration tests use cached fixtures in `tests/fixtures/`.
  Refresh them only when scraper logic intentionally changes.
