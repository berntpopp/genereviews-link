# Batch B — Ranking Quality (passage_role + intent-aware scoring) — Implementation Plan

> Historical record

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Date:** 2026-05-12 (rev 2 — addresses peer review)

**Goal:** Fix the persistent top-1 ranking failure for clinical-management queries by classifying each passage with `passage_role` at ingest and applying an intent-aware multiplicative-plus-additive `adjusted_score` at rerank. Reviewer's failing query `BRCA1 risk-reducing mastectomy salpingo-oophorectomy` must return `NBK1247:0024` (Management > Prevention) as top-1.

**Architectural shift vs rev 1:** role and intent affect the **score** (primary sort key), not the tail tuple — the prior tiebreaker-only approach cannot win against a higher `rrf_score`. See spec §"Critical architecture correction (rev 2)."

**Architecture:** Single phase (Phase 12 — Ranking-Quality-v1), **13 tasks**, includes ingest-time classifier, unqualified-schema SQL data-migration, full corpus reingest (~10 min), regression smoke. Tag: `phase-12-ranking-quality-v1`.

**Tech Stack:** Python 3.12, FastAPI + FastMCP, asyncpg, PostgreSQL + pgvector, BGE-small-en-v1.5 embeddings, pytest + ruff + mypy strict.

**Spec:** `docs/superpowers/specs/2026-05-12-batch-B-ranking-quality-design.md`

**Branch:** `feat/ranking-quality-v1` cut from `main` after Batch A+E (`phase-11-score-visibility-v1`) merges. Escalate BLOCKED if Batch A+E has not merged — Batch B's regression curation depends on inspecting Batch A+E's `rrf_score` and `dense_rank_position` per row.

---

## File Map

**Modified:**

- `genereview_link/corpus/nxml.py` — bump `PARSER_VERSION` to `2026-05-12-r2`; pass `caption_text` from extracted tables; call `classify_passage_role`; increment `audit.role_counts`.
- `genereview_link/corpus/records.py` — add `passage_role: str` to `PassageRecord`.
- `genereview_link/corpus/parallel.py` — extend `_log_audit` with role-distribution WARN; extend `copy_passages` columns + tuple.
- `genereview_link/corpus/tables.py` — ensure `ExtractedTable.caption` is exposed to the parser site that constructs the table-typed `PassageRecord` (it already is — verify and surface to the classifier call).
- `genereview_link/retrieval/repository.py` — project `passage_role` in passage selection; `LexicalPassageRow` gains the field.
- `genereview_link/retrieval/rerank.py` — add `apply_score_adjustments`; extend sort key; expose `adjusted_score`, `role_multiplier`, `intent_section_boost` on the returned row.
- `genereview_link/api/routes/passages.py` — call `detect_query_intents(q)`; pass into rerank; surface `query_intents` into the always-on diagnostics (from Batch A+E); add top-level `passage_role` to `RankedPassage` response rows.
- `genereview_link/models/genereview_models.py` — extend `SearchDiagnosticsModel` with `query_intents`; extend `ScoreBreakdown` with `adjusted_score`, `role_multiplier`, `intent_section_boost`, `passage_role`; add top-level `passage_role` to `RankedPassage`; add `passage_role` to `PassageDetail`.
- `genereview_link/config.py` — bump `corpus_version` to `2026-05-12-r5`.
- `genereview_link/corpus/nxml.py` (audit dataclass) — extend `ChapterIngestAudit` with `role_counts: dict[str, int]` and `as_log_extra` exposure.

**New:**

- `genereview_link/corpus/passage_role.py` (NEW) — `PassageRole` Literal, `classify_passage_role`, trigger constants.
- `genereview_link/db/migrations/data/0005_passage_role.sql` (NEW) — unqualified `ALTER TABLE` + `CREATE INDEX`, idempotent.
- `tests/unit/test_corpus_role_classifier.py` (NEW) — hand-label-driven.
- `tests/unit/test_retrieval_query_intents.py` (NEW) — intent detection.
- `tests/unit/test_retrieval_adjusted_score.py` (NEW) — `adjusted_score` rerank behavior.
- `tests/fixtures/labeled_passages.jsonl` (NEW) — ≥40 hand-labeled rows.
- `tests/fixtures/ranking_baseline.json` (NEW) — Batch A+E baseline top-1 per regression query.
- `tests/smoke/phase_12_ranking_regression.sh` (NEW) — 10+ queries with expected top-1.

---

# Phase 12 — Ranking-Quality-v1

**Execution order:** 1 (regression query curation against Batch A+E baseline) → 2 (hand-label fixture) → 3 (classifier) → 4 (intent detection) → 5 (LexicalPassageRow + models) → 6 (`adjusted_score` + rerank sort) → 7 (audit + role_counts) → 8 (SQL migration) → 9 (ingest plumbing) → 10 (reingest) → 11 (wire into search route + diagnostics) → 12 (usage resource) → 13 (regression smoke + gate).

---

### Task 1: Curate regression queries + capture Batch A+E baseline

**Files:**

- New: `tests/fixtures/ranking_baseline.json`.

**Why:** Two halves of the regression set need different curation. For the "must change" queries we need to identify the expected target passage. For the "must not regress" queries we need to lock the current top-1 before Batch B changes anything. Both depend on inspecting the deployed Batch A+E server's full per-row score visibility.

- [ ] Step 1: Against the running Batch A+E server (post-merge), run each candidate regression query with `mode=full&include=score_breakdown`. Record `rrf_score`, `lexical_score`, `lexical_rank_position`, `dense_rank_position`, `chapter_section`, `heading_path`, and `passage_id` for the top 10 results.

- [ ] Step 2: For each "must change" query, identify the topically-correct target passage by content inspection (not by current rank). Write down the expected top-1 `passage_id`.

- [ ] Step 3: For each "unaffected" query, lock the current top-1 `passage_id`.

- [ ] Step 4: Curate ≥10 queries:

```text
# Management-intent (must change to expected top-1):
1.  "BRCA1 risk-reducing mastectomy salpingo-oophorectomy" → expect NBK1247:0024
2.  "hereditary hemochromatosis phlebotomy treatment"      → expect NBK1440:<curated>
3.  "Lynch syndrome surveillance colonoscopy"              → expect NBK1211:<curated>
4.  "BRCA2 prophylactic surgery"                           → expect NBK1101:<curated>
5.  "tetra-amelia management"                              → expect NBK1488:<curated>

# Diagnosis-intent (must change to evidence-section top-1):
6.  "FAP diagnostic criteria"                              → expect NBK1345:<curated>
7.  "MCAD deficiency diagnosis confirmation"               → expect <curated>

# Unaffected (must NOT regress vs baseline):
8.  "HFE C282Y allele frequency"                           → baseline locked
9.  "tetra-amelia gene WNT3"                               → baseline locked
10. "BRCA1 founder mutation Ashkenazi"                     → baseline locked
```

- [ ] Step 5: Write the locked baselines to `tests/fixtures/ranking_baseline.json`. Each entry has `query`, `expected_top1_passage_id`, `notes` (free text for curator reasoning).

**Acceptance:** ≥10 curated queries; each has a justified `expected_top1_passage_id`. The 3 unaffected queries' baselines reflect the live Batch A+E server.

---

### Task 2: Build hand-labeled fixture set

**Files:**

- New: `tests/fixtures/labeled_passages.jsonl` (≥40 rows covering 5 roles).

**Why:** Heuristic classifier needs ground truth to measure accuracy and tune.

- [ ] Step 1: Query the running server for passages across the 5-role taxonomy:
  - 10 management-section narratives from NBK1247/NBK1488/NBK1440 → `evidence`.
  - 5 "Related X Issues" / "Family Member" pointer passages → `cross_reference`.
  - 5 table-caption-only passages (`passage_type=='table'` with text close to caption length) → `table_caption`.
  - 5 substantive table-body passages → `table_body`.
  - 5 short definitional passages (`char_count<=120`, colon form) → `definition`.
  - 10 borderline cases (short evidence, long cross-refs, mixed content) labeled by the curator.

- [ ] Step 2: Each row: `{"passage_id", "text", "heading_path", "passage_type", "char_count", "caption_text", "expected_role", "notes"}`. `caption_text` is the `caption` field from `ExtractedTable` for table-typed passages; empty string for narrative passages.

- [ ] Step 3: Commit the fixture file. Include `notes` for borderlines explaining the label choice.

**Acceptance:** ≥40 rows covering all 5 roles; ≥10 borderlines with explanations.

---

### Task 3: Implement role classifier

**Files:**

- New: `genereview_link/corpus/passage_role.py`.
- New: `tests/unit/test_corpus_role_classifier.py`.

**Why:** Deterministic, auditable, no ML.

- [ ] Step 1: Implement the module per spec — `PassageRole` Literal, trigger constants, `classify_passage_role(*, text, heading_path, passage_type, char_count, caption_text="")`. Use word-bounded matching for `"see "` to avoid false positives on words like "seem", "seek".

- [ ] Step 2: Write the table-driven test that loads `labeled_passages.jsonl`. Assert classifier output matches `expected_role` for ≥95% of rows. Pytest output reports each mismatch with `passage_id`, predicted, expected, and the fixture's `notes`.

- [ ] Step 3: If <95%, tune heuristics conservatively (loosen rule 2's heading-suffix list before loosening its phrase-trigger list; never widen rule 3). Do not chase 100% — keep cross-reference false-positive rate <5%.

**Acceptance:** Classifier ≥95% on fixture; cross_reference FP rate <5%.

---

### Task 4: Implement query-intent detection

**Files:**

- Modify: `genereview_link/retrieval/rerank.py` — add `QUERY_INTENT_BOOSTS` and `detect_query_intents`.
- New: `tests/unit/test_retrieval_query_intents.py`.

- [ ] Step 1: Add the `QUERY_INTENT_BOOSTS` table per spec (management/diagnosis/genetics).

- [ ] Step 2: Implement `detect_query_intents(query: str) -> list[str]` — case-insensitive substring scan; returns sorted intent names; multi-intent stacking; empty list when nothing matches.

- [ ] Step 3: Table-driven tests cover: each single intent, multi-intent stacking, the "foo bar baz" empty case.

**Acceptance:** All test cases green.

---

### Task 5: Extend models with role/intent fields

**Files:**

- Modify: `genereview_link/models/genereview_models.py`.
- Modify: `genereview_link/retrieval/repository.py` — `LexicalPassageRow` gains `passage_role: str = "evidence"`, `adjusted_score: float | None = None`, `role_multiplier: float = 1.0`, `intent_section_boost: float = 0.0`. Project `passage_role` in the SELECT at `repository.py:220-256`.

- [ ] Step 1: Public-model changes:
  - `RankedPassage` gains top-level `passage_role: Literal[...] | None = None` (always set after reingest; None only until reingest is complete).
  - `PassageDetail` gains `passage_role: Literal[...] | None = None`.
  - `ScoreBreakdown` gains `adjusted_score`, `role_multiplier`, `intent_section_boost`, `passage_role`.
  - `SearchDiagnosticsModel` gains `query_intents: list[str] = []`.

- [ ] Step 2: SELECT in `repository.search_passages` includes `p.passage_role`; SELECT in `repository.get_passage` does too. Both populate the row's `passage_role`.

- [ ] Step 3: Tests for model serialization — `tests/test_routes_passages.py` extended to assert `passage_role` is present (may be `None` until reingest in CI).

**Acceptance:** Schemas + repo selection updated; tests pass.

---

### Task 6: `adjusted_score` and new sort key in rerank

**Files:**

- Modify: `genereview_link/retrieval/rerank.py`.
- New: `tests/unit/test_retrieval_adjusted_score.py`.

**Why:** This is the architectural correction — sort by `-adjusted_score` primary, `-rrf_score` secondary.

- [ ] Step 1: Add `ROLE_MULTIPLIER` mapping per spec.

- [ ] Step 2: Add helpers:

```python
def _section_boost(section: str, query_intents: list[str]) -> float:
    return sum(
        QUERY_INTENT_BOOSTS[i]["section_boost"].get(section, 0.0)   # type: ignore[index,union-attr]
        for i in query_intents if i in QUERY_INTENT_BOOSTS
    )

def adjusted_score_for(*, rrf_score: float, role: str, section: str, query_intents: list[str]) -> tuple[float, float, float]:
    role_mul = ROLE_MULTIPLIER.get(role, 1.0)
    sec_boost = _section_boost(section, query_intents)
    return rrf_score * role_mul * (1.0 + sec_boost), role_mul, sec_boost
```

- [ ] Step 3: Extend `rerank_with_embeddings` signature with `query_intents: list[str] = ()`. After computing `rrf_score` per row, compute the adjusted-score triple and `dataclasses.replace(r, adjusted_score=..., role_multiplier=..., intent_section_boost=...)` onto the row.

- [ ] Step 4: Change the final sort key to:

```python
key = lambda r: (
    -(r.adjusted_score if r.adjusted_score is not None else (r.rrf_score or 0.0)),
    -(r.rrf_score or 0.0),
    -dense_scores.get(r.passage.passage_id, 0.0),
    _section_key(r),
    r.passage.nbk_id,
    r.passage.passage_id,
)
```

The `adjusted_score is None` fallback covers the edge case where a row reaches sort with no rerank context (defensive — should not happen in the rrf path).

- [ ] Step 5: Tests:
  - Two rows, identical `rrf_score`, one `cross_reference` + one `evidence`, same section → `evidence` ranks first (role multiplier).
  - Two rows, identical `rrf_score`, both `evidence`, sections `management` and `genetic_counseling`, with `query_intents=["management"]` → management ranks first (intent boost).
  - Two rows where `cross_reference` has a slightly higher `rrf_score` (e.g. 0.025 vs 0.022) but evidence ratio still wins after multiplier — verify the architectural correction works against the realistic case.
  - With empty intents and all-evidence rows: ordering is **identical** to today's `(-rrf_score, -dense, …)` order on a synthetic input.

**Acceptance:** All five unit cases green.

---

### Task 7: Extend `ChapterIngestAudit` with role counts + WARN

**Files:**

- Modify: `genereview_link/corpus/nxml.py` — `ChapterIngestAudit` dataclass.
- Modify: `genereview_link/corpus/parallel.py` — `_log_audit` checks role distribution.

**Why:** Operator observability for classifier over-fire detection.

- [ ] Step 1: Add to `ChapterIngestAudit`:

```python
role_counts: dict[str, int] = field(default_factory=dict)

@property
def cross_reference_ratio(self) -> float:
    total = sum(self.role_counts.values()) or 1
    return self.role_counts.get("cross_reference", 0) / total
```

Include `role_counts` and `cross_reference_ratio` in `as_log_extra()`.

- [ ] Step 2: In `_walk_section` (or wherever passages are emitted), after `PassageRecord` construction, increment `audit.role_counts[record.passage_role] = audit.role_counts.get(record.passage_role, 0) + 1`.

- [ ] Step 3: Extend `_log_audit` in `parallel.py:76` with a new branch — WARN when `audit.cross_reference_ratio > 0.25`:

```python
if audit.cross_reference_ratio > 0.25:
    logger.warning(
        "ingest role-distribution nbk=%s cross_reference_ratio=%.3f role_counts=%s",
        audit.nbk_id, audit.cross_reference_ratio, audit.role_counts,
        extra=extra,
    )
```

**Acceptance:** Role counts appear in audit extra; WARN fires on synthetic test of an over-classified chapter.

---

### Task 8: SQL data-migration

**Files:**

- New: `genereview_link/db/migrations/data/0005_passage_role.sql`.

**Why:** Unqualified table names. `db/migrate.py:113` sets `search_path` per migration, so the migration applies to whichever schema is being ingested into (active or staging). Hard-coding `genereview.genereview_passages` would write to the wrong schema during staging ingest.

- [ ] Step 1: Create the file with idempotent SQL (unqualified — runner sets search_path):

```sql
-- 0005_passage_role.sql
-- Adds the passage_role classification column (and index) used by Batch B rerank.
-- search_path is set by the data-migration runner; do not qualify table names.

alter table genereview_passages
  add column if not exists passage_role text not null default 'evidence';

create index if not exists idx_passages_role
  on genereview_passages (passage_role);
```

- [ ] Step 2: Verify the file is picked up by `db/migrate.py`'s `_list_sql(data_pkg)` and the `apply_data_migrations` flow.

- [ ] Step 3: Local apply against gr-pg dev DB. Time the ALTER (expected near-instant — metadata-only on PG 11+).

- [ ] Step 4: Test reversibility manually: `drop index idx_passages_role; alter table genereview_passages drop column passage_role;`. Document in a comment in the SQL file that there's no down-migration in the runner — rollback is manual.

**Acceptance:** Migration applies cleanly to both active and (when present) staging schemas; column present; index present; existing rows default to `evidence`.

---

### Task 9: Ingest plumbing — emit `passage_role` (with `caption_text`)

**Files:**

- Modify: `genereview_link/corpus/records.py` — add `passage_role: str` (no default — must be explicit at every construction site).
- Modify: `genereview_link/corpus/nxml.py` — at every `PassageRecord(...)` construction, compute and pass `passage_role`.
- Modify: `genereview_link/corpus/parallel.py` — extend the records tuple and columns list in `copy_passages`.

**Why:** Classifier needs `caption_text` for table-typed passages. The narrative-passage construction site already has `text`, `heading_path`, `passage_type`, `char_count`; thread `caption_text=""`. The table-passage construction site (at `nxml.py:432` per the no-loss commit) needs `caption_text=extracted.caption`.

- [ ] Step 1: Add `passage_role: str` field to `PassageRecord` (no default).

- [ ] Step 2: In `nxml.py`, locate every site that constructs a `PassageRecord`:
  - Narrative chunk flush in `_flush_paragraphs` → compute `role = classify_passage_role(text=text, heading_path=heading_path, passage_type="narrative", char_count=len(text), caption_text="")`. Pass to `PassageRecord`.
  - Table emission (search for `passage_type="table"` near line 432) → compute `role = classify_passage_role(text=text, heading_path=heading_path, passage_type="table", char_count=len(text), caption_text=extracted.caption)`. Pass to `PassageRecord`.

- [ ] Step 3: After construction, increment `audit.role_counts[role]` (Task 7's audit hook).

- [ ] Step 4: Bump `PARSER_VERSION = "2026-05-12-r2"`.

- [ ] Step 5: In `parallel.py:copy_passages`, add `"passage_role"` to the columns tuple and `p.passage_role` to the row record tuple.

- [ ] Step 6: Run `make test-unit`. Expect parse-tests to break on the new required field; update fixtures to set `passage_role` explicitly.

**Acceptance:** All unit tests green; classifier called at both emission sites; `caption_text` threaded.

---

### Task 10: Reingest

**Files:** ops step.

- [ ] Step 1: Bump `corpus_version = "2026-05-12-r5"` in `genereview_link/config.py`.

- [ ] Step 2: Run the ingest CLI (`uv run genereview-link ingest-corpus ...` — verify subcommand with `--help`).

- [ ] Step 3: Monitor logs for `ingest content-loss` WARNs (must be zero — would indicate regression of `97a67a1`) and `ingest role-distribution` WARNs (must be empty or tightly limited).

- [ ] Step 4: SQL audit:

```sql
select passage_role, count(*)
  from genereview_passages
 where corpus_version='2026-05-12-r5'
 group by passage_role
 order by count(*) desc;
```

Expected: `evidence` 70–90%, `cross_reference` 2–15%, `table_body` 5–15%, `table_caption` 1–5%, `definition` 1–10%.

- [ ] Step 5: Per-chapter check on the three reviewer-frequented chapters:

```sql
select nbk_id, passage_role, count(*)
  from genereview_passages
 where corpus_version='2026-05-12-r5'
   and nbk_id in ('NBK1247','NBK1488','NBK1440')
 group by nbk_id, passage_role
 order by nbk_id, count(*) desc;
```

If `cross_reference` ratio exceeds 25% for any chapter, escalate BLOCKED, tune classifier, re-run.

**Acceptance:** Reingest complete; role distribution within expected ranges; no content-loss regressions.

---

### Task 11: Wire intent + role into search route + diagnostics

**Files:**

- Modify: `genereview_link/api/routes/passages.py`.

- [ ] Step 1: Near the top of `search_passages`, call `query_intents = detect_query_intents(q)`. Pass as kwarg to `rerank_with_embeddings(..., query_intents=query_intents)`.

- [ ] Step 2: Extend the always-on diagnostics block (built in Batch A+E Task 3) with `query_intents=query_intents`.

- [ ] Step 3: At row construction in `passages.py:321-345`, populate top-level `passage_role=r.passage.passage_role`. When `include=score_breakdown` is set, also populate `adjusted_score`, `role_multiplier`, `intent_section_boost`, `passage_role` on the `ScoreBreakdown` sub-object from the row.

- [ ] Step 4: Verify the `mode=ids_only` lean row also exposes `passage_role` (consumers ranking-explainability work in lean mode too).

- [ ] Step 5: Update `tests/test_routes_passages.py` for `passage_role` presence, `_meta.diagnostics.query_intents` on a management-intent query, `_meta.diagnostics.query_intents == []` on a neutral query.

**Acceptance:** Search response surfaces both fields end-to-end.

---

### Task 12: Update usage resource

**Files:**

- Modify: `genereview_link/api/resources/usage.py`.

- [ ] Step 1: Add a "Passage roles" subsection. Document the 5 values, the role multipliers, and the note that `adjusted_score` is the field that sorts (visible inside `score_breakdown` when opted in).

- [ ] Step 2: Add a "Query-intent boosts" subsection. List the 3 intents, their trigger patterns, and section boosts. Note: server-inferred, not user-tunable, surfaced in `_meta.diagnostics.query_intents`.

- [ ] Step 3: Update the search-results paragraph to mention `passage_role` at top-level and where to find the role-affected `adjusted_score`.

**Acceptance:** Markdown reads cleanly; substring assertions in `test_mcp_usage_resource.py` updated.

---

### Task 13: Regression smoke + phase gate

**Files:**

- New: `tests/smoke/phase_12_ranking_regression.sh`.

- [ ] Step 1: Smoke script reads `tests/fixtures/ranking_baseline.json` and the curated expected top-1 per query from Task 1's deliverable. For each query, curl the live server (`mode=full&limit=1`), `jq -r '.results[0].passage_id'`, compare against expected. Report PASS/FAIL per query and exit non-zero on any failure.

- [ ] Step 2: Calibration loop:
  - Run smoke. If any "must change" query fails, inspect that query's top-3 with `include=score_breakdown`. Decide:
    - If a more aggressive `ROLE_MULTIPLIER[cross_reference]` (e.g. 0.3, then 0.25) fixes it without breaking unaffected queries → tune.
    - If section boost needs to grow (e.g. 0.30 → 0.40 for management) → tune.
    - If neither works, escalate BLOCKED — the architectural assumption (intent + role suffices) may not hold for this query; consider Batch B.1.
  - Re-run smoke after each tweak.

- [ ] Step 3: Run Batch A+E phase-11 smoke against the live Batch B build — must still pass (no regression in score-visibility behavior).

- [ ] Step 4: `make ci-local` clean.

- [ ] Step 5: Docker rebuild + smoke against the rebuilt image (not just restart — same lesson as `97a67a1`).

- [ ] Step 6: Tag `phase-12-ranking-quality-v1` on the merge commit.

**Acceptance:** All ≥10 regression checks PASS; Batch A+E smoke still PASS; CI green; tag pushed.

---

## Done criteria for Batch B

- All 13 tasks complete with green tests.
- Reviewer's failing query → `NBK1247:0024` top-1.
- ≥10/10 regression queries PASS (must-change and must-not-regress both).
- Per-chapter role distribution within expected ranges; classifier ≥95% accurate on hand-label set; cross_reference ratio <25% on every chapter.
- `_meta.diagnostics.query_intents` populated on every response.
- Top-level `passage_role` populated on every `RankedPassage` (and `PassageDetail`).
- `adjusted_score`, `role_multiplier`, `intent_section_boost`, `passage_role` visible inside `score_breakdown` when opted in.
- Tag `phase-12-ranking-quality-v1` pushed.
