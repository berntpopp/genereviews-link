.PHONY: help install lock upgrade sync format format-check lint lint-ci lint-fix lint-loc typecheck typecheck-fast typecheck-stop typecheck-fresh test test-fast test-unit test-integration test-cov test-cov-all test-all check ci-local precommit clean dev mcp-serve mcp-serve-http docker-build docker-up docker-down docker-logs bundle-validate bundle-publish-local cuda-check eval eval-baseline bench-ranking bench-ranking-validate

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

lint-loc: ## Enforce per-file line budget (see AGENTS.md "File Size Discipline")
	uv run python scripts/check_file_size.py

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

test-fast: ## Run fast non-integration tests in parallel with pytest-xdist
	uv run pytest tests -q -n auto -m "not integration and not slow"

test-unit: ## Run unit tests in parallel
	uv run pytest tests -q -n auto -m "not integration and not slow"

test-integration: ## Run integration tests (requires GENEREVIEW_TEST_DATABASE_URL)
	uv run pytest tests/integration/ -v

test-cov: ## Run unit tests with coverage (matches ci-local's selection)
	uv run pytest tests -m "not integration and not slow" --cov=genereview_link --cov-report=term-missing --cov-report=html --cov-report=xml

test-cov-all: ## Run full test suite with coverage (includes integration; needs NCBI access)
	uv run pytest tests --cov=genereview_link --cov-report=term-missing --cov-report=html --cov-report=xml

test-all: test-cov ## Alias for full test run with coverage

check: format lint ## Format and lint

ci-local: format-check lint-ci lint-loc typecheck-fast test-unit ## Run fast local CI-equivalent checks (unit tests only)

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

docker-up: ## Start Docker services (waits for the restore sidecar and a healthy app)
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml up -d --wait --wait-timeout 600

docker-down: ## Stop Docker services
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml down

docker-logs: ## Tail Docker service logs
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml logs -f

# --- Dev compose stack -------------------------------------------------------
# Wires the dockerized app at host port 8765 against the host's gr-pg corpus
# postgres on port 5436 (bypasses the empty in-stack postgres). Always layers
# docker-compose.override.gr-pg.yml so the non-standard ports + DATABASE_URL
# stick across rebuild/restart cycles. Run `gr-pg` separately (one-time):
#   docker run -d --name gr-pg --restart unless-stopped \
#     -p 127.0.0.1:5436:5432 \
#     -e POSTGRES_USER=genereview -e POSTGRES_PASSWORD=genereview \
#     -e POSTGRES_DB=genereview \
#     -v genereview_gr_pg:/var/lib/postgresql/data \
#     pgvector/pgvector:0.8.2-pg18
DOCKER_DEV_COMPOSE := $(DOCKER_COMPOSE) -f docker/docker-compose.yml -f docker/docker-compose.override.gr-pg.yml

docker-dev-build: ## Build dev image (uses gr-pg override)
	$(DOCKER_DEV_COMPOSE) build genereview-link

docker-dev-up: ## Start dev app container against host gr-pg on :5436 (publishes :8765)
	$(DOCKER_DEV_COMPOSE) up -d genereview-link

docker-dev-down: ## Stop dev app container (leaves host gr-pg running)
	$(DOCKER_DEV_COMPOSE) down genereview-link

docker-dev-rebuild: docker-dev-down docker-dev-build docker-dev-up ## Rebuild + restart dev app in one shot

docker-dev-logs: ## Tail dev app logs
	$(DOCKER_DEV_COMPOSE) logs -f genereview-link
# -----------------------------------------------------------------------------

db-migrate: ## Apply control + data migrations against $DATABASE_URL
	uv run genereview-link db migrate

db-shell: ## psql shell into the docker-compose postgres
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml -f docker/docker-compose.dev.yml exec postgres psql -U $${POSTGRES_USER:-genereview} -d genereview

db-reset: ## DROP and recreate genereview schemas (dev only)
	uv run genereview-link db reset --yes

ingest: ## Run full ingest pipeline (download → parse → write → swap)
	uv run genereview-link ingest

embed: ## Backfill embeddings + build HNSW index
	uv run genereview-link embed

bundle: ## Build release bundle from active corpus
	uv run genereview-link bundle build

bundle-validate: ## Validate active corpus is ready for bundle publishing
	uv run genereview-link bundle validate

bundle-publish-local: ## Build/publish corpus bundle locally; set RELEASE_ID=YYYY-MM-DD-rN
	uv run genereview-link bundle publish-local --release-id $${RELEASE_ID:?set RELEASE_ID=YYYY-MM-DD-rN}

cuda-check: ## Verify local PyTorch CUDA availability
	uv run python scripts/verify_torch_cuda.py

eval: ## Run MRR@10 / section-precision@5 against tests/eval/
	uv run python -m tests.eval.run_eval

eval-baseline: ## Re-capture baseline.json (requires explicit operator confirmation)
	@echo "Refusing — edit tests/eval/baseline.json by hand or via a tracked PR."
	@exit 1

bench-ranking:  ## Run the ranking benchmark; assumes MCP is up at MCP_BASE_URL (default http://127.0.0.1:8765).
	uv run python scripts/bench_ranking.py --bench tests/fixtures/ranking_bench.jsonl --json-out bench_ranking_results.json

bench-ranking-validate:  ## Re-validate the benchmark fixture; assumes MCP is up.
	uv run python scripts/validate_ranking_bench.py --input tests/fixtures/ranking_bench.jsonl
