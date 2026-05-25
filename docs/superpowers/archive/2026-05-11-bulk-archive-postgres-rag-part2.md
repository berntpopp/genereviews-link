# Bulk Archive Ingest + Postgres RAG Retrieval — Implementation Plan (Part 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. This file continues [2026-05-11-bulk-archive-postgres-rag.md](2026-05-11-bulk-archive-postgres-rag.md). All Phase 1-3 tasks must be complete before starting here.

---

## Phase 4 — Retrieval layer

Goal: `GeneReviewRepository` exists and is unit/integration tested. No route changes yet.

### Task 4.1: Repository skeleton + pool integration

**Files:**
- Create: `genereview_link/retrieval/repository.py`
- Test: `tests/integration/test_repository_smoke.py`

- [ ] **Step 1: Write smoke test**

```python
"""Smoke test: repository can be instantiated against a pool."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
from genereview_link.retrieval.repository import GeneReviewRepository


@pytest.mark.asyncio
async def test_active_corpus_version_when_none(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    repo = GeneReviewRepository(pool)
    cv = await repo.active_corpus_version()
    assert cv is None


@pytest.mark.asyncio
async def test_active_embedding_table_returns_default(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    repo = GeneReviewRepository(pool)
    table = await repo.active_embedding_table()
    assert table == "genereview_embeddings_bge384"
```

- [ ] **Step 2: Implement skeleton**

`genereview_link/retrieval/repository.py`:

```python
"""GeneReviewRepository — asyncpg-backed reads for the API layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import asyncpg

from genereview_link.config import settings


@dataclass(frozen=True, slots=True)
class CorpusVersionRow:
    version: str
    is_active: bool
    ingest_status: str
    ingest_finished_at: datetime | None
    chapter_count: int | None


@dataclass(frozen=True, slots=True)
class ChapterRow:
    nbk_id: str
    short_name: str
    title: str
    pubmed_id: str | None
    gene_symbols: tuple[str, ...]
    omim_ids: tuple[str, ...]
    authors: str | None
    initial_pub_date: date | None
    last_updated_date: date | None


@dataclass(frozen=True, slots=True)
class PassageRow:
    nbk_id: str
    passage_id: str
    chapter_section: str
    heading_path: str | None
    section_level: int
    chunk_index: int
    text: str


@dataclass(frozen=True, slots=True)
class LexicalPassageRow:
    """A passage with its lexical scores attached."""

    passage: PassageRow
    phrase_rank: float
    strict_rank: float
    recall_rank: float
    recall_overlap_count: int
    lexical_rank: float
    gene_symbols: tuple[str, ...]


class GeneReviewRepository:
    """Read-mostly facade over Postgres."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        acquire_timeout_s: float | None = None,
    ) -> None:
        self._pool = pool
        self._acquire_timeout_s = (
            acquire_timeout_s
            if acquire_timeout_s is not None
            else settings.DATABASE_ACQUIRE_TIMEOUT_S
        )

    def _acquire(self):
        return self._pool.acquire(timeout=self._acquire_timeout_s)

    # ---- operational ----
    async def active_corpus_version(self) -> CorpusVersionRow | None:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                """
                select version, is_active, ingest_status, ingest_finished_at, chapter_count
                  from public.genereview_corpus_version
                 where is_active
                """
            )
        if row is None:
            return None
        return CorpusVersionRow(
            version=row["version"],
            is_active=row["is_active"],
            ingest_status=row["ingest_status"],
            ingest_finished_at=row["ingest_finished_at"],
            chapter_count=row["chapter_count"],
        )

    async def active_embedding_table(self) -> str:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                "select table_name from public.genereview_active_embedding where id = 1"
            )
        return row["table_name"] if row else "genereview_embeddings_bge384"
```

- [ ] **Step 3: Run + commit**

```bash
GENEREVIEW_TEST_DATABASE_URL=… uv run pytest tests/integration/test_repository_smoke.py -v
git add genereview_link/retrieval/repository.py tests/integration/test_repository_smoke.py
git commit -m "feat(retrieval): GeneReviewRepository skeleton + operational reads"
```

### Task 4.2: Lexical helper functions

**Files:**
- Create: `genereview_link/retrieval/lexical.py`
- Test: `tests/unit/test_retrieval_lexical_helpers.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for _recall_terms and _recall_tsquery."""

from __future__ import annotations

from genereview_link.retrieval.lexical import recall_terms, recall_tsquery


def test_recall_terms_lowers_and_dedupes() -> None:
    out = recall_terms("BRCA1 BRCA1 tumor SUPPRESSOR")
    assert "brca1" in out
    assert out.count("brca1") == 1
    assert "tumor" in out


def test_recall_terms_drops_short_tokens() -> None:
    out = recall_terms("a is the cat")
    assert "a" not in out
    assert "is" not in out
    assert "the" not in out
    assert "cat" in out


def test_recall_tsquery_joins_with_or() -> None:
    q = recall_tsquery("BRCA1 tumor")
    assert "|" in q
    assert "brca1" in q.lower()


def test_recall_tsquery_empty_returns_safe_string() -> None:
    q = recall_tsquery("")
    assert q  # safe, parseable
```

- [ ] **Step 2: Implement**

`genereview_link/retrieval/lexical.py`:

```python
"""Helpers for the three-tsquery lexical search.

Ported from pubtator-link with renames.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def recall_terms(query: str) -> list[str]:
    """Extract distinct 3+-char lowercased tokens from *query*."""
    tokens = (m.group(0).lower() for m in _TOKEN_RE.finditer(query))
    out: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        if len(tok) < 3:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def recall_tsquery(query: str) -> str:
    """Build an OR-joined to_tsquery from *query* tokens."""
    terms = recall_terms(query)
    if not terms:
        return "x:*"  # safe, matches nothing meaningful but parses
    return " | ".join(terms)
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/unit/test_retrieval_lexical_helpers.py -v
git add genereview_link/retrieval/lexical.py tests/unit/test_retrieval_lexical_helpers.py
git commit -m "feat(retrieval): lexical helper functions (recall_terms, recall_tsquery)"
```

### Task 4.3: search_passages SQL (three-tsquery) on repository

**Files:**
- Modify: `genereview_link/retrieval/repository.py`
- Test: `tests/integration/test_repository_lexical.py`

- [ ] **Step 1: Write integration test**

```python
"""Tests for repository.search_passages."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
from genereview_link.retrieval.repository import GeneReviewRepository


async def _seed(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("set search_path to genereview, public")
        await conn.execute(
            """
            insert into genereview_chapters
                (nbk_id, short_name, title, gene_symbols, corpus_version, nxml_relpath)
            values ('NBK1', 'brca', 'BRCA Chapter', '{BRCA1}', '2026-05-10', 'x.nxml')
            """
        )
        await conn.execute(
            """
            insert into genereview_passages
                (nbk_id, passage_id, chapter_section, heading_path, section_level,
                 chunk_index, text, text_hash, char_count, token_estimate, corpus_version)
            values
                ('NBK1', 'NBK1:0001', 'summary', 'Summary', 1, 0,
                 'BRCA1 is a tumor suppressor gene involved in DNA repair.',
                 'h1', 56, 12, '2026-05-10'),
                ('NBK1', 'NBK1:0002', 'management', 'Management', 1, 0,
                 'Risk-reducing surgery is offered to carriers.',
                 'h2', 45, 9, '2026-05-10')
            """
        )


@pytest.mark.asyncio
async def test_phrase_match_outranks_recall_match(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    results = await repo.search_passages("tumor suppressor")
    assert results
    assert results[0].passage.passage_id == "NBK1:0001"


@pytest.mark.asyncio
async def test_section_filter(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    results = await repo.search_passages("BRCA1", sections=["management"])
    assert all(r.passage.chapter_section == "management" for r in results)
```

- [ ] **Step 2: Add search_passages to repository**

In `genereview_link/retrieval/repository.py`, add (after `active_embedding_table`):

```python
    async def search_passages(
        self,
        query: str,
        *,
        gene_symbol: str | None = None,
        nbk_id: str | None = None,
        sections: list[str] | None = None,
        limit: int = 20,
    ) -> list[LexicalPassageRow]:
        """Run the three-tsquery hybrid lexical search."""
        from genereview_link.retrieval.lexical import recall_terms, recall_tsquery

        recall_query = recall_tsquery(query)
        terms = recall_terms(query)
        sections_param = sections if sections else None

        async with self._acquire() as conn:
            await conn.execute("set local search_path to genereview, public")
            rows = await conn.fetch(
                """
                with q as (
                    select
                        phraseto_tsquery('english', $2) as phrase_query,
                        websearch_to_tsquery('english', $2) as strict_query,
                        to_tsquery('english', $7) as recall_query
                ),
                cand as (
                    select
                        p.nbk_id, p.passage_id, p.chapter_section, p.heading_path,
                        p.section_level, p.chunk_index, p.text,
                        c.gene_symbols,
                        ts_rank_cd(p.search_vector, q.phrase_query) as phrase_rank,
                        ts_rank_cd(p.search_vector, q.strict_query) as strict_rank,
                        ts_rank_cd(p.search_vector, q.recall_query) as recall_rank,
                        (
                            select count(*)
                              from (
                                  select distinct token
                                    from regexp_split_to_table(lower(p.text), '[^a-zA-Z0-9]+') as token
                                   where length(token) >= 3
                              ) pt
                             where pt.token = any($8::text[])
                        ) as recall_overlap_count
                      from genereview_passages p
                      join genereview_chapters c on c.nbk_id = p.nbk_id, q
                     where (
                              p.search_vector @@ q.phrase_query
                           or p.search_vector @@ q.strict_query
                           or p.search_vector @@ q.recall_query
                          )
                       and ($3::text is null or $3 = any(c.gene_symbols))
                       and ($4::text is null or p.nbk_id = $4)
                       and ($5::text[] is null or p.chapter_section = any($5::text[]))
                )
                select
                    nbk_id, passage_id, chapter_section, heading_path, section_level,
                    chunk_index, text, gene_symbols,
                    phrase_rank, strict_rank, recall_rank, recall_overlap_count,
                    (phrase_rank * 3.0 + strict_rank * 2.0 + recall_rank)
                      * case
                          when phrase_rank = 0 and strict_rank = 0 and recall_rank > 0
                            and array_length(regexp_split_to_array($2, E'\\s+'), 1) >= 4
                            and recall_overlap_count <= 1
                          then least(1.0, greatest(0.25, char_length(text)::double precision / 400.0))
                          else 1.0
                        end as lexical_rank
                  from cand
                 order by lexical_rank desc, nbk_id, passage_id
                 limit $6
                """,
                "ignored",
                query,
                gene_symbol,
                nbk_id,
                sections_param,
                limit,
                recall_query,
                terms,
            )

        return [
            LexicalPassageRow(
                passage=PassageRow(
                    nbk_id=r["nbk_id"],
                    passage_id=r["passage_id"],
                    chapter_section=r["chapter_section"],
                    heading_path=r["heading_path"],
                    section_level=r["section_level"],
                    chunk_index=r["chunk_index"],
                    text=r["text"],
                ),
                phrase_rank=float(r["phrase_rank"]),
                strict_rank=float(r["strict_rank"]),
                recall_rank=float(r["recall_rank"]),
                recall_overlap_count=int(r["recall_overlap_count"]),
                lexical_rank=float(r["lexical_rank"]),
                gene_symbols=tuple(r["gene_symbols"] or ()),
            )
            for r in rows
        ]
```

- [ ] **Step 3: Run + commit**

```bash
GENEREVIEW_TEST_DATABASE_URL=… uv run pytest tests/integration/test_repository_lexical.py -v
git add genereview_link/retrieval/repository.py tests/integration/test_repository_lexical.py
git commit -m "feat(retrieval): three-tsquery search_passages with weak-recall penalty"
```

### Task 4.4: Chapter fetchers + get_section

**Files:**
- Modify: `genereview_link/retrieval/repository.py`
- Test: `tests/integration/test_repository_chapters.py`

- [ ] **Step 1: Write tests**

```python
"""Repository chapter and section fetcher tests."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
from genereview_link.retrieval.repository import GeneReviewRepository


async def _seed(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("set search_path to genereview, public")
        await conn.execute(
            """
            insert into genereview_chapters
                (nbk_id, short_name, title, gene_symbols, omim_ids, corpus_version, nxml_relpath, pubmed_id)
            values ('NBK1', 'brca', 'BRCA Chapter', '{BRCA1,BRCA2}', '{113705}', '2026-05-10', 'x', '12345')
            """
        )
        await conn.execute(
            """
            insert into genereview_passages
                (nbk_id, passage_id, chapter_section, chunk_index, text, text_hash,
                 char_count, token_estimate, corpus_version)
            values
                ('NBK1', 'NBK1:0001', 'summary', 0, 'Summary text', 'h', 12, 3, '2026-05-10'),
                ('NBK1', 'NBK1:0002', 'summary', 1, 'More summary', 'h', 12, 3, '2026-05-10'),
                ('NBK1', 'NBK1:0003', 'diagnosis', 0, 'Diagnosis text', 'h', 14, 3, '2026-05-10')
            """
        )


@pytest.mark.asyncio
async def test_get_chapter_by_gene(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    chapter = await repo.get_chapter_by_gene("BRCA1")
    assert chapter is not None
    assert chapter.nbk_id == "NBK1"
    assert "BRCA2" in chapter.gene_symbols


@pytest.mark.asyncio
async def test_get_section_returns_ordered(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    passages = await repo.get_section("NBK1", "summary")
    assert [p.chunk_index for p in passages] == [0, 1]


@pytest.mark.asyncio
async def test_get_chapter_by_pmid(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    await _seed(pool)
    repo = GeneReviewRepository(pool)
    chapter = await repo.get_chapter_by_pmid("12345")
    assert chapter is not None
    assert chapter.nbk_id == "NBK1"
```

- [ ] **Step 2: Implement methods**

Append to `GeneReviewRepository`:

```python
    async def get_chapter_by_gene(self, gene_symbol: str) -> ChapterRow | None:
        async with self._acquire() as conn:
            await conn.execute("set local search_path to genereview, public")
            row = await conn.fetchrow(
                """
                select nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
                       authors, initial_pub_date, last_updated_date
                  from genereview_chapters
                 where $1 = any(gene_symbols)
                 order by last_updated_date desc nulls last
                 limit 1
                """,
                gene_symbol,
            )
        return _to_chapter_row(row) if row else None

    async def get_chapter_by_nbk(self, nbk_id: str) -> ChapterRow | None:
        async with self._acquire() as conn:
            await conn.execute("set local search_path to genereview, public")
            row = await conn.fetchrow(
                """
                select nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
                       authors, initial_pub_date, last_updated_date
                  from genereview_chapters
                 where nbk_id = $1
                """,
                nbk_id,
            )
        return _to_chapter_row(row) if row else None

    async def get_chapter_by_pmid(self, pmid: str) -> ChapterRow | None:
        async with self._acquire() as conn:
            await conn.execute("set local search_path to genereview, public")
            row = await conn.fetchrow(
                """
                select nbk_id, short_name, title, pubmed_id, gene_symbols, omim_ids,
                       authors, initial_pub_date, last_updated_date
                  from genereview_chapters
                 where pubmed_id = $1
                """,
                pmid,
            )
        return _to_chapter_row(row) if row else None

    async def get_section(self, nbk_id: str, chapter_section: str) -> list[PassageRow]:
        async with self._acquire() as conn:
            await conn.execute("set local search_path to genereview, public")
            rows = await conn.fetch(
                """
                select nbk_id, passage_id, chapter_section, heading_path,
                       section_level, chunk_index, text
                  from genereview_passages
                 where nbk_id = $1 and chapter_section = $2
                 order by chunk_index
                """,
                nbk_id,
                chapter_section,
            )
        return [
            PassageRow(
                nbk_id=r["nbk_id"],
                passage_id=r["passage_id"],
                chapter_section=r["chapter_section"],
                heading_path=r["heading_path"],
                section_level=r["section_level"],
                chunk_index=r["chunk_index"],
                text=r["text"],
            )
            for r in rows
        ]


def _to_chapter_row(row: asyncpg.Record) -> ChapterRow:
    return ChapterRow(
        nbk_id=row["nbk_id"],
        short_name=row["short_name"],
        title=row["title"],
        pubmed_id=row["pubmed_id"],
        gene_symbols=tuple(row["gene_symbols"] or ()),
        omim_ids=tuple(row["omim_ids"] or ()),
        authors=row["authors"],
        initial_pub_date=row["initial_pub_date"],
        last_updated_date=row["last_updated_date"],
    )
```

- [ ] **Step 3: Run + commit**

```bash
GENEREVIEW_TEST_DATABASE_URL=… uv run pytest tests/integration/test_repository_chapters.py -v
git add genereview_link/retrieval/repository.py tests/integration/test_repository_chapters.py
git commit -m "feat(retrieval): chapter fetchers (by gene/nbk/pmid) + get_section"
```

### Task 4.5: dense_scores_for_passages

**Files:**
- Modify: `genereview_link/retrieval/repository.py`
- Test: `tests/integration/test_repository_dense.py`

- [ ] **Step 1: Write test**

```python
"""Dense score retrieval test."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations
from genereview_link.ingest.orchestrator import backfill_embeddings
from genereview_link.retrieval.embeddings import FakeEmbeddingProvider
from genereview_link.retrieval.repository import GeneReviewRepository


@pytest.mark.asyncio
async def test_dense_scores_returns_cosine_in_range(pool: asyncpg.Pool) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    async with pool.acquire() as conn:
        await conn.execute("set search_path to genereview, public")
        await conn.execute(
            """
            insert into genereview_chapters (nbk_id, short_name, title, corpus_version, nxml_relpath)
            values ('NBK1', 'x', 'T', '2026', 'r')
            """
        )
        await conn.execute(
            """
            insert into genereview_passages
                (nbk_id, passage_id, chapter_section, chunk_index, text, text_hash,
                 char_count, token_estimate, corpus_version)
            values
                ('NBK1', 'NBK1:0001', 'summary', 0, 'Hello world.', 'h', 12, 3, '2026'),
                ('NBK1', 'NBK1:0002', 'summary', 1, 'Different text.', 'h', 15, 3, '2026')
            """
        )

    provider = FakeEmbeddingProvider(dim=384)
    await backfill_embeddings(pool, provider, schema="genereview")

    repo = GeneReviewRepository(pool)
    qv = await provider.embed_query("hello")
    scores = await repo.dense_scores_for_passages(
        qv, [("NBK1", "NBK1:0001"), ("NBK1", "NBK1:0002")], model_table="genereview_embeddings_bge384",
    )
    assert set(scores.keys()) == {"NBK1:0001", "NBK1:0002"}
    for v in scores.values():
        assert -1.001 <= v <= 1.001
```

- [ ] **Step 2: Implement**

```python
    async def dense_scores_for_passages(
        self,
        query_vector: list[float],
        passage_ids: list[tuple[str, str]],
        *,
        model_table: str,
    ) -> dict[str, float]:
        if not passage_ids:
            return {}
        nbks = [n for n, _ in passage_ids]
        pids = [p for _, p in passage_ids]
        async with self._acquire() as conn:
            await conn.execute("set local search_path to genereview, public")
            rows = await conn.fetch(
                f"""
                select passage_id, 1 - (embedding <=> $1::vector) as score
                  from "{model_table}"
                 where (nbk_id, passage_id) in (
                     select unnest($2::text[]), unnest($3::text[])
                 )
                """,
                query_vector,
                nbks,
                pids,
            )
        return {r["passage_id"]: float(r["score"]) for r in rows}
```

- [ ] **Step 3: Run + commit**

```bash
GENEREVIEW_TEST_DATABASE_URL=… uv run pytest tests/integration/test_repository_dense.py -v
git add genereview_link/retrieval/repository.py tests/integration/test_repository_dense.py
git commit -m "feat(retrieval): dense_scores_for_passages with cosine similarity"
```

### Task 4.6: RRF + section_priority rerank

**Files:**
- Create: `genereview_link/retrieval/rerank.py`
- Test: `tests/unit/test_retrieval_rerank.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for RRF + section_priority rerank."""

from __future__ import annotations

from genereview_link.retrieval.repository import LexicalPassageRow, PassageRow
from genereview_link.retrieval.rerank import (
    SECTION_PRIORITY,
    rerank_with_embeddings,
)


def _row(passage_id: str, section: str, lexical_rank: float = 1.0) -> LexicalPassageRow:
    return LexicalPassageRow(
        passage=PassageRow(
            nbk_id="NBK1",
            passage_id=passage_id,
            chapter_section=section,
            heading_path=section.title(),
            section_level=1,
            chunk_index=0,
            text=f"text for {passage_id}",
        ),
        phrase_rank=lexical_rank,
        strict_rank=0.0,
        recall_rank=0.0,
        recall_overlap_count=1,
        lexical_rank=lexical_rank,
        gene_symbols=(),
    )


def test_section_priority_orders_ties() -> None:
    rows = [_row("a", "references", 1.0), _row("b", "summary", 1.0)]
    out, diag = rerank_with_embeddings(rows, dense_scores={}, rrf_k=60)
    # references is guarded — appended last
    assert out[0].passage.passage_id == "b"
    assert out[-1].passage.passage_id == "a"


def test_rrf_combines_lexical_and_dense() -> None:
    rows = [_row("a", "summary", 1.0), _row("b", "summary", 0.5)]
    # dense flips the order
    dense = {"a": 0.1, "b": 0.9}
    out, diag = rerank_with_embeddings(rows, dense_scores=dense, rrf_k=60)
    assert out[0].passage.passage_id == "b"
    assert diag.strategy == "lexical_top_k_dense_rrf"


def test_section_priority_constants() -> None:
    assert SECTION_PRIORITY["summary"] == 0
    assert SECTION_PRIORITY["references"] == 50
```

- [ ] **Step 2: Implement**

`genereview_link/retrieval/rerank.py`:

```python
"""RRF + section_priority reranker.

Ported from pubtator-link/services/review_context/{ranking,embedding_rerank}.py
with the simplification that there's only one source (FTP archive), so
source_priority is gone.

Sort key (tuple, descending RRF, then ascending priorities):
    (-rrf_score, SECTION_PRIORITY[section], nbk_id, passage_id)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from genereview_link.retrieval.repository import LexicalPassageRow

SECTION_PRIORITY: Mapping[str, int] = {
    "summary": 0,
    "diagnosis": 0,
    "clinical_features": 1,
    "management": 1,
    "genetic_counseling": 2,
    "molecular_genetics": 2,
    "resources": 5,
    "other": 7,
    "references": 50,
}

GUARDED_SECTIONS = frozenset({"references"})
RRF_STRATEGY = "lexical_top_k_dense_rrf"


@dataclass(slots=True)
class RerankDiagnostics:
    enabled: bool = True
    active: bool = False
    candidate_count: int = 0
    embedded_candidate_count: int = 0
    missing_embedding_count: int = 0
    strategy: str | None = None
    fallback_reason: str | None = None


def _section_key(row: LexicalPassageRow) -> int:
    return SECTION_PRIORITY.get(row.passage.chapter_section, 100)


def _is_guarded(row: LexicalPassageRow) -> bool:
    return row.passage.chapter_section in GUARDED_SECTIONS


def _rerank_key(row: LexicalPassageRow) -> tuple[float, int, str, str]:
    return (-row.lexical_rank, _section_key(row), row.passage.nbk_id, row.passage.passage_id)


def rerank_with_embeddings(
    rows: Sequence[LexicalPassageRow],
    dense_scores: Mapping[str, float],
    *,
    rrf_k: int = 60,
) -> tuple[list[LexicalPassageRow], RerankDiagnostics]:
    """Rank lexical candidates with RRF; section_priority is a tiebreaker."""
    diag = RerankDiagnostics(
        candidate_count=len(rows),
        embedded_candidate_count=sum(1 for r in rows if r.passage.passage_id in dense_scores),
    )
    diag.missing_embedding_count = diag.candidate_count - diag.embedded_candidate_count

    if not rows:
        diag.fallback_reason = "no_candidates"
        return [], diag

    lex_sorted = sorted(rows, key=_rerank_key)

    if not dense_scores:
        diag.fallback_reason = "no_dense_scores"
        return lex_sorted, diag

    evidence = [r for r in lex_sorted if not _is_guarded(r)]
    guarded = [r for r in lex_sorted if _is_guarded(r)]
    if not evidence:
        diag.fallback_reason = "no_evidence_candidates"
        return guarded, diag

    diag.active = True
    diag.strategy = RRF_STRATEGY

    lex_rank = {r.passage.passage_id: i + 1 for i, r in enumerate(lex_sorted)}
    dense_sorted = sorted(
        (r for r in evidence if r.passage.passage_id in dense_scores),
        key=lambda r: (-dense_scores[r.passage.passage_id], _rerank_key(r)),
    )
    dense_rank = {r.passage.passage_id: i + 1 for i, r in enumerate(dense_sorted)}

    def rrf(r: LexicalPassageRow) -> float:
        score = 1.0 / (rrf_k + lex_rank[r.passage.passage_id])
        if r.passage.passage_id in dense_rank:
            score += 1.0 / (rrf_k + dense_rank[r.passage.passage_id])
        return score

    final_evidence = sorted(
        evidence,
        key=lambda r: (-rrf(r), _section_key(r), r.passage.nbk_id, r.passage.passage_id),
    )
    return final_evidence + guarded, diag
```

- [ ] **Step 3: Run + commit**

```bash
uv run pytest tests/unit/test_retrieval_rerank.py -v
git add genereview_link/retrieval/rerank.py tests/unit/test_retrieval_rerank.py
git commit -m "feat(retrieval): RRF reranker with section_priority tiebreaker"
```

### Task 4.7: Eval set scaffold

**Files:**
- Create: `tests/eval/__init__.py`
- Create: `tests/eval/genereviews_queries.jsonl`
- Create: `tests/eval/baseline.json` (empty starter)
- Create: `tests/eval/run_eval.py`

- [ ] **Step 1: Curate initial queries**

`tests/eval/genereviews_queries.jsonl`:

```jsonl
{"query": "BRCA1 tumor suppressor function", "expected_chapter": "NBK1247", "expected_section": "summary"}
{"query": "BRCA1 risk-reducing surgery", "expected_chapter": "NBK1247", "expected_section": "management"}
{"query": "Huntington trinucleotide repeat", "expected_chapter": "NBK1305", "expected_section": "molecular_genetics"}
{"query": "Huntington genetic counseling", "expected_chapter": "NBK1305", "expected_section": "genetic_counseling"}
{"query": "cystic fibrosis CFTR mutations", "expected_chapter": "NBK1250", "expected_section": "molecular_genetics"}
```

(Initial 5 — expand to 30 during Phase 5 implementation; spec mandates ~30.)

- [ ] **Step 2: Stub runner**

`tests/eval/run_eval.py`:

```python
"""Compute MRR@10 and section-precision@5 for the eval set."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from genereview_link.db.pool import create_pool
from genereview_link.retrieval.repository import GeneReviewRepository

QUERIES = Path(__file__).parent / "genereviews_queries.jsonl"
BASELINE = Path(__file__).parent / "baseline.json"


async def run() -> dict[str, float]:
    pool = await create_pool()
    repo = GeneReviewRepository(pool)
    try:
        total = 0
        mrr_sum = 0.0
        section_hits = 0
        for line in QUERIES.read_text().splitlines():
            if not line.strip():
                continue
            q = json.loads(line)
            results = await repo.search_passages(q["query"], limit=10)
            total += 1
            for i, r in enumerate(results, start=1):
                if r.passage.nbk_id == q["expected_chapter"]:
                    mrr_sum += 1.0 / i
                    break
            top5 = results[:5]
            if any(r.passage.chapter_section == q["expected_section"] for r in top5):
                section_hits += 1
        return {
            "mrr_at_10": mrr_sum / max(total, 1),
            "section_precision_at_5": section_hits / max(total, 1),
            "queries_run": total,
        }
    finally:
        await pool.close()


if __name__ == "__main__":
    metrics = asyncio.run(run())
    print(json.dumps(metrics, indent=2))
    if BASELINE.exists():
        baseline = json.loads(BASELINE.read_text())
        for k in ("mrr_at_10", "section_precision_at_5"):
            delta = metrics[k] - baseline.get(k, 0.0)
            if delta < -0.05:
                print(f"REGRESSION: {k} dropped by {-delta:.3f}")
                sys.exit(1)
```

`tests/eval/baseline.json`:

```json
{
  "mrr_at_10": 0.0,
  "section_precision_at_5": 0.0,
  "queries_run": 0,
  "captured_against_corpus_version": "TBD-on-first-run"
}
```

- [ ] **Step 3: Make targets**

```makefile
eval: ## Run MRR@10 / section-precision@5 against tests/eval/
	uv run python -m tests.eval.run_eval

eval-baseline: ## Re-capture baseline.json (requires explicit operator confirmation)
	@echo "Refusing — edit tests/eval/baseline.json by hand or via a tracked PR."
	@exit 1
```

- [ ] **Step 4: Commit**

```bash
git add tests/eval/ Makefile
git commit -m "feat(eval): initial query set + runner + baseline.json scaffold"
```

**Phase 4 done.** Repository fully functional; eval scaffolding in place; no route changes yet.

---

## Phase 5 — Route migration

Goal: Existing routes serve index-backed data; `?fresh=true` reaches EutilsClient; new `/passages/search` + `/chapters/.../sections/...` + `/debug/ranking` routes live; MCP tools updated.

### Task 5.1: New Pydantic models + additive fields

**Files:**
- Modify: `genereview_link/models/genereview_models.py`

- [ ] **Step 1: Read existing models**

```bash
sed -n '1,150p' genereview_link/models/genereview_models.py
```

- [ ] **Step 2: Append new models**

```python
class CorpusVersion(BaseModel):
    version: str
    last_updated: datetime | None = None
    is_active: bool


class LicenseNotice(BaseModel):
    copyright: str = "(c) 1993-2026 University of Washington"
    terms_url: str = "https://www.ncbi.nlm.nih.gov/books/NBK138602/"


class ScoreBreakdown(BaseModel):
    lexical_rank: float
    phrase_rank: float
    strict_rank: float
    recall_rank: float
    dense_score: float | None = None
    dense_rank: int | None = None
    rrf_score: float | None = None
    section_priority: int
    final_position: int


class RankedPassage(BaseModel):
    passage_id: str
    nbk_id: str
    gene_symbols: list[str] = []
    chapter_section: str
    heading_path: str | None = None
    text: str
    char_count: int
    score_breakdown: ScoreBreakdown
```

Add `corpus_version: str | None = None` and `license: LicenseNotice | None = None` to each existing model (`GeneReview`, `FullTextData`, `AbstractData`, `LinkData`, `SearchResult`). Confirm via grep:

```bash
grep -n "class GeneReview" genereview_link/models/genereview_models.py
```

- [ ] **Step 3: Commit**

```bash
git add genereview_link/models/genereview_models.py
git commit -m "feat(models): add CorpusVersion, LicenseNotice, RankedPassage, ScoreBreakdown"
```

### Task 5.2: NotYetIndexedError + exception handler

**Files:**
- Create: `genereview_link/services/errors.py`
- Modify: `genereview_link/api/__init__.py` (or wherever the FastAPI app is built)

- [ ] **Step 1: Create**

```python
"""Domain exceptions."""

from __future__ import annotations


class NotYetIndexedError(Exception):
    """Raised when a chapter/gene isn't in the active corpus and fresh=False."""

    def __init__(self, *, gene_symbol: str | None = None, nbk_id: str | None = None,
                 pubmed_id: str | None = None, corpus_version: str | None = None) -> None:
        super().__init__("not_yet_indexed")
        self.gene_symbol = gene_symbol
        self.nbk_id = nbk_id
        self.pubmed_id = pubmed_id
        self.corpus_version = corpus_version
```

- [ ] **Step 2: Wire exception handler**

In the FastAPI app builder (search for `FastAPI(` in `genereview_link/`):

```python
from fastapi.responses import JSONResponse
from genereview_link.services.errors import NotYetIndexedError

@app.exception_handler(NotYetIndexedError)
async def not_yet_indexed_handler(_request, exc: NotYetIndexedError):
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_yet_indexed",
            "gene_symbol": exc.gene_symbol,
            "nbk_id": exc.nbk_id,
            "pubmed_id": exc.pubmed_id,
            "corpus_version": exc.corpus_version,
            "hint": "Pass ?fresh=true to fetch from NCBI live",
        },
    )
```

- [ ] **Step 3: Commit**

```bash
git add genereview_link/services/errors.py genereview_link/api/
git commit -m "feat(errors): NotYetIndexedError + structured 404 handler"
```

### Tasks 5.3-5.8: Route updates (search / abstract / links / fulltext / genereview)

Each existing route file follows the same pattern. Template:

```python
@router.get("/<path>/{key}", response_model=ResponseModel)
async def handler(
    key: str,
    fresh: bool = Query(False, alias="fresh"),
    service: GeneReviewService = Depends(get_service),
):
    if fresh:
        result = await service.get_via_eutils(key)
        result.corpus_version = f"live:{datetime.now(UTC).isoformat()}"
    else:
        try:
            result = await service.get_via_repository(key)
        except NotYetIndexedError:
            raise
    result.license = LicenseNotice()
    return result
```

For each task: write a route test asserting both index-backed and `?fresh=true` paths return correct shapes; modify the route; commit. Per-route detail follows the pattern; agent should adapt to each route's existing shape.

### Task 5.9: New /passages/search route

**Files:**
- Create: `genereview_link/api/routes/passages.py`
- Test: `tests/test_routes_passages.py`

- [ ] **Step 1: Implement**

```python
"""GET /passages/search — RAG-shaped retrieval."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from genereview_link.models.genereview_models import RankedPassage, ScoreBreakdown
from genereview_link.retrieval.embeddings import (
    EmbeddingProvider,
    bge_query_text,
)
from genereview_link.retrieval.repository import GeneReviewRepository
from genereview_link.retrieval.rerank import (
    SECTION_PRIORITY,
    rerank_with_embeddings,
)

router = APIRouter()


async def get_repository() -> GeneReviewRepository: ...  # wired in app factory
async def get_embedding_provider() -> EmbeddingProvider: ...


@router.get("/passages/search", response_model=list[RankedPassage])
async def search_passages(
    q: Annotated[str, Query(min_length=1, max_length=500)],
    gene: Annotated[str | None, Query()] = None,
    nbk: Annotated[str | None, Query()] = None,
    sections: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    rerank: Annotated[Literal["rrf", "lexical", "off"], Query()] = "rrf",
    repo: GeneReviewRepository = Depends(get_repository),
    embedder: EmbeddingProvider = Depends(get_embedding_provider),
) -> list[RankedPassage]:
    lex = await repo.search_passages(
        q, gene_symbol=gene, nbk_id=nbk, sections=sections, limit=max(limit * 3, 50),
    )
    dense_scores: dict[str, float] = {}
    if rerank == "rrf":
        qv = await embedder.embed_query(q)
        active_table = await repo.active_embedding_table()
        dense_scores = await repo.dense_scores_for_passages(
            qv,
            [(r.passage.nbk_id, r.passage.passage_id) for r in lex],
            model_table=active_table,
        )
    ranked, _diag = rerank_with_embeddings(lex, dense_scores)
    ranked = ranked[:limit]

    out: list[RankedPassage] = []
    for pos, r in enumerate(ranked, start=1):
        out.append(
            RankedPassage(
                passage_id=r.passage.passage_id,
                nbk_id=r.passage.nbk_id,
                gene_symbols=list(r.gene_symbols),
                chapter_section=r.passage.chapter_section,
                heading_path=r.passage.heading_path,
                text=r.passage.text,
                char_count=len(r.passage.text),
                score_breakdown=ScoreBreakdown(
                    lexical_rank=r.lexical_rank,
                    phrase_rank=r.phrase_rank,
                    strict_rank=r.strict_rank,
                    recall_rank=r.recall_rank,
                    dense_score=dense_scores.get(r.passage.passage_id),
                    dense_rank=None,
                    rrf_score=None,
                    section_priority=SECTION_PRIORITY.get(r.passage.chapter_section, 100),
                    final_position=pos,
                ),
            )
        )
    return out
```

- [ ] **Step 2: Register router in app**

In the FastAPI app factory:

```python
from genereview_link.api.routes import passages as passages_routes
app.include_router(passages_routes.router)
```

- [ ] **Step 3: Test + commit**

```bash
uv run pytest tests/test_routes_passages.py -v
git add genereview_link/api/routes/passages.py tests/test_routes_passages.py
git commit -m "feat(api): /passages/search route with optional RRF rerank"
```

### Task 5.10: /chapters/{nbk}/sections/{section}

Same pattern; the route returns a flattened `dict` with concatenated passage text and `heading_path`. Tests asserting section content recovered from seeded data.

### Task 5.11: /debug/ranking

Same pattern; gated behind `DEBUG_RANKING_ENABLED` env var; returns full ScoreBreakdown for top-N candidates. Excluded from MCP via route map filter in `server_manager.py`.

### Task 5.12: MCP tool name mapping

**Files:**
- Modify: `genereview_link/server_manager.py:161-181`

- [ ] **Step 1: Add to the name map**

```python
"search_passages": "search_passages",
"get_chapter_section": "get_chapter_section",
```

Confirm `/debug/` is in the exclude list.

- [ ] **Step 2: Commit**

```bash
git add genereview_link/server_manager.py
git commit -m "feat(mcp): expose search_passages and get_chapter_section MCP tools"
```

### Tasks 5.13-5.14: Route integration tests + eval baseline capture

After Phase 5 lands, run the eval set once against a populated DB and check in the resulting `baseline.json` as the official baseline.

**Phase 5 done.** User-visible release.

---

## Phase 6 — CI bundle workflow

Goal: GitHub Actions builds bundle weekly; container downloads + restores on first boot.

### Task 6.1: bundle.py — pg_dump + manifest + tar

**Files:**
- Create: `genereview_link/corpus/bundle.py`
- Test: `tests/unit/test_corpus_bundle.py`

- [ ] **Step 1: Implement**

```python
"""Build a release bundle from a populated Postgres."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class BundleManifest:
    manifest_version: str = "1"
    bundle_format: str = "tar.gz"
    corpus_version: str = ""
    tarball_source_sha256: str = ""
    tarball_last_updated: str = ""
    chapter_count: int = 0
    passage_count: int = 0
    embedding: dict = field(default_factory=lambda: {
        "model_name": "BAAI/bge-small-en-v1.5",
        "dimension": 384,
        "distance_metric": "cosine",
        "active_table": "genereview_embeddings_bge384",
    })
    postgres: dict = field(default_factory=lambda: {
        "major_version": "18",
        "pgvector_version": "0.8.2",
    })
    schema_migrations: list[str] = field(default_factory=list)
    genereview_link_version: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    created_by: str = "manual"
    license: dict = field(default_factory=lambda: {
        "copyright": "(c) 1993-2026 University of Washington",
        "terms_url": "https://www.ncbi.nlm.nih.gov/books/NBK138602/",
    })
    checksums: dict[str, str] = field(default_factory=dict)


def pg_dump_to(dump_path: Path, *, database_url: str) -> None:
    subprocess.run(
        ["pg_dump", "-Fc", "-f", str(dump_path), database_url],
        check=True,
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_bundle(
    *,
    work_dir: Path,
    output: Path,
    manifest: BundleManifest,
    sidedata_dir: Path,
) -> Path:
    """Pack manifest + corpus.dump + sidedata/ into a single .tar.gz."""
    dump = work_dir / "corpus.dump"
    manifest.checksums["corpus.dump"] = sha256_file(dump)
    for f in sidedata_dir.iterdir():
        if f.is_file():
            manifest.checksums[f"sidedata/{f.name}"] = sha256_file(f)

    manifest_path = work_dir / "manifest.json"
    manifest_path.write_text(json.dumps(asdict(manifest), indent=2))

    with tarfile.open(output, "w:gz") as tar:
        tar.add(manifest_path, arcname="manifest.json")
        tar.add(dump, arcname="corpus.dump")
        for f in sidedata_dir.iterdir():
            if f.is_file():
                tar.add(f, arcname=f"sidedata/{f.name}")

    sha_sibling = output.with_suffix(output.suffix + ".sha256")
    sha_sibling.write_text(sha256_file(output) + "  " + output.name + "\n")
    return output
```

- [ ] **Step 2: Commit**

```bash
git add genereview_link/corpus/bundle.py tests/unit/test_corpus_bundle.py
git commit -m "feat(bundle): pg_dump + manifest.json + sibling sha256 packaging"
```

### Task 6.2: github_release.py — download + verify

**Files:**
- Create: `genereview_link/ingest/github_release.py`

- [ ] **Step 1: Implement**

```python
"""Resolve and download GitHub Release bundles."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

GITHUB_API = "https://api.github.com"


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    name: str
    url: str
    digest: str | None


async def resolve_latest(repo: str) -> str:
    """Return the asset URL for the latest 'corpus-*' release bundle."""
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(f"{GITHUB_API}/repos/{repo}/releases/latest")
        r.raise_for_status()
        for asset in r.json().get("assets", []):
            if asset["name"].endswith(".tar.gz") and asset["name"].startswith("genereview-corpus-"):
                return asset["browser_download_url"]
    raise RuntimeError("no corpus bundle found in latest release")


async def fetch_sibling_sha256(url: str) -> str:
    """Fetch <url>.sha256 sibling file and return the hex digest."""
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(f"{url}.sha256")
        r.raise_for_status()
        return r.text.strip().split()[0]


async def download_with_integrity(
    url: str, dest: Path, *, expected_sha256: str
) -> None:
    """Stream-download *url* to *dest*, verifying sha256."""
    sha = hashlib.sha256()
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=None) as c:
        async with c.stream("GET", url) as r:
            r.raise_for_status()
            with dest.open("wb") as fh:
                async for chunk in r.aiter_bytes(1 << 20):
                    sha.update(chunk)
                    fh.write(chunk)
    if sha.hexdigest() != expected_sha256:
        dest.unlink(missing_ok=True)
        raise RuntimeError(
            f"bundle sha256 mismatch: expected {expected_sha256}, got {sha.hexdigest()}"
        )


async def pg_restore(dump_path: Path, *, database_url: str, jobs: int | None = None) -> None:
    import subprocess
    cmd = ["pg_restore", "-d", database_url]
    if jobs:
        cmd += ["-j", str(jobs)]
    cmd.append(str(dump_path))
    subprocess.run(cmd, check=True)
```

- [ ] **Step 2: Commit**

```bash
git add genereview_link/ingest/github_release.py
git commit -m "feat(ingest): GitHub Release resolver + sibling-sha256 download + pg_restore"
```

### Task 6.3: Three-mode entrypoint

**Files:**
- Modify: `genereview_link/cli.py` (extend `serve` command's startup)

- [ ] **Step 1: Add bootstrap hook**

Before uvicorn starts, run:

```python
async def bootstrap() -> None:
    pool = await create_pool()
    try:
        applied = await apply_control_migrations(pool)
        if applied:
            typer.echo(f"applied control migrations: {applied}")

        active = await pool.fetchval("select 1 from public.genereview_corpus_version where is_active")
        if active:
            return  # MODE 1 hot path / already-populated

        bundle_url = settings.BUNDLE_URL
        if bundle_url == "latest":
            bundle_url = await resolve_latest(settings.GITHUB_REPO)
        if bundle_url:
            sha = await fetch_sibling_sha256(bundle_url)
            tmp = Path("/tmp") / "bundle.tar.gz"
            await download_with_integrity(bundle_url, tmp, expected_sha256=sha)
            # extract + verify manifest + pg_restore
            with tarfile.open(tmp, "r:gz") as tf:
                tf.extractall("/tmp/bundle_extract")
            manifest = json.loads(Path("/tmp/bundle_extract/manifest.json").read_text())
            for relpath, expected in manifest["checksums"].items():
                actual = sha256_file(Path("/tmp/bundle_extract") / relpath)
                if actual != expected:
                    raise RuntimeError(f"manifest checksum mismatch on {relpath}")
            await pg_restore(
                Path("/tmp/bundle_extract/corpus.dump"),
                database_url=settings.DATABASE_URL,
                jobs=os.cpu_count(),
            )
            return

        if settings.BUILD_LOCAL:
            from genereview_link.corpus.pipeline import run_full_ingest
            from genereview_link.ingest.orchestrator import backfill_embeddings, build_hnsw_index
            from genereview_link.retrieval.embeddings import SentenceTransformerEmbeddingProvider
            await run_full_ingest(pool)
            await backfill_embeddings(pool, SentenceTransformerEmbeddingProvider())
            await build_hnsw_index(pool)
            return

        # MODE 3: external Postgres — assume corpus exists
    finally:
        await pool.close()
```

- [ ] **Step 2: Add settings**

```python
    BUNDLE_URL: str = ""
    BUILD_LOCAL: bool = False
    GITHUB_REPO: str = "berntpopp/genereviews-link"
    AUTO_PULL_RELEASES: bool = False
    GENEREVIEW_EAGER_LOAD_BGE: bool = False
```

- [ ] **Step 3: Commit**

```bash
git add genereview_link/cli.py genereview_link/config.py
git commit -m "feat(cli): three-mode entrypoint bootstrap (bundle / build / external)"
```

### Task 6.4: Advisory-lock-guarded release watcher

**Files:**
- Create: `genereview_link/ingest/scheduler.py`

- [ ] **Step 1: Implement**

```python
"""APScheduler hourly release watcher, single-fired across gunicorn workers."""

from __future__ import annotations

import logging

import asyncpg

from genereview_link.config import settings
from genereview_link.ingest.github_release import resolve_latest

logger = logging.getLogger(__name__)

RELEASE_WATCHER_LOCK_ID = 0x47525F524C5F31  # "GR_RL_1"


async def check_for_new_release(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        got = await conn.fetchval("select pg_try_advisory_lock($1)", RELEASE_WATCHER_LOCK_ID)
        if not got:
            return
        try:
            latest_url = await resolve_latest(settings.GITHUB_REPO)
            active = await conn.fetchval(
                "select version from public.genereview_corpus_version where is_active"
            )
            logger.info(
                "release watcher fired",
                extra={"latest_url": latest_url, "active": active},
            )
            # Pull and swap only if AUTO_PULL_RELEASES is true
            if settings.AUTO_PULL_RELEASES:
                pass  # implementation extends Task 6.3 bootstrap into a hot-swap path
        finally:
            await conn.fetchval("select pg_advisory_unlock($1)", RELEASE_WATCHER_LOCK_ID)
```

- [ ] **Step 2: Wire APScheduler in app factory**

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
scheduler = AsyncIOScheduler()
scheduler.add_job(check_for_new_release, "cron", minute=17, args=[pool])
scheduler.start()
```

- [ ] **Step 3: Test advisory lock works**

`tests/integration/test_scheduler_advisory_lock.py` — spawn two coroutines holding the lock; assert only one proceeds.

- [ ] **Step 4: Commit**

```bash
git add genereview_link/ingest/scheduler.py tests/integration/test_scheduler_advisory_lock.py
git commit -m "feat(scheduler): advisory-lock-guarded release watcher"
```

### Task 6.5: GitHub Actions workflow

**Files:**
- Create: `.github/workflows/build-corpus.yml`

- [ ] **Step 1: Create workflow**

```yaml
name: Build corpus bundle

on:
  schedule:
    - cron: "0 6 * * MON"
  workflow_dispatch:
    inputs:
      force:
        description: "Force build even if file_list.csv unchanged"
        type: boolean
        default: false

concurrency:
  group: build-corpus
  cancel-in-progress: false

jobs:
  check:
    runs-on: ubuntu-latest
    outputs:
      corpus_version: ${{ steps.check.outputs.corpus_version }}
      should_build: ${{ steps.check.outputs.should_build }}
    steps:
      - uses: actions/checkout@v4
      - id: check
        run: |
          curl -sf https://ftp.ncbi.nlm.nih.gov/pub/litarch/file_list.csv \
            | awk -F, '$5=="NBK1116" {print $6}' > /tmp/last_updated.txt
          LU=$(cat /tmp/last_updated.txt | head -1)
          CV=$(echo "$LU" | cut -d' ' -f1)
          echo "corpus_version=$CV" >> $GITHUB_OUTPUT
          # Compare to latest release tag
          LATEST=$(gh release list --limit 1 --json tagName -q '.[0].tagName' 2>/dev/null || echo "")
          if [ "${{ inputs.force }}" = "true" ] || [ "corpus-$CV" != "$LATEST" ]; then
            echo "should_build=true" >> $GITHUB_OUTPUT
          else
            echo "should_build=false" >> $GITHUB_OUTPUT
          fi
        env:
          GH_TOKEN: ${{ github.token }}

  build:
    needs: check
    if: needs.check.outputs.should_build == 'true'
    runs-on: ubuntu-latest-8-core
    services:
      postgres:
        image: pgvector/pgvector:0.8.2-pg18
        env:
          POSTGRES_PASSWORD: ci
          POSTGRES_DB: genereview
        ports: ["5432:5432"]
        options: --health-cmd pg_isready --health-interval 5s --health-retries 10
    env:
      DATABASE_URL: postgresql://postgres:ci@localhost:5432/genereview
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --frozen
      - uses: actions/cache@v4
        with:
          path: ~/.cache/huggingface
          key: hf-BAAI_bge-small-en-v1.5-${{ hashFiles('pyproject.toml') }}
          restore-keys: |
            hf-BAAI_bge-small-en-v1.5-
      - run: uv run genereview-link db migrate
      - run: uv run genereview-link ingest
      - run: uv run genereview-link embed
      - run: |
          mkdir -p dist
          pg_dump -Fc -f dist/corpus.dump $DATABASE_URL
          # build manifest + tarball via CLI
          uv run genereview-link bundle --output dist/genereview-corpus-${{ needs.check.outputs.corpus_version }}-bge384.tar.gz
      - uses: actions/upload-artifact@v4
        with:
          name: bundle
          path: dist/

  release:
    needs: [check, build]
    if: needs.check.outputs.should_build == 'true'
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: bundle
          path: dist
      - uses: softprops/action-gh-release@v2
        with:
          tag_name: corpus-${{ needs.check.outputs.corpus_version }}
          files: |
            dist/*.tar.gz
            dist/*.sha256
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/build-corpus.yml
git commit -m "ci: weekly build-corpus workflow with matrix-ready scaffold"
```

### Task 6.6: bundle CLI subcommand

**Files:**
- Modify: `genereview_link/cli.py`

- [ ] **Step 1: Append**

```python
bundle_app = typer.Typer(name="bundle", help="Build and verify release bundles.")
app.add_typer(bundle_app)


@bundle_app.command("build")
def bundle_build(
    output: Annotated[Path, typer.Option("--output")] = Path("genereview-corpus.tar.gz"),
) -> None:
    """Build a release bundle from the current DATABASE_URL."""
    import asyncio
    import tempfile
    from datetime import UTC, datetime

    from genereview_link.config import settings
    from genereview_link.corpus.bundle import (
        BundleManifest,
        pg_dump_to,
        write_bundle,
    )
    from genereview_link.db.pool import create_pool

    async def run() -> None:
        pool = await create_pool()
        try:
            row = await pool.fetchrow(
                "select version, chapter_count from public.genereview_corpus_version where is_active"
            )
            if not row:
                typer.echo("no active corpus version; aborting")
                raise typer.Exit(1)
            with tempfile.TemporaryDirectory() as td:
                td_path = Path(td)
                pg_dump_to(td_path / "corpus.dump", database_url=settings.DATABASE_URL)
                sidedata = td_path / "sidedata"
                sidedata.mkdir()
                # In CI: side-data is fetched by the ingest step into a known dir;
                # here we re-fetch for simplicity.
                from genereview_link.corpus.pipeline import _download_sidedata  # noqa
                await _download_sidedata(sidedata)
                m = BundleManifest(
                    corpus_version=row["version"],
                    chapter_count=row["chapter_count"] or 0,
                    created_at=datetime.now(UTC).isoformat(),
                    created_by="cli",
                )
                write_bundle(work_dir=td_path, output=output, manifest=m, sidedata_dir=sidedata)
                typer.echo(f"wrote {output} (+ {output}.sha256)")
        finally:
            await pool.close()

    asyncio.run(run())
```

- [ ] **Step 2: Make target**

```makefile
bundle: ## Build release bundle from active corpus
	uv run genereview-link bundle build
```

- [ ] **Step 3: Commit**

```bash
git add genereview_link/cli.py Makefile
git commit -m "feat(cli): bundle build subcommand"
```

### Task 6.7: Bundle round-trip integration test

**Files:**
- Create: `tests/integration/test_bundle_round_trip.py`

- [ ] **Step 1: Write test**

```python
"""pg_dump → pg_restore round-trip preserves data + builds HNSW."""

from __future__ import annotations

import asyncpg
import pytest

from genereview_link.corpus.bundle import pg_dump_to
from genereview_link.db.migrate import apply_control_migrations, apply_data_migrations


@pytest.mark.asyncio
@pytest.mark.slow
async def test_pg_dump_restore_round_trip(pool: asyncpg.Pool, tmp_path) -> None:
    await apply_control_migrations(pool)
    await apply_data_migrations(pool, schema="genereview")
    # ... seed minimal data, dump, restore into a fresh schema, assert counts match
```

- [ ] **Step 2: Commit**

```bash
git add tests/integration/test_bundle_round_trip.py
git commit -m "test(bundle): pg_dump/pg_restore round-trip integration test"
```

### Task 6.8: Dockerfile — install postgresql-client

**Files:**
- Modify: `docker/Dockerfile`

- [ ] **Step 1: Add apt step**

In the production stage, before `COPY` of source:

```dockerfile
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 2: Commit**

```bash
git add docker/Dockerfile
git commit -m "feat(docker): install postgresql-client for pg_restore at runtime"
```

### Task 6.9: README updates

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document three modes + memory tuning**

Add a "Deployment modes" section explaining `BUNDLE_URL`, `BUILD_LOCAL`, and external Postgres; document `GUNICORN_WORKERS=2` recommendation; document the 3 GB memory budget.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: deployment modes, memory budget, and tuning guidance"
```

### Task 6.10: Final acceptance — fresh `docker compose up` with BUNDLE_URL

- [ ] **Step 1: Smoke test**

```bash
GENEREVIEW_BUNDLE_URL=<release-asset-url> docker compose up
# verify /health/corpus reports the expected version
# verify /passages/search?q=BRCA1 returns ranked passages
```

- [ ] **Step 2: Tag v0.4.0**

```bash
git tag v0.4.0
git push --tags
```

**Phase 6 done.** Feature complete.

---

## Self-review

After saving both parts, run the spec-coverage check:

| Spec area | Plan coverage |
|---|---|
| Two-schema atomic swap | Phase 2 tasks 2.9, 2.11 |
| Three-tsquery lexical | Phase 4 task 4.3 |
| RRF + section_priority tuple sort | Phase 4 task 4.6 |
| HNSW post-COPY | Phase 3 task 3.2 |
| `?fresh=true` opt-in | Phase 5 tasks 5.3-5.8 |
| Structured 404 | Phase 5 task 5.2 |
| `/abstract` field mapping | Phase 5 task 5.4 (abstract route) |
| MCP tool name map | Phase 5 task 5.12 |
| CI runner sizing | Phase 6 task 6.5 |
| Sibling sha256 | Phase 6 task 6.2 |
| `BUNDLE_URL=latest` | Phase 6 task 6.3 |
| Advisory-lock release watcher | Phase 6 task 6.4 |
| Eval baseline | Phase 4 task 4.7 |
| Makefile targets | Phases 1-6 each add their own |
| Memory budget | Phase 1 task 1.13 + Phase 6 task 6.9 |
| Tokenizer specification | Phase 2 task 2.3 |
| `pubmed_id` column | Phase 1 task 1.9 |
| Risk register entries (advisory lock, sha256 sibling, HNSW timing, missing summary, eval drift) | Tasks 6.4, 6.2, 3.2, 5.4, 4.7 |

All major spec areas have at least one task. Plan complete.
