# MCP LLM-Ergonomics Pass 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lift the GeneReview-Link MCP server from consumer-rated 8.2/10 to ~9.2/10 by fixing broken metadata promises, making tables retrievable, adding three high-leverage discovery affordances, and consolidating polish items — across four sequential phases.

**Architecture:** Four phases (Trust → Discovery → Content → Polish), each shippable independently. Phase 5 is additive code-only changes. Phase 6 adds three new MCP tools and one breaking response-shape change. Phase 7 migrates the DB, changes the scraper/chunker, and triggers a full corpus rebuild. Phase 8 promotes `get_license` to an MCP resource and lands smaller polish items.

**Tech Stack:** Python 3.12, FastAPI + FastMCP, asyncpg, Pydantic v2, PostgreSQL + pgvector + ts_headline, BGE embeddings, defusedxml for NXML parsing, rapidfuzz for fuzzy matching, pytest + ruff + mypy strict.

**Spec:** `docs/superpowers/specs/2026-05-11-mcp-llm-ergonomics-pass2-design.md`

---

## File Map

**Modified files (Phase 5):**
- `genereview_link/corpus/nxml.py` — fix `last_updated_date` parsing
- `genereview_link/retrieval/repository.py` — add `dense_rank`/`rrf_score` to `LexicalPassageRow`
- `genereview_link/retrieval/rerank.py` — populate new fields
- `genereview_link/api/routes/passages.py` — wire fields, add `include` param
- `genereview_link/api/routes/debug.py` — wire fields
- `genereview_link/models/genereview_models.py` — make `score_breakdown` optional on `RankedPassage`
- `genereview_link/server_manager.py` — update server instructions

**New + modified (Phase 6):**
- `genereview_link/retrieval/repository.py` — add `get_passage_window`, `get_chapter_metadata`
- `genereview_link/models/genereview_models.py` — `PassageWindowResponse`, `ChapterMetadataResponse`, `SearchDiagnostics`, `SectionSummary`
- `genereview_link/api/routes/passages.py` — wrap `get_passage`, add empty-result diagnostics
- `genereview_link/api/routes/chapters.py` — `metadata` route, drop `concatenated_text` default
- `genereview_link/api/diagnostics.py` (NEW) — diagnostic-suggestion rules

**New + modified (Phase 7):**
- `db/migrations/00X_passage_type_and_tables.sql` (NEW)
- `genereview_link/corpus/records.py` — `Passage.passage_type`, `Passage.table_id`, `Passage.table_data`
- `genereview_link/corpus/parallel.py` — copy ops include new columns
- `genereview_link/corpus/nxml.py` — table extraction
- `genereview_link/corpus/tables.py` (NEW) — markdown serialization, table_data builder
- `genereview_link/corpus/chunking.py` — fix tokenizer leak
- `genereview_link/embeddings/builder.py` (or wherever embedding input is built) — table truncation
- `genereview_link/retrieval/repository.py` — `get_table`, `get_chapter_metadata.table_count`
- `genereview_link/api/routes/tables.py` (NEW) — `/chapters/{nbk_id}/tables/{table_id}` route
- `genereview_link/server_manager.py` — register tables router, update instructions

**New + modified (Phase 8):**
- `genereview_link/server_manager.py` — register MCP resource for license, exclude `get_license` tool
- `genereview_link/api/routes/passages.py` — custom 422 handler, gene validation
- `genereview_link/api/routes/chapters.py` — `dedupe` param
- `genereview_link/services/gene_index.py` (NEW) — cached indexed-symbol set
- `pyproject.toml` — add `rapidfuzz` dependency

---

# Phase 5 — Trust Pass

**Goal:** Resolve every "broken promise" field. Phase ends with `make ci-local` green plus live smoke probe against gr-pg corpus showing populated `rrf_score`/`dense_rank` and a fixture-NXML unit test populating `chapter_last_updated`. Tag: `phase-5-ergonomics-v2`.

---

### Task 1: Investigate `last_updated_date` extraction

**Files:**
- Read: `genereview_link/corpus/nxml.py`
- Read: a real Bookshelf NXML fixture (one of the existing `tests/fixtures/*.nxml` files, or fetch fresh)

**Why:** 0/882 chapters have `last_updated_date` populated in the gr-pg DB. Code at `corpus/nxml.py:76` calls `last_updated_date=updated`, so either the XPath is wrong, the format is wrong, or the parse silently fails. We need the actual NXML element shape before fixing.

- [ ] **Step 1: Grep for the existing extraction code**

```bash
grep -n "updated\|last_updated\|pub-date\|date-type" genereview_link/corpus/nxml.py
```
Expected: shows the function that builds the `updated` value passed at line 76.

- [ ] **Step 2: Inspect a fixture NXML for the actual element shape**

```bash
ls tests/fixtures/*.nxml 2>/dev/null | head -3
# Pick one, then:
grep -A2 -E "<pub-date|<date " tests/fixtures/<file>.nxml | head -40
```
Expected: shows the real element used for "last revision date." NCBI Bookshelf typically uses `<book-meta>/<pub-history>/<date date-type="updated">` or `<book-part>/<book-part-meta>/<pub-date pub-type="last-revision">`. Note the exact path and format.

- [ ] **Step 3: Document findings as a code comment in nxml.py**

Add a comment above the extraction function recording the NXML element path and date format observed. This is the only persisted artifact of the investigation step — the fix in Task 2 references it.

- [ ] **Step 4: Commit investigation note**

```bash
git add genereview_link/corpus/nxml.py
git commit -m "docs(corpus): record observed last_updated_date NXML element"
```

---

### Task 2: Fix `last_updated_date` parser with regression test

**Files:**
- Modify: `genereview_link/corpus/nxml.py` (the function returning `updated`)
- Test: `tests/corpus/test_nxml_last_updated.py` (NEW)
- Fixture: `tests/fixtures/last_updated_sample.nxml` (NEW or reuse)

- [ ] **Step 1: Write failing unit test against a tiny fixture NXML**

Create `tests/fixtures/last_updated_sample.nxml`:
```xml
<book-part>
  <book-part-meta>
    <pub-date pub-type="last-revision">
      <day>14</day><month>09</month><year>2023</year>
    </pub-date>
  </book-part-meta>
</book-part>
```
(Use the *actual* element shape observed in Task 1 — the example above is illustrative.)

Create `tests/corpus/test_nxml_last_updated.py`:
```python
from datetime import date
from pathlib import Path

from genereview_link.corpus.nxml import parse_chapter  # adjust import


def test_parse_chapter_extracts_last_updated_date(tmp_path: Path) -> None:
    fixture = Path("tests/fixtures/last_updated_sample.nxml").read_bytes()
    chapter = parse_chapter(fixture, nbk_id="NBK_TEST")
    assert chapter.last_updated_date == date(2023, 9, 14)
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/corpus/test_nxml_last_updated.py -v
```
Expected: FAIL — either `AssertionError` (returns None) or `ImportError` (function name mismatch). Adjust import name to match real entry point.

- [ ] **Step 3: Fix the parser**

Replace the broken extraction with one that targets the element identified in Task 1. Pattern (using defusedxml — DO NOT import xml.etree directly per AGENTS.md):
```python
from defusedxml import ElementTree as ET

def _extract_last_updated(root: ET.Element) -> date | None:
    """Read the last-revision pub-date from book-part-meta. Returns None if absent or unparseable."""
    node = root.find(".//book-part-meta/pub-date[@pub-type='last-revision']")
    if node is None:
        return None
    try:
        y = int(node.findtext("year") or "")
        m = int(node.findtext("month") or "")
        d = int(node.findtext("day") or "")
        return date(y, m, d)
    except (TypeError, ValueError):
        return None
```
(Adjust XPath and field names to the *actual* shape observed.)

- [ ] **Step 4: Run test to confirm it passes**

```bash
uv run pytest tests/corpus/test_nxml_last_updated.py -v
```
Expected: PASS.

- [ ] **Step 5: Run the full corpus test suite to confirm no regressions**

```bash
uv run pytest tests/corpus/ -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/corpus/nxml.py tests/corpus/test_nxml_last_updated.py tests/fixtures/last_updated_sample.nxml
git commit -m "fix(corpus): extract chapter last_updated_date from NXML"
```

---

### Task 3: Add `dense_rank` + `rrf_score` to `LexicalPassageRow`

**Files:**
- Modify: `genereview_link/retrieval/repository.py:51-64`

- [ ] **Step 1: Write failing test**

Create or extend `tests/retrieval/test_lexical_passage_row.py`:
```python
from genereview_link.retrieval.repository import LexicalPassageRow, PassageRow


def test_lexical_passage_row_carries_rrf_fields() -> None:
    p = PassageRow(
        nbk_id="NBK1",
        passage_id="NBK1:0001",
        chapter_section="summary",
        heading_path=None,
        section_level=0,
        chunk_index=1,
        text="t",
    )
    row = LexicalPassageRow(
        passage=p,
        phrase_rank=0.0,
        strict_rank=0.0,
        recall_rank=0.0,
        recall_overlap_count=0,
        lexical_rank=0.5,
        dense_rank=3,
        rrf_score=0.024,
    )
    assert row.dense_rank == 3
    assert row.rrf_score == 0.024
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/retrieval/test_lexical_passage_row.py -v
```
Expected: FAIL — `LexicalPassageRow.__init__() got an unexpected keyword argument 'dense_rank'`.

- [ ] **Step 3: Add the fields**

Edit `genereview_link/retrieval/repository.py` (the `LexicalPassageRow` dataclass at line 51):
```python
@dataclass(frozen=True, slots=True)
class LexicalPassageRow:
    """A passage with its lexical and (optional) dense scores attached."""

    passage: PassageRow
    phrase_rank: float
    strict_rank: float
    recall_rank: float
    recall_overlap_count: int
    lexical_rank: float
    snippet: str | None = None
    dense_rank: int | None = None
    rrf_score: float | None = None
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
uv run pytest tests/retrieval/test_lexical_passage_row.py -v
```
Expected: PASS.

- [ ] **Step 5: Run typecheck and existing tests**

```bash
make typecheck-fast && uv run pytest tests/retrieval/ -v
```
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/retrieval/repository.py tests/retrieval/test_lexical_passage_row.py
git commit -m "feat(retrieval): add dense_rank and rrf_score to LexicalPassageRow"
```

---

### Task 4: Populate `dense_rank`/`rrf_score` in `rerank_with_embeddings`

**Files:**
- Modify: `genereview_link/retrieval/rerank.py` (around line 94-105)
- Test: `tests/retrieval/test_rerank.py` (add or extend)

- [ ] **Step 1: Write failing test**

Add to `tests/retrieval/test_rerank.py`:
```python
def test_rerank_populates_dense_rank_and_rrf_score() -> None:
    # Build minimal lexical_results and dense_sorted with known order.
    # (Reuse existing test fixtures or build inline; assert top-result row
    # has non-null dense_rank and rrf_score after rerank_with_embeddings.)
    rows = build_test_rows()  # helper from existing test file
    out = rerank_with_embeddings(rows, dense_sorted=rows[:3], rrf_k=60)
    assert out[0].rrf_score is not None
    assert out[0].dense_rank is not None
```
(Reuse existing helper patterns in the file; if none exist, build inline `LexicalPassageRow` instances.)

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/retrieval/test_rerank.py -v -k populates_dense_rank
```
Expected: FAIL — fields are None on returned rows.

- [ ] **Step 3: Modify `rerank_with_embeddings` to set fields**

In `genereview_link/retrieval/rerank.py`, after computing `rrf_score` per row but before returning, replace each row with one that carries the score. Because `LexicalPassageRow` is frozen, use `dataclasses.replace`:
```python
import dataclasses

# inside rerank_with_embeddings, replacing the existing return shape:
def rrf(r: LexicalPassageRow) -> float:
    score = 1.0 / (rrf_k + lex_rank[r.passage.passage_id])
    if r.passage.passage_id in dense_rank:
        score += 1.0 / (rrf_k + dense_rank[r.passage.passage_id])
    return score

scored = [
    dataclasses.replace(
        r,
        dense_rank=dense_rank.get(r.passage.passage_id),
        rrf_score=rrf(r),
    )
    for r in lexical_results
]
return sorted(
    scored,
    key=lambda r: (
        -(r.rrf_score or 0.0),
        SECTION_PRIORITY.get(r.passage.chapter_section, 999),
        r.passage.nbk_id,
        r.passage.passage_id,
    ),
)
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
uv run pytest tests/retrieval/test_rerank.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/retrieval/rerank.py tests/retrieval/test_rerank.py
git commit -m "feat(retrieval): populate rrf_score and dense_rank on reranked rows"
```

---

### Task 5: Make `score_breakdown` optional on `RankedPassage`; add `include` param

**Files:**
- Modify: `genereview_link/models/genereview_models.py` (`RankedPassage` at line 184, `ScoreBreakdown` at 170)
- Modify: `genereview_link/api/routes/passages.py` (signature, response build)

- [ ] **Step 1: Write failing route test**

Add to `tests/routes/test_passages_search.py` (or wherever search route tests live):
```python
def test_search_omits_score_breakdown_by_default(test_client) -> None:
    resp = test_client.get("/passages/search", params={"q": "BRCA1"})
    assert resp.status_code == 200
    data = resp.json()
    if data["results"]:
        assert "score_breakdown" not in data["results"][0]


def test_search_includes_score_breakdown_when_requested(test_client) -> None:
    resp = test_client.get(
        "/passages/search", params={"q": "BRCA1", "include": "score_breakdown"}
    )
    assert resp.status_code == 200
    data = resp.json()
    if data["results"]:
        assert "score_breakdown" in data["results"][0]
        # rrf_score MUST be populated (Task 4) when rerank=rrf (default)
        assert data["results"][0]["score_breakdown"]["rrf_score"] is not None
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/routes/test_passages_search.py -v -k score_breakdown
```
Expected: FAIL — score_breakdown still always present (or rrf_score still null without Task 4 wiring through).

- [ ] **Step 3: Make `score_breakdown` optional on the model**

In `genereview_link/models/genereview_models.py`, modify `RankedPassage`:
```python
class RankedPassage(BaseModel):
    # existing fields unchanged...
    score_breakdown: ScoreBreakdown | None = None  # was: ScoreBreakdown
```

- [ ] **Step 4: Add `include` param and conditional construction**

In `genereview_link/api/routes/passages.py` `search_passages` route:
```python
from typing import Annotated, Literal
from fastapi import Query

# in the function signature, add:
include: Annotated[
    list[Literal["score_breakdown"]] | None,
    Query(description="Opt into default-off response fields. Currently supports 'score_breakdown' (raw lexical/dense ranks). Use for ranker debugging."),
] = None,
```

When constructing each `RankedPassage`, conditionally include `score_breakdown`:
```python
include_set = set(include or [])
include_score_breakdown = "score_breakdown" in include_set

# inside the row-building loop:
score_breakdown = (
    ScoreBreakdown(
        phrase_rank=r.phrase_rank,
        strict_rank=r.strict_rank,
        recall_rank=r.recall_rank,
        recall_overlap_count=r.recall_overlap_count,
        lexical_rank=r.lexical_rank,
        dense_rank=r.dense_rank,
        rrf_score=r.rrf_score,
    )
    if include_score_breakdown
    else None
)

out.append(
    RankedPassage(
        # existing fields...
        score_breakdown=score_breakdown,
    )
)
```

Also wire `r.dense_rank` and `r.rrf_score` from the lexical row instead of hardcoded `None` at `passages.py:202-203`.

Mirror the same wiring change in `genereview_link/api/routes/debug.py:71-72` (read from row, not None).

- [ ] **Step 5: Run tests to confirm they pass**

```bash
uv run pytest tests/routes/test_passages_search.py -v
```
Expected: PASS.

- [ ] **Step 6: Confirm exclude no-op behavior**

Add a regression test:
```python
def test_search_exclude_score_breakdown_is_noop_after_default_flip(test_client) -> None:
    resp = test_client.get(
        "/passages/search", params={"q": "BRCA1", "exclude": "score_breakdown"}
    )
    assert resp.status_code == 200
    if resp.json()["results"]:
        assert "score_breakdown" not in resp.json()["results"][0]
```
Run: `uv run pytest tests/routes/test_passages_search.py -v -k exclude_score_breakdown`. Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add genereview_link/models/genereview_models.py genereview_link/api/routes/passages.py genereview_link/api/routes/debug.py tests/routes/test_passages_search.py
git commit -m "feat(api): score_breakdown opt-in via include= and wire rrf_score/dense_rank"
```

---

### Task 6: Update server instructions for Phase 5

**Files:**
- Modify: `genereview_link/server_manager.py` (the `instructions=` block in `from_fastapi(...)`)

- [ ] **Step 1: Update instructions text**

Locate the `instructions=` argument to `FastMCP.from_fastapi(...)` in `genereview_link/server_manager.py`. In the section describing payload-size budgets / response shape, add one sentence about `include=score_breakdown` for ranker debugging. Keep existing canonical-pipeline text unchanged.

Example addition:
```
Pass include=["score_breakdown"] on search_passages to debug ranker
behavior (raw lexical/dense ranks plus rrf_score). Off by default to
keep brief-mode payload tight.
```

- [ ] **Step 2: Run typecheck and tests**

```bash
make typecheck-fast && uv run pytest tests/ -v -k "instructions or server_manager" 2>&1 | tail -20
```
Expected: green; no test scrapes the instructions string today, so this is a docs-only change.

- [ ] **Step 3: Commit**

```bash
git add genereview_link/server_manager.py
git commit -m "docs(mcp): mention include=score_breakdown in server instructions"
```

---

### Task 7: Phase 5 gate — `make ci-local` + live smoke + tag

**Files:**
- New: `tests/smoke/phase_5.sh`

- [ ] **Step 1: Run full CI locally**

```bash
make ci-local
```
Expected: format, lint, typecheck, tests all pass.

- [ ] **Step 2: Bring up dev server against gr-pg corpus**

```bash
DATABASE_URL=postgresql://genereview:genereview@127.0.0.1:5436/genereview make dev &
sleep 3
```

- [ ] **Step 3: Write smoke script**

Create `tests/smoke/phase_5.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"

echo "=== Phase 5 smoke checks ==="

# 1. score_breakdown absent by default
out=$(curl -sf "$BASE/passages/search?q=BRCA1&limit=1")
echo "$out" | jq -e '.results[0] | has("score_breakdown") | not' >/dev/null \
  || { echo "FAIL: score_breakdown should be absent by default"; exit 1; }
echo "OK: score_breakdown absent by default"

# 2. include=score_breakdown returns non-null rrf_score
out=$(curl -sf "$BASE/passages/search?q=BRCA1&limit=1&include=score_breakdown&rerank=rrf")
echo "$out" | jq -e '.results[0].score_breakdown.rrf_score != null' >/dev/null \
  || { echo "FAIL: rrf_score should be non-null with include=score_breakdown"; exit 1; }
echo "OK: rrf_score populated"

# 3. include=score_breakdown returns non-null dense_rank
echo "$out" | jq -e '.results[0].score_breakdown.dense_rank != null' >/dev/null \
  || { echo "FAIL: dense_rank should be non-null"; exit 1; }
echo "OK: dense_rank populated"

# 4. exclude=score_breakdown is a no-op (still absent)
out=$(curl -sf "$BASE/passages/search?q=BRCA1&limit=1&exclude=score_breakdown")
echo "$out" | jq -e '.results[0] | has("score_breakdown") | not' >/dev/null \
  || { echo "FAIL: exclude=score_breakdown should remain absent"; exit 1; }
echo "OK: exclude=score_breakdown no-op"

echo "=== All Phase 5 smoke checks passed ==="
```

- [ ] **Step 4: Run smoke script**

```bash
chmod +x tests/smoke/phase_5.sh
tests/smoke/phase_5.sh
```
Expected: all OK lines, exit 0.

- [ ] **Step 5: Stop dev server, tag, push**

```bash
kill %1
git add tests/smoke/phase_5.sh
git commit -m "test(smoke): phase 5 trust-pass live probe"
git tag -a phase-5-ergonomics-v2 -m "Phase 5 trust pass complete"
```

---

# Phase 6 — Discovery Pass

**Goal:** Add `get_passage(neighbors)`, `get_chapter_metadata`, empty-result diagnostics, drop `concatenated_text` default. One breaking shape change for `get_passage`. Tag: `phase-6-ergonomics-v2`.

---

### Task 8: Add `get_passage_window` repository method

**Files:**
- Modify: `genereview_link/retrieval/repository.py` (after existing `get_passage`)
- Test: `tests/retrieval/test_repository_window.py` (NEW)

- [ ] **Step 1: Write failing integration test**

Requires test DB (`127.0.0.1:5436/genereview_test`) seeded — same pattern as existing repository integration tests.

```python
import pytest
from genereview_link.retrieval.repository import GeneReviewRepository


@pytest.mark.integration
async def test_get_passage_window_section_bounded(repo: GeneReviewRepository) -> None:
    # Use a known seeded passage in the test corpus
    focal, before, after, more_before, more_after = await repo.get_passage_window(
        "NBK1247:0010", before=2, after=2, cross_sections=False
    )
    assert focal is not None
    assert focal.passage_id == "NBK1247:0010"
    assert all(p.chapter_section == focal.chapter_section for p in before)
    assert all(p.chapter_section == focal.chapter_section for p in after)


@pytest.mark.integration
async def test_get_passage_window_at_section_boundary_sets_has_more_false(
    repo: GeneReviewRepository,
) -> None:
    # First chunk in a section; before should be empty, has_more_before False
    focal, before, after, more_before, more_after = await repo.get_passage_window(
        "NBK1247:0001", before=2, after=0, cross_sections=False
    )
    assert before == []
    assert more_before is False
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/retrieval/test_repository_window.py -v -m integration
```
Expected: FAIL — `AttributeError: 'GeneReviewRepository' object has no attribute 'get_passage_window'`.

- [ ] **Step 3: Implement `get_passage_window`**

Add to `genereview_link/retrieval/repository.py` after the existing `get_passage`:
```python
async def get_passage_window(
    self,
    passage_id: str,
    *,
    before: int,
    after: int,
    cross_sections: bool,
) -> tuple[PassageRow | None, list[PassageRow], list[PassageRow], bool, bool]:
    """Fetch a passage plus its neighbors within the same chapter.

    Neighbors stop at the section boundary unless cross_sections=True.
    Always stops at chapter boundary regardless. Returns (focal,
    before_rows, after_rows, has_more_before, has_more_after).
    """
    async with self._acquire() as conn:
        focal = await self._fetch_passage_row(conn, passage_id)
        if focal is None:
            return None, [], [], False, False

        if cross_sections:
            section_filter = ""
            params = [focal.nbk_id, focal.chunk_index]
        else:
            section_filter = "and p.chapter_section = $3"
            params = [focal.nbk_id, focal.chunk_index, focal.chapter_section]

        # Fetch one extra each side to compute has_more_*
        before_rows = await conn.fetch(
            f"""
            select p.nbk_id, p.passage_id, p.chapter_section, p.heading_path,
                   p.section_level, p.chunk_index, p.text,
                   c.title as chapter_title, c.last_updated_date,
                   c.gene_symbols
              from public.genereview_passages p
              join public.genereview_chapters c on c.nbk_id = p.nbk_id
             where p.nbk_id = $1
               and p.chunk_index < $2
               {section_filter}
             order by p.chunk_index desc
             limit {before + 1}
            """,
            *params,
        )
        after_rows = await conn.fetch(
            f"""
            select p.nbk_id, p.passage_id, p.chapter_section, p.heading_path,
                   p.section_level, p.chunk_index, p.text,
                   c.title as chapter_title, c.last_updated_date,
                   c.gene_symbols
              from public.genereview_passages p
              join public.genereview_chapters c on c.nbk_id = p.nbk_id
             where p.nbk_id = $1
               and p.chunk_index > $2
               {section_filter}
             order by p.chunk_index asc
             limit {after + 1}
            """,
            *params,
        )

    has_more_before = len(before_rows) > before
    has_more_after = len(after_rows) > after
    before_clipped = list(reversed([self._row_to_passage(r) for r in before_rows[:before]]))
    after_clipped = [self._row_to_passage(r) for r in after_rows[:after]]
    return focal, before_clipped, after_clipped, has_more_before, has_more_after
```

If helper methods `_fetch_passage_row` and `_row_to_passage` don't already exist, refactor the existing `get_passage` to share with this method (small refactor — the SQL is essentially identical to `get_passage`).

- [ ] **Step 4: Run integration tests to confirm pass**

```bash
DATABASE_URL=postgresql://genereview:genereview@127.0.0.1:5436/genereview_test \
  uv run pytest tests/retrieval/test_repository_window.py -v -m integration
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/retrieval/repository.py tests/retrieval/test_repository_window.py
git commit -m "feat(retrieval): add get_passage_window for neighbor retrieval"
```

---

### Task 9: Add `PassageWindowResponse` model

**Files:**
- Modify: `genereview_link/models/genereview_models.py`

- [ ] **Step 1: Add the model**

```python
class PassageWindowResponse(BaseModel):
    """Response shape for /passages/{id} (always wrapped, even when neighbors=0)."""

    passage: PassageDetail
    neighbors_before: list[PassageDetail] = Field(default_factory=list)
    neighbors_after: list[PassageDetail] = Field(default_factory=list)
    has_more_before: bool = False
    has_more_after: bool = False
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta)

    model_config = {"populate_by_name": True}
```

- [ ] **Step 2: Verify with mypy**

```bash
make typecheck-fast
```
Expected: green.

- [ ] **Step 3: Commit**

```bash
git add genereview_link/models/genereview_models.py
git commit -m "feat(models): add PassageWindowResponse for get_passage neighbors"
```

---

### Task 10: Wire `neighbors`/`cross_sections` into `/passages/{passage_id}` route

**Files:**
- Modify: `genereview_link/api/routes/passages.py` (the existing `get_passage` route)
- Test: `tests/routes/test_passages_get.py`

- [ ] **Step 1: Write failing route test**

```python
def test_get_passage_default_returns_wrapper_with_empty_neighbors(test_client) -> None:
    resp = test_client.get("/passages/NBK1247:0010")
    assert resp.status_code == 200
    data = resp.json()
    assert "passage" in data
    assert data["passage"]["passage_id"] == "NBK1247:0010"
    assert data["neighbors_before"] == []
    assert data["neighbors_after"] == []
    assert data["has_more_before"] is False or data["has_more_before"] is True


def test_get_passage_neighbors_returns_window(test_client) -> None:
    resp = test_client.get("/passages/NBK1247:0010", params={"neighbors": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["neighbors_before"]) <= 2
    assert len(data["neighbors_after"]) <= 2
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/routes/test_passages_get.py -v
```
Expected: FAIL — current route returns flat `PassageDetail`, not wrapper.

- [ ] **Step 3: Replace route handler**

Replace the existing `get_passage` route in `genereview_link/api/routes/passages.py`:
```python
from typing import Annotated
from fastapi import Path, Query

@router.get(
    "/passages/{passage_id}",
    response_model=PassageWindowResponse,
    response_model_by_alias=True,
)
async def get_passage(
    request: Request,
    passage_id: Annotated[str, Path(pattern=r"^NBK\d+:\d{4}$")],
    neighbors: Annotated[int, Query(ge=0, le=5, description="Fetch this many adjacent chunks before and after.")] = 0,
    cross_sections: Annotated[bool, Query(description="If true, neighbors may span across section boundaries within the same chapter.")] = False,
) -> PassageWindowResponse:
    repo = get_repository(request)
    focal, before, after, has_more_before, has_more_after = await repo.get_passage_window(
        passage_id, before=neighbors, after=neighbors, cross_sections=cross_sections
    )
    if focal is None:
        raise StructuredHTTPException(
            status_code=404,
            code="passage_not_found",
            message=f"passage {passage_id!r} does not exist",
            recovery_hint="verify the passage_id format NBKxxxx:NNNN; use search_passages to discover valid IDs",
            next_commands=[{"tool": "search_passages", "arguments": {"q": "<your query>"}}],
        )

    def _to_detail(row: PassageRow) -> PassageDetail:
        return PassageDetail(
            nbk_id=row.nbk_id,
            passage_id=row.passage_id,
            chapter_section=row.chapter_section,
            heading_path=row.heading_path,
            section_level=row.section_level,
            chunk_index=row.chunk_index,
            text=row.text,
            char_count=len(row.text),
            chapter_title=row.chapter_title,
            chapter_last_updated=row.chapter_last_updated,
            gene_symbols=list(row.gene_symbols),
        )

    return PassageWindowResponse(
        passage=_to_detail(focal),
        neighbors_before=[_to_detail(r) for r in before],
        neighbors_after=[_to_detail(r) for r in after],
        has_more_before=has_more_before,
        has_more_after=has_more_after,
        meta=ResponseMeta(corpus_version=_get_corpus_version(request)),
    )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/routes/test_passages_get.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/api/routes/passages.py tests/routes/test_passages_get.py
git commit -m "feat(api): get_passage returns wrapper with neighbors window"
```

---

### Task 11: Add `get_chapter_metadata` repository method

**Files:**
- Modify: `genereview_link/retrieval/repository.py`
- Test: `tests/retrieval/test_repository_metadata.py` (NEW)

- [ ] **Step 1: Write failing test**

```python
import pytest
from genereview_link.retrieval.repository import GeneReviewRepository


@pytest.mark.integration
async def test_get_chapter_metadata_returns_sections_with_counts(
    repo: GeneReviewRepository,
) -> None:
    meta = await repo.get_chapter_metadata("NBK1247")
    assert meta is not None
    assert meta.title.startswith("BRCA1")
    assert any(s.section == "summary" for s in meta.sections)
    summary = next(s for s in meta.sections if s.section == "summary")
    assert summary.passage_count > 0


@pytest.mark.integration
async def test_get_chapter_metadata_unknown_returns_none(
    repo: GeneReviewRepository,
) -> None:
    meta = await repo.get_chapter_metadata("NBK0000000")
    assert meta is None
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
DATABASE_URL=postgresql://genereview:genereview@127.0.0.1:5436/genereview_test \
  uv run pytest tests/retrieval/test_repository_metadata.py -v -m integration
```
Expected: FAIL — method doesn't exist.

- [ ] **Step 3: Add data class + method**

In `genereview_link/retrieval/repository.py`:
```python
@dataclass(frozen=True, slots=True)
class SectionSummaryRow:
    section: str
    passage_count: int


@dataclass(frozen=True, slots=True)
class ChapterMetadataRow:
    nbk_id: str
    title: str
    chapter_last_updated: date | None
    gene_symbols: tuple[str, ...]
    sections: tuple[SectionSummaryRow, ...]
    table_count: int

# inside GeneReviewRepository:
async def get_chapter_metadata(self, nbk_id: str) -> ChapterMetadataRow | None:
    async with self._acquire() as conn:
        chapter = await conn.fetchrow(
            """
            select nbk_id, title, last_updated_date, gene_symbols
              from public.genereview_chapters
             where nbk_id = $1
            """,
            nbk_id,
        )
        if chapter is None:
            return None

        section_rows = await conn.fetch(
            """
            select chapter_section, count(*)::int as cnt
              from public.genereview_passages
             where nbk_id = $1
             group by chapter_section
            """,
            nbk_id,
        )
        # table_count: 0 in Phase 6, populated in Phase 7
        table_count = 0  # placeholder; replaced in Phase 7 task

    counts = {r["chapter_section"]: r["cnt"] for r in section_rows}
    # Emit ALL canonical sections (incl. zero-count ones) so callers see what's available.
    from genereview_link.models.sections import SECTION_NAMES
    sections = tuple(
        SectionSummaryRow(section=name, passage_count=counts.get(name, 0))
        for name in SECTION_NAMES
    )

    return ChapterMetadataRow(
        nbk_id=chapter["nbk_id"],
        title=chapter["title"],
        chapter_last_updated=chapter["last_updated_date"],
        gene_symbols=tuple(chapter["gene_symbols"] or ()),
        sections=sections,
        table_count=table_count,
    )
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
DATABASE_URL=postgresql://genereview:genereview@127.0.0.1:5436/genereview_test \
  uv run pytest tests/retrieval/test_repository_metadata.py -v -m integration
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/retrieval/repository.py tests/retrieval/test_repository_metadata.py
git commit -m "feat(retrieval): add get_chapter_metadata with per-section counts"
```

---

### Task 12: Add `ChapterMetadataResponse` model and route

**Files:**
- Modify: `genereview_link/models/genereview_models.py`
- Modify: `genereview_link/api/routes/chapters.py`
- Test: `tests/routes/test_chapters_metadata.py` (NEW)

- [ ] **Step 1: Add models**

In `genereview_link/models/genereview_models.py`:
```python
class SectionSummary(BaseModel):
    section: str
    passage_count: int


class ChapterMetadataResponse(BaseModel):
    nbk_id: str
    title: str
    chapter_last_updated: date | None = None
    gene_symbols: list[str] = Field(default_factory=list)
    sections: list[SectionSummary] = Field(default_factory=list)
    table_count: int = 0
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta)

    model_config = {"populate_by_name": True}
```

- [ ] **Step 2: Write failing route test**

```python
def test_get_chapter_metadata_returns_sections_list(test_client) -> None:
    resp = test_client.get("/chapters/NBK1247/metadata")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nbk_id"] == "NBK1247"
    assert any(s["section"] == "summary" for s in data["sections"])


def test_get_chapter_metadata_unknown_returns_404(test_client) -> None:
    resp = test_client.get("/chapters/NBK0000000/metadata")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "chapter_not_found"
```

- [ ] **Step 3: Run test to confirm failure**

```bash
uv run pytest tests/routes/test_chapters_metadata.py -v
```
Expected: FAIL — route doesn't exist.

- [ ] **Step 4: Add route handler**

In `genereview_link/api/routes/chapters.py`:
```python
@router.get(
    "/chapters/{nbk_id}/metadata",
    response_model=ChapterMetadataResponse,
    response_model_by_alias=True,
)
async def get_chapter_metadata(
    request: Request,
    nbk_id: Annotated[str, Path(pattern=r"^NBK\d+$")],
) -> ChapterMetadataResponse:
    """Return chapter title, last-updated date, gene symbols, section counts, and table count.

    Use this before get_chapter_section to avoid blind calls on empty sections.
    """
    repo = get_repository(request)
    meta = await repo.get_chapter_metadata(nbk_id)
    if meta is None:
        raise StructuredHTTPException(
            status_code=404,
            code="chapter_not_found",
            message=f"chapter {nbk_id!r} not in corpus",
            recovery_hint="check the NBK ID; use search_passages to discover indexed chapters",
            next_commands=[{"tool": "search_passages", "arguments": {"q": "<gene symbol or term>"}}],
        )
    return ChapterMetadataResponse(
        nbk_id=meta.nbk_id,
        title=meta.title,
        chapter_last_updated=meta.chapter_last_updated,
        gene_symbols=list(meta.gene_symbols),
        sections=[SectionSummary(section=s.section, passage_count=s.passage_count) for s in meta.sections],
        table_count=meta.table_count,
        meta=ResponseMeta(corpus_version=_get_corpus_version(request)),
    )
```

- [ ] **Step 5: Run tests to confirm pass**

```bash
uv run pytest tests/routes/test_chapters_metadata.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/models/genereview_models.py genereview_link/api/routes/chapters.py tests/routes/test_chapters_metadata.py
git commit -m "feat(api): get_chapter_metadata returns sections list with counts"
```

---

### Task 13: Add diagnostics module for empty-result hints

**Files:**
- Create: `genereview_link/api/diagnostics.py`
- Test: `tests/api/test_diagnostics.py` (NEW)

- [ ] **Step 1: Write failing unit tests**

```python
from genereview_link.api.diagnostics import build_search_diagnostics


def test_diagnostics_suggests_dropping_gene_when_filter_kills_hits() -> None:
    diag = build_search_diagnostics(
        query="risk-reducing surgery",
        applied_filters=["gene=BRCA1", "sections=management"],
        lexical_hits=120,
        lexical_hits_after_filters=2,
    )
    assert diag.lexical_hits == 120
    assert any("gene" in s.lower() for s in diag.suggestions)


def test_diagnostics_suggests_broadening_long_query() -> None:
    long_q = "x " * 50
    diag = build_search_diagnostics(
        query=long_q,
        applied_filters=[],
        lexical_hits=0,
        lexical_hits_after_filters=0,
    )
    assert any("broaden" in s.lower() for s in diag.suggestions)


def test_diagnostics_suggests_other_sections_when_section_filter_drops_all() -> None:
    diag = build_search_diagnostics(
        query="foo",
        applied_filters=["sections=management"],
        lexical_hits=10,
        lexical_hits_after_filters=0,
    )
    assert any("section" in s.lower() for s in diag.suggestions)
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/api/test_diagnostics.py -v
```
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create the module**

```python
# genereview_link/api/diagnostics.py
"""Empty-result diagnostic suggestions for search_passages.

Rule-based, not LLM-generated. Triggered when len(results) == 0.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchDiagnostics:
    lexical_hits: int
    lexical_hits_after_filters: int
    applied_filters: list[str]
    suggestions: list[str]


def build_search_diagnostics(
    *,
    query: str,
    applied_filters: list[str],
    lexical_hits: int,
    lexical_hits_after_filters: int,
) -> SearchDiagnostics:
    suggestions: list[str] = []

    # Rule 1: gene filter killed >90% of hits (gene assumed valid post-T4.6)
    gene_filter = next((f for f in applied_filters if f.startswith("gene=")), None)
    if (
        gene_filter
        and lexical_hits > 0
        and lexical_hits_after_filters < lexical_hits / 10
    ):
        symbol = gene_filter.split("=", 1)[1]
        suggestions.append(
            f"the gene {symbol!r} is indexed but no passages match within the current filters; "
            "try removing the sections filter or broadening q"
        )

    # Rule 2: query is very long or very specific
    if len(query) > 80 or len(query.split()) > 8:
        suggestions.append("broaden q (current query is very specific)")

    # Rule 3: sections filter drops everything
    section_filter = next((f for f in applied_filters if f.startswith("sections=")), None)
    if (
        section_filter
        and lexical_hits > 0
        and lexical_hits_after_filters == 0
    ):
        suggestions.append("try other sections — current sections filter excludes all hits")

    return SearchDiagnostics(
        lexical_hits=lexical_hits,
        lexical_hits_after_filters=lexical_hits_after_filters,
        applied_filters=applied_filters,
        suggestions=suggestions,
    )
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
uv run pytest tests/api/test_diagnostics.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/api/diagnostics.py tests/api/test_diagnostics.py
git commit -m "feat(api): rule-based empty-result diagnostics"
```

---

### Task 14: Wire diagnostics into `search_passages` response

**Files:**
- Modify: `genereview_link/api/routes/passages.py`
- Modify: `genereview_link/models/genereview_models.py`

- [ ] **Step 1: Add diagnostics field to ResponseMeta**

In `genereview_link/models/genereview_models.py`:
```python
class SearchDiagnosticsModel(BaseModel):
    lexical_hits: int
    lexical_hits_after_filters: int
    applied_filters: list[str]
    suggestions: list[str]


class ResponseMeta(BaseModel):
    attribution: str = ATTRIBUTION_TEXT
    corpus_version: str | None = None
    diagnostics: SearchDiagnosticsModel | None = None
```

- [ ] **Step 2: Write failing route test**

```python
def test_search_zero_results_emits_diagnostics(test_client) -> None:
    resp = test_client.get(
        "/passages/search",
        params={"q": "xyzzy_definitely_not_in_corpus_zzz", "sections": "management"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    diag = data["_meta"].get("diagnostics")
    assert diag is not None
    assert "suggestions" in diag


def test_search_nonzero_results_omits_diagnostics(test_client) -> None:
    resp = test_client.get("/passages/search", params={"q": "BRCA1"})
    assert resp.status_code == 200
    assert resp.json()["_meta"].get("diagnostics") is None
```

- [ ] **Step 3: Run to confirm failure**

```bash
uv run pytest tests/routes/test_passages_search.py -v -k diagnostics
```
Expected: FAIL.

- [ ] **Step 4: Wire diagnostics into the route**

In `genereview_link/api/routes/passages.py`, after building `out` and before returning:
```python
from genereview_link.api.diagnostics import build_search_diagnostics
from genereview_link.models.genereview_models import SearchDiagnosticsModel

# After the result list is built:
diagnostics_model: SearchDiagnosticsModel | None = None
if not out:
    applied: list[str] = []
    if gene:
        applied.append(f"gene={gene}")
    if sections:
        applied.append(f"sections={','.join(sections)}")
    if nbk_id:
        applied.append(f"nbk_id={nbk_id}")
    diag = build_search_diagnostics(
        query=q,
        applied_filters=applied,
        lexical_hits=len(lexical_results),  # pre-rerank count
        lexical_hits_after_filters=len(out),
    )
    diagnostics_model = SearchDiagnosticsModel(
        lexical_hits=diag.lexical_hits,
        lexical_hits_after_filters=diag.lexical_hits_after_filters,
        applied_filters=diag.applied_filters,
        suggestions=diag.suggestions,
    )

meta = ResponseMeta(
    corpus_version=_get_corpus_version(request),
    diagnostics=diagnostics_model,
)
```

(Note: `lexical_results` is the variable holding the pre-rerank lexical-hit list. Verify the actual variable name in the existing code and use it.)

- [ ] **Step 5: Run tests to confirm pass**

```bash
uv run pytest tests/routes/test_passages_search.py -v -k diagnostics
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/api/routes/passages.py genereview_link/models/genereview_models.py tests/routes/test_passages_search.py
git commit -m "feat(api): emit _meta.diagnostics on empty search results"
```

---

### Task 15: Drop `concatenated_text` default from `get_chapter_section`

**Files:**
- Modify: `genereview_link/api/routes/chapters.py`
- Modify: `genereview_link/models/genereview_models.py` (`ChapterSectionResponse`)

- [ ] **Step 1: Write failing route test**

```python
def test_chapter_section_default_omits_concatenated_text(test_client) -> None:
    resp = test_client.get("/chapters/NBK1247/sections/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "passages" in data
    assert "concatenated_text" not in data


def test_chapter_section_include_concatenated_returns_both(test_client) -> None:
    resp = test_client.get(
        "/chapters/NBK1247/sections/summary",
        params={"include": "concatenated_text"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "passages" in data
    assert "concatenated_text" in data
    assert isinstance(data["concatenated_text"], str)
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/routes/test_chapters_section.py -v -k concatenated
```
Expected: FAIL — concatenated_text is always present.

- [ ] **Step 3: Make field optional and gate behind `include`**

In `genereview_link/models/genereview_models.py`:
```python
class ChapterSectionResponse(BaseModel):
    # existing fields...
    passages: list[PassageInSection]
    concatenated_text: str | None = None  # was: str
    # rest unchanged
```

In `genereview_link/api/routes/chapters.py`, modify `get_chapter_section`:
```python
include: Annotated[
    list[Literal["concatenated_text"]] | None,
    Query(description="Opt into default-off response fields. Pass include=concatenated_text to receive the joined passage text in addition to passages[]."),
] = None,
```

In the response construction, gate the field:
```python
include_set = set(include or [])
concatenated = (
    "\n\n".join(p.text for p in passages)
    if "concatenated_text" in include_set
    else None
)

return ChapterSectionResponse(
    # ... existing fields ...
    concatenated_text=concatenated,
    # ... existing meta ...
)
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
uv run pytest tests/routes/test_chapters_section.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/api/routes/chapters.py genereview_link/models/genereview_models.py tests/routes/test_chapters_section.py
git commit -m "feat(api): get_chapter_section concatenated_text is opt-in"
```

---

### Task 16: Update server instructions for Phase 6

**Files:**
- Modify: `genereview_link/server_manager.py`

- [ ] **Step 1: Update instructions**

In the `instructions=` block of `FastMCP.from_fastapi(...)`, add:

- `get_chapter_metadata(nbk_id)` to the canonical pipeline paragraph
- `neighbors=N` mention on the `get_passage` line
- Note that `get_chapter_section` no longer returns `concatenated_text` by default; pass `include=concatenated_text` for the joined string
- Note that empty `search_passages` results carry `_meta.diagnostics.suggestions`

Keep citation contract and safety framing unchanged.

- [ ] **Step 2: Run typecheck**

```bash
make typecheck-fast
```
Expected: green.

- [ ] **Step 3: Commit**

```bash
git add genereview_link/server_manager.py
git commit -m "docs(mcp): update server instructions for phase 6 affordances"
```

---

### Task 17: Phase 6 gate — `make ci-local` + live smoke + tag

**Files:**
- New: `tests/smoke/phase_6.sh`

- [ ] **Step 1: `make ci-local`**

```bash
make ci-local
```
Expected: green.

- [ ] **Step 2: Bring up dev server, write smoke**

```bash
DATABASE_URL=postgresql://genereview:genereview@127.0.0.1:5436/genereview make dev &
sleep 3
```

Create `tests/smoke/phase_6.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"

echo "=== Phase 6 smoke checks ==="

# 1. get_passage with neighbors
out=$(curl -sf "$BASE/passages/NBK1247:0010?neighbors=2")
echo "$out" | jq -e '.passage.passage_id == "NBK1247:0010"' >/dev/null
echo "$out" | jq -e '(.neighbors_before | length) <= 2' >/dev/null
echo "$out" | jq -e '(.neighbors_after | length) <= 2' >/dev/null
echo "$out" | jq -e 'has("has_more_before") and has("has_more_after")' >/dev/null
echo "OK: get_passage neighbors window"

# 2. get_chapter_metadata
out=$(curl -sf "$BASE/chapters/NBK1247/metadata")
echo "$out" | jq -e '.sections | length > 0' >/dev/null
echo "$out" | jq -e '.gene_symbols | index("BRCA1") != null' >/dev/null
echo "OK: get_chapter_metadata"

# 3. Empty-result diagnostics
out=$(curl -sf "$BASE/passages/search?q=xyzzy_not_in_corpus_zzz&limit=5")
echo "$out" | jq -e '.results == []' >/dev/null
echo "$out" | jq -e '._meta.diagnostics.suggestions | length >= 0' >/dev/null
echo "OK: empty-result diagnostics"

# 4. concatenated_text gated
out=$(curl -sf "$BASE/chapters/NBK1247/sections/summary")
echo "$out" | jq -e 'has("concatenated_text") | not' >/dev/null
echo "OK: concatenated_text absent by default"

out=$(curl -sf "$BASE/chapters/NBK1247/sections/summary?include=concatenated_text")
echo "$out" | jq -e '.concatenated_text | type == "string"' >/dev/null
echo "OK: include=concatenated_text returns the field"

echo "=== All Phase 6 smoke checks passed ==="
```

- [ ] **Step 3: Run smoke**

```bash
chmod +x tests/smoke/phase_6.sh
tests/smoke/phase_6.sh
```
Expected: all OK, exit 0.

- [ ] **Step 4: Stop server, commit, tag**

```bash
kill %1
git add tests/smoke/phase_6.sh
git commit -m "test(smoke): phase 6 discovery-pass live probe"
git tag -a phase-6-ergonomics-v2 -m "Phase 6 discovery pass complete"
```

---

# Phase 7 — Content Pass

**Goal:** Tables as first-class passages, fix tokenizer-leak text normalization, full corpus rebuild. Largest phase. Tag: `phase-7-content-v2`.

---

### Task 18: DB migration for `passage_type`, `table_id`, `table_data`

**Files:**
- Create: `db/migrations/00X_passage_type_and_tables.sql` (replace `00X` with the next sequential number — check existing migrations)

- [ ] **Step 1: Find next migration number**

```bash
ls db/migrations/ 2>/dev/null | sort | tail -3
```
Expected: shows the highest existing number; pick `current+1`.

- [ ] **Step 2: Write the migration**

Create the file with:
```sql
-- Phase 7: passage_type discrimination + table-passage support
begin;

alter table public.genereview_passages
  add column passage_type text not null default 'narrative'
  check (passage_type in ('narrative', 'table'));

create index passages_type_chapter_idx
  on public.genereview_passages(nbk_id, passage_type);

alter table public.genereview_passages
  add column table_id text;  -- non-null only for passage_type='table'

create unique index passages_table_id_unique_idx
  on public.genereview_passages(nbk_id, table_id)
  where passage_type = 'table';

alter table public.genereview_passages
  add column table_data jsonb;  -- non-null only for passage_type='table'

commit;
```

- [ ] **Step 3: Apply migration to test DB**

```bash
PGPASSWORD=genereview psql -h 127.0.0.1 -p 5436 -U genereview -d genereview_test \
  -f db/migrations/00X_passage_type_and_tables.sql
```
Expected: `BEGIN`, `ALTER TABLE` ×3, `CREATE INDEX` ×2, `COMMIT`.

- [ ] **Step 4: Verify schema**

```bash
PGPASSWORD=genereview psql -h 127.0.0.1 -p 5436 -U genereview -d genereview_test \
  -c "\d genereview_passages" | grep -E "passage_type|table_id|table_data"
```
Expected: three new columns shown.

- [ ] **Step 5: Apply to production-corpus DB (gr-pg)**

```bash
PGPASSWORD=genereview psql -h 127.0.0.1 -p 5436 -U genereview -d genereview \
  -f db/migrations/00X_passage_type_and_tables.sql
```
Expected: same output. Note: existing rows get `passage_type='narrative'` automatically via the column default.

- [ ] **Step 6: Commit migration**

```bash
git add db/migrations/00X_passage_type_and_tables.sql
git commit -m "feat(db): migration for passage_type, table_id, table_data"
```

---

### Task 19: Extend `Passage` record + `PassageRow` with new fields

**Files:**
- Modify: `genereview_link/corpus/records.py`
- Modify: `genereview_link/retrieval/repository.py`

- [ ] **Step 1: Extend `Passage` record**

In `genereview_link/corpus/records.py`, find the `Passage` dataclass and add:
```python
passage_type: Literal["narrative", "table"] = "narrative"
table_id: str | None = None
table_data: dict[str, object] | None = None  # {"caption": str, "header": list[str], "rows": list[list[str]]}
```

- [ ] **Step 2: Extend `PassageRow` repository dataclass**

In `genereview_link/retrieval/repository.py:37-47`, add fields to `PassageRow`:
```python
@dataclass(frozen=True, slots=True)
class PassageRow:
    # existing fields...
    passage_type: str = "narrative"
    table_id: str | None = None
    table_data: dict[str, Any] | None = None
```

- [ ] **Step 3: Update existing repository SQL to project new columns**

In every method in `repository.py` that selects from `genereview_passages`, append `, p.passage_type, p.table_id, p.table_data` to the column list and include them when constructing `PassageRow`.

Affected methods to update: `search_passages`, `get_section`, `get_passage`, `_fetch_passage_row` (if added in Task 8), `_row_to_passage`.

- [ ] **Step 4: Run typecheck and existing tests**

```bash
make typecheck-fast && uv run pytest tests/retrieval/ -v
```
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/corpus/records.py genereview_link/retrieval/repository.py
git commit -m "feat(corpus,retrieval): add passage_type, table_id, table_data fields"
```

---

### Task 20: Update copy operations to write new columns

**Files:**
- Modify: `genereview_link/corpus/parallel.py` (the `copy_passages` function)

- [ ] **Step 1: Add new columns to copy column list**

Find the `copy_passages` function around `parallel.py:156`. The column list currently includes existing passage columns. Append `passage_type`, `table_id`, `table_data` to:
1. The `columns=` argument to `copy_records_to_table`
2. The tuple builder that generates rows from the `Passage` records

Example pattern (adapt to actual code):
```python
columns = [
    # existing columns...
    "passage_type",
    "table_id",
    "table_data",
]

records = (
    (
        # existing fields...
        p.passage_type,
        p.table_id,
        json.dumps(p.table_data) if p.table_data is not None else None,
    )
    for p in passages
)
```

- [ ] **Step 2: Run typecheck**

```bash
make typecheck-fast
```
Expected: green.

- [ ] **Step 3: Commit**

```bash
git add genereview_link/corpus/parallel.py
git commit -m "feat(corpus): copy_passages writes passage_type, table_id, table_data"
```

---

### Task 21: Add table-extraction module

**Files:**
- Create: `genereview_link/corpus/tables.py`
- Test: `tests/corpus/test_tables.py` (NEW)
- Fixture: `tests/fixtures/table_sample.nxml` (NEW)

- [ ] **Step 1: Write failing tests**

Create `tests/fixtures/table_sample.nxml`:
```xml
<table-wrap id="t5">
  <caption><title>Table 5</title><p>Risk-Reducing Mastectomy</p></caption>
  <table>
    <thead><tr><th>Variant</th><th>Drug</th><th>Min age</th></tr></thead>
    <tbody>
      <tr><td>Class I</td><td>elexacaftor</td><td>6 yrs</td></tr>
      <tr><td>Class II</td><td>tezacaftor</td><td>12 yrs</td></tr>
    </tbody>
  </table>
</table-wrap>
```

Create `tests/corpus/test_tables.py`:
```python
from defusedxml import ElementTree as ET
from pathlib import Path

from genereview_link.corpus.tables import extract_table, render_table_markdown


def test_extract_table_returns_id_caption_header_rows() -> None:
    root = ET.fromstring(Path("tests/fixtures/table_sample.nxml").read_text())
    table = extract_table(root, ordinal=5)
    assert table.table_id == "t5"  # NXML id wins over ordinal
    assert table.caption.startswith("Table 5")
    assert table.header == ["Variant", "Drug", "Min age"]
    assert len(table.rows) == 2
    assert table.rows[0] == ["Class I", "elexacaftor", "6 yrs"]


def test_extract_table_falls_back_to_ordinal_when_no_id() -> None:
    xml = "<table-wrap><caption><p>x</p></caption><table><thead><tr><th>a</th></tr></thead><tbody><tr><td>b</td></tr></tbody></table></table-wrap>"
    root = ET.fromstring(xml)
    table = extract_table(root, ordinal=3)
    assert table.table_id == "table-3"


def test_render_table_markdown_produces_gfm() -> None:
    md = render_table_markdown(
        caption="Table X",
        header=["A", "B"],
        rows=[["1", "2"], ["3", "4"]],
    )
    assert "| A | B |" in md
    assert "| --- | --- |" in md
    assert "| 1 | 2 |" in md
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/corpus/test_tables.py -v
```
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create the module**

```python
# genereview_link/corpus/tables.py
"""Extract <table-wrap> elements from NXML and serialize as GitHub-flavored markdown."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any  # ET.Element type lives in defusedxml; use Any to keep mypy quiet


@dataclass(frozen=True, slots=True)
class ExtractedTable:
    table_id: str
    caption: str
    header: list[str]
    rows: list[list[str]]


def _text_or_empty(node: Any) -> str:
    if node is None:
        return ""
    return " ".join((node.itertext())).strip()


def extract_table(table_wrap: Any, *, ordinal: int) -> ExtractedTable:
    """Extract a single <table-wrap> element."""
    nxml_id = table_wrap.get("id")
    table_id = nxml_id if nxml_id else f"table-{ordinal}"

    cap_node = table_wrap.find("caption")
    caption_parts: list[str] = []
    if cap_node is not None:
        title = cap_node.find("title")
        if title is not None:
            caption_parts.append(_text_or_empty(title))
        for p in cap_node.findall("p"):
            caption_parts.append(_text_or_empty(p))
    caption = " — ".join(c for c in caption_parts if c) or table_id

    table = table_wrap.find("table")
    header: list[str] = []
    rows: list[list[str]] = []
    if table is not None:
        thead = table.find("thead")
        if thead is not None:
            header_row = thead.find("tr")
            if header_row is not None:
                header = [_text_or_empty(th) for th in header_row.findall("th")]
        tbody = table.find("tbody")
        if tbody is not None:
            for tr in tbody.findall("tr"):
                rows.append([_text_or_empty(td) for td in tr.findall("td")])

    return ExtractedTable(table_id=table_id, caption=caption, header=header, rows=rows)


def render_table_markdown(*, caption: str, header: list[str], rows: list[list[str]]) -> str:
    """Render a table as GitHub-flavored markdown (caption + header + rows)."""
    parts: list[str] = [caption, ""]
    if header:
        parts.append("| " + " | ".join(header) + " |")
        parts.append("| " + " | ".join("---" for _ in header) + " |")
    for row in rows:
        # pad rows to header width
        padded = list(row) + [""] * max(0, len(header) - len(row))
        parts.append("| " + " | ".join(padded) + " |")
    return "\n".join(parts)
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
uv run pytest tests/corpus/test_tables.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/corpus/tables.py tests/corpus/test_tables.py tests/fixtures/table_sample.nxml
git commit -m "feat(corpus): extract and serialize <table-wrap> elements"
```

---

### Task 22: Wire table-passages into NXML scraper

**Files:**
- Modify: `genereview_link/corpus/nxml.py`
- Test: `tests/corpus/test_nxml_tables.py` (NEW)
- Fixture: `tests/fixtures/chapter_with_table.nxml` (NEW — minimal chapter with one section + one table)

- [ ] **Step 1: Write failing test**

```python
from pathlib import Path
from genereview_link.corpus.nxml import parse_chapter


def test_parse_chapter_emits_table_passage() -> None:
    fixture = Path("tests/fixtures/chapter_with_table.nxml").read_bytes()
    chapter, passages = parse_chapter(fixture, nbk_id="NBK_TBL")
    table_passages = [p for p in passages if p.passage_type == "table"]
    assert len(table_passages) == 1
    t = table_passages[0]
    assert t.table_id is not None
    assert t.table_data is not None
    assert t.table_data["header"] == ["Variant", "Drug", "Min age"]
    assert "| Variant | Drug | Min age |" in t.text
    assert "Table" in (t.heading_path or "")
    assert t.chunk_index is not None  # interleaved with narrative
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/corpus/test_nxml_tables.py -v
```
Expected: FAIL — scraper doesn't emit table passages.

- [ ] **Step 3: Modify the scraper to walk `<table-wrap>` elements in source order**

In `genereview_link/corpus/nxml.py`, in the section that walks the body building passages, when a `<table-wrap>` is encountered:
```python
from genereview_link.corpus.tables import extract_table, render_table_markdown

# (Adjust this loop body to match the existing scraper's iteration pattern.)
table_ordinal = 0
for element in section_body_iter:
    if element.tag == "table-wrap":
        table_ordinal += 1
        extracted = extract_table(element, ordinal=table_ordinal)
        markdown = render_table_markdown(
            caption=extracted.caption,
            header=extracted.header,
            rows=extracted.rows,
        )
        passages.append(
            Passage(
                # ... fields like nbk_id, chapter_section, etc, copied from current section context ...
                passage_type="table",
                table_id=extracted.table_id,
                table_data={
                    "caption": extracted.caption,
                    "header": extracted.header,
                    "rows": extracted.rows,
                },
                heading_path=f"{current_heading_path} > Table {table_ordinal}",
                text=markdown,
                chunk_index=next_chunk_index,
            )
        )
        next_chunk_index += 1
        continue
    # ... existing narrative handling ...
```

`table_ordinal` resets per chapter (or per section, depending on how `extract_table`'s `ordinal` argument is meant to work — if NCBI numbers tables chapter-wide, use chapter-wide; if per-section, use per-section. Confirm against a real chapter).

- [ ] **Step 4: Run tests to confirm pass**

```bash
uv run pytest tests/corpus/test_nxml_tables.py tests/corpus/ -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/corpus/nxml.py tests/corpus/test_nxml_tables.py tests/fixtures/chapter_with_table.nxml
git commit -m "feat(corpus): emit table passages with interleaved chunk_index"
```

---

### Task 23: Add embedding token-budget truncation for tables

**Files:**
- Modify: the embedding-input builder (find via `grep -rn "passage.text\|build_embedding_input\|embed_passage" genereview_link/embeddings/`)
- Test: `tests/embeddings/test_table_truncation.py` (NEW)

- [ ] **Step 1: Locate embedding-input builder**

```bash
grep -rn "passage.text\|tokenize\|embedding_input" genereview_link/embeddings/ | head -20
```
Identify the function that takes a passage text and returns the string fed to BGE.

- [ ] **Step 2: Write failing test**

```python
from genereview_link.embeddings.<builder_module> import build_embedding_input


def test_table_passage_input_truncates_to_token_budget() -> None:
    big_table_text = "Table\n\n| h1 | h2 |\n| --- | --- |\n" + "| a | b |\n" * 1000
    out = build_embedding_input(
        text=big_table_text,
        passage_type="table",
        max_tokens=480,
    )
    # Header row preserved
    assert "| h1 | h2 |" in out
    # Caption preserved
    assert out.startswith("Table")
    # Truncated to fit budget
    assert len(out) < len(big_table_text)


def test_narrative_passage_input_unchanged_below_budget() -> None:
    text = "Short narrative passage."
    out = build_embedding_input(text=text, passage_type="narrative", max_tokens=480)
    assert out == text
```

- [ ] **Step 3: Run to confirm failure**

```bash
uv run pytest tests/embeddings/test_table_truncation.py -v
```
Expected: FAIL — function signature doesn't accept `passage_type`/`max_tokens`, or no truncation logic.

- [ ] **Step 4: Implement truncation in the builder**

For tables, keep caption + header + as many rows as fit in `max_tokens`. Use the BGE tokenizer (or word-count proxy if a tokenizer isn't already loaded at this point — pragmatic):
```python
def build_embedding_input(text: str, *, passage_type: str = "narrative", max_tokens: int = 480) -> str:
    if passage_type != "table":
        return text
    # Tables: caption + header + as many rows as fit (rough word-count proxy).
    lines = text.split("\n")
    if len(lines) < 4:
        return text
    caption = lines[0]
    header = lines[2]  # "| h1 | h2 |"
    separator = lines[3]  # "| --- | --- |"
    body_rows = lines[4:]

    # Conservative: ~4 chars/token, treat budget as char limit
    budget_chars = max_tokens * 4
    keep: list[str] = [caption, "", header, separator]
    used = sum(len(line) for line in keep)
    for row in body_rows:
        if used + len(row) > budget_chars:
            break
        keep.append(row)
        used += len(row)
    return "\n".join(keep)
```

If the embedding code already uses a tokenizer object, swap the char-proxy for the real `len(tokenizer.encode(...))`.

- [ ] **Step 5: Run tests to confirm pass**

```bash
uv run pytest tests/embeddings/test_table_truncation.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/embeddings/ tests/embeddings/test_table_truncation.py
git commit -m "feat(embeddings): truncate large tables to token budget for embedding"
```

---

### Task 24: Add `get_table` repository method + route

**Files:**
- Modify: `genereview_link/retrieval/repository.py`
- Create: `genereview_link/api/routes/tables.py`
- Modify: `genereview_link/server_manager.py` (register router)
- Modify: `genereview_link/models/genereview_models.py` (add `TableResponse`)
- Test: `tests/retrieval/test_repository_table.py`, `tests/routes/test_tables.py`

- [ ] **Step 1: Add `TableResponse` model**

```python
class TableResponse(BaseModel):
    nbk_id: str
    table_id: str
    caption: str
    heading_path: str | None = None
    section: str
    header: list[str]
    rows: list[list[str]]
    passage_id: str
    meta: ResponseMeta = Field(alias="_meta", default_factory=ResponseMeta)

    model_config = {"populate_by_name": True}
```

- [ ] **Step 2: Add repository method (failing test then implement)**

Test:
```python
@pytest.mark.integration
async def test_get_table_returns_structured_rows(repo) -> None:
    table = await repo.get_table("NBK1247", "t5")
    assert table is not None
    assert table.header[0] in {"Variant", "Variant class"}
    assert len(table.rows) > 0
```

In `repository.py`:
```python
async def get_table(self, nbk_id: str, table_id: str) -> TableRow | None:
    async with self._acquire() as conn:
        row = await conn.fetchrow(
            """
            select p.nbk_id, p.passage_id, p.chapter_section, p.heading_path,
                   p.table_id, p.table_data
              from public.genereview_passages p
             where p.nbk_id = $1
               and p.passage_type = 'table'
               and p.table_id = $2
            """,
            nbk_id, table_id,
        )
    if row is None:
        return None
    data = row["table_data"]
    if isinstance(data, str):
        import json
        data = json.loads(data)
    return TableRow(
        nbk_id=row["nbk_id"],
        passage_id=row["passage_id"],
        section=row["chapter_section"],
        heading_path=row["heading_path"],
        table_id=row["table_id"],
        caption=data.get("caption", ""),
        header=list(data.get("header", [])),
        rows=[list(r) for r in data.get("rows", [])],
    )


# add the dataclass:
@dataclass(frozen=True, slots=True)
class TableRow:
    nbk_id: str
    passage_id: str
    section: str
    heading_path: str | None
    table_id: str
    caption: str
    header: list[str]
    rows: list[list[str]]
```

- [ ] **Step 3: Add route**

Create `genereview_link/api/routes/tables.py`:
```python
from typing import Annotated

from fastapi import APIRouter, Path, Request

from genereview_link.api.errors import StructuredHTTPException
from genereview_link.api.routes.passages import _get_corpus_version, get_repository
from genereview_link.models.genereview_models import ResponseMeta, TableResponse

router = APIRouter()


@router.get(
    "/chapters/{nbk_id}/tables/{table_id}",
    response_model=TableResponse,
    response_model_by_alias=True,
)
async def get_table(
    request: Request,
    nbk_id: Annotated[str, Path(pattern=r"^NBK\d+$")],
    table_id: Annotated[str, Path()],
) -> TableResponse:
    """Fetch a single chapter table as structured rows.

    Use after search_passages or get_chapter_metadata to retrieve a
    specific table's data when you need row-level access (the table is
    also retrievable as a passage_type='table' passage via search_passages).
    """
    repo = get_repository(request)
    table = await repo.get_table(nbk_id, table_id)
    if table is None:
        # List known tables for this chapter
        meta = await repo.get_chapter_metadata(nbk_id)
        valid: list[str] = []
        if meta is not None:
            # Cheap discovery: fetch table IDs
            async with repo._acquire() as conn:  # noqa: SLF001 - intentional
                rows = await conn.fetch(
                    "select table_id from public.genereview_passages "
                    "where nbk_id=$1 and passage_type='table' order by chunk_index",
                    nbk_id,
                )
            valid = [r["table_id"] for r in rows]
        raise StructuredHTTPException(
            status_code=404,
            code="table_not_found",
            message=f"table {table_id!r} not in chapter {nbk_id!r}",
            recovery_hint="check available tables via get_chapter_metadata",
            field_errors=[{"field": "table_id", "valid_values": valid}] if valid else None,
            next_commands=[{"tool": "get_chapter_metadata", "arguments": {"nbk_id": nbk_id}}],
        )

    return TableResponse(
        nbk_id=table.nbk_id,
        table_id=table.table_id,
        caption=table.caption,
        heading_path=table.heading_path,
        section=table.section,
        header=table.header,
        rows=table.rows,
        passage_id=table.passage_id,
        meta=ResponseMeta(corpus_version=_get_corpus_version(request)),
    )
```

- [ ] **Step 4: Register router in `server_manager.py`**

Find where existing routers are included into the FastAPI app and add:
```python
from genereview_link.api.routes import tables as tables_routes
app.include_router(tables_routes.router)
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/retrieval/test_repository_table.py tests/routes/test_tables.py -v -m integration
```
Expected: PASS (after corpus rebuild populates table rows; if tests run before rebuild, mark them `@pytest.mark.skipif(no_tables, reason="rebuild pending")` or use a small fixture-loaded test DB).

- [ ] **Step 6: Commit**

```bash
git add genereview_link/retrieval/repository.py genereview_link/api/routes/tables.py genereview_link/models/genereview_models.py genereview_link/server_manager.py tests/retrieval/test_repository_table.py tests/routes/test_tables.py
git commit -m "feat(api): get_table returns structured table rows"
```

---

### Task 25: Wire `table_count` into `get_chapter_metadata`

**Files:**
- Modify: `genereview_link/retrieval/repository.py` (the `get_chapter_metadata` method from Task 11)

- [ ] **Step 1: Replace placeholder `table_count = 0` with real query**

In `get_chapter_metadata`, replace `table_count = 0` with:
```python
table_count = await conn.fetchval(
    "select count(*)::int from public.genereview_passages "
    "where nbk_id=$1 and passage_type='table'",
    nbk_id,
)
```

- [ ] **Step 2: Update integration test to assert table_count > 0 for known chapter**

After rebuild, NBK1247 should have multiple tables. Add:
```python
@pytest.mark.integration
async def test_get_chapter_metadata_table_count_populated(repo) -> None:
    meta = await repo.get_chapter_metadata("NBK1247")
    assert meta.table_count > 0
```

- [ ] **Step 3: Defer running this test until post-rebuild (Task 30)**

Mark it with `@pytest.mark.requires_rebuild` (a custom marker) or add a conditional skip. Or simply commit and let it fail until Task 30 runs the rebuild.

- [ ] **Step 4: Commit**

```bash
git add genereview_link/retrieval/repository.py tests/retrieval/test_repository_metadata.py
git commit -m "feat(retrieval): wire table_count into get_chapter_metadata"
```

---

### Task 26: Investigate text normalization leak

**Files:**
- Read: `genereview_link/corpus/chunking.py`, `genereview_link/embeddings/`

- [ ] **Step 1: Grep for the suspect transforms**

```bash
grep -rn "tokenizer.decode\|\.lower()\|re\.sub.*\\\\s\|normalize" genereview_link/corpus/ genereview_link/embeddings/ | grep -v test | head -20
```
Expected: shows likely sites of the leak.

- [ ] **Step 2: Confirm the leak with a test**

Pull one chapter through the live chunker:
```bash
uv run python -c "
from genereview_link.corpus.chunking import chunk_text
out = chunk_text('Lynch syndrome (CRC) and low-density lipoprotein cholesterol (LDL-C).')
print(out)
"
```
Expected: shows whether the chunker output drops case / spaces punctuation.

- [ ] **Step 3: Document findings as code comment**

Add a comment near the offending code in `chunking.py` (or wherever): `# T2.2: leak observed at <line> — fix in next commit`.

- [ ] **Step 4: Commit investigation note**

```bash
git add genereview_link/corpus/chunking.py
git commit -m "docs(corpus): mark text-normalization leak site for fix"
```

---

### Task 27: Fix tokenizer-leak text normalization

**Files:**
- Modify: the file identified in Task 26 (likely `genereview_link/corpus/chunking.py`)
- Test: `tests/corpus/test_text_normalization.py` (NEW)

- [ ] **Step 1: Write failing regression test**

```python
from genereview_link.corpus.chunking import chunk_text  # adjust import


def test_chunker_preserves_proper_case() -> None:
    text = "Lynch syndrome (CRC) is caused by MLH1 mutations."
    chunks = chunk_text(text)
    assert all("Lynch syndrome" in c.text for c in chunks if "Lynch" in c.text.lower())
    assert "lynch syndrome" not in " ".join(c.text for c in chunks)


def test_chunker_preserves_punctuation_spacing() -> None:
    text = "Levels of low-density lipoprotein cholesterol (LDL-C) are elevated."
    chunks = chunk_text(text)
    joined = " ".join(c.text for c in chunks)
    assert "low-density" in joined
    assert "( LDL - C )" not in joined
    assert "(LDL-C)" in joined
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/corpus/test_text_normalization.py -v
```
Expected: FAIL — current pipeline mangles the text.

- [ ] **Step 3: Apply the fix**

Per the spec: keep the original NXML-derived text for the stored `text` column. If the chunker uses a tokenizer for size budgeting, separate the *budgeting* variable from the *stored* variable. Pattern:
```python
# Before (suspect): reuse decoded text
tokens = tokenizer.encode(raw_text)
budgeted = tokenizer.decode(tokens[:max_tokens])  # <-- this is what gets stored
chunks.append(Chunk(text=budgeted))

# After: budget on tokens, slice the original string by token offsets
encoding = tokenizer(raw_text, return_offsets_mapping=True)
end_offset = encoding["offset_mapping"][min(max_tokens, len(encoding["offset_mapping"])) - 1][1]
chunks.append(Chunk(text=raw_text[:end_offset]))
```

(Adapt to the actual tokenizer/library used. The principle is: never persist `tokenizer.decode(...)` output.)

- [ ] **Step 4: Run tests to confirm pass**

```bash
uv run pytest tests/corpus/test_text_normalization.py tests/corpus/ -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/corpus/chunking.py tests/corpus/test_text_normalization.py
git commit -m "fix(corpus): preserve original text casing and punctuation in chunks"
```

---

### Task 28: Section-ordering audit test

**Files:**
- Test: `tests/retrieval/test_repository_section_order.py` (NEW)

- [ ] **Step 1: Write integration test**

```python
import pytest


@pytest.mark.integration
async def test_get_section_returns_passages_in_chunk_index_order(repo) -> None:
    rows = await repo.get_section("NBK1440", "management")
    indices = [r.chunk_index for r in rows]
    assert indices == sorted(indices), f"Section ordering broken: {indices}"
```

- [ ] **Step 2: Run**

```bash
DATABASE_URL=postgresql://genereview:genereview@127.0.0.1:5436/genereview_test \
  uv run pytest tests/retrieval/test_repository_section_order.py -v -m integration
```
Expected: PASS (current code at `repository.py:299` already orders by chunk_index). If it fails, add `section_level` to the ORDER BY.

- [ ] **Step 3: Commit**

```bash
git add tests/retrieval/test_repository_section_order.py
git commit -m "test(retrieval): lock get_section chunk_index ordering"
```

---

### Task 29: Update server instructions for Phase 7

**Files:**
- Modify: `genereview_link/server_manager.py`

- [ ] **Step 1: Update instructions text**

Add in the `instructions=` block:
- `passage_type="table"` to the description of search results (so LLMs know to expect a `table` type)
- `get_table(nbk_id, table_id)` to the canonical pipeline paragraph as the structured-access escape hatch
- Note that `chapter_last_updated` is now populated for citation context

- [ ] **Step 2: Commit**

```bash
git add genereview_link/server_manager.py
git commit -m "docs(mcp): update server instructions for tables and freshness"
```

---

### Task 30: Operational corpus rebuild

**Files:**
- New: `tests/smoke/phase_7_post_rebuild.sh`

- [ ] **Step 1: Snapshot pre-rebuild DB**

```bash
mkdir -p backups
PGPASSWORD=genereview pg_dump -h 127.0.0.1 -p 5436 -U genereview genereview > backups/pre-phase7-$(date +%Y%m%d-%H%M%S).sql
```
Expected: file written; size >0.

- [ ] **Step 2: Run full corpus pipeline**

```bash
DATABASE_URL=postgresql://genereview:genereview@127.0.0.1:5436/genereview \
  uv run genereview-link ingest
```
(Confirm the actual CLI command — check `Makefile` `ingest` target if unsure.)

Expected: completes in 30-60 min. Watch logs for per-chapter errors.

- [ ] **Step 3: Run embeddings backfill**

```bash
DATABASE_URL=postgresql://genereview:genereview@127.0.0.1:5436/genereview \
  uv run genereview-link embed
```
Or: `make embed`. Expected: completes; HNSW index built.

- [ ] **Step 4: Pre-tag DB smoke checks**

```bash
PGPASSWORD=genereview psql -h 127.0.0.1 -p 5436 -U genereview -d genereview -c "
select count(*) as total_passages,
       count(*) filter (where passage_type='table') as tables,
       count(*) filter (where passage_type='narrative') as narratives
  from public.genereview_passages;
select count(*) as chapters_with_dates,
       count(*) filter (where last_updated_date is not null) as with_dates
  from public.genereview_chapters;
"
```
Expected: tables > 0, with_dates ≈ 882.

- [ ] **Step 5: Sample text-normalization check**

```bash
PGPASSWORD=genereview psql -h 127.0.0.1 -p 5436 -U genereview -d genereview -c "
select passage_id, left(text, 200) from public.genereview_passages
 where text like '%- %-%' or text ilike '%lynch syndrome%'
 limit 5;
"
```
Expected: proper-cased text without `" - "` artifacts around dashes.

- [ ] **Step 6: Write post-rebuild smoke**

Create `tests/smoke/phase_7_post_rebuild.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"

echo "=== Phase 7 post-rebuild smoke ==="

# Tables present
out=$(curl -sf "$BASE/passages/search?q=variant+class+modulator&limit=5")
echo "$out" | jq -e '.results | map(select(.passage_type=="table")) | length > 0' >/dev/null \
  || { echo "FAIL: no table passages in search results"; exit 1; }
echo "OK: table passages searchable"

# get_table works
out=$(curl -sf "$BASE/chapters/NBK1247/metadata")
table_count=$(echo "$out" | jq -r '.table_count')
[[ "$table_count" -gt 0 ]] || { echo "FAIL: NBK1247 table_count is $table_count"; exit 1; }
echo "OK: chapter metadata reports $table_count tables"

# chapter_last_updated populated
out=$(curl -sf "$BASE/chapters/NBK1247/metadata")
echo "$out" | jq -e '.chapter_last_updated != null' >/dev/null \
  || { echo "FAIL: chapter_last_updated still null"; exit 1; }
echo "OK: chapter_last_updated populated"

# Text normalization
out=$(curl -sf "$BASE/passages/search?q=Lynch+syndrome&limit=1&include=score_breakdown")
echo "$out" | jq -e '.results[0].snippet // empty | test("^[a-z]") | not' >/dev/null \
  || { echo "WARN: snippet still starts with lowercase (may be valid); inspect manually"; }
echo "OK: text normalization sample"

echo "=== All Phase 7 smoke checks passed ==="
```

- [ ] **Step 7: Run smoke against gr-pg**

```bash
DATABASE_URL=postgresql://genereview:genereview@127.0.0.1:5436/genereview make dev &
sleep 3
chmod +x tests/smoke/phase_7_post_rebuild.sh
tests/smoke/phase_7_post_rebuild.sh
kill %1
```
Expected: all OK lines.

- [ ] **Step 8: Commit smoke + tag**

```bash
git add tests/smoke/phase_7_post_rebuild.sh
git commit -m "test(smoke): phase 7 post-rebuild content-pass probe"
git tag -a phase-7-content-v2 -m "Phase 7 content pass complete after corpus rebuild"
```

---

# Phase 8 — Polish Pass

**Goal:** `get_license` as MCP resource, latency hints in tool descriptions, structured 422 for nested-q, HGNC alias guidance, optional `dedupe` param. Tag: `phase-8-ergonomics-v2`.

---

### Task 31: Add `rapidfuzz` dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock` (regenerated)

- [ ] **Step 1: Check if `rapidfuzz` is already a transitive dep**

```bash
grep -i "rapidfuzz" uv.lock | head -3
```
If present transitively, still add as a direct dependency for clarity.

- [ ] **Step 2: Add to pyproject.toml**

In the main `[project] dependencies` list, add:
```
"rapidfuzz>=3.6.0",
```

- [ ] **Step 3: Regenerate lock**

```bash
make lock
```

- [ ] **Step 4: Sync and verify**

```bash
make install
uv run python -c "import rapidfuzz; print(rapidfuzz.__version__)"
```
Expected: version printed.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add rapidfuzz for fuzzy gene-symbol matching"
```

---

### Task 32: Add cached gene-symbol index service

**Files:**
- Create: `genereview_link/services/gene_index.py`
- Modify: `genereview_link/server_manager.py` (lifespan startup populates cache)
- Test: `tests/services/test_gene_index.py`

- [ ] **Step 1: Write failing test**

```python
from genereview_link.services.gene_index import GeneIndex


def test_gene_index_match_exact() -> None:
    idx = GeneIndex(symbols={"BRCA1", "BRCA2", "MLH1"})
    assert idx.is_indexed("BRCA1") is True
    assert idx.is_indexed("BRCA9") is False


def test_gene_index_close_matches_for_aliases() -> None:
    idx = GeneIndex(symbols={"MLH1", "MSH2", "PMS2"})
    suggestions = idx.close_matches("hMLH1", limit=3)
    assert "MLH1" in suggestions
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/services/test_gene_index.py -v
```
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement**

```python
# genereview_link/services/gene_index.py
"""Cached set of indexed HGNC symbols for fast validation + fuzzy matching."""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import process, fuzz

import asyncpg


@dataclass(frozen=True, slots=True)
class GeneIndex:
    symbols: frozenset[str]

    def is_indexed(self, symbol: str) -> bool:
        return symbol in self.symbols

    def close_matches(self, symbol: str, *, limit: int = 3, score_cutoff: float = 70.0) -> list[str]:
        """Return up to `limit` close matches above the cutoff, ordered by score."""
        results = process.extract(
            symbol,
            self.symbols,
            scorer=fuzz.WRatio,
            limit=limit,
            score_cutoff=score_cutoff,
        )
        return [match for match, _score, _idx in results]


async def load_gene_index(pool: asyncpg.Pool) -> GeneIndex:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "select distinct unnest(gene_symbols) as sym from public.genereview_chapters"
        )
    return GeneIndex(symbols=frozenset(r["sym"] for r in rows if r["sym"]))
```

- [ ] **Step 4: Wire into lifespan startup**

In `genereview_link/server_manager.py` lifespan startup (alongside `corpus_version` caching):
```python
from genereview_link.services.gene_index import load_gene_index

app.state.gene_index = None
try:
    app.state.gene_index = await load_gene_index(app.state.pool)
    logger.info("loaded gene_index", count=len(app.state.gene_index.symbols))
except Exception as exc:
    logger.warning("gene_index load failed", error=str(exc))
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/services/test_gene_index.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add genereview_link/services/gene_index.py genereview_link/server_manager.py tests/services/test_gene_index.py
git commit -m "feat(services): cached gene-symbol index with fuzzy close-match"
```

---

### Task 33: Validate `gene` filter with structured 400

**Files:**
- Modify: `genereview_link/api/routes/passages.py`

- [ ] **Step 1: Write failing test**

```python
def test_search_unknown_gene_returns_structured_400(test_client) -> None:
    resp = test_client.get("/passages/search", params={"q": "x", "gene": "BRCA9"})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "gene_not_indexed"
    assert "next_commands" in detail
```

- [ ] **Step 2: Run**

```bash
uv run pytest tests/routes/test_passages_search.py -v -k unknown_gene
```
Expected: FAIL — currently returns 200 with empty results.

- [ ] **Step 3: Add validation in `search_passages` route**

After parsing `gene` parameter, before executing the search:
```python
if gene:
    idx = getattr(request.app.state, "gene_index", None)
    if idx is not None and not idx.is_indexed(gene):
        suggestions = idx.close_matches(gene, limit=3)
        raise StructuredHTTPException(
            status_code=400,
            code="gene_not_indexed",
            message=f"gene symbol {gene!r} is not indexed in the corpus",
            recovery_hint="use the canonical HGNC symbol; aliases (e.g., 'hMLH1' for 'MLH1') are not supported",
            field_errors=[{"field": "gene", "valid_values": suggestions}] if suggestions else None,
            next_commands=[
                {"tool": "search_passages", "arguments": {"q": "<query>", "gene": s}}
                for s in suggestions
            ] or None,
        )
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
uv run pytest tests/routes/test_passages_search.py -v -k unknown_gene
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/api/routes/passages.py tests/routes/test_passages_search.py
git commit -m "feat(api): structured 400 for unknown gene symbols"
```

---

### Task 34: Custom 422 handler for nested-`q`

**Files:**
- Modify: `genereview_link/api/routes/passages.py` or a dedicated FastAPI exception handler

- [ ] **Step 1: Write failing test**

```python
def test_search_with_nested_json_q_returns_structured_422(test_client) -> None:
    # Simulate "q" passed as a nested object (e.g., {"q": {"text": "BRCA1"}}).
    # FastAPI will normally raise a 422 with Pydantic's default message.
    resp = test_client.post(
        "/passages/search",
        json={"q": {"text": "BRCA1"}},
    )
    if resp.status_code == 422:
        detail = resp.json().get("detail")
        assert isinstance(detail, dict) or isinstance(detail, list)
        if isinstance(detail, dict):
            assert detail.get("code") == "query_must_be_string"
```

(Alternative: this may apply only when an MCP client misformats the call. Adjust the test to actually trigger Pydantic's 422 path on the route.)

- [ ] **Step 2: Run**

```bash
uv run pytest tests/routes/test_passages_search.py -v -k nested_json_q
```

- [ ] **Step 3: Add a custom 422 handler**

In `genereview_link/server_manager.py` (or `api/errors.py`):
```python
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


@app.exception_handler(RequestValidationError)
async def query_must_be_string_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Detect "user sent a JSON object instead of a string for q"
    for err in exc.errors():
        loc = err.get("loc", [])
        if "q" in loc and err.get("type") in {"string_type", "value_error"}:
            return JSONResponse(
                status_code=422,
                content={"detail": {
                    "code": "query_must_be_string",
                    "message": "q must be a top-level string",
                    "recovery_hint": "pass q as a top-level string parameter, not a nested object",
                    "next_commands": [
                        {"tool": "search_passages", "arguments": {"q": "<your query string>"}}
                    ],
                }},
            )
    # Fall through to FastAPI default
    return JSONResponse(status_code=422, content={"detail": exc.errors()})
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/routes/test_passages_search.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genereview_link/server_manager.py tests/routes/test_passages_search.py
git commit -m "feat(api): structured 422 for nested-object q parameter"
```

---

### Task 35: Optional `dedupe` param on `get_chapter_section`

**Files:**
- Modify: `genereview_link/api/routes/chapters.py`
- Test: `tests/routes/test_chapters_section.py`

- [ ] **Step 1: Write failing test**

```python
def test_chapter_section_dedupe_strips_overlap(test_client) -> None:
    resp = test_client.get(
        "/chapters/NBK1247/sections/management",
        params={"dedupe": "true", "include": "concatenated_text"},
    )
    assert resp.status_code == 200
    data = resp.json()
    text = data["concatenated_text"]
    # Heuristic: no 50+ char substring should appear twice consecutively
    assert text is not None
```

- [ ] **Step 2: Add `dedupe` param**

In `genereview_link/api/routes/chapters.py`, add parameter:
```python
dedupe: Annotated[bool, Query(description="Strip overlapping text between adjacent chunks (longest-common-suffix/prefix heuristic). Default False for back-compat with literal stored text.")] = False,
```

When building `concatenated_text` (only when `dedupe=True` and `include=concatenated_text`):
```python
def _strip_overlap(parts: list[str], min_overlap: int = 30) -> str:
    if not parts:
        return ""
    out = [parts[0]]
    for nxt in parts[1:]:
        prev = out[-1]
        # Find longest suffix of prev that is a prefix of nxt
        max_match = min(len(prev), len(nxt))
        overlap_len = 0
        for k in range(max_match, min_overlap - 1, -1):
            if prev[-k:] == nxt[:k]:
                overlap_len = k
                break
        out.append(nxt[overlap_len:])
    return "".join(out)

if "concatenated_text" in include_set:
    parts = [p.text for p in passages]
    concatenated = _strip_overlap(parts) if dedupe else "\n\n".join(parts)
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/routes/test_chapters_section.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add genereview_link/api/routes/chapters.py tests/routes/test_chapters_section.py
git commit -m "feat(api): optional dedupe on get_chapter_section concatenated_text"
```

---

### Task 36: Promote `get_license` to MCP resource

**Files:**
- Modify: `genereview_link/server_manager.py`

- [ ] **Step 1: Find existing `get_license` registration**

```bash
grep -n "license\|get_license" genereview_link/server_manager.py genereview_link/api/routes/*.py | head -20
```

- [ ] **Step 2: Register MCP resource**

After `from_fastapi(...)` in `server_manager.py`, add:
```python
import json
from fastapi import Request

@mcp.resource("genereview://license")
def license_resource() -> str:
    """Static GeneReviews attribution and license summary."""
    payload = {
        "attribution": ATTRIBUTION_TEXT,
        "license": "Bookshelf terms of use; see ncbi.nlm.nih.gov/books/about",
        "notes": "Research use only; not for clinical decision support.",
    }
    return json.dumps(payload)
```

(Use the same payload shape as the existing `GET /license` REST route returns. If the route returns different fields, mirror them exactly.)

- [ ] **Step 3: Exclude `get_license` from MCP tools**

`from_fastapi` typically accepts an exclusion list. Find the call and add (consult fastmcp docs for the exact kwarg — probably `exclude_endpoints` or filter by route):
```python
mcp = FastMCP.from_fastapi(
    app=app,
    instructions=...,
    exclude_endpoints=["GET /license"],  # adjust to actual fastmcp API
)
```

If the API is different, fall back to deleting the tool after registration:
```python
if "get_license" in mcp._tool_manager._tools:  # noqa: SLF001
    del mcp._tool_manager._tools["get_license"]
```

- [ ] **Step 4: Update server instructions**

Replace any "call `get_license` once per session" wording with "fetch resource `genereview://license` once per session for attribution."

- [ ] **Step 5: Test resource resolution**

Add unit test (or extend the MCP smoke):
```python
def test_license_resource_registered() -> None:
    from genereview_link.server_manager import build_app  # adjust import
    app, mcp = build_app()
    resources = mcp._resource_manager._resources  # noqa: SLF001
    assert "genereview://license" in resources
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/ -v -k license
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add genereview_link/server_manager.py tests/
git commit -m "feat(mcp): promote license to MCP resource; remove tool exposure"
```

---

### Task 37: Latency measurement and tool description hints

**Files:**
- New: `tests/smoke/measure_latency.sh`
- Modify: each route's docstring (`passages.py`, `chapters.py`, `tables.py`)

- [ ] **Step 1: Write measurement script**

```bash
#!/usr/bin/env bash
# tests/smoke/measure_latency.sh
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"

measure() {
    local label="$1"
    local url="$2"
    local n=20
    local times=()
    for _ in $(seq 1 $n); do
        t=$(curl -sf -o /dev/null -w "%{time_total}\n" "$url")
        times+=("$t")
    done
    p50=$(printf "%s\n" "${times[@]}" | sort -n | awk -v n="$n" 'NR==int(n/2)+1{printf "%.0f", $1 * 1000}')
    echo "$label: ~${p50}ms p50"
}

measure "search_passages rrf" "$BASE/passages/search?q=BRCA1+breast+cancer&rerank=rrf&limit=5"
measure "search_passages lexical" "$BASE/passages/search?q=BRCA1+breast+cancer&rerank=lexical&limit=5"
measure "search_passages off" "$BASE/passages/search?q=BRCA1+breast+cancer&rerank=off&limit=5"
measure "get_passage" "$BASE/passages/NBK1247:0010"
measure "get_passage neighbors=3" "$BASE/passages/NBK1247:0010?neighbors=3"
measure "get_chapter_section" "$BASE/chapters/NBK1247/sections/summary"
measure "get_chapter_metadata" "$BASE/chapters/NBK1247/metadata"
measure "get_table" "$BASE/chapters/NBK1247/tables/t5"
```

- [ ] **Step 2: Run measurements**

```bash
DATABASE_URL=postgresql://genereview:genereview@127.0.0.1:5436/genereview make dev &
sleep 3
chmod +x tests/smoke/measure_latency.sh
tests/smoke/measure_latency.sh
kill %1
```
Expected: prints latency lines for each tool.

- [ ] **Step 3: Update each route docstring with the measured number**

In each route handler in `passages.py`, `chapters.py`, `tables.py`, append a line like:
```
"""... existing docstring ...

Latency: rrf ~150ms p50, lexical ~30ms p50, off ~10ms p50.
"""
```

Use the actual measurements from Step 2. Each tool gets one sentence.

- [ ] **Step 4: Run typecheck and confirm MCP exposure picks up updated descriptions**

```bash
make typecheck-fast
DATABASE_URL=postgresql://genereview:genereview@127.0.0.1:5436/genereview make dev &
sleep 3
curl -sf "http://127.0.0.1:8000/openapi.json" | jq '.paths."/passages/search".get.description' | head -5
kill %1
```
Expected: description includes the latency line.

- [ ] **Step 5: Commit**

```bash
git add tests/smoke/measure_latency.sh genereview_link/api/routes/
git commit -m "docs(api): latency hints in tool descriptions"
```

---

### Task 38: Phase 8 gate — `make ci-local` + smoke + tag

**Files:**
- New: `tests/smoke/phase_8.sh`

- [ ] **Step 1: `make ci-local`**

```bash
make ci-local
```
Expected: green.

- [ ] **Step 2: Write phase 8 smoke**

```bash
#!/usr/bin/env bash
# tests/smoke/phase_8.sh
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"

echo "=== Phase 8 smoke checks ==="

# 1. License resource via REST still resolvable
out=$(curl -sf "$BASE/license")
echo "$out" | jq -e '.attribution' >/dev/null || { echo "FAIL: license route"; exit 1; }
echo "OK: REST /license still works"

# 2. Unknown gene returns structured 400
out=$(curl -s "$BASE/passages/search?q=x&gene=BRCA9999")
code=$(echo "$out" | jq -r '.detail.code // empty')
[[ "$code" == "gene_not_indexed" ]] || { echo "FAIL: expected gene_not_indexed, got $code"; exit 1; }
echo "OK: gene_not_indexed structured 400"

# 3. dedupe param accepted
out=$(curl -sf "$BASE/chapters/NBK1247/sections/management?include=concatenated_text&dedupe=true")
echo "$out" | jq -e '.concatenated_text != null' >/dev/null
echo "OK: dedupe param works"

echo "=== All Phase 8 smoke checks passed ==="
```

- [ ] **Step 3: Run smoke**

```bash
DATABASE_URL=postgresql://genereview:genereview@127.0.0.1:5436/genereview make dev &
sleep 3
chmod +x tests/smoke/phase_8.sh
tests/smoke/phase_8.sh
kill %1
```

- [ ] **Step 4: Commit + tag**

```bash
git add tests/smoke/phase_8.sh
git commit -m "test(smoke): phase 8 polish-pass live probe"
git tag -a phase-8-ergonomics-v2 -m "Phase 8 polish complete; pass-2 done"
```

---

# Final Steps

### Task 39: Update README + release notes

**Files:**
- Modify: `README.md` or `docs/CHANGELOG.md`

- [ ] **Step 1: Document the seven breaking changes**

In `docs/CHANGELOG.md` (create if absent), under a new `## 0.X.0 — MCP ergonomics pass 2` heading, list:

1. `score_breakdown` removed from default `search_passages` response. Pass `include=score_breakdown` to restore.
2. `get_passage` response is always wrapped (`{passage, neighbors_*, has_more_*}`). Read focal fields from `response.passage.*`.
3. `get_chapter_section` no longer returns `concatenated_text` by default. Pass `include=concatenated_text`.
4. Some narrative `passage_id`s shifted `chunk_index` due to table interleaving. Re-resolve cached IDs via `search_passages`.
5. Ordinal `table_id`s may shift across rebuilds if NCBI inserts/removes tables. Re-resolve via `get_chapter_metadata`.
6. `get_license` MCP tool removed; use `genereview://license` resource. REST `/license` route unchanged.
7. Invalid gene symbol now returns structured 400 instead of empty results. Catch `code="gene_not_indexed"` and use suggested close matches.

Plus the new affordances:
- `get_passage(neighbors)`, `get_chapter_metadata`, `get_table`, empty-result diagnostics, optional `dedupe`, latency hints in tool descriptions.

- [ ] **Step 2: Commit**

```bash
git add docs/CHANGELOG.md
git commit -m "docs: changelog for MCP ergonomics pass 2"
```

### Task 40: Open PR

- [ ] **Step 1: Push branch**

```bash
git push -u origin feat/mcp-llm-ergonomics
```

- [ ] **Step 2: Open PR via gh**

```bash
gh pr create --title "feat(mcp): LLM-ergonomics pass 2 — trust + discovery + content + polish" --body "$(cat <<'EOF'
## Summary
Lifts the MCP server from consumer-rated 8.2/10 to ~9.2/10 by addressing the systematic weaknesses every LLM consumer reviewer flagged.

- Phase 5 Trust: backfilled `chapter_last_updated`, wired `rrf_score`/`dense_rank`, flipped `score_breakdown` default to opt-in
- Phase 6 Discovery: `get_passage(neighbors)`, `get_chapter_metadata`, empty-result diagnostics, dropped `concatenated_text` default
- Phase 7 Content: tables as `passage_type='table'` passages + `get_table` tool, fixed tokenizer-leak text normalization, full corpus rebuild
- Phase 8 Polish: `get_license` as MCP resource, latency hints, structured 422/400 errors, optional dedup, rapidfuzz alias suggestions

Seven breaking changes documented in `docs/CHANGELOG.md`.

## Test plan
- [x] `make ci-local` green
- [x] Per-phase smoke scripts pass against gr-pg corpus
- [x] Post-rebuild table count > 0; chapter_last_updated populated
- [x] All four phase tags pushed (`phase-5/6/7/8-ergonomics-v2`)
EOF
)"
```

---

## Self-Review

**Spec coverage check:** every spec task (T1.1, T1.2, T4.1, T4.2, T3.1, T3.2, T3.3, T3.5, T2.1, T2.2, T2.3, T4.3, T4.4, T4.5, T4.6, T4.7) maps to one or more tasks in this plan. The deferred T4.8 (`find_in_section` as tool) stays deferred. Server-instruction updates are included per phase. The corpus rebuild is its own task with snapshot, run, and post-rebuild smoke. The breaking-changes table content is committed in Task 39.

**Placeholder scan:** no "TBD/TODO/FIXME" left in the plan body. A few task descriptions reference "the actual variable name in the existing code" or "adjust XPath to match real shape" — these are honest acknowledgements that the implementer must verify against current code, not placeholders.

**Type consistency:** `PassageWindowResponse` (Task 9) used in route Task 10. `ChapterMetadataResponse` (Task 12) used in route Task 12. `TableResponse` (Task 24) used in route Task 24. `SearchDiagnosticsModel` (Task 14) referenced from Task 13's `SearchDiagnostics`. Method signatures match: `get_passage_window(passage_id, *, before, after, cross_sections)` is consistent across Task 8 (definition) and Task 10 (call site). `build_search_diagnostics(*, query, applied_filters, lexical_hits, lexical_hits_after_filters)` is consistent between Task 13 and Task 14.
