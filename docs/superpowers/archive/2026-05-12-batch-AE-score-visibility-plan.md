# Batch A+E — Score Visibility & Parameter Ergonomics — Implementation Plan

> Historical record

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Date:** 2026-05-12 (rev 2 — addresses peer review)

**Goal:** Surface `rrf_score` + ranking-position fields on every search hit, populate `_meta.diagnostics` on every response with honest semantics, surface rerank/mode enum values inline in tool descriptions, accept `query` as an alias for `q` via dual params, add `heading_path_contains` to `search_passages`, include diagnostics in `ids_only`, and fix the `POST /passages/batch` instruction drift. Lift LLM-consumer rating to **≥9.5/10** on ranking-transparency and diagnostics dimensions.

**Architecture:** Single phase (Phase 11 — Score-Visibility-v1), **9 tasks**, code-only, no schema change, no reingest. Tag: `phase-11-score-visibility-v1`.

**Tech Stack:** Python 3.12, FastAPI 0.136.1, FastMCP, Pydantic v2, pytest + ruff + mypy strict.

**Spec:** `docs/superpowers/specs/2026-05-12-batch-AE-score-visibility-design.md`

**Branch:** `feat/mcp-llm-ergonomics-pass3b` cut from `main` after PR #17 merges. Escalate BLOCKED if PR #17 has not merged.

---

## File Map

**Modified:**

- `genereview_link/models/genereview_models.py` — promote `rrf_score`, `lexical_score`, `lexical_rank_position`, `dense_rank_position` to top-level optional fields on `RankedPassage`; extend `SearchDiagnosticsModel` with `rerank_used`, `lexical_candidate_count`, `dense_candidate_count`, `section_filters`, `unfiltered_lexical_count`.
- `genereview_link/api/routes/passages.py` — unconditionally populate the four new top-level rank fields; build `diagnostics_model` before the mode branch; include diagnostics in `ids_only`; dual `q`/`query` params; `heading_path_contains` parameter; expand `rerank` (`rrf|lexical|off`) and `mode` descriptions inline.
- `genereview_link/api/routes/chapters.py` — expand `section` description inline.
- `genereview_link/retrieval/repository.py` — accept `heading_path_contains` kwarg on `search_passages`; concatenate parameterized `ILIKE` clause into the `cand` CTE.
- `genereview_link/retrieval/rerank.py` — set `lexical_rank_position` and `dense_rank_position` on each returned row using existing `lex_rank` / `dense_rank` dicts at `rerank.py:90, 95`.
- `genereview_link/server_manager.py` (verify path during impl) — replace `POST /passages/batch` with `get_passages_batch`; audit other tool-name references.
- `genereview_link/api/resources/usage.py` — document the new always-on fields, candidate-count semantics, and the unfiltered-probe-on-empty path.

**New:**

- `tests/unit/test_api_passages_search_heading_path.py` (NEW) — `heading_path_contains` filter on `search_passages`.
- `tests/unit/test_api_passages_dual_query.py` (NEW) — `q` and `query` alias behavior + conflict.
- `tests/test_mcp_search_passages_params.py` (NEW) — MCP-side verification that both `q` and `query` reach the same handler.
- `tests/smoke/phase_11_score_visibility.sh` (NEW) — live probe.

**Test files extended:**

- `tests/test_routes_passages.py` — assert new top-level rank fields populated by default; assert `_meta.diagnostics.rerank_used == "rrf"`; assert `ids_only` includes diagnostics; assert empty-result branch populates `unfiltered_lexical_count`.
- `tests/test_mcp_usage_resource.py` — substring assertions for the new documented fields.

---

# Phase 11 — Score-Visibility-v1

**Goal:** Surface ranking transparency and diagnostics on every response without breaking PR #17's schema. Tag: `phase-11-score-visibility-v1`.

**Execution order:** 1 (rank fields) → 2 (rerank-side population) → 3 (diagnostics refactor including ids_only) → 4 (dual-query params + heading_path filter) → 5 (description inlining) → 6 (instruction drift) → 7 (usage resource) → 8 (MCP smoke) → 9 (gate).

---

### Task 1: Promote rank fields to top-level on `RankedPassage`

**Files:**

- Modify: `genereview_link/models/genereview_models.py`.
- Modify: `genereview_link/api/routes/passages.py` (row construction at lines 321–345).

**Why:** Five of six reviewers flagged ranking opacity. Root cause: `rrf_score` lives inside `score_breakdown`, stripped by default.

- [ ] Step 1: Add four new optional top-level fields to `RankedPassage`:

```python
rrf_score: float | None = None
lexical_score: float | None = None        # weighted lexical score (a.k.a. repo "lexical_rank")
lexical_rank_position: int | None = None  # 1-indexed position in lexical sort
dense_rank_position: int | None = None    # 1-indexed position in dense sort, None when no dense
```

Place them above the existing `score_breakdown: ScoreBreakdown | None = None`. Update the `RankedPassage` docstring to note these are always populated when the rerank produces them (rrf rerank produces all four; lexical rerank populates lexical_* only; off populates none).

- [ ] Step 2: In `passages.py` row construction, unconditionally populate the four fields:
  - `rrf_score = r.rrf_score`
  - `lexical_score = r.lexical_rank`  ← internal field name unchanged; public field is `lexical_score`
  - `lexical_rank_position = r.lexical_rank_position`  ← new attribute set by rerank (Task 2)
  - `dense_rank_position = r.dense_rank`  ← already exists on row dataclass

- [ ] Step 3: Confirm `score_breakdown` remains excluded by default (existing `excluded.add("score_breakdown")` line stays).

- [ ] Step 4: Run `make test-fast`. Existing tests asserting `score_breakdown is None` keep passing.

**Acceptance:** `GET /passages/search?q=foo` shows non-null `rrf_score`, `lexical_score`, `lexical_rank_position`, `dense_rank_position` on each row when `rerank=rrf`; `score_breakdown` is still null.

---

### Task 2: Populate `lexical_rank_position` in rerank

**Files:**

- Modify: `genereview_link/retrieval/rerank.py`.
- Modify: `genereview_link/retrieval/repository.py` (`LexicalPassageRow` dataclass — add `lexical_rank_position: int | None = None`).

**Why:** `rerank.py:90` already builds `lex_rank = {passage_id: i+1, ...}`. Surface it on the row.

- [ ] Step 1: Add `lexical_rank_position: int | None = None` to `LexicalPassageRow`.
- [ ] Step 2: At `rerank.py:103-108` (the `scored_evidence = [dataclasses.replace(r, ...)]` block), add `lexical_rank_position=lex_rank[r.passage.passage_id]` to the replace kwargs.
- [ ] Step 3: Also populate it for the no-dense-scores fallback path at `rerank.py:75-79` so `rerank=lexical` produces positions too.

**Acceptance:** `r.lexical_rank_position` set on every row returned by `rerank_with_embeddings`.

---

### Task 3: Always-on `_meta.diagnostics` (including `ids_only`)

**Files:**

- Modify: `genereview_link/models/genereview_models.py` — extend `SearchDiagnosticsModel`.
- Modify: `genereview_link/api/routes/passages.py` — refactor so diagnostics builds **before** the mode branch and is included in all three branches (`brief`, `full`, `ids_only`).

**Why:** Five reviewers read `"diagnostics": null` as a failure signal. The `ids_only` early-return at `passages.py:279-293` also misses diagnostics today.

- [ ] Step 1: Extend `SearchDiagnosticsModel`:

```python
rerank_used: Literal["rrf","lexical","off"]
lexical_candidate_count: int                  # candidates returned by repo.search_passages (post-SQL-filter)
dense_candidate_count: int | None = None      # len(dense_scores); None when rerank != 'rrf'
section_filters: list[str] = []
unfiltered_lexical_count: int | None = None   # set only on empty-result branch (see Step 3)
# applied_filters, suggestions: already present
```

- [ ] Step 2: In `search_passages`, after `ranked = ranked[:limit]` and **before** the `if mode == "ids_only":` branch, construct `diagnostics_model` with the always-on fields:

```python
diagnostics_model = SearchDiagnosticsModel(
    rerank_used=rerank,
    lexical_candidate_count=len(lex),
    dense_candidate_count=(len(dense_scores) if rerank == "rrf" else None),
    applied_filters=[
        *(f"gene={gene}" for _ in [gene] if gene),
        *(f"nbk_id={nbk_id}" for _ in [nbk_id] if nbk_id),
        *(f"sections={','.join(sections)}" for _ in [sections] if sections),
        *(f"heading_path_contains={heading_path_contains}" for _ in [heading_path_contains] if heading_path_contains),
    ],
    section_filters=list(sections) if sections else [],
    suggestions=[],
)
```

- [ ] Step 3: When `ranked` is empty AND filters were applied, issue a second probe **without** filters to compute `unfiltered_lexical_count`. Reuse `repo.search_passages(q, gene_symbol=None, nbk_id=None, sections=None, ...)`. Populate `unfiltered_lexical_count`. If non-zero, append the existing suggestion codes (`section-filter-drops-all`, `gene-filter-drops-all`, etc.) — keep the `build_search_diagnostics` helper for the suggestion logic.

- [ ] Step 4: Refactor the mode branches. Both `meta = ResponseMeta(...)` constructions now always receive `diagnostics=diagnostics_model`. The `ids_only` branch builds its lean rows and wraps them with the same `meta` envelope.

- [ ] Step 5: Update `ids_only` row shape to also include `lexical_rank_position`:

```python
{
    "passage_id": r.passage.passage_id,
    "rrf_score": r.rrf_score,
    "lexical_rank_position": r.lexical_rank_position,
    "chapter_section": r.passage.chapter_section,
}
```

- [ ] Step 6: Update existing assertions in `test_routes_passages.py` that expected `diagnostics is None` on successful searches.

**Acceptance:** Every search response (brief/full/ids_only) shows populated `_meta.diagnostics`. Empty-result responses additionally show `unfiltered_lexical_count` and a suggestion code when filters dropped all rows.

---

### Task 4: Dual `q` / `query` params + `heading_path_contains`

**Files:**

- Modify: `genereview_link/api/routes/passages.py`.
- Modify: `genereview_link/retrieval/repository.py` — `search_passages` accepts `heading_path_contains: str | None = None`.

**Why:** Local HTTP verification: `Query(alias="query")` makes `query` the canonical wire name and rejects `?q=` with 422. Need true dual support.

- [ ] Step 1: Change param declarations. Make both optional, resolve in the handler:

```python
q: Annotated[str | None, Query(min_length=1, max_length=512, description="Query string (canonical). Either q or query is required.")] = None,
query: Annotated[str | None, Query(min_length=1, max_length=512, description="Alias for q (cross-MCP convention).")] = None,
```

Inside the handler:

```python
if q is not None and query is not None and q != query:
    raise StructuredHTTPException(
        status_code=422, code="conflicting_query_param",
        message="both q and query supplied with different values",
        recovery_hint="pass only one of q or query, or pass the same string in both",
    )
if not q and not query:
    raise StructuredHTTPException(
        status_code=422, code="missing_query",
        message="one of q or query is required",
        recovery_hint="pass q='your query string'",
    )
q = q or query
assert q is not None
```

The rest of the handler uses `q` as before.

- [ ] Step 2: Add `heading_path_contains` parameter:

```python
heading_path_contains: Annotated[
    str | None,
    Query(min_length=1, max_length=200,
          description="Case-insensitive substring filter on heading_path. Applied pre-rerank."),
] = None,
```

Pass through to `repo.search_passages(..., heading_path_contains=heading_path_contains)`.

- [ ] Step 3: In `repository.py`, extend `search_passages`'s signature with `heading_path_contains: str | None = None`. Add the parameterized predicate to the `cand` CTE WHERE clause:

```sql
and ($N::text is null or p.heading_path ILIKE '%' || $N || '%')
```

Bind `heading_path_contains` as a new parameter alongside the existing ones — do not inline.

- [ ] Step 4: Tests:
  - `tests/unit/test_api_passages_dual_query.py` (NEW): `?q=BRCA1`, `?query=BRCA1`, `?q=BRCA1&query=BRCA1` all return 200 with identical results. `?q=foo&query=bar` returns 422 with `conflicting_query_param`. Neither supplied returns 422 with `missing_query`.
  - `tests/unit/test_api_passages_search_heading_path.py` (NEW): `?q=mastectomy&heading_path_contains=Prevention` restricts to Prevention heading paths.

**Acceptance:** All four HTTP cases above behave as specified; SQL parameter is bound, never inlined.

---

### Task 5: Inline enum values in tool descriptions

**Files:**

- Modify: `genereview_link/api/routes/passages.py` — `search_passages` `rerank` and `mode` parameter descriptions.
- Modify: `genereview_link/api/routes/chapters.py` — `section` parameter description on `get_chapter_section`.

**Why:** Four reviewers said they had to round-trip to `genereview://usage` to discover enum values. Tool schemas are read first.

- [ ] Step 1: `rerank` description gains (matches **actual** Literal at `passages.py:202`, which is `rrf|lexical|off`):

> Values: `"rrf"` (default; reciprocal-rank fusion of weighted lexical + dense embedding rank — best for clinical-concept queries), `"lexical"` (weighted lexical score with section-priority tiebreaker — best for exact gene-symbol or variant strings), `"off"` (raw repository order — debugging only; do not rely on ordering).

- [ ] Step 2: `mode` description gains:

> Values: `"brief"` (default; snippet + IDs, ~3 KB), `"full"` (full text), `"ids_only"` (lean rows: `passage_id` + `rrf_score` + `lexical_rank_position` + `chapter_section`).

- [ ] Step 3: `chapters.py:section` description gains the 8 closed vocabulary values inline.

**Acceptance:** A consumer reading the tool schema sees enum values without fetching the usage resource.

---

### Task 6: Fix MCP instruction-string drift

**Files:**

- Modify: `genereview_link/server_manager.py` (verify exact path during impl).

**Why:** Reviewer caught `POST /passages/batch` in the instructions; actual MCP tool is `get_passages_batch`.

- [ ] Step 1: Open the instructions string. Find every HTTP-verb reference.
- [ ] Step 2: Replace with the MCP `operation_id` names: `get_passages_batch`, `search_passages`, `get_passage`, `get_chapter_metadata`, `get_chapter_section`, `get_table`. Cross-reference each against the route file's `operation_id=` definition.
- [ ] Step 3: Grep the rest of the repo for any other tool-name drift between docstrings and `operation_id`.

**Acceptance:** Every tool name in the instructions matches a registered MCP tool.

---

### Task 7: Update usage resource

**Files:**

- Modify: `genereview_link/api/resources/usage.py`.

**Why:** Document the new always-on fields, the candidate-count semantics, and the unfiltered probe.

- [ ] Step 1: Update the score-visibility paragraph: list `rrf_score`, `lexical_score`, `lexical_rank_position`, `dense_rank_position` as always-on top-level fields when `rerank=rrf`; note `score_breakdown` remains the opt-in deep view.
- [ ] Step 2: Update the diagnostics paragraph: explain `lexical_candidate_count` / `dense_candidate_count` are **post-filter** candidate counts (not pre-vs-post-filter). State that `unfiltered_lexical_count` appears only on empty-result responses when a filter dropped all candidates.
- [ ] Step 3: Document the `q` / `query` dual-parameter behavior.
- [ ] Step 4: Document `heading_path_contains` on `search_passages` and that it also applies on `get_chapter_section`.

**Acceptance:** Markdown reads cleanly; existing `tests/test_mcp_usage_resource.py` substring assertions still pass after updating them for the new field names.

---

### Task 8: MCP-side smoke for dual-query

**Files:**

- New: `tests/test_mcp_search_passages_params.py`.

**Why:** FastMCP serializes the OpenAPI schema into a tool schema. Verify both `q` and `query` reach the underlying handler via the MCP protocol, not just over HTTP.

- [ ] Step 1: Use the existing MCP test harness (look at `tests/test_mcp_*.py` for the pattern). Invoke `search_passages` via the MCP tool path with `{"q": "BRCA1"}`. Then invoke with `{"query": "BRCA1"}`. Then invoke with `{"q": "BRCA1", "query": "BRCA1"}`. All three return identical top-1 `passage_id`.
- [ ] Step 2: Invoke with `{"q": "foo", "query": "bar"}` — expect a structured error response with code `conflicting_query_param`.

**Acceptance:** All four invocations behave as expected via the MCP path.

---

### Task 9: Phase gate — smoke + tag

**Files:**

- New: `tests/smoke/phase_11_score_visibility.sh`.

- [ ] Step 1: Smoke script probes:
  - `GET /passages/search?q=BRCA1+risk-reducing+mastectomy+salpingo-oophorectomy&limit=5` → every row has non-null `rrf_score`, `lexical_score`, `lexical_rank_position`; `_meta.diagnostics.rerank_used == "rrf"`; `lexical_candidate_count >= 1`; `dense_candidate_count >= 1`.
  - `GET /passages/search?query=BRCA1&limit=1` → alias resolves; 1 result with non-null rank fields.
  - `GET /passages/search?q=mastectomy&heading_path_contains=Prevention&limit=3` → all rows' `heading_path` contains "Prevention" case-insensitively.
  - `GET /passages/search?q=mastectomy&mode=ids_only&limit=3` → `_meta.diagnostics` non-null; row shape includes `lexical_rank_position`.
  - `GET /passages/search?q=zzz_no_match&sections=management&limit=3` → empty results; `_meta.diagnostics.unfiltered_lexical_count` present.

- [ ] Step 2: `make ci-local` clean.
- [ ] Step 3: Docker rebuild (`docker compose down genereview-link && docker compose build genereview-link && docker compose up -d genereview-link`). Run smoke.
- [ ] Step 4: Tag `phase-11-score-visibility-v1` on the merge commit.

**Acceptance:** All smoke checks PASS; CI green; tag pushed.

---

## Done criteria for Batch A+E

- All 9 tasks complete with green tests.
- Every search hit (brief/full/ids_only) shows non-null `rrf_score`, `lexical_score`, `lexical_rank_position`, `dense_rank_position`.
- Every search response shows a populated `_meta.diagnostics` block; empty-result responses include `unfiltered_lexical_count` when a filter dropped candidates.
- `query=` alias works via both HTTP and MCP; conflict produces 422.
- `heading_path_contains` works on `search_passages` (parameter-bound, not inlined).
- Tool descriptions inline `rerank` (`rrf|lexical|off`) and `mode` enum values.
- Instructions string references match `operation_id` names.
- Tag `phase-11-score-visibility-v1` pushed.
