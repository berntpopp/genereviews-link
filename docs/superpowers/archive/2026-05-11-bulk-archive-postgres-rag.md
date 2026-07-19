# Bulk Archive Ingest + Postgres RAG Retrieval — Implementation Plan

> Historical record

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace live NCBI scraping as the primary data path with a Postgres + pgvector-backed corpus ingested from `gene_NBK1116.tar.gz`, served via hybrid lexical+dense retrieval with RRF rerank, distributed as CI-built GitHub Release bundles, with the existing `EutilsClient` retained as an opt-in `?fresh=true` fallback.

**Architecture:** Six sequential phases. Two-schema atomic-swap pattern (`public` for control tables, `genereview`/`genereview_staging`/`genereview_old_*` for data). ProcessPool parallelized NXML parse + asyncpg COPY for ingest. Three-tsquery lexical search + BGE-small dense rerank with RRF k=60. CI builds bundles weekly; container `pg_restore`s on first boot.

**Tech Stack:** Postgres 18 + pgvector 0.8.2, asyncpg, defusedxml + lxml, BAAI/bge-small-en-v1.5 via sentence-transformers, FastAPI + FastMCP, APScheduler, GitHub Actions.

**Spec:** [`docs/superpowers/specs/2026-05-11-bulk-archive-postgres-rag-design.md`](../specs/2026-05-11-bulk-archive-postgres-rag-design.md)

---

## File structure

### Created
```
genereview_link/
├── db/
│   ├── __init__.py
│   ├── migrate.py                       # Migration runner (ported from pubtator-link)
│   ├── pool.py                          # asyncpg pool factory
│   └── migrations/
│       ├── __init__.py
│       ├── control/
│       │   ├── 0001_base.sql
│       │   ├── 0002_corpus_version.sql
│       │   ├── 0003_refresh_log.sql
│       │   └── 0004_active_embedding.sql
│       └── data/
│           ├── 0001_chapters.sql
│           ├── 0002_passages.sql
│           └── 0003_embeddings_bge384.sql
├── corpus/
│   ├── __init__.py
│   ├── archive.py                       # file_list.csv + tarball download
│   ├── canonicalize.py                  # section name → canonical vocabulary
│   ├── tokenizer.py                     # BGE AutoTokenizer cache
│   ├── nxml.py                          # BITS parser
│   ├── chunking.py                      # 510-token windows
│   ├── sidedata.py                      # /pub/GeneReviews/ files
│   ├── parallel.py                      # ProcessPool + COPY writers
│   ├── pipeline.py                      # 9-stage orchestrator
│   ├── bundle.py                        # pg_dump + manifest + tar
│   └── records.py                       # ChapterRecord, PassageRecord dataclasses
├── retrieval/
│   ├── __init__.py
│   ├── embeddings.py                    # BGE provider (lifted from pubtator-link)
│   ├── lexical.py                       # Three-tsquery SQL + helpers
│   ├── rerank.py                        # RRF + section_priority
│   └── repository.py                    # GeneReviewRepository
├── ingest/
│   ├── __init__.py
│   ├── orchestrator.py                  # Drives pipeline
│   └── github_release.py                # Release watcher + downloader
└── api/routes/
    ├── passages.py                      # /passages/search
    ├── chapters.py                      # /chapters/{nbk}/sections/{sec}
    └── debug.py                         # /debug/ranking

tests/
├── fixtures/
│   ├── nxml/                            # 6 NXML test fixtures
│   ├── sidedata/                        # Abbreviated side-data files
│   └── bundles/mini.tar.gz              # 3-chapter test bundle
├── eval/
│   ├── genereviews_queries.jsonl        # ~30 hand-curated triples
│   └── baseline.json                    # MRR baseline
├── unit/
│   ├── test_corpus_nxml.py
│   ├── test_corpus_chunking.py
│   ├── test_corpus_sidedata.py
│   ├── test_corpus_canonicalize.py
│   ├── test_corpus_tokenizer.py
│   ├── test_corpus_bundle.py
│   ├── test_retrieval_rerank.py
│   └── test_retrieval_lexical_helpers.py
└── integration/
    ├── conftest.py                      # testcontainers Postgres fixture
    ├── test_migrations.py
    ├── test_repository_lexical.py
    ├── test_repository_dense.py
    ├── test_ingest_end_to_end.py
    ├── test_bundle_round_trip.py
    └── test_scheduler_advisory_lock.py

.github/workflows/build-corpus.yml

docs/MEMORY.md                           # Operator memory budget guidance
```

### Modified
- `pyproject.toml` — add deps: asyncpg, pgvector (python), sentence-transformers, transformers, apscheduler, lxml, defusedxml is already pinned
- `genereview_link/config.py` — add DATABASE_URL, BUNDLE_URL, BUILD_LOCAL, AUTO_PULL_RELEASES, GENEREVIEW_INGEST_*, GENEREVIEW_EAGER_LOAD_BGE
- `genereview_link/cli.py` — add subcommands: `db migrate`, `ingest`, `embed`, `bundle`, `bundle verify`, `bundle restore`, `eval`
- `genereview_link/models/genereview_models.py` — add CorpusVersion, LicenseNotice, RankedPassage, ScoreBreakdown; add optional `corpus_version` + `license` to existing models
- `genereview_link/services/genereview_service.py` — flip to repository-first with EutilsClient fallback on `fresh=True`
- `genereview_link/server_manager.py` — extend MCP tool name mapping
- `genereview_link/api/routes/{search,abstract,links,fulltext,genereview}.py` — repository-backed + `?fresh=true`
- `Makefile` — add db-migrate, ingest, embed, bundle, eval targets
- `docker/docker-compose.yml`, `docker-compose.dev.yml`, `docker-compose.prod.yml` — postgres service, 3 GB memory limit, BUNDLE_URL env
- `docker/Dockerfile` — install postgresql-client (for pg_restore)

---

## Phase navigation

- [Phase 1: Schema & migrations](#phase-1--schema--migrations) — 14 tasks
- [Phase 2: Corpus ingest pipeline](#phase-2--corpus-ingest-pipeline) — 18 tasks
- [Phase 3: Embedding backfill](#phase-3--embedding-backfill) — 6 tasks
- [Phase 4: Retrieval layer](#phase-4--retrieval-layer) — 12 tasks
- [Phase 5: Route migration](#phase-5--route-migration) — 14 tasks
- [Phase 6: CI bundle workflow](#phase-6--ci-bundle-workflow) — 12 tasks

Each phase commits to `main` independently and leaves the system green.

---

## Phase 1 — Schema & migrations

Goal: empty Postgres schema provisioned by `docker compose up`; control migrations applied; data migrations exist but unused. No behavior change for users.

### Task 1.1: Add Postgres + pgvector dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock` (auto-regenerated)

- [ ] **Step 1: Add deps to pyproject.toml**

Locate the `[project] dependencies` array and add:

```toml
"asyncpg>=0.30.0",
"pgvector>=0.4.0",
```

- [ ] **Step 2: Regenerate lockfile**

```bash
make lock
```

Expected: `uv.lock` updated, no errors.

- [ ] **Step 3: Install**

```bash
make install
```

- [ ] **Step 4: Verify import works**

```bash
uv run python -c "import asyncpg, pgvector; print(asyncpg.__version__, pgvector.__version__)"
```

Expected: two version strings printed.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(deps): add asyncpg and pgvector for Postgres-backed corpus"
```

### Task 1.2: Wire DATABASE_URL into config

**Files:**
- Modify: `genereview_link/config.py`
- Test: `tests/test_config_database.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_config_database.py`:

```python
"""Tests for DATABASE_URL configuration."""

from __future__ import annotations

import os
from unittest.mock import patch

from genereview_link.config import Settings


def test_database_url_defaults_to_empty() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DATABASE_URL", None)
        settings = Settings()
        assert settings.DATABASE_URL == ""


def test_database_url_from_env() -> None:
    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@h:5432/db"}):
        settings = Settings()
        assert settings.DATABASE_URL == "postgresql://u:p@h:5432/db"


def test_database_pool_min_max_defaults() -> None:
    settings = Settings()
    assert settings.DATABASE_POOL_MIN_SIZE == 2
    assert settings.DATABASE_POOL_MAX_SIZE == 10
```

- [ ] **Step 2: Run test (should fail)**

```bash
uv run pytest tests/test_config_database.py -v
```

Expected: `AttributeError: 'Settings' object has no attribute 'DATABASE_URL'`.

- [ ] **Step 3: Implement**

In `genereview_link/config.py`, inside the `Settings` class (alphabetical with NCBI_API_KEY block), add:

```python
    # Postgres connection (set in MODE 1/2; empty triggers EutilsClient-only fallback path)
    DATABASE_URL: str = ""
    DATABASE_POOL_MIN_SIZE: int = 2
    DATABASE_POOL_MAX_SIZE: int = 10
    DATABASE_ACQUIRE_TIMEOUT_S: float = 5.0
```

- [ ] **Step 4: Run test (should pass)**

```bash
uv run pytest tests/test_config_database.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/config.py tests/test_config_database.py
git commit -m "feat(config): add DATABASE_URL and asyncpg pool settings"
```

### Task 1.3: Create asyncpg pool factory

**Files:**
- Create: `genereview_link/db/__init__.py`
- Create: `genereview_link/db/pool.py`
- Test: `tests/integration/conftest.py`, `tests/integration/test_pool.py`

- [ ] **Step 1: Create integration test fixture**

Create `tests/integration/__init__.py` (empty file).

Create `tests/integration/conftest.py`:

```python
"""Shared fixtures for integration tests requiring a real Postgres."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio


def _database_url() -> str:
    url = os.environ.get("GENEREVIEW_TEST_DATABASE_URL")
    if not url:
        pytest.skip("GENEREVIEW_TEST_DATABASE_URL not set; integration test skipped")
    return url


@pytest_asyncio.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    """Yield a pool against the test Postgres; drop genereview schemas after."""
    url = _database_url()
    pool = await asyncpg.create_pool(url, min_size=1, max_size=4)
    yield pool
    async with pool.acquire() as conn:
        await conn.execute("drop schema if exists genereview cascade")
        await conn.execute("drop schema if exists genereview_staging cascade")
        rows = await conn.fetch(
            "select schema_name from information_schema.schemata "
            "where schema_name like 'genereview_old_%'"
        )
        for row in rows:
            await conn.execute(f"drop schema if exists {row['schema_name']} cascade")
    await pool.close()
```

Add `pytest-asyncio` to dev deps if not present (check `pyproject.toml` first; pubtator-link uses it).

- [ ] **Step 2: Add dev dep if needed**

```bash
grep -q pytest-asyncio pyproject.toml || uv add --group dev pytest-asyncio
```

- [ ] **Step 3: Write failing test**

Create `tests/integration/test_pool.py`:

```python
"""Smoke test for asyncpg pool factory."""

from __future__ import annotations

import pytest

from genereview_link.db.pool import create_pool


@pytest.mark.asyncio
async def test_pool_can_be_acquired_and_queries(monkeypatch, database_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    pool = await create_pool()
    try:
        async with pool.acquire() as conn:
            value = await conn.fetchval("select 1")
            assert value == 1
    finally:
        await pool.close()
```

Add fixture to `tests/integration/conftest.py`:

```python
@pytest.fixture
def database_url() -> str:
    return _database_url()
```

- [ ] **Step 4: Run test (should fail)**

```bash
uv run pytest tests/integration/test_pool.py -v
```

Expected: `ModuleNotFoundError: No module named 'genereview_link.db.pool'`.

- [ ] **Step 5: Create db package**

Create `genereview_link/db/__init__.py`:

```python
"""Database layer: asyncpg pool, migrations, schema management."""
```

Create `genereview_link/db/pool.py`:

```python
"""Async pool factory for Postgres connections."""

from __future__ import annotations

import asyncpg

from genereview_link.config import settings


async def create_pool() -> asyncpg.Pool:
    """Create an asyncpg pool from settings.

    Raises:
        RuntimeError: if DATABASE_URL is empty.
    """
    if not settings.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=settings.DATABASE_POOL_MIN_SIZE,
        max_size=settings.DATABASE_POOL_MAX_SIZE,
    )
```

- [ ] **Step 6: Run integration test**

Start a local Postgres (any pgvector image works):

```bash
docker run --rm -d --name gr-pg-test -e POSTGRES_PASSWORD=test -p 5433:5432 pgvector/pgvector:0.8.2-pg18
sleep 3
GENEREVIEW_TEST_DATABASE_URL=postgresql://postgres:test@localhost:5433/postgres uv run pytest tests/integration/test_pool.py -v
docker stop gr-pg-test
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add genereview_link/db/ tests/integration/
git commit -m "feat(db): asyncpg pool factory + integration test fixtures"
```

### Task 1.4: Migration runner

**Files:**
- Create: `genereview_link/db/migrate.py`
- Create: `genereview_link/db/migrations/__init__.py`
- Create: `genereview_link/db/migrations/control/__init__.py`
- Create: `genereview_link/db/migrations/data/__init__.py`
- Test: `tests/integration/test_migrations.py`

- [ ] **Step 1: Create empty migrations directories**

```bash
mkdir -p genereview_link/db/migrations/control genereview_link/db/migrations/data
touch genereview_link/db/migrations/__init__.py
touch genereview_link/db/migrations/control/__init__.py
touch genereview_link/db/migrations/data/__init__.py
```

- [ ] **Step 2: Write failing test**

Create `tests/integration/test_migrations.py`:

```python
"""Tests for the migration runner."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.db.migrate import (
    apply_control_migrations,
    apply_data_migrations,
    list_applied,
)


@pytest.mark.asyncio
async def test_apply_control_migrations_is_idempotent(pool: asyncpg.Pool) -> None:
    first = await apply_control_migrations(pool)
    second = await apply_control_migrations(pool)
    assert len(first) >= 1
    assert second == []  # no new migrations applied on second run
    applied = await list_applied(pool, namespace="control")
    assert "0001_base" in applied


@pytest.mark.asyncio
async def test_apply_data_migrations_targets_schema(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    applied = await apply_data_migrations(pool, schema="genereview")
    assert len(applied) >= 1
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "select exists ("
            "  select 1 from information_schema.tables "
            "  where table_schema = 'genereview' and table_name = 'genereview_chapters'"
            ")"
        )
        assert exists is True
```

- [ ] **Step 3: Run test (should fail)**

```bash
GENEREVIEW_TEST_DATABASE_URL=postgresql://postgres:test@localhost:5433/postgres \
  uv run pytest tests/integration/test_migrations.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implement migrate.py**

Create `genereview_link/db/migrate.py`:

```python
"""Migration runner.

Applies SQL migration files in lexical order and records applied versions
in the schema_migrations table. Supports two namespaces:

- ``control`` migrations always apply to the ``public`` schema
- ``data`` migrations apply to a caller-specified target schema
  (typically ``genereview`` or ``genereview_staging``)
"""

from __future__ import annotations

import importlib.resources as pkg_resources
import logging
from typing import Literal

import asyncpg

from genereview_link.db.migrations import control as control_pkg
from genereview_link.db.migrations import data as data_pkg

logger = logging.getLogger(__name__)


Namespace = Literal["control", "data"]


def _list_sql(pkg: object) -> list[tuple[str, str]]:
    files = sorted(
        f.name for f in pkg_resources.files(pkg).iterdir()
        if f.is_file() and f.name.endswith(".sql")
    )
    return [
        (name.removesuffix(".sql"), pkg_resources.files(pkg).joinpath(name).read_text())
        for name in files
    ]


async def _ensure_migrations_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        create schema if not exists public;
        create table if not exists public.schema_migrations (
            namespace   text not null,
            version     text not null,
            applied_at  timestamptz not null default now(),
            primary key (namespace, version)
        )
        """
    )


async def list_applied(pool: asyncpg.Pool, *, namespace: Namespace) -> list[str]:
    async with pool.acquire() as conn:
        await _ensure_migrations_table(conn)
        rows = await conn.fetch(
            "select version from public.schema_migrations where namespace = $1 order by version",
            namespace,
        )
    return [row["version"] for row in rows]


async def apply_control_migrations(pool: asyncpg.Pool) -> list[str]:
    """Apply control migrations into public; return newly applied versions."""
    applied: list[str] = []
    files = _list_sql(control_pkg)
    async with pool.acquire() as conn:
        await _ensure_migrations_table(conn)
        existing = {
            row["version"]
            for row in await conn.fetch(
                "select version from public.schema_migrations where namespace = 'control'"
            )
        }
        for version, sql in files:
            if version in existing:
                continue
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "insert into public.schema_migrations (namespace, version) values ('control', $1)",
                    version,
                )
            applied.append(version)
            logger.info("applied control migration: %s", version)
    return applied


async def apply_data_migrations(pool: asyncpg.Pool, *, schema: str) -> list[str]:
    """Apply data migrations into the given schema; return newly applied versions.

    Migrations may reference the unqualified table names since search_path
    is set to schema,public for the duration of each migration.
    """
    applied: list[str] = []
    files = _list_sql(data_pkg)
    async with pool.acquire() as conn:
        await _ensure_migrations_table(conn)
        await conn.execute(f'create schema if not exists "{schema}"')
        existing = {
            row["version"]
            for row in await conn.fetch(
                "select version from public.schema_migrations "
                "where namespace = 'data' and version like $1",
                f"{schema}:%",
            )
        }
        for version, sql in files:
            qualified = f"{schema}:{version}"
            if qualified in existing:
                continue
            async with conn.transaction():
                await conn.execute(f'set local search_path to "{schema}", public')
                await conn.execute(sql)
                await conn.execute(
                    "insert into public.schema_migrations (namespace, version) values ('data', $1)",
                    qualified,
                )
            applied.append(qualified)
            logger.info("applied data migration: %s into %s", version, schema)
    return applied
```

- [ ] **Step 5: Run test (still fails — no migration files yet)**

Expected: now the import works but tests fail because there are no `.sql` files yet. That's the next task.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/db/migrate.py genereview_link/db/migrations/
git commit -m "feat(db): migration runner with control + data namespaces"
```

### Task 1.5: Control migration 0001 — schema_migrations DDL

**Files:**
- Create: `genereview_link/db/migrations/control/0001_base.sql`

- [ ] **Step 1: Create file**

```sql
-- 0001_base.sql — bootstrap. schema_migrations is created by the runner
-- itself before this migration applies; this file is intentionally a no-op
-- ensuring the runner reaches a valid first-version record.

select 1;
```

- [ ] **Step 2: Commit**

```bash
git add genereview_link/db/migrations/control/0001_base.sql
git commit -m "feat(db): control migration 0001 (schema_migrations bootstrap)"
```

### Task 1.6: Control migration 0002 — corpus_version

**Files:**
- Create: `genereview_link/db/migrations/control/0002_corpus_version.sql`

- [ ] **Step 1: Create file**

```sql
create table if not exists public.genereview_corpus_version (
    version                 text primary key,
    file_list_etag          text,
    tarball_sha256          text,
    tarball_size_bytes      bigint,
    chapter_count           int,
    ingest_started_at       timestamptz not null,
    ingest_finished_at      timestamptz,
    ingest_status           text not null,
    is_active               boolean not null default false,
    notes                   text
);

create unique index if not exists genereview_corpus_version_active_unique
    on public.genereview_corpus_version (is_active) where is_active;
```

- [ ] **Step 2: Commit**

```bash
git add genereview_link/db/migrations/control/0002_corpus_version.sql
git commit -m "feat(db): control migration 0002 (corpus_version table)"
```

### Task 1.7: Control migration 0003 — refresh_log

**Files:**
- Create: `genereview_link/db/migrations/control/0003_refresh_log.sql`

- [ ] **Step 1: Create file**

```sql
create table if not exists public.genereview_refresh_log (
    refresh_id              uuid primary key default gen_random_uuid(),
    check_time              timestamptz not null default now(),
    file_list_last_updated  text,
    decision                text not null,
    duration_ms             bigint,
    detail                  jsonb not null default '{}'::jsonb
);

create index if not exists genereview_refresh_log_time_idx
    on public.genereview_refresh_log (check_time desc);
```

Note: `gen_random_uuid()` requires the `pgcrypto` extension on older Postgres but is built-in in pg13+. We target pg18.

- [ ] **Step 2: Commit**

```bash
git add genereview_link/db/migrations/control/0003_refresh_log.sql
git commit -m "feat(db): control migration 0003 (refresh_log table)"
```

### Task 1.8: Control migration 0004 — active_embedding pointer

**Files:**
- Create: `genereview_link/db/migrations/control/0004_active_embedding.sql`

- [ ] **Step 1: Create file**

```sql
create table if not exists public.genereview_active_embedding (
    id              int primary key default 1 check (id = 1),
    table_name      text not null default 'genereview_embeddings_bge384',
    model_name      text not null default 'BAAI/bge-small-en-v1.5',
    updated_at      timestamptz not null default now()
);

insert into public.genereview_active_embedding default values on conflict do nothing;
```

- [ ] **Step 2: Commit**

```bash
git add genereview_link/db/migrations/control/0004_active_embedding.sql
git commit -m "feat(db): control migration 0004 (active_embedding pointer)"
```

### Task 1.9: Data migration 0001 — genereview_chapters

**Files:**
- Create: `genereview_link/db/migrations/data/0001_chapters.sql`

- [ ] **Step 1: Create file**

```sql
create table if not exists genereview_chapters (
    nbk_id              text primary key,
    short_name          text not null,
    title               text not null,
    pubmed_id           text,
    gene_symbols        text[] not null default '{}',
    omim_ids            text[] not null default '{}',
    authors             text,
    initial_pub_date    date,
    last_updated_date   date,
    corpus_version      text not null,
    nxml_relpath        text not null,
    raw_metadata        jsonb not null default '{}'::jsonb,
    ingested_at         timestamptz not null default now()
);

create index if not exists genereview_chapters_gene_symbols_gin
    on genereview_chapters using gin (gene_symbols);
create index if not exists genereview_chapters_omim_gin
    on genereview_chapters using gin (omim_ids);
create index if not exists genereview_chapters_pubmed_id_idx
    on genereview_chapters (pubmed_id) where pubmed_id is not null;
create index if not exists genereview_chapters_last_updated_idx
    on genereview_chapters (last_updated_date desc);
create index if not exists genereview_chapters_corpus_version_idx
    on genereview_chapters (corpus_version);
```

- [ ] **Step 2: Commit**

```bash
git add genereview_link/db/migrations/data/0001_chapters.sql
git commit -m "feat(db): data migration 0001 (genereview_chapters)"
```

### Task 1.10: Data migration 0002 — genereview_passages

**Files:**
- Create: `genereview_link/db/migrations/data/0002_passages.sql`

- [ ] **Step 1: Create file**

```sql
create table if not exists genereview_passages (
    nbk_id              text not null references genereview_chapters(nbk_id) on delete cascade,
    passage_id          text not null,
    chapter_section     text not null,
    heading_path        text,
    section_level       int not null default 1,
    chunk_index         int not null,
    text                text not null,
    text_hash           text not null,
    char_count          int not null,
    token_estimate      int not null,
    corpus_version      text not null,
    search_vector       tsvector generated always as (
        to_tsvector('english',
            coalesce(heading_path, '') || ' ' ||
            chapter_section || ' ' ||
            text
        )
    ) stored,
    created_at          timestamptz not null default now(),
    primary key (nbk_id, passage_id)
);

create index if not exists genereview_passages_search_vector_gin
    on genereview_passages using gin (search_vector);
create index if not exists genereview_passages_nbk_section_idx
    on genereview_passages (nbk_id, chapter_section);
create index if not exists genereview_passages_section_idx
    on genereview_passages (chapter_section);
create index if not exists genereview_passages_corpus_version_idx
    on genereview_passages (corpus_version);
```

- [ ] **Step 2: Commit**

```bash
git add genereview_link/db/migrations/data/0002_passages.sql
git commit -m "feat(db): data migration 0002 (genereview_passages with tsvector)"
```

### Task 1.11: Data migration 0003 — embeddings_bge384 (no HNSW)

**Files:**
- Create: `genereview_link/db/migrations/data/0003_embeddings_bge384.sql`

- [ ] **Step 1: Create file**

```sql
create extension if not exists vector;

create table if not exists genereview_embeddings_bge384 (
    nbk_id              text not null,
    passage_id          text not null,
    model_name          text not null default 'BAAI/bge-small-en-v1.5',
    model_revision      text,
    text_hash           text not null,
    embedding           vector(384) not null,
    created_at          timestamptz not null default now(),
    primary key (nbk_id, passage_id),
    foreign key (nbk_id, passage_id)
        references genereview_passages(nbk_id, passage_id)
        on delete cascade
);

-- HNSW index intentionally omitted here. The `embed` CLI builds it post-COPY
-- in Phase 3 to avoid per-row index maintenance during bulk ingest.
```

- [ ] **Step 2: Commit**

```bash
git add genereview_link/db/migrations/data/0003_embeddings_bge384.sql
git commit -m "feat(db): data migration 0003 (embeddings table without HNSW)"
```

### Task 1.12: Run migration tests

**Files:** none (uses existing test file)

- [ ] **Step 1: Run integration tests**

```bash
docker run --rm -d --name gr-pg-test -e POSTGRES_PASSWORD=test -p 5433:5432 pgvector/pgvector:0.8.2-pg18
sleep 3
GENEREVIEW_TEST_DATABASE_URL=postgresql://postgres:test@localhost:5433/postgres \
  uv run pytest tests/integration/test_migrations.py -v
docker stop gr-pg-test
```

Expected: 2 passed.

- [ ] **Step 2: Add make target**

In `Makefile` (after the `docker-down` target), append:

```makefile
test-integration: ## Run integration tests (requires GENEREVIEW_TEST_DATABASE_URL)
	uv run pytest tests/integration/ -v
```

If a `test-integration` target already exists, modify to point at `tests/integration/`.

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "build(make): wire test-integration to tests/integration/"
```

### Task 1.13: Add Postgres service to docker-compose

**Files:**
- Modify: `docker/docker-compose.yml`
- Modify: `docker/docker-compose.dev.yml`
- Modify: `docker/docker-compose.prod.yml`

- [ ] **Step 1: Inspect existing compose**

```bash
cat docker/docker-compose.yml
```

- [ ] **Step 2: Add postgres service to docker-compose.yml**

At the top of the `services:` block, add:

```yaml
  postgres:
    image: pgvector/pgvector:0.8.2-pg18
    environment:
      POSTGRES_DB: genereview
      POSTGRES_USER: ${POSTGRES_USER:-genereview}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-genereview}
    volumes:
      - genereview_pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER:-genereview}"]
      interval: 5s
      timeout: 5s
      retries: 10
    restart: unless-stopped
```

At the bottom (after services), add:

```yaml
volumes:
  genereview_pg_data:
```

In the existing `genereview-link` service, add:

```yaml
    environment:
      DATABASE_URL: postgresql://${POSTGRES_USER:-genereview}:${POSTGRES_PASSWORD:-genereview}@postgres:5432/genereview
    depends_on:
      postgres:
        condition: service_healthy
```

- [ ] **Step 3: Mirror dev/prod variants**

In `docker/docker-compose.prod.yml`, add the same postgres block, and on the `genereview-link` service raise:

```yaml
    deploy:
      resources:
        limits:
          memory: 3G        # Raised from 1G — BGE model loaded per gunicorn worker
```

- [ ] **Step 4: Smoke test**

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up -d postgres
sleep 5
docker compose -f docker/docker-compose.yml exec postgres pg_isready -U genereview
docker compose -f docker/docker-compose.yml down -v
```

Expected: `accepting connections`.

- [ ] **Step 5: Commit**

```bash
git add docker/docker-compose.yml docker/docker-compose.dev.yml docker/docker-compose.prod.yml
git commit -m "feat(docker): add Postgres+pgvector service; raise prod memory to 3G"
```

### Task 1.14: db-migrate CLI subcommand + Make target

**Files:**
- Modify: `genereview_link/cli.py`
- Modify: `Makefile`

- [ ] **Step 1: Read current CLI structure**

```bash
sed -n '1,80p' genereview_link/cli.py
```

- [ ] **Step 2: Add `db` subapp**

Append to `genereview_link/cli.py` (after the existing app commands):

```python
db_app = typer.Typer(name="db", help="Database administration commands.")
app.add_typer(db_app)


@db_app.command("migrate")
def db_migrate(
    schema: Annotated[
        str,
        typer.Option("--schema", help="Data schema to apply data migrations into."),
    ] = "genereview",
) -> None:
    """Apply control and data migrations against DATABASE_URL."""
    import asyncio

    from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
    from genereview_link.db.pool import create_pool

    async def run() -> None:
        pool = await create_pool()
        try:
            control = await apply_control_migrations(pool)
            data = await apply_data_migrations(pool, schema=schema)
            for v in control:
                typer.echo(f"control: {v}")
            for v in data:
                typer.echo(f"data: {v}")
            if not control and not data:
                typer.echo("nothing to apply (all migrations already applied)")
        finally:
            await pool.close()

    asyncio.run(run())
```

- [ ] **Step 3: Make targets**

Append to `Makefile`:

```makefile
db-migrate: ## Apply control + data migrations against $DATABASE_URL
	uv run genereview-link db migrate

db-shell: ## psql shell into the docker-compose postgres
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml -f docker/docker-compose.dev.yml exec postgres psql -U $${POSTGRES_USER:-genereview} -d genereview

db-reset: ## DROP and recreate genereview schemas (dev only)
	uv run python -c "import asyncio,asyncpg,os; \
async def go():\n  p=await asyncpg.create_pool(os.environ['DATABASE_URL']);\n  async with p.acquire() as c:\n    await c.execute('drop schema if exists genereview cascade');\n    await c.execute('drop schema if exists genereview_staging cascade');\n  await p.close()\nasyncio.run(go())"
	$(MAKE) db-migrate
```

(Note: the `db-reset` heredoc is fiddly; replace with a small `genereview_link/cli.py` `db reset` command instead — see Step 4.)

- [ ] **Step 4: Add `db reset` CLI command and simplify Makefile**

In `genereview_link/cli.py` (after `db_migrate`):

```python
@db_app.command("reset")
def db_reset(
    confirm: Annotated[bool, typer.Option("--yes", help="Confirm destructive operation.")] = False,
) -> None:
    """DROP genereview/genereview_staging schemas and re-run migrations (dev only)."""
    import asyncio

    from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
    from genereview_link.db.pool import create_pool

    if not confirm:
        typer.echo("Refusing to reset without --yes")
        raise typer.Exit(1)

    async def run() -> None:
        pool = await create_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute("drop schema if exists genereview cascade")
                await conn.execute("drop schema if exists genereview_staging cascade")
                rows = await conn.fetch(
                    "select schema_name from information_schema.schemata "
                    "where schema_name like 'genereview_old_%'"
                )
                for row in rows:
                    await conn.execute(f"drop schema {row['schema_name']} cascade")
            await apply_control_migrations(pool)
            await apply_data_migrations(pool, schema="genereview")
            typer.echo("reset complete")
        finally:
            await pool.close()

    asyncio.run(run())
```

Replace the broken `db-reset` make target with:

```makefile
db-reset: ## DROP and recreate genereview schemas (dev only)
	uv run genereview-link db reset --yes
```

- [ ] **Step 5: End-to-end smoke**

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up -d postgres
sleep 5
DATABASE_URL=postgresql://genereview:genereview@localhost:5432/genereview make db-migrate
DATABASE_URL=postgresql://genereview:genereview@localhost:5432/genereview make db-migrate  # idempotent
docker compose -f docker/docker-compose.yml down -v
```

Expected: first run prints control + data migration names; second run prints `nothing to apply`.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/cli.py Makefile
git commit -m "feat(cli): add db migrate + db reset subcommands; wire make targets"
```

**Phase 1 done.** `docker compose up` provisions Postgres; `make db-migrate` creates the schema. No route behavior changed.

---

## Phase 2 — Corpus ingest pipeline

Goal: `genereview-link ingest` populates `genereview_chapters` + `genereview_passages` for all ~900 chapters via staging-schema atomic swap. No embeddings yet.

### Task 2.1: ChapterRecord / PassageRecord dataclasses

**Files:**
- Create: `genereview_link/corpus/__init__.py`
- Create: `genereview_link/corpus/records.py`
- Test: `tests/unit/__init__.py`, `tests/unit/test_corpus_records.py`

- [ ] **Step 1: Create unit tests dir**

```bash
mkdir -p tests/unit
touch tests/unit/__init__.py
```

- [ ] **Step 2: Write failing test**

`tests/unit/test_corpus_records.py`:

```python
"""Tests for corpus record dataclasses."""

from __future__ import annotations

from datetime import date

from genereview_link.corpus.records import ChapterRecord, PassageRecord


def test_chapter_record_is_frozen() -> None:
    rec = ChapterRecord(
        nbk_id="NBK1247",
        short_name="brca1",
        title="BRCA1- and BRCA2-Associated Hereditary Breast and Ovarian Cancer",
        pubmed_id="20301425",
        gene_symbols=("BRCA1", "BRCA2"),
        omim_ids=("113705", "600185"),
        authors="Petrucelli N, Daly MB, Pal T",
        initial_pub_date=date(1998, 9, 4),
        last_updated_date=date(2023, 9, 21),
        nxml_relpath="gene_NBK1116/brca1.nxml",
        raw_metadata={},
    )
    assert rec.nbk_id == "NBK1247"
    assert "BRCA1" in rec.gene_symbols


def test_passage_record_text_hash_property() -> None:
    rec = PassageRecord(
        nbk_id="NBK1247",
        passage_id="NBK1247:0001",
        chapter_section="summary",
        heading_path="Summary",
        section_level=1,
        chunk_index=0,
        text="The hallmark of hereditary breast and ovarian cancer.",
        char_count=53,
        token_estimate=10,
    )
    assert rec.text_hash.startswith(("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "a", "b", "c", "d", "e", "f"))
    assert len(rec.text_hash) == 64
```

- [ ] **Step 3: Run test (should fail)**

```bash
uv run pytest tests/unit/test_corpus_records.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implement**

`genereview_link/corpus/__init__.py`:

```python
"""Corpus ingestion: FTP fetch, NXML parsing, chunking, side-data join."""
```

`genereview_link/corpus/records.py`:

```python
"""Dataclasses representing parsed corpus rows before DB insert."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from functools import cached_property


@dataclass(frozen=True, slots=True)
class ChapterRecord:
    """One GeneReviews chapter, ready for genereview_chapters insertion."""

    nbk_id: str
    short_name: str
    title: str
    pubmed_id: str | None
    gene_symbols: tuple[str, ...]
    omim_ids: tuple[str, ...]
    authors: str | None
    initial_pub_date: date | None
    last_updated_date: date | None
    nxml_relpath: str
    raw_metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PassageRecord:
    """One chunked passage, ready for genereview_passages insertion."""

    nbk_id: str
    passage_id: str
    chapter_section: str
    heading_path: str | None
    section_level: int
    chunk_index: int
    text: str
    char_count: int
    token_estimate: int

    @cached_property
    def text_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()
```

- [ ] **Step 5: Run test (should pass)**

```bash
uv run pytest tests/unit/test_corpus_records.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/corpus/ tests/unit/__init__.py tests/unit/test_corpus_records.py
git commit -m "feat(corpus): ChapterRecord and PassageRecord dataclasses"
```

### Task 2.2: Canonical section vocabulary

**Files:**
- Create: `genereview_link/corpus/canonicalize.py`
- Test: `tests/unit/test_corpus_canonicalize.py`

- [ ] **Step 1: Write failing test**

`tests/unit/test_corpus_canonicalize.py`:

```python
"""Tests for section-name canonicalization."""

from __future__ import annotations

import pytest

from genereview_link.corpus.canonicalize import (
    CANONICAL_SECTIONS,
    canonical_section,
)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Summary", "summary"),
        ("SUMMARY", "summary"),
        ("Diagnosis", "diagnosis"),
        ("Diagnosis/Testing", "diagnosis"),
        ("Establishing the Diagnosis", "diagnosis"),
        ("Clinical Description", "clinical_features"),
        ("Clinical Characteristics", "clinical_features"),
        ("Differential Diagnosis", "clinical_features"),
        ("Management", "management"),
        ("Treatment of Manifestations", "management"),
        ("Surveillance", "management"),
        ("Genetic Counseling", "genetic_counseling"),
        ("Molecular Genetics", "molecular_genetics"),
        ("Pathogenic variants", "molecular_genetics"),
        ("Resources", "resources"),
        ("References", "references"),
        ("Some Other Heading", "other"),
        ("", "other"),
    ],
)
def test_canonical_section_maps_titles(title: str, expected: str) -> None:
    assert canonical_section(title) == expected


def test_canonical_sections_are_documented() -> None:
    assert {
        "summary",
        "diagnosis",
        "clinical_features",
        "management",
        "genetic_counseling",
        "molecular_genetics",
        "resources",
        "references",
        "other",
    } <= CANONICAL_SECTIONS
```

- [ ] **Step 2: Run test (should fail)**

```bash
uv run pytest tests/unit/test_corpus_canonicalize.py -v
```

- [ ] **Step 3: Implement**

`genereview_link/corpus/canonicalize.py`:

```python
"""Map free-form GeneReviews section titles to the closed canonical vocabulary.

The closed vocabulary feeds retrieval/rerank.py SECTION_PRIORITY and lets
operators reliably filter by section.
"""

from __future__ import annotations

import re

CANONICAL_SECTIONS: frozenset[str] = frozenset(
    {
        "summary",
        "diagnosis",
        "clinical_features",
        "management",
        "genetic_counseling",
        "molecular_genetics",
        "resources",
        "references",
        "other",
    }
)


# Ordered: first match wins. Patterns are case-insensitive whole-token matches.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*summary\b", re.I), "summary"),
    (re.compile(r"^\s*references?\b", re.I), "references"),
    (re.compile(r"^\s*resources\b", re.I), "resources"),
    (re.compile(r"genetic\s+counsel", re.I), "genetic_counseling"),
    (re.compile(r"molecular\s+genetics", re.I), "molecular_genetics"),
    (re.compile(r"pathogenic\s+variants?", re.I), "molecular_genetics"),
    (re.compile(r"diagnos", re.I), "diagnosis"),
    (re.compile(r"clinical\s+(description|characteristics|features)", re.I), "clinical_features"),
    (re.compile(r"differential\s+diagnos", re.I), "clinical_features"),
    (re.compile(r"^\s*(treatment|surveillance|management|therapy|prevention)\b", re.I), "management"),
)


def canonical_section(title: str | None) -> str:
    """Return the canonical section key for a free-form chapter section title."""
    if not title:
        return "other"
    for pattern, canonical in _RULES:
        if pattern.search(title):
            return canonical
    return "other"
```

- [ ] **Step 4: Run test (should pass)**

```bash
uv run pytest tests/unit/test_corpus_canonicalize.py -v
```

Expected: ~16 passed.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/corpus/canonicalize.py tests/unit/test_corpus_canonicalize.py
git commit -m "feat(corpus): canonical section vocabulary + regex rules"
```

### Task 2.3: BGE tokenizer cache

**Files:**
- Create: `genereview_link/corpus/tokenizer.py`
- Test: `tests/unit/test_corpus_tokenizer.py`

- [ ] **Step 1: Add transformers dep**

In `pyproject.toml`, add to `[project] dependencies`:

```toml
"transformers>=4.46.0",
"tokenizers>=0.20.0",
```

Run `make lock && make install`.

- [ ] **Step 2: Write failing test**

`tests/unit/test_corpus_tokenizer.py`:

```python
"""Tests for the BGE tokenizer cache."""

from __future__ import annotations

import pytest

from genereview_link.corpus.tokenizer import bge_tokenizer, count_tokens


@pytest.mark.slow
def test_count_tokens_matches_bge_tokenizer() -> None:
    text = "The breast cancer susceptibility gene BRCA1 encodes a tumor suppressor."
    n = count_tokens(text)
    assert 10 <= n <= 20  # rough; actual exact count depends on tokenizer version


@pytest.mark.slow
def test_tokenizer_is_singleton() -> None:
    a = bge_tokenizer()
    b = bge_tokenizer()
    assert a is b
```

Add a `[tool.pytest.ini_options]` marker registration if not already present, in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "slow: tests that load heavy ML models",
]
```

- [ ] **Step 3: Implement**

`genereview_link/corpus/tokenizer.py`:

```python
"""Cached BGE tokenizer for chunk boundary calculation and encoding.

The same tokenizer instance is used by chunking.py (window boundaries) and
retrieval/embeddings.py (query encoding) so chunk size guarantees match
encoder input size.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

BGE_MODEL_NAME = "BAAI/bge-small-en-v1.5"
BGE_MAX_TOKENS = 512                  # model context
BGE_RESERVED_SPECIAL_TOKENS = 2       # [CLS], [SEP]
BGE_NET_CHUNK_TOKENS = BGE_MAX_TOKENS - BGE_RESERVED_SPECIAL_TOKENS  # 510


@lru_cache(maxsize=1)
def bge_tokenizer() -> Any:
    """Load BGE WordPiece tokenizer once per process."""
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(BGE_MODEL_NAME, use_fast=True)


def count_tokens(text: str) -> int:
    """Return the BGE token count for *text* (excluding special tokens)."""
    tok = bge_tokenizer()
    return len(tok.encode(text, add_special_tokens=False))


def encode_to_token_ids(text: str) -> list[int]:
    """Return the BGE token id sequence (no special tokens)."""
    tok = bge_tokenizer()
    return list(tok.encode(text, add_special_tokens=False))


def decode_tokens(token_ids: list[int]) -> str:
    """Inverse of encode_to_token_ids."""
    tok = bge_tokenizer()
    return tok.decode(token_ids, skip_special_tokens=True)
```

- [ ] **Step 4: Run test (slow — downloads model)**

```bash
uv run pytest tests/unit/test_corpus_tokenizer.py -v -m slow
```

Expected: 2 passed. First run downloads ~70 MB.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock genereview_link/corpus/tokenizer.py tests/unit/test_corpus_tokenizer.py
git commit -m "feat(corpus): cached BGE tokenizer for chunk + encode parity"
```

### Task 2.4: Chunking (510-token windows within sections)

**Files:**
- Create: `genereview_link/corpus/chunking.py`
- Test: `tests/unit/test_corpus_chunking.py`

- [ ] **Step 1: Write failing test**

`tests/unit/test_corpus_chunking.py`:

```python
"""Tests for chunking."""

from __future__ import annotations

import pytest

from genereview_link.corpus.chunking import chunk_section_text


@pytest.mark.slow
def test_short_section_yields_one_chunk() -> None:
    text = "Short summary of the disease and its inheritance pattern."
    chunks = chunk_section_text(text, max_tokens=510, overlap_tokens=50)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].chunk_index == 0


@pytest.mark.slow
def test_long_section_splits_with_overlap() -> None:
    text = ". ".join(f"Sentence number {i} discussing pathogenic variants" for i in range(200))
    chunks = chunk_section_text(text, max_tokens=510, overlap_tokens=50)
    assert len(chunks) >= 2
    # adjacent chunks must overlap
    a = chunks[0].text
    b = chunks[1].text
    # the last ~50 tokens of a should appear at start of b
    assert any(word in a and word in b for word in b.split()[:20])


@pytest.mark.slow
def test_chunks_index_is_sequential() -> None:
    text = ". ".join(f"Word {i}" for i in range(2000))
    chunks = chunk_section_text(text, max_tokens=510, overlap_tokens=50)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
```

- [ ] **Step 2: Run test (should fail)**

```bash
uv run pytest tests/unit/test_corpus_chunking.py -v -m slow
```

- [ ] **Step 3: Implement**

`genereview_link/corpus/chunking.py`:

```python
"""Token-window chunker that never crosses section boundaries.

Used by corpus/nxml.py to split each <sec> body into BGE-compatible windows.
"""

from __future__ import annotations

from dataclasses import dataclass

from genereview_link.corpus.tokenizer import (
    BGE_NET_CHUNK_TOKENS,
    decode_tokens,
    encode_to_token_ids,
)

DEFAULT_OVERLAP_TOKENS = 50


@dataclass(frozen=True, slots=True)
class TextChunk:
    """One section-bounded chunk."""

    chunk_index: int
    text: str
    token_count: int


def chunk_section_text(
    text: str,
    *,
    max_tokens: int = BGE_NET_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[TextChunk]:
    """Split *text* into overlapping token windows.

    The full *text* must come from within a single <sec>; this function never
    looks for paragraph boundaries to split — that decoupling happens in nxml.py.
    """
    if not text.strip():
        return []

    token_ids = encode_to_token_ids(text)
    if len(token_ids) <= max_tokens:
        return [
            TextChunk(chunk_index=0, text=text, token_count=len(token_ids))
        ]

    stride = max_tokens - overlap_tokens
    if stride <= 0:
        raise ValueError(f"overlap_tokens ({overlap_tokens}) must be < max_tokens ({max_tokens})")

    chunks: list[TextChunk] = []
    start = 0
    index = 0
    while start < len(token_ids):
        window = token_ids[start : start + max_tokens]
        chunks.append(
            TextChunk(
                chunk_index=index,
                text=decode_tokens(window),
                token_count=len(window),
            )
        )
        if start + max_tokens >= len(token_ids):
            break
        start += stride
        index += 1
    return chunks
```

- [ ] **Step 4: Run test (should pass)**

```bash
uv run pytest tests/unit/test_corpus_chunking.py -v -m slow
```

- [ ] **Step 5: Commit**

```bash
git add genereview_link/corpus/chunking.py tests/unit/test_corpus_chunking.py
git commit -m "feat(corpus): token-window chunker with overlap"
```

### Task 2.5: NXML parser (BITS book-part)

**Files:**
- Create: `genereview_link/corpus/nxml.py`
- Create: `tests/fixtures/nxml/typical.nxml`
- Create: `tests/fixtures/nxml/multigene.nxml`
- Create: `tests/fixtures/nxml/missing_pubdate.nxml`
- Create: `tests/fixtures/nxml/malformed.nxml`
- Test: `tests/unit/test_corpus_nxml.py`

- [ ] **Step 1: Build fixture: typical.nxml**

`tests/fixtures/nxml/typical.nxml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<book-part xmlns:xlink="http://www.w3.org/1999/xlink" book-part-type="chapter" id="brca1">
  <book-part-meta>
    <book-part-id pub-id-type="pmid">20301425</book-part-id>
    <title-group>
      <title>BRCA1- and BRCA2-Associated Hereditary Breast and Ovarian Cancer</title>
    </title-group>
    <contrib-group>
      <contrib contrib-type="author"><name><surname>Petrucelli</surname><given-names>N</given-names></name></contrib>
      <contrib contrib-type="author"><name><surname>Daly</surname><given-names>MB</given-names></name></contrib>
    </contrib-group>
    <pub-date pub-type="initial"><day>4</day><month>9</month><year>1998</year></pub-date>
    <pub-date pub-type="updated"><day>21</day><month>9</month><year>2023</year></pub-date>
  </book-part-meta>
  <body>
    <sec id="summary">
      <title>Summary</title>
      <p>BRCA1 and BRCA2 are tumor suppressor genes.</p>
      <p>Pathogenic variants confer increased lifetime risk of breast and ovarian cancer.</p>
    </sec>
    <sec id="diagnosis">
      <title>Diagnosis</title>
      <p>Molecular genetic testing is required for diagnosis.</p>
      <sec id="diagnosis-establishing">
        <title>Establishing the Diagnosis</title>
        <p>Multi-gene panel testing is the preferred approach.</p>
      </sec>
    </sec>
    <sec id="management">
      <title>Management</title>
      <p>Risk-reducing surgery may be considered.</p>
    </sec>
  </body>
</book-part>
```

- [ ] **Step 2: Build fixture: multigene.nxml**

`tests/fixtures/nxml/multigene.nxml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<book-part book-part-type="chapter" id="nf1nf2">
  <book-part-meta>
    <book-part-id pub-id-type="pmid">30000001</book-part-id>
    <title-group><title>Neurofibromatosis Type 1 and Type 2 — Combined Chapter</title></title-group>
    <pub-date pub-type="updated"><day>1</day><month>1</month><year>2025</year></pub-date>
  </book-part-meta>
  <body>
    <sec><title>Summary</title><p>NF1 and NF2 share clinical overlap.</p></sec>
  </body>
</book-part>
```

- [ ] **Step 3: Build fixture: missing_pubdate.nxml**

`tests/fixtures/nxml/missing_pubdate.nxml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<book-part book-part-type="chapter" id="nopub">
  <book-part-meta>
    <title-group><title>Test Chapter Without Dates</title></title-group>
  </book-part-meta>
  <body><sec><title>Summary</title><p>Body text.</p></sec></body>
</book-part>
```

- [ ] **Step 4: Build fixture: malformed.nxml**

`tests/fixtures/nxml/malformed.nxml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<book-part book-part-type="chapter">
  <book-part-meta><title-group><title>Truncated
```

- [ ] **Step 5: Write failing test**

`tests/unit/test_corpus_nxml.py`:

```python
"""Tests for the BITS NXML parser."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from genereview_link.corpus.nxml import NxmlParseError, parse_and_chunk_one

FIXTURES = Path(__file__).parent.parent / "fixtures" / "nxml"


@pytest.mark.slow
def test_typical_chapter_yields_record_and_passages() -> None:
    raw = (FIXTURES / "typical.nxml").read_bytes()
    chapter, passages = parse_and_chunk_one(
        raw, nbk_id="NBK1247", short_name="brca1", nxml_relpath="gene_NBK1116/brca1.nxml"
    )
    assert chapter.nbk_id == "NBK1247"
    assert chapter.pubmed_id == "20301425"
    assert chapter.title.startswith("BRCA1")
    assert chapter.last_updated_date == date(2023, 9, 21)
    assert chapter.initial_pub_date == date(1998, 9, 4)
    assert "Petrucelli" in (chapter.authors or "")

    sections = {p.chapter_section for p in passages}
    assert {"summary", "diagnosis", "management"} <= sections
    # heading_path includes nesting
    diag_subs = [p for p in passages if "Establishing" in (p.heading_path or "")]
    assert diag_subs and diag_subs[0].section_level >= 2


@pytest.mark.slow
def test_missing_pubdate_does_not_crash() -> None:
    raw = (FIXTURES / "missing_pubdate.nxml").read_bytes()
    chapter, _ = parse_and_chunk_one(raw, nbk_id="NBK9999", short_name="nopub", nxml_relpath="x.nxml")
    assert chapter.last_updated_date is None
    assert chapter.initial_pub_date is None


def test_malformed_raises() -> None:
    raw = (FIXTURES / "malformed.nxml").read_bytes()
    with pytest.raises(NxmlParseError):
        parse_and_chunk_one(raw, nbk_id="NBKBAD", short_name="bad", nxml_relpath="bad.nxml")
```

- [ ] **Step 6: Run test (should fail)**

```bash
uv run pytest tests/unit/test_corpus_nxml.py -v -m slow
```

- [ ] **Step 7: Implement**

`genereview_link/corpus/nxml.py`:

```python
"""Parse one BITS NXML chapter into ChapterRecord + PassageRecord list.

Uses defusedxml.lxml per AGENTS.md. Output is ready for asyncpg COPY.
"""

from __future__ import annotations

from datetime import date
from typing import cast

from defusedxml.lxml import fromstring
from lxml import etree

from genereview_link.corpus.canonicalize import canonical_section
from genereview_link.corpus.chunking import DEFAULT_OVERLAP_TOKENS, chunk_section_text
from genereview_link.corpus.records import ChapterRecord, PassageRecord
from genereview_link.corpus.tokenizer import BGE_NET_CHUNK_TOKENS


class NxmlParseError(Exception):
    """Raised when an NXML file cannot be parsed at all."""


def parse_and_chunk_one(
    raw_xml: bytes,
    *,
    nbk_id: str,
    short_name: str,
    nxml_relpath: str,
    max_tokens: int = BGE_NET_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> tuple[ChapterRecord, list[PassageRecord]]:
    """Parse one BITS book-part NXML and emit chapter + chunked passages.

    Raises:
        NxmlParseError: if the XML cannot be parsed.
    """
    try:
        root = fromstring(raw_xml)
    except etree.XMLSyntaxError as exc:
        raise NxmlParseError(f"XML syntax error in {nbk_id}: {exc}") from exc

    meta = root.find("book-part-meta")
    if meta is None:
        raise NxmlParseError(f"{nbk_id}: missing <book-part-meta>")

    title_el = meta.find("title-group/title")
    title = _text(title_el) or short_name

    pubmed_id = _text(meta.find("book-part-id[@pub-id-type='pmid']")) or None

    authors = _join_authors(meta.find("contrib-group"))
    initial = _parse_pub_date(meta.find("pub-date[@pub-type='initial']"))
    updated = _parse_pub_date(meta.find("pub-date[@pub-type='updated']"))

    chapter = ChapterRecord(
        nbk_id=nbk_id,
        short_name=short_name,
        title=title,
        pubmed_id=pubmed_id,
        gene_symbols=(),     # populated by sidedata join
        omim_ids=(),         # populated by sidedata join
        authors=authors,
        initial_pub_date=initial,
        last_updated_date=updated,
        nxml_relpath=nxml_relpath,
        raw_metadata={},
    )

    body = root.find("body")
    passages: list[PassageRecord] = []
    if body is not None:
        global_chunk = 0
        for section in body.findall("sec"):
            for chunk_passages, global_chunk in _walk_section(
                section,
                nbk_id=nbk_id,
                ancestor_titles=(),
                level=1,
                global_chunk=global_chunk,
                max_tokens=max_tokens,
                overlap_tokens=overlap_tokens,
            ):
                passages.extend(chunk_passages)
    return chapter, passages


# ---------- helpers ----------

def _text(el: etree._Element | None) -> str | None:
    if el is None:
        return None
    return ("".join(el.itertext()) or "").strip() or None


def _join_authors(group: etree._Element | None) -> str | None:
    if group is None:
        return None
    names: list[str] = []
    for contrib in group.findall("contrib"):
        surname = _text(contrib.find("name/surname"))
        given = _text(contrib.find("name/given-names"))
        if surname and given:
            names.append(f"{surname} {given}")
        elif surname:
            names.append(surname)
    return ", ".join(names) if names else None


def _parse_pub_date(el: etree._Element | None) -> date | None:
    if el is None:
        return None
    try:
        y = int(_text(el.find("year")) or "")
        m = int(_text(el.find("month")) or "1")
        d = int(_text(el.find("day")) or "1")
        return date(y, m, d)
    except (TypeError, ValueError):
        return None


def _walk_section(
    section: etree._Element,
    *,
    nbk_id: str,
    ancestor_titles: tuple[str, ...],
    level: int,
    global_chunk: int,
    max_tokens: int,
    overlap_tokens: int,
):
    """Recursive section walker. Yields (passages_for_this_call, next_global_chunk)."""
    title_el = section.find("title")
    title = _text(title_el) or "(untitled)"
    titles = ancestor_titles + (title,)
    heading_path = " > ".join(titles)
    canonical = canonical_section(titles[0])

    own_text_parts = [
        _text(p) for p in section.findall("p") if _text(p)
    ]
    if own_text_parts:
        full = "\n\n".join(cast(list[str], own_text_parts))
        chunks = chunk_section_text(full, max_tokens=max_tokens, overlap_tokens=overlap_tokens)
        passages: list[PassageRecord] = []
        for c in chunks:
            passages.append(
                PassageRecord(
                    nbk_id=nbk_id,
                    passage_id=f"{nbk_id}:{global_chunk:04d}",
                    chapter_section=canonical,
                    heading_path=heading_path,
                    section_level=level,
                    chunk_index=c.chunk_index,
                    text=c.text,
                    char_count=len(c.text),
                    token_estimate=c.token_count,
                )
            )
            global_chunk += 1
        yield passages, global_chunk

    for sub in section.findall("sec"):
        yield from _walk_section(
            sub,
            nbk_id=nbk_id,
            ancestor_titles=titles,
            level=level + 1,
            global_chunk=global_chunk,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
        )
```

- [ ] **Step 8: Run test**

```bash
uv run pytest tests/unit/test_corpus_nxml.py -v -m slow
```

Expected: 3 passed.

- [ ] **Step 9: Commit**

```bash
git add genereview_link/corpus/nxml.py tests/fixtures/nxml/ tests/unit/test_corpus_nxml.py
git commit -m "feat(corpus): BITS NXML parser with section walker"
```

### Task 2.6: Side-data parser

**Files:**
- Create: `genereview_link/corpus/sidedata.py`
- Create: `tests/fixtures/sidedata/NBKid_shortname_genesymbol.txt`
- Create: `tests/fixtures/sidedata/NBKid_shortname_OMIM.txt`
- Create: `tests/fixtures/sidedata/GRtitle_shortname_NBKid.txt`
- Test: `tests/unit/test_corpus_sidedata.py`

- [ ] **Step 1: Build fixtures**

`tests/fixtures/sidedata/NBKid_shortname_genesymbol.txt`:

```
NBK1247	brca1	BRCA1
NBK1247	brca1	BRCA2
NBK1311	huntington	HTT
NBK9999	nopub	TESTGENE
```

`tests/fixtures/sidedata/NBKid_shortname_OMIM.txt`:

```
NBK1247	brca1	113705
NBK1247	brca1	600185
NBK1311	huntington	143100
```

`tests/fixtures/sidedata/GRtitle_shortname_NBKid.txt`:

```
BRCA1- and BRCA2-Associated Hereditary Breast and Ovarian Cancer	brca1	NBK1247
Huntington Disease	huntington	NBK1311
```

- [ ] **Step 2: Write failing test**

`tests/unit/test_corpus_sidedata.py`:

```python
"""Tests for side-data parsing."""

from __future__ import annotations

from pathlib import Path

from genereview_link.corpus.sidedata import SideData, load_sidedata

FIXTURES = Path(__file__).parent.parent / "fixtures" / "sidedata"


def test_gene_symbols_aggregate_per_nbk() -> None:
    sd = load_sidedata(FIXTURES)
    assert sd.gene_symbols["NBK1247"] == ("BRCA1", "BRCA2")
    assert sd.gene_symbols["NBK1311"] == ("HTT",)


def test_omim_ids_aggregate_per_nbk() -> None:
    sd = load_sidedata(FIXTURES)
    assert sd.omim_ids["NBK1247"] == ("113705", "600185")


def test_short_name_lookup() -> None:
    sd = load_sidedata(FIXTURES)
    assert sd.short_name_by_nbk["NBK1247"] == "brca1"


def test_missing_nbk_returns_empty_tuple() -> None:
    sd = load_sidedata(FIXTURES)
    assert sd.gene_symbols.get("NBKMISSING", ()) == ()
```

- [ ] **Step 3: Run test (should fail)**

```bash
uv run pytest tests/unit/test_corpus_sidedata.py -v
```

- [ ] **Step 4: Implement**

`genereview_link/corpus/sidedata.py`:

```python
"""Parse the three NBK side-data files into in-memory dicts.

Source: https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews/
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

GENESYM_FILE = "NBKid_shortname_genesymbol.txt"
OMIM_FILE = "NBKid_shortname_OMIM.txt"
TITLE_FILE = "GRtitle_shortname_NBKid.txt"


@dataclass(frozen=True, slots=True)
class SideData:
    """In-memory join tables keyed by NBK id."""

    gene_symbols: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    omim_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    short_name_by_nbk: Mapping[str, str] = field(default_factory=dict)


def load_sidedata(directory: Path) -> SideData:
    """Load and parse the three GeneReviews side-data files.

    Each is tab-separated. Multi-value rows are aggregated into tuples.
    """
    gene_symbols: dict[str, list[str]] = defaultdict(list)
    omim_ids: dict[str, list[str]] = defaultdict(list)
    short_name_by_nbk: dict[str, str] = {}

    gs_path = directory / GENESYM_FILE
    if gs_path.exists():
        for row in _rows(gs_path):
            if len(row) >= 3:
                nbk, short, gene = row[0], row[1], row[2]
                if gene and gene not in gene_symbols[nbk]:
                    gene_symbols[nbk].append(gene)
                if short:
                    short_name_by_nbk.setdefault(nbk, short)

    om_path = directory / OMIM_FILE
    if om_path.exists():
        for row in _rows(om_path):
            if len(row) >= 3:
                nbk, _short, omim = row[0], row[1], row[2]
                if omim and omim not in omim_ids[nbk]:
                    omim_ids[nbk].append(omim)

    title_path = directory / TITLE_FILE
    if title_path.exists():
        for row in _rows(title_path):
            if len(row) >= 3:
                _title, short, nbk = row[0], row[1], row[2]
                short_name_by_nbk.setdefault(nbk, short)

    return SideData(
        gene_symbols={k: tuple(v) for k, v in gene_symbols.items()},
        omim_ids={k: tuple(v) for k, v in omim_ids.items()},
        short_name_by_nbk=short_name_by_nbk,
    )


def _rows(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        yield line.split("\t")
```

- [ ] **Step 5: Run test (should pass)**

```bash
uv run pytest tests/unit/test_corpus_sidedata.py -v
```

- [ ] **Step 6: Commit**

```bash
git add genereview_link/corpus/sidedata.py tests/fixtures/sidedata/ tests/unit/test_corpus_sidedata.py
git commit -m "feat(corpus): side-data file parsing (gene_symbol, OMIM, title)"
```

### Task 2.7: Archive downloader (file_list.csv + tarball)

**Files:**
- Create: `genereview_link/corpus/archive.py`
- Test: `tests/unit/test_corpus_archive.py`

- [ ] **Step 1: Write failing test**

`tests/unit/test_corpus_archive.py`:

```python
"""Tests for archive metadata parsing (offline)."""

from __future__ import annotations

from genereview_link.corpus.archive import parse_file_list_row


def test_parse_nbk1116_row() -> None:
    row = "ca/84/gene_NBK1116.tar.gz,GeneReviews(R),\"University of Washington, Seattle\",1993,NBK1116,2026-05-10 03:32:37"
    parsed = parse_file_list_row(row)
    assert parsed is not None
    assert parsed.nbk_id == "NBK1116"
    assert parsed.last_updated == "2026-05-10 03:32:37"
    assert parsed.relpath == "ca/84/gene_NBK1116.tar.gz"


def test_unrelated_row_returns_none() -> None:
    row = "aa/01/other.tar.gz,Other Book,Author,2020,NBK9999,2024-01-01 00:00:00"
    parsed = parse_file_list_row(row, nbk_filter="NBK1116")
    assert parsed is None
```

- [ ] **Step 2: Run test (should fail)**

```bash
uv run pytest tests/unit/test_corpus_archive.py -v
```

- [ ] **Step 3: Implement**

`genereview_link/corpus/archive.py`:

```python
"""Fetch and parse the NCBI litarch file_list.csv and the gene_NBK1116 tarball.

The tarball is large (~607 MB); download is range-resumable and verifies
sha256 against an out-of-band expected value when one is supplied.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import httpx

FILE_LIST_URL = "https://ftp.ncbi.nlm.nih.gov/pub/litarch/file_list.csv"
LITARCH_BASE = "https://ftp.ncbi.nlm.nih.gov/pub/litarch"


@dataclass(frozen=True, slots=True)
class ArchiveListing:
    relpath: str
    title: str
    publisher: str
    initial_year: str
    nbk_id: str
    last_updated: str


def parse_file_list_row(row: str, nbk_filter: str = "NBK1116") -> ArchiveListing | None:
    """Parse one row of file_list.csv; return ArchiveListing iff nbk matches."""
    reader = csv.reader(io.StringIO(row))
    fields = next(reader, None)
    if not fields or len(fields) < 6:
        return None
    relpath, title, publisher, year, nbk, last = (
        fields[0],
        fields[1],
        fields[2],
        fields[3],
        fields[4],
        fields[5],
    )
    if nbk != nbk_filter:
        return None
    return ArchiveListing(
        relpath=relpath,
        title=title,
        publisher=publisher,
        initial_year=year,
        nbk_id=nbk,
        last_updated=last,
    )


async def fetch_listing(*, nbk_id: str = "NBK1116") -> ArchiveListing:
    """Fetch file_list.csv and return the ArchiveListing for *nbk_id*."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(FILE_LIST_URL)
        resp.raise_for_status()
        for line in resp.text.splitlines():
            parsed = parse_file_list_row(line, nbk_filter=nbk_id)
            if parsed:
                return parsed
    raise RuntimeError(f"NBK id {nbk_id} not found in {FILE_LIST_URL}")


async def download_tarball(
    listing: ArchiveListing,
    *,
    dest: Path,
    chunk_size: int = 1 << 20,  # 1 MiB
) -> str:
    """Stream-download the tarball to *dest*; return its sha256."""
    url = f"{LITARCH_BASE}/{listing.relpath}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with dest.open("wb") as fh:
                async for chunk in resp.aiter_bytes(chunk_size):
                    sha.update(chunk)
                    fh.write(chunk)
    return sha.hexdigest()
```

- [ ] **Step 4: Run unit test (should pass)**

```bash
uv run pytest tests/unit/test_corpus_archive.py -v
```

- [ ] **Step 5: Commit**

```bash
git add genereview_link/corpus/archive.py tests/unit/test_corpus_archive.py
git commit -m "feat(corpus): file_list.csv parser + tarball streaming download"
```

### Task 2.8: ProcessPool parallelism helpers

**Files:**
- Create: `genereview_link/corpus/parallel.py`

- [ ] **Step 1: Implement (no test yet — covered by integration test in 2.14)**

`genereview_link/corpus/parallel.py`:

```python
"""ProcessPool + asyncio.Queue plumbing for the parse → chunk → write fan-out.

stages 4-6 of the ingest pipeline:
    tarfile stream → raw_nxml_queue → ProcessPool → record_queue → COPY writers
"""

from __future__ import annotations

import asyncio
import logging
import os
import tarfile
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from genereview_link.config import settings
from genereview_link.corpus.nxml import NxmlParseError, parse_and_chunk_one
from genereview_link.corpus.records import ChapterRecord, PassageRecord
from genereview_link.corpus.sidedata import SideData

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _RawNxml:
    nbk_id: str
    relpath: str
    raw: bytes


def _iter_tarball(path: Path) -> Iterator[_RawNxml]:
    with tarfile.open(path, "r:gz") as tf:
        for member in tf:
            if not member.isfile() or not member.name.endswith(".nxml"):
                continue
            fh = tf.extractfile(member)
            if fh is None:
                continue
            data = fh.read()
            short = Path(member.name).stem
            yield _RawNxml(nbk_id="", relpath=member.name, raw=data)
            del data
            # nbk_id is resolved later via short-name + sidedata


def _worker_parse_chunk(
    raw_bytes: bytes,
    nbk_id: str,
    short_name: str,
    nxml_relpath: str,
) -> tuple[ChapterRecord, list[PassageRecord]] | None:
    try:
        return parse_and_chunk_one(
            raw_bytes,
            nbk_id=nbk_id,
            short_name=short_name,
            nxml_relpath=nxml_relpath,
        )
    except NxmlParseError as exc:
        logger.warning("parse failed nbk=%s relpath=%s: %s", nbk_id, nxml_relpath, exc)
        return None


async def parse_pipeline(
    tarball_path: Path,
    sidedata: SideData,
    *,
    parse_workers: int | None = None,
) -> AsyncIterator[tuple[ChapterRecord, list[PassageRecord]]]:
    """Yield (chapter, passages) per chapter; per-chapter independent."""
    parse_workers = parse_workers or settings.INGEST_PARSE_WORKERS
    loop = asyncio.get_running_loop()
    nbk_by_short = {v: k for k, v in sidedata.short_name_by_nbk.items()}

    with ProcessPoolExecutor(max_workers=parse_workers) as executor:
        in_flight: list[asyncio.Future] = []
        for raw in _iter_tarball(tarball_path):
            short_name = Path(raw.relpath).stem
            nbk_id = nbk_by_short.get(short_name, "")
            if not nbk_id:
                logger.warning("no NBK id for short_name=%s; skipping", short_name)
                continue
            fut = loop.run_in_executor(
                executor,
                _worker_parse_chunk,
                raw.raw,
                nbk_id,
                short_name,
                raw.relpath,
            )
            in_flight.append(fut)
            if len(in_flight) >= parse_workers * 2:
                done = await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)
                in_flight = list(done.pending)
                for d in done.done:
                    result = await d
                    if result is None:
                        continue
                    yield result
        for fut in asyncio.as_completed(in_flight):
            result = await fut
            if result is None:
                continue
            yield result


async def copy_chapters(
    conn: asyncpg.Connection,
    chapters: list[ChapterRecord],
    *,
    corpus_version: str,
) -> None:
    records = [
        (
            c.nbk_id,
            c.short_name,
            c.title,
            c.pubmed_id,
            list(c.gene_symbols),
            list(c.omim_ids),
            c.authors,
            c.initial_pub_date,
            c.last_updated_date,
            corpus_version,
            c.nxml_relpath,
            "{}",  # raw_metadata default
        )
        for c in chapters
    ]
    await conn.copy_records_to_table(
        "genereview_chapters",
        records=records,
        columns=(
            "nbk_id",
            "short_name",
            "title",
            "pubmed_id",
            "gene_symbols",
            "omim_ids",
            "authors",
            "initial_pub_date",
            "last_updated_date",
            "corpus_version",
            "nxml_relpath",
            "raw_metadata",
        ),
    )


async def copy_passages(
    conn: asyncpg.Connection,
    passages: list[PassageRecord],
    *,
    corpus_version: str,
) -> None:
    records = [
        (
            p.nbk_id,
            p.passage_id,
            p.chapter_section,
            p.heading_path,
            p.section_level,
            p.chunk_index,
            p.text,
            p.text_hash,
            p.char_count,
            p.token_estimate,
            corpus_version,
        )
        for p in passages
    ]
    await conn.copy_records_to_table(
        "genereview_passages",
        records=records,
        columns=(
            "nbk_id",
            "passage_id",
            "chapter_section",
            "heading_path",
            "section_level",
            "chunk_index",
            "text",
            "text_hash",
            "char_count",
            "token_estimate",
            "corpus_version",
        ),
    )
```

- [ ] **Step 2: Add settings**

In `genereview_link/config.py`, add to the `Settings` class:

```python
    # Ingest parallelism
    INGEST_PARSE_WORKERS: int = 8
    INGEST_DB_WRITERS: int = 4
    INGEST_EMBED_BATCH_SIZE: int = 256
    INGEST_EMBED_WRITERS: int = 2
    INGEST_EMBED_DEVICE: str = "auto"
```

- [ ] **Step 3: Commit**

```bash
git add genereview_link/corpus/parallel.py genereview_link/config.py
git commit -m "feat(corpus): ProcessPool parse + asyncpg COPY writer helpers"
```

### Task 2.9: Pipeline orchestrator

**Files:**
- Create: `genereview_link/corpus/pipeline.py`

- [ ] **Step 1: Implement**

`genereview_link/corpus/pipeline.py`:

```python
"""Orchestrate the 9-stage ingest pipeline against an asyncpg pool.

Stages 0, 8, 9 mutate the control schema. Stages 4-6 use parallel.py.
Stage 7 (embeddings) is in retrieval/embeddings.py + ingest/orchestrator.py.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import asyncpg

from genereview_link.corpus.archive import ArchiveListing, download_tarball, fetch_listing
from genereview_link.corpus.parallel import copy_chapters, copy_passages, parse_pipeline
from genereview_link.corpus.records import ChapterRecord, PassageRecord
from genereview_link.corpus.sidedata import SideData, load_sidedata
from genereview_link.db.migrate import apply_data_migrations

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestResult:
    corpus_version: str
    chapter_count: int
    passage_count: int
    skipped_chapters: int


async def prepare_staging(pool: asyncpg.Pool) -> None:
    """Stage 0: drop and recreate the genereview_staging schema."""
    async with pool.acquire() as conn:
        await conn.execute("drop schema if exists genereview_staging cascade")
    await apply_data_migrations(pool, schema="genereview_staging")


async def record_corpus_version_start(
    pool: asyncpg.Pool, *, listing: ArchiveListing, tarball_sha256: str, size: int
) -> str:
    """Insert a new corpus_version row; return the chosen version string."""
    base = listing.last_updated.split(" ")[0]  # "2026-05-10"
    async with pool.acquire() as conn:
        # pick next free -rN suffix for same-day re-ingest
        version = base
        existing = await conn.fetchval(
            "select 1 from public.genereview_corpus_version where version = $1", version
        )
        if existing:
            n = 2
            while await conn.fetchval(
                "select 1 from public.genereview_corpus_version where version = $1",
                f"{base}-r{n}",
            ):
                n += 1
            version = f"{base}-r{n}"
        await conn.execute(
            """
            insert into public.genereview_corpus_version
                (version, file_list_etag, tarball_sha256, tarball_size_bytes,
                 ingest_started_at, ingest_status, is_active)
            values ($1, $2, $3, $4, $5, 'in_progress', false)
            """,
            version,
            listing.last_updated,
            tarball_sha256,
            size,
            datetime.now(UTC),
        )
    return version


async def atomic_swap(
    pool: asyncpg.Pool,
    *,
    new_version: str,
    chapter_count: int,
) -> None:
    """Stage 8: rename schemas + flip is_active in a single transaction."""
    async with pool.acquire() as conn, conn.transaction():
        # find any existing active version
        existing = await conn.fetchval(
            "select version from public.genereview_corpus_version where is_active"
        )
        if existing:
            target = f"genereview_old_{existing.replace('-', '_').replace('.', '_')}"
            await conn.execute(f'alter schema genereview rename to "{target}"')
        await conn.execute("alter schema genereview_staging rename to genereview")
        await conn.execute(
            "update public.genereview_corpus_version set is_active = false where is_active"
        )
        await conn.execute(
            """
            update public.genereview_corpus_version
               set is_active = true,
                   ingest_status = 'completed',
                   ingest_finished_at = $1,
                   chapter_count = $2
             where version = $3
            """,
            datetime.now(UTC),
            chapter_count,
            new_version,
        )


async def cleanup_old(pool: asyncpg.Pool, *, retain: int = 2) -> int:
    """Stage 9: drop genereview_old_* schemas beyond retention."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            select schema_name
              from information_schema.schemata
             where schema_name like 'genereview_old_%'
             order by schema_name desc
            """
        )
    dropped = 0
    if len(rows) <= retain:
        return 0
    for row in rows[retain:]:
        async with pool.acquire() as conn:
            await conn.execute(f'drop schema "{row["schema_name"]}" cascade')
        dropped += 1
    return dropped


async def run_full_ingest(
    pool: asyncpg.Pool,
    *,
    work_dir: Path | None = None,
) -> IngestResult:
    """End-to-end stages 0-9 (excluding embeddings, which run separately)."""
    listing = await fetch_listing()
    with TemporaryDirectory(dir=work_dir) as td:
        td_path = Path(td)
        tarball = td_path / "gene_NBK1116.tar.gz"
        logger.info("downloading %s …", listing.relpath)
        sha = await download_tarball(listing, dest=tarball)
        # sidedata: download the three files alongside
        sidedata_dir = td_path / "sidedata"
        sidedata_dir.mkdir()
        await _download_sidedata(sidedata_dir)
        sidedata = load_sidedata(sidedata_dir)

        await prepare_staging(pool)
        version = await record_corpus_version_start(
            pool,
            listing=listing,
            tarball_sha256=sha,
            size=tarball.stat().st_size,
        )

        chapter_count = 0
        passage_count = 0
        chapter_buf: list[ChapterRecord] = []
        passage_buf: list[PassageRecord] = []
        BATCH = 50

        async for chapter, passages in parse_pipeline(tarball, sidedata):
            # apply sidedata joins
            chapter = ChapterRecord(
                nbk_id=chapter.nbk_id,
                short_name=chapter.short_name,
                title=chapter.title,
                pubmed_id=chapter.pubmed_id,
                gene_symbols=sidedata.gene_symbols.get(chapter.nbk_id, ()),
                omim_ids=sidedata.omim_ids.get(chapter.nbk_id, ()),
                authors=chapter.authors,
                initial_pub_date=chapter.initial_pub_date,
                last_updated_date=chapter.last_updated_date,
                nxml_relpath=chapter.nxml_relpath,
                raw_metadata={},
            )
            chapter_buf.append(chapter)
            passage_buf.extend(passages)
            chapter_count += 1
            passage_count += len(passages)
            if len(chapter_buf) >= BATCH:
                await _flush(pool, chapter_buf, passage_buf, version)
                chapter_buf.clear()
                passage_buf.clear()
        if chapter_buf:
            await _flush(pool, chapter_buf, passage_buf, version)

        await atomic_swap(pool, new_version=version, chapter_count=chapter_count)
        await cleanup_old(pool)

    return IngestResult(
        corpus_version=version,
        chapter_count=chapter_count,
        passage_count=passage_count,
        skipped_chapters=0,
    )


async def _flush(
    pool: asyncpg.Pool,
    chapters: list[ChapterRecord],
    passages: list[PassageRecord],
    version: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute("set local search_path to genereview_staging, public")
        await copy_chapters(conn, chapters, corpus_version=version)
        await copy_passages(conn, passages, corpus_version=version)


async def _download_sidedata(target: Path) -> None:
    import httpx

    base = "https://ftp.ncbi.nlm.nih.gov/pub/GeneReviews"
    files = (
        "GRtitle_shortname_NBKid.txt",
        "NBKid_shortname_genesymbol.txt",
        "NBKid_shortname_OMIM.txt",
    )
    async with httpx.AsyncClient(timeout=60.0) as client:
        for name in files:
            resp = await client.get(f"{base}/{name}")
            resp.raise_for_status()
            (target / name).write_bytes(resp.content)
```

- [ ] **Step 2: Commit**

```bash
git add genereview_link/corpus/pipeline.py
git commit -m "feat(corpus): 9-stage ingest pipeline orchestrator"
```

### Task 2.10: ingest CLI subcommand

**Files:**
- Modify: `genereview_link/cli.py`

- [ ] **Step 1: Append ingest command**

In `genereview_link/cli.py`, after the `db_app` block:

```python
@app.command("ingest")
def ingest_cmd(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Download + parse only; do not write to DB."),
    ] = False,
) -> None:
    """Run the full ingest pipeline against DATABASE_URL."""
    import asyncio

    from genereview_link.corpus.pipeline import run_full_ingest
    from genereview_link.db.pool import create_pool

    async def run() -> None:
        pool = await create_pool()
        try:
            if dry_run:
                typer.echo("dry-run not yet implemented; aborting")
                raise typer.Exit(2)
            result = await run_full_ingest(pool)
            typer.echo(
                f"ingested {result.chapter_count} chapters / "
                f"{result.passage_count} passages "
                f"as corpus_version={result.corpus_version}"
            )
        finally:
            await pool.close()

    asyncio.run(run())
```

- [ ] **Step 2: Make target**

Append to `Makefile`:

```makefile
ingest: ## Run full ingest pipeline (download → parse → write → swap)
	uv run genereview-link ingest
```

- [ ] **Step 3: Commit**

```bash
git add genereview_link/cli.py Makefile
git commit -m "feat(cli): add ingest subcommand"
```

### Task 2.11: Integration test for end-to-end ingest (mini bundle)

**Files:**
- Create: `tests/fixtures/bundles/mini.tar.gz`
- Create: `tests/integration/test_ingest_end_to_end.py`

- [ ] **Step 1: Build mini tarball fixture**

```bash
mkdir -p tests/fixtures/bundles
cd tests/fixtures
mkdir -p _mini_stage/gene_NBK1116
cp nxml/typical.nxml _mini_stage/gene_NBK1116/brca1.nxml
cp nxml/multigene.nxml _mini_stage/gene_NBK1116/nf1nf2.nxml
cp nxml/missing_pubdate.nxml _mini_stage/gene_NBK1116/nopub.nxml
tar czf bundles/mini.tar.gz -C _mini_stage gene_NBK1116
rm -rf _mini_stage
cd ../..
```

- [ ] **Step 2: Write integration test**

`tests/integration/test_ingest_end_to_end.py`:

```python
"""End-to-end ingest against a mini 3-chapter tarball."""

from __future__ import annotations

from pathlib import Path

import asyncpg
import pytest

from genereview_link.corpus.pipeline import (
    atomic_swap,
    cleanup_old,
    prepare_staging,
    record_corpus_version_start,
    _download_sidedata,  # noqa: PLC2701 (intentional use)
)
from genereview_link.corpus.archive import ArchiveListing
from genereview_link.corpus.parallel import copy_chapters, copy_passages, parse_pipeline
from genereview_link.corpus.records import ChapterRecord
from genereview_link.corpus.sidedata import load_sidedata
from genereview_link.db.migrate import apply_control_migrations

FIXTURE_TARBALL = Path(__file__).parent.parent / "fixtures" / "bundles" / "mini.tar.gz"
FIXTURE_SIDEDATA = Path(__file__).parent.parent / "fixtures" / "sidedata"


@pytest.mark.asyncio
@pytest.mark.slow
async def test_full_ingest_against_mini_tarball(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await prepare_staging(pool)

    listing = ArchiveListing(
        relpath="ca/84/gene_NBK1116.tar.gz",
        title="GeneReviews",
        publisher="UW",
        initial_year="1993",
        nbk_id="NBK1116",
        last_updated="2026-05-10 03:32:37",
    )
    version = await record_corpus_version_start(
        pool, listing=listing, tarball_sha256="deadbeef" * 8, size=FIXTURE_TARBALL.stat().st_size,
    )

    sidedata = load_sidedata(FIXTURE_SIDEDATA)

    chapter_count = 0
    passage_count = 0
    async with pool.acquire() as conn:
        await conn.execute("set search_path to genereview_staging, public")
        async for chapter, passages in parse_pipeline(FIXTURE_TARBALL, sidedata, parse_workers=2):
            enriched = ChapterRecord(
                nbk_id=chapter.nbk_id,
                short_name=chapter.short_name,
                title=chapter.title,
                pubmed_id=chapter.pubmed_id,
                gene_symbols=sidedata.gene_symbols.get(chapter.nbk_id, ()),
                omim_ids=sidedata.omim_ids.get(chapter.nbk_id, ()),
                authors=chapter.authors,
                initial_pub_date=chapter.initial_pub_date,
                last_updated_date=chapter.last_updated_date,
                nxml_relpath=chapter.nxml_relpath,
                raw_metadata={},
            )
            await copy_chapters(conn, [enriched], corpus_version=version)
            await copy_passages(conn, passages, corpus_version=version)
            chapter_count += 1
            passage_count += len(passages)

    assert chapter_count >= 2

    await atomic_swap(pool, new_version=version, chapter_count=chapter_count)
    await cleanup_old(pool)

    async with pool.acquire() as conn:
        in_genereview = await conn.fetchval(
            "select count(*) from genereview.genereview_chapters"
        )
        assert in_genereview == chapter_count
```

- [ ] **Step 3: Run integration test**

```bash
docker run --rm -d --name gr-pg-test -e POSTGRES_PASSWORD=test -p 5433:5432 pgvector/pgvector:0.8.2-pg18
sleep 3
GENEREVIEW_TEST_DATABASE_URL=postgresql://postgres:test@localhost:5433/postgres \
  uv run pytest tests/integration/test_ingest_end_to_end.py -v -m slow
docker stop gr-pg-test
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/bundles/mini.tar.gz tests/integration/test_ingest_end_to_end.py
git commit -m "test(ingest): end-to-end test with 3-chapter mini tarball"
```

**Phase 2 done.** A populated `genereview` schema with ~900 chapters / ~150K passages exists after `make ingest`. No retrieval routes yet.

---

## Phase 3 — Embedding backfill

Goal: BGE-small embeddings populated into `genereview_embeddings_bge384`; HNSW index built post-COPY.

### Task 3.1: BGE embedding provider

**Files:**
- Create: `genereview_link/retrieval/__init__.py`
- Create: `genereview_link/retrieval/embeddings.py`
- Test: `tests/unit/test_retrieval_embeddings.py`

- [ ] **Step 1: Add sentence-transformers dep**

```toml
"sentence-transformers>=3.0.0",
```

`make lock && make install`.

- [ ] **Step 2: Write failing test**

`tests/unit/test_retrieval_embeddings.py`:

```python
"""Tests for the embedding provider."""

from __future__ import annotations

import pytest

from genereview_link.retrieval.embeddings import (
    FakeEmbeddingProvider,
    bge_passage_text,
    bge_query_text,
)


def test_bge_query_prefix() -> None:
    assert bge_query_text("hello").startswith("Represent this sentence")


def test_bge_passage_text_is_identity() -> None:
    assert bge_passage_text("hello") == "hello"


@pytest.mark.asyncio
async def test_fake_provider_returns_correct_dim() -> None:
    p = FakeEmbeddingProvider(dim=384)
    v = await p.embed_query("test")
    assert len(v) == 384
    vs = await p.embed_passages(["a", "b"])
    assert all(len(x) == 384 for x in vs)
```

- [ ] **Step 3: Run (should fail)**

```bash
uv run pytest tests/unit/test_retrieval_embeddings.py -v
```

- [ ] **Step 4: Implement (lift pattern from pubtator-link)**

`genereview_link/retrieval/__init__.py`:

```python
"""Retrieval layer: lexical SQL, dense embeddings, RRF rerank, repository."""
```

`genereview_link/retrieval/embeddings.py`:

```python
"""Embedding provider for BGE-small-en-v1.5.

Lifted from pubtator-link/pubtator_link/services/review_context/embeddings.py
with project renames.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Protocol, cast

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class EmbeddingProviderUnavailableError(RuntimeError):
    """Raised when optional embedding deps are not installed."""


class EmbeddingProvider(Protocol):
    model_name: str
    dim: int

    async def embed_query(self, text: str) -> list[float]: ...
    async def embed_passages(self, texts: list[str]) -> list[list[float]]: ...


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bge_query_text(text: str) -> str:
    return f"{BGE_QUERY_PREFIX}{text}"


def bge_passage_text(text: str) -> str:
    return text


class FakeEmbeddingProvider:
    """Deterministic fake — for tests."""

    def __init__(self, *, dim: int, model_name: str = "fake-embedding") -> None:
        self.model_name = model_name
        self.dim = dim

    async def embed_query(self, text: str) -> list[float]:
        return self._embed_one(bge_query_text(text))

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(bge_passage_text(t)) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        counter = 0
        while len(values) < self.dim:
            digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
            values.extend((byte / 127.5) - 1.0 for byte in digest)
            counter += 1
        return values[: self.dim]


class SentenceTransformerEmbeddingProvider:
    """Real BGE-small provider — lazy-loaded."""

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-small-en-v1.5",
        dim: int = 384,
        device: str = "auto",
    ) -> None:
        self.model_name = model_name
        self.dim = dim
        self.device = device
        self._model: Any | None = None
        self._np: Any | None = None

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._encode([bge_query_text(text)])
        return vectors[0]

    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return await self._encode([bge_passage_text(t) for t in texts])

    async def _encode(self, texts: list[str]) -> list[list[float]]:
        model, np = self._ensure_model()

        def encode() -> list[list[float]]:
            vectors = model.encode(texts, normalize_embeddings=True)
            return cast(list[list[float]], np.asarray(vectors, dtype=float).tolist())

        return await asyncio.to_thread(encode)

    def _ensure_model(self) -> tuple[Any, Any]:
        if self._model is not None and self._np is not None:
            return self._model, self._np
        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingProviderUnavailableError(
                "Install sentence-transformers + numpy to use BGE embeddings."
            ) from exc
        self._np = np
        device = None if self.device == "auto" else self.device
        self._model = SentenceTransformer(self.model_name, device=device)
        return self._model, self._np
```

- [ ] **Step 5: Run test (pass)**

```bash
uv run pytest tests/unit/test_retrieval_embeddings.py -v
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock genereview_link/retrieval/ tests/unit/test_retrieval_embeddings.py
git commit -m "feat(retrieval): BGE embedding provider (ported from pubtator-link)"
```

### Task 3.2: Embedding-backfill pipeline

**Files:**
- Create: `genereview_link/ingest/__init__.py`
- Create: `genereview_link/ingest/orchestrator.py`

- [ ] **Step 1: Implement**

`genereview_link/ingest/__init__.py`:

```python
"""Ingest orchestration (called by CLI and CI workflow)."""
```

`genereview_link/ingest/orchestrator.py`:

```python
"""Drive the embedding backfill stage with pipelined encoder + writers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import asyncpg

from genereview_link.config import settings
from genereview_link.retrieval.embeddings import (
    EmbeddingProvider,
    bge_passage_text,
    text_hash,
)

logger = logging.getLogger(__name__)


async def iter_passages_missing_embedding(
    pool: asyncpg.Pool,
    *,
    model_name: str,
    schema: str,
    batch_size: int,
) -> AsyncIterator[list[tuple[str, str, str]]]:
    """Yield batches of (nbk_id, passage_id, text) lacking an embedding row."""
    offset = 0
    while True:
        async with pool.acquire() as conn:
            await conn.execute(f'set local search_path to "{schema}", public')
            rows = await conn.fetch(
                """
                select p.nbk_id, p.passage_id, p.text
                  from genereview_passages p
                  left join genereview_embeddings_bge384 e
                    on e.nbk_id = p.nbk_id
                   and e.passage_id = p.passage_id
                   and e.model_name = $1
                 where e.passage_id is null
                 order by p.nbk_id, p.passage_id
                 limit $2 offset $3
                """,
                model_name,
                batch_size,
                offset,
            )
        if not rows:
            return
        yield [(r["nbk_id"], r["passage_id"], r["text"]) for r in rows]
        offset += batch_size


async def backfill_embeddings(
    pool: asyncpg.Pool,
    provider: EmbeddingProvider,
    *,
    schema: str = "genereview",
    batch_size: int | None = None,
    db_writers: int | None = None,
) -> int:
    """Encode and COPY embeddings for all unembedded passages in *schema*."""
    batch_size = batch_size or settings.INGEST_EMBED_BATCH_SIZE
    db_writers = db_writers or settings.INGEST_EMBED_WRITERS

    encoded_q: asyncio.Queue[list[tuple] | None] = asyncio.Queue(maxsize=2)
    total = 0

    async def encoder() -> None:
        async for batch in iter_passages_missing_embedding(
            pool, model_name=provider.model_name, schema=schema, batch_size=batch_size
        ):
            texts = [bge_passage_text(text) for _nbk, _pid, text in batch]
            vectors = await provider.embed_passages(texts)
            records = [
                (
                    nbk,
                    pid,
                    provider.model_name,
                    None,  # model_revision
                    text_hash(text),
                    vec,
                )
                for (nbk, pid, text), vec in zip(batch, vectors, strict=True)
            ]
            await encoded_q.put(records)
        for _ in range(db_writers):
            await encoded_q.put(None)

    async def writer() -> None:
        nonlocal total
        while True:
            records = await encoded_q.get()
            if records is None:
                return
            async with pool.acquire() as conn:
                await conn.execute(f'set local search_path to "{schema}", public')
                await conn.copy_records_to_table(
                    "genereview_embeddings_bge384",
                    records=records,
                    columns=(
                        "nbk_id",
                        "passage_id",
                        "model_name",
                        "model_revision",
                        "text_hash",
                        "embedding",
                    ),
                )
            total += len(records)

    await asyncio.gather(encoder(), *(writer() for _ in range(db_writers)))
    return total


async def build_hnsw_index(pool: asyncpg.Pool, *, schema: str = "genereview") -> None:
    """Build the HNSW index post-COPY."""
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            create index if not exists genereview_embeddings_bge384_hnsw_cosine
                on "{schema}".genereview_embeddings_bge384
                using hnsw (embedding vector_cosine_ops)
                with (m = 16, ef_construction = 200)
            """
        )
```

- [ ] **Step 2: Commit**

```bash
git add genereview_link/ingest/
git commit -m "feat(ingest): pipelined embedding backfill + HNSW build"
```

### Task 3.3: embed CLI subcommand

**Files:**
- Modify: `genereview_link/cli.py`

- [ ] **Step 1: Append**

```python
@app.command("embed")
def embed_cmd(
    schema: Annotated[str, typer.Option("--schema")] = "genereview",
    fake: Annotated[
        bool, typer.Option("--fake", help="Use deterministic FakeEmbeddingProvider (testing).")
    ] = False,
) -> None:
    """Backfill BGE embeddings for missing passages and build HNSW index."""
    import asyncio

    from genereview_link.db.pool import create_pool
    from genereview_link.ingest.orchestrator import backfill_embeddings, build_hnsw_index
    from genereview_link.retrieval.embeddings import (
        FakeEmbeddingProvider,
        SentenceTransformerEmbeddingProvider,
    )

    async def run() -> None:
        pool = await create_pool()
        try:
            provider = (
                FakeEmbeddingProvider(dim=384)
                if fake
                else SentenceTransformerEmbeddingProvider()
            )
            count = await backfill_embeddings(pool, provider, schema=schema)
            typer.echo(f"embedded {count} passages")
            await build_hnsw_index(pool, schema=schema)
            typer.echo("HNSW index built")
        finally:
            await pool.close()

    asyncio.run(run())
```

- [ ] **Step 2: Make target**

```makefile
embed: ## Backfill embeddings + build HNSW index
	uv run genereview-link embed
```

- [ ] **Step 3: Commit**

```bash
git add genereview_link/cli.py Makefile
git commit -m "feat(cli): add embed subcommand"
```

### Task 3.4: Integration test — HNSW absent until embed runs

**Files:**
- Create: `tests/integration/test_embedding_backfill.py`

- [ ] **Step 1: Write test**

```python
"""HNSW index must not exist before `embed` finishes."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
from genereview_link.ingest.orchestrator import backfill_embeddings, build_hnsw_index
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider


@pytest.mark.asyncio
async def test_hnsw_absent_after_migrations(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            """
            select exists (
              select 1 from pg_indexes
               where schemaname = 'genereview'
                 and indexname = 'genereview_embeddings_bge384_hnsw_cosine'
            )
            """
        )
    assert exists is False


@pytest.mark.asyncio
async def test_build_hnsw_index_creates_it(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    await build_hnsw_index(pool, schema="genereview")
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            """
            select exists (
              select 1 from pg_indexes
               where schemaname = 'genereview'
                 and indexname = 'genereview_embeddings_bge384_hnsw_cosine'
            )
            """
        )
    assert exists is True
```

- [ ] **Step 2: Run + commit**

```bash
GENEREVIEW_TEST_DATABASE_URL=… uv run pytest tests/integration/test_embedding_backfill.py -v
git add tests/integration/test_embedding_backfill.py
git commit -m "test(embed): HNSW index absent until embed runs"
```

**Phase 3 done.** `make ingest && make embed` populates a fully-indexed corpus.

---

## Phase 4 — Retrieval layer (no route changes)

I'll continue Phase 4 in a follow-up file extension to keep this manageable. The plan continues with:

### Task 4.1 — GeneReviewRepository skeleton with asyncpg pool
### Task 4.2 — Lexical helpers (_recall_terms, _recall_tsquery)
### Task 4.3 — Three-tsquery search_passages SQL
### Task 4.4 — Chapter-level fetchers (get_chapter_by_gene/nbk)
### Task 4.5 — dense_scores_for_passages
### Task 4.6 — Repository operational methods (active_corpus_version)
### Task 4.7 — SECTION_PRIORITY + rerank_key tuple
### Task 4.8 — rerank_with_embeddings (RRF + guarded sections)
### Task 4.9 — eval set + baseline runner
### Task 4.10 — Repository integration tests

**Continuation of phases 4-6 is detailed in [PLAN-PART-2.md](2026-05-11-bulk-archive-postgres-rag-part2.md).** This split is for readability; subagent-driven execution treats both files as one plan, proceeding sequentially.

---

## Execution

After Phase 1-3 land, follow the rest in PART-2. Plan complete.
