# Ranking Redesign Phase C-gamma — Entity-Aware Retrieval (Design Spec)

**Date:** 2026-05-13
> Historical record

**Author:** senior MCP engineer (Opus 4.7, in collaboration with project maintainer)
**Source review:** `docs/superpowers/reviews/2026-05-13-c-beta-bench-results.md`
**Predecessors:**
- C-alpha (parallel hybrid retrieval + role-aware RRF) — merged in PR #32.
- C-beta (cross-encoder bake-off) — measured negative result. Branch and tooling removed; results doc retained as evidence.

**Branch target:** new branch off `main`.

---

## Goal

Close the residual quality gaps that C-alpha's role-aware RRF and C-beta's cross-encoder bake-off both failed to close — without adding a model on the hot path.

The bench evidence (next section) shows the failures are **representation problems, not reranker problems**. C-gamma adds an entity layer between the corpus and the existing rerank, so that queries and passages can match on normalized biomedical IDs (gene NCBI Gene, variant dbSNP/HGVS, drug MeSH, disease MeSH/HPO) instead of on surface text alone. The entity layer is built offline on the RTX 5090 by running the open-source PubTator-3 annotator stack over the full corpus; queries are tagged on the VPS at search time by a small local gazetteer derived from the same normalization dictionaries. No outbound API at runtime.

Target outcome on the locked 299-entry `tests/fixtures/ranking_bench.jsonl`:
- **P@1 ≥ 0.50** (vs C-alpha 0.408)
- **Recall@5 ≥ 0.826** (no regression below C-alpha)
- **p95 search latency overhead ≤ 50 ms** vs C-alpha
- **All three marquee misses resolved at top-1 or top-5**: HFE C282Y allele frequency, CFTR F508del modulator therapy indication, GRIN2B-related neurodevelopmental disorder.

## Background — what C-beta measured

C-beta scored four cross-encoder candidates against the locked C-alpha bench. Headline numbers:

| Candidate | P@1 | MRR@5 | **Recall@5** | Composite | p95 latency | Qualified |
|---|---:|---:|---:|---:|---:|---|
| RRF baseline (C-alpha) | 0.408 | 0.560 | **0.826** | 0.598 | n/a | — |
| MedCPT | 0.585 | 0.633 | **0.696** | 0.638 | 5,627 ms | no |
| ms-marco MiniLM-L12 | 0.579 | 0.631 | **0.699** | 0.636 | 2,374 ms | no |
| mxbai-rerank-base | — | — | — | — | timeout >6h40m | no |
| bge-reranker-base | — | — | — | — | not run | no |

Two findings drive the C-gamma design:

1. **The "P@1 lift" is also a Recall@5 regression.** Both qualified CEs moved Recall@5 from 0.826 → ~0.70 — they shuffle gold passages out of the top-5 while pushing a single confident-looking passage to #1. Composite went up only because P@1 is weighted heavily. This is a quality trade we should refuse; the C-beta gate that judged it a "win" was the wrong gate.

2. **Latency is 3-8× over the 750 ms gate on CPU.** Even MiniLM-L12 (33M params) sits at 2.4 s p95 on the 6-core VPS. No production CE on this hardware without distillation + quantization, which is a phase of its own.

## Diagnosis — why the marquee misses fail

I pulled the gold and picked passages with score breakdowns. Each failure has a distinct root cause; none is "the CE wasn't strong enough":

- **HFE C282Y allele frequency** — gold `NBK1440:0051` (allele table). Dense ranks it #1; lexical ranks `NBK1440:0005` (diagnostic-testing table) #1 because the query says "C282Y" while the gold table body uses HGVS form "p.Cys282Tyr" / "c.845G>A". RRF tie-breaks to 0005. **Root cause: variant-nomenclature asymmetry.** A surface-text reranker amplifies this; MedCPT re-introduces the regression for exactly this reason.

- **CFTR F508del modulator therapy indication** — gold `NBK1250:0032` (Table 6, indications). RRF picks `NBK1250:0043` (Therapies Under Investigation narrative). Both are management; both get identical intent boosts; the narrative wins by 0.1 on lexical because its prose density is higher than the table body's terse cell text. **Root cause: tables under-rank vs adjacent narrative.**

- **GRIN2B-related neurodevelopmental disorder phenotype spectrum** — gold `NBK501979:0005`. RRF top-5 contains **zero** passages from chapter NBK501979; all top-5 are differential-diagnosis tables from KMT2B, Stickler, POLG, SLC6A3, Leigh. The gene symbol "GRIN2B" sits in the query but is treated as bag-of-words. **Root cause: gene symbol in query is not used as a structural anchor.** No CE rescoring the RRF top-50 can recover this — the right chapter isn't in the candidate set.

Confirmed against the genereview-link MCP entity lookup: `GRIN2B` resolves to NCBI Gene `24410`; `C282Y` and `p.Cys282Tyr` resolve to the **same** LitVar variant entity in PubTator's normalization (HLA-H is the legacy synonym for HFE). Entity-level matching collapses the variant-nomenclature asymmetry for free.

## Out of scope

- **Distilled cross-encoder on the hot path.** C-beta's `scripts/ranking_ce/` package is deleted. If after C-gamma the bench still shows a measurable gap, a follow-up phase decides whether to distill a student CE; that phase will design its own apparatus.
- **ColBERTv2 / late-interaction retrieval.** Strongest research alternative but a different architectural bet. Note in rejected alternatives only.
- **Fine-tuning BGE-small on GeneReviews silver.** Worth doing later in parallel with this work; not required for the gates above.
- **Bench fixture rewrite.** The 299-entry locked bench stays locked. C-gamma adds a *non-blocking* entity-driven audit that flags suspect silver labels for a future fixture refresh; it does not gate on the audit.
- **External LLM API at runtime.** Constraint, not a choice.

## Architecture overview

```
                                                                          
              OFFLINE  (RTX 5090, one-shot per corpus version)            
              ┌─────────────────────────────────────────────────┐         
 passages ─►  │ PubTator-3 stack (Zenodo bundle 10.5281/10839630):│ ─► passage_entities
 (full text)  │   AIONER (NER) + GNorm2 (gene) + tmVar3 (variant) │     (passage_id, type,
              │   + TaggerOne (disease/chemical/cellline)         │     normalized_id,
              └─────────────────────────────────────────────────┘     surface_form,
                                                                          offsets, source)
                                                                          
              ONLINE  (VPS, no API, ≤ 50 ms p95 overhead)                 
              ┌─────────────────────────────────────────────────┐         
 query ────►  │ aho-corasick gazetteer                          │ ─► query_entities
              │ (HGNC + dbSNP/HGVS + MeSH + HPO bulk dumps)     │     {gene:[…],
              └─────────────────────────────────────────────────┘      variant:[…],
                                                                       drug:[…],
                                                                       disease:[…]}
                                                                          
              RANK  (additive feature on existing rerank=rrf)            
                                                                          
 RRF top-50 ──► entity_overlap_boost ──► role/section_intent_boost ──►   
                (gene, variant, drug, disease + chapter_gene_match)       
                                                                          
                                                                          
              (No CE in the hot path.)                                    
```

## Components

### C2.1 — Offline annotation pipeline (RTX 5090)

- Pull the PubTator-3 source bundle (`zenodo.org/records/10839631`), or assemble from individual NCBI repos (`github.com/ncbi/AIONER`, `github.com/ncbi/GNorm2`, `github.com/ncbi/tmVar3`, `BIOIN-401-Project-8/TaggerOne-PubTator3`).
- Containerize: Python 3.9 env for AIONER + GNorm2 + tmVar3, JVM env for TaggerOne. One docker-compose, two services, shared volume.
- Input: every passage from the active corpus version (`corpus_version` in `_meta`).
- Output: BioC JSON per chapter → loader → `passage_entities` table (FK to passage, entity_type, normalized_id, surface_form, char_offset_start, char_offset_end, source). Indexed on `(normalized_id, entity_type)` for the search-time join.
- Rollup: `chapter_entities` materialized view for the chapter-level gene-match feature (avoids a per-query aggregation).
- Re-run only when corpus refreshes. Estimate: ~12-24h wallclock for full corpus on 5090.

### C2.2 — Online entity gazetteer (VPS)

- Aho-Corasick automaton built once at package install time from authoritative bulk dumps:
  - **HGNC** symbol + alias + previous-symbol list (5 MB, weekly updates).
  - **dbSNP** HGVS short forms via the ClinGen Allele Registry or the curated subset extracted from C2.1's `passage_entities` (whichever has better recall on the bench).
  - **MeSH** chemical + disease descriptor names (one-time download).
  - **HPO** phenotype labels + synonyms.
- Loads in ≤ 1 s at uvicorn worker startup. Matches in single-digit ms/query.
- Output: `{entity_type: [(normalized_id, surface_form), …]}`.
- Versioned artifact: gazetteer build is reproducible from pinned bulk-file URLs; version string lands in `_meta.diagnostics`.

### C2.3 — Entity-overlap score feature

Added to `score_breakdown` in `genereview_link/retrieval/rerank.py`:

```
entity_overlap_boost =
      w_gene     · |Q_gene     ∩ P_gene    |
    + w_variant  · |Q_variant  ∩ P_variant |
    + w_drug     · |Q_drug     ∩ P_drug    |
    + w_disease  · |Q_disease  ∩ P_disease |
    + w_chapter_gene · 1[ any(Q_gene)  ∈ chapter.gene_symbols  ]
```

Implementation:
- Top-50 from RRF → SQL join against `passage_entities` filtered by `passage_id IN (…)`. Cheap; the join touches ≤ 50 × ~30 entity rows.
- Weights tuned by grid sweep on the 299-entry bench. Default starting weights (from diagnosis): `w_gene=0.025, w_variant=0.030, w_drug=0.020, w_disease=0.015, w_chapter_gene=0.020`. The grid sweep is part of the execution plan, not part of this spec.
- Cap `w_chapter_gene` contribution at 1 per passage, so multi-gene chapters (e.g., NBK320989 Leigh syndrome listing 70+ genes) don't drown out within-chapter signal.

### C2.4 — Role/section intent boost (orthogonal, independent of entities)

- Extend the existing `QUERY_INTENT_BOOSTS` in `genereview_link/retrieval/rerank.py` with a property-intent dictionary: `{"indication", "frequency", "dose", "onset", "penetrance", "carrier"}`.
- When a property-intent token is present, add a small `passage_role` boost favoring `table_body` (currently neutral). Fixes the CFTR table-vs-narrative case independently of entities.
- Boost magnitude (initial): +0.05 multiplier on the role component when intent matches.

### C2.5 — Bench fixture audit (non-blocking)

- Once `passage_entities` exists, run a one-shot script that joins each silver entry's query against its gold-passage entities. Flag silvers where query and gold share **zero** entity IDs of any type.
- Output: `docs/superpowers/reviews/2026-05-13-c-gamma-fixture-audit.md` with the flagged entries.
- **Not a gate.** Findings feed a future fixture refresh; C-gamma still ships against the locked 299-entry bench.

## Data flow

**Index-time (offline):** corpus → PubTator-3 stack → BioC JSON → loader → `passage_entities` + `chapter_entities`. One migration adds the two tables. Sanity audit: random-sample chapter entity sets vs the PubTator abstract-level annotations fetched via `pubtator_fetch_publication_annotations(PMID)` — they should agree on chapter-level gene and disease IDs.

**Search-time (online, VPS):** query → gazetteer tagger → `query_entities` → existing RRF retrieval over top-50 → entity-overlap join against `passage_entities` → role/intent boost → final order. Target latency overhead: gazetteer ≤ 20 ms p95, entity-overlap join ≤ 30 ms p95.

## File structure

**Created:**
- `genereview_link/entities/__init__.py`
- `genereview_link/entities/gazetteer.py` — aho-corasick runtime tagger
- `genereview_link/entities/normalizers.py` — surface form ↔ normalized ID helpers
- `genereview_link/entities/sources/` — pinned-URL fetchers for HGNC, dbSNP/HGVS, MeSH, HPO (offline build only)
- `genereview_link/retrieval/entity_overlap.py` — score-feature implementation
- `scripts/build_gazetteer.py` — one-shot artifact builder (run offline, output committed)
- `scripts/annotate_corpus_pubtator.py` — driver for the dockerized PubTator-3 stack
- `scripts/audit_bench_fixture_entities.py` — produces the non-blocking audit doc
- `docker/pubtator3/Dockerfile` + `docker-compose.pubtator3.yml` — offline-only, not part of the VPS image
- `tests/unit/test_entities_gazetteer.py` — golden entity tests
- `tests/unit/test_entity_overlap.py` — score-feature math
- `tests/integration/test_search_with_entities.py` — full search-path smoke
- `docs/superpowers/reviews/2026-05-13-c-gamma-fixture-audit.md` — generated, non-blocking
- One alembic migration: `passage_entities` + `chapter_entities` tables and indexes

**Modified:**
- `genereview_link/retrieval/repository.py` — wire the entity-overlap join into the top-50 path
- `genereview_link/retrieval/rerank.py` — add `entity_overlap_boost` to `score_breakdown` and the property-intent role boost
- `genereview_link/api/routes/passages.py` — surface `entity_overlap` in opt-in `score_breakdown` field
- `Makefile` — add `annotate-corpus`, `build-gazetteer`, `audit-bench` targets
- `genereview_link/config/settings.py` — feature flag `ENTITY_LAYER_ENABLED` (default true once the migration applies)

**Untouched:**
- `tests/fixtures/ranking_bench.jsonl` — locked
- `scripts/bench_ranking.py` — the existing C-alpha harness measures C-gamma without changes (it consumes `score_breakdown` and the bench JSON; entity_overlap is additive)
- `genereview_link/retrieval/embeddings.py` — BGE-small stays
- Docker/VPS prod image — no PubTator at runtime

## Hard gates (replace C-beta's mistaken P@1-only gate)

1. **Recall@5 ≥ 0.826** on `tests/fixtures/ranking_bench.jsonl` with `rerank=rrf` and the entity layer enabled. *No regression below the C-alpha baseline. Non-negotiable.*
2. **P@1 ≥ 0.50** on the same bench. *Meaningful lift over C-alpha's 0.408.*
3. **p95 search latency overhead ≤ 50 ms** vs C-alpha on the same VPS-class hardware. *Measured via `/passages/search` end-to-end.*
4. **`must_change` regressions = 0** on `rerank=rrf`.
5. **At least two of three marquee misses resolve correctly:** HFE C282Y → `NBK1440:0051` top-1, GRIN2B → any `NBK501979:*` in top-5, CFTR F508del → `NBK1250:0032` top-1 or top-2.

## Tests

- **Golden entity tests (new):** unit tests assert that the gazetteer extracts `(@GENE_GRIN2B, NCBIGene:2904)` from "GRIN2B-related neurodevelopmental disorder phenotype spectrum" and `(@VARIANT_p.C282Y_HFE, rs1800562)` from "HFE C282Y allele frequency". Locks the normalization layer separately from the bench so silver-label noise doesn't mask a regression.
- **Score-feature math tests:** boundary cases for `entity_overlap_boost` — empty query entities, empty passage entities, multi-gene chapter cap, missing normalized_id rows (surface-form-only fallback).
- **Integration smoke (new):** end-to-end search via the FastAPI test client; assert that with the entity layer enabled, GRIN2B query returns at least one `NBK501979:*` passage in top-5.
- **Bench rerun:** existing `make bench-ranking` runs unchanged; entity layer surfaces in the per-passage `score_breakdown`.

## Rejected alternatives

- **Re-run C-beta with a distilled student CE.** The C-beta diagnosis says the CE-side levers are the wrong layer to act on. A student CE can be revisited once the entity layer is in production and the residual gap (if any) is characterized.
- **ColBERTv2 late-interaction reranker.** Strongest research alternative per Yang 2025 *Sci Rep* (PMID 40413225). Different architecture, different operational profile (PLAID index, MaxSim at query time). Worth a future C-delta if entity-aware retrieval underperforms; not the right bet to make now.
- **Fine-tune BGE-small on GeneReviews silver pairs.** Per Arzideh 2026 *JMIR* (PMID 41880603), competitive with CE rerank. Limited by the 299-entry silver size; doesn't fix structural anchoring (GRIN2B-class miss). Worth running in parallel as a separate phase, not folded in here.
- **Use chapter PMIDs to pull PubTator's pre-annotated full text.** Tested: PubTator does return annotations for GeneReviews chapter PMIDs (e.g., PMID 20301613 for `NBK1440`), **but coverage is the chapter's PubMed summary only (~3-5 KB)**, not the full chapter body. GeneReviews chapters are on NCBI Bookshelf, not PMC; the body never enters PubTator's pipeline. Suitable as a free chapter-level seed for the gazetteer (and as ground-truth for cross-validating C2.1's chapter-level entity sets), but cannot replace running the stack ourselves for passage-level entities. Recorded as an offline enrichment step in C2.1, not as a substitute.
- **Lex-only variant-alias query expansion (without a real entity layer).** A regex-based "C282Y → p.Cys282Tyr OR c.845G>A" expansion would fix HFE but not GRIN2B or any structural case. Pays half the engineering cost for one-third of the lift.

## Open questions

1. **Gazetteer drift over time.** HGNC and MeSH publish updates monthly. Pin a version in the gazetteer artifact; the operational plan needs a refresh cadence. Not blocking C-gamma ship.
2. **Multi-species disambiguation.** PubTator's stack handles species; GeneReviews is human-only. Decide whether to filter the offline pipeline to human only (faster, fewer entities) or keep all species and rely on the gazetteer to ignore non-human matches. Default: filter to human at the offline stage.
3. **Variant surface-form coverage gap.** PubTator's tmVar3 recognizes HGVS forms reliably; legacy 1-letter forms ("C282Y", "G6PD A−") sometimes miss. The gazetteer should backstop with regex + the alias table mined from C2.1's `passage_entities`. Coverage check is part of the bench audit (C2.5).

## Sources

- C-alpha results: `docs/superpowers/reviews/2026-05-12-c-alpha-bench-results.md`
- C-beta results (negative): `docs/superpowers/reviews/2026-05-13-c-beta-bench-results.md`
- Wei et al, *PubTator 3.0: an AI-powered literature resource*, Nucleic Acids Research 2024 (PMID 38410657)
- Luo et al, *AIONER: all-in-one biomedical NER*, Bioinformatics 2023 (PMID 37171899)
- Wei et al, *GNorm2*, Bioinformatics 2023 (PMID 37878810)
- Wei et al, *tmVar 3.0*, Bioinformatics 2022 (PMID 36071328)
- Leaman & Lu, *TaggerOne*, Bioinformatics 2016 (PMID 27283952)
- Muhetaer et al, *Medical QA dialogue datasets in RAG systems*, Sci Rep 2025 (PMID 41444718) — empirical "rerank-only > cascade-RRF-rerank, representation > algorithm complexity"
- Yang et al, *Dual retrieving and ranking medical LLM with RAG*, Sci Rep 2025 (PMID 40413225) — ColBERTv2 in medical RAG, +10% accuracy
- Arzideh et al, *Fine-Tuning Clinical Embedding Models for RAG*, JMIR 2026 (PMID 41880603) — embedding fine-tune competitive with CE rerank
- Jin et al, *TrialGPT*, Nat Commun 2024 (PMID 39557832) — recall-favoring retrieval + downstream ranking pattern
- Zhang et al, *Long context in retrieval-augmented LLMs for medical QA*, NPJ Digit Med 2025 (PMID 40316710) — lost-in-the-middle motivation for recall preservation
