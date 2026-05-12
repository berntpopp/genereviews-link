# Batch B — Ranking Quality (passage_role + intent-aware scoring) — Design Spec

**Date:** 2026-05-12 (rev 2 — addresses peer review)
**Branch target:** new branch off `main` after Batch A+E merges.
**Predecessor:** Batch A+E (score visibility) — needed to inspect resulting `rrf_score` and `dense_rank_position` per row.
**Successor:** Batch C — discoverability/navigation.

## Goal

Fix the most-cited ranking failure across six LLM-consumer reviews: for clinical-management queries, the wrong passage is returned first because a cross-reference passage gets a higher RRF score than the topically-correct evidence passage. Live confirmation: query `BRCA1 risk-reducing mastectomy salpingo-oophorectomy` still ranks `NBK1247:0035 (Genetic Counseling > Related Genetic Counseling Issues)` above `NBK1247:0024 (Management > Prevention of Primary Manifestations)`. The Management passage is the right answer.

Target: top-1 is the topically-correct passage for clinical-phase queries across a curated regression set of ≥10 reviewer-style prompts, without regressing baseline ordering on prompts that don't trigger intent.

## Critical architecture correction (rev 2)

The previous spec revision proposed role/intent as a **tail-tuple tiebreaker** applied to `(-rrf_score, …)`. That cannot fix the BRCA case. The current sort key at `genereview_link/retrieval/rerank.py:111-119` is:

```
key = (-rrf_score, -dense_score, section_priority, nbk_id, passage_id)
```

If NBK1247:0035 has *any* higher `rrf_score` (or any higher `dense_score`) than NBK1247:0024 — which it almost certainly does, because RRF picks up its title's lexical match — the section/role tail never fires.

**Corrected approach: role and intent must affect the score that sorts.** We introduce `adjusted_score`, computed once per row at rerank time, derived from `rrf_score` via a multiplicative role multiplier and an additive intent boost. Sort key becomes:

```
key = (-adjusted_score, -rrf_score, -dense_score, section_priority, nbk_id, passage_id)
```

The original `rrf_score` is preserved (and remains the value exposed by Batch A+E's top-level field) for transparency. `adjusted_score` is a new field surfaced in diagnostics and the deep `score_breakdown`. This is auditable, debuggable, and keeps the no-intent default behavior identical to today (when intent is empty and no passage is `cross_reference`, `adjusted_score == rrf_score`).

## Scope

Single phase (**Phase 12 — Ranking-Quality-v1**), reingest-bearing. Tag: `phase-12-ranking-quality-v1`.

**Reviewer themes addressed:**

| Theme | Reviewers | Approach |
|---|---|---|
| Top-1 wrong on clinical-management queries | 4/6 | Ingest-time `passage_role` + intent-aware `adjusted_score` |
| Section bias too weak | 4/6 | Intent-aware section boost folded into `adjusted_score` |
| Cross-reference vs evidence indistinguishable | 2/6 | `passage_role` persisted, surfaced on every search hit and `PassageDetail` |

**Approach: both** — ingest-time role classification **and** query-time intent boost — folded into a single `adjusted_score`.

**Explicit non-goals:**

- ML-based intent classifier.
- Embedding-model swap.
- Cross-encoder rerank.
- New rerank modes.
- User-facing `section_bias=` query param (deferred — depends on regression results).
- Deep section anchor URLs — Batch C.

## Architecture

### `passage_role` field — ingest time (taxonomy revised, rev 2)

A closed-vocabulary string on every passage. Persisted in `genereview_passages`, indexed.

```python
PassageRole = Literal[
    "evidence",         # primary content — narrative, recommendations
    "cross_reference",  # pointer passages: "See Genetic Counseling for related issues"
    "definition",       # short glossary-style entries
    "table_caption",    # table caption only
    "table_body",       # rendered table content
]
```

**`structural` dropped** (rev 2): the no-loss chunker (`nxml.py:_walk_section` → `_flush_now` → `_flush_paragraphs`) only emits a passage when `para_accumulator` has content. Heading-only stubs were the bug we already fixed in `97a67a1`. A `structural` value would have zero production occurrences and confuse downstream consumers.

**Classifier signature (rev 2):**

```python
def classify_passage_role(
    *,
    text: str,
    heading_path: str,
    passage_type: str,           # "narrative" | "table"
    char_count: int,
    caption_text: str = "",      # NEW — for passage_type=='table'; from ExtractedTable.caption
) -> PassageRole: ...
```

**Heuristics (in order):**

1. `passage_type == "table"`:
   - If `caption_text` is non-empty AND `char_count <= len(caption_text) * 2.0 + 50` → `table_caption`.
   - Else → `table_body`.
2. Cross-reference: `char_count <= 200` AND text contains one of `("see ", "refer to ", "for details", "for more information", "described in")` (case-insensitive, word-bounded for "see ") AND heading_path ends in one of `("Related", "Issues", "Counseling", "Information")` after splitting on " > " → `cross_reference`.
3. Definition: `char_count <= 120` AND matches `^\w[\w\s\-/()]{0,40}:\s+\S` (term-colon-body, narrow) → `definition`.
4. Default → `evidence`.

The classifier is deterministic, auditable, and tunable against a hand-labeled fixture set. False-positive direction: rule (2) is the only one that suppresses content; we keep its gates strict.

### `adjusted_score` — query time

Computed by an `apply_score_adjustments(rows, *, query_intents, dense_scores) -> rows` step inside `rerank_with_embeddings`, after `rrf_score` is computed but before the final sort.

```python
# Multiplicative role multiplier:
ROLE_MULTIPLIER: Mapping[str, float] = {
    "cross_reference": 0.4,   # dampen — keeps ordering within role but ranks below evidence
    "evidence":         1.0,
    "definition":       0.95, # mild dampen — definitions outrank only when content is sparse
    "table_caption":    0.85, # captions are pointers; body content is in table_body
    "table_body":       1.0,
}

# Intent-keyed additive boosts (proportional to rrf_score so the effect scales):
QUERY_INTENT_BOOSTS: dict[str, dict[str, object]] = {
    "management": {
        "patterns": ["treatment", "management", "therapy", "surgery", "prophylactic",
                     "risk-reducing", "screening", "surveillance", "intervention",
                     "prevent", "prevention", "managing"],
        "section_boost": {"management": 0.30},   # 30% of rrf_score, additive
    },
    "diagnosis": {
        "patterns": ["diagnosis", "diagnostic criteria", "establishing", "confirming",
                     "differential", "differential diagnosis"],
        "section_boost": {"diagnosis": 0.30, "clinical_features": 0.10},
    },
    "genetics": {
        "patterns": ["inheritance", "penetrance", "autosomal", "x-linked",
                     "variant spectrum", "molecular genetics"],
        "section_boost": {"molecular_genetics": 0.20, "genetic_counseling": 0.05},
    },
}

def detect_query_intents(query: str) -> list[str]: ...

def adjusted_score_for(
    *,
    rrf_score: float,
    role: str,
    section: str,
    query_intents: list[str],
) -> float:
    role_mul = ROLE_MULTIPLIER.get(role, 1.0)
    section_boost = sum(
        QUERY_INTENT_BOOSTS[i]["section_boost"].get(section, 0.0)  # type: ignore[index]
        for i in query_intents
        if i in QUERY_INTENT_BOOSTS
    )
    return rrf_score * role_mul * (1.0 + section_boost)
```

**Calibration constants are starting points, not load-bearing.** Spec acceptance gates them via the regression suite: if a constant choice breaks an unaffected-query baseline, tune it.

**Sort key:**

```
key = (-adjusted_score, -rrf_score, -dense_score, section_priority, nbk_id, passage_id)
```

`-rrf_score` as secondary preserves stable inter-row ordering for rows that tie on `adjusted_score` (e.g. when role multiplier is 1.0 and no intent matches the section, `adjusted_score == rrf_score` and the original ordering is preserved).

### Diagnostics surfacing

`SearchDiagnosticsModel` (extended atop Batch A+E):

```python
query_intents: list[str] = []   # NEW — server-inferred intents, e.g. ["management"]
```

`ScoreBreakdown` (extended; still opt-in via `include=score_breakdown`):

```python
adjusted_score: float | None = None
role_multiplier: float = 1.0
intent_section_boost: float = 0.0
passage_role: str = "evidence"
```

`RankedPassage` (top-level additive, rev 2):

```python
passage_role: Literal["evidence","cross_reference","definition","table_caption","table_body"] | None = None
```

Always populated when the row carries a role (i.e. after reingest). Explainability lives at search time, not just on single-passage fetches.

`PassageDetail` (single-passage fetch):

```python
passage_role: Literal[...] | None = None
```

### Role audit at ingest

`ChapterIngestAudit` (extended):

```python
role_counts: dict[str, int] = field(default_factory=dict)
```

Incremented in `_walk_section` after each passage emission. `_log_audit` in `parallel.py` issues a WARN when `role_counts.get("cross_reference", 0) / max(passage_count, 1) > 0.25` for a chapter. The 25% threshold is a "heuristic overfires" canary — if it trips on real corpus chapters during reingest, the classifier is tightened before proceeding.

## Data flow

Ingest:

```
NXML parse → existing chunker emits passage text + heading_path + passage_type + extracted table
                                ↓
                  classify_passage_role(text, heading_path, passage_type, char_count, caption_text)
                                ↓
                  PassageRecord(passage_role=...) + audit.role_counts increment
                                ↓
                  copy_passages writes passage_role column
```

Query:

```
search_passages(q=...) → detect_query_intents(q) → list[str]
                       → repo.search_passages(...)   [now also returns passage_role per row]
                       → rerank_with_embeddings(rows, dense_scores, query_intents=intents)
                            → computes rrf_score (existing)
                            → computes adjusted_score (new)
                            → sorts by adjusted_score
                       → response carries rrf_score (Batch A+E), adjusted_score (opt-in via score_breakdown),
                         passage_role (top-level), _meta.diagnostics.query_intents
```

## Error handling

No new error paths. Unknown intents produce empty `query_intents`. A missing `passage_role` on a row (shouldn't happen after reingest) defaults to `"evidence"` in `adjusted_score_for`.

## Testing

**Unit — classifier:**

- `tests/unit/test_corpus_role_classifier.py` with a ≥40-row hand-labeled fixture (`tests/fixtures/labeled_passages.jsonl`).
- Coverage: 5 roles × ≥6 representatives each, plus ≥10 borderline cases.
- Note: `structural` is no longer in the taxonomy; tests assert no `evidence` passage is mistakenly classified as `cross_reference` more than 5% of the time.

**Unit — intent detection:**

- `tests/unit/test_retrieval_query_intents.py` — table-driven; multi-intent stacking; empty case.

**Unit — `adjusted_score`:**

- `tests/unit/test_retrieval_adjusted_score.py` — assert `cross_reference` rows are demoted below otherwise-identical `evidence` rows; `management` section rows are promoted under `["management"]` intent; with empty intents and all-evidence rows, ordering matches the pre-Batch-B baseline.

**Regression suite (the load-bearing gate):**

- `tests/smoke/phase_12_ranking_regression.sh` — N ≥ 10 queries with expected top-1 `passage_id`. Curate against the running corpus by inspecting Batch A+E's `rrf_score` + `dense_rank_position` per candidate first (see Task 1 in the plan).
- Must include: reviewer's failing query (`BRCA1 risk-reducing mastectomy salpingo-oophorectomy` → `NBK1247:0024`), three additional management-intent queries, two diagnosis-intent queries, three unaffected queries (no intent match → top-1 must match Batch A+E baseline).

**Conservation invariants:**

- Per-chapter role distribution logged in `ChapterIngestAudit`. WARN if `cross_reference` ratio > 25%.
- Zero `ingest content-loss` regressions vs the post-`97a67a1` baseline.

## Migration / rollout

1. SQL data-migration: `genereview_link/db/migrations/data/0005_passage_role.sql` (rev 2 — unqualified table names; `db/migrate.py:113` sets `search_path` so the migration applies to whichever schema is being ingested into):

```sql
-- 0005_passage_role.sql — add role classification column with index.
-- Run inside the data-migration runner; search_path is set by the runner.
alter table genereview_passages
  add column if not exists passage_role text not null default 'evidence';
create index if not exists idx_passages_role on genereview_passages (passage_role);
```

Reversibility: drop index, drop column. Idempotent (`if not exists`).

2. Bump `PARSER_VERSION` in `corpus/nxml.py` to `2026-05-12-r2`; bump `corpus_version` in `config.py` to `2026-05-12-r5`.

3. Reingest (~10 min, full corpus). Verify per-chapter role distribution against expected ranges.

4. Calibrate `adjusted_score` constants if regression queries 1–7 don't all pass on first run. **Tuning is bounded to the constants in `QUERY_INTENT_BOOSTS` and `ROLE_MULTIPLIER`** — no SQL or classifier changes during the calibration loop.

5. Run regression smoke; require zero new failures vs baseline on unaffected queries.

Rollback: revert the `rerank.py` change to restore the prior sort key. `passage_role` column stays — harmless data without the new code.

## Risk register

| Risk | Mitigation |
|---|---|
| Cross-reference heuristic over-fires, demoting legitimate short evidence passages | Hand-labeled regression set; `cross_reference` ratio audit WARN at 25%; conservative phrase + heading gating |
| `ROLE_MULTIPLIER[cross_reference]=0.4` is too aggressive (legit short evidence loses to longer cross-refs in adjacent sections) | Calibration loop bounded to constants; regression suite catches inversions |
| Intent boost flips an already-correct top-1 to a wrong section | Regression includes "unaffected" queries that must not regress vs Batch A+E baseline |
| Reingest doubles ingest time | Classifier is `O(chars)` per passage with one regex; expected <5% ingest-time delta |
| Schema migration on populated table | `ADD COLUMN ... NOT NULL DEFAULT 'evidence'` is metadata-only on PG 11+ (instant) |
| Schema migration applied to wrong schema during staging ingest | Migration uses unqualified table names; runner sets `search_path` per `db/migrate.py:113` — same pattern as existing migrations |
| `caption_text` not threaded through to classifier | Plan task explicitly extends `PassageRecord` / `ExtractedTable` plumbing |

## Success criteria

- Regression smoke: all ≥10 queries PASS, including the reviewer's failing query → `NBK1247:0024`.
- Per-chapter role distribution: `evidence` 70–90%, `cross_reference` 2–15%, others smaller. No chapter trips the 25% WARN.
- Classifier matches ≥95% of hand-labeled fixture.
- Unaffected queries (no intent match) ranked identically to Batch A+E baseline.
- `_meta.diagnostics.query_intents` and top-level `passage_role` populated on every search response.

## Out of scope (explicit)

- `next_passage_id`, capability tool, section anchors — Batch C.
- Section TOC in metadata — Batch D.
- User-facing `section_bias=` param — Batch B.1 follow-up if regression suite shows server-inferred intent isn't enough.
- ML-based classification — only if heuristic accuracy on hand-label set is <90%.
