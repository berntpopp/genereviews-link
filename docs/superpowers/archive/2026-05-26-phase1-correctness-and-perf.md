# Phase 1 Correctness And Performance Implementation Plan

**Status:** Completed in PR #53, merged to `main` as
> Historical record

`4496721b5bbe58106220b92baf20e6fd6aa85da6`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the first remediation phase from the 2026-05-25 senior
engineering review (findings #1, #2, #4, #11, #12, #19) plus one
enabling refactor (extract `server_lifecycle.py`) as one PR with one
atomic commit per item. No schema changes, no public API changes, no
defusedxml-violating XML touches.

**Architecture:** Surgical edits to `genereview_service.py`,
`db/pool.py`, `retrieval/repository.py`, `config.py`,
`tests/integration/conftest.py`, plus one pure-refactor commit that
extracts `_bootstrap`, `_bundle_bootstrap_paths`, and the new
`_sha256_stream` / `_initialize_state` / `_teardown_state` into
`genereview_link/server_lifecycle.py`. The refactor commit also lowers
the `.loc-allowlist` entry for `server_manager.py`. Each commit is
independently revertable.

**Tech Stack:** Python 3.12, FastAPI, FastMCP, asyncpg, async_lru,
pytest, pytest-asyncio, Ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-05-26-phase1-correctness-and-perf-design.md`

**Branch:** `feat/phase1-correctness-and-perf`

---

## File Map

**Modify:**

- `genereview_link/server_manager.py` — drop `mcp_custom_names` dict and
  `mcp_names=` kwarg (Task 1, #19); have `UnifiedServerManager.lifespan`
  and `start_stdio_server` import and call
  `server_lifecycle._initialize_state` / `_teardown_state`
  (Tasks 5 and 7); pure-refactor commit (Task 5) removes the
  in-line `_bootstrap`, `_bundle_bootstrap_paths`, and lifespan body.
- `genereview_link/services/genereview_service.py` — pass
  `ttl=settings.CACHE_TTL_HOURS * 3600` to all three `alru_cache` calls;
  drop dead `self.cache_ttl` attribute (Task 2, #1).
- `genereview_link/db/pool.py` — keep `_init_conn` to pgvector
  registration only; thread `server_settings={"search_path":
  "genereview, public"}` and new tuning kwargs into `create_pool`
  (Tasks 3 and 4).
- `genereview_link/retrieval/repository.py` — delete the 13
  `await conn.execute("set search_path to genereview, public")` lines
  (Task 3, #11).
- `genereview_link/config.py` — add
  `DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S`,
  `DATABASE_COMMAND_TIMEOUT_S`, `DATABASE_STATEMENT_CACHE_SIZE`; bump
  `DATABASE_POOL_MAX_SIZE` default from `10 → 20` (Task 4, #12).
- `tests/integration/conftest.py` — add
  `server_settings={"search_path": "genereview, public"}` and use the
  production `_init_conn` so integration tests pick up the
  session-default `search_path` (Task 3, #11).
- `tests/test_config_database.py` — extend with assertions for the
  three new fields (Task 4, #12).
- `AGENTS.md` — add short "Postgres Connection" subsection noting
  `DATABASE_STATEMENT_CACHE_SIZE=0` for PgBouncer txn mode (Task 4, #12).
- `.loc-allowlist` — lower the `genereview_link/server_manager.py`
  ceiling to the new measured value after the Task 5 refactor.

**Create:**

- `tests/unit/test_mcp_tool_surface.py` — canonical tool names still
  present after `mcp_custom_names` removal (Task 1, #19).
- `tests/unit/test_genereview_service_cache_ttl.py` — `alru_cache` spy
  + mock-client cache-hit tests (Task 2, #1).
- `tests/unit/test_pool_search_path.py` — `create_pool` passes
  `server_settings={"search_path": ...}` (Task 3, #11); extended
  with `create_pool` kwargs pass-through and default `max_size=20`
  (Task 4, #12).
- `tests/integration/test_pool_search_path_survives_reset.py` —
  acquire/release/acquire integration test proving `search_path`
  survives asyncpg's RESET ALL (Task 3, #11).
- `genereview_link/server_lifecycle.py` — new module owning
  `_bootstrap`, `_bundle_bootstrap_paths`, `_sha256_stream`,
  `_initialize_state`, `_teardown_state` (Tasks 5, 6, 7).
- `tests/unit/test_bootstrap_tarfile_security.py` — manifest
  re-verify, expected-set, duplicate-member, `filter="data"`-required,
  `jobs=None` fallback (Task 6, #4).
- `tests/unit/test_server_lifecycle.py` — `_initialize_state`
  bare-state path + `start_stdio_server` wiring regression (Task 7, #2).

---

## Task Ordering Rationale

1. **#19 first** — pure deletion, smallest diff. Baseline confidence.
2. **#1 second** — isolated `services/genereview_service.py`.
3. **#11 third** — uses `server_settings` (not `init=`); includes the
   integration conftest update and the acquire/release/acquire
   integration test that proves `search_path` survives asyncpg's
   `RESET ALL`.
4. **#12 fourth** — extends `create_pool` with the three new tuning
   kwargs and the default `max_size=20` bump.
5. **Pure refactor fifth** — extracts `server_lifecycle.py` from
   `server_manager.py`. Moves `_bootstrap`,
   `_bundle_bootstrap_paths`, and creates skeleton
   `_initialize_state` / `_teardown_state` that *exactly* mirror the
   current lifespan body (no behaviour change yet). Lowers
   `.loc-allowlist` for `server_manager.py`. The two following commits
   then add their logic to `server_lifecycle.py` without further
   inflating `server_manager.py`.
6. **#4 sixth** — tar hardening lands in
   `server_lifecycle._bootstrap`. Adds `_sha256_stream` helper.
7. **#2 seventh (last)** — `start_stdio_server` is wired to call the
   already-extracted `_initialize_state` / `_teardown_state`. This
   commit also moves the scheduler onto `app.state` and adds the
   defensive `getattr` guards in `_teardown_state`. Lands last so
   prior commits' tests run against the unchanged lifespan wiring.
8. **CI + PR** as Task 8.

---

### Task 1: Drop dead `mcp_custom_names` identity-mapped dict (#19)

**Files:**

- Modify: `genereview_link/server_manager.py:435-445, 479`
- Create: `tests/unit/test_mcp_tool_surface.py`

- [ ] **Step 1: Write the failing test first**

Create `tests/unit/test_mcp_tool_surface.py` with the following:

```python
"""Regression test: MCP tool surface keeps canonical names after the
identity-mapped ``mcp_custom_names`` dict is removed (#19)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from genereview_link.api.routes import abstract, fulltext, genereview, links, search
from genereview_link.api.routes import chapters as chapters_routes
from genereview_link.api.routes import license as license_routes
from genereview_link.api.routes import passages as passages_routes
from genereview_link.api.routes import tables as tables_routes
from genereview_link.config import ServerConfig
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider

CANONICAL_TOOLS = {
    "search_passages",
    "get_chapter_metadata",
    "get_chapter_section",
    "get_passage",
    "get_table",
    "get_passages_batch",
    "get_genereview_summary",
    "search_genereviews",
    "get_abstract",
    "get_links",
    "get_fulltext",
    "get_license",
}


def _build_app_with_state() -> FastAPI:
    """Stand up a minimal FastAPI app with the routes the MCP server walks."""
    app = FastAPI()
    app.include_router(search.router)
    app.include_router(abstract.router)
    app.include_router(links.router)
    app.include_router(fulltext.router)
    app.include_router(genereview.router)
    app.include_router(passages_routes.router)
    app.include_router(chapters_routes.router)
    app.include_router(tables_routes.router)
    app.include_router(license_routes.router)
    app.state.repository = None
    app.state.pool = None
    app.state.embedder = FakeEmbeddingProvider(dim=384)
    app.state.gene_index = None
    app.state.corpus_version = None
    app.state.dense_model_id = "test"
    app.state.embedding_dim = 384
    return app


@pytest.mark.asyncio
async def test_mcp_tools_keep_canonical_names_after_dict_removal() -> None:
    """The dead ``mcp_custom_names`` dict mapped every key to itself.
    Removing it must not rename or drop any canonical tool."""
    from genereview_link.server_manager import UnifiedServerManager

    app = _build_app_with_state()
    mgr = UnifiedServerManager()
    mcp = await mgr.create_mcp_server(app, ServerConfig())

    tools = await mcp.list_tools()
    tool_names = {tool.name for tool in tools}

    missing = CANONICAL_TOOLS - tool_names
    assert not missing, f"canonical tools missing after #19: {missing}"


def test_server_manager_no_longer_defines_mcp_custom_names() -> None:
    """Grep guard: the dead dict must not be reintroduced."""
    from pathlib import Path

    source = Path("genereview_link/server_manager.py").read_text()
    assert "mcp_custom_names" not in source, (
        "mcp_custom_names dict is dead code (every key mapped to itself); "
        "do not reintroduce it"
    )
```

- [ ] **Step 2: Run test to verify the grep-guard fails**

Run: `uv run pytest tests/unit/test_mcp_tool_surface.py::test_server_manager_no_longer_defines_mcp_custom_names -v`

Expected: FAIL with "mcp_custom_names is dead code ... do not reintroduce it"
(because the dict is still present on `main`).

The first test (`test_mcp_tools_keep_canonical_names_after_dict_removal`)
may pass or fail depending on FastMCP behaviour with identity-mapped
names — that does not matter for the TDD red gate; the grep test pins
the failure.

- [ ] **Step 3: Delete the dead dict and the kwarg**

In `genereview_link/server_manager.py`, delete the entire block at
lines 435-445 (currently the `mcp_custom_names = {...}` literal) AND
the `mcp_names=mcp_custom_names,` kwarg on the `FastMCP.from_fastapi`
call (around line 479).

After the edit, `create_mcp_server` should call:

```python
mcp = FastMCP.from_fastapi(
    app=app,
    name="GeneReview Link Tool",
    instructions=(
        "GeneReview-Link grounds gene-disease questions in NCBI GeneReviews.\n\n"
        ... (instructions string unchanged) ...
    ),
    route_maps=mcp_route_maps,
)
```

- [ ] **Step 4: Run tests to verify both pass**

Run: `uv run pytest tests/unit/test_mcp_tool_surface.py -v`

Expected: both tests PASS.

- [ ] **Step 5: Run focused make target**

Run: `make test-fast`

Expected: no regression elsewhere.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/server_manager.py tests/unit/test_mcp_tool_surface.py
git commit -m "refactor(mcp): drop dead mcp_custom_names identity-mapped dict (#19)"
```

---

### Task 2: Wire `CACHE_TTL_HOURS` into `alru_cache` instances (#1)

**Files:**

- Modify: `genereview_link/services/genereview_service.py:43-52`
- Create: `tests/unit/test_genereview_service_cache_ttl.py`

- [ ] **Step 1: Write the failing tests first**

Create `tests/unit/test_genereview_service_cache_ttl.py`:

```python
"""Tests for #1: CACHE_TTL_HOURS must reach every alru_cache wrapper.

The upstream ``async_lru`` library tests its own TTL semantics; this
file verifies the wiring contract (that we pass ``ttl`` at all) and one
behavioural smoke test (that the cache is reached on the second call).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from genereview_link import config as config_mod
from genereview_link.services import genereview_service as svc_mod


def test_ttl_kwarg_is_passed_to_alru_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spy on ``alru_cache`` at construction time and assert every
    cached method gets ``ttl=CACHE_TTL_HOURS * 3600``."""
    captured: list[dict[str, object]] = []

    def fake_alru_cache(maxsize: int, *, ttl: float | None = None):
        captured.append({"maxsize": maxsize, "ttl": ttl})

        def decorator(fn):
            return fn

        return decorator

    monkeypatch.setattr(svc_mod, "alru_cache", fake_alru_cache)
    monkeypatch.setattr(config_mod.settings, "CACHE_TTL_HOURS", 2)
    monkeypatch.setattr(config_mod.settings, "CACHE_SIZE", 99)

    svc_mod.GeneReviewService()

    assert len(captured) == 3, (
        f"expected alru_cache called for 3 methods, got {len(captured)}"
    )
    for call in captured:
        assert call["maxsize"] == 99
        assert call["ttl"] == 7200, (
            f"TTL must be CACHE_TTL_HOURS * 3600 = 7200, got {call['ttl']}"
        )


@pytest.mark.asyncio
async def test_cache_hits_underlying_client_only_once_within_ttl() -> None:
    """Inject a mock client through the constructor seam; await the
    cached wrapper twice; assert the client was called only once."""
    mock_client = AsyncMock()
    mock_client.search_genereview_pmid = AsyncMock(return_value="12345")
    mock_client.get_book_url_from_pmid = AsyncMock(
        return_value="https://www.ncbi.nlm.nih.gov/books/NBK1247/"
    )
    mock_client.scrape_genereview_book = AsyncMock(
        return_value={"title": {"content": "BRCA1 Summary"}}
    )

    service = svc_mod.GeneReviewService(client=mock_client)

    r1 = await service.get_genereview("BRCA1")
    r2 = await service.get_genereview("BRCA1")

    assert r1.gene_symbol == "BRCA1"
    assert r2.gene_symbol == "BRCA1"
    assert mock_client.search_genereview_pmid.await_count == 1, (
        "cached wrapper bypassed — alru_cache may have been removed"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_genereview_service_cache_ttl.py -v`

Expected: `test_ttl_kwarg_is_passed_to_alru_cache` FAILs with
`TTL must be CACHE_TTL_HOURS * 3600 = 7200, got None`.
The mock-client test will PASS today (alru_cache is wired, just
without TTL). That is fine — it's a regression guard for later, not
the red gate for this commit.

- [ ] **Step 3: Apply the fix**

In `genereview_link/services/genereview_service.py`, replace lines
43-52 (the `__init__` body that constructs the cached methods):

```python
def __init__(self, client: EutilsClient | None = None):
    """Initialize the GeneReview service.

    Args:
        client: Optional EutilsClient instance, creates new one if None.
    """
    self.client = client or EutilsClient()

    ttl_seconds = settings.CACHE_TTL_HOURS * 3600
    self.get_genereview = alru_cache(
        maxsize=settings.CACHE_SIZE, ttl=ttl_seconds
    )(self._get_genereview_impl)
    self.get_genereview_comprehensive = alru_cache(
        maxsize=settings.CACHE_SIZE, ttl=ttl_seconds
    )(self._get_genereview_comprehensive_cached_impl)
    self.get_genereview_comprehensive_indexed = alru_cache(
        maxsize=settings.CACHE_SIZE, ttl=ttl_seconds
    )(self._get_genereview_comprehensive_indexed_impl)
```

Also delete the unused import at the top of the file:

```python
from datetime import timedelta
```

(`timedelta` is no longer used after `self.cache_ttl` is removed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_genereview_service_cache_ttl.py -v`

Expected: both tests PASS.

- [ ] **Step 5: Run the broader service test suite for regression**

Run: `uv run pytest tests/test_genereview_service.py -v`

Expected: PASS. Investigate any failures; the TTL wiring should be
behavior-compatible (entries still cache the same way; they just also
expire after `CACHE_TTL_HOURS`).

- [ ] **Step 6: Run focused make target**

Run: `make test-fast`

Expected: green.

- [ ] **Step 7: Commit**

```bash
git add genereview_link/services/genereview_service.py tests/unit/test_genereview_service_cache_ttl.py
git commit -m "fix(services): wire CACHE_TTL_HOURS into alru_cache instances (#1)"
```

---

### Task 3: Use `server_settings` for connection-default search_path (#11)

**Files:**

- Modify: `genereview_link/db/pool.py:25-42` (`create_pool` adds
  `server_settings`; `_init_conn` untouched, stays at pgvector only)
- Modify: `genereview_link/retrieval/repository.py` (lines 342, 437,
  454, 468, 488, 512, 596, 662, 746, 780, 805, 845, 863)
- Modify: `tests/integration/conftest.py:74-84`
- Create: `tests/unit/test_pool_search_path.py`
- Create: `tests/integration/test_pool_search_path_survives_reset.py`

**Background — why `server_settings` and not `init=`:**

asyncpg's pool runs `Connection.reset` on every release, and per the
asyncpg docstring this resets *all session configuration variables*
to their defaults. Any `set search_path` issued from `init=` (or
`setup=`) survives only the first acquire — the next release wipes
it, every subsequent acquire sees the session-default
`"$user, public"`, and repository queries hit the wrong schema.

`server_settings={"search_path": "genereview, public"}` is forwarded
to `asyncpg.connect`, which sends `search_path` as a Postgres startup
parameter. That becomes the **session default**, and `RESET ALL`
reverts to *this* value rather than `"$user, public"`. Zero
per-acquire cost.

- [ ] **Step 1: Write the failing unit tests first**

Create `tests/unit/test_pool_search_path.py`:

```python
"""Tests for #11 and #12: pool wiring (search_path via server_settings,
plus tuning kwargs)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_create_pool_passes_search_path_server_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``create_pool`` must thread ``server_settings={'search_path':
    'genereview, public'}`` into ``asyncpg.create_pool``. This is the
    only correct mechanism — ``init=`` would be wiped by RESET ALL
    on every pool release."""
    import asyncpg

    from genereview_link import config as config_mod
    from genereview_link.db import pool as pool_mod

    captured: dict[str, object] = {}

    async def fake_create_pool(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(config_mod.settings, "DATABASE_URL", "postgres://u:p@h:5432/db")

    await pool_mod.create_pool()

    assert captured["server_settings"] == {"search_path": "genereview, public"}, (
        "search_path must be a startup parameter so it survives RESET ALL "
        "on pool release (#11)"
    )


def test_repository_module_has_no_set_search_path_calls() -> None:
    """Regression guard: per-query ``set search_path`` calls must not
    be reintroduced into the repository module."""
    source = Path("genereview_link/retrieval/repository.py").read_text()
    assert "set search_path" not in source, (
        "Per-query 'set search_path' calls in repository.py defeat the "
        "connection-default optimization (#11). Use the server_settings "
        "param on create_pool."
    )
```

Create `tests/integration/test_pool_search_path_survives_reset.py`:

```python
"""Integration test for #11: search_path must survive asyncpg's
RESET ALL on pool release.

This is the test that distinguishes ``server_settings=`` (correct)
from ``init=`` (broken after first release). Requires a real test
Postgres via ``GENEREVIEW_TEST_DATABASE_URL``; skipped otherwise.
"""

from __future__ import annotations

import asyncpg
import pytest


@pytest.mark.asyncio
async def test_search_path_survives_pool_release_and_reacquire(
    pool: asyncpg.Pool,
) -> None:
    """Acquire A, observe search_path; release A; acquire B (likely
    same physical conn after reset); search_path must still be the
    one we configured."""
    async with pool.acquire() as conn_a:
        sp_a = await conn_a.fetchval("show search_path")
        assert sp_a == "genereview, public", (
            f"first acquire: expected 'genereview, public', got {sp_a!r}"
        )
    # Pool release runs Connection.reset → RESET ALL. If search_path
    # was set via init=, the next acquire would see the session
    # default ('"$user", public'). With server_settings= it must
    # still be 'genereview, public'.
    async with pool.acquire() as conn_b:
        sp_b = await conn_b.fetchval("show search_path")
        assert sp_b == "genereview, public", (
            f"after RESET ALL: expected 'genereview, public', got "
            f"{sp_b!r} — search_path was wiped (probably moved back "
            "to init= instead of server_settings=)"
        )
```

- [ ] **Step 2: Run tests to verify both fail (unit) and skip
  (integration without DB)**

Run: `uv run pytest tests/unit/test_pool_search_path.py -v`

Expected:
- `test_create_pool_passes_search_path_server_setting` FAILs with
  `KeyError: 'server_settings'` (current `create_pool` doesn't pass it).
- `test_repository_module_has_no_set_search_path_calls` FAILs with
  "Per-query 'set search_path' calls in repository.py..."

Run: `uv run pytest tests/integration/test_pool_search_path_survives_reset.py -v`

Expected: SKIP ("GENEREVIEW_TEST_DATABASE_URL not set") if no test
DB; FAIL on the second `show search_path` assertion if `init=` is
used (it currently isn't set up to put `search_path` anywhere, so
test fails because of the first assertion).

- [ ] **Step 3: Update `create_pool` to use `server_settings`**

In `genereview_link/db/pool.py`, replace `create_pool` (lines 25-42)
with:

```python
async def create_pool() -> asyncpg.Pool:
    """Create an asyncpg pool from settings.

    Reads ``config.settings`` lazily (at call time, not import time)
    so tests that reassign ``genereview_link.config.settings`` see
    updated values.

    ``server_settings={"search_path": "genereview, public"}`` is sent
    as a Postgres startup parameter; this becomes the session default
    that RESET ALL reverts to, so the connection-default search_path
    survives every pool release/reacquire cycle (#11).

    Raises:
        RuntimeError: if DATABASE_URL is not configured.
    """
    s = config.settings
    if not s.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return await asyncpg.create_pool(
        dsn=s.DATABASE_URL,
        min_size=s.DATABASE_POOL_MIN_SIZE,
        max_size=s.DATABASE_POOL_MAX_SIZE,
        server_settings={"search_path": "genereview, public"},
        init=_init_conn,
    )
```

`_init_conn` is **unchanged**. It only registers the pgvector codec
(setting search_path inside `_init_conn` would be wiped by every
release).

- [ ] **Step 4: Delete the 13 per-query `set search_path` calls in
  repository.py**

In `genereview_link/retrieval/repository.py`, delete the line
`await conn.execute("set search_path to genereview, public")` at each
position. Use grep to enumerate current positions (line numbers shift
as deletions land):

```bash
grep -n 'set search_path' genereview_link/retrieval/repository.py
```

Delete each match. After the edit:

```bash
grep -c 'set search_path' genereview_link/retrieval/repository.py
```

Expected: `0`.

- [ ] **Step 5: Update integration conftest with `server_settings`**

In `tests/integration/conftest.py`, replace the `pool` fixture (lines
74-84) with:

```python
@pytest_asyncio.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    """Yield a pool against the test Postgres; wipe genereview state before and after.

    Mirrors the production pool's session-default contract: passes
    ``server_settings`` so ``search_path`` survives asyncpg's RESET
    ALL on release (#11), and uses the production ``_init_conn`` for
    pgvector codec registration.
    """
    from genereview_link.db.pool import _init_conn

    url = _database_url()
    pool = await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=4,
        server_settings={"search_path": "genereview, public"},
        init=_init_conn,
    )
    await _wipe(pool)
    yield pool
    await _wipe(pool)
    await pool.close()
```

The standalone `import pgvector.asyncpg` at the top of the conftest
(line 10) is no longer referenced by the fixture; verify with
`grep -n 'pgvector' tests/integration/conftest.py`, and drop the
import if it has no remaining references.

- [ ] **Step 6: Run the new unit tests**

Run: `uv run pytest tests/unit/test_pool_search_path.py -v`

Expected: both PASS.

- [ ] **Step 7: Run repository-related fast tests**

Run: `uv run pytest tests/ -k repository -v --no-header`

Expected: PASS. Any failure points to a missed `set search_path`
deletion or a typo in the conftest update.

- [ ] **Step 8: (Optional, when test Postgres is available) run the
  integration test**

Run: `GENEREVIEW_TEST_DATABASE_URL=postgres://...test... uv run pytest tests/integration/test_pool_search_path_survives_reset.py -v`

Expected: PASS. SKIPped CI runs still satisfy `ci-local`.

- [ ] **Step 9: Run focused make target**

Run: `make test-fast`

Expected: green.

- [ ] **Step 10: Commit**

```bash
git add genereview_link/db/pool.py genereview_link/retrieval/repository.py tests/integration/conftest.py tests/unit/test_pool_search_path.py tests/integration/test_pool_search_path_survives_reset.py
git commit -m "perf(db): use server_settings for search_path connection-default (#11)"
```

---

### Task 4: Production-tune asyncpg pool kwargs and defaults (#12)

**Files:**

- Modify: `genereview_link/config.py:32-34`
- Modify: `genereview_link/db/pool.py:25-42`
- Modify: `tests/test_config_database.py`
- Modify: `tests/unit/test_pool_search_path.py` (extend)
- Modify: `AGENTS.md` (add subsection)

- [ ] **Step 1: Write the failing tests first**

Append to `tests/unit/test_pool_search_path.py`:

```python
@pytest.mark.asyncio
async def test_create_pool_passes_tuning_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``create_pool`` must thread the three new tuning settings into
    ``asyncpg.create_pool``."""
    import asyncpg

    from genereview_link import config as config_mod
    from genereview_link.db import pool as pool_mod

    captured: dict[str, object] = {}

    async def fake_create_pool(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(config_mod.settings, "DATABASE_URL", "postgres://u:p@h:5432/db")
    monkeypatch.setattr(
        config_mod.settings,
        "DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S",
        120.0,
    )
    monkeypatch.setattr(config_mod.settings, "DATABASE_COMMAND_TIMEOUT_S", 30.0)
    monkeypatch.setattr(config_mod.settings, "DATABASE_STATEMENT_CACHE_SIZE", 0)

    await pool_mod.create_pool()

    assert captured["max_inactive_connection_lifetime"] == 120.0
    assert captured["command_timeout"] == 30.0
    assert captured["statement_cache_size"] == 0


def test_default_pool_max_size_is_20() -> None:
    """The default pool size must rise from 10 to 20 for unified mode."""
    import os
    from unittest.mock import patch

    from genereview_link.config import Settings

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DATABASE_POOL_MAX_SIZE", None)
        s = Settings()
        assert s.DATABASE_POOL_MAX_SIZE == 20
```

Append to `tests/test_config_database.py` after the existing tests:

```python
def test_new_database_tuning_fields_have_documented_defaults() -> None:
    """Phase 1 #12 added three tuning fields; the defaults match the
    spec's Contract Changes table."""
    import os
    from unittest.mock import patch

    with patch.dict(os.environ, {}, clear=False):
        for var in (
            "DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S",
            "DATABASE_COMMAND_TIMEOUT_S",
            "DATABASE_STATEMENT_CACHE_SIZE",
        ):
            os.environ.pop(var, None)
        settings = Settings()
        assert settings.DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S == 300.0
        assert settings.DATABASE_COMMAND_TIMEOUT_S is None
        assert settings.DATABASE_STATEMENT_CACHE_SIZE == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_pool_search_path.py tests/test_config_database.py -v`

Expected: `test_create_pool_passes_tuning_kwargs` FAILs with
`KeyError: 'max_inactive_connection_lifetime'`;
`test_default_pool_max_size_is_20` FAILs with `assert 10 == 20`;
`test_new_database_tuning_fields_have_documented_defaults` FAILs with
`AttributeError: 'Settings' object has no attribute
'DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S'`.

- [ ] **Step 3: Add the new settings and bump the max-size default**

In `genereview_link/config.py`, replace lines 32-34 with:

```python
DATABASE_URL: str = ""
DATABASE_POOL_MIN_SIZE: int = 2
DATABASE_POOL_MAX_SIZE: int = 20
DATABASE_ACQUIRE_TIMEOUT_S: float = 5.0
# asyncpg default is 300.0; expose as a tunable so operators behind
# aggressive idle-kill firewalls (e.g. PgBouncer with
# ``server_idle_timeout`` < 300) can lower it.
DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S: float = 300.0
# asyncpg default = no timeout. Set a positive number of seconds in
# production to prevent a pathological query from pinning a connection.
DATABASE_COMMAND_TIMEOUT_S: float | None = None
# asyncpg default = 100. Set to 0 when running behind PgBouncer in
# transaction-pooling mode (PgBouncer txn mode does not preserve
# prepared statements across queries).
DATABASE_STATEMENT_CACHE_SIZE: int = 100
```

- [ ] **Step 4: Thread the new kwargs into `create_pool`**

In `genereview_link/db/pool.py`, replace `create_pool` (lines 25-42) with:

```python
async def create_pool() -> asyncpg.Pool:
    """Create an asyncpg pool from settings.

    Reads ``config.settings`` lazily (at call time, not import time) so tests
    that reassign ``genereview_link.config.settings`` see updated values.

    Raises:
        RuntimeError: if DATABASE_URL is empty.
    """
    s = config.settings
    if not s.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return await asyncpg.create_pool(
        dsn=s.DATABASE_URL,
        min_size=s.DATABASE_POOL_MIN_SIZE,
        max_size=s.DATABASE_POOL_MAX_SIZE,
        max_inactive_connection_lifetime=s.DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S,
        command_timeout=s.DATABASE_COMMAND_TIMEOUT_S,
        statement_cache_size=s.DATABASE_STATEMENT_CACHE_SIZE,
        server_settings={"search_path": "genereview, public"},
        init=_init_conn,
    )
```

(`server_settings` is preserved from Task 3.)

- [ ] **Step 5: Add the AGENTS.md subsection**

In `AGENTS.md`, after the "Coding Standards" section and before the
"File Size Discipline" section, insert:

```markdown
## Postgres Connection

The asyncpg pool is configured by `config.Settings` and constructed by
`db.pool.create_pool`. Operator-tunable knobs:

- `DATABASE_POOL_MIN_SIZE` / `DATABASE_POOL_MAX_SIZE` — pool bounds.
  Default `2 / 20`. Unified mode (HTTP + MCP + parallel retrieval) can
  acquire several connections per request via lexical/dense/metadata
  fanout, so a max of 20 is the sensible floor.
- `DATABASE_COMMAND_TIMEOUT_S` — per-query timeout in seconds.
  Default `None` (no timeout). Set a positive value in production to
  prevent a pathological query from pinning a connection.
- `DATABASE_STATEMENT_CACHE_SIZE` — asyncpg prepared-statement cache
  size. Default `100`. **Set to `0` when running behind PgBouncer in
  transaction-pooling mode**, otherwise asyncpg's prepared-statement
  cache breaks because PgBouncer txn mode does not preserve session
  state across queries.
- `DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S` — idle-connection
  lifetime in seconds. Default `300.0` (matches asyncpg's own default).
  Lower this if a cloud-Postgres firewall or PgBouncer is killing
  connections faster than 5 minutes.

```

- [ ] **Step 6: Run tests to verify all pass**

Run: `uv run pytest tests/unit/test_pool_search_path.py tests/test_config_database.py -v`

Expected: all PASS.

- [ ] **Step 7: Run focused make target**

Run: `make test-fast`

Expected: green.

- [ ] **Step 8: Commit**

```bash
git add genereview_link/config.py genereview_link/db/pool.py tests/test_config_database.py tests/unit/test_pool_search_path.py AGENTS.md
git commit -m "feat(db): production-tune asyncpg pool kwargs and defaults (#12)"
```

---

### Task 5: Extract `server_lifecycle.py` from `server_manager.py` (refactor)

**Goal:** Move `_bootstrap`, `_bundle_bootstrap_paths`, and the
lifespan startup/teardown bodies into a new module
`genereview_link/server_lifecycle.py`. Pure refactor — no behaviour
change. This commit creates the headroom that Tasks 6 (#4) and 7
(#2) need to add code without exceeding `server_manager.py`'s 618 LOC
allowlist ceiling.

**Files:**

- Create: `genereview_link/server_lifecycle.py` (~250 LOC after this
  task; grows by ~30 in Task 6 and ~10 in Task 7)
- Modify: `genereview_link/server_manager.py` — delete the moved code
  and `import` it from the new module
- Modify: `.loc-allowlist` — lower the
  `genereview_link/server_manager.py` ceiling to the new measured value

- [ ] **Step 1: Verify the starting state**

Run:

```bash
wc -l genereview_link/server_manager.py
grep '^genereview_link/server_manager.py:' .loc-allowlist
```

Expected: both report `618`.

- [ ] **Step 2: Create `genereview_link/server_lifecycle.py` with the
  moved code**

Create the new file. Copy `_bundle_bootstrap_paths` and `_bootstrap`
**verbatim** from `server_manager.py` (currently lines 61-153). Add
the body of `UnifiedServerManager.lifespan` as two top-level
coroutines `_initialize_state(app)` and `_teardown_state(app)` —
each is exactly the corresponding half of the lifespan, split at the
`yield`. Stash `pool` and `scheduler` onto `app.state` as locals
were used before (no defensive `getattr` yet — that lands in Task 7).

```python
"""Server lifecycle helpers: bundle bootstrap + per-app startup / teardown.

Extracted from ``server_manager.py`` so the file size discipline
(600-LOC budget per AGENTS.md) is sustainable while #4 (tar hardening)
and #2 (STDIO lifespan wiring) land. ``UnifiedServerManager`` imports
``_initialize_state`` and ``_teardown_state`` from here.
"""

from __future__ import annotations

import asyncio  # noqa: F401 — kept for symmetry with the orig module
import json
import os
import shutil
import tarfile as tf_mod
from pathlib import Path

import asyncpg
from fastapi import FastAPI

from genereview_link.api.client_manager import (
    get_client_manager,
    shutdown_clients,
)
from genereview_link.config import settings
from genereview_link.logging_config import get_logger
from genereview_link.services.service_manager import (
    get_service_manager,
    shutdown_services,
)

logger = get_logger("server.lifecycle")


def _bundle_bootstrap_paths(work_dir: Path) -> tuple[Path, Path]:
    """Return bundle tarball and extraction paths under the writable work dir."""
    return work_dir / "bundle.tar.gz", work_dir / "bundle_extract"


async def _bootstrap() -> None:
    """Bootstrap the corpus before the pool is opened for request serving.

    Three modes:
    1. BUNDLE_URL set → download + verify + pg_restore bundle.
    2. BUILD_LOCAL=true → run full local ingest pipeline.
    3. Neither → assume an external Postgres already has a corpus (or it's empty).

    In all cases, if an active corpus version already exists the function
    returns immediately (hot-path / already-populated).
    """
    from genereview_link.corpus.bundle import sha256_file
    from genereview_link.db.migrate import apply_control_migrations
    from genereview_link.db.pool import create_pool
    from genereview_link.ingest.github_release import (
        download_with_integrity,
        fetch_sibling_sha256,
        pg_restore,
        resolve_latest,
    )

    pool = await create_pool()
    try:
        applied = await apply_control_migrations(pool)
        if applied:
            logger.info("applied control migrations", versions=applied)

        active = await pool.fetchval(
            "select 1 from public.genereview_corpus_version where is_active"
        )
        if active:
            logger.info("active corpus found; skipping bootstrap")
            return

        bundle_url = settings.BUNDLE_URL
        if bundle_url == "latest":
            bundle_url = await resolve_latest(settings.GITHUB_REPO)
        if bundle_url:
            logger.info("downloading corpus bundle", url=bundle_url)
            sha = await fetch_sibling_sha256(bundle_url)
            work_dir = Path(settings.BUNDLE_BOOTSTRAP_DIR)
            tmp, extract_dir = _bundle_bootstrap_paths(work_dir)
            shutil.rmtree(extract_dir, ignore_errors=True)
            work_dir.mkdir(parents=True, exist_ok=True)
            await download_with_integrity(bundle_url, tmp, expected_sha256=sha)
            extract_dir.mkdir(parents=True, exist_ok=True)
            with tf_mod.open(tmp, "r:gz") as tar:
                tar.extractall(str(extract_dir))  # noqa: S202
            manifest = json.loads((extract_dir / "manifest.json").read_text())
            for relpath, expected in manifest["checksums"].items():
                actual = sha256_file(extract_dir / relpath)
                if actual != expected:
                    raise RuntimeError(f"manifest checksum mismatch on {relpath}")
            await pg_restore(
                extract_dir / "corpus.dump",
                database_url=settings.DATABASE_URL,
                jobs=os.cpu_count(),
            )
            logger.info("corpus bundle restored")
            return

        if settings.BUILD_LOCAL:
            logger.info("BUILD_LOCAL=true; running full local ingest")
            from genereview_link.corpus.pipeline import run_full_ingest
            from genereview_link.ingest.orchestrator import backfill_embeddings, build_hnsw_index
            from genereview_link.retrieval.embeddings import SentenceTransformerEmbeddingProvider

            await run_full_ingest(pool)
            await backfill_embeddings(pool, SentenceTransformerEmbeddingProvider())
            await build_hnsw_index(pool)
            logger.info("local ingest complete")
            return

        logger.warning(
            "no BUNDLE_URL or BUILD_LOCAL set and no active corpus; "
            "/passages/search will return 503 until corpus is loaded"
        )
    except asyncpg.PostgresError as exc:
        logger.warning("bootstrap failed; server will start without corpus", error=str(exc))
    finally:
        await pool.close()


async def _initialize_state(app: FastAPI) -> None:
    """Run bootstrap and attach all runtime state onto ``app.state``.

    Body is the verbatim startup half of the original
    ``UnifiedServerManager.lifespan`` (server_manager.py:184-289 on
    pre-refactor `main`). Task 7 (#2) adds the scheduler-on-app.state
    move; no other behaviour change.
    """
    logger.info(
        "Starting GeneReview Link Server",
        version="2.0.0",
        environment=settings.ENVIRONMENT,
    )

    if settings.DATABASE_URL:
        await _bootstrap()

    client_manager = await get_client_manager()
    service_manager = await get_service_manager()
    await client_manager.get_client()
    await service_manager.get_service()
    logger.info("Client and Service managers initialized.")

    app.state.pool = None
    app.state.repository = None
    if settings.DATABASE_URL:
        try:
            from genereview_link.db.pool import create_pool
            from genereview_link.retrieval.repository import GeneReviewRepository

            app.state.pool = await create_pool()
            app.state.repository = GeneReviewRepository(app.state.pool)
            logger.info("Postgres pool and repository initialised.")
        except Exception as exc:
            logger.warning(
                "Failed to create Postgres pool; /passages/* will 503.", error=str(exc)
            )
            app.state.pool = None
            app.state.repository = None
    else:
        logger.info("DATABASE_URL not set; skipping Postgres pool (repository unavailable).")

    from genereview_link.corpus.tokenizer import BGE_DIM, BGE_MODEL_NAME

    app.state.dense_model_id = BGE_MODEL_NAME
    app.state.embedding_dim = BGE_DIM

    app.state.corpus_version = None
    if app.state.repository is not None:
        try:
            cv = await app.state.repository.active_corpus_version()
            app.state.corpus_version = cv.version if cv is not None else None
            logger.info(
                "Active corpus version cached on app.state",
                corpus_version=app.state.corpus_version,
            )
        except Exception as exc:
            logger.warning(
                "Failed to read active corpus version; _meta will omit it.",
                error=str(exc),
            )

    app.state.gene_index = None
    if app.state.pool is not None:
        try:
            from genereview_link.services.gene_index import load_gene_index

            app.state.gene_index = await load_gene_index(app.state.pool)
            logger.info(
                "loaded gene_index",
                count=len(app.state.gene_index.symbols),
            )
        except Exception as exc:
            logger.warning("gene_index load failed", error=str(exc))

    if settings.GENEREVIEW_EAGER_LOAD_BGE:
        from genereview_link.retrieval.embeddings import SentenceTransformerEmbeddingProvider

        app.state.embedder = SentenceTransformerEmbeddingProvider(
            device=settings.INGEST_EMBED_DEVICE
        )
        logger.info("BGE SentenceTransformer embedding provider loaded.")
    else:
        from genereview_link.retrieval.embeddings import FakeEmbeddingProvider

        app.state.embedder = FakeEmbeddingProvider(dim=384)
        logger.info(
            "FakeEmbeddingProvider active (set GENEREVIEW_EAGER_LOAD_BGE=true for BGE)."
        )

    app.state.scheduler = None
    if settings.AUTO_PULL_RELEASES and app.state.pool is not None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from genereview_link.ingest.scheduler import check_for_new_release

        scheduler = AsyncIOScheduler()
        scheduler.add_job(check_for_new_release, "cron", minute=17, args=[app.state.pool])
        scheduler.start()
        app.state.scheduler = scheduler
        logger.info("Release watcher scheduler started (fires at :17 each hour).")


async def _teardown_state(app: FastAPI) -> None:
    """Symmetric teardown for everything ``_initialize_state`` attached.

    Body mirrors the post-yield cleanup from the original lifespan.
    Task 7 (#2) hardens this with defensive ``getattr`` calls; for
    now it assumes attributes are present (matches pre-refactor
    behaviour exactly).
    """
    logger.info("Shutting down GeneReview Link Server...")
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("Release watcher scheduler stopped.")
    await shutdown_services()
    await shutdown_clients()
    pool = getattr(app.state, "pool", None)
    if pool is not None:
        await pool.close()
        logger.info("Postgres pool closed.")
    logger.info("Shutdown complete.")
```

- [ ] **Step 3: Update `server_manager.py` to import from the new
  module and delete the moved bodies**

In `genereview_link/server_manager.py`:

(a) At the top, alongside the other `genereview_link.*` imports, add:

```python
from genereview_link.server_lifecycle import (
    _bootstrap,
    _bundle_bootstrap_paths,
    _initialize_state,
    _teardown_state,
)
```

(b) Delete the in-file `_bundle_bootstrap_paths` function (currently
lines 61-63) and `_bootstrap` function (currently lines 66-153).

(c) Replace `UnifiedServerManager.lifespan` (currently lines 181-300)
with the slim delegating body:

```python
    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncGenerator[None, None]:
        """Manage application lifecycle for startup and shutdown.

        Delegates to ``server_lifecycle._initialize_state`` and
        ``_teardown_state`` so the STDIO transport can call the same
        code path (#2).
        """
        await _initialize_state(app)
        try:
            yield
        finally:
            await _teardown_state(app)
```

`start_stdio_server` is **not** updated in this commit — Task 7 does
that. (The current STDIO bug remains until Task 7.)

(d) The unused imports `asyncio`, `shutil`, `json`, `os`, `tarfile`
(if present at the top of `server_manager.py` after deletion) and
the `shutdown_clients` / `shutdown_services` imports may also be
unused after the move; verify with
`uv run ruff check genereview_link/server_manager.py` and let Ruff's
`F401` flag the dead ones — drop them.

- [ ] **Step 4: Run the existing test suite to verify no behaviour
  change**

Run: `make test-fast`

Expected: green. This is a pure refactor; every existing test should
still pass without modification.

- [ ] **Step 5: Measure new LOC and lower the allowlist ceiling**

Run:

```bash
wc -l genereview_link/server_manager.py
wc -l genereview_link/server_lifecycle.py
```

Expected: `server_manager.py` near 400; `server_lifecycle.py` near
250. If `server_lifecycle.py` exceeds 600, the move was wrong — split
further.

In `.loc-allowlist`, change the line:

```
genereview_link/server_manager.py:618
```

to the new measured value (e.g. `genereview_link/server_manager.py:400`).
Do **not** add an entry for `server_lifecycle.py` — it's under 600.

- [ ] **Step 6: Verify the allowlist gate passes**

Run: `make lint-loc`

Expected: PASS.

- [ ] **Step 7: Run the broader unit test suite for regression**

Run: `make test-fast`

Expected: green.

- [ ] **Step 8: Commit**

```bash
git add genereview_link/server_manager.py genereview_link/server_lifecycle.py .loc-allowlist
git commit -m "refactor(server): extract server_lifecycle.py from server_manager.py"
```

---

### Task 6: Verify manifest hashes and reject extras before extract (#4)

**Files:**

- Modify: `genereview_link/server_lifecycle.py` (`_bootstrap` body
  + add module-level `_sha256_stream` helper). Task 5 moved these
  out of `server_manager.py`.
- Create: `tests/unit/test_bootstrap_tarfile_security.py`

- [ ] **Step 1: Write the failing tests first**

Create `tests/unit/test_bootstrap_tarfile_security.py`:

```python
"""Tests for #4: tarball hardening + jobs=None fallback.

Tests build small synthetic bundles in ``tmp_path`` and drive
``_bootstrap`` end-to-end with the DB/HTTP boundaries mocked. The
tarball-handling logic is the unit under test.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_bundle(
    bundle_path: Path,
    members: list[tuple[str, bytes]],
    manifest_checksums: dict[str, str],
) -> Path:
    """Build a .tar.gz at bundle_path containing manifest.json plus the
    listed members. ``manifest_checksums`` is written into manifest.json
    verbatim, so callers can produce forged-manifest fixtures."""
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"checksums": manifest_checksums, "corpus_version": "test-1"}
    manifest_bytes = json.dumps(manifest).encode()

    with tarfile.open(bundle_path, "w:gz") as tar:
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    return bundle_path


def _patches_for_bootstrap(bundle: Path):
    """Return a contextmanager that patches all the DB/HTTP boundaries
    _bootstrap touches, so the test only exercises the tar logic."""
    from contextlib import ExitStack

    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=None)  # no active corpus
    mock_pool.close = AsyncMock()

    async def fake_download(url, dest, expected_sha256):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundle, dest)

    stack = ExitStack()
    stack.enter_context(
        patch(
            "genereview_link.db.pool.create_pool",
            AsyncMock(return_value=mock_pool),
        )
    )
    stack.enter_context(
        patch(
            "genereview_link.db.migrate.apply_control_migrations",
            AsyncMock(return_value=[]),
        )
    )
    stack.enter_context(
        patch(
            "genereview_link.ingest.github_release.fetch_sibling_sha256",
            AsyncMock(return_value="dummy"),
        )
    )
    stack.enter_context(
        patch(
            "genereview_link.ingest.github_release.download_with_integrity",
            new=fake_download,
        )
    )
    return stack


@pytest.mark.asyncio
async def test_bootstrap_rejects_forged_manifest_before_extracting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A manifest claiming a wrong SHA for corpus.dump must raise
    before any file is written under extract_dir."""
    from genereview_link import config as config_mod

    bundle = _make_bundle(
        tmp_path / "fixtures" / "bundle.tar.gz",
        [("corpus.dump", b"real bytes")],
        {"corpus.dump": "0" * 64},  # forged
    )
    boot_dir = tmp_path / "boot"
    monkeypatch.setattr(
        config_mod.settings, "BUNDLE_URL", "https://example.test/bundle.tar.gz"
    )
    monkeypatch.setattr(config_mod.settings, "BUNDLE_BOOTSTRAP_DIR", str(boot_dir))
    monkeypatch.setattr(config_mod.settings, "DATABASE_URL", "postgres://t/t")

    pg_restore_mock = AsyncMock()
    with _patches_for_bootstrap(bundle), patch(
        "genereview_link.ingest.github_release.pg_restore", pg_restore_mock
    ):
        from genereview_link.server_lifecycle import _bootstrap

        with pytest.raises(RuntimeError, match="manifest checksum mismatch"):
            await _bootstrap()

    extract_dir = boot_dir / "bundle_extract"
    if extract_dir.exists():
        assert not (extract_dir / "corpus.dump").exists(), (
            "forged-manifest member should not have been extracted"
        )
    pg_restore_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_bootstrap_rejects_unexpected_member(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A safe-path member NOT listed in manifest.json (e.g. an
    unauthorized extra) must be rejected before any extract."""
    from genereview_link import config as config_mod

    dump = b"real corpus dump"
    evil = b"unauthorized payload"
    bundle = _make_bundle(
        tmp_path / "fixtures" / "bundle.tar.gz",
        [("corpus.dump", dump), ("evil.txt", evil)],
        {"corpus.dump": _sha(dump)},
    )
    boot_dir = tmp_path / "boot"
    monkeypatch.setattr(
        config_mod.settings, "BUNDLE_URL", "https://example.test/bundle.tar.gz"
    )
    monkeypatch.setattr(config_mod.settings, "BUNDLE_BOOTSTRAP_DIR", str(boot_dir))
    monkeypatch.setattr(config_mod.settings, "DATABASE_URL", "postgres://t/t")

    pg_restore_mock = AsyncMock()
    with _patches_for_bootstrap(bundle), patch(
        "genereview_link.ingest.github_release.pg_restore", pg_restore_mock
    ):
        from genereview_link.server_lifecycle import _bootstrap

        with pytest.raises(RuntimeError, match="unexpected tar member"):
            await _bootstrap()

    extract_dir = boot_dir / "bundle_extract"
    if extract_dir.exists():
        assert not (extract_dir / "evil.txt").exists()
    pg_restore_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_bootstrap_rejects_duplicate_member(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tarball with the same member name twice must be rejected
    before extract (defense against name-collision smuggling)."""
    from genereview_link import config as config_mod

    bundle_path = tmp_path / "fixtures" / "bundle.tar.gz"
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    dump = b"real corpus dump"
    manifest = {"checksums": {"corpus.dump": _sha(dump)}, "corpus_version": "test-1"}
    manifest_bytes = json.dumps(manifest).encode()
    with tarfile.open(bundle_path, "w:gz") as tar:
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        # corpus.dump appears twice — the second one shadows the first
        info = tarfile.TarInfo("corpus.dump")
        info.size = len(dump)
        tar.addfile(info, io.BytesIO(dump))
        info = tarfile.TarInfo("corpus.dump")
        info.size = len(dump)
        tar.addfile(info, io.BytesIO(dump))

    boot_dir = tmp_path / "boot"
    monkeypatch.setattr(
        config_mod.settings, "BUNDLE_URL", "https://example.test/bundle.tar.gz"
    )
    monkeypatch.setattr(config_mod.settings, "BUNDLE_BOOTSTRAP_DIR", str(boot_dir))
    monkeypatch.setattr(config_mod.settings, "DATABASE_URL", "postgres://t/t")

    pg_restore_mock = AsyncMock()
    with _patches_for_bootstrap(bundle_path), patch(
        "genereview_link.ingest.github_release.pg_restore", pg_restore_mock
    ):
        from genereview_link.server_lifecycle import _bootstrap

        with pytest.raises(RuntimeError, match="duplicate tar member"):
            await _bootstrap()
    pg_restore_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_bootstrap_filter_data_blocks_listed_unsafe_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tarball whose manifest LISTS an absolute-path member with a
    valid checksum must still be rejected — by ``filter="data"``,
    since the membership and checksum checks both pass. An
    implementation that omits ``filter="data"`` would write
    ``/tmp/evil.txt`` outside ``extract_dir`` and this test would
    catch it."""
    from genereview_link import config as config_mod

    bundle_path = tmp_path / "fixtures" / "bundle.tar.gz"
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    dump = b"real corpus dump"
    evil_bytes = b"unauthorized payload"
    # The malicious path goes into both manifest checksums AND tar
    # members with a *matching* SHA — so the only protection left
    # is the extraction filter.
    manifest = {
        "checksums": {
            "corpus.dump": _sha(dump),
            "/tmp/evil.txt": _sha(evil_bytes),
        },
        "corpus_version": "test-1",
    }
    manifest_bytes = json.dumps(manifest).encode()
    with tarfile.open(bundle_path, "w:gz") as tar:
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        info = tarfile.TarInfo("corpus.dump")
        info.size = len(dump)
        tar.addfile(info, io.BytesIO(dump))
        info = tarfile.TarInfo("/tmp/evil.txt")
        info.size = len(evil_bytes)
        tar.addfile(info, io.BytesIO(evil_bytes))

    boot_dir = tmp_path / "boot"
    monkeypatch.setattr(
        config_mod.settings, "BUNDLE_URL", "https://example.test/bundle.tar.gz"
    )
    monkeypatch.setattr(config_mod.settings, "BUNDLE_BOOTSTRAP_DIR", str(boot_dir))
    monkeypatch.setattr(config_mod.settings, "DATABASE_URL", "postgres://t/t")

    pg_restore_mock = AsyncMock()
    with _patches_for_bootstrap(bundle_path), patch(
        "genereview_link.ingest.github_release.pg_restore", pg_restore_mock
    ):
        from genereview_link.server_lifecycle import _bootstrap

        # filter="data" raises AbsolutePathError (or similar tarfile.FilterError
        # subclass) per PEP 706. Match generically on the parent class.
        with pytest.raises(tarfile.FilterError):
            await _bootstrap()

    # Critical: the malicious absolute-path member must NOT have
    # landed under /tmp.
    assert not Path("/tmp/evil.txt").exists(), (  # noqa: S108
        "filter='data' is missing: tar wrote outside extract_dir"
    )
    pg_restore_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_bootstrap_handles_cpu_count_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``jobs=os.cpu_count()`` can yield None on exotic systems; we
    must fall back to 2."""
    import os as os_mod

    from genereview_link import config as config_mod

    dump = b"real corpus dump"
    bundle = _make_bundle(
        tmp_path / "fixtures" / "bundle.tar.gz",
        [("corpus.dump", dump)],
        {"corpus.dump": _sha(dump)},
    )
    boot_dir = tmp_path / "boot"
    monkeypatch.setattr(
        config_mod.settings, "BUNDLE_URL", "https://example.test/bundle.tar.gz"
    )
    monkeypatch.setattr(config_mod.settings, "BUNDLE_BOOTSTRAP_DIR", str(boot_dir))
    monkeypatch.setattr(config_mod.settings, "DATABASE_URL", "postgres://t/t")
    monkeypatch.setattr(os_mod, "cpu_count", lambda: None)

    pg_restore_mock = AsyncMock()
    with _patches_for_bootstrap(bundle), patch(
        "genereview_link.ingest.github_release.pg_restore", pg_restore_mock
    ):
        from genereview_link.server_lifecycle import _bootstrap

        await _bootstrap()

    pg_restore_mock.assert_awaited_once()
    kwargs = pg_restore_mock.await_args.kwargs
    assert kwargs["jobs"] == 2, f"expected jobs=2 fallback, got {kwargs.get('jobs')!r}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_bootstrap_tarfile_security.py -v`

Expected: all five FAIL (current `_bootstrap` extracts before
verifying, ignores extras, allows duplicates, omits `filter="data"`,
and passes `jobs=os.cpu_count()` raw).

- [ ] **Step 3: Add the `_sha256_stream` helper at module scope**

In `genereview_link/server_lifecycle.py`, immediately above
`_bundle_bootstrap_paths`, add:

```python
def _sha256_stream(fh) -> str:  # type: ignore[no-untyped-def]
    """Compute SHA-256 of a file-like, reading in 64 KiB chunks to keep
    peak memory bounded regardless of file size."""
    import hashlib

    hasher = hashlib.sha256()
    while True:
        chunk = fh.read(65536)
        if not chunk:
            break
        hasher.update(chunk)
    return hasher.hexdigest()
```

- [ ] **Step 4: Rewrite the bundle tar handling in `_bootstrap`**

In `genereview_link/server_lifecycle.py`, replace the
`download_with_integrity ... pg_restore` block inside `_bootstrap`
with:

```python
            await download_with_integrity(bundle_url, tmp, expected_sha256=sha)
            extract_dir.mkdir(parents=True, exist_ok=True)
            with tf_mod.open(tmp, "r:gz") as tar:
                # 1. Read manifest from in-tarball bytes.
                manifest_member = tar.getmember("manifest.json")
                manifest_fh = tar.extractfile(manifest_member)
                if manifest_fh is None:
                    raise RuntimeError(
                        "manifest.json missing or unreadable from bundle"
                    )
                manifest = json.loads(manifest_fh.read())
                expected_members = {"manifest.json", *manifest["checksums"].keys()}

                # 2. Reject unexpected or duplicate members.
                seen: set[str] = set()
                for member in tar.getmembers():
                    if member.name in seen:
                        raise RuntimeError(
                            f"duplicate tar member: {member.name}"
                        )
                    seen.add(member.name)
                    if member.name not in expected_members:
                        raise RuntimeError(
                            "unexpected tar member not listed in manifest: "
                            f"{member.name}"
                        )

                # 3. Verify every manifest-listed member against in-tarball bytes.
                for relpath, expected in manifest["checksums"].items():
                    member = tar.getmember(relpath)
                    fh = tar.extractfile(member)
                    if fh is None:
                        raise RuntimeError(
                            f"manifest references missing member {relpath}"
                        )
                    if _sha256_stream(fh) != expected:
                        raise RuntimeError(
                            f"manifest checksum mismatch on {relpath}"
                        )

                # 4. Extract only the verified members. filter="data"
                #    is the second line of defence per PEP 706.
                for name in expected_members:
                    tar.extract(
                        tar.getmember(name),
                        path=str(extract_dir),
                        filter="data",  # noqa: S202
                    )
            await pg_restore(
                extract_dir / "corpus.dump",
                database_url=settings.DATABASE_URL,
                jobs=os.cpu_count() or 2,
            )
            logger.info("corpus bundle restored")
            return
```

Also delete the now-unused `sha256_file` import inside `_bootstrap`
(`from genereview_link.corpus.bundle import sha256_file`) — the
per-file SHA verification now uses `_sha256_stream` against in-tarball
bytes.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_bootstrap_tarfile_security.py -v`

Expected: all five PASS.

- [ ] **Step 6: Run focused make target**

Run: `make test-fast`

Expected: green.

- [ ] **Step 7: Verify the LOC budget still holds**

Run: `make lint-loc`

Expected: PASS. `server_lifecycle.py` should still be under 600 LOC
after the additions (Task 5 left ample headroom).

- [ ] **Step 8: Commit**

```bash
git add genereview_link/server_lifecycle.py tests/unit/test_bootstrap_tarfile_security.py
git commit -m "fix(corpus): verify manifest hashes and reject extras before extract (#4)"
```

---

### Task 7: Wire `start_stdio_server` to `_initialize_state` / `_teardown_state` (#2)

**Files:**

- Modify: `genereview_link/server_manager.py` — replace the body of
  `UnifiedServerManager.start_stdio_server` to call
  `_initialize_state` / `_teardown_state` (already imported in
  Task 5). No other changes; Task 5 already moved the helpers and
  added `app.state.scheduler` + defensive `getattr` in teardown.
- Create: `tests/unit/test_server_lifecycle.py`

- [ ] **Step 1: Write the failing tests first**

Create `tests/unit/test_server_lifecycle.py`:

```python
"""Tests for #2: STDIO MCP transport now runs the lifespan body.

The bug today is purely structural: ``start_stdio_server`` never
called the lifespan body, so every ``/passages/*`` MCP tool returned
503. Task 5 extracted the body into ``server_lifecycle._initialize_state``;
this task wires ``start_stdio_server`` to call it.

Two regressions matter: (a) the extracted function works for the
empty-database path (everything else short-circuits cleanly);
(b) ``start_stdio_server`` actually calls it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI


@pytest.mark.asyncio
async def test_initialize_state_with_empty_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With DATABASE_URL empty, _initialize_state must short-circuit
    cleanly: no pool, no repository, no scheduler, but the embedder
    and dense model metadata must still land on app.state."""
    from genereview_link import config as config_mod
    from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
    from genereview_link.server_lifecycle import _initialize_state, _teardown_state

    monkeypatch.setattr(config_mod.settings, "DATABASE_URL", "")
    monkeypatch.setattr(config_mod.settings, "GENEREVIEW_EAGER_LOAD_BGE", False)
    monkeypatch.setattr(config_mod.settings, "AUTO_PULL_RELEASES", False)

    app = FastAPI()
    await _initialize_state(app)

    assert app.state.pool is None
    assert app.state.repository is None
    assert app.state.corpus_version is None
    assert app.state.gene_index is None
    assert isinstance(app.state.embedder, FakeEmbeddingProvider)
    assert getattr(app.state, "scheduler", None) is None
    assert app.state.dense_model_id  # set by lifespan body
    assert app.state.embedding_dim   # set by lifespan body

    # Teardown must not raise on a half-initialized app.
    await _teardown_state(app)


@pytest.mark.asyncio
async def test_start_stdio_server_invokes_initialize_and_teardown() -> None:
    """Regression for the original bug: start_stdio_server must call
    _initialize_state (so MCP tools see app.state.repository etc.) and
    _teardown_state on shutdown.

    Note: ``server_manager.py`` imports ``_initialize_state`` /
    ``_teardown_state`` from ``server_lifecycle``, so patches target
    the *server_manager* binding (Python imports create a local
    name in the importing module's namespace; patching the source
    module would not affect the caller's binding)."""
    from genereview_link.config import ServerConfig
    from genereview_link.server_manager import UnifiedServerManager

    mock_init = AsyncMock()
    mock_teardown = AsyncMock()
    mock_mcp = AsyncMock()
    mock_mcp.run_async = AsyncMock(return_value=None)

    with patch("genereview_link.server_manager._initialize_state", mock_init), patch(
        "genereview_link.server_manager._teardown_state", mock_teardown
    ), patch.object(
        UnifiedServerManager,
        "create_mcp_server",
        AsyncMock(return_value=mock_mcp),
    ):
        mgr = UnifiedServerManager()
        await mgr.start_stdio_server(ServerConfig(transport="stdio"))

    assert mock_init.await_count == 1, "STDIO must run the lifespan body"
    assert mock_mcp.run_async.await_count == 1
    assert mock_teardown.await_count == 1


@pytest.mark.asyncio
async def test_start_stdio_server_teardown_runs_on_exception() -> None:
    """If run_async raises, _teardown_state must still run (finally)."""
    from genereview_link.config import ServerConfig
    from genereview_link.server_manager import UnifiedServerManager

    mock_init = AsyncMock()
    mock_teardown = AsyncMock()
    mock_mcp = AsyncMock()
    mock_mcp.run_async = AsyncMock(side_effect=RuntimeError("boom"))

    with patch("genereview_link.server_manager._initialize_state", mock_init), patch(
        "genereview_link.server_manager._teardown_state", mock_teardown
    ), patch.object(
        UnifiedServerManager,
        "create_mcp_server",
        AsyncMock(return_value=mock_mcp),
    ):
        mgr = UnifiedServerManager()
        with pytest.raises(RuntimeError, match="boom"):
            await mgr.start_stdio_server(ServerConfig(transport="stdio"))

    assert mock_teardown.await_count == 1, "teardown must run in finally"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_server_lifecycle.py -v`

Expected:
- `test_initialize_state_with_empty_database_url` PASSes already
  (Task 5 created `_initialize_state` and the empty-DB path is
  unchanged behaviour). Leave the test in place as a regression guard.
- `test_start_stdio_server_invokes_initialize_and_teardown` FAILs:
  `mock_init.await_count == 0` because `start_stdio_server` still
  hand-rolls client/service init instead of calling
  `_initialize_state`.
- `test_start_stdio_server_teardown_runs_on_exception` FAILs for the
  same reason.

- [ ] **Step 3: Refactor `start_stdio_server` to call the helpers**

In `genereview_link/server_manager.py`, replace `start_stdio_server`
(currently lines 580-592 on the post-Task-5 file; the body manually
calls `get_client_manager()` etc.) with:

```python
    async def start_stdio_server(self, config: ServerConfig) -> None:
        """Start the server in STDIO mode for MCP.

        Calls ``_initialize_state`` so MCP tools see the same
        ``app.state.repository`` / ``embedder`` / ``gene_index`` /
        ``corpus_version`` the HTTP transport's lifespan provides
        (#2). Without this, every ``/passages/*`` tool returned 503
        because ``app.state.repository`` was None.
        """
        self._current_transport = "stdio"
        logger.info("Starting STDIO MCP server...")
        self.app = self.create_fastapi_app(config)
        await _initialize_state(self.app)
        try:
            self.mcp = await self.create_mcp_server(self.app, config)
            await self.mcp.run_async(transport="stdio")
        finally:
            await _teardown_state(self.app)
```

The previous manual `get_client_manager()` / `get_service_manager()`
initialization is gone — `_initialize_state` already does this.

`UnifiedServerManager.lifespan` was already refactored in Task 5;
leave it untouched.

- [ ] **Step 4: Run the lifecycle tests**

Run: `uv run pytest tests/unit/test_server_lifecycle.py -v`

Expected: all three PASS.

- [ ] **Step 5: Run focused make target**

Run: `make test-fast`

Expected: green.

- [ ] **Step 6: Verify the LOC budget still holds**

Run: `make lint-loc`

Expected: PASS. The `server_manager.py` edits in this task are a
small net-negative (the bigger manual-init block is replaced by a
short delegating call). Task 5's lowered allowlist ceiling should
still hold.

- [ ] **Step 7: Commit**

```bash
git add genereview_link/server_manager.py tests/unit/test_server_lifecycle.py
git commit -m "fix(mcp): extract lifespan body into _initialize_state for STDIO transport (#2)"
```

---

### Task 8: Full CI + PR

- [ ] **Step 1: Run `make ci-local`**

Run: `make ci-local`

Expected: format-check, lint-ci, lint-loc, typecheck-fast, and
test-unit all green. Investigate and fix any real failures. Do not
bypass with `--no-verify`.

If `typecheck-fast` flags the new test files' `pytest.MonkeyPatch`
annotation or `pytest.fixture` re-imports, prefer adding the minimal
`from __future__ import annotations` import (already present in every
new test file in this plan).

- [ ] **Step 2: Confirm closing-list comment readiness**

Each of the six commits should reference its finding number in the
message footer (already done in the task templates above). The PR
body should list:

```
Closes #1
Closes #2
Closes #4
Closes #11
Closes #12
Closes #19
```

(`#18` is intentionally absent — verified obsolete during the spec
review; the planning doc was updated to record this.)

- [ ] **Step 3: Push branch and open PR**

```bash
git push -u origin feat/phase1-correctness-and-perf
gh pr create \
  --title "feat: Phase 1 correctness and performance bundle" \
  --body "$(cat <<'EOF'
## Summary

First remediation phase from the 2026-05-25 senior engineering review.
Six findings + one enabling refactor, one PR, one atomic commit per item.

- #19 refactor(mcp): drop the dead `mcp_custom_names` identity-mapped dict (every key mapped to itself; passing it as `mcp_names=` was a no-op).
- #1 fix(services): wire `CACHE_TTL_HOURS` into the three `alru_cache` constructions in `GeneReviewService`. The setting was previously read into `self.cache_ttl` and never applied; scraped GeneReview content lived in-process until LRU eviction or restart.
- #11 perf(db): pass `server_settings={"search_path": "genereview, public"}` to `asyncpg.create_pool` (sent as a Postgres startup parameter, survives `RESET ALL` on pool release). Removes 13 per-query round-trips on every retrieval path. Integration test fixture mirrors the production session-default contract; a new integration test asserts `search_path` survives acquire/release/reacquire.
- #12 feat(db): production-tune asyncpg pool. New settings `DATABASE_COMMAND_TIMEOUT_S`, `DATABASE_STATEMENT_CACHE_SIZE`, `DATABASE_MAX_INACTIVE_CONNECTION_LIFETIME_S`; default `DATABASE_POOL_MAX_SIZE` bumped 10 → 20.
- refactor(server): extract `_bootstrap`, `_bundle_bootstrap_paths`, `_initialize_state`, `_teardown_state` from `server_manager.py` into a new `genereview_link/server_lifecycle.py` module. Pure mechanical move, no behaviour change. `server_manager.py` LOC drops from 618 to ~400; `.loc-allowlist` ceiling lowered accordingly.
- #4 fix(corpus): in `server_lifecycle._bootstrap`, verify manifest SHA-256 hashes against in-tarball bytes before any extract; reject unexpected and duplicate members; apply `filter="data"` to every extract; `jobs=os.cpu_count() or 2` for `pg_restore`.
- #2 fix(mcp): wire `start_stdio_server` to call the `_initialize_state` / `_teardown_state` helpers Task 5 extracted. STDIO MCP clients now see `app.state.repository`, `embedder`, `gene_index`, and `corpus_version` — previously all `/passages/*` tools returned 503 over STDIO.

#18 from the senior eng review was verified obsolete during this PR's spec review — `server_manager.py:427` already sets `include_in_schema=False`, which removes `/metrics` from the OpenAPI schema that FastMCP walks. The planning doc has been updated.

## Operator-visible defaults

`DATABASE_POOL_MAX_SIZE` default rises 10 → 20. Deployments with an explicit env override keep their value. A Postgres `max_connections` of 100 still supports 5 workers × 20 connections.

The three new tuning fields default to asyncpg's own defaults (`max_inactive_connection_lifetime=300.0`, `command_timeout=None`, `statement_cache_size=100`) — zero behaviour change unless an operator opts in. PgBouncer txn-mode deployments should set `DATABASE_STATEMENT_CACHE_SIZE=0`; see `AGENTS.md` "Postgres Connection."

## Test plan

- [ ] `make ci-local` passes
- [ ] `make lint-loc` passes (server_manager.py allowlist lowered)
- [ ] `make mcp-serve` (or `uv run genereview-link serve --transport stdio`) and a manual MCP `tools/call search_passages` returns 200 (not 503)
- [ ] Existing `tests/integration/` suite passes against a test Postgres (`GENEREVIEW_TEST_DATABASE_URL=...`)

Closes #1
Closes #2
Closes #4
Closes #11
Closes #12
Closes #19

Spec: docs/superpowers/specs/2026-05-26-phase1-correctness-and-perf-design.md
Plan: docs/superpowers/plans/2026-05-26-phase1-correctness-and-perf.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Watch CI**

```bash
gh pr checks --watch
```

Expected: all required checks green within ~10 minutes.

---

## Risk Notes

- **#11 asyncpg `RESET ALL` wipes session settings.** This is why
  `init=` (or `setup=`) cannot be used for `search_path`. The design
  uses `server_settings=`, which sends the setting as a startup
  parameter so `RESET ALL` reverts to that value. The new integration
  test `test_search_path_survives_pool_release_and_reacquire` is the
  regression net.
- **#11 `SET LOCAL search_path` interaction.** Session-default
  `search_path` is overridden inside any txn that issues `SET LOCAL`,
  then restored on txn end. Verified at `corpus/pipeline.py:237`.
- **#4 manifest re-verify reads the tarball twice.** The verification
  pass streams every member through SHA-256 before extraction starts,
  which doubles I/O on bootstrap. Acceptable at one-time-per-restart
  cost; 64 KiB streaming chunks bound peak memory.
- **#4 `filter="data"` must be present.** A tarball whose manifest
  lists an unsafe path (absolute or symlink) with a valid checksum
  passes the membership and checksum checks; only `filter="data"`
  rejects it. The `test_bootstrap_filter_data_blocks_listed_unsafe_path`
  test forces this; an implementation that omits the kwarg fails it.
- **#2 scheduler attribute access.** `_teardown_state` reads
  `getattr(app.state, "scheduler", None)` so a partial init that
  raised before scheduler creation does not crash teardown.
- **`server_manager.py` LOC ceiling.** Currently 618. Task 5's
  pure refactor moves ~200+ LOC into `server_lifecycle.py` and lowers
  the allowlist entry to the new actual (expected ~400). Task 6 and
  Task 7 land their additions in `server_lifecycle.py`, which has
  ample headroom under 600. `make lint-loc` is the gate at every
  commit boundary.
- **`DATABASE_POOL_MAX_SIZE` default change.** Operator-visible. Called
  out in the PR body. Postgres `max_connections` ≥ 100 is the implicit
  assumption.
- **Test isolation around `monkeypatch.setattr(config_mod.settings, ...)`.**
  `Settings` is a Pydantic singleton; tests that mutate it must use
  `monkeypatch` (auto-undo) — never raw assignment. All test code in
  this plan follows this convention.
- **Patching imports across modules (#2 test wiring).**
  `server_manager.py` imports `_initialize_state` /
  `_teardown_state` from `server_lifecycle`, so Python creates new
  name bindings in `server_manager`'s namespace. Tests targeting
  the wiring must patch `genereview_link.server_manager._initialize_state`
  (the caller's binding), not `genereview_link.server_lifecycle._initialize_state`
  (the source). The plan's Task 7 tests follow this rule.

## Execution Order Rationale

See the **Task Ordering Rationale** section above (after the File Map)
— it describes the 8-task ordering and the rationale for the
pure-refactor commit between Tasks 4 and 6.
