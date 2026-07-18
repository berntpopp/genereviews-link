# Issue #43 — Distinguish primary genes from mentioned genes

**Date:** 2026-06-12  
> Historical record

**Issue:** #43 — BRCA1=FANCS causes false-positive Fanconi hits  
**Effort:** M  
**PR strategy:** Single PR (all changes are additive, backward-compatible)

---

## 1. Summary & Goal

`gene=BRCA1` searches return Fanconi Anemia passages because the FA chapter
lists BRCA1 (= FANCS alias) in its `gene_symbols` flat array alongside the
canonical FA genes.  The GIN `@>` query at
`retrieval/repository.py:167,377` cannot distinguish "BRCA1 is the defining
gene" (HBOC, NBK1247) from "BRCA1 is mentioned as an alias" (FA chapter).
The fix adds a `primary_gene_symbols` column populated from the NXML chapter
title during ingest, exposes a `gene_role` query param (default `any`,
backward-compatible), and boosts primary-match passages in the reranker.

---

## 2. Chosen Approach: Option A

**Option A is the right choice.** Rationale vs the alternatives:

- **Option B** (per-passage `chapter_role`) requires serialisation work on
  every search response and joins a derived field that has no natural home in
  the passage-level schema.
- **Option C** (boost first array element) is explicitly flagged in the issue
  as fragile — `gene_symbols` is set-typed with no ordering guarantee across
  ingestion runs.
- **Option A** is additive: a new column defaults to `'{}'`, the GIN index is
  partial-friendly, and the existing `gene=` filter keeps working unchanged.
  The primary symbols are known at NXML parse time from the chapter title —
  the same XML element that already produces `chapter.title` at
  `corpus/nxml.py:182-183`.

---

## 3. Schema Migration

### Migration file

`genereview_link/db/migrations/data/0006_primary_gene_symbols.sql`

(The existing series is `0001`–`0005`; next is `0006`.)

```sql
-- 0006_primary_gene_symbols.sql
-- Add primary_gene_symbols to genereview_chapters.
-- Existing rows keep the default '{}'; they will be repopulated on the
-- next full ingest (see rollout notes below).
--
-- Rollback is manual:
--   drop index genereview_chapters_primary_gene_gin;
--   alter table genereview_chapters drop column primary_gene_symbols;

alter table genereview_chapters
    add column if not exists primary_gene_symbols text[] not null default '{}';

create index if not exists genereview_chapters_primary_gene_gin
    on genereview_chapters using gin (primary_gene_symbols);
```

### Backfill strategy

The migration runner (`db/migrate.py:89`) is called by `prepare_staging`
(`corpus/pipeline.py:38`) before every ingest, so the new column and index
are created in `genereview_staging` at the start of the next ingest run. All
rows written by `copy_chapters` (`corpus/parallel.py:171`) include the new
column value and will be populated correctly. The `atomic_swap` at
`corpus/pipeline.py:76` promotes the fully-populated staging schema.

**A full re-ingest is required** to populate `primary_gene_symbols` in the
live schema. There is no in-place SQL backfill because the title-to-symbols
parsing is Python logic, not SQL-expressible without a stored procedure. The
operational cost is the same as a standard corpus refresh (typically a few
minutes; the NCBI tarball download dominates). There is no data loss: the
column defaults to `'{}'` on existing installations; `gene_role=any` continues
to use `gene_symbols` and works without re-ingest.

---

## 4. Ingest Changes

### 4a. Parsing rule — `extract_primary_gene_symbols(title, sidedata_genes)`

Add a new helper in `corpus/nxml.py` (already the home of `_join_authors`,
`_text`, `_parse_pub_date`). The chapter title is available at line 183 as
`title = _text(title_el) or short_name`.

**Algorithm:**

1. Intersect the words extracted from the title against the chapter's known
   `sidedata_genes` (the tuple from `sidedata.gene_symbols[nbk_id]`).
2. Return the intersection in the order they appear in `sidedata_genes`.

```python
import re

def extract_primary_gene_symbols(
    title: str,
    sidedata_genes: tuple[str, ...],
) -> tuple[str, ...]:
    """Return the subset of sidedata_genes that appear as whole words in title."""
    title_upper = title.upper()
    return tuple(
        g for g in sidedata_genes
        if re.search(r"\b" + re.escape(g.upper()) + r"\b", title_upper)
    )
```

Why title, not `<contrib-group>`? Authors are not gene symbols. The issue
states "NXML `<contrib-group>` / chapter-title parsing" but
`<contrib-group>` holds author names. The chapter title is the correct
source — "BRCA1- and BRCA2-Associated Hereditary Breast and Ovarian Cancer"
titles the HBOC chapter; "Fanconi Anemia" titles the FA chapter. Matching
against `sidedata_genes` avoids free-text hallucination and keeps the primary
set consistent with the existing `gene_symbols` array.

**Examples:**

- NBK1247 title `"BRCA1- and BRCA2-Associated Hereditary Breast and Ovarian Cancer"`,
  `sidedata_genes=("BRCA1","BRCA2")` → primary: `("BRCA1","BRCA2")`.
- FA chapter title `"Fanconi Anemia"`,
  `sidedata_genes=("FANCA","FANCB",...,"FANCS")` → primary: `()` (no gene
  symbols appear in the title).

### 4b. ChapterRecord changes

`corpus/records.py:13` — add one field to `ChapterRecord`:

```python
primary_gene_symbols: tuple[str, ...] = ()
```

### 4c. Ingest wiring — `corpus/pipeline.py:188-202`

In `run_full_ingest` at the sidedata-join reconstruction block (lines 189-201),
set the new field:

```python
sidedata_gs = sidedata.gene_symbols.get(chapter.nbk_id, ())
chapter = ChapterRecord(
    ...
    gene_symbols=sidedata_gs,
    primary_gene_symbols=extract_primary_gene_symbols(chapter.title, sidedata_gs),
    ...
)
```

`extract_primary_gene_symbols` is imported from `corpus/nxml.py`.

### 4d. `copy_chapters` — `corpus/parallel.py:171-211`

Extend the `records` tuple and `columns` list to include `primary_gene_symbols`:

```python
list(c.primary_gene_symbols),   # new element in the tuple
# ...
"primary_gene_symbols",          # new column name
```

---

## 5. Query / Ranker Changes

### 5a. `gene_role` query param — `api/routes/passages.py:150`

Add after the existing `gene` param (line 158):

```python
gene_role: Annotated[
    Literal["any", "primary", "mentioned"],
    Query(
        description=(
            "Filter by gene role in the chapter. 'any' (default): gene in "
            "gene_symbols (current behaviour). 'primary': gene in "
            "primary_gene_symbols (chapter-defining gene). 'mentioned': gene "
            "in gene_symbols but NOT in primary_gene_symbols."
        ),
    ),
] = "any",
```

Pass it through to `repo.search_passages(...)` and
`repo._dense_candidates_filtered(...)` at lines 322 and 334.

### 5b. `search_passages` SQL — `repository.py:377`

Current filter: `and ($3::text is null or $3 = any(c.gene_symbols))`

Replace with a role-aware form (add `$10::text` as the new `gene_role` param):

```sql
and ($3::text is null or (
    $10 = 'any'      and $3 = any(c.gene_symbols)
    or $10 = 'primary'   and $3 = any(c.primary_gene_symbols)
    or $10 = 'mentioned' and $3 = any(c.gene_symbols)
                     and not ($3 = any(c.primary_gene_symbols))
))
```

Apply the same role-aware filter in `build_dense_candidates_sql` at line 167.

### 5c. Reranker boost — `retrieval/rerank.py`

Add a new multiplier constant and apply it in `rerank_with_embeddings`:

```python
PRIMARY_GENE_BOOST = 1.25   # chapters where queried gene is primary rank higher
```

The `LexicalPassageRow` dataclass (`repository.py:59`) already has
`role_multiplier` and `adjusted_score`; add a `primary_gene_match: bool = False`
field. In the route (`passages.py:364`), when constructing
`dense_only_rows` and `lex_rows`, set `primary_gene_match=True` if
`gene and gene in row.passage.primary_gene_symbols`.

In `adjusted_score_for` (`rerank.py:121`) extend the signature with
`primary_gene_match: bool = False` and multiply the result:

```python
if primary_gene_match:
    adjusted = adjusted * PRIMARY_GENE_BOOST
```

The boost of 1.25 is a starting value, tunable via the ranking bench at
`tests/fixtures/ranking_bench.jsonl`.

For `gene_role='any'` with `gene=None`, `primary_gene_match=False` always —
no behaviour change.

**Affected lines (summary):**
- `rerank.py`: `adjusted_score_for` (line 121), `rerank_with_embeddings` (line 205)
- `repository.py:59` `LexicalPassageRow`, line 167, line 377, `_row_to_passage` (line 533–545) — add `primary_gene_symbols` column to every `JOIN genereview_chapters` SELECT
- `passages.py:150` (param), 322, 334, 364, 397

---

## 6. Tests

### Unit — `tests/unit/test_corpus_nxml.py`

Add `test_primary_gene_symbols_from_title()`:

```python
def test_primary_gene_symbols_from_title() -> None:
    from genereview_link.corpus.nxml import extract_primary_gene_symbols
    # HBOC chapter: BRCA1 and BRCA2 appear in the title
    assert extract_primary_gene_symbols(
        "BRCA1- and BRCA2-Associated Hereditary Breast and Ovarian Cancer",
        ("BRCA1", "BRCA2"),
    ) == ("BRCA1", "BRCA2")
    # FA chapter: no gene symbols appear in the plain title
    assert extract_primary_gene_symbols(
        "Fanconi Anemia",
        ("FANCA", "FANCB", "FANCS"),  # BRCA1 alias
    ) == ()
    # Gene not in sidedata_genes is never returned
    assert extract_primary_gene_symbols("BRCA1 Cancer", ()) == ()
```

### Unit — `tests/unit/test_corpus_sidedata.py` or new `test_corpus_pipeline_primary.py`

Assert that after the sidedata join, NBK1247 gets `primary_gene_symbols`
populated and a hypothetical FA chapter (whose title doesn't contain FANCS)
does not:

```python
def test_chapter_record_primary_gene_symbols() -> None:
    from genereview_link.corpus.nxml import extract_primary_gene_symbols
    # NBK1247 sidedata genes: BRCA1, BRCA2
    assert "BRCA1" in extract_primary_gene_symbols(
        "BRCA1- and BRCA2-Associated Hereditary Breast and Ovarian Cancer",
        ("BRCA1", "BRCA2"),
    )
    # FANCS alias should NOT appear as primary for FA (title is "Fanconi Anemia")
    assert "BRCA1" not in extract_primary_gene_symbols(
        "Fanconi Anemia",
        ("FANCA", "FANCB", "FANCS"),
    )
```

### Integration ranking test — `tests/test_ranking_filter_scope.py` or `tests/test_api_integration.py`

Add a test against the DB fixture (mocked chapter rows) asserting:

> `search_passages(q="BRCA1 risk-reducing surgery", gene="BRCA1", gene_role="primary")`
> returns passages from NBK1247 (HBOC) and NOT from the FA chapter NBK.

This mirrors the acceptance criterion from the issue exactly.

---

## 7. Risks & Rollout

| Risk | Mitigation |
|------|-----------|
| Re-ingest required | Column defaults to `'{}'`; `gene_role=any` (default) is unaffected until re-ingest. Deploy migration first, re-ingest in the next scheduled corpus refresh. |
| `passages.py` is allowlisted at 741 LOC | Adding ~12 LOC for `gene_role` param stays within ceiling. Flag in commit message. |
| `repository.py` is allowlisted at 915 LOC | SQL modifications are in-place replacements; net change ~+10 LOC. Stays within ceiling. |
| GIN index cost | A second `text[]` GIN index on `genereview_chapters` is small (~700 chapters). Negligible. |
| Alias collapse | The regex `\b<GENE>\b` match against `sidedata_genes` means we rely on NCBI sidedata being accurate for primary assignment, not title NLP. This is intentional and avoids hallucination. |
| Backward compat | `gene_role` defaults to `any`; existing callers see identical behaviour. The new `primary_gene_symbols` column is default `'{}'` so queries on the old data return empty primary sets, not errors. |

**Single PR:** Yes. Migration + ingest + ranker + tests are all additive. The
only operational constraint is that the ranker boost for `gene_role=any` only
activates on chapters that have been re-ingested with the new parser. Ship in
one PR; coordinate re-ingest with the next scheduled corpus refresh.

---

## 8. Effort Estimate & Task Checklist

**Effort: M** (~1–2 days implementation + test, excluding re-ingest time)

- [ ] Add `extract_primary_gene_symbols` helper in `corpus/nxml.py`
- [ ] Add `primary_gene_symbols: tuple[str, ...] = ()` to `ChapterRecord` in `corpus/records.py`
- [ ] Wire `extract_primary_gene_symbols` into `run_full_ingest` at `corpus/pipeline.py:195`
- [ ] Extend `copy_chapters` tuple and columns list in `corpus/parallel.py:177-210`
- [ ] Write `genereview_link/db/migrations/data/0006_primary_gene_symbols.sql`
- [ ] Add `primary_gene_symbols` to every `JOIN genereview_chapters` SELECT in `retrieval/repository.py` (lines ~167, 356, 491, 514, 565, 609, 628, 657); add to `_row_to_passage` return at line 545; update `ChapterRow` and `ChapterMetadataRow` dataclasses if exposed in the API
- [ ] Add `gene_role` param to `api/routes/passages.py:150`; pass to repo + dense calls
- [ ] Update `build_dense_candidates_sql` gene filter at `repository.py:167` for role awareness
- [ ] Update lexical SQL gene filter at `repository.py:377` for role awareness
- [ ] Add `primary_gene_match: bool` to `LexicalPassageRow`; populate in route; apply boost in `rerank.py`
- [ ] Add `PRIMARY_GENE_BOOST = 1.25` constant and wire into `adjusted_score_for` in `rerank.py`
- [ ] Write unit tests: `extract_primary_gene_symbols` (title parsing)
- [ ] Write integration/ranking test: HBOC above FA for `gene=BRCA1 gene_role=primary`
- [ ] Run `make ci-local` — confirm lint-loc ceilings hold
- [ ] Update `genereview://usage` resource doc to describe `gene_role` param
