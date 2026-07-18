# Ranking Architecture Redesign — Design Spec

**Date:** 2026-05-12
> Historical record

**Author:** senior MCP engineer (Opus 4.7, in collaboration with project maintainer)
**Source review:** `docs/superpowers/reviews/2026-05-12-mcp-llm-deep-toolset-review.md`
**Companion spec:** `docs/superpowers/specs/2026-05-12-deep-review-solutions-design.md` (subsumes B7 from this doc)
**Branch target:** new branch off `main` after `feat/ranking-quality-v1` (Batch B) merges.
**Predecessors:** Batch B (role-aware ranking) — already in flight on `feat/ranking-quality-v1`.

---

## Goal

Fix the **recall-ceiling failure** that role-aware ranking cannot reach. The current pipeline gates dense scoring behind the top-50 lexical candidates ([`genereview_link/retrieval/repository.py:655-670`](../../genereview_link/retrieval/repository.py)). Passages outside that window — confirmed live cases: hemochromatosis target at lexical rank 66, MCAD target at rank 114 — can never be promoted no matter how good the role multiplier or intent boost is.

The fix is structural: run lexical retrieval and dense retrieval **independently corpus-wide**, fuse with RRF, then optionally pass the top-50 of the fused union to a **cross-encoder reranker**. Pick the cross-encoder model empirically against a benchmark built specifically for the GeneReviews corpus, not from off-the-shelf datasets that target a different corpus type.

Target outcome: top-1 is the topically-correct passage on the curated regression set, **including the recall-ceiling cases** (hemochromatosis #66, MCAD #114, BRCA1 risk-reducing surgery, plus reviewer-style prompts surfaced during the cross-LLM deep review).

## Out of scope

- Anything in the companion deep-review-solutions spec (B1, B2–B6, B8–B13 fixes).
- ML-trained intent classifier (keep the rule-based `QUERY_INTENT_BOOSTS` at [`rerank.py`](../../genereview_link/retrieval/rerank.py)).
- Embedding model swap (BGE-small-en-v1.5 stays as the bi-encoder).
- HGVS-aware tokenizer for `tsquery`. Future spec.
- BM25 (`pg_textsearch` extension) replacement for `ts_rank`. Future spec.
- User-curated review indexes (pubtator-style). Future feature.

## Architecture overview

### Current pipeline (broken)

```
query → tsquery lexical retrieval (top 50)
        ↓
        dense scoring (GATED to lexical's 50 candidates only)
        ↓
        RRF fusion
        ↓
        adjusted_score = rrf × role × (1 + intent_boost)   [Batch B already here]
        ↓
        top-N
```

Problem: if the right passage is at lexical rank 51+, dense scoring never sees it, so RRF cannot recover it. Confirmed live: hemochromatosis target at lexical rank 66.

### Redesigned pipeline (Option C from the brainstorm)

```
                      user query string
                              │
            ┌─────────────────┼─────────────────┐
            ▼                                   ▼
   lexical retrieval                    dense retrieval
   (tsquery, top K=200)            (BGE-small embed → pgvector
                                    HNSW, top K=200, corpus-wide)
            │                                   │
            └────────────────┬──────────────────┘
                             ▼
                    UNION (~250-400 unique)
                             ▼
                  RRF fusion across both ranks
                             ▼
       adjusted_score = rrf × role_multiplier × (1 + intent_boost)
                  [existing Batch B logic, unchanged]
                             ▼
              ┌──────────────┴──────────────┐
              │                             │
        rerank=rrf                    rerank=ce
        (stop here)                          │
                                             ▼
                                  cross-encoder rerank
                                  (top 50 of fused union)
                                             ▼
                              re-apply role_multiplier × intent_boost
                              to cross-encoder score
                                             ▼
                                     top-N output
```

`rerank=lexical` short-circuits at the lexical step (returns lexical-only ordering, unchanged from today).
`rerank=off` skips RRF and section_priority tiebreak (debug-only, unchanged from today).

## Mode taxonomy

The `rerank` query parameter on `/passages/search` accepts the existing 3 values plus 1 new value. **`rrf` is upgraded in-place** so existing callers get the fix automatically.

| Mode | Semantics | Latency budget (VPS 6c/24GB, CPU only) |
|---|---|---|
| `lexical` | tsquery only; unchanged from today | ~10-15ms |
| `rrf` (upgraded) | parallel lexical-top-200 ∪ dense-top-200 → RRF → adjusted_score | ~40-60ms |
| `ce` (new) | rrf pipeline → cross-encoder on top 50 → adjusted_score → top-N | ~80-600ms (depends on chosen model — see shortlist below) |
| `off` | debug-only repo order; unchanged from today | ~5ms |

Default mode remains `rrf`. The semantics change for `rrf` callers is an **intended quality improvement** — but it is a real behavior change. Current route code gates dense scoring to lexical candidates at [`api/routes/passages.py:309`](../../genereview_link/api/routes/passages.py) and [`api/routes/passages.py:321`](../../genereview_link/api/routes/passages.py); switching to corpus-wide dense union can perturb exact-symbol or variant-string top-1s that previously happened to rank well only because dense was constrained. The C-α `must_not_regress` gate (see Phase C-α below) is the hard guard against this.

**Documentation update:** `genereview://usage` must clearly state the new `rrf` semantics and add `ce` to the rerank-modes section. The existing tool description on `search_passages` route gains a one-line addition explaining when to pick `ce` (precision-critical queries; accept ~200ms latency).

## Three implementation phases

The work is decomposed into three phases following the standard ML/IR experimental-design pattern: **build the apparatus**, **run the experiment**, **productionize the winner**. The cleanest analogy is a clinical trial — endpoints and protocol are designed before enrollment, enrollment runs the protocol unchanged, and analysis happens only after enrollment closes. The same discipline applies here.

### Why split, not collapse

| Phase | Clinical trial analogue | What it builds |
|---|---|---|
| **C-α** | Protocol design + cohort assembly | Parallel-retrieval pipeline + benchmark dataset + metric (P@1 + MRR@5 + Recall@5) + `make bench-ranking` harness |
| **C-β** | Locked trial execution / assay run | Measure 4 candidate cross-encoders against the benchmark; no production change |
| **C-γ** | Analysis + SOP publication | Implement `rerank=ce` with the chosen model, harden ops, ship |

Collapsing the phases produces specific failure modes:

| If you collapsed… | …you'd fail this way |
|---|---|
| C-α and C-β (build bench while picking model) | Bench keeps changing during eval; candidate results aren't comparable across runs. Same failure mode as "we kept tweaking the endpoint definition during enrollment" — no clean comparison. |
| C-β and C-γ (pick and ship in one step) | Model committed on theory or vibes; no quantified gain to justify the latency cost; no rollback baseline. Same failure mode as "we picked the drug before pre-registering endpoints" — selection bias. |
| Skip C-β entirely (skip benchmark, ship a model) | No way to detect regressions later because the bench was never built. Future model swaps become guesswork. |

**C-α alone delivers half the value** — fixing the recall ceiling (hemochromatosis #66, MCAD #114) requires zero new model dependency, because the bug is the lexical-50 gate, not the absence of cross-encoders. C-α can ship to the public host as soon as it lands.

**C-γ is the precision win on top of C-α.** It only ships if C-β shows meaningful gain over hybrid-only on the benchmark. If no candidate cross-encoder beats `rrf`-upgraded on the composite metric, C-γ is dropped and the spec closes after C-β — that's the discipline the split enforces.

### Phase C-α — Hybrid retrieval + benchmark harness

**Deliverables:**
1. `repository.py` parallel-retrieval path: lexical-top-K and dense-top-K run independently **over the filtered candidate universe** (i.e., `gene`, `nbk_id`, `sections`, and `heading_path_contains` filters are applied to *both* branches, not just lexical). Union is RRF-fused. `rerank=rrf` upgraded to use this path. `rerank=lexical` and `rerank=off` unchanged.

   **Important — filter scope.** Today's gated pipeline applies user-supplied filters only to the lexical step, and dense scoring naturally inherits the same scope because it operates on lexical's candidate set. With the parallel-retrieval upgrade, the dense SQL must explicitly JOIN `genereview_passages` (and `genereview_chapters` where needed) and apply the same `WHERE` clauses, otherwise filtered searches would leak out-of-filter passages into results. Add a route-level test matrix for `gene=`, `nbk_id=`, `sections=`, and `heading_path_contains=` under upgraded `rerank=rrf` to lock the scope contract.

   **Important — HNSW + filter query plan.** The current corpus has only **one global HNSW index on `embedding`** (per [`db/migrations/data/0003_embeddings_bge384.sql`](../../genereview_link/db/migrations/data/0003_embeddings_bge384.sql); index built post-COPY by the embed CLI per [`ingest/orchestrator.py:129-138`](../../genereview_link/ingest/orchestrator.py)). pgvector applies `WHERE` filters **after** the approximate ANN scan ([pgvector filtering docs](https://github.com/pgvector/pgvector#filtering)). This means a selective filter (e.g., `gene='HFE'` restricts to ~30 passages out of ~25K) interacting with `ORDER BY embedding <=> $q LIMIT 200` can either:

   - Return *fewer than 200 dense candidates* because HNSW's 200-row prefix didn't happen to contain enough rows matching the filter — silently degrading recall. OR
   - Force PostgreSQL to fall back to an exact scan + filter, which can be slow on the full embeddings table.

   C-α must:
   - Run `EXPLAIN ANALYZE` on the dense branch with each of the filter combinations against the live corpus and **record the plan choices** in the spec addendum.
   - Configure `hnsw.iterative_scan = 'strict_order'` (or `'relaxed_order'`) and `hnsw.ef_search` (default 40, suggest ≥200) at session level inside the dense SQL ([pgvector iterative scan docs](https://github.com/pgvector/pgvector#iterative-index-scans)).
   - For *highly selective* filters (e.g., a single `nbk_id`, which limits to typically 20-50 passages), **bypass HNSW and run exact KNN over the filtered set**. A B-tree index on `(nbk_id)` already exists at [`0002_passages.sql:26`](../../genereview_link/db/migrations/data/0002_passages.sql:26); cosine distance over 50 vectors is a microsecond on 6 cores. Pick the strategy at runtime based on estimated filter selectivity.
   - For moderately selective filters (e.g., `sections=['management']`, ~12% of corpus), use HNSW with iterative scan + larger `ef_search`. Add a partial HNSW index per section *only* if measurement shows the iterative-scan path is too slow.
   - Add supporting indexes if missing: a GIN index for `gene_symbols` on the chapters table already exists ([`0001_chapters.sql:17`](../../genereview_link/db/migrations/data/0001_chapters.sql:17)); a B-tree on `chapter_section` already exists ([`0002_passages.sql:28`](../../genereview_link/db/migrations/data/0002_passages.sql:28)); `heading_path` may need a GIN trigram index for `heading_path_contains` substring matching (verify at C-α start).
2. Benchmark fixture `tests/fixtures/ranking_bench.jsonl` (~200 queries — see Benchmark design below).
3. `Makefile` target `bench-ranking` that runs the harness across all retrieval modes and emits a comparison table.
4. Scoring script computing P@1, MRR@5, Recall@5 per mode.

**Gate (all three required):**
1. `make ci-local` clean.
2. Benchmark shows hemochromatosis and MCAD targets reachable (in top-10 for upgraded `rerank=rrf`).
3. **Hard regression gate (with a documented caveat):** all `must_not_regress` entries in `tests/fixtures/ranking_baseline.json` continue to return their locked `expected_top1_passage_id` under upgraded `rerank=rrf`. Currently 3 entries (HFE C282Y allele frequency, tetra-amelia gene WNT3, BRCA1 founder mutation Ashkenazi). Any change in their top-1 fails the phase by default — **except** when the new top-1 is semantically better (see audit caveat below). This is the guard against the corpus-wide dense union perturbing exact-symbol / variant-string top-1s that previously ranked well partly because dense was constrained to lexical-top-50.

   **Audit caveat — known-wrong baselines are accepted as candidates for upgrade.** The tetra-amelia/WNT3 row currently locks `NBK131811:0005` (a Coffin-Siris molecular-testing passage) as the expected top-1, but its own `notes` field explicitly says "the current result is Coffin-Siris syndrome molecular testing rather than tetra-amelia/WNT3; this is a no-intent baseline guard." If upgraded `rerank=rrf` returns a passage that is *actually* about tetra-amelia / WNT3 (i.e., from NBK1100, the WNT3-related GeneReviews chapter, or similar), that is a semantic improvement and the row should be **manually re-locked** to the new gold rather than treated as a regression. C-α therefore splits the gate into two checks: (a) exact-symbol locks (HFE C282Y, BRCA1 Ashkenazi founder) — these are anchors and any change fails the phase; (b) knowingly-wrong locks (currently just WNT3) — a change is reviewed manually, and if confirmed-better, the baseline file is updated as part of C-α's deliverable. The split must be encoded by adding a per-row `regression_kind` field to `ranking_baseline.json` with values `exact-symbol-anchor` or `pending-improvement`.

**Effort:** ~1 week.

**No new model dependency.** This phase delivers half the value (recall ceiling fixed) without touching cross-encoders.

### Phase C-β — Model selection benchmark

**Deliverables:**
1. Run the C-α benchmark harness with each candidate cross-encoder (see shortlist below).
2. **Preflight step (run OFF the live VPS):** before running the full benchmark, instantiate every candidate model end-to-end (download weights, load into memory, run a single throwaway forward pass on a dummy query/passage pair). Surface load errors immediately rather than mid-benchmark. Record the **adapter code path** (activation, native output type — see C-γ Score combination rule) per model so C-γ wires the chosen model identically. Failing models are dropped from the bench with a logged reason. **Environment requirement:** C-β preflight + bench MUST run on a staging machine or during a maintenance window with the public MCP stopped, not concurrently with production traffic on the 6-core VPS. Model downloads (some are ~1 GB), torch init, and repeated batch=50 forwards on a small CPU contend with production CE/RRF requests. Load and benchmark one candidate at a time with explicit unload + `gc.collect()` between candidates; record peak RSS and CPU% per candidate in the C-β results.
3. Generate a comparison table: each model × each metric × each retrieval mode.
4. Empirical model pick committed to spec as an addendum or follow-up doc.
5. Optional: external-validity sanity check using BEIR-bioASQ or NFCorpus via `mteb` or `pyserini` — confirms the picked model isn't overfit to our silver set.

**Gate:** model selection documented; no production change yet.

**Effort:** ~3 days (mostly inference runtime, since the harness is built in C-α).

### Phase C-γ — Cross-encoder mode ship

**Deliverables:**
1. `rerank=ce` mode implemented using the model chosen in C-β.
2. In-process model loading via sentence-transformers (or FlagEmbedding if the chosen model is BGE family). **Preload before fork** with **`device="cpu"` forced explicitly** at the model-load call (not relying on `device="auto"` like the existing embedding loader at [`genereview_link/retrieval/embeddings.py:115-151`](../../genereview_link/retrieval/embeddings.py)). Reason: PyTorch's "[poison-fork](https://pytorch.org/docs/stable/notes/multiprocessing.html#multiprocessing-cuda-note)" issue — initializing CUDA runtime in the master process before fork causes worker corruption. The lockfile carries CUDA-capable torch wheels, so accidental GPU init is possible if the production host ever gets an accelerator. The existing `docker/gunicorn_conf.py:39` already enables `preload_app=True`, so CoW sharing works once we guarantee CPU-only load. Add a boot-time assertion `assert not torch.cuda.is_initialized()` immediately after the model load. Worst-case memory cost is ~one model copy, not N (CoW). The trade-off is a ~2-5s slower boot, which is acceptable for a long-running server. **Do not also document "lazy on first use"** — the two strategies are mutually exclusive and lazy-per-worker would defeat CoW sharing.
3. LRU cache keyed on `(query_hash, passage_id) → score`, invalidated on `corpus_version` bump.
4. Failure handling: boot-time model load is loud-fail; per-query 30s timeout → graceful fallback to `rrf` mode + `_meta.diagnostics.suggestions += ["ce_timeout_fallback"]`.
5. `genereview://usage` updated with the `ce` mode entry and latency guidance.
6. Smoke tests for the new mode.

**Score combination rule for `rerank=ce` (REQUIRED — decide before C-γ ships).** Cross-encoder outputs are not all calibrated to the same range. Some models return raw logits (can be negative), some return sigmoid probabilities in [0, 1], some return cosine-like similarity. Naively applying `role_multiplier × (1 + intent_boost)` to a raw negative logit will *invert* the role penalty (a cross_reference passage with logit -2 becomes -0.8 after × 0.4, which ranks higher than -2). The architecture diagram's "re-apply role × intent after cross-encoder" step must therefore specify a normalization:

- **Step 1 — extract raw logits via a project-owned adapter, then apply exactly one project-owned sigmoid.** Different candidate models behave differently out of the box: MedCPT and ms-marco-MiniLM examples return raw logits; mxbai's published examples apply sigmoid client-side; BGE rerankers document "score is unbounded"; `sentence-transformers` 5.4+ may apply a default activation depending on the model's config. Naively combining these would risk **double-sigmoid** (mxbai if we apply sigmoid on top of its already-sigmoided output) OR **inconsistent scoring** across candidates (some bounded, some not). The adapter contract is: each model's adapter explicitly disables any library-applied activation, returns raw logits, and the rerank pipeline applies a single `sigmoid()` before any role/intent multiplication. C-β must record the **adapter code path** (not just "activation choice") for each candidate so C-γ wires the chosen model identically.

  Sources for the adapter-level activation behavior: [MedCPT model card](https://huggingface.co/ncbi/MedCPT-Cross-Encoder), [ms-marco-MiniLM-L12-v2](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L12-v2), [mxbai-rerank-base-v1](https://huggingface.co/mixedbread-ai/mxbai-rerank-base-v1), [BGE-reranker-base](https://huggingface.co/BAAI/bge-reranker-base).

- **Step 2 — combine:** `final_score = sigmoid_ce_score × role_multiplier × (1 + intent_section_boost)`. Same multiplicative pattern as Batch B's `adjusted_score`, but now multiplying a bounded positive value, not a raw logit.

**Sigmoid is not invariant for all output styles.** Sigmoid is the correct normalization for *binary-classification logits* (which is what MedCPT, ms-marco-MiniLM, and most BGE rerankers output: a single scalar trained with binary cross-entropy on relevant/non-relevant pairs). It is **not** the correct normalization for cosine-similarity-style outputs (some embedding-based rerankers; mxbai's exact head behavior must be verified during C-β). For those, sigmoid arbitrarily squashes already-bounded values, changing the relative strength of the role/intent multiplication.

The adapter contract is therefore **conditional**, per candidate:

| Native output style | Normalization | When |
|---|---|---|
| Raw logit (cross-entropy trained scalar) | `sigmoid(logit)` | MedCPT, MiniLM, BGE rerankers (verify per-model in C-β preflight) |
| Already-bounded probability in [0, 1] | identity (no double-sigmoid) | only if confirmed by the model card or by inspecting the model's head |
| Cosine-similarity-style ([-1, 1]) | min/max calibration against benchmark distribution recorded in C-β: `(score - p1) / (p99 - p1)` clamped to [0, 1] | mxbai if head turns out to be similarity-style — verify; some BGE variants |
| Unbounded similarity (no fixed range) | quantile calibration on the C-α validation set (record p1, p99 in spec addendum); apply at production time | rare; flag for re-evaluation if a candidate falls here |

C-β must record **per-candidate**: the native output style, the chosen normalization, and the calibration constants (if any). C-γ wires the chosen model with the exact same normalization recorded in C-β — no live re-derivation. If a candidate's output style cannot be cleanly classified, drop it from the shortlist rather than ship an arbitrary squashing.

Alternative considered and rejected: keep CE score primary and apply role/intent as small additive prior. Cleaner mathematically (no normalization step) but breaks consistency with the existing Batch B `adjusted_score` semantics, and means cross_reference passages can only be edged out by ties — not actually demoted when CE thinks they're highly relevant. The conditional-normalization-then-multiply pattern preserves the Batch B contract while being honest about cross-model output heterogeneity.

The architecture diagram earlier in this spec ("re-apply role_multiplier × intent_boost to cross-encoder score") is shorthand for this normalize-then-multiply rule.

**Gate:** end-to-end smoke against the regression set; cross-encoder mode wins on P@1 vs `rrf` on the benchmark; latency p95 within whatever budget the chosen model implies.

**Effort:** ~1 week.

## Cross-encoder model shortlist for Phase C-β

VPS constraint: **6 cores / 24 GB RAM, CPU-only inference**. Models above ~300M params become impractical at batch=50 on this hardware. The shortlist is pruned accordingly and includes one strong **medical-domain** candidate to test the "biomedical pretraining matters" hypothesis.

| Candidate | Params | License | Why it's on the shortlist | Est. latency (50 pairs, 6-core CPU) |
|---|---|---|---|---|
| **[ncbi/MedCPT-Cross-Encoder](https://huggingface.co/ncbi/MedCPT-Cross-Encoder)** | 110M | Public domain (NIH) | **NCBI-built, biomedical SOTA.** Trained on **255 million query/article pairs from PubMed search logs** ([Jin et al 2023, PMID 37930324](https://pmc.ncbi.nlm.nih.gov/articles/PMC10627406/)). Strongest biomedical publisher / search-log match — but **not guaranteed** to win, because GeneReviews chapters are structured long-form Bookshelf content, not PubMed abstracts. The training distribution shift (search-log query → abstract retrieval) vs deployment (clinical query → chapter passage retrieval) is real and is exactly what C-β measures. | ~150-250ms |
| **[cross-encoder/ms-marco-MiniLM-L12-v2](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L12-v2)** | 33M | Apache 2.0 | The "if this wins, ops is trivial" baseline. Multiple recent benchmarks show it beating 10× larger generic models. Tests whether model strength × speed beats domain pretraining. | ~50-80ms |
| **[mixedbread-ai/mxbai-rerank-base-v1](https://huggingface.co/mixedbread-ai/mxbai-rerank-base-v1)** | 184M | Apache 2.0 | Modern (2024) general-domain reranker, BEIR-competitive, fits the 6-core budget comfortably. | ~250-400ms |
| **[BAAI/bge-reranker-base](https://huggingface.co/BAAI/bge-reranker-base)** | 278M | MIT | Anchors "general-SOTA at borderline-acceptable latency". If MedCPT loses to this, biomedical pretraining didn't help on our corpus and we'd revisit assumptions. | ~400-600ms |

**Models considered and dropped** (do not run unless the above 4 all underperform):

| Model | Why dropped |
|---|---|
| BAAI/bge-reranker-v2-m3 (568M) | ~1000ms+ at batch=50 on 6 cores — exceeds practical p95 budget |
| Qwen3-Reranker-0.6B | Same reason; too large for 6-core CPU |
| zerank-1-small (1.7B LoRA) | Won't fit in worker memory once multiplied by worker count |
| MedGemma-4B / -27B | These are **generative** models, not cross-encoders. Used as LLM-as-judge they take seconds per query — orders of magnitude outside our latency budget. Stay with purpose-built rerankers. |

**Optional fifth — only if C-β has spare time.** Add a BioASQ / PubMedBERT-based reranker from [IEETA/BioASQ-14B](https://huggingface.co/IEETA/BioASQ-14B) (Apache 2.0; bundled experimental medical rerankers, includes PubMedBERT and BioBERT variants) as a clearly-labeled **external-validity challenger**. Not in the core 4-model bake-off because it is a bundled experimental collection rather than a clean primary checkpoint, but it would tell us whether *any* biomedical-pretrained model beats MedCPT on our corpus or whether MedCPT is already pulling all the medical-domain value available.

[AronowLab/BOND-reranker](https://huggingface.co/AronowLab/BOND-reranker) was also considered but is entity-normalization focused, not passage retrieval — does not belong in this benchmark.

**Decision criteria.** Highest composite `(P@1 + MRR@5 + Recall@5) / 3` on the benchmark, with two hard gates:

1. **`must_not_regress` gate (regression_kind-aware):** the C-β gate honors the same `regression_kind` split defined in C-α (see "Audit caveat — known-wrong baselines" above). For `regression_kind=exact-symbol-anchor` rows, any change disqualifies the model. For `regression_kind=pending-improvement` rows, a change is reviewed in batch: a model is allowed to update at most **N pending-improvement rows per C-β run** (proposed cap: **1**, to prevent silent regressions across multiple rows hiding behind individual re-locks), with before/after evidence required and the baseline updated in the same PR. The current 3 must-not-regress queries are: HFE C282Y (exact-symbol-anchor), BRCA1 Ashkenazi founder (exact-symbol-anchor), tetra-amelia WNT3 (pending-improvement — see C-α audit caveat).

2. **Latency gate:** any model whose p95 on the bench exceeds 750ms on the 6-core VPS is disqualified. Slow models that win on quality don't ship.

**The hypothesis the benchmark is designed to test**: does NCBI's medical-domain pretraining on PubMed search logs translate into better ranking on GeneReviews chapters than general-purpose rerankers of similar or larger size? Plausible because GeneReviews chapters use the same biomedical vocabulary as PubMed; equally plausible that general-purpose rerankers handle the structured chapter format better. C-β answers this empirically.

If MedCPT wins clearly, the spec recommendation is to ship MedCPT in C-γ. If MiniLM wins, ship MiniLM (5× smaller, ~3× faster). If they're within ~2 percentage points of each other on the composite, ship MiniLM (latency advantage breaks ties).

## Benchmark design

### Sizing and composition

```
~150  silver queries generated by Codex via codex:rescue subagent
       (stratified across section, chapter age, gene popularity, intent)
+   5  existing tests/fixtures/ranking_baseline.json (Bernt's curated regression set)
+ ~25  deep-review failures (hemochromatosis #66, MCAD #114, BRCA1 risk-reducing
       surgery, HFE C282Y, CF F508del, GRIN2B phenotype, Lynch surveillance,
       phenocopy cases, plus reviewer-style prompts from the cross-LLM session)
+ ~30  external-validity entries (MIRAGE / RAGCare-QA queries about genes
       covered by the GeneReviews corpus, gold passage labeled manually)
─────
~210 queries total
```

### Entry schema

```jsonl
{
  "query": "BRCA1 risk-reducing mastectomy salpingo-oophorectomy",
  "expected_top1_passage_id": "NBK1247:0024",
  "expected_top5_passage_ids": ["NBK1247:0024", "NBK1247:0025", "NBK1247:0026"],
  "status": "must_change",  // or "must_not_regress" or "silver"
  "source": "deep_review_session_2026-05-12",
  "intent": "management",
  "section": "management",
  "gene": "BRCA1",
  "chapter_nbk_id": "NBK1247",
  "notes": "Top-1 currently NBK1247:0035 (a cross-reference); should be 0024 (substantive prevention guidance)."
}
```

The `expected_top5_passage_ids` field is new (existing `ranking_baseline.json` only has top-1). For entries without graded labels, top5 is set to `[expected_top1_passage_id]` and Recall@5 collapses to P@1 for those queries.

### Generation method (Codex via codex:rescue)

The benchmark is generated by a `codex:rescue` subagent given:

1. **A chapter worklist** — ~30 chapters stratified across:
   - Sections (management, diagnosis, molecular_genetics, clinical_features, genetic_counseling)
   - Chapter recency (recently updated, 5+ years old)
   - Gene popularity (well-known like BRCA1/CFTR vs rare like GRIN2B)
   - Each chapter contributes ~5 substantive evidence passages

2. **A prompt template** — instructs Codex to read a passage and emit 1-2 clinical queries that a researcher or clinician would use to find this passage. Format: JSONL line with `query`, `expected_top1_passage_id` (the source passage), `intent`, `section`, `notes`.

3. **An output target** — `tests/fixtures/ranking_bench.jsonl`.

The full Codex prompt template is committed to `tests/fixtures/ranking_bench_generation_prompt.md` for reproducibility. The generated dataset itself is the canonical artifact — future regeneration is optional, not required.

### Auto-validation (no second LLM needed)

Once the silver set is generated, validate each entry by running it through the live MCP across the **non-candidate** retrieval modes only: `lexical`, `rrf-upgraded` (Option B, no cross-encoder), and `off`. **No shortlisted cross-encoder is used for auto-validation** — including any of the C-β candidates as a validator would bake one model's relevance preferences into the dataset and bias C-β toward that model's style.

- **Bucket A** — all 3 modes return the gold in top-5 → confirmed silver, kept as-is.
- **Bucket B** — modes disagree on top-5 → flagged for SME spot-check (Bernt eyeballs ~30-50 entries, corrects gold or rewrites query).
- **Bucket C** — no mode returns the gold in top-50 → either the silver is a perfect test of recall (kept as `must_change`) or the silver is bogus (dropped).

If a non-CE 3-mode validator proves too weak (too many Bucket-B / Bucket-C entries to spot-check), the fallback is a **non-candidate cross-encoder** explicitly excluded from C-β — e.g., a small generic reranker that we commit to never picking for production. Document the validator choice in `tests/fixtures/ranking_bench_generation_prompt.md`.

This makes the retrieval pipeline itself the validator — closer to deployment than using a second LLM to grade — *without* the bias of using a candidate model as referee.

### External-validity check

After the benchmark is built and the cross-encoder picked, run that model on **BEIR-bioASQ** (or **NFCorpus**) via `mteb` or `pyserini`. If our top model also places near SOTA on a public biomedical IR benchmark, we have high confidence. If it wins on our set but is mediocre publicly, treat with suspicion (possible overfitting to silver-set distribution).

## Caching strategy

LRU cache keyed on `(query_hash, passage_id) → cross_encoder_score`. Sized for ~10K entries (≈ 80 bytes per entry × 10K = 800 KB). Invalidated when `corpus_version` changes.

Reasoning:
- For repeat queries, every passage we re-rank is cache-hit. Latency collapses to near-zero.
- For novel queries against the same corpus, dense and lexical retrieval are fast; only the cross-encoder pass on 50 new (query, passage) pairs is the cost.
- For brand-new corpus versions, cache is bust; behavior matches first-call cold start.

Implementation: `functools.lru_cache` is unsuitable because it ignores `corpus_version`. Use a small custom dict-backed LRU with explicit invalidation on the existing corpus-version-change hook (look for where `corpus_version` is computed and add the invalidation there).

## Failure mode and degradation

**Preload-before-fork initialization** — load the model in the gunicorn master before workers fork, so Linux copy-on-write shares the weights:

| Stage | What happens |
|---|---|
| **Boot — gunicorn master** | Checks model files on disk (`os.path.exists` on the expected paths, ~milliseconds). If absent and `rerank=ce` is enabled in config, **fail loud** and refuse to start. If `ce` is disabled in config, skip model load entirely and proceed. Otherwise: **load the cross-encoder model into the master process** before fork (the existing `preload_app=True` in `docker/gunicorn_conf.py:39` makes this happen automatically when the model is imported at app-init time). ~2-5s one-time cost. |
| **Fork to workers** | Each gunicorn worker inherits the loaded model via CoW. No per-worker load step. Memory cost is ~one model copy total (CoW), not N × model. |
| **Per-request `rerank=ce`** | Workers reuse the inherited model directly. No init cost on first request. |

| Runtime failure | Behavior |
|---|---|
| Model load fails in master at boot (e.g., corrupted file) | Loud crash with the model path in the log. Workers don't start. Operator must fix and restart. |
| Model loads but inference times out per-query (>30s) | **Graceful degrade.** Return `rrf`-ordered results; add `"ce_timeout_fallback"` to `_meta.diagnostics.suggestions`. Log a warning with query hash + duration. |
| Model loads, inference succeeds, but returns NaN scores | **Skip the cross-encoder step.** Return `rrf` ordering; add `"ce_invalid_score"` to suggestions. |
| First request after `corpus_version` change | Cache bust; first request pays the full cross-encoder cost, subsequent identical requests cache-hit. (Worker model copy itself is unaffected — only the per-`(query, passage)` score cache is invalidated.) |

## Model serving

In-process via `sentence-transformers` (works for all four shortlisted models — MedCPT, MiniLM, mxbai, bge-reranker-base). Loaded in the gunicorn master before fork; shared across workers via CoW (see "Preload-before-fork initialization" above).

VPS resource budget (6 cores / 24 GB RAM):
- ~4 gunicorn workers (~1.5 cores per worker leaves headroom for I/O-bound work).
- Worst-case model footprint in this shortlist: bge-reranker-base at ~560 MB FP32 or ~280 MB FP16.
- **CoW-shared total: ~1 model copy = ~280-560 MB**, NOT 4 × model. This is the architectural reason to preload.
- Total VPS RAM 24 GB → ample headroom for Postgres + corpus cache + the (shared) cross-encoder weights. No memory pressure expected at this shortlist's size class.
- Caveat: any write into the shared pages (e.g., if some library internally mutates buffers per call) will trigger copy-on-write and the savings degrade. The C-β bench-off should record per-worker RSS after sustained load to confirm CoW sharing actually held for the chosen model. If it didn't, fall back to per-worker memory budgeting.
- Alternative if a future model swap pushes past ~1.5 GB per worker even after CoW: switch to HuggingFace TEI sidecar (separate container, single model copy, gRPC). Defer until measured pressure.

## Ingest changes

**None required.** This is a pure query-time architecture change. Passage embeddings already exist in `genereview_embeddings_bge384`; HNSW index already exists ("intentionally omitted in the migration; built post-COPY by the embed CLI" per [`db/migrations/data/0003_embeddings_bge384.sql`](../../genereview_link/db/migrations/data/0003_embeddings_bge384.sql)).

Optional future optimization (Batch D, not this spec): pre-compute cross-encoder scores at ingest for the top-N "common intent templates" against each chapter, caching as a materialized view. Defer until production latency pressure justifies it.

## Metrics

Composite `(P@1 + MRR@5 + Recall@5) / 3`, reported alongside each component metric for transparency.

- **P@1**: did the top hit match `expected_top1_passage_id`? Binary per query. Matters because LLM consumers often only read position 1.
- **MRR@5**: reciprocal rank of the first correct hit in top 5. Penalizes near-misses smoothly. Captures "almost got it right" cases.
- **Recall@5**: did any of `expected_top5_passage_ids` appear in the returned top 5? Matters because LLMs read 3-5 hits and synthesize.

Hard gates:
- `must_not_regress` (regression_kind-aware): for `exact-symbol-anchor` rows, *any* top-1 change disqualifies. For `pending-improvement` rows, max 1 re-lock per C-β run with evidence and baseline update in same PR (see C-α "Audit caveat" + C-β shortlist "must_not_regress gate").
- `must_change` queries: model is disqualified if zero `must_change` entries are fixed (i.e., no demonstrated improvement on known-bad cases).

## Test budget

| Phase | New tests | Notes |
|---|---|---|
| C-α | ~10 unit + integration | parallel-retrieval correctness, RRF fusion math, benchmark harness scoring, `rerank=rrf` smoke against `ranking_bench.jsonl` |
| C-β | 0 new unit tests | measurement only; benchmark report committed to `docs/superpowers/reviews/2026-05-NN-ranking-bench-results.md` |
| C-γ | ~8 unit + integration | boot-time preload (CPU-forced, no CUDA init), `ce`-disabled-config startup smoke, fork + per-worker RSS smoke confirming CoW sharing held, cache hit/miss, timeout fallback, `rerank=ce` smoke, regression on full `ranking_baseline.json` |

New fixtures:
- `tests/fixtures/ranking_bench.jsonl` (the benchmark dataset)
- `tests/fixtures/ranking_bench_generation_prompt.md` (the Codex prompt template)
- `tests/fixtures/ranking_bench_worklist.json` (the chapter worklist used to seed generation)

## Public-host deployment policy

Based on 2025-2026 MCP rate-limiting best-practice consensus ([MintMCP](https://www.mintmcp.com/blog/rate-limiting-with-mcp), [Cloudflare WAF guidance](https://developers.cloudflare.com/waf/rate-limiting-rules/best-practices/), [Markaicode MCP API protection 2025](https://markaicode.com/mcp-api-protection-2025/)) and the AGENTS.md note that public hosted tools are research-use scoped: the `rerank=ce` mode is **enabled but rate-limited** on the public host, not auth-gated. Three layers of protection — per-IP request rate, global CE concurrency cap, and privacy-safe telemetry — together prevent both runaway agent abuse and disproportionate single-client compute share.

| Setting | Public host | Local Docker |
|---|---|---|
| `rerank=lexical / rrf / off` | Available; modest per-IP rate limit (e.g., **60 req/min**) | Unlimited |
| `rerank=ce` | Available; per-IP rate limit (e.g., **15 req/min**) + **global CE concurrency cap** (e.g., **max 2 in-flight `ce` requests across all workers**). No auth header required. Returns 429 on per-IP overage; returns 503 with `Retry-After` on global concurrency overage. | Unlimited |
| Telemetry | See "Privacy-safe telemetry" below — never raw query, IP-prefix-only, HMAC-hashed query, bounded retention. | Not needed |

**Reasoning for "rate-limited, not gated":**

- This MCP is a **research-use** tool. Requiring auth for the high-quality mode creates friction for legitimate clinicians and researchers — exactly the audience the project exists for.
- A 15 req/min cap is well above any genuine clinical workflow (you're not running `ce` 100× per minute by hand) but caps theoretical abuse at ~21,600 queries/day max per IP. With the global concurrency cap on top, no single client can monopolize CE compute even if it stays under the per-minute limit.
- The cautionary tale informing this design: a runaway AI agent ran a $47K cloud bill in 8 hours via a single MCP ([MintMCP](https://www.mintmcp.com/blog/rate-limiting-with-mcp)). Layered limits make this failure mode structurally impossible: even if the per-IP limiter is somehow bypassed (e.g., distributed botnet), the global concurrency cap keeps total CE compute bounded.
- Auth tokens are an **escalation path, not the starting point**. Move to auth only after observed abuse or if hosting costs become material.

**Important caveat about in-memory limiters and multi-worker deployments.** A naive in-memory per-IP rate limiter (e.g., `slowapi` with the default in-process backend) maintains separate counters per uvicorn/gunicorn worker. With N workers, the effective per-IP limit becomes N × the configured value. For a single-process deployment this is fine; for multi-worker (e.g., gunicorn with 4 workers), either:
- Run a single worker (acceptable for this MCP's traffic profile), OR
- Use a shared-state backend (Redis or a small Postgres advisory-lock-based counter), OR
- Configure the per-IP limit as `(target_limit ÷ worker_count)` to compensate.

The global CE concurrency cap is **always cross-worker** — implement it via Postgres advisory locks since this repo already has a Postgres connection per worker and no shared cache infrastructure. Concrete contract:

```python
# concurrency_cap.py — pseudocode
CE_SLOT_KEYS = (0xC0DEC2, 0xC0DEC3)  # two slot keys → max 2 in-flight CE requests
CE_LOCK_TIMEOUT_S = 30  # match the per-query CE timeout

async def acquire_ce_slot(conn) -> int | None:
    """Try to grab a CE slot via pg_try_advisory_lock. Return slot key on success, None on failure.
    The lock is held on THIS connection until released; do not return the connection to the pool
    while a CE request is in flight, or the lock will be released and the cap defeated.
    """
    for key in CE_SLOT_KEYS:
        if await conn.fetchval("SELECT pg_try_advisory_lock($1)", key):
            return key
    return None

async def release_ce_slot(conn, key: int) -> None:
    await conn.execute("SELECT pg_advisory_unlock($1)", key)

# Route handler pattern.
# CE inference runs in a bounded ProcessPoolExecutor (size = CE_SLOT_COUNT)
# so that cancellation of the awaiting task DOES NOT release the slot before
# the underlying compute actually finishes. asyncio.to_thread() would NOT
# suffice — cancellation there releases the awaiter but the OS thread keeps
# running, which would let the advisory lock be released early and the cap
# would silently undercount real CPU pressure.
async def handle_ce_request(...):
    async with pool.acquire() as conn:  # dedicated conn for the duration
        slot = await acquire_ce_slot(conn)
        if slot is None:
            return Response(status=503, headers={"Retry-After": "5"})
        future = CE_EXECUTOR.submit(run_cross_encoder_sync, ...)
        try:
            # Await the future. On cancellation, we still wait for the future
            # to complete (or explicit kill) before releasing the slot.
            result = await asyncio.shield(asyncio.wrap_future(future))
            return result
        except asyncio.CancelledError:
            # Caller went away. Don't release the slot until the worker is
            # actually done (or we forcibly kill the process pool worker).
            try:
                future.result(timeout=CE_LOCK_TIMEOUT_S)
            except Exception:
                pass  # swallow; we just need to wait for completion
            raise
        finally:
            # Release the slot only AFTER the future has terminated.
            # Wrap unlock in cancellation shielding so a second cancellation
            # during finally doesn't leak the slot.
            await asyncio.shield(release_ce_slot(conn, slot))
```

Key correctness points the implementation must hit:
- **CE work runs in a process pool**, not `asyncio.to_thread()`. `asyncio.to_thread()` does not propagate cancellation into the sync worker function — cancelling the awaiter leaves the thread running ([Python docs, asyncio.to_thread](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread)). If you used to_thread and unlocked in `finally`, the lock would release while the CPU is still pegged on inference; the slot count would silently undercount real CE pressure and the cap would be ineffective. A `ProcessPoolExecutor(max_workers=CE_SLOT_COUNT)` constrains real OS-level concurrency in addition to the lock — defense in depth.
- **Hold the same connection** from `pg_try_advisory_lock` through `pg_advisory_unlock`. Returning the connection to the pool releases the session-scoped lock automatically — defeating the cap. ([asyncpg pool docs](https://magicstack.github.io/asyncpg/current/usage.html#connection-pools))
- **Release in `finally` + `asyncio.shield`**, including under `asyncio.CancelledError` and per-query timeouts. Without shielding, a second cancellation during the finally block can interrupt the unlock SQL and leak the slot until session timeout (default 0 = forever; advisory locks are session-scoped).
- **`CE_SLOT_COUNT = 2`** matches the bench-time concurrency target. Adjust slot count to fit measured CE latency × target throughput on the VPS. The slot key constants (e.g., 0xC0DEC2/0xC0DEC3) must not collide with other advisory locks the codebase already uses — verified: the existing release-watcher key at [`genereview_link/ingest/scheduler.py:14`](../../genereview_link/ingest/scheduler.py) uses a different key space.
- **Coordinator-worker pattern is NOT recommended** for this MCP — gunicorn workers don't selectively route by path, and adding a separate "CE-only worker" requires either a sidecar service or path-based proxying (e.g., nginx). Postgres advisory locks + a bounded process pool together give the same guarantee with infrastructure that already exists.

**Privacy-safe telemetry.** Never log raw query text. The logging spec for `ce` calls is:

```
log line fields:
  - timestamp_bucket   # coarsened to 5-minute window (e.g., truncate to nearest 300s)
  - ip_prefix          # /24 for IPv4, /48 for IPv6 — coarsening dilutes individual-tracking
  - client_key_hmac    # HMAC-SHA256 of any client-supplied token, using the same rotating secret
                       # as query_hmac. Never log raw client_key.
  - query_hmac         # HMAC-SHA256(query, rotating server secret); secret rotated weekly
  - retrieval_mode     # 'lexical' | 'rrf' | 'ce' | 'off'
  - latency_ms
  - status_code
  # top_chapter_nbk_id is REMOVED from per-request logs.
  # Reason: a chapter ID still distinguishes "researcher querying a rare disease"
  # from "researcher querying a common disease" (e.g., NBK501979 GRIN2B has very
  # few queriers and is identifying; NBK1247 BRCA1 is anonymizing). At low public
  # query volume, this is fingerprintable.
  # Emit top_chapter_nbk_id ONLY in k-anonymous aggregate windows
  # (e.g., a daily roll-up showing chapters with >= 5 distinct ip_prefix
  # queriers in the window). Below the k threshold, the chapter is omitted
  # from the aggregate.
```

Retention: 30 days rolling.

**What this telemetry shape prevents and what it does not:**
- **Prevents:** raw query disclosure, long-term query reconstruction (weekly HMAC rotation breaks links across weeks), exact-IP individual identification (coarsened to /24 or /48), millisecond-timing correlations (coarsened to 5-minute bucket).
- **Reduces but does not prevent** *topic leakage*: by removing `top_chapter_nbk_id` from per-request logs and emitting it only in k-anonymous aggregates, individual queries no longer reveal which chapter was returned. But the existence of a request at all, combined with `ip_prefix` + 5-minute timing + retrieval mode, is still partial signal.
- **Does NOT prevent:** within-week linkability for repeat queries from the same IP-prefix (same `query_hmac` value within the 7-day rotation window groups identical-text repeat queries together). Acceptable for a research-use MCP querying a *public* corpus; document explicitly so operators don't claim stronger guarantees than the design delivers.
- **Does NOT prevent:** joint-distribution fingerprinting if `latency_ms` or `status_code` is unique-enough to identify a query. Mitigate by reporting `latency_ms` in coarse buckets (e.g., 0-50, 50-200, 200-1000, 1000+) rather than raw milliseconds.
- **Does NOT prevent** a determined adversary with side channels (e.g., the public corpus is small, an adversary can enumerate plausible queries against an arbitrary chapter and compare against logs). For higher-assurance use cases, do not deploy this telemetry at all — turn it off and accept blind operations.

No PHI by construction (no raw IP, no raw query, no raw client_key persisted). For a public-research MCP on a public corpus this is appropriate; do not redeploy this telemetry shape to a host serving non-public clinical data without re-reviewing the privacy contract.

**`X-Forwarded-For` is spoofable** unless the public host runs behind a known trusted proxy (CDN, load balancer). Only honor `X-Forwarded-For` if the incoming connection's source IP is in a trusted-proxy allowlist; otherwise use `request.client.host` directly. Misconfiguration here defeats the entire per-IP limit.

**Implementation note**: rate-limiting at the application layer is sufficient for this MCP's scale; no external gateway needed. Use FastAPI middleware (e.g., `slowapi` or a small custom middleware on the search route) with the cross-worker caveats above. Document the configured limits in `genereview://usage` so LLM consumers know what to expect from a 429.

## Open questions (decide before C-γ ships, not blocking C-α)

1. **Model storage location.** Where does the chosen cross-encoder model get downloaded to / mounted from on the VPS? Options: bundle in Docker image (large image but reproducible), pre-warm to a volume mount at container start, on-demand download (network dependency at boot). Recommendation: pre-warm to a volume mount; document the path.

2. **Corpus-version cache invalidation hook.** Where to wire the cache-bust. Likely at the same place `corpus_version` is recomputed (somewhere in `genereview_link/ingest/orchestrator.py` or `genereview_link/services/service_manager.py` — confirm during C-γ implementation).

3. **Rate-limit middleware choice.** `slowapi` (FastAPI-native, redis-optional) vs a small custom in-memory limiter for single-VPS deployment. Recommendation: start with custom in-memory (no new dep); migrate to `slowapi` if running multi-worker with shared state becomes needed.

## Migration order and rollout

| Phase | Wall-clock effort | Risk | Ships |
|---|---|---|---|
| **C-α** Hybrid + harness | ~1 week | low | upgraded `rerank=rrf` + benchmark fixture + `make bench-ranking` |
| **C-β** Model bake-off | ~3 days | none | model picked, written into this spec as an addendum |
| **C-γ** Cross-encoder mode | ~1 week | medium (new model dep) | `rerank=ce` shipped |

Total: ~2.5 weeks serial, or ~1.5 weeks with parallel work (one engineer on C-γ harness while another runs C-β).

## Sources

- [Beyond Retrieval: Ensembling Cross-Encoders and GPT Rerankers for Biomedical QA (arXiv 2507.05577)](https://arxiv.org/html/2507.05577v1)
- [BAAI/bge-reranker-v2-m3 — Hugging Face](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- [BAAI/bge-reranker-base — Hugging Face](https://huggingface.co/BAAI/bge-reranker-base)
- [ms-marco-MiniLM-L12-v2 — Hugging Face](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L12-v2)
- [mixedbread-ai/mxbai-rerank-base-v1](https://huggingface.co/mixedbread-ai/mxbai-rerank-base-v1)
- [MIRAGE benchmark — Benchmarking RAG for Medicine (ACL 2024)](https://aclanthology.org/2024.findings-acl.372.pdf) — for external-validity entries
- [RAGCare-QA benchmark (medRxiv 2025)](https://www.sciencedirect.com/science/article/pii/S2352340925008674)
- [BEIR datasets — Hugging Face](https://huggingface.co/datasets/BeIR/nfcorpus)
- [Hybrid Search in PostgreSQL — ParadeDB](https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual)
- [Synthetic data for RAG evaluation — Dylan Castillo](https://dylancastillo.co/posts/synthetic-data-rag.html)
- [FastMCP Tools documentation](https://gofastmcp.com/v2/servers/tools) — for `outputSchema` integration patterns
