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

## Testing Notes

- `make test` is the fast default.
- `make test-cov` runs coverage with the 70% floor.
- `make ci-local` runs formatting, linting, type checking, and tests.
- Treat failing checks as real issues unless you have clear evidence
  otherwise.
- Scraper integration tests use cached fixtures in `tests/fixtures/`.
  Refresh them only when scraper logic intentionally changes.
