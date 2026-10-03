# GeneReviews Retrieval Entity and Evaluation Plan

> Historical record — This plan captures the approved design and evidence for the 2026-10-03 retrieval repair.

**Goal:** Correct measurable retrieval issues without changing the frozen corpus acceptance suite or weakening its floors, and report ranking quality through the production hybrid path.

**Architecture:** Preserve exact replay for historical manifests with no evaluation algorithm. New manifests declare `primary-gene-aware-lexical-v2`; its lexical candidate ranking is shared with the API's lexical mode. Production `/passages/search?rerank=rrf` remains separately measured with real BGE query embeddings on disposable prior/candidate databases. Add conservative ranking context for one exact, unambiguous gene symbol, preserve explicit `gene=` filtering, and retain cross-chapter results. Correct only independently demonstrated RRF defects.

**Tech Stack:** Python 3.12, FastAPI, asyncpg/PostgreSQL 18 with pgvector 0.8.2 or 0.8.6 runtime, ONNX Runtime and the existing digest-pinned BGE model.

**Spec:** In-chat design approved by the parent on 2026-10-03; evidence and constraints are in the parent task messages and audit-b findings.

## Global Constraints

- Preserve `tests/eval/genereviews_queries.jsonl`, its reviewed SHA-256, and the existing `MIN_MRR_AT_10` / `MIN_SECTION_PRECISION_AT_5` floors.
- Do not hard-filter a query to an inferred gene; only the explicit `gene=` parameter filters.
- Do not add model/dependency/network requirements or alter corpus schema or immutable release records. Record current runtime identity separately from historical computation provenance.
- Use only the disposable prior/candidate PostgreSQL databases; all SQL evidence queries are read-only.
- Execute `make ci-local` and the prior/candidate corpus evaluations against the exact final committed tree.
- Keep independently rejectable fixes in separate commits.

## Review Focus

1. Lowercase or common English tokens matching symbols such as `MET`, `KIT`, or `CAT` must not silently invoke gene relevance; test recognized, ambiguous, and unrecognized symbol input.
2. Explicit `gene=` filters must remain hard filters and must override inferred-symbol ranking context.
3. A query mentioning two genes or a cross-gene comparison must retain both chapters and must not select one inferred primary gene.
4. Dense-only candidates must not receive a lexical RRF vote unless measurement disproves that they are actually lexical candidates.
5. HNSW candidate ranks must be repeatable and ordered by exact distance; only change the SQL plan if real database evidence shows the current query violates that behavior.

## File Structure

- `genereview_link/retrieval/` owns query entity recognition, candidate retrieval, and fusion.
- `genereview_link/api/routes/passages.py` remains the HTTP response and filter boundary.
- `genereview_link/corpus/evaluation.py` replays the declared lexical algorithm; absent identifiers select exact legacy semantics.
- Production hybrid diagnostics use the actual HTTP route, exact ONNX model and disposable database. Their selected-query metrics are descriptive, not a general benchmark.
- `tests/unit/` and `tests/integration/` cover entity recognition, RRF membership, and SQL/runtime behavior.

## Tasks

### Task 1: Capture the failing entity-aware ranking behavior

- Add tests for exact recognized single-symbol extraction, ambiguous/common words, multiple symbols, lowercase ambiguity, and a conflicting explicit filter.
- Add a candidate-level ranking test where BRCA1 is primary for NBK1247 and absent from primary genes for the Lynch chapter; verify ranking context boosts without eliminating the Lynch result.
- Run each focused test and confirm it fails before implementation.

### Task 2: Implement conservative entity-aware ranking

- Reuse indexed `primary_gene_symbols` metadata; do not infer from arbitrary passage-body mentions.
- Infer only one exact canonical token under conservative casing/ambiguity rules; return no inferred symbol for ambiguous or multi-symbol queries.
- Apply the existing modest primary-gene boost in lexical ranking and production RRF; explicit `gene=` remains the only filter.
- Run focused tests and the frozen lexical evaluation against both prior and candidate databases.

### Task 3: Correct RRF membership only if the failing test and production comparison confirm it

- Add a failing test proving a dense-only row currently gains a lexical rank contribution solely because it is in the union.
- Make lexical and dense rank maps reflect membership in their respective source lists; preserve lexical positions as diagnostics.
- Run focused reranker tests and compare prior/candidate production rankings before/after.

### Task 4: Capture supplementary production-path evidence

- Share the query-gene candidate augmentation and lexical ranking core between the API and V2 evaluator; keep response serialization separate.
- Capture production HTTP route evidence on both the prior and October candidate, with selected queries spanning genes, disease names, treatment, genetics, and cross-gene comparisons.
- Report frozen lexical metrics separately from production-route target ranking and latency; state the limitations of the small selected query set.
- Record model revision, execution provider, database/corpus identity, evaluation algorithm, and raw result digests.

### Task 5: Verify dense-index ordering, cold restore, and final acceptance

- Record repeated route rankings and dense-score ordering on the pgvector 0.8.6 candidate runtime.
- Change SQL only if a real mismatch is reproduced; add a corresponding regression test and benchmark.
- Cold-restore both prior and candidate data-only dumps onto the pinned pgvector 0.8.6 server image; rebuild HNSW and run native bundle validation.
- Run the unchanged frozen lexical acceptance gate and production-route probe on the final clean commit, then run `make ci-local`.
