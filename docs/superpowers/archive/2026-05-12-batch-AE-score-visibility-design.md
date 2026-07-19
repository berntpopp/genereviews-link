# Batch A+E — Score Visibility & Parameter Ergonomics — Design Spec

**Date:** 2026-05-12 (rev 2 — addresses peer review)
> Historical record

**Branch target:** new branch off `main` after PR #17 (`feat/mcp-llm-ergonomics-pass3a`) merges.
**Successor:** Batch B (separate spec) — section-aware ranking + `passage_role`.
**Related:** Batch C (deferred) — deep section anchors, `next/prev_passage_id`, capability tool.

## Goal

Address the four highest-frequency complaints across six independent LLM-consumer reviews — all of which scored ranking transparency and diagnostics at **6–7/10** — without breaking the schema that PR #17 ships. Lift consumer rating to a sustained **≥9.5/10** by making rank scores and a minimal diagnostics block visible by default, surfacing enums in tool descriptions, and aligning parameter names with cross-MCP convention.

## Scope

Single phase (**Phase 11 — Score-Visibility-v1**), code-only changes, no reingest. Tag: `phase-11-score-visibility-v1`.

**Reviewer themes addressed (count = # of 6 reviewers flagging it):**

| Theme | Reviewers | Current state | Fix |
|---|---|---|---|
| `rrf_score` null on every default search hit | 5/6 | Field plumbed but gated behind `include=score_breakdown` | Promote `rrf_score` + ranking-position fields to top-level additive fields on `RankedPassage` |
| `_meta.diagnostics` null on every non-empty response | 5/6 | `diagnostics_model` only constructed when `not out` (`passages.py:348`) | Always build a minimal block; document candidate-count semantics honestly |
| Rerank/mode enums not surfaced in tool descriptions | 4/6 | Enum names live only in `genereview://usage` | Inline in `description=` strings |
| Server-instruction drift (`POST /passages/batch` vs `get_passages_batch`) | 1/6 | Reviewer caught the mismatch | Align names |
| `heading_path_contains` not on `search_passages` | 2/6 | Filter exists on `get_chapter_section` only | Reuse SQL clause; add to search route |
| `query` alias for `q` parameter | 2/6 | Only `q` accepted | Dual optional params resolved in handler |

**Explicit non-goals (deferred):**

- Ranking-quality fixes — Batch B.
- `passage_role` field — Batch B (requires reingest).
- `next_passage_id` / `prev_passage_id` — Batch C.
- `get_server_capabilities` tool — Batch C.
- Deep section anchor URLs — Batch C.
- BibTeX/CSL JSON citation variants — defer.
- `anyOf:[X,null]` JSON-schema cleanup — **deferred** (rev 2). Pydantic's `model_config` does **not** rewrite this shape on its own; achieving the desired form requires a FastAPI `app.openapi` post-process hook with end-to-end validation against the FastMCP-served schema. Out of scope for this batch; revisit if any LLM consumer reports the form as a real obstacle.

## Architecture

### Score visibility (additive, factually correct)

Three reviewers wanted "an `rrf_score` plus enough breakdown to debug." Today these fields exist in `LexicalPassageRow` and `ScoreBreakdown` but are hidden by default. Two corrections vs the previous spec revision:

- **`lexical_rank` in the repository is a weighted lexical score** (`phrase_rank*3 + strict_rank*2 + recall_rank`, optionally dampened — see `repository.py:257-264`). It is **not** a 1-indexed rank position. We will not relabel it as a rank position.
- **A 1-indexed lexical rank position does exist** but only at rerank time (`rerank.py:90` — `lex_rank` dict). To surface it without redoing arithmetic per row, we materialize it onto the row during rerank.

New top-level optional fields on `RankedPassage`:

```python
rrf_score: float | None = None
lexical_score: float | None = None        # weighted lexical score (was: lexical_rank in repo)
lexical_rank_position: int | None = None  # 1-indexed position in the lexical sort
dense_rank_position: int | None = None    # 1-indexed position in the dense sort (None when no dense)
score_breakdown: ScoreBreakdown | None = None  # unchanged; opt-in deep view
```

Always populated when the rerank produces them. The deep `score_breakdown` view is still gated by `include=score_breakdown` and retains the existing per-rank-component fields (`phrase_rank`, `strict_rank`, `recall_rank`, `dense_score`, `section_priority`, `final_position`).

Token cost: ~40 bytes per row over the prior payload. Acceptable at default `limit=5`.

Routing change at `passages.py:321-345`: stop nulling these on the default branch; have the rerank function set `lexical_rank_position` and (when applicable) `dense_rank_position` on each returned row, rename the repo-side `lexical_rank` exposure to `lexical_score` on the public model.

**Internal rename trade-off**: the `LexicalPassageRow.lexical_rank` field stays the same name internally to minimize churn. The public field is `lexical_score`. The two are connected by a single mapping at row-construction time.

### Always-on minimal diagnostics (with honest semantics)

`SearchDiagnosticsModel` extended:

```python
class SearchDiagnosticsModel(BaseModel):
    # always populated:
    rerank_used: Literal["rrf","lexical","off"]     # NEW — echoes the chosen mode
    lexical_candidate_count: int                    # NEW — count returned by repo.search_passages
    dense_candidate_count: int | None = None        # NEW — len(dense_scores); None when rerank!='rrf'
    applied_filters: list[str] = []                 # already exists
    section_filters: list[str] = []                 # NEW — sections[] as list (subset of applied_filters)
    # populated only when results are empty:
    suggestions: list[str] = []                     # already exists
    unfiltered_lexical_count: int | None = None     # NEW — second SQL probe; see below
```

**Naming honesty** (rev 2): the previous spec used `lexical_hits` / `lexical_hits_after_filters` and implied filter-drop detection on every response. That implication was wrong. The repo's SQL applies `gene`, `nbk_id`, and `sections` filters inside the `WHERE` clause (`repository.py:241-248`), so the lexical-row count returned by `search_passages` is **already post-filter**. The correct semantics:

- `lexical_candidate_count` and `dense_candidate_count` describe candidates **after** SQL filters. They are the right numbers for "did rerank have anything to work with?" — useful, honest.
- "Did filters drop everything?" is answerable only via a second probe. We do this **only when results are empty**, by re-running the lexical SQL with `gene/nbk_id/sections` set to NULL — once. Populates `unfiltered_lexical_count`. If non-zero, the existing `section-filter-drops-all` / `gene-filter-drops-all` suggestion codes fire.

This avoids inventing data the system doesn't have, keeps the non-empty path fast, and gives consumers a real signal on the dead-end path.

Build path at `passages.py:347`: drop the `if not out` guard for the always-on fields. The empty-result branch additionally issues the unfiltered probe and populates `unfiltered_lexical_count` + `suggestions`. The `_meta.diagnostics` block is non-null on every successful response.

### `ids_only` mode includes diagnostics (rev 2)

The current `ids_only` branch returns early at `passages.py:279-293`, before `diagnostics_model` is built. Refactor so that:

1. Diagnostics is constructed **once**, before the mode branch.
2. All three mode branches (`brief`, `full`, `ids_only`) include `_meta.diagnostics` in their response.
3. The lean `ids_only` row gains the top-level `rrf_score` and `lexical_rank_position` (it already has `rrf_score`; add `lexical_rank_position`).

### Rerank enum (factually correct)

The route accepts `Literal["rrf", "lexical", "off"]` (`passages.py:202`). The previous spec wrote `rrf|dense|bm25`, which is wrong — no such values are accepted. We do not introduce a behavioral change to this enum in Batch A+E.

Tool description text:

> `rerank`: Values: `"rrf"` (default; reciprocal-rank fusion of weighted lexical + dense embedding rank — best for clinical-concept queries), `"lexical"` (weighted lexical score with section-priority tiebreaker — best for exact gene-symbol or variant strings), `"off"` (raw repo order — debugging only; do not rely on order).

`mode` description gains its inline values likewise (`brief`, `full`, `ids_only`).

`get_chapter_section`'s `section` parameter description gains the 8-value closed vocabulary inline.

### `query` accepted alongside `q` (with HTTP-verified dual params)

The previous spec proposed `Query(alias="query")`. Local HTTP verification confirms FastAPI 0.136.1 makes the **alias** the canonical wire name — `?query=foo` returns 200 but `?q=foo` returns 422. That breaks any existing client. Correct approach: dual optional params, resolved in the handler.

```python
async def search_passages(
    ...,
    q: Annotated[str | None, Query(min_length=1, max_length=512, description="...")] = None,
    query: Annotated[str | None, Query(min_length=1, max_length=512, description="Alias for q. ...")] = None,
    ...
):
    query_str = q if q is not None else query
    if not query_str:
        raise StructuredHTTPException(status_code=422, code="missing_query", ...)
    ...
```

Behavior: any single one works; passing both with different values raises 422 with code `conflicting_query_param`. The MCP tool schema needs verification — FastMCP may serialize this in a way that requires testing. Plan adds an MCP-side smoke test.

### `heading_path_contains` on `search_passages`

The retrieval repository's `search_passages` does **not** currently accept `heading_path_contains` (it lives on the chapter-section path). Add it as a kwarg threaded through to a new SQL `AND p.heading_path ILIKE '%' || $N || '%'` predicate inside the `cand` CTE at `repository.py:246-248`. Sanitize via parameter binding only; never inline.

Surfaces filter inclusion in `_meta.diagnostics.applied_filters` if set.

### Server-instruction drift

In whichever module the MCP instructions string is built (verify during impl — likely `genereview_link/server_manager.py`), replace `POST /passages/batch` with `get_passages_batch` and audit every other tool-name mention against `operation_id` registrations.

## Data flow

No DB or ingest changes. Edits are in:

- `genereview_link/api/routes/passages.py`
- `genereview_link/api/routes/chapters.py`
- `genereview_link/models/genereview_models.py`
- `genereview_link/retrieval/rerank.py` (set `lexical_rank_position` / `dense_rank_position` on returned rows)
- `genereview_link/retrieval/repository.py` (add `heading_path_contains` kwarg to `search_passages`)
- MCP instructions string
- `genereview_link/api/resources/usage.py`

## Error handling

No new error paths beyond the dual-query conflict case (`conflicting_query_param`).

## Testing

**Unit:**

- `tests/test_routes_passages.py`:
  - assert `rrf_score`, `lexical_score`, `lexical_rank_position` are non-null in default response (when rerank produces them).
  - assert `score_breakdown is None` by default; non-null when `include=score_breakdown`.
  - assert `_meta.diagnostics.rerank_used == "rrf"`, `lexical_candidate_count >= 1`, `dense_candidate_count >= 1`.
  - assert `ids_only` response includes `_meta.diagnostics`.
- Dual-query: both `?q=foo` and `?query=foo` succeed and resolve identically. Passing both with different values returns 422 with `conflicting_query_param`.
- `tests/unit/test_api_passages_search_heading_path.py` (NEW) — filter restricts results to matching heading paths before rerank.
- Empty-result + filtered diagnostics — `?q=zzz&sections=management` returns `unfiltered_lexical_count` and suggestion code.

**MCP integration:**

- `tests/test_mcp_search_passages_params.py` (NEW) — verify both `q` and `query` reach the same handler via FastMCP tool invocation.

**Smoke:**

- `tests/smoke/phase_11_score_visibility.sh` — probes BRCA query and the alias path; verifies `rrf_score` and diagnostics shape.

## Migration / rollout

No DB migration. No reingest. No corpus_version bump.

Schema is additive — no consumer reading existing fields will break. Tag `phase-11-score-visibility-v1` after merge.

## Out of scope (explicit)

- All ranking-quality work — Batch B.
- Discoverability/navigation features — Batch C.
- Section TOC inside `get_chapter_metadata` — Batch D.
- Citation-format variants — Batch F.
- `anyOf:[X,null]` schema rewrite — deferred until a real consumer reports it as friction (Pydantic does not produce the desired form via `model_config` alone).
