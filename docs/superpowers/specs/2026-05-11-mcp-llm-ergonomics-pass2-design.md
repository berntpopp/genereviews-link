# MCP LLM-Ergonomics Pass 2 — Design Spec

**Date:** 2026-05-11
**Status:** Draft, awaiting user review
**Predecessor:** `2026-05-11-mcp-llm-ergonomics-design.md` (shipped)
**Scope:** Lift the deployed MCP server from consumer-rated 8.2/10 toward
~9.2/10 by addressing the two systematic weaknesses every reviewer
flagged (broken metadata promises, table-blindness) plus the highest-
leverage missing affordances (neighbors, chapter metadata, empty-result
diagnostics).

## Motivation

Seven independent LLM consumer reviews of the post-Pass-1 server scored
it 8.0–8.8 (mean 8.2) and converged on the same gaps:

| Review-cluster issue | Reviewers | Severity |
|---|---|---|
| `chapter_last_updated: null` everywhere | 6/7 | Trust (broken promise) |
| Tables / lists / figures not retrievable | 3/7 | Content (whole answers missing) |
| `score_breakdown.rrf_score` and `dense_rank` always null | 2/7 | Trust (broken promise) |
| `score_breakdown` shipped by default (~30% payload waste) | 3/7 | Polish |
| `get_chapter_section` duplicates `passages[]` and `concatenated_text` | 3/7 | Polish |
| No `get_neighboring_passages` analog | 3/7 | Missing affordance |
| No `get_chapter_metadata` / section listing | 4/7 | Missing affordance |
| Empty result has no diagnostic signal | 3/7 | Missing affordance |
| Tokenizer-leak in stored text (`"low - density lipoprotein cholesterol"`) | 3/7 | Content quality |
| `get_license` should be a resource, not a tool | 1/7 | API hygiene |

The strengths the reviewers explicitly called out are preserved unchanged:
server-level instructions block, `passage_id` citation contract,
`_meta.attribution` envelope, `mode=brief`/`mode=full` split, section enum,
safety framing, structured 404s.

## Goals

- Resolve every "broken promise" field (Trust pass).
- Make tables first-class retrievable content (Content pass).
- Add the three highest-asked affordances (Discovery pass).
- Land the polish items that have unanimous reviewer consensus
  (Polish pass).
- Keep the citation contract, server instructions, and brief/full split
  intact.

## Non-goals

- New ranking strategies or embedding-model swap.
- Eval harness construction.
- API authentication or rate-limiting changes.
- Multi-tenant cache invalidation.
- UI / dashboard work.

## Stance

- **Back-compat:** break freely. This is pre-1.0 ergonomic iteration.
  Three breaking changes are accepted (see "Breaking changes" below).
- **Corpus rebuild:** full re-scrape + re-chunk + re-embed of all 882
  chapters, scheduled at end of Phase 7. Some narrative `passage_id`s
  will shift `chunk_index` because tables are interleaved among them.
- **Phasing:** four phases, sequential, each with its own gate. Phase 5
  is small and additive; Phase 7 is the largest and is on its own
  commit train.

## Phase structure

| Phase | Theme | Risk | Sequencing |
|-------|-------|------|------------|
| Phase 5 — Trust | Backfill `chapter_last_updated`, wire `rrf_score`/`dense_rank`, flip `exclude=score_breakdown` default, lock `exclude` bug | Low (additive) | First |
| Phase 6 — Discovery | `get_passage(neighbors)`, `get_chapter_metadata`, empty-result diagnostics, drop `concatenated_text` default | Low-medium (one breaking shape change) | Second |
| Phase 7 — Content | Tables as `passage_type="table"` passages + `get_table` tool, fix tokenizer-leak normalization, verify section ordering, full corpus rebuild | High (DB migration + rebuild + passage_id shifts) | Third |
| Phase 8 — Polish | `get_license` as MCP resource, latency hints in tool descriptions, structured 422 for nested-q, HGNC alias guidance, optional dedup on chapter section | Low | Fourth |

**Phase gates** (each phase ends with):
- `make ci-local` green
- Targeted live MCP smoke test against gr-pg corpus (curl probes per
  phase, shipped as `tests/smoke/phase_N.sh`)
- Annotated git tag `phase-N-ergonomics-v2`

Phase 7 additionally requires the full corpus rebuild verified by
post-rebuild smoke checks before tag.

## Phase 5 — Trust pass

### T1.1 — Backfill `chapter_last_updated`

**Current state:** 0 of 882 chapters in the gr-pg corpus have
`last_updated_date` populated. The scraper code at `corpus/nxml.py:76`
calls `last_updated_date=updated`, so either the NXML element is being
read from the wrong path or the parse silently fails per-chapter.

**Investigation step (first task):** Pull a sample NXML from Bookshelf
for NBK1247, inspect the actual element shape, compare to the parser's
expectation. Likely fixes are an XPath mismatch, a date-format mismatch,
or a swallowed exception.

**Implementation:** Fix the parser. Confirm via unit test against a
fixture NXML.

**Backfill:** Phase 5 only guarantees that *new* corpus loads populate
the field correctly. The full backfill happens during the Phase 7
rebuild.

**Acceptance:** Unit test parses a fixture NXML and asserts
`chapter.last_updated_date == date(YYYY, M, D)`. Existing rows still
null after Phase 5 — fine.

### T1.2 — Wire `rrf_score` + `dense_rank` through

**Current state:** `retrieval/rerank.py:94-99` already computes a
`dense_rank` dict and an `rrf` score function, but neither is preserved
on `LexicalPassageRow`. `RankedPassage` construction at
`api/routes/passages.py:202-203` and `api/routes/debug.py:71-72`
hardcodes both as `None`.

**Implementation:**

1. Add `dense_rank: int | None` and `rrf_score: float | None` to
   `LexicalPassageRow` in `retrieval/repository.py`.
2. In `rerank_with_embeddings`, populate those fields on each row before
   returning the sorted list.
3. In `passages.py:202-203` and `debug.py:71-72`, replace
   `dense_rank=None, rrf_score=None` with the row's values.
4. With `rerank=lexical` or `rerank=off`, both fields stay `None` —
   correct, RRF wasn't computed.

**Acceptance:** Integration test: `search_passages?q=BRCA1&rerank=rrf`
returns top hit with non-null `score_breakdown.rrf_score` and non-null
`dense_rank`. With `rerank=lexical`, both null.

### T4.1 — Flip `exclude=score_breakdown` default

**Current state:** `score_breakdown` shipped on every result row by
default; reviewers consistently called it ~30% of brief-mode payload
waste.

**New schema:** Add an `include` parameter parallel to the existing
`exclude`. `score_breakdown` becomes opt-in via `include=score_breakdown`.

- `include: list[Literal["score_breakdown"]] | None = None` (default
  omits `score_breakdown` from response rows).
- `exclude: list[Literal["score_breakdown", "heading_path"]]` schema
  unchanged. `exclude=["score_breakdown"]` still validates but is now a
  redundant no-op (the field is excluded by default anyway). This keeps
  Phase 5 truly additive — old callers that explicitly excluded
  `score_breakdown` are unaffected.

**Why parallel `include` rather than just inverting `exclude`:**
keeps the parameters semantically clear (each affects a disjoint field
set when combined with the new default).

**Server instructions:** updated to mention `include=["score_breakdown"]`
for ranker debugging.

**Acceptance:** `search_passages?q=foo` returns rows without
`score_breakdown` field. `search_passages?q=foo&include=score_breakdown`
includes it. Mean brief-mode payload drops ~25-30%.

### T4.2 — Confirm `exclude` output-validation bug

**Status:** One reviewer reported `exclude=["score_breakdown"]`
triggering `Output validation error: 'score_breakdown' is a required
property`. After T4.1, `score_breakdown` becomes Optional in the
response model, which makes T4.2 effectively moot.

**Action:** Add a route-test matrix locking the new behavior:
`exclude=heading_path`, `include=score_breakdown`, neither, both — all
must return valid responses with the expected fields present/absent.

## Phase 6 — Discovery pass

### T3.1 — Extend `get_passage` with neighbors

**Endpoint:** `GET /passages/{passage_id}?neighbors=N&cross_sections=false`

**Response:**

```jsonc
{
  "passage": PassageDetail,                  // unchanged shape
  "neighbors_before": list[PassageDetail],   // length 0..N
  "neighbors_after": list[PassageDetail],    // length 0..N
  "has_more_before": bool,                   // true iff section/chapter edge truncated
  "has_more_after": bool,
  "_meta": ResponseMeta                      // unchanged
}
```

**Constraints:**

- `neighbors: int` in `[0, 5]`, default `0`. With `0`, the focal-passage
  shape is preserved and both neighbor arrays are empty.
- `cross_sections: bool`, default `false`. Neighbors stop at section
  boundaries. With `true`, they wrap to the previous/next section in
  `chunk_index` order.
- `has_more_before`/`has_more_after` flag the LLM that more context
  exists past the returned window.

**Repository method:** new `get_passage_window(passage_id, before, after,
cross_sections)` returning `(focal, before_rows, after_rows,
has_more_before, has_more_after)`. Single SQL with two CTEs (focal +
window) avoids N+1.

**Acceptance:** Integration tests for `neighbors=0` (single-passage
shape preserved), `neighbors=2` mid-section (returns 2+2),
`neighbors=2` at section boundary (returns whatever's available +
`has_more_*=false`), `cross_sections=true` at section boundary (returns
from adjacent section).

### T3.2 — `get_chapter_metadata(nbk_id)` tool

**Endpoint:** `GET /chapters/{nbk_id}/metadata`

**Response:**

```jsonc
{
  "nbk_id": "NBK1247",
  "title": "BRCA1- and BRCA2-Associated Hereditary Breast and Ovarian Cancer",
  "chapter_last_updated": "2023-09-21",      // may be null in Phase 6, populated post-Phase 7
  "gene_symbols": ["BRCA1", "BRCA2"],
  "sections": [
    {"section": "summary", "passage_count": 3},
    {"section": "diagnosis", "passage_count": 8},
    {"section": "clinical_features", "passage_count": 22},
    {"section": "management", "passage_count": 14},
    {"section": "genetic_counseling", "passage_count": 5},
    {"section": "molecular_genetics", "passage_count": 7},
    {"section": "references", "passage_count": 0}
  ],
  "table_count": 0,                          // populated in Phase 7
  "_meta": ResponseMeta
}
```

**Why `passage_count` per section:** lets the LLM size calls (skip a
22-chunk section if 3-chunk summary suffices). Empty sections are still
listed so the LLM doesn't waste calls.

**Why `table_count` is in Phase 6 schema already:** zero-cost field,
becomes populated automatically once Phase 7 ships. Better to commit the
shape once.

**Repository:** single SQL with `count(*) group by chapter_section` over
the chapter's passages. 404 with structured payload if `nbk_id` doesn't
exist (mirrors existing 404s).

**Acceptance:** Known chapter returns expected sections + counts;
unknown nbk_id returns structured 404 with `code="chapter_not_found"`.

### T3.3 — Empty-result diagnostics in `search_passages`

When `len(results) == 0`, attach a `diagnostics` block to `_meta`:

```jsonc
"_meta": {
  "attribution": "...",
  "corpus_version": "2026-05-10",
  "diagnostics": {
    "lexical_hits": 0,
    "lexical_hits_after_filters": 0,
    "dense_max_score": 0.42,                 // null if rerank != "rrf"
    "applied_filters": ["gene=BRCA9", "sections=management"],
    "suggestions": [
      "drop the gene filter (no chapters indexed for 'BRCA9' — did you mean BRCA1 or BRCA2?)",
      "broaden q (current query is very specific)",
      "try sections=clinical_features instead of management"
    ]
  }
}
```

**Trigger rules:**

- Always emit `diagnostics` when `len(results) == 0`.
- Suppress otherwise (keeps payload tight on the common path).

**Suggestion generation:** rule-based, not LLM-generated. Three rules:

1. If `applied_filters` includes `gene=X` and
   `lexical_hits_after_filters < lexical_hits / 10`, suggest dropping
   the gene filter. Optionally Levenshtein-suggest a close gene from
   indexed symbols.
2. If `len(q) > 80` chars or query has >8 tokens, suggest broadening.
3. If `applied_filters` includes `sections=X` and `lexical_hits` is
   non-zero pre-filter but post-filter is zero, suggest other sections.

**Acceptance:** Integration tests: zero-result query returns
`diagnostics` with non-empty suggestions; non-zero result query has no
`diagnostics` key in `_meta`.

### T3.5 — Drop `concatenated_text` default from `get_chapter_section`

**Current:** `api/routes/chapters.py:87` returns both `passages[]` and
`concatenated_text`. Reviewers consistently called this ~50% redundant
payload.

**New:**

- Default response returns `passages[]` only.
- `?include=concatenated_text` opts into the joined string.
- Existing join logic preserved; gated behind the param.

**Breaking change** (accepted under "break freely" stance).

**Acceptance:** `GET /chapters/NBK1247/sections/management` returns
`passages[]` only; `?include=concatenated_text` returns both.

## Phase 7 — Content pass

Largest and riskiest. DB migration + scraper + chunker changes + full
corpus rebuild.

### T2.1 — Tables as passages + `get_table` tool

**Schema migration:**

```sql
alter table public.genereview_passages
  add column passage_type text not null default 'narrative'
  check (passage_type in ('narrative', 'table'));
create index passages_type_chapter_idx
  on public.genereview_passages(nbk_id, passage_type);

alter table public.genereview_passages
  add column table_data jsonb;
create index passages_table_data_idx
  on public.genereview_passages(nbk_id, table_data)
  where passage_type = 'table';
```

`default 'narrative'` makes the migration safe for existing rows.
`table_data` is populated only for `passage_type='table'`.

**Scraper changes (`corpus/nxml.py`):**

- Detect `<table-wrap>` elements with `<table>` children.
- Extract caption from `<caption>/<title>` and `<caption>/<p>`.
- Serialize the table as GitHub-flavored markdown (header row +
  alignment row + data rows, cell text whitespace-normalized).
- Compute stable `table_id`: prefer NXML `<table-wrap id="...">`
  attribute when present; otherwise ordinal `"table-N"` (1-indexed by
  document order).
- Emit one `Passage` per table with:
  - `passage_type="table"`
  - `chapter_section` = the section containing the table
  - `heading_path` = section's heading path with `" > Table N"` suffix
  - `text` = `f"{caption}\n\n{markdown_table}"` (caption first for
    embedding)
  - `chunk_index` = computed in source-order with table chunks
    interleaved among narrative chunks
  - `table_data` = `{"caption": str, "header": list[str], "rows":
    list[list[str]]}`

**Why interleaved `chunk_index` (not separate index range):** narrative
chunks reference "see Table 5". `get_passage(narrative_id, neighbors=2)`
should pick up the adjacent table. A separate range (e.g., 9000+) would
break that.

**Embeddings:** tables embed with the same BGE model as narrative.
Caption + first 1-2 rows usually carry enough semantics for retrieval.
For very large tables this may underperform; the structured `get_table`
tool is the precision escape hatch.

**`get_table(nbk_id, table_id)` tool:**

```
GET /chapters/{nbk_id}/tables/{table_id}
```

Response:

```jsonc
{
  "nbk_id": "NBK1247",
  "table_id": "table-5",
  "caption": "Risk-Reducing Mastectomy in BRCA1/2 Carriers",
  "heading_path": "Management > Treatment of Manifestations > Table 5",
  "section": "management",
  "header": ["Variant class", "Recommended modulator", "Min age", "Source"],
  "rows": [
    ["Class I", "elexacaftor/tezacaftor/ivacaftor", "6 yrs", "FDA 2023"]
  ],
  "passage_id": "NBK1247:0042",              // for cross-referencing
  "_meta": ResponseMeta
}
```

**404 behavior:** unknown `table_id` returns structured 404 with
`code="table_not_found"`, `valid_values` listing this chapter's known
tables, `next_commands` suggesting `get_chapter_metadata(nbk_id)`.

**`get_chapter_metadata.table_count`** (Phase 6 placeholder): now
populated from `count(*) where passage_type='table' and nbk_id=$1`.

### T2.2 — Fix tokenizer-leak text normalization

**Symptom:** stored text like `"low - density lipoprotein cholesterol
( ldl - c )"` and `"lynch syndrome ( crc )"` — lowercased + space-around-
punctuation, the signature of a tokenizer's `decode()` step (likely
Hugging Face slow tokenizer with default whitespace rules) leaking into
the persisted text column.

**Investigation:** grep for `tokenizer.decode`, `.lower()`,
`regex.sub.*\s` in `corpus/chunking.py` and `embeddings/`. The leak is
likely at one of:

1. The chunker calls a tokenizer to count tokens for chunk-size
   budgeting and reuses the decoded text instead of the original.
2. A normalization preprocessor meant for embedding input accidentally
   writes back to the stored text column.

**Fix:** keep the original NXML-derived text for the stored `text`
column. Tokenization for size-budgeting must use a separate variable
that is never persisted. If embeddings need normalized text, normalize
on the way *into* the embedding call, never on the way into the
database.

**Acceptance:** Re-scrape NBK1230 (Lynch syndrome) and NBK174884.
Assert stored text contains `"Lynch syndrome (CRC)"` not
`"lynch syndrome ( crc )"`. Add a regression unit test with a fixture
NXML containing punctuation-adjacent terms.

### T2.3 — Section ordering audit

**Status:** `repository.py:299` already has `order by p.chunk_index` on
`get_section`. The "scrambled order" complaint likely came from a build
that pre-dated this ordering or from multi-`section_level`
interleaving.

**Action:** Add an integration test asserting `chunk_index` is
monotonically increasing across the response for
`get_chapter_section(NBK1440, "management")`. If it fails, change
`order by p.chunk_index` → `order by p.section_level, p.chunk_index`.
Likely passes on current code.

### Operational: full corpus rebuild

Triggered after T1.1 + T2.1 + T2.2 + T2.3 land. Single
`make corpus-load` invocation against gr-pg DB.

**Sequence:**

1. Tag pre-rebuild snapshot: `pg_dump genereview > backups/pre-phase7.sql`
2. Run scraper + chunker + embedder for all 882 chapters (~30-60 min on
   GPU host).
3. New `corpus_version` row marked active (existing pipeline already
   does this).
4. Pre-tag smoke checks:
   - `select count(*) from genereview_passages where passage_type='table'` > 0
   - `select count(*) from genereview_chapters where last_updated_date is not null` ~ 882
   - Sample 5 narrative passages, assert no `" - "` artifacts around
     dashes/parens
5. Tag `phase-7-content-v2`.

**`passage_id` stability:** existing `NBKxxxx:NNNN` format preserved.
Chunks whose text didn't change retain `chunk_index`. New table-passages
get new `chunk_index` interleaved at scrape time. Some narrative chunks
WILL shift `chunk_index` because tables are interleaved among them.
This is unavoidable and accepted under "break freely". Document in
release notes; cached `passage_id`s should re-resolve via
`search_passages`.

## Phase 8 — Polish

### T4.3 — `get_license` becomes an MCP resource

License data is static, parameterless, and the server instructions
already say "call once per session" — textbook MCP resource use case.

- Register MCP resource at URI `genereview://license` returning the
  same payload structure as the current `get_license` response.
- Keep `GET /license` REST route unchanged.
- Remove the `get_license` MCP tool from FastMCP `from_fastapi`
  exposure (selective tool exclusion).
- Update server instructions: "fetch resource `genereview://license`
  once per session for attribution."

### T4.4 — Latency hints in tool descriptions

Measure during Phase 5 smoke tests and add one-line hints to each tool
description:

- `search_passages` p50: rrf vs lexical vs off
- `get_passage` p50: with/without `neighbors=3`
- `get_chapter_section` p50
- `get_chapter_metadata` p50
- `get_table` p50

Format example: `"Latency: rrf ~150ms p50, lexical ~30ms p50, off
~10ms p50."` Numbers go in FastAPI route docstrings (which become MCP
tool descriptions).

### T4.5 — Better 422 for nested-q

Custom 422 handler on `search_passages` detects "user sent a JSON
object instead of a query string" and returns structured
`MCPErrorPayload`:

- `code="query_must_be_string"`
- `recovery_hint="pass q as a top-level string parameter, not a nested object"`
- `next_commands=[{"tool": "search_passages", "arguments": {"q": "<your query string>"}}]`

### T4.6 — HGNC alias guidance in gene-filter 400s

Today gene filtering silently returns empty when the symbol doesn't
match an indexed canonical symbol.

- Validate `gene` against the indexed-symbols set at request time
  (cached in `app.state` alongside `corpus_version`).
- On miss, return 400 with `code="gene_not_indexed"`,
  `recovery_hint="use the canonical HGNC symbol; aliases (e.g., 'hMLH1'
  for 'MLH1') are not supported"`, plus Levenshtein-1 close matches in
  `next_commands`.

Symbol-set query: `select distinct unnest(gene_symbols) from
genereview_chapters`.

### T4.7 — Optional chunk overlap dedup

Optional `?dedupe=true` on `get_chapter_section` using a longest-
common-suffix/prefix heuristic to strip overlapping text between
adjacent chunks. Default `false` for back-compat. Useful for LLMs
feeding sections into a summarizer.

### Deferred (T4.8): `find_in_section` as tool vs prompt

Currently a prompt; reviewers asked for it as a tool too.

**Decision:** keep as prompt only. The prompt invokes
`search_passages(sections=[X], gene=Y)` then `get_chapter_section`,
both already-exposed tools. Promoting to a tool duplicates orchestration
the LLM can do itself. Revisit if Phase 8 metrics show the prompt is
rarely used because clients don't surface prompts well.

## Cross-cutting concerns

### Server instructions update

- Add `genereview://license` resource reference (Phase 8).
- Add `get_chapter_metadata` to the canonical pipeline paragraph
  (Phase 6).
- Add `neighbors=N` to the `get_passage` mention (Phase 6).
- Add `passage_type="table"` and `get_table` (Phase 7).
- Update payload-size budgets to reflect dropped `score_breakdown` and
  `concatenated_text` defaults.
- Keep the citation contract and safety framing unchanged.

### Testing strategy

- Per phase: unit tests for new logic + integration tests against test
  DB (port 5436 / `genereview_test`).
- Per phase: live smoke test against `genereview` corpus DB via curl
  probes (same pattern as the previous ergonomics PR), shipped as
  `tests/smoke/phase_N.sh`.
- Coverage floor stays at 70%; new code expected to land at >85% per
  file.
- New regression tests added for:
  - Phase 7: text normalization
  - Phase 7: section ordering
  - Phase 7: passage_id stability for non-rechunked passages

### Per-phase smoke checks

Phase 5:

- Top RRF hit has non-null `score_breakdown.rrf_score`
- `score_breakdown` absent by default
- `chapter.last_updated_date` non-null after Phase 7 rebuild

Phase 6:

- `get_passage?neighbors=2` returns ±2 with `has_more_*` flags
- `get_chapter_metadata` returns sections list with counts
- Zero-result query returns `_meta.diagnostics.suggestions`
- `get_chapter_section` no longer returns `concatenated_text` by default

Phase 7:

- `count(*) where passage_type='table'` > 0
- `get_table(NBK1247, "table-1")` returns structured rows
- Sample passage text contains proper case + no stray spacing

Phase 8:

- `genereview://license` resource resolvable
- 422 on nested-q returns structured payload
- 400 on invalid gene returns structured payload with Levenshtein
  suggestions

### Migration safety / rollback

- Phase 5 + Phase 6: pure code changes, revertible by `git revert`.
- Phase 7 schema migration: `add column ... default 'narrative'` is
  online-safe; backout migration drops the columns.
- Phase 7 corpus rebuild: rollback = restore from `pg_dump` snapshot
  taken before rebuild + revert code.

### Breaking changes (accepted)

| # | Change | Phase | Caller impact |
|---|---|---|---|
| 1 | `get_chapter_section` no longer returns `concatenated_text` by default | 6 | Add `?include=concatenated_text` if you relied on it |
| 2 | Some narrative `passage_id`s shift `chunk_index` | 7 | Re-resolve cached IDs via `search_passages` |
| 3 | `get_license` MCP tool removed (REST route stays) | 8 | MCP clients use `genereview://license` resource |

Document all three in release notes / commit messages.

## Effort estimate

- Phase 5: 1-2 days, ~3-4 commits, additive only.
- Phase 6: 3-5 days, ~6 commits, one breaking shape change.
- Phase 7: 1-2 weeks (mostly investigation + rebuild verification + DB
  migration + scraper changes), ~10-12 commits.
- Phase 8: 2-3 days, ~5 commits, one MCP-tool removal.

Total: ~3 weeks across the four phases, with each phase shippable
independently.

## Open questions for implementer

- Whether the `corpus_version` cache invalidation on `app.state`
  survives a corpus reload triggered by another process. Lifespan
  startup re-reads it; an in-flight server may serve a stale value
  briefly.
- Whether `get_chapter_metadata` should also expose dense-embedding
  health (e.g., "this chapter has embeddings: true"). Defer to Phase 6
  implementer judgment.
- Levenshtein library choice for gene-symbol close-matches in T3.3
  and T4.6. Recommend `python-Levenshtein` for speed; `rapidfuzz` if
  already a transitive dep (check `uv.lock`).
