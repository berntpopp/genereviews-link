# Modernize Stack and Agents Files Implementation Plan

> Historical record

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring genereviews-link to parity with pubtator-link's build, dependency, tooling, security, observability, Docker, CI, and agents-file conventions without changing the HTTP/MCP surface or service-layer semantics.

**Architecture:** Eight phases, each ending with `make ci-local` green. Phase A rebuilds the build/lint/type/test toolchain on uv + hatchling. Phase B swaps custom middleware for `asgi-correlation-id`, adds `defusedxml` and Prometheus metrics. Phase C ports CLI to Typer. Phase D audits and (if compatible) upgrades to FastMCP 3.x. Phase E rewrites agents files (AGENTS.md/CLAUDE.md split, docs/superpowers scaffold, .claude/skills). Phase F adds Docker. Phase G adds CI workflows. Phase H verifies coverage threshold.

**Tech Stack:** Python 3.12, uv, hatchling, ruff, mypy strict, pytest, FastAPI, fastmcp 3.x (fallback 2.10.x), Typer, structlog, asgi-correlation-id, prometheus-client, defusedxml, Docker, GitHub Actions.

**Reference spec:** `docs/superpowers/specs/2026-05-10-modernize-stack-and-agents-design.md`

---

## Phase A — Build, tooling, dependencies

### Task A1: Replace pyproject.toml with hatchling-based config

**Files:**
- Modify: `pyproject.toml`
- Delete: `mypy.ini`

- [ ] **Step 1: Replace `pyproject.toml` with the modern config**

Overwrite `pyproject.toml` with this exact content:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "genereview-link"
version = "2.0.0"
description = "A unified server providing REST API and MCP interfaces for NCBI GeneReviews data."
readme = "README.md"
authors = [{ name = "Bernt Popp", email = "bernt.popp@charite.de" }]
license = { text = "MIT" }
requires-python = ">=3.12"
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Intended Audience :: Science/Research",
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Scientific/Engineering :: Bio-Informatics",
    "Topic :: Software Development :: Libraries :: Python Modules",
]
dependencies = [
    "fastapi>=0.115.0,<1.0.0",
    "uvicorn[standard]>=0.46.0,<1.0.0",
    "pydantic>=2.11.0,<3.0.0",
    "pydantic-settings>=2.6.0,<3.0.0",
    "httpx>=0.28.0,<1.0.0",
    "async-lru>=2.0.4,<3.0.0",
    "structlog>=24.4.0,<26.0.0",
    "orjson>=3.10.0,<4.0.0",
    "beautifulsoup4>=4.12.0,<5.0.0",
    "lxml>=5.2.0,<7.0.0",
    "rich>=15.0.0,<16.0.0",
    "typer>=0.25.1,<1.0.0",
    "mcp[cli]>=1.27.0,<2.0.0",
    "fastmcp>=3.2.0,<4.0.0",
    "gunicorn>=25.3.0,<26.0.0",
    "defusedxml>=0.7.1",
    "asgi-correlation-id>=4.3.0,<5.0.0",
    "prometheus-client>=0.21.0,<1.0.0",
]

[dependency-groups]
dev = [
    "pytest>=9.0.3,<10.0.0",
    "pytest-asyncio>=1.3.0,<2.0.0",
    "pytest-cov>=6.0.0,<8.0.0",
    "pytest-mock>=3.14.0,<4.0.0",
    "pytest-xdist>=3.6.0,<4.0.0",
    "respx>=0.22.0,<1.0.0",
    "ruff>=0.8.0,<1.0.0",
    "mypy>=1.14.0,<2.0.0",
    "pre-commit>=4.0.0,<5.0.0",
    "types-defusedxml>=0.7.0.20260408",
]

[project.scripts]
genereview-link = "genereview_link.cli:app"
genereview-link-mcp = "mcp_server:main"

[project.urls]
Homepage = "https://github.com/berntpopp/genereviews-link"
Repository = "https://github.com/berntpopp/genereviews-link"
Issues = "https://github.com/berntpopp/genereviews-link/issues"

[tool.hatch.build.targets.wheel]
packages = ["genereview_link"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
extend-select = [
    "E",
    "W",
    "F",
    "I",
    "N",
    "UP",
    "B",
    "C4",
    "S",
    "T20",
    "SIM",
    "RUF",
]
ignore = [
    "S101",
    "E501",
]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
line-ending = "lf"

[tool.ruff.lint.per-file-ignores]
"tests/**/*" = ["S101", "T20"]

[tool.mypy]
python_version = "3.12"
strict = true
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
disallow_untyped_decorators = true
no_implicit_optional = true
warn_redundant_casts = true
warn_unused_ignores = true
warn_no_return = true
warn_unreachable = true
exclude = [
    ".*site-packages.*",
    ".*/miniforge3/.*",
    ".*/venv/.*",
    ".*/.venv/.*",
    "htmlcov/.*",
]

[[tool.mypy.overrides]]
module = [
    "async_lru.*",
    "structlog.*",
    "mcp.*",
    "fastmcp.*",
    "fastapi.*",
    "pydantic.*",
    "pydantic_settings.*",
    "httpx.*",
    "uvicorn.*",
    "bs4.*",
    "asgi_correlation_id.*",
    "prometheus_client.*",
]
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = [
    "--strict-markers",
    "-ra",
]
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "integration: marks tests as integration tests",
]
filterwarnings = [
    "ignore::bs4.XMLParsedAsHTMLWarning",
]

[tool.coverage.run]
source = ["genereview_link"]
omit = [
    "tests/*",
    "*/tests/*",
]
branch = true

[tool.coverage.report]
fail_under = 70
precision = 2
show_missing = true
skip_empty = true
exclude_also = [
    "def __repr__",
    "if self.debug:",
    "if settings.DEBUG",
    "raise AssertionError",
    "raise NotImplementedError",
    "if 0:",
    "if __name__ == .__main__.:",
    "class .*\\bProtocol\\):",
    "@(abc\\.)?abstractmethod",
]

[tool.coverage.html]
directory = "htmlcov"

[tool.coverage.xml]
output = "coverage.xml"
```

- [ ] **Step 2: Delete obsolete `mypy.ini`**

```bash
rm mypy.ini
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml mypy.ini
git commit -m "build: rewrite pyproject.toml on hatchling, drop mypy.ini, pin py3.12"
```

---

### Task A2: Generate uv.lock and install

**Files:**
- Create: `uv.lock`

- [ ] **Step 1: Verify uv is installed**

Run: `uv --version`
Expected: `uv 0.5.x` or newer. If missing: `pip install --user uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`.

- [ ] **Step 2: Resolve dependencies and create lockfile**

Run: `uv lock`
Expected: A new `uv.lock` file appears at repo root. No error output.

- [ ] **Step 3: Install dev dependencies into a managed venv**

Run: `uv sync --group dev`
Expected: Creates `.venv/`, installs all runtime + dev deps. Final line reports number of packages installed.

- [ ] **Step 4: Sanity-check the resolution**

Run: `uv run python -c "import fastapi, fastmcp, defusedxml, asgi_correlation_id, prometheus_client, typer; print('imports ok')"`
Expected: `imports ok` printed and no traceback. If `fastmcp` fails to import, note this — Task D1 handles the v3 audit.

- [ ] **Step 5: Commit**

```bash
git add uv.lock
git commit -m "build: add uv.lock for reproducible installs"
```

---

### Task A3: Add Makefile

**Files:**
- Create: `Makefile`

- [ ] **Step 1: Write the Makefile**

Create `Makefile` with this exact content:

```makefile
.PHONY: help install lock upgrade sync format format-check lint lint-ci lint-fix typecheck typecheck-fast typecheck-stop typecheck-fresh test test-fast test-unit test-integration test-cov test-all check ci-local precommit clean dev mcp-serve mcp-serve-http docker-build docker-up docker-down docker-logs

DOCKER_COMPOSE := $(shell if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then echo "docker compose"; elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"; else echo "docker compose"; fi)

.DEFAULT_GOAL := help

help: ## Display this help message
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install: ## Install project and development dependencies with uv
	uv sync --group dev

sync: install ## Alias for install

lock: ## Resolve and update uv.lock
	uv lock

upgrade: ## Upgrade locked dependencies
	uv lock --upgrade

format: ## Format Python code
	uv run ruff format genereview_link tests server.py mcp_server.py

format-check: ## Check formatting without writing
	uv run ruff format --check genereview_link tests server.py mcp_server.py

lint: ## Lint Python code
	uv run ruff check genereview_link tests server.py mcp_server.py

lint-ci: ## Lint Python code without modifying files
	uv run ruff check genereview_link tests server.py mcp_server.py --output-format=github

lint-fix: ## Lint and apply safe fixes
	uv run ruff check genereview_link tests server.py mcp_server.py --fix

typecheck: ## Type check package
	uv run mypy genereview_link server.py mcp_server.py

typecheck-fast: ## Type check with mypy daemon and fallback
	@tmp_log=$$(mktemp); \
	if uv run dmypy run -- genereview_link server.py mcp_server.py >$$tmp_log 2>&1; then \
		cat $$tmp_log; \
	elif grep -Eq "Daemon crashed!|INTERNAL ERROR" $$tmp_log; then \
		echo "dmypy crashed; retrying with a fresh daemon..."; \
		uv run dmypy stop >/dev/null 2>&1 || true; \
		if uv run dmypy run -- genereview_link server.py mcp_server.py >$$tmp_log 2>&1; then \
			cat $$tmp_log; \
		else \
			cat $$tmp_log; \
			echo "Falling back to plain mypy..."; \
			uv run dmypy stop >/dev/null 2>&1 || true; \
			uv run mypy genereview_link server.py mcp_server.py; \
		fi; \
	else \
		cat $$tmp_log; \
		rm -f $$tmp_log; \
		exit 1; \
	fi; \
	rm -f $$tmp_log

typecheck-stop: ## Stop mypy daemon
	uv run dmypy stop

typecheck-fresh: ## Clear mypy cache and run typecheck
	rm -rf .mypy_cache
	uv run mypy genereview_link server.py mcp_server.py

test: ## Run tests quickly
	uv run pytest tests -q

test-fast: ## Run tests in parallel with pytest-xdist
	uv run pytest tests -q -n auto

test-unit: ## Run unit tests in parallel
	uv run pytest tests -q -n auto -m "not integration and not slow"

test-integration: ## Run integration tests serially
	uv run pytest tests -q -m "integration"

test-cov: ## Run tests with coverage
	uv run pytest tests --cov=genereview_link --cov-report=term-missing --cov-report=html --cov-report=xml

test-all: test-cov ## Alias for full test run with coverage

check: format lint ## Format and lint

ci-local: format-check lint-ci typecheck-fast test-fast ## Run fast local CI-equivalent checks

precommit: ci-local ## Run checks expected before commit

clean: ## Remove local caches and generated reports
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml

dev: ## Start REST plus MCP development server
	uv run python server.py --transport unified --host 127.0.0.1 --port 8000

mcp-serve: ## Start local stdio MCP server
	uv run python mcp_server.py

mcp-serve-http: ## Start hosted MCP endpoint with REST API
	uv run python server.py --transport unified --host 0.0.0.0 --port 8000

docker-build: ## Build Docker image
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml build

docker-up: ## Start Docker services
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml up -d

docker-down: ## Stop Docker services
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml down

docker-logs: ## Tail Docker service logs
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml logs -f
```

- [ ] **Step 2: Verify `make help` works**

Run: `make help`
Expected: Two-column list of every target with its `##` description, colored.

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "build: add Makefile mirroring pubtator-link targets"
```

---

### Task A4: Run format + lint to surface tooling errors early

**Files:**
- Modify: any code file that ruff format/lint touches

- [ ] **Step 1: Run formatter**

Run: `make format`
Expected: Either no output (already formatted) or a list of reformatted files. Ruff may reformat to its 100-char `line-length`.

- [ ] **Step 2: Run linter and inspect**

Run: `make lint`
Expected: Either clean output or a list of violations. Common new violations from the expanded ruleset (`S`, `B`, `SIM`, `UP`):
- `S314` (use of unsafe `xml.etree`) — will be fixed in Task B1.
- `UP006`/`UP007` (use `list[str]` not `List[str]`) — auto-fixable.
- `B007` (unused loop variable) — manual.
- `SIM` (simplify) — auto-fixable.

- [ ] **Step 3: Apply auto-fixes**

Run: `make lint-fix`
Expected: Ruff applies safe fixes; remaining manual issues are reported.

- [ ] **Step 4: Manually fix any remaining lint issues**

For each remaining violation in the output of `make lint`:
- If it's `S314`/`S320` (defusedxml), leave it — Task B1 fixes by changing import. Add `# noqa: S314` only if necessary to unblock Phase A.
- If it's `B`/`SIM`/`N`/`RUF`, fix in place. These should be small (rename a var, use a list comprehension, etc.).

Re-run `make lint` until clean (modulo the `S314` exception which is closed in Task B1).

- [ ] **Step 5: Commit**

```bash
git add genereview_link tests server.py mcp_server.py
git commit -m "style: apply ruff format and fix expanded lint ruleset"
```

---

### Task A5: Run mypy strict and surface type errors

**Files:**
- Modify: any code with type issues uncovered by strict mode

- [ ] **Step 1: Run typecheck**

Run: `make typecheck`
Expected: A list of errors. Strict mode adds:
- `disallow_untyped_decorators = true` — requires typing on custom decorators
- `warn_unused_ignores = true` — flags any obsolete `# type: ignore`
- `warn_unreachable = true` — flags unreachable code
- `no_implicit_optional = true` — `def f(x: str = None)` must become `x: str | None = None`

Recent commits already pushed mypy coverage to "100%" — most issues will be from the strict additions only.

- [ ] **Step 2: Fix each error**

For each error reported:
- Missing decorator typing: add explicit `Callable[..., Any]` types to decorator return.
- Unused ignore: delete the comment.
- Implicit optional: change `x: T = None` to `x: T | None = None`.
- Unreachable code: delete or restructure.

Do NOT silence errors with blanket `# type: ignore`. If a strict check is genuinely too noisy for a single line, use `# type: ignore[specific-code]`.

- [ ] **Step 3: Verify clean**

Run: `make typecheck`
Expected: `Success: no issues found in N source files`

- [ ] **Step 4: Commit**

```bash
git add genereview_link server.py mcp_server.py
git commit -m "types: pass mypy strict on entire package"
```

---

### Task A6: Verify test suite still runs under new toolchain

**Files:** none

- [ ] **Step 1: Run tests**

Run: `make test`
Expected: All tests pass (or, if some failures predate this work, the same set still fails — no regressions introduced by the toolchain swap).

- [ ] **Step 2: If failures appear, classify them**

For any new failure (i.e., not present on `git log -1 HEAD` before this branch):
- If caused by `pytest-asyncio` major bump (1.x): may need `asyncio_default_fixture_loop_scope` removal (now defaults).
- If caused by `respx` major bump: may need to update mock signatures.

Fix the test (not the assertion) to adapt to the API change.

- [ ] **Step 3: Run `make ci-local`**

Run: `make ci-local`
Expected: Format-check, lint-ci, typecheck-fast, and test-fast all pass.

- [ ] **Step 4: Commit (if any test fixes were made)**

```bash
git add tests
git commit -m "test: adapt suite to pytest-asyncio 1.x / respx 0.22 APIs"
```

---

## Phase B — Security and observability

### Task B1: Switch xml.etree to defusedxml in eutils_client

**Files:**
- Modify: `genereview_link/api/eutils_client.py:12` (the `xml.etree` import)
- Test: `tests/test_scraper_parsers.py` (verify nothing breaks)

- [ ] **Step 1: Write a failing test for safe XML parsing**

Add to `tests/test_scraper_parsers.py` (at the end of the file):

```python
def test_eutils_client_uses_defusedxml() -> None:
    """Verify the eutils client imports XML parsing from defusedxml, not stdlib."""
    import inspect

    from genereview_link.api import eutils_client

    source = inspect.getsource(eutils_client)
    assert "from defusedxml" in source, "eutils_client must import from defusedxml"
    assert "from xml.etree" not in source, (
        "eutils_client must not import from xml.etree (XXE attack surface)"
    )
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `uv run pytest tests/test_scraper_parsers.py::test_eutils_client_uses_defusedxml -v`
Expected: FAIL with assertion that `from defusedxml` is missing.

- [ ] **Step 3: Update the import**

In `genereview_link/api/eutils_client.py`, find this line (around line 12):

```python
from xml.etree import ElementTree as ET
```

Replace with:

```python
from defusedxml import ElementTree as ET
```

No other call sites change — defusedxml's `ElementTree` module exposes the same `Element`, `fromstring`, `parse`, etc. API.

- [ ] **Step 4: Run the new test to confirm it passes**

Run: `uv run pytest tests/test_scraper_parsers.py::test_eutils_client_uses_defusedxml -v`
Expected: PASS.

- [ ] **Step 5: Run full scraper test suite**

Run: `uv run pytest tests/test_scraper_parsers.py tests/test_scraper_integration.py -v`
Expected: All tests pass. defusedxml is a drop-in replacement for ElementTree.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/api/eutils_client.py tests/test_scraper_parsers.py
git commit -m "security: switch eutils_client XML parsing to defusedxml"
```

---

### Task B2: Wire asgi-correlation-id middleware

**Files:**
- Modify: `genereview_link/server_manager.py:25-27` (remove RequestLoggingMiddleware import), `:79` (replace middleware registration)
- Modify: `genereview_link/config.py` (add CORRELATION_ID_HEADER setting)
- Test: `tests/test_correlation_id.py` (new)

- [ ] **Step 1: Add the setting**

In `genereview_link/config.py`, in the `Settings(BaseSettings)` class, add after the `ENVIRONMENT` line (around line 41):

```python
    # Correlation ID
    CORRELATION_ID_HEADER: str = "X-Request-ID"
```

- [ ] **Step 2: Write a failing test that the correlation ID propagates**

Create `tests/test_correlation_id.py`:

```python
"""Verify that asgi-correlation-id propagates request IDs to response headers."""

from fastapi.testclient import TestClient


def test_correlation_id_in_response_header() -> None:
    """A request without X-Request-ID gets one assigned in the response."""
    from server import app

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert "X-Request-ID" in response.headers
        # Should be a UUID-shaped string
        assert len(response.headers["X-Request-ID"]) >= 16


def test_correlation_id_echoed_from_request() -> None:
    """A request with X-Request-ID gets the same value back in the response."""
    from server import app

    incoming = "test-correlation-12345"
    with TestClient(app) as client:
        response = client.get("/health", headers={"X-Request-ID": incoming})
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == incoming
```

- [ ] **Step 3: Run the test to confirm it fails**

Run: `uv run pytest tests/test_correlation_id.py -v`
Expected: FAIL. The existing custom middleware sets `X-Correlation-ID` (not `X-Request-ID`) and does not echo incoming headers.

- [ ] **Step 4: Update server_manager.py to use asgi-correlation-id**

In `genereview_link/server_manager.py`:

1. Remove this import block (around line 25-27):

```python
from genereview_link.middleware.logging_middleware import (
    RequestLoggingMiddleware,
)
```

2. Add this import near the other middleware imports (around line 9):

```python
from asgi_correlation_id import CorrelationIdMiddleware
```

3. Replace the middleware registration. Find this in `create_fastapi_app` (around line 79):

```python
        app.add_middleware(RequestLoggingMiddleware)
```

Replace with:

```python
        app.add_middleware(
            CorrelationIdMiddleware,
            header_name=settings.CORRELATION_ID_HEADER,
            update_request_header=True,
        )
```

- [ ] **Step 5: Run the test to confirm it passes**

Run: `uv run pytest tests/test_correlation_id.py -v`
Expected: PASS for both tests.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/server_manager.py genereview_link/config.py tests/test_correlation_id.py
git commit -m "feat: replace custom logging middleware with asgi-correlation-id"
```

---

### Task B3: Add structlog processor that reads correlation ID

**Files:**
- Modify: `genereview_link/logging_config.py` (add processor)
- Test: `tests/test_logging_correlation.py` (new)

- [ ] **Step 1: Write a failing test that log records contain the correlation ID**

Create `tests/test_logging_correlation.py`:

```python
"""Verify that asgi-correlation-id propagates into structlog records."""

from asgi_correlation_id.context import correlation_id


def test_structlog_processor_picks_up_correlation_id() -> None:
    """The custom processor should inject correlation_id from contextvar into the event dict."""
    from genereview_link.logging_config import add_correlation_id

    token = correlation_id.set("abc-123")
    try:
        event = add_correlation_id(None, "info", {"event": "test"})
        assert event["correlation_id"] == "abc-123"
    finally:
        correlation_id.reset(token)


def test_structlog_processor_handles_missing_correlation_id() -> None:
    """When no correlation ID is set, the processor must not crash."""
    from genereview_link.logging_config import add_correlation_id

    event = add_correlation_id(None, "info", {"event": "test"})
    assert "correlation_id" in event
    # Either None or empty string is acceptable as a "not set" sentinel
    assert event["correlation_id"] in (None, "")
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `uv run pytest tests/test_logging_correlation.py -v`
Expected: FAIL with `ImportError: cannot import name 'add_correlation_id'`.

- [ ] **Step 3: Add the processor to logging_config.py**

In `genereview_link/logging_config.py`, near the other `add_*` processors (after `add_service_context`, around line 40), add:

```python
def add_correlation_id(
    logger: Any, method_name: str, event_dict: EventDict
) -> EventDict:
    """Inject asgi-correlation-id's request-scoped correlation ID into the log record."""
    from asgi_correlation_id.context import correlation_id

    event_dict["correlation_id"] = correlation_id.get()
    return event_dict
```

Then, in `configure_structlog()`, add the processor to `common_processors` (insert after `add_service_context`):

```python
    common_processors: list[Processor] = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        add_log_level,
        add_timestamp,
        add_service_context,
        add_correlation_id,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `uv run pytest tests/test_logging_correlation.py -v`
Expected: PASS for both tests.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/logging_config.py tests/test_logging_correlation.py
git commit -m "feat: structlog processor injects correlation_id from asgi-correlation-id"
```

---

### Task B4: Delete the custom RequestLoggingMiddleware

**Files:**
- Delete: `genereview_link/middleware/logging_middleware.py`
- Modify: `genereview_link/middleware/__init__.py` (likely will be empty — delete if so)

- [ ] **Step 1: Verify no remaining imports of the old middleware**

Run: `uv run python -c "import genereview_link.server_manager"`
Expected: No `ImportError`. If it fails because something still imports `logging_middleware`, grep the codebase:

Run: `grep -r "logging_middleware\|RequestLoggingMiddleware" genereview_link tests`
Expected: No matches. If matches found, remove or update those imports first.

- [ ] **Step 2: Delete the file**

```bash
rm genereview_link/middleware/logging_middleware.py
```

- [ ] **Step 3: Check if the middleware package is now empty**

Run: `ls genereview_link/middleware/`
Expected: only `__init__.py` (and `__pycache__/`).

If `__init__.py` is empty or only contains an import of the deleted middleware, delete the whole directory:

```bash
rm -rf genereview_link/middleware
```

If `__init__.py` has other content, just leave it.

- [ ] **Step 4: Run full test suite**

Run: `make ci-local`
Expected: All checks green.

- [ ] **Step 5: Commit**

```bash
git add -A genereview_link/middleware
git commit -m "refactor: remove custom RequestLoggingMiddleware (replaced by asgi-correlation-id)"
```

---

### Task B5: Add /metrics endpoint with prometheus-client

**Files:**
- Modify: `genereview_link/server_manager.py` (add metrics ASGI app mount + middleware)
- Modify: `genereview_link/config.py` (add ENABLE_METRICS setting)
- Test: `tests/test_metrics.py` (new)

- [ ] **Step 1: Add the setting**

In `genereview_link/config.py`, add to the `Settings` class (after `CORRELATION_ID_HEADER`):

```python
    # Metrics
    ENABLE_METRICS: bool = True
```

- [ ] **Step 2: Write a failing test for the /metrics endpoint**

Create `tests/test_metrics.py`:

```python
"""Verify Prometheus /metrics endpoint exposes basic counters."""

from fastapi.testclient import TestClient


def test_metrics_endpoint_returns_prometheus_format() -> None:
    """/metrics returns text/plain with Prometheus exposition format."""
    from server import app

    with TestClient(app) as client:
        # Trigger at least one request to populate counters
        client.get("/health")
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        body = response.text
        assert "genereview_requests_total" in body
        assert "genereview_request_duration_seconds" in body
```

- [ ] **Step 3: Run the test to confirm it fails**

Run: `uv run pytest tests/test_metrics.py -v`
Expected: FAIL with 404 on `/metrics`.

- [ ] **Step 4: Wire metrics into server_manager.py**

In `genereview_link/server_manager.py`, add near the other imports:

```python
import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
```

Then, near the top of the module (after the logger line), define the metrics:

```python
REQUEST_COUNTER = Counter(
    "genereview_requests_total",
    "Total HTTP requests",
    labelnames=("method", "path", "status"),
)
REQUEST_LATENCY = Histogram(
    "genereview_request_duration_seconds",
    "HTTP request latency in seconds",
    labelnames=("method", "path"),
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        path = request.url.path
        REQUEST_LATENCY.labels(method=request.method, path=path).observe(elapsed)
        REQUEST_COUNTER.labels(
            method=request.method,
            path=path,
            status=str(response.status_code),
        ).inc()
        return response
```

In `create_fastapi_app`, after the `CorrelationIdMiddleware` registration and before `app.include_router(...)` calls, add:

```python
        if settings.ENABLE_METRICS:
            app.add_middleware(PrometheusMiddleware)
```

In `_add_utility_endpoints`, add a new endpoint:

```python
        @app.get("/metrics", tags=["Observability"], include_in_schema=False)
        async def metrics() -> Response:
            return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

- [ ] **Step 5: Run the test to confirm it passes**

Run: `uv run pytest tests/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 6: Verify full ci-local still green**

Run: `make ci-local`
Expected: All checks pass.

- [ ] **Step 7: Commit**

```bash
git add genereview_link/server_manager.py genereview_link/config.py tests/test_metrics.py
git commit -m "feat: expose /metrics with Prometheus counters and latency histogram"
```

---

## Phase C — CLI to Typer

### Task C1: Rewrite cli.py with Typer

**Files:**
- Modify: `genereview_link/cli.py` (full rewrite)

- [ ] **Step 1: Write the new Typer-based CLI**

Overwrite `genereview_link/cli.py` with:

```python
"""Typer-based CLI for the GeneReview Link unified server."""

from __future__ import annotations

import asyncio
import sys
from enum import Enum

import typer
import uvicorn

from genereview_link.config import ServerConfig
from genereview_link.logging_config import configure_structlog, get_logger

configure_structlog()
logger = get_logger("cli")

app = typer.Typer(
    name="genereview-link",
    help="GeneReview Link Unified Server",
    no_args_is_help=False,
    add_completion=False,
)


class Transport(str, Enum):
    """Transport mode for the server."""

    unified = "unified"
    http = "http"
    stdio = "stdio"


class LogLevel(str, Enum):
    """Supported log levels."""

    debug = "DEBUG"
    info = "INFO"
    warning = "WARNING"
    error = "ERROR"


def build_config(
    transport: Transport = Transport.unified,
    host: str = "127.0.0.1",
    port: int = 8000,
    mcp_path: str = "/mcp",
    disable_docs: bool = False,
    log_level: LogLevel = LogLevel.info,
) -> ServerConfig:
    """Build a ServerConfig from CLI inputs."""
    return ServerConfig(
        transport=transport.value,
        host=host,
        port=port,
        mcp_path=mcp_path,
        enable_docs=not disable_docs,
        log_level=log_level.value,
    )


@app.command()
def serve(
    transport: Transport = typer.Option(
        Transport.unified, "--transport", help="Transport mode"
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    port: int = typer.Option(8000, "--port", help="Port to bind to"),
    mcp_path: str = typer.Option("/mcp", "--mcp-path", help="MCP endpoint path"),
    disable_docs: bool = typer.Option(
        False, "--disable-docs", help="Disable API documentation endpoints"
    ),
    log_level: LogLevel = typer.Option(
        LogLevel.info, "--log-level", help="Log level"
    ),
    dev: bool = typer.Option(
        False, "--dev", help="Development mode with auto-reload"
    ),
) -> None:
    """Start the GeneReview Link unified server."""
    from genereview_link.server_manager import UnifiedServerManager

    config = build_config(
        transport=transport,
        host=host,
        port=port,
        mcp_path=mcp_path,
        disable_docs=disable_docs,
        log_level=log_level,
    )

    if dev and config.transport != "stdio":
        logger.info("Running in development mode with auto-reload.")
        uvicorn.run(
            "server:app",
            host=config.host,
            port=config.port,
            reload=True,
            log_config=None,
        )
        return

    try:
        manager = UnifiedServerManager()
        asyncio.run(manager.start_server(config))
    except (ValueError, asyncio.CancelledError) as exc:
        logger.error("Server startup failed", error=str(exc))
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Server shutdown requested by user.")
        sys.exit(0)


if __name__ == "__main__":
    app()
```

- [ ] **Step 2: Update `server.py` to delegate to the Typer app**

Overwrite `server.py` with:

```python
#!/usr/bin/env python
"""Unified GeneReview Link server entry point."""

import asyncio

from genereview_link.cli import app as cli_app
from genereview_link.config import ServerConfig
from genereview_link.logging_config import configure_structlog
from genereview_link.server_manager import UnifiedServerManager

configure_structlog()


# Module-level ASGI app for uvicorn --reload and gunicorn
manager = UnifiedServerManager()
config = ServerConfig()
app = asyncio.run(manager.create_fastapi_app(config))


def main() -> None:
    """CLI entry point."""
    cli_app()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the new CLI to verify it loads**

Run: `uv run python server.py --help`
Expected: Typer help text shown, listing `serve` subcommand and global options.

- [ ] **Step 4: Run the serve subcommand help**

Run: `uv run python server.py serve --help`
Expected: Help for `serve` listing `--transport`, `--host`, `--port`, `--mcp-path`, `--disable-docs`, `--log-level`, `--dev`.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/cli.py server.py
git commit -m "feat: rewrite CLI on Typer with serve subcommand"
```

---

### Task C2: Rewrite tests/test_cli.py against Typer

**Files:**
- Modify: `tests/test_cli.py` (full rewrite)

- [ ] **Step 1: Write the new CLI tests**

Overwrite `tests/test_cli.py` with:

```python
"""Tests for the Typer-based CLI."""

from typer.testing import CliRunner

from genereview_link.cli import LogLevel, Transport, app, build_config
from genereview_link.config import ServerConfig

runner = CliRunner()


class TestServeHelp:
    def test_help_lists_serve_command(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "serve" in result.stdout

    def test_serve_help_lists_all_flags(self) -> None:
        result = runner.invoke(app, ["serve", "--help"])
        assert result.exit_code == 0
        for flag in (
            "--transport",
            "--host",
            "--port",
            "--mcp-path",
            "--disable-docs",
            "--log-level",
            "--dev",
        ):
            assert flag in result.stdout


class TestBuildConfig:
    def test_defaults(self) -> None:
        config = build_config()
        assert isinstance(config, ServerConfig)
        assert config.transport == "unified"
        assert config.host == "127.0.0.1"
        assert config.port == 8000
        assert config.mcp_path == "/mcp"
        assert config.enable_docs is True
        assert config.log_level == "INFO"

    def test_explicit_overrides(self) -> None:
        config = build_config(
            transport=Transport.stdio,
            host="0.0.0.0",
            port=9000,
            mcp_path="/api/mcp",
            disable_docs=True,
            log_level=LogLevel.debug,
        )
        assert config.transport == "stdio"
        assert config.host == "0.0.0.0"
        assert config.port == 9000
        assert config.mcp_path == "/api/mcp"
        assert config.enable_docs is False
        assert config.log_level == "DEBUG"

    def test_disable_docs_inverts_enable_docs(self) -> None:
        assert build_config(disable_docs=False).enable_docs is True
        assert build_config(disable_docs=True).enable_docs is False


class TestInvalidInput:
    def test_invalid_transport_rejected(self) -> None:
        result = runner.invoke(app, ["serve", "--transport", "invalid"])
        assert result.exit_code != 0

    def test_invalid_log_level_rejected(self) -> None:
        result = runner.invoke(app, ["serve", "--log-level", "TRACE"])
        assert result.exit_code != 0

    def test_non_numeric_port_rejected(self) -> None:
        result = runner.invoke(app, ["serve", "--port", "abc"])
        assert result.exit_code != 0
```

- [ ] **Step 2: Run the new tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: All tests pass.

- [ ] **Step 3: Verify the legacy CLI symbols are gone**

Run: `grep -rn "create_parser\|create_config_from_args" genereview_link tests`
Expected: No matches.

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli.py
git commit -m "test: rewrite CLI tests against Typer using CliRunner"
```

---

### Task C3: Update README and CLAUDE.md references to the old CLI syntax

**Files:**
- Modify: `README.md` (any `python server.py --transport ...` examples become `python server.py serve --transport ...`)
- Modify: `CLAUDE.md` if it surfaces old CLI examples (note: CLAUDE.md is fully rewritten in Task E2 — only touch if A-D phases would block on stale docs)

- [ ] **Step 1: Grep for old CLI usages**

Run: `grep -rn "server\.py --transport\|server\.py --dev\|server\.py --host" README.md`
Expected: A list of matches. Each needs `--transport` → `serve --transport`, etc.

- [ ] **Step 2: Apply the fixes**

In `README.md`, find each shell example like:

```
python server.py --transport unified
```

and replace with:

```
python server.py serve --transport unified
```

Apply the same transformation to every `server.py --…` example.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: update README CLI examples to Typer serve subcommand"
```

---

## Phase D — FastMCP 3 spike and upgrade

### Task D1: Audit fastmcp 3 API surface used in server_manager.py

**Files:** none (research-only)

- [ ] **Step 1: Verify fastmcp version installed**

Run: `uv run python -c "import fastmcp; print(fastmcp.__version__)"`
Expected: `3.2.x` or newer (per the pin in `pyproject.toml`).

- [ ] **Step 2: Inspect FastMCP.from_fastapi signature**

Run: `uv run python -c "from fastmcp import FastMCP; import inspect; print(inspect.signature(FastMCP.from_fastapi))"`
Expected: Output the signature. Record whether `mcp_names`, `route_maps` are still accepted kwargs.

- [ ] **Step 3: Inspect MCPType and RouteMap**

Run: `uv run python -c "from fastmcp.server.openapi import MCPType, RouteMap; print(MCPType.__members__); print(RouteMap.__init__.__doc__)"`
Expected: confirms `MCPType.EXCLUDE` still exists and `RouteMap(pattern=..., mcp_type=...)` still accepts those kwargs.

- [ ] **Step 4: Inspect http_app**

Run: `uv run python -c "from fastmcp import FastMCP; import inspect; m = FastMCP(name='t'); print(inspect.signature(m.http_app))"`
Expected: confirms `http_app(path=...)` or equivalent.

- [ ] **Step 5: Inspect run_async transport modes**

Run: `uv run python -c "from fastmcp import FastMCP; import inspect; m = FastMCP(name='t'); print(inspect.signature(m.run_async))"`
Expected: confirms `transport='stdio'` is still a valid string.

- [ ] **Step 6: Decide v3 vs fallback to v2.10.x**

If steps 2-5 all return compatible signatures: proceed with v3 (no pin change).

If any signature has diverged in a breaking way, update `pyproject.toml`:

In `[project].dependencies`, change:

```toml
    "fastmcp>=3.2.0,<4.0.0",
```

to:

```toml
    "fastmcp>=2.10.0,<3.0.0",
```

Then run `uv lock` and `uv sync --group dev`. Append a note to `docs/superpowers/specs/2026-05-10-modernize-stack-and-agents-design.md` under "Risks" documenting the deviation.

- [ ] **Step 7: Commit (only if pin changed)**

```bash
git add pyproject.toml uv.lock docs/superpowers/specs/2026-05-10-modernize-stack-and-agents-design.md
git commit -m "build: pin fastmcp 2.10.x — v3 API surface incompatible with our usage"
```

If v3 is fine, no commit needed.

---

### Task D2: Adapt server_manager.py to fastmcp 3 (only if v3 is being used)

**Files:**
- Modify: `genereview_link/server_manager.py` (only if D1 step 6 chose v3)

> **Skip this task if D1 step 6 fell back to v2.10.x.** The existing code already works on v2.

- [ ] **Step 1: Run the existing FastMCP startup path**

Run: `uv run python -c "
import asyncio
from genereview_link.server_manager import UnifiedServerManager
from genereview_link.config import ServerConfig

async def go():
    m = UnifiedServerManager()
    app = await m.create_fastapi_app(ServerConfig())
    mcp = await m.create_mcp_server(app, ServerConfig())
    print('OK:', type(mcp).__name__)
asyncio.run(go())
"`
Expected: prints `OK: FastMCP` (or whatever subclass v3 uses). If it raises a `TypeError` for an unexpected kwarg in `from_fastapi`, that kwarg has been renamed/removed.

- [ ] **Step 2: For each TypeError encountered, adapt the call**

The most likely break points (from typical v2 → v3 migrations):
- `mcp_names=` may be renamed (check D1 step 2 output).
- `route_maps=` may be renamed.

If `mcp_names` is gone, look for an equivalent — e.g., a per-tool `name` arg passed during registration, or `name_mappings`. Update accordingly.

If `route_maps` is gone, look for `route_filters` or an `exclude_paths` arg. Update accordingly.

- [ ] **Step 3: Verify unified server starts**

Run: `uv run python -c "
import asyncio
from genereview_link.server_manager import UnifiedServerManager
from genereview_link.config import ServerConfig

async def go():
    m = UnifiedServerManager()
    config = ServerConfig()
    m.app = await m.create_fastapi_app(config)
    m.mcp = await m.create_mcp_server(m.app, config)
    m.app.mount(config.mcp_path, m.mcp.http_app())
    print('mounted ok')
asyncio.run(go())
"`
Expected: `mounted ok`.

- [ ] **Step 4: Run the test suite to confirm nothing regressed**

Run: `make test`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/server_manager.py
git commit -m "feat: adapt server_manager to fastmcp 3.x API"
```

---

### Task D3: Manual stdio MCP smoke test

**Files:** none

- [ ] **Step 1: Start the stdio server and send an MCP initialize request**

Run, in one shell:

```bash
uv run python server.py serve --transport stdio < tests/fixtures/mcp_initialize.json
```

If `tests/fixtures/mcp_initialize.json` does not exist, create it first:

```json
{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "smoke-test", "version": "0.0.1"}}}
```

Expected: A JSON-RPC response on stdout with `"result"` containing `"protocolVersion"` and `"serverInfo"`. Logs go to stderr (do not corrupt stdout).

- [ ] **Step 2: Commit the fixture (if newly created)**

```bash
git add tests/fixtures/mcp_initialize.json
git commit -m "test: add MCP initialize fixture for stdio smoke test"
```

---

## Phase E — Agents files and docs scaffold

### Task E1: Create AGENTS.md as source of truth

**Files:**
- Create: `AGENTS.md`

- [ ] **Step 1: Write AGENTS.md**

Create `AGENTS.md` with:

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: add AGENTS.md as shared agent source of truth"
```

---

### Task E2: Slim CLAUDE.md to delegate to AGENTS.md

**Files:**
- Modify: `CLAUDE.md` (full rewrite — original content moves into AGENTS.md)

- [ ] **Step 1: Overwrite CLAUDE.md**

Replace the entire contents of `CLAUDE.md` with:

```markdown
# CLAUDE.md

@AGENTS.md

Claude Code entrypoint only:

- Use `AGENTS.md` for shared repository instructions.
- Keep Claude-specific additions here short and tool-specific.
- Prefer `make ci-local` before final handoff.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: slim CLAUDE.md to delegate to AGENTS.md"
```

---

### Task E3: Create docs/superpowers scaffold

**Files:**
- Create: `docs/superpowers/README.md`
- (Directories `plans/`, `specs/`, `prompts/`, `archive/` already exist from the brainstorm step.)

- [ ] **Step 1: Verify directories exist**

Run: `ls docs/superpowers/`
Expected: `README.md` or no README; `plans/`, `specs/`, `prompts/`, `archive/` directories. `specs/` should contain the design spec from the brainstorm.

If any subdirectory is missing, create it:

```bash
mkdir -p docs/superpowers/plans docs/superpowers/specs docs/superpowers/prompts docs/superpowers/archive
```

- [ ] **Step 2: Write the README**

Create `docs/superpowers/README.md`:

```markdown
# Superpowers Documents

Workspace for agentic-worker artifacts.

- `specs/` — design specs (output of brainstorming).
- `plans/` — implementation plans (output of writing-plans).
- `prompts/` — reusable prompt fragments.
- `archive/` — completed specs and plans.

File naming convention: `YYYY-MM-DD-<topic>.md`.
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/README.md
git commit -m "docs: add docs/superpowers README"
```

---

### Task E4: Port .claude/skills (adapted to genereviews-link)

**Files:**
- Create: `.claude/skills/ci-failure-triage/SKILL.md`
- Create: `.claude/skills/fastapi-route-change/SKILL.md`
- Create: `.claude/skills/mcp-tool-change/SKILL.md`
- Create: `.claude/skills/release-readiness/SKILL.md`
- Create: `.claude/skills/ncbi-scraper-change/SKILL.md`

- [ ] **Step 1: Verify .claude/ exists**

Run: `ls -la .claude/`
Expected: `settings.local.json` and `skills/` (creating). If `skills/` is missing:

```bash
mkdir -p .claude/skills
```

- [ ] **Step 2: Write ci-failure-triage skill**

Create `.claude/skills/ci-failure-triage/SKILL.md`:

```markdown
---
name: ci-failure-triage
description: Use when `make ci-local` fails or a GitHub Actions run reports a CI failure.
---

# CI Failure Triage

Follow `AGENTS.md` first.

## Workflow

1. Run `make ci-local` locally and identify which sub-target failed
   (format-check, lint-ci, typecheck-fast, or test-fast).
2. For format failures: run `make format` and re-check.
3. For lint failures: run `make lint-fix` and address remaining manual issues.
4. For typecheck failures: read the mypy error, fix the type annotation —
   do not silence with blanket `# type: ignore`. Use `# type: ignore[specific-code]`
   only for legitimate library typing gaps.
5. For test failures: read the assertion, fix the code or test (not the
   assertion to mask the bug). For scraper tests, consider refreshing
   `tests/fixtures/` only if scraper logic intentionally changed.
6. Re-run `make ci-local` until green.
```

- [ ] **Step 3: Write fastapi-route-change skill**

Create `.claude/skills/fastapi-route-change/SKILL.md`:

```markdown
---
name: fastapi-route-change
description: Use when adding, renaming, or modifying a FastAPI route in genereviews-link.
---

# FastAPI Route Change

Follow `AGENTS.md` first.

## Workflow

1. Inspect existing route modules under `genereview_link/api/routes/` and
   reuse their pattern (router declaration, dependency injection, error
   handling, response_model).
2. Use Pydantic models from `genereview_link/models/` for request and
   response shapes. Add new models there if needed.
3. Raise `DataNotFoundError` from the service layer (genereview_service.py)
   for 404 cases; let the existing exception handler convert it to HTTP 404.
4. Update or add MCP tool naming in `server_manager.create_mcp_server`'s
   `mcp_custom_names` and `mcp_route_maps` if the route should be exposed
   via MCP.
5. Add route-level tests in `tests/test_api_integration.py` and unit tests
   for any new service logic in the appropriate test file.
6. Update `README.md` API table if the public endpoint list changed.
7. Run `make ci-local` before handoff.
```

- [ ] **Step 4: Write mcp-tool-change skill**

Create `.claude/skills/mcp-tool-change/SKILL.md`:

```markdown
---
name: mcp-tool-change
description: Use when adding, renaming, or changing GeneReview-Link MCP tools, resources, or schemas.
---

# MCP Tool Change

Follow `AGENTS.md` first.

## Workflow

1. Inspect `genereview_link/server_manager.py:create_mcp_server` for the
   existing `mcp_custom_names` and `mcp_route_maps` patterns.
2. Keep hosted public tools research-use scoped; do not add clinical
   decision support, destructive cache operations, or broad
   filesystem/network powers.
3. Prefer typed Pydantic input/output models over raw dicts.
4. Update MCP tool name mappings and route-map filters in
   `create_mcp_server`. New REST endpoints should be auto-exposed via
   `FastMCP.from_fastapi`; explicitly exclude any endpoint that should
   not be a tool.
5. Add or update tests that touch the MCP-mounted app path.
6. Update README and AGENTS.md if tool names or scopes change.
7. Run `make ci-local` and a manual stdio smoke test (`make mcp-serve`
   with a JSON-RPC initialize) before handoff.
```

- [ ] **Step 5: Write release-readiness skill**

Create `.claude/skills/release-readiness/SKILL.md`:

```markdown
---
name: release-readiness
description: Use before tagging a release of genereviews-link.
---

# Release Readiness

Follow `AGENTS.md` first.

## Workflow

1. Verify `make ci-local` is green on `main`.
2. Run `make test-cov` and confirm coverage is above the 70% floor.
3. Run `make docker-build` and verify it succeeds.
4. Update `pyproject.toml` `version`. Use semantic versioning.
5. Update `README.md` if behavior changed.
6. Create a tag `vX.Y.Z` and push. The `release.yml` workflow handles
   wheel build and TestPyPI publication.
7. Verify the release artifact downloads and installs cleanly:
   `uv run pip install --index-url https://test.pypi.org/simple/ genereview-link==X.Y.Z`.
```

- [ ] **Step 6: Write ncbi-scraper-change skill**

Create `.claude/skills/ncbi-scraper-change/SKILL.md`:

```markdown
---
name: ncbi-scraper-change
description: Use when modifying NCBI E-utilities or NCBI Bookshelf scraping logic in eutils_client.py.
---

# NCBI Scraper Change

Follow `AGENTS.md` first.

## Workflow

1. Inspect `genereview_link/api/eutils_client.py` for the affected scraper
   method (`_extract_title`, `_extract_authors`, `_find_main_content`, etc.).
2. Never bypass the rate limiter — keep the existing 0.11s/0.34s delays
   and the `RATE_LIMIT_STATE_FILE` coordination path intact.
3. Always parse XML via `defusedxml.ElementTree`. Never import
   `xml.etree.ElementTree` directly.
4. For HTML scraping, prefer existing BeautifulSoup selectors over new ones.
   Add a fixture in `tests/fixtures/` that captures the current NCBI page
   structure and write a parser test against it.
5. Run `pytest tests/test_scraper_parsers.py tests/test_scraper_integration.py`
   before claiming the change is complete.
6. If selectors had to change, document the trigger (page structure change,
   new section, etc.) in the commit message body.
7. Run `make ci-local` before handoff.
```

- [ ] **Step 7: Commit**

```bash
git add .claude/skills
git commit -m "docs: port repo-local .claude/skills from pubtator + add ncbi-scraper-change"
```

---

## Phase F — Docker

### Task F1: Create docker/ scaffold and Dockerfile

**Files:**
- Create: `docker/Dockerfile`
- Create: `docker/gunicorn_conf.py`
- Create: `docker/README.md`

- [ ] **Step 1: Create the directory**

```bash
mkdir -p docker
```

- [ ] **Step 2: Write the Dockerfile**

Create `docker/Dockerfile`:

```dockerfile
# Multi-stage Dockerfile for GeneReview-Link

# --- Builder Stage ---
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV VIRTUAL_ENV="/opt/venv" \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY uv.lock pyproject.toml README.md ./

RUN pip install --upgrade pip uv && \
    uv sync --frozen --no-dev --active

# --- Production Stage ---
FROM python:3.12-slim AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/home/app/web" \
    GENEREVIEW_LINK_HOST=0.0.0.0 \
    GENEREVIEW_LINK_PORT=8000 \
    TMPDIR=/tmp/genereview-link

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

RUN groupadd --system app && \
    useradd --system --gid app --home /home/app --create-home app && \
    mkdir -p /tmp/genereview-link /var/cache/genereview-link && \
    chown -R app:app /tmp/genereview-link /var/cache/genereview-link /home/app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /home/app/web

COPY --chown=app:app ./genereview_link ./genereview_link
COPY --chown=app:app ./server.py ./mcp_server.py ./pyproject.toml ./README.md ./
COPY --chown=app:app ./docker/gunicorn_conf.py ./

RUN /opt/venv/bin/pip install -e . --no-deps

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["gunicorn", "-c", "gunicorn_conf.py", "server:app"]
```

- [ ] **Step 3: Write gunicorn_conf.py**

Create `docker/gunicorn_conf.py`:

```python
"""Gunicorn configuration for GeneReview-Link production deployment."""

from __future__ import annotations

import os
from typing import Any

bind = f"0.0.0.0:{os.environ.get('GENEREVIEW_LINK_PORT', os.environ.get('PORT', '8000'))}"
backlog = 2048

workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
max_requests = 1000
max_requests_jitter = 50

timeout = 30
keepalive = 2
graceful_timeout = 30

accesslog = "-"
errorlog = "-"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
capture_output = True
enable_stdio_inheritance = True

proc_name = "genereview-link"

limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190
forwarded_allow_ips = os.environ.get("GUNICORN_FORWARDED_ALLOW_IPS", "*")
secure_scheme_headers = {
    "X-FORWARDED-PROTO": "https",
    "X-FORWARDED-SSL": "on",
}

preload_app = True
reuse_port = True

worker_tmp_dir = "/dev/shm"


def on_starting(server: Any) -> None:
    server.log.info("Starting GeneReview-Link server")


def on_reload(server: Any) -> None:
    server.log.info("Reloading GeneReview-Link server")


def worker_int(worker: Any) -> None:
    worker.log.info("Worker received INT or QUIT signal")


def post_fork(server: Any, worker: Any) -> None:
    server.log.info("Worker spawned (pid: %s)", worker.pid)


def post_worker_init(worker: Any) -> None:
    worker.log.info("Worker initialized (pid: %s)", worker.pid)


def worker_abort(worker: Any) -> None:
    worker.log.info("Worker aborted (pid: %s)", worker.pid)
```

- [ ] **Step 4: Write docker/README.md**

Create `docker/README.md`:

```markdown
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
```

- [ ] **Step 5: Commit**

```bash
git add docker/Dockerfile docker/gunicorn_conf.py docker/README.md
git commit -m "feat(docker): add multi-stage Dockerfile and gunicorn config"
```

---

### Task F2: Write compose files

**Files:**
- Create: `docker/docker-compose.yml`
- Create: `docker/docker-compose.dev.yml`
- Create: `docker/docker-compose.prod.yml`
- Create: `docker/docker-compose.npm.yml`

- [ ] **Step 1: Write base compose**

Create `docker/docker-compose.yml`:

```yaml
services:
  genereview-link:
    build:
      context: ..
      dockerfile: docker/Dockerfile
      target: production

    container_name: genereview_link_server

    env_file:
      - path: ../.env
        required: false

    environment:
      GENEREVIEW_LINK_LOG_LEVEL: INFO
      GENEREVIEW_LINK_HOST: 0.0.0.0
      GENEREVIEW_LINK_PORT: 8000

    ports:
      - "${GENEREVIEW_LINK_PORT:-8000}:8000"

    command: ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s

    deploy:
      resources:
        limits:
          memory: 512M
        reservations:
          memory: 256M

    restart: unless-stopped

    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

- [ ] **Step 2: Write dev overlay**

Create `docker/docker-compose.dev.yml`:

```yaml
# Development overlay with hot-reloading
# Usage: docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up --build

services:
  genereview-link:
    build:
      context: ..
      dockerfile: docker/Dockerfile
      target: builder

    environment:
      GENEREVIEW_LINK_LOG_LEVEL: DEBUG
      PYTHONPATH: /home/app/web

    volumes:
      - ../genereview_link:/home/app/web/genereview_link:delegated
      - ../server.py:/home/app/web/server.py:ro
      - ../mcp_server.py:/home/app/web/mcp_server.py:ro
      - ../pyproject.toml:/home/app/web/pyproject.toml:ro
      - ../README.md:/home/app/web/README.md:ro

    command: >
      sh -c "
        cd /home/app/web &&
        pip install -e . &&
        uvicorn server:app --host 0.0.0.0 --port 8000 --reload --reload-dir genereview_link
      "

    user: root

    working_dir: /home/app/web
```

- [ ] **Step 3: Write prod overlay**

Create `docker/docker-compose.prod.yml`:

```yaml
# Production overlay
# Usage: docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml up -d

services:
  genereview-link:
    environment:
      GENEREVIEW_LINK_LOG_LEVEL: INFO
      LOG_JSON: "true"
      GUNICORN_WORKERS: 4
      GUNICORN_LOG_LEVEL: warning

    volumes: []
    ports: !reset []

    read_only: true
    tmpfs:
      - /tmp/genereview-link:rw,noexec,nosuid,size=64m

    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL

    pids_limit: 256
    init: true

    command: ["gunicorn", "-c", "gunicorn_conf.py", "server:app"]

    deploy:
      resources:
        limits:
          memory: 1G
          cpus: '1.0'
          pids: 256
        reservations:
          memory: 512M
          cpus: '0.5'

      restart_policy:
        condition: on-failure
        delay: 5s
        max_attempts: 3
        window: 120s

    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 20s
      timeout: 5s
      retries: 5
      start_period: 30s

    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "5"
        labels: "service=genereview-link,environment=production"
```

- [ ] **Step 4: Write npm overlay**

Create `docker/docker-compose.npm.yml`:

```yaml
# Nginx Proxy Manager overlay
# Usage: docker compose -f docker/docker-compose.yml -f docker/docker-compose.npm.yml up -d

services:
  genereview-link:
    ports: !reset []
    expose:
      - "8000"
    networks:
      - npm-network

networks:
  npm-network:
    external: true
    name: ${NPM_NETWORK_NAME:-npm_network}
```

- [ ] **Step 5: Commit**

```bash
git add docker/docker-compose.yml docker/docker-compose.dev.yml docker/docker-compose.prod.yml docker/docker-compose.npm.yml
git commit -m "feat(docker): add base, dev, prod, and npm compose overlays"
```

---

### Task F3: Build and smoke-test the image

**Files:** none

- [ ] **Step 1: Build the image**

Run: `make docker-build`
Expected: Build succeeds and the final image is created. If a step fails, fix the Dockerfile in place and re-run.

- [ ] **Step 2: Start the service**

Run: `make docker-up`
Expected: Container starts and reports healthy within ~30 seconds. Check with: `docker ps`.

- [ ] **Step 3: Verify /health responds**

Run: `curl -fsS http://localhost:8000/health`
Expected: `{"status": "healthy", "client_health": {...}}`.

- [ ] **Step 4: Verify /metrics responds**

Run: `curl -fsS http://localhost:8000/metrics | head -20`
Expected: Prometheus exposition text including `genereview_requests_total`.

- [ ] **Step 5: Tear down**

Run: `make docker-down`
Expected: Container stops and is removed.

- [ ] **Step 6: Commit any Dockerfile fixes (if needed)**

If any Dockerfile fixes were needed:

```bash
git add docker/
git commit -m "fix(docker): adjust Dockerfile for smoke test"
```

---

## Phase G — CI

### Task G1: Add ci.yml workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  pull_request:
  push:
    branches:
      - main

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  quality:
    name: Format, lint, typecheck, tests, and coverage
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.12", "3.13"]

    steps:
      - name: Checkout
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6

      - name: Set up Python
        uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6
        with:
          python-version: ${{ matrix.python-version }}

      - name: Set up uv
        uses: astral-sh/setup-uv@94527f2e458b27549849d47d273a16bec83a01e9 # v7
        with:
          enable-cache: true
          version: "0.8.7"

      - name: Install dependencies
        run: uv sync --group dev --frozen

      - name: Run local CI checks
        run: make ci-local

      - name: Run coverage
        run: make test-cov
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add CI workflow running make ci-local on py3.12 and py3.13"
```

---

### Task G2: Add security.yml workflow

**Files:**
- Create: `.github/workflows/security.yml`

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/security.yml`:

```yaml
name: Security

on:
  pull_request:
  push:
    branches:
      - main
  schedule:
    - cron: "17 3 * * 1"

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  codeql:
    name: CodeQL
    runs-on: ubuntu-latest
    if: ${{ !github.event.repository.private }}
    permissions:
      actions: read
      contents: read
      security-events: write

    steps:
      - name: Checkout
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6

      - name: Initialize CodeQL
        uses: github/codeql-action/init@ed410739ba306e4ebe5e123421a6bd694e494a2b # v4
        with:
          languages: python
          build-mode: none

      - name: Analyze
        uses: github/codeql-action/analyze@ed410739ba306e4ebe5e123421a6bd694e494a2b # v4

  dependency-review:
    name: Dependency review
    runs-on: ubuntu-latest
    if: github.event_name == 'pull_request'
    permissions:
      contents: read
      pull-requests: read

    steps:
      - name: Checkout
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6

      - name: Dependency Review
        uses: actions/dependency-review-action@2031cfc080254a8a887f58cffee85186f0e49e48 # v4.9.0
        continue-on-error: true
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/security.yml
git commit -m "ci: add CodeQL and dependency-review security workflow"
```

---

### Task G3: Add docker.yml workflow

**Files:**
- Create: `.github/workflows/docker.yml`

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/docker.yml`:

```yaml
name: Docker

on:
  pull_request:
    paths:
      - "docker/**"
      - "Dockerfile"
      - "pyproject.toml"
      - "uv.lock"
  push:
    branches:
      - main
    paths:
      - "docker/**"
      - "Dockerfile"
      - "pyproject.toml"
      - "uv.lock"

permissions:
  contents: read

jobs:
  build:
    name: Build and smoke-test image
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: docker/Dockerfile
          push: false
          load: true
          tags: genereview-link:ci

      - name: Run container and check health
        run: |
          docker run --rm -d --name gr-ci -p 8000:8000 genereview-link:ci
          for i in $(seq 1 30); do
            if curl -fsS http://localhost:8000/health; then
              echo "healthy"
              break
            fi
            sleep 2
          done
          docker stop gr-ci
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/docker.yml
git commit -m "ci: add docker build + smoke-test workflow"
```

---

### Task G4: Add container-security.yml workflow

**Files:**
- Create: `.github/workflows/container-security.yml`

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/container-security.yml`:

```yaml
name: Container security

on:
  push:
    branches:
      - main
    paths:
      - "docker/**"
      - "Dockerfile"
      - "pyproject.toml"
      - "uv.lock"
  schedule:
    - cron: "23 4 * * 1"

permissions:
  contents: read

jobs:
  trivy:
    name: Trivy scan
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write

    steps:
      - name: Checkout
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: docker/Dockerfile
          push: false
          load: true
          tags: genereview-link:scan

      - name: Run Trivy scanner
        uses: aquasecurity/trivy-action@0.28.0
        with:
          image-ref: genereview-link:scan
          format: sarif
          output: trivy-results.sarif
          severity: CRITICAL,HIGH
          ignore-unfixed: true

      - name: Upload SARIF
        uses: github/codeql-action/upload-sarif@ed410739ba306e4ebe5e123421a6bd694e494a2b # v4
        with:
          sarif_file: trivy-results.sarif
        if: always() && !github.event.repository.private
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/container-security.yml
git commit -m "ci: add Trivy container vulnerability scan"
```

---

### Task G5: Add release.yml workflow

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    tags:
      - "v*"

permissions:
  contents: read

jobs:
  build-and-publish:
    name: Build wheel and publish to TestPyPI
    runs-on: ubuntu-latest
    environment: release

    steps:
      - name: Checkout
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6

      - name: Set up Python
        uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6
        with:
          python-version: "3.12"

      - name: Set up uv
        uses: astral-sh/setup-uv@94527f2e458b27549849d47d273a16bec83a01e9 # v7
        with:
          enable-cache: true
          version: "0.8.7"

      - name: Build distributions
        run: uv build

      - name: Publish to TestPyPI
        env:
          UV_PUBLISH_USERNAME: __token__
          UV_PUBLISH_PASSWORD: ${{ secrets.TEST_PYPI_API_TOKEN }}
        run: uv publish --publish-url https://test.pypi.org/legacy/
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add tag-triggered release workflow publishing to TestPyPI"
```

---

### Task G6: Add dependabot.yml and PR template

**Files:**
- Create: `.github/dependabot.yml`
- Create: `.github/pull_request_template.md`

- [ ] **Step 1: Write dependabot.yml**

Create `.github/dependabot.yml`:

```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 5
    labels:
      - "dependencies"
      - "python"

  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 5
    labels:
      - "dependencies"
      - "github-actions"

  - package-ecosystem: "docker"
    directory: "/docker"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 3
    labels:
      - "dependencies"
      - "docker"
```

- [ ] **Step 2: Write PR template**

Create `.github/pull_request_template.md`:

```markdown
## Summary

<!-- 1-3 bullets describing what changed and why -->

## Test plan

- [ ] `make ci-local` passes locally
- [ ] New behavior covered by tests
- [ ] Manual smoke test (if applicable)

## Notes for reviewers

<!-- Anything reviewers should know — risks, follow-ups, deviations from AGENTS.md -->
```

- [ ] **Step 3: Commit**

```bash
git add .github/dependabot.yml .github/pull_request_template.md
git commit -m "ci: add dependabot config and PR template"
```

---

## Phase H — Coverage and final verification

### Task H1: Measure coverage and bring it to 70

**Files:** depends on coverage report

- [ ] **Step 1: Run coverage**

Run: `make test-cov`
Expected: Test report ends with a coverage percentage. Read the percentage and the "Missing" column.

- [ ] **Step 2: Identify the top three lowest-covered files**

Look at the `htmlcov/index.html` report (or terminal output) for the three files with the lowest coverage percentage that are above ~50 LOC.

- [ ] **Step 3: For each of the three files, add or extend tests**

For each low-coverage file, write tests that exercise the uncovered branches. Stay disciplined:
- Only test behavior that exists. Do not add tests that pin implementation detail.
- Prefer integration-style tests through the public surface (route, service method) over unit tests of private helpers.
- Use existing fixtures from `tests/fixtures/` where possible.

Re-run `make test-cov` after each file's tests are added. Verify the per-file percentage goes up.

- [ ] **Step 4: Confirm overall coverage ≥ 70**

Run: `make test-cov`
Expected: Final line shows total coverage at or above 70%.

If still under 70%, repeat step 3 with the next-lowest-covered file.

- [ ] **Step 5: Commit**

```bash
git add tests
git commit -m "test: raise coverage to 70% threshold floor"
```

---

### Task H2: Final golden gate

**Files:** none

- [ ] **Step 1: Run `make ci-local`**

Run: `make ci-local`
Expected: All four sub-targets pass.

- [ ] **Step 2: Run `make test-cov`**

Run: `make test-cov`
Expected: Coverage ≥ 70%.

- [ ] **Step 3: Build and run the docker image**

Run: `make docker-build && make docker-up`
Expected: container starts and is healthy.

- [ ] **Step 4: Verify /health and /metrics**

Run:

```bash
curl -fsS http://localhost:8000/health | head
curl -fsS http://localhost:8000/metrics | head -5
```

Expected: `/health` returns `{"status":"healthy",...}`. `/metrics` returns Prometheus text.

- [ ] **Step 5: Verify correlation ID round-trips**

Run:

```bash
curl -fsS -H "X-Request-ID: golden-gate-123" http://localhost:8000/health -i | grep -i "x-request-id"
```

Expected: response header `X-Request-ID: golden-gate-123`.

- [ ] **Step 6: stdio MCP smoke test**

Run:

```bash
docker exec genereview_link_server sh -c 'echo "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"smoke\",\"version\":\"0\"}}}" | python server.py serve --transport stdio'
```

Or, locally without docker:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' | uv run python server.py serve --transport stdio
```

Expected: a JSON-RPC response on stdout containing `"protocolVersion"` and `"serverInfo"` and no log lines mixed in (stdout must be clean).

- [ ] **Step 7: Tear down**

Run: `make docker-down`

- [ ] **Step 8: Final commit (only if any small fixups were needed above)**

```bash
git add -A
git commit -m "chore: final golden-gate fixups for modernization"
```

---

## Done

After all phases pass `make ci-local`, the repo is at parity with pubtator-link on:
- Build (hatchling + uv)
- Language target (py3.12)
- Tooling (ruff-only, mypy strict, pytest with markers and xdist)
- Security (defusedxml, asgi-correlation-id)
- Observability (prometheus /metrics, correlation-id propagation)
- CLI (Typer)
- MCP runtime (fastmcp 3.x or pinned 2.10.x with documented fallback)
- Container (multi-stage Dockerfile, gunicorn, compose overlays)
- CI (ci, security, docker, container-security, release, dependabot)
- Agents (AGENTS.md + slim CLAUDE.md, docs/superpowers/, .claude/skills/)
- Coverage floor (70%)
